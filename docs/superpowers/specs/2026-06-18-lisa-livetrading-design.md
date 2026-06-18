# Lisa LiveTrading — Chain Video Generator (Design Spec)

**Date**: 2026-06-18
**Status**: Approved (user)
**Parent app**: `D:\Projects\story_video_making_v2` (Grok CDP-based)

---

## 1. Mục đích

App PyQt6 desktop nhỏ, tự động tạo chuỗi video clip liên tục bằng Grok CDP:

1. User pick: 1 ref image + 1 `prompts.json` + aspect (9:16) + duration (10s)
2. App tạo folder `{image_parent}/project_{YYYYMMDD_HHMMSS}/`
3. Loop: prompt[i] + ref → Grok gen video → cắt frame cuối → frame trở thành ref cho prompt[i+1]
4. Khi xong: ffmpeg concat tất cả clip với xfade 0.5s → `final.mp4`
5. State.json atomic write → có thể stop/crash và resume

App **không có** voice/scene/render/karaoke (loại bỏ hoàn toàn so với v2).

## 2. Fork strategy

| Reuse từ v2 (copy nguyên xi) | Bỏ hoàn toàn |
|---|---|
| `engines/grok/` (engine, flows, masonry, downloader, settings) | `voice/`, `render/`, `core/project.py` (scenes/voice) |
| `workers/task_contract.py` (giản lược) | `workers/voice_processing_worker.py`, `slideshow_worker.py`, `render_worker.py`, `single_image.py`, `batch_image.py` |
| `workers/process_launcher.py` | `ui/main_window.py` v2 (scene grid, voice panel) |
| Atomic write utils (rotating backup) | Whisper, Claude, ASS, BGM mixer |
| Logging setup (loguru, faulthandler) | |

## 3. Cấu trúc thư mục

```
Lisa_livetrading/
├── app/
│   ├── __init__.py
│   └── main.py                       # entry: faulthandler + loguru + QApplication
├── ui/
│   ├── __init__.py
│   └── main_window.py                # MinimalWindow (1 form)
├── core/
│   ├── __init__.py
│   ├── project.py                    # ChainProject: state.json atomic load/save
│   ├── chain_runner.py               # ChainRunner: loop, retry, resume
│   └── settings.py                   # AppSettings (aspect, duration, paths)
├── workers/
│   ├── __init__.py
│   ├── task_contract.py              # TaskJson schema, exit codes, stdout markers
│   ├── process_launcher.py           # subprocess + marker parser
│   └── video_chain_worker.py         # 1 prompt → 1 mp4
├── engines/
│   └── grok/                          # COPY nguyên từ v2
│       ├── __init__.py
│       ├── engine.py                 # GrokVideoEngine.gen_video()
│       ├── flows.py                  # workflow image_to_video
│       ├── masonry.py
│       ├── downloader.py
│       └── settings.py
├── utils/
│   ├── __init__.py
│   ├── paths.py
│   ├── atomic.py                     # atomic_write + rotating backup
│   ├── logging.py                    # loguru config
│   ├── frame_extractor.py            # ffmpeg cut last frame
│   └── video_concat.py               # ffmpeg concat + xfade
├── tests/
│   ├── __init__.py
│   ├── test_chain_runner.py
│   ├── test_frame_extractor.py
│   ├── test_video_concat.py
│   ├── test_chain_e2e.py
│   └── fixtures/                     # sample prompts.json, mock mp4
├── config.yaml                        # cdp_url, brave_profile_dir, ffmpeg_path
├── requirements.txt
├── pyproject.toml
├── .gitignore
├── README.md
└── docs/
    └── superpowers/
        ├── specs/2026-06-18-lisa-livetrading-design.md (this file)
        └── plans/
```

## 4. Folder runtime (output)

Khi user Start, app tạo:
```
{image_parent}/project_{YYYYMMDD_HHMMSS}/
├── input/
│   ├── ref.png                       # copy từ user pick
│   └── prompts.json                  # copy từ user pick
├── clips/
│   ├── clip_001.mp4
│   ├── clip_002.mp4
│   └── ...
├── frames/
│   ├── frame_001.png
│   └── ...
├── logs/
│   └── app.log
├── state.json
└── final.mp4                          # sau khi concat xong
```

## 5. Schema `prompts.json` (user cung cấp)

```json
[
  {"id": 1, "prompt": "Lisa stands up from her chair, looking confident"},
  {"id": 2, "prompt": "She walks towards the trading screen"},
  {"id": 3, "prompt": "She points at the chart with a smile"}
]
```

- `id`: int, 1-based, không trùng. Dùng làm thứ tự (sort by `id` asc).
- `prompt`: string, non-empty.
- App **không** validate prompt content (Grok tự reject nếu vi phạm).

## 6. Schema `state.json` (runtime)

```json
{
  "version": 1,
  "created_at": "2026-06-18T14:30:12+07:00",
  "updated_at": "2026-06-18T14:38:55+07:00",
  "inputs": {
    "ref_image": "input/ref.png",
    "prompts": "input/prompts.json",
    "aspect": "9:16",
    "duration": 10
  },
  "clips": {
    "001": {
      "status": "done",
      "prompt": "...",
      "ref": "input/ref.png",
      "clip": "clips/clip_001.mp4",
      "frame": "frames/frame_001.png",
      "started_at": "...",
      "finished_at": "...",
      "attempts": 1
    },
    "002": {"status": "failed", "reason": "CDP timeout", "attempts": 3}
  },
  "final": {"status": "pending", "path": null}
}
```

Status values: `pending` | `running` | `done` | `failed` | `interrupted`.

## 7. Luồng chính

```
[GUI] User picks ref + prompts + Start
   │
   ▼
[ChainRunner.start()]
   1. Create project folder + state.json (init)
   2. Copy ref → input/ref.png
   3. Copy prompts.json → input/prompts.json
   4. Load prompts, sort by id
   │
   ▼
For each prompt[i]:
   ┌──────────────────────────────────────────────┐
   │ a. Determine ref:                            │
   │    - i==0: input/ref.png                     │
   │    - i>0:  frames/frame_{i-1:03d}.png        │
   │                                              │
   │ b. Spawn worker subprocess:                  │
   │    python -m workers.video_chain_worker      │
   │      --task /tmp/task_{i}.json               │
   │    task = {prompt, ref, aspect, duration,   │
   │            output_path, retries_remaining}   │
   │                                              │
   │ c. Worker (subprocess):                      │
   │    - Connect CDP (Patchright)                │
   │    - engines.grok.GrokVideoEngine.gen_video  │
   │      → download clip_{i:03d}.mp4             │
   │    - Print marker EVENT scene_done           │
   │    - Exit 0 on success                       │
   │                                              │
   │ d. Parent (ChainRunner):                     │
   │    - On EXIT_SUCCESS:                        │
   │      - frame_extractor.extract_last()        │
   │        → frames/frame_{i:03d}.png            │
   │      - atomic update state.json              │
   │      - Emit UI signal: clip_done(i)          │
   │    - On EXIT_FLOW_FAILED:                    │
   │      - retries_remaining > 0 → respawn       │
   │      - else: state failed, stop chain        │
   │                                              │
   │    - On user Stop:                           │
   │      - SIGTERM worker                        │
   │      - state.json: clips[i] = interrupted    │
   │      - break loop                            │
   └──────────────────────────────────────────────┘
   │
   ▼
All clips done:
   1. video_concat.build(clips/, xfade=0.5s) → final.mp4
   2. state.json: final = {status: done, path: final.mp4}
   3. UI: enable "Open folder"
```

## 8. UI Layout (PyQt6 minimal)

```
┌─ Lisa LiveTrading ────────────────────────────┐
│                                               │
│  Ref image:  [/path/to/ref.png    ] [Browse] │
│  Prompts:    [/path/to/prompts.json] [Browse]│
│                                               │
│  Aspect:    [9:16 ▾]   Duration: [10s ▾]    │
│                                               │
│  ┌─────────────────────────────────────────┐ │
│  │ [Start]  [Stop]  [Open Folder]          │ │
│  └─────────────────────────────────────────┘ │
│                                               │
│  Progress: ████████░░░░░░  3/10 clips        │
│  Status:   Running clip 4: "She walks..."    │
│                                               │
│  ┌─ Log ──────────────────────────────────┐  │
│  │ 14:30:12 INFO Project created          │  │
│  │ 14:30:15 INFO Clip 1 started           │  │
│  │ 14:30:58 INFO Clip 1 done (45s)        │  │
│  │ ...                                    │  │
│  └────────────────────────────────────────┘  │
└───────────────────────────────────────────────┘
```

Widgets:
- `QLineEdit` × 2 (ref, prompts) + Browse buttons (QFileDialog)
- `QComboBox` × 2 (aspect: 9:16/16:9/1:1, duration: 5s/10s/15s)
- `QPushButton` × 3 (Start, Stop, Open Folder)
- `QProgressBar`
- `QLabel` (status)
- `QPlainTextEdit` (read-only, log)

## 9. Error handling

| Lỗi | Hành vi |
|---|---|
| Network / CDP timeout | Retry 2× (respawn worker) |
| Grok content reject | Stop chain, state.failed, user fix prompt + Start lại |
| Frame extract fail | Clip giữ `pending` (rerun lần sau), stop |
| User Stop | SIGTERM worker → mark `interrupted` |
| App crash | atomic state → mở lại folder resume |
| Final concat fail | clips vẫn còn, GUI có nút "Re-build final" |
| `prompts.json` invalid | UI hiện error trước khi Start |
| Ref image không tồn tại | UI disable Start button |

## 10. Resume semantics

Khi user Start với folder đã tồn tại (mở lại app + pick folder cũ):
- ChainRunner đọc state.json
- Skip clips có `status == done`
- Retry clips `failed` / `interrupted` / `pending`
- Nếu tất cả done nhưng final pending → chạy concat luôn

(Phase 1: chỉ resume nếu user pick lại đúng folder. Không auto detect.)

## 11. CDP session (kế thừa v2)

- Brave launched bên ngoài (user dùng `launch_brave.bat` từ v2):
  ```
  brave.exe --remote-debugging-port=9222 --user-data-dir="...\brave-grok-profile"
  ```
- Worker `attach_existing_browser(cdp_url)` qua Patchright
- Reuse tab có URL `grok.com/imagine`; không thì mở mới
- Worker **không quit** browser khi xong (để worker kế tiếp reuse session)
- Config `cdp_url` mặc định `http://127.0.0.1:9222`

## 12. Exit codes (worker)

Kế thừa v2 `task_contract.py`:
- `0` EXIT_SUCCESS
- `1` EXIT_FLOW_FAILED (generic)
- `2` EXIT_PREREQ_MISSING (file/env missing)
- `3` EXIT_USER_KILLED
- `4` EXIT_PARSE_FAILED (task.json invalid)
- `5` EXIT_CDP_UNREACHABLE
- `6` EXIT_PROJECT_LOCKED (không dùng phase 1)

ChainRunner đối xử khác nhau:
- `0` → success
- `5` → retry, sau 2 lần fail → "Check Brave CDP" message
- `3` → mark interrupted, dừng
- `1, 2, 4` → mark failed, dừng

## 13. Stdout marker contract (worker → parent)

```
TASK START {"task_id":"clip_001","prompt":"...","ref":"..."}
EVENT {"type":"cdp_connected","url":"http://127.0.0.1:9222"}
EVENT {"type":"prompt_submitted","at":"..."}
EVENT {"type":"video_ready","download_url":"..."}
EVENT {"type":"download_done","path":"clips/clip_001.mp4","duration_sec":45.2}
TASK DONE {"success":1,"clip":"clips/clip_001.mp4"}
```

ChainRunner parse các marker này để update progress UI.

## 14. Config (`config.yaml`)

```yaml
cdp:
  url: "http://127.0.0.1:9222"
  profile_marker: "brave-grok-profile"
  base_url: "https://grok.com/imagine"

ffmpeg:
  path: "ffmpeg"   # in PATH; or full path

defaults:
  aspect: "9:16"
  duration: 10
  retry_count: 2
  worker_timeout_sec: 600   # 10 min per clip

logging:
  level: "INFO"
  file_rotation_mb: 10
```

## 15. FFmpeg commands chính

**Extract last frame** (utils/frame_extractor.py):
```bash
ffmpeg -sseof -0.5 -i clip.mp4 -frames:v 1 -q:v 2 -update 1 frame.png
```
Lý do `-sseof -0.5`: seek 0.5s từ cuối, lấy 1 frame → tránh black frame ở chính cuối.

**Concat + xfade** (utils/video_concat.py):
Dùng `xfade` filter chain. Pseudocode (3 clips):
```bash
ffmpeg -i clip_001.mp4 -i clip_002.mp4 -i clip_003.mp4 \
  -filter_complex "\
    [0][1]xfade=transition=fade:duration=0.5:offset=9.5[v01]; \
    [v01][2]xfade=transition=fade:duration=0.5:offset=19[v012]" \
  -map "[v012]" -c:v libx264 -pix_fmt yuv420p final.mp4
```
Audio: bỏ (Grok video không có voiceover, app này không xử lý voice).

## 16. Testing strategy (TDD)

Theo `superpowers:test-driven-development`:

**Unit tests:**
- `test_frame_extractor.py`:
  - Cắt frame từ mp4 fixture → file PNG > 0 byte, đúng dimension
  - Lỗi file không tồn tại → raise FileNotFoundError
- `test_video_concat.py`:
  - 3 clip fixtures → final.mp4 length ≈ Σ − (N−1) × 0.5s
  - 1 clip → final.mp4 == clip (no xfade)
  - 0 clip → raise
- `test_chain_runner.py`:
  - Mock worker dispatch → verify thứ tự, retry count, resume logic
  - Mock state.json IO → verify atomic write
- `test_task_contract.py`:
  - Marker parse correctness

**Integration test:**
- `test_chain_e2e.py`:
  - Mock `engines.grok.engine.gen_video` để return fixture mp4
  - Run full chain (3 prompts) → verify final.mp4 + state.json

**Manual:**
- GUI smoke test (PyQt6 widget creation)
- Real Grok run (1 chain ngắn 2-3 prompt) — sau khi all unit pass

## 17. Dependencies (`requirements.txt`)

```
PyQt6>=6.7
patchright>=1.40
loguru>=0.7
pydantic>=2.0
pyyaml>=6.0
pytest>=8.0
pytest-asyncio>=0.23
```

FFmpeg: hệ thống (không pip), đường dẫn trong config.yaml.

## 18. Non-goals (Phase 1)

- Voice / audio overlay
- Multi-project (chỉ 1 chain tại 1 thời điểm)
- Cloud/remote run
- Other providers ngoài Grok
- Auto re-prompt khi Grok reject (user fix tay)
- GUI preview clip inline
- Auto Brave launch (user tự `launch_brave.bat`)
- Multi-aspect per chain (toàn chain dùng cùng 1 aspect)

## 19. Acceptance criteria

- [ ] User pick ref + prompts + aspect + duration → Start
- [ ] App tạo đúng folder `project_{date}` cạnh ref image
- [ ] N clips được gen tuần tự, mỗi clip dùng frame cuối của clip trước làm ref
- [ ] Failed clip retry 2 lần trước khi stop
- [ ] Stop button → SIGTERM worker → state preserved → mở lại resume được
- [ ] All clips done → final.mp4 sinh ra với xfade 0.5s
- [ ] All unit tests pass; integration test (mock Grok) pass
- [ ] Manual smoke test 1 chain ngắn (2 prompt) chạy được trên Grok thật

---

End of spec.
