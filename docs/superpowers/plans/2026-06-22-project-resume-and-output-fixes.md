# Project Resume and Output Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix audio/config/validation defects and make the GUI resume an existing project when its state or copied prompts file is selected.

**Architecture:** Add small project-selection helpers in `app/main.py`, keep persistence in `ChainProject`, and inject configured media binaries into existing utility calls. Extend video concatenation to normalize and crossfade both video and audio streams.

**Tech Stack:** Python 3.11+, PyQt6, Pydantic, FFmpeg/FFprobe, pytest.

---

### Task 1: Prompt validation and project selection

**Files:**
- Modify: `app/main.py`
- Modify: `ui/main_window.py`
- Test: `tests/test_app_project_setup.py`

- [ ] Write focused failing tests for duplicate IDs, external prompt project creation, `state.json` resume, and `input/prompts.json` resume.
- [ ] Run only `tests/test_app_project_setup.py` and verify expected failures.
- [ ] Add prompt validation and project-path resolution helpers.
- [ ] Cache the prompt selection/project pair in the GUI start handler.
- [ ] Change the prompts picker filter to allow `state.json`.
- [ ] Run only `tests/test_app_project_setup.py`.

### Task 2: Configured FFmpeg path

**Files:**
- Modify: `core/chain_runner.py`
- Modify: `app/main.py`
- Test: `tests/test_chain_runner.py`
- Test: `tests/test_app_project_setup.py`

- [ ] Write failing tests proving configured FFmpeg/FFprobe paths reach frame extraction and concatenation.
- [ ] Run the new focused tests and verify expected failures.
- [ ] Pass `config["ffmpeg"]["path"]` and optional `ffprobe_path` through wrappers.
- [ ] Run the focused tests.

### Task 3: Preserve audio during concatenation

**Files:**
- Modify: `utils/video_concat.py`
- Test: `tests/test_video_concat.py`

- [ ] Write a failing media test asserting the final file contains an audio stream.
- [ ] Run that single test and verify failure.
- [ ] Normalize video/audio inputs and build paired `xfade`/`acrossfade` filters.
- [ ] Run `tests/test_video_concat.py`.

### Task 4: Native folder opening and collision-safe minute names

**Files:**
- Modify: `utils/paths.py`
- Modify: `ui/main_window.py`
- Test: `tests/test_app_project_setup.py`

- [ ] Write focused tests for minute names and collision suffixes.
- [ ] Run the tests and verify failure.
- [ ] Implement minute naming with `_02`, `_03` collision suffixes.
- [ ] Open folders using `os.startfile` on Windows with the existing browser fallback elsewhere.
- [ ] Run focused tests and compile touched modules.

### Task 5: Verify changed behavior

- [ ] Run only:
  - `tests/test_app_project_setup.py`
  - `tests/test_chain_runner.py`
  - `tests/test_video_concat.py`
- [ ] Run `python -m py_compile` for touched Python files.
- [ ] Inspect `git diff --check`.
- [ ] Report the unchanged FlowRunner masking behavior precisely.
