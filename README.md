# Lisa LiveTrading

Chain video generator using Grok (Brave CDP). Fork of `story_video_making_v2`.

The app takes a reference image + a `prompts.json`, then generates one Grok
video per prompt, using the last frame of clip N as the reference for clip
N+1. All clips are concatenated with a 0.5s xfade into `final.mp4`.

## Setup
1. `python -m venv .venv && .venv/Scripts/activate`
2. `pip install -r requirements.txt`
3. Make sure `ffmpeg` and `ffprobe` are on PATH.
4. Launch Brave with CDP enabled — copy `launch_brave.bat` from `story_video_making_v2` if needed (default debug port: 9222).
5. Log in to grok.com/imagine in that Brave instance.
6. `python -m app.main`

## Usage

In the GUI:
- Pick a reference PNG/JPG.
- Pick a `prompts.json` of the form `[{"id": 1, "prompt": "..."}, ...]`.
- Choose aspect (default `9:16`) and duration (default `10`).
- Choose **Start** and **Count** to run only a slice of the prompt file.
  `Start=21`, `Count=10` runs prompt items 21-30 by file order; `Count=0`
  runs from Start through the end.
- Click **Start**.

A `project_YYYYMMDD_HHMMSS/` folder is created next to the reference image,
containing `input/`, `clips/`, `frames/`, `logs/`, `state.json`, and the
final `final.mp4`.

**Stop** preserves state. Re-running with the same folder resumes from the
first non-done clip (use the worker CLI directly if you want to resume; the
GUI always starts a new project).

## Running the tests

```
python -m pytest --basetemp=.pytest-tmp
```

Requires `ffmpeg` / `ffprobe` on PATH for the video tests.

## Design

See `docs/superpowers/specs/2026-06-18-lisa-livetrading-design.md` and the
matching plan at `docs/superpowers/plans/2026-06-18-lisa-livetrading-implementation.md`.

## Known limitations (Phase 1)

- Single concurrent chain at a time.
- Aspect ratio applies to whole chain (no per-prompt override).
- Brave must be launched manually with CDP enabled.
- ffmpeg must be on PATH.
- GUI always starts a new project folder — resume is currently driver-level only.
