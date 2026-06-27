# Grok DOM Flow Spec

Hướng dẫn chi tiết để **điều chỉnh luồng DOM + await** cho engine `engines/grok/` khi
Grok đổi UI hoặc khi bạn muốn thêm flow mới (vd. image-to-video → image-to-audio,
hoặc một chế độ "Make video" mới).

---

## 0. Kiến trúc engine — đọc trước khi sửa

```
engines/grok/
├── selectors.py   ← Bảng tra cứu CSS selector duy nhất. Sửa UI = sửa file này TRƯỚC.
├── actions.py     ← Hàm async nguyên tử (click_submit, set_aspect, wait_video_ready...).
│                    Mỗi hàm: lấy Page + params → trả {"ok": bool, ...}. Không raise.
├── flows.py       ← Khai báo declarative: list các step → action + params.
├── runner.py      ← Dispatcher: đọc flow, map action name → hàm trong actions.py,
│                    quản lý state (counter, current_prompt, vars, errors, downloaded).
├── engine.py      ← Adapter cấp cao: GrokVideoEngine.
│                    UI worker chỉ gọi gen_video(...); image_edit dùng image_ref_engine.py.
├── image_ref_engine.py ← Direct image-edit flow: upload ref image(s), prompt, wait, download.
├── browser.py     ← GrokConnection: CDP attach, list_tabs, select_tab, kill+relaunch.
```

**Quy tắc bất biến (đừng phá):**

1. **Selector duy nhất ở `selectors.py`.** Không hardcode CSS trong `actions.py`.
2. **Action không raise.** Mọi lỗi trả về `{"ok": False, "reason": "..."}`. Runner mới
   quyết định fail hay tiếp.
3. **Prefix-match aria-label** (`button[aria-label^="Submit"]`). Grok thường thêm
   suffix động ("Submit prompt" vs "Submit"). Không bao giờ dùng `=` exact match.
4. **Flow chỉ là dict.** Không nhồi logic vào `flows.py` — chỉ list step + params.
5. **Mỗi action atomic.** Không gộp 2 hành động vào 1 hàm. `set_mode` và
   `set_quality` riêng — vì có flow chỉ cần một trong hai.

---

## 1. Cập nhật selector khi Grok đổi UI

### 1.1 Cách kiểm tra DOM hiện tại

1. Mở Brave với CDP profile: chạy `launch_brave.bat` (hoặc để app tự bật).
2. Vào `https://grok.com/imagine`, F12 → Elements.
3. Hover vào element cần (nút Submit, dropdown Aspect…), copy attribute thực tế.
4. Đối chiếu với `selectors.py`.

### 1.2 Quy tắc viết selector mới

| Ưu tiên | Pattern | Lý do |
|---|---|---|
| 1 | `[aria-label^="..."]` | Accessibility label ổn định hơn class hash |
| 2 | `[role="..."]` + filter text | Role chuẩn semantic, ít rename |
| 3 | `[id^="..."]` (prefix) | Grok dùng UUID suffix động — luôn prefix-match |
| 4 | `:has-text("...")` | Khi không có aria/role — chấp nhận giòn |
| ❌ | `.bg-surface-l2.hover\\:bg-...` | Class TailwindCSS hash, đổi theo build |

### 1.3 Selector hiện tại — checklist khi UI đổi

Nếu bất kỳ trong số này không tìm thấy element → cập nhật `selectors.py`:

| Const | Selector | Dùng cho |
|---|---|---|
| `PROMPT_INPUT` | `[contenteditable="true"]` | Ô nhập prompt (TipTap) |
| `PROMPT_INPUT_EMPTY` | `p.is-empty.is-editor-empty` | Verify input đã clear |
| `SUBMIT` | `button[aria-label^="Submit"]` | Nút submit prompt |
| `UPLOAD` | `button[aria-label^="Upload"]` | Mở popup upload ref |
| `MODE_GROUP` | `div[aria-label^="Generation mode"]` | Container Image/Video toggle |
| `QUALITY_RADIO` | `button[role="radio"]` | Speed / Quality preset |
| `ASPECT_TRIGGER` | `button[aria-label^="Aspect Ratio"]` | Nút mở dropdown aspect |
| `ASPECT_OPTION` | `div[role="menuitem"]` | Item trong dropdown aspect |
| `VIDEO_RES_GROUP` | `div[aria-label="Video resolution"]` | Group 480p/720p (chỉ khi mode=Video) |
| `VIDEO_DUR_GROUP` | `div[aria-label="Video duration"]` | Group 6s/10s |
| `MASONRY_PREFIX` | `[id^="imagine-masonry-section-"]` | Grid kết quả image |
| `LIST_ITEM` | `[role="listitem"]` | 1 candidate trong masonry |
| `DOWNLOAD` | `button[aria-label^="Download"]` | Trên /post/{uuid} |
| `MAKE_VIDEO` | `button[aria-label^="Make video"]` | Trên /post/{uuid} (image-to-video) |
| `BACK` | `div[aria-label^="Back"]` | **DIV, không phải button** — quay về /imagine |
| `VIDEO_ELEMENT` | `#sd-video` | Element `<video>` — presence = video ready |
| `TOAST` | `[data-sonner-toast]` | Toast lỗi (rate limit, policy) |
| `UPLOAD_POPUP_BTN` | `button:has-text("Upload or drop images")` | Nút trong popup upload |
| `FILE_INPUT` | `input[type="file"]` | Hidden input sau khi popup hiện |

### 1.4 Quy trình sửa 1 selector

1. Mở `selectors.py`, đổi giá trị const.
2. Chạy thử 1 task qua app GUI (1 prompt, 1 ref). Theo dõi log:
   - `[runner] step '<name>' fail: <reason>` → biết action nào hỏng.
3. Nếu vẫn fail, thêm `safe_screenshot(page, Path("debug.png"))` vào action đó để
   chụp lúc fail. So sánh với DOM trong DevTools.
4. Nếu selector đổi nhiều → cập nhật batch, **không sửa 1 lần 5 hàm rồi mới test**.

---

## 2. Chiến lược await — bao giờ dùng cái gì

### 2.1 Nguyên tắc

- **Không `asyncio.sleep` để chờ element**. Dùng `locator.wait_for(state="visible")`.
- **Có `asyncio.sleep` cho UI animation** (300-500ms sau click). Không dài hơn.
- **Polling chỉ cho long-running ops** (image gen, video gen).
- **Fixed initial sleep TRƯỚC khi poll** đối với video / image-with-ref — vì overlay
  "Generating X%" mất 10-15s mới render. Poll ngay sẽ false-positive.

### 2.2 Bảng await theo loại thao tác

| Loại | Await pattern | Code mẫu |
|---|---|---|
| Click element có trên DOM | `locator.click()` (Patchright auto-wait visible+stable) | `await page.locator(SEL.SUBMIT).first.click()` |
| Đợi element xuất hiện | `locator.wait_for(state="visible", timeout=X)` | `await group.wait_for(state="visible", timeout=10000)` |
| Đợi URL match | `page.wait_for_url(re.compile(...), timeout=X)` | `await page.wait_for_url(re.compile(r".*/imagine/post/"), timeout=10000)` |
| UI animation settle | `asyncio.sleep(0.3-0.5)` | Sau click radio để aria-checked update |
| Long-running gen | Fixed initial sleep + polling loop với deadline | Xem `wait_video_ready` |
| File download | `page.expect_download(timeout=X)` context | Xem `download_to` |

### 2.3 Timeout mặc định — phân bổ

| Context | Timeout | Sửa ở đâu |
|---|---|---|
| `wait_for(state="visible")` cho UI element | 10s | Inline trong action |
| `wait_for_url` sau click submit | 10-30s | Inline trong action |
| Image gen polling (`wait_timeout_s`) | 60s | `config.yaml` defaults → settings dict |
| Video gen polling (`video_timeout_s`) | 600s (10 min) | `config.yaml` → engine settings |
| Video initial wait (`video_initial_wait_s`) | 20s | engine.py default |
| Download `expect_download` | 60s | Inline `download_to` (retry 1 lần) |
| Worker per-task watchdog | 600s | `config.yaml` `defaults.worker_timeout_sec` |

**Quan hệ timeout — sắp xếp tăng dần:**
```
UI element visible (10s)
  < URL match (30s)
    < image gen (60s)
      < video gen (600s) ≈ worker watchdog (600s)
```
Worker watchdog **phải ≥** video gen timeout — không thì watchdog kill worker
giữa lúc Grok đang render xong video.

### 2.4 Anti-pattern phải tránh

```python
# ❌ SAI: sleep cố định chờ element
await asyncio.sleep(3)
await page.locator(SEL.SUBMIT).click()

# ✅ ĐÚNG
await page.locator(SEL.SUBMIT).wait_for(state="visible", timeout=10000)
await page.locator(SEL.SUBMIT).click()
```

```python
# ❌ SAI: poll ngay sau submit video — overlay chưa render
await page.locator(SEL.SUBMIT).click()
while True:
    if await page.locator(SEL.VIDEO_ELEMENT).count() > 0:
        break  # false positive: bắt được <video> cũ từ post trước

# ✅ ĐÚNG (xem wait_video_ready)
await page.locator(SEL.SUBMIT).click()
await page.wait_for_url(re.compile(r".*/imagine/post/"), timeout=30000)
await asyncio.sleep(20)  # ← cho overlay "Generating X%" có thời gian render
# rồi mới poll
```

```python
# ❌ SAI: dùng page.type cho TipTap contenteditable — timeout
await page.type(SEL.PROMPT_INPUT, text)

# ✅ ĐÚNG: click focus, clear, dùng keyboard
await page.locator(SEL.PROMPT_INPUT).first.click()
await page.keyboard.press("Control+A")
await page.keyboard.press("Delete")
await page.keyboard.type(char)  # hoặc keyboard.insert_text(line) cho fast_mode
```

---

## 3. Thêm flow mới — quy trình

Ví dụ: thêm flow `image_to_video_loop` để gen 1 video rồi feed frame cuối thành ref
cho video tiếp theo (chain video).

### 3.1 Liệt kê step

Mô tả bằng lời:
1. Vào /imagine
2. Set mode = video
3. Set resolution = 720p, duration = 10s, aspect = 9:16
4. Upload ref (frame cuối từ video trước)
5. Fill prompt
6. Submit
7. Đợi /post/{uuid}
8. Đợi video ready
9. Download
10. Quay lại /imagine

### 3.2 Map sang action có sẵn

| Step | Action trong `actions.py` |
|---|---|
| 1 | `ensure_at("/imagine")` |
| 2 | `set_mode("video")` |
| 3 | `set_video_resolution`, `set_video_duration`, `set_aspect` |
| 4 | `upload_ref_if_present` |
| 5 | `fill_prompt` |
| 6 | `click_submit` |
| 7 | `wait_url_match(r".*/imagine/post/")` |
| 8 | `wait_video_ready` |
| 9 | `download_to` |
| 10 | `click_back` |

### 3.3 Thêm entry vào `flows.py`

```python
FLOWS["image_to_video_loop"] = {
    "name": "Image-to-Video (loop)",
    "loop_per_prompt": True,
    "steps": [
        {"action": "ensure_at", "url": "/imagine"},
        {"action": "set_mode", "value": "video"},
        {"action": "set_video_resolution", "from_config": "resolution"},
        {"action": "set_video_duration", "from_config": "duration"},
        {"action": "set_aspect", "from_config": "aspect"},
        {"action": "upload_ref_if_present", "from_prompt": "ref"},
        {"action": "fill_prompt", "from_prompt": "text"},
        {"action": "human_pause", "min_ms": 500, "max_ms": 1200},
        {"action": "click_submit"},
        {"action": "wait_url_match", "pattern": r".*/imagine/post/"},
        {"action": "wait_video_ready"},
        {"action": "human_pause", "min_ms": 600, "max_ms": 1200},
        {"action": "download_to", "from_config": "output_path"},
        {"action": "human_pause", "min_ms": 500, "max_ms": 1000},
        {"action": "click_back"},
        {"action": "wait_url_match", "pattern": r".*/imagine(?!/post)"},
    ],
}
```

### 3.4 Param resolution — cú pháp

| Cú pháp | Lấy từ | Ví dụ |
|---|---|---|
| `"value": X` | Literal | `{"action": "set_mode", "value": "video"}` |
| `"from_prompt": K` | `state["current_prompt"][K]` | `{"action": "fill_prompt", "from_prompt": "text"}` |
| `"from_config": K` | `config[K]` | `{"action": "set_aspect", "from_config": "aspect"}` |
| `"from_var": K` | `state["vars"][K]` (hỗ trợ dotted `"foo.bar"`) | `{"action": "click_image", "from_var": "best_idx"}` |

### 3.5 Cách runner đi qua flow

```
runner.run("image_to_video_loop")
  └─ _run_per_prompt(flow)                   # vì loop_per_prompt=True
      └─ for prompt in self.prompts:
          ├─ state["current_prompt"] = prompt
          ├─ state["vars"].clear()
          └─ _run_steps(flow["steps"])
              └─ for step in steps:
                  ├─ _check_stop()           # raise StopRequested nếu set
                  └─ _exec_step(step)
                      └─ dispatch theo step["action"] → gọi hàm actions.py
```

### 3.6 Thêm action mới (nếu cần)

Nếu flow cần thao tác chưa có (vd. click "Continue with this image" thay vì Back):

1. **Thêm selector** vào `selectors.py`:
   ```python
   CONTINUE_IMAGE = 'button[aria-label^="Continue with this image"]'
   ```
2. **Thêm action** vào `actions.py`:
   ```python
   async def click_continue_image(page: Page) -> dict[str, Any]:
       try:
           await page.locator(SEL.CONTINUE_IMAGE).first.click(timeout=10000)
           await asyncio.sleep(0.5)
           return {"ok": True}
       except Exception as e:
           return {"ok": False, "reason": f"click_continue_image: {e}"}
   ```
3. **Đăng ký dispatch** trong `runner.py:_exec_step`:
   ```python
   if action == "click_continue_image":
       return await actions.click_continue_image(page)
   ```
4. **Dùng trong flow**:
   ```python
   {"action": "click_continue_image"},
   ```

### 3.7 Wire flow vào engine adapter (nếu là loại engine mới)

Nếu flow là biến thể của video/image hiện có → adapter không cần đổi: dùng
`GrokVideoEngine` và override `flow_key` thông qua settings (cần sửa `engine.py`
nếu muốn external chọn flow). Cách đơn giản hơn: thêm method mới trong adapter:

```python
class GrokVideoEngine(_GrokEngineBase):
    async def gen_video_loop(self, prompt, ref_image, settings) -> Path:
        # ... same as gen_video but pass "image_to_video_loop" instead
        result = await runner.run("image_to_video_loop")
        ...
```

---

## 4. Kiểm thử thay đổi

### 4.1 Unit-test layer (không cần Brave)

Test runner + flow dispatch bằng cách stub Page object:

```python
class _StubPage:
    def __init__(self, url="https://grok.com/imagine"):
        self.url = url
    def locator(self, sel):
        return _StubLocator()

# Test: runner gọi đúng action theo flow
```

Hiện chưa có test cho runner — nếu thêm flow mới, viết 1 test stub đảm bảo flow
dispatch đúng thứ tự action.

### 4.2 Integration test (cần Brave + CDP)

Chạy 1 prompt thật:

```bash
.venv/Scripts/activate
python -c "
import asyncio
from pathlib import Path
from engines.grok.browser import GrokConnection
from engines.grok.engine import GrokVideoEngine

async def main():
    conn = GrokConnection()
    await conn.connect('http://127.0.0.1:9222')
    tabs = await conn.list_tabs(grok_only=True)
    await conn.select_tab(tabs[0]['index'])
    engine = GrokVideoEngine(conn.page)
    result = await engine.gen_video(
        prompt='test prompt',
        ref_image=Path('./test_ref.png'),
        settings={'aspect':'9:16','duration':'10s','output_path':'./test_out.mp4'},
    )
    print('OK:', result)
    await conn.disconnect()
asyncio.run(main())
"
```

### 4.3 Verify trong GUI

1. Run `run_gui.bat`
2. Chọn 1 ref image + prompts.json (1 prompt duy nhất)
3. Click Start
4. Xem progress log realtime. Quan sát các marker:
   - `[ATTEMPT 1/3] task=001` — retry counter
   - `Masonry #N ready: M/T` — image gen progress
   - `Video tiến độ: X%` — video gen progress
   - `TASK DONE {...}` hoặc `TASK FAILED {reason: ...}` — kết quả

### 4.4 Debug khi step fail

Log sẽ có:
```
[runner] step '<action_name>' fail: <reason>
```

Hành động:
1. Mở `app/logs/.../app.log` (DEBUG level)
2. Tìm dòng `fail`, đọc reason
3. Nếu reason là `"timeout"` → check timeout config có đủ chưa
4. Nếu reason là `"not found"` → selector hỏng, mở DevTools verify
5. Nếu reason là `"rate_limit"` / `"policy_fail"` → toast Grok bắn ra,
   không phải bug code

---

## 5. Checklist khi Grok đổi UI (cheatsheet)

- [ ] Chạy `launch_brave.bat`, mở DevTools tại /imagine
- [ ] Verify từng selector trong `selectors.py` còn match
- [ ] Update selector hỏng (giữ pattern aria-label prefix khi có thể)
- [ ] Chạy 1 prompt qua GUI → xem step nào fail
- [ ] Nếu fail ở `submit_and_wait_ready` → check `MASONRY_PREFIX` + `LIST_ITEM`
- [ ] Nếu fail ở `wait_video_ready` → check `VIDEO_ELEMENT` (#sd-video) + overlay text
  regex (`r"Generating\s+\d+%"`)
- [ ] Nếu fail ở `download_to` → check `DOWNLOAD` aria-label
- [ ] Nếu UI thêm bước mới (vd. confirm dialog) → thêm action + chèn vào flow
- [ ] Commit selector update riêng commit (không trộn với flow changes)

---

## 6. Checklist khi sửa await/timing

- [ ] Có replace `wait_for(state="visible")` bằng `asyncio.sleep` không? → KHÔNG
- [ ] Polling loop có `deadline` (time-based break) không? → BẮT BUỘC
- [ ] Initial sleep trước polling cho long-gen ops? → BẮT BUỘC cho video
- [ ] Timeout có nhỏ hơn worker watchdog (`worker_timeout_sec`) không? → BẮT BUỘC
- [ ] Có stop_check trong loop dài? → Nên có (qua `stop_event` hoặc `_check_stop`)

---

## 7. Liên hệ giữa engine và phần còn lại

```
ui/main_window.py  ← user click Start
   │
   ▼
app/main.py:handle_start
   │  ChainRunner(project, config)
   ▼
core/chain_runner.py:run()
   │  với worker_factory() as worker:
   ▼
workers/process_launcher.py:LaunchedWorker
   │  spawn subprocess python -m workers.video_chain_worker
   ▼
workers/video_chain_worker.py:_run_loop()
   │  GrokConnection.connect() → BraveLauncher.ensure_running()
   │  _process_task(conn, task) → run_with_retry
   ▼
workers/_retry.py:run_with_retry
   │  for attempt in 1..max:  gen_factory()
   ▼
engines/grok/engine.py:GrokVideoEngine.gen_video()
   │  FlowRunner(page, config, prompts).run("image_to_video")
   ▼
engines/grok/runner.py:FlowRunner.run()
   │  for step in flow.steps: _exec_step(step)
   ▼
engines/grok/actions.py  ← THỰC SỰ thao tác DOM ở đây
   │  page.locator(SEL.X).click(), page.keyboard.type(...), ...
   ▼
Patchright (CDP) → Brave → grok.com/imagine
```

**Cần đổi gì khi:**

| Thay đổi | File cần sửa |
|---|---|
| Grok đổi class/aria của 1 element | `selectors.py` |
| Grok thêm bước mới trong flow gen | `flows.py` + có thể `actions.py` |
| Grok đổi cách báo "video ready" | `actions.py:wait_video_ready` + có thể `selectors.py:VIDEO_ELEMENT` |
| Đổi timeout gen | `config.yaml` `defaults` + có thể `engine.py` default |
| Thêm loại engine (vd. audio) | tạo `engines/grok/audio_engine.py` + flow mới |
| Đổi format prompt (vd. JSON object) | `core/chain_runner.py:_format_prompt` |

---

## 8. Quy ước commit khi sửa flow

- 1 commit / 1 layer: selectors-only, hoặc actions-only, hoặc flows-only.
- Commit message: `engine(grok): <verb> <noun> — <why>`
  - `engine(grok): update SUBMIT selector — aria-label suffix changed`
  - `engine(grok): add click_continue_image action`
  - `engine(grok): add image_to_video_loop flow`
- Không trộn refactor + behavior change.
