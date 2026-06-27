# Video Resolution Downgrade Detection — Design Spec

**Date:** 2026-06-20
**Status:** approved
**Scope:** Detect when Grok serves 480p instead of the requested 720p, pause the
chain, and let the user decide whether to accept the downgrade or abort.

## Motivation

Grok auto-downgrades video output from 720p → 480p when the daily quota is
exhausted. Today the chain happily downloads the 480p file without telling the
user, who only notices after watching the final concatenated video.

We want an in-flow check that:

1. Verifies the actual rendered video matches the requested resolution.
2. On downgrade, surfaces a modal popup so the user can decide:
   - **Accept 480p** — keep going at the lower quality (sticky for the rest of
     this chain — subsequent 480p clips will not prompt again).
   - **Abort chain** — stop the run, leave the partial 480p clip on disk for
     inspection.

Retry is not offered: once Grok downgrades, it keeps doing so until the quota
resets — retrying inside the same session is wasted compute.

## Detection

New action `verify_video_resolution(page, expected)` in
`engines/grok/actions.py`:

- Calls `page.evaluate()` to read `#sd-video.videoWidth` and `videoHeight`.
- `actual_p = min(width, height)` — works for both landscape (1280×720 → 720)
  and portrait (720×1280 → 720).
- `expected_p` parsed from `"720p"` config string.
- Returns `{"ok": True, "downgrade": True|False, "actual_p": int,
  "expected_p": int}`. The action **never blocks** — the 480p file is still
  downloaded so the user can inspect it before deciding.

Flow integration (`engines/grok/flows.py`): insert
`{"action": "verify_video_resolution", "from_config": "resolution"}` between
`wait_video_ready` and `download_to` in `text_to_video` and `image_to_video`.

When the action reports `downgrade=True`, the runner appends to
`state["warnings"]`:

```python
{"type": "resolution_downgrade", "actual_p": 480, "expected_p": 720}
```

## Worker → parent signalling

`engines/grok/engine.py`: after `runner.run()` completes, copy
`state["warnings"]` to `engine.last_warnings` so the worker can read it.

`workers/video_chain_worker.py`: before emitting `TASK DONE`, iterate
`engine.last_warnings` and emit one `EVENT` marker per warning:

```
EVENT {"type": "resolution_downgrade", "actual_p": 480, "expected_p": 720}
```

The order matters — emit `EVENT` before `TASK DONE` so the chain runner sees
the warning while processing the completed clip.

## Pause logic in ChainRunner

`core/chain_runner.py` adds:

- Instance state `self.accept_480p: bool = False` — sticky for the project run.
- `run()` parameter `on_resolution_downgrade: Callable[[str, int], str]` that
  returns `"accept"` or `"abort"`. Default = `lambda *a: "abort"` so headless
  callers (tests) fail closed.

For each clip, wrap `on_marker` so that an incoming
`EVENT {"type": "resolution_downgrade", ...}` mutates a per-clip
`clip_meta = {"downgrade": False, "actual_p": None}` dict.

After `_send_task_with_optional_timeout` returns with success **and**
`clip_meta["downgrade"]` is True:

- If `self.accept_480p` is False, call
  `on_resolution_downgrade(clip_id, clip_meta["actual_p"])`.
  - `"accept"` → set `self.accept_480p = True`, mark clip done, continue.
  - `"abort"` → mark clip as `failed` with reason
    `f"user_aborted_after_{actual_p}p"`, emit `clip_failed`, return.
- If `self.accept_480p` is already True, log and continue without prompting.

The clip file is treated as a normal success — frame extraction and the
project state machine see no difference from a 720p clip.

## UI bridge (PyQt6)

`app/main.py`:

`_RunnerThread` gains:

```python
ask_resolution_downgrade = pyqtSignal(str, int)  # clip_id, actual_p
_decision_event: threading.Event
_decision: str | None
```

- `on_decision(choice: str)` — slot called from the main thread to record the
  user's click and unblock the worker thread.
- `request_resolution_decision(clip_id, actual_p) -> str` — runs on the worker
  thread. Clears the event, emits the signal, blocks on `wait()`, returns the
  recorded decision.

In `main()`, connect `ask_resolution_downgrade` to a slot that shows a
`QMessageBox.Warning` with two buttons:

- **Accept 480p (tiếp tục)** → `thread.on_decision("accept")`
- **Abort chain** → `thread.on_decision("abort")`

Pass `runner.run(..., on_resolution_downgrade=thread.request_resolution_decision)`.

Popup text:

> **Grok hạ resolution xuống {actual_p}p**
> Clip {clip_id}: Grok không serve 720p (có thể hết daily quota).
> • Accept = tiếp tục chain ở {actual_p}p, không hỏi lại các clip sau.
> • Abort = dừng toàn bộ chain.

## Edge cases

- `#sd-video` not in DOM when verify runs → action returns
  `{"ok": False, "reason": "verify_video_resolution: #sd-video not ready"}`.
  Treated as a normal step failure (existing retry semantics apply). This
  shouldn't happen because verify runs after `wait_video_ready`, which already
  guarantees the element exists.
- `expected_p` cannot be parsed (e.g. config typo) → action returns ok=False
  with a clear reason; clip fails normally.
- User closes the popup without clicking → treated as `"abort"` (Qt's reject
  role).
- `accept_480p=True` carries only within a single ChainRunner instance. Each
  new project run starts fresh.

## Out of scope

- Persisting `accept_480p` across project runs.
- Retry-with-wait-for-quota-reset.
- Auto-switching resolution config to `480p` for remaining clips (we keep
  asking Grok for 720p; we just stop complaining when it ignores us).
- Detecting downgrade for the image-generation flows (only `set_video_*`
  flows have a resolution option to mismatch).

## Affected files

- `engines/grok/actions.py` — new action
- `engines/grok/flows.py` — add step to two flows
- `engines/grok/runner.py` — dispatch new action, track warnings in state
- `engines/grok/engine.py` — expose `last_warnings`
- `workers/video_chain_worker.py` — emit EVENT before TASK DONE
- `core/chain_runner.py` — pause logic + new callback param
- `app/main.py` — thread signal + QMessageBox slot
- `tests/test_chain_runner.py` — new tests for accept/abort/sticky paths
