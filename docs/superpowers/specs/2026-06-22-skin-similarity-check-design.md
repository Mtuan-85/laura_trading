# Skin-similarity check after image edit

**Date:** 2026-06-22
**Status:** Approved (verbal)

## Motivation

`image_edit.json` (project-level prompt for `GrokImageRefEngine`) refines the last
frame of clip *N-1* into a stylized still that becomes the ref for the *N*-th
video. Grok occasionally misinterprets the prompt and returns an obviously wrong
result — most commonly a darker skin tone than the character actually has. The
chain currently has no guard: the bad still flows into video gen and the user
only notices many minutes later.

We need a cheap, automated check between *image_edit done* and *video gen
start* that catches the obvious cases and lets the user decide on borderline
ones.

## What gets compared

For every clip with `idx > 0`:

| Role     | File                                 | Origin                             |
|----------|--------------------------------------|------------------------------------|
| ref      | `frames/frame_{N-1:03d}.png`         | last-frame extracted from clip N-1 |
| refined  | `refined/refined_{N-1:03d}.jpg`      | Grok image-edit output             |

Clip `001` (idx 0) has no image_edit, so no skin check runs.

## Decision logic

Claude returns JSON:

```json
{
  "skin_similarity": 92,
  "lighting_similarity": 85,
  "verdict": "pass" | "ask",
  "rationale": "1-2 sentences"
}
```

Rules:

- `skin_similarity >= threshold` (default 95) → auto-pass, continue to video.
- Else → callback `on_skin_low(clip_id, result, stitched_path) -> "accept"|"retry"|"abort"`.
  - `accept` → continue to video gen with current refined image.
  - `retry` → discard refined image, re-send image_edit task (loop). Capped by
    `max_retries` (default 2). When the cap is hit and the last verdict is
    still `ask`, treat it as `abort`.
  - `abort` → mark clip failed, stop chain.
- Lighting is reported in the rationale but does NOT gate the decision. Per
  user: when skin tone matches, lighting follows.

Failure modes (Claude CLI unavailable, timeout, bad JSON, ffmpeg crash) →
treated as `skin_similarity=0` so the user always sees a dialog instead of
silently continuing.

## Architecture

Plan **A** (split tasks):

```
chain_runner (per clip, idx > 0):
    if refined exists and non-empty: skip to video
    loop attempt 1..max_retries+1:
        send TaskJson(kind="image_edit") to worker
        wait done → refined_NNN.jpg saved
        stitch(ref, refined) → debug/skin/clip_NNN.jpg
        result = check_skin(stitched)
        if result.skin >= threshold: break
        verdict = on_skin_low(clip_id, result, stitched)
        if verdict == "accept": break
        if verdict == "abort": fail clip; return
        # retry: delete refined, loop
    send TaskJson(kind="video") to worker
    wait done → clip_NNN.mp4 saved
    extract frame
```

Worker no longer batches image_edit + video into one `_factory`. Each `kind`
maps to its own factory call, both wrapped by the existing `run_with_retry`
(Brave-kill retry stays only for hard failures — CDP loss, page crash).

## Components

### `core/skin_check.py` (new)

Two pure functions, no shared state:

```python
@dataclass(frozen=True)
class SkinResult:
    skin: int           # 0..100
    lighting: int       # 0..100
    verdict: str        # "pass" | "ask"
    rationale: str
    raw_stdout: str     # for logs

def stitch_side_by_side(ref: Path, refined: Path, out: Path, *, ffmpeg: str = "ffmpeg") -> None
def check_skin(stitched: Path, *, threshold: int = 95, timeout_s: int = 120) -> SkinResult
```

- `stitch_side_by_side` runs `ffmpeg -y -i ref -i refined -filter_complex
  "[0:v]scale=-1:768[a];[1:v]scale=-1:768[b];[a][b]hstack=inputs=2" -q:v 3 out`.
- `check_skin` calls `claude --print --dangerously-skip-permissions` with
  stdin instruction (Vietnamese prompt, JSON-only output). It uses the shared
  `core.claude_cli` helper, which clears `ANTHROPIC_API_KEY` and proxy env vars.
- Parsing failures → `SkinResult(skin=0, lighting=0, verdict="ask",
  rationale="<error>")` so the caller always gets a valid object.

### `workers/task_contract.py`

```python
class TaskJson(BaseModel):
    task_id: str
    kind: Literal["image_edit", "video"] = "video"
    ref_path: str
    output_path: str
    cdp_url: str
    cdp_base_url: str
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    # image_edit only
    image_edit_prompt: str | None = None
    # video only
    prompt: str = ""
    duration: int = 10
```

Removes the no-longer-needed `refined_ref_path` and `reuse_refined_ref` fields;
chain_runner owns those filesystem decisions now.

### `workers/video_chain_worker.py`

`_process_task` dispatches on `task.kind`:

- `image_edit`: factory calls `image_engine.gen_image_with_refs(ref=ref_path,
  output=output_path, prompt=image_edit_prompt)`. On done emits
  `TASK DONE {"success": 1, "kind": "image_edit", "path": output_path,
  "attempts": N}`.
- `video`: factory calls `engine.gen_video(prompt, ref_path, settings)`.
  Existing video resolution-downgrade warning still emits before TASK DONE.

### `core/chain_runner.py`

New per-clip orchestration (idx > 0):

- Wraps existing send-task helper in a small skin-check loop.
- New callback signature:
  `on_skin_low(clip_id, result: SkinResult, stitched_path: Path) -> str`.
- Stitched debug images go to `<project>/debug/skin/clip_{N:03d}_attempt_{a}.jpg`.
- Threshold + max_retries + timeout pulled from `config["defaults"]["skin_check"]`.

If `image_edit` task fails (worker-level), the clip is marked failed exactly
like today — skin check only runs after a successful image_edit task.

### `core/config.py` + `config.yaml`

Add to `defaults`:

```yaml
defaults:
  skin_check:
    enabled: true
    threshold: 95
    max_retries: 2
    timeout_s: 120
```

Merged via existing `_merge` (already deep-merges per top-level section; need
to confirm nested-dict merge works for `defaults.skin_check`).

### `app/main.py` + `ui/main_window.py`

`_RunnerThread` gains `ask_skin_low` signal + `request_skin_decision`. Main
thread shows `QMessageBox` with the stitched thumbnail and three buttons:
"Continue (accept)" / "Retry image_edit" / "Abort chain". Decision routed back
via the same `_decision` / `_decision_event` pair as resolution_downgrade.

## Testing

- `tests/test_skin_check.py` (new):
  - `stitch_side_by_side` with two small JPGs → output exists, non-zero size,
    wider than each input (ffmpeg actually runs; skipped if no ffmpeg).
  - `check_skin` with subprocess mock: pass, ask, malformed JSON, timeout,
    FileNotFoundError → correct `SkinResult`.
- `tests/test_chain_runner.py` (updated):
  - Existing tests adapted: `_FakeWorker` now receives two tasks per clip
    (image_edit then video) when idx > 0.
  - New tests:
    - skin pass → no callback invoked, both tasks sent.
    - skin ask + accept → callback called, both tasks still sent.
    - skin ask + retry then pass → image_edit task sent twice, video sent once.
    - skin ask + abort → only image_edit task sent, clip failed.
    - max_retries exhausted → fail.
    - reuse existing refined → skin check still runs once (cached file is the
      "result of the last successful image_edit", so we check it too).
- `tests/test_video_chain_worker.py` (updated): dispatch test for both kinds.

## Migration / Compatibility

- `TaskJson` schema change is internal — only worker + chain_runner produce/
  consume it. No on-disk format change.
- `state.json` schema unchanged (still records `refined_ref` on each clip).
- Existing projects resume cleanly: if `refined/refined_NNN.jpg` exists on
  disk, chain_runner reuses it and skips straight to skin check (no re-edit).

## Out of scope

- Lighting-only verdicts (per user: skin gate is enough).
- Configurable thresholds per project (single global threshold for now).
- Caching Claude verdicts (rerunning is cheap — Claude pro/max quota).
- Showing the stitched image inline in the GUI log (just a path link).

## Deferred (follow-up)

- **Stitched thumbnail in the skin-low dialog.** Currently the `QMessageBox`
  shows the stitched file path as text. Future enhancement: load the JPG via
  `QPixmap` and call `box.setIconPixmap(pixmap.scaledToWidth(480))` so the
  user can eyeball the side-by-side without leaving the app.
- **End-to-end test against real Claude CLI + ffmpeg.** Unit tests mock the
  subprocess; an opt-in slow test could exercise the full pipeline on a
  known-good and a known-bad pair.
