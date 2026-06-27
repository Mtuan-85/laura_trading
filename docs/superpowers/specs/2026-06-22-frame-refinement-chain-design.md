# Frame Refinement Chain

**Date:** 2026-06-22
**Status:** approved

## Pipeline

- Clip 1 uses `input/ref.png` directly.
- After each successful clip, FFmpeg extracts `frames/frame_NNN.png`.
- For clip 2 and later, the worker:
  1. Loads the previous raw frame.
  2. Runs the direct image-with-reference Grok flow using the composed prompt
     from `input/image_edit.json` — the bare `"prompt"` field followed by a
     natural-language English block describing `"skin_tone_profile"` (when
     present): description, palette hex colors, Y-luminance scale, and lighting.
  3. Saves `refined/refined_NNN.jpg`.
  4. Uses that refined image as the reference for video generation.
- The direct image edit flow does not use masonry, Quality/Speed, or candidate
  selection. The `skin_tone_profile` block is flattened into the prompt text
  itself, not configured as a separate Grok setting.

## Resume

- A valid existing refined image is reused.
- A failed/interrupted video task is regenerated.
- If the refined image is absent, refinement runs before video generation.
- `image_edit.json` is copied into each project so resume is self-contained.

## State

Each clip records its raw reference and optional refined reference. The raw
frame remains associated with the preceding generated clip.

## Non-Scope

`video_driff_color_v4` remains untouched.
