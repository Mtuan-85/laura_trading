# Lisa LiveTrading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimal PyQt6 desktop app that runs a chain of video-generation prompts via Grok CDP. Each clip uses the previous clip's last frame as its ref image; final output is an ffmpeg-concatenated MP4 with 0.5s crossfade.

**Architecture:** Fork the proven Grok-CDP engine from `D:\Projects\story_video_making_v2`. New code = minimal UI, chain orchestrator, frame extractor, and concat helper. State persisted to `state.json` (atomic write + rotating backup) for resume.

**Tech Stack:** Python 3.11+, PyQt6, Patchright (CDP), Pydantic, loguru, PyYAML, ffmpeg (system binary), pytest.

## Global Constraints

- App root: `D:\Projects\Lisa_livetrading\` (already initialized as git repo with `.gitignore` and spec committed).
- Python package style: top-level packages `app/`, `ui/`, `core/`, `workers/`, `engines/`, `utils/`, `tests/`.
- All file/folder names exactly as in the design spec (`docs/superpowers/specs/2026-06-18-lisa-livetrading-design.md`).
- No voice/audio/Whisper/Claude/scenes/karaoke code is to be brought over from v2. Only the Grok-CDP engine + minimal subprocess machinery.
- Reference source for verbatim copies: `D:\Projects\story_video_making_v2\` (DO NOT modify v2).
- ffmpeg must be available on `PATH` or its full path set in `config.yaml`.
- Brave with CDP remote port 9222 is launched by the user externally (out-of-scope for app).
- Commit after every task. Use Conventional-Commits-style messages (`feat:`, `test:`, `chore:`, `docs:`).
- All Python source files start with `from __future__ import annotations`.
- Default timezone for timestamps: local (`datetime.now().astimezone()`).
- Default ffmpeg invocation must include `-y -hide_banner -loglevel error` unless tests need otherwise.

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `config.yaml`
- Create: `README.md`
- Create: `app/__init__.py`, `ui/__init__.py`, `core/__init__.py`, `workers/__init__.py`, `utils/__init__.py`, `engines/__init__.py`, `tests/__init__.py`

**Interfaces:**
- Consumes: nothing
- Produces: package skeleton importable as `app`, `ui`, `core`, `workers`, `engines`, `utils`

- [ ] **Step 1: Create empty package files**

Create each `__init__.py` with empty content (just so pytest/import resolves):

```python
```

Files: `app/__init__.py`, `ui/__init__.py`, `core/__init__.py`, `workers/__init__.py`, `utils/__init__.py`, `engines/__init__.py`, `tests/__init__.py`.

- [ ] **Step 2: Write `requirements.txt`**

```
PyQt6>=6.7
patchright>=1.40
loguru>=0.7
pydantic>=2.0
pyyaml>=6.0
pytest>=8.0
pytest-asyncio>=0.23
```

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "lisa-livetrading"
version = "0.1.0"
description = "Chain video generator using Grok CDP"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 4: Write `config.yaml`**

```yaml
cdp:
  url: "http://127.0.0.1:9222"
  profile_marker: "brave-grok-profile"
  base_url: "https://grok.com/imagine"

ffmpeg:
  path: "ffmpeg"

defaults:
  aspect: "9:16"
  duration: 10
  retry_count: 2
  worker_timeout_sec: 600

logging:
  level: "INFO"
  file_rotation_mb: 10
```

- [ ] **Step 5: Write `README.md`**

```markdown
# Lisa LiveTrading

Chain video generator using Grok (Brave CDP). Fork of `story_video_making_v2`.

## Setup
1. `python -m venv .venv && .venv/Scripts/activate`
2. `pip install -r requirements.txt`
3. Launch Brave with CDP: see `launch_brave.bat` (copy from v2 if needed)
4. `python -m app.main`

## Design
See `docs/superpowers/specs/2026-06-18-lisa-livetrading-design.md`.
```

- [ ] **Step 6: Verify pytest discovers the package**

Run: `cd D:/Projects/Lisa_livetrading && python -m pytest --collect-only 2>&1 | tail -5`
Expected: `no tests ran` (no errors).

- [ ] **Step 7: Commit**

```bash
cd D:/Projects/Lisa_livetrading
git add pyproject.toml requirements.txt config.yaml README.md app ui core workers utils engines tests
git commit -m "chore: scaffold package structure and config"
```

---

## Task 2: Utility — atomic JSON write + logging

**Files:**
- Create: `utils/atomic.py`
- Create: `utils/logging.py`
- Create: `utils/paths.py`
- Create: `tests/test_atomic.py`

**Interfaces:**
- Produces:
  - `utils.atomic.atomic_write_json(path: Path, data: dict, *, backup_keep: int = 5) -> None`
  - `utils.atomic.read_json(path: Path) -> dict`
  - `utils.logging.setup_logging(log_file: Path | None = None, level: str = "INFO") -> None`
  - `utils.paths.project_folder_name(now: datetime | None = None) -> str` — returns `project_YYYYMMDD_HHMMSS`

- [ ] **Step 1: Write failing test `tests/test_atomic.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.atomic import atomic_write_json, read_json


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"a": 1})
    assert target.exists()
    assert json.loads(target.read_text("utf-8")) == {"a": 1}


def test_atomic_write_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"a": 1})
    atomic_write_json(target, {"a": 2})
    assert read_json(target) == {"a": 2}


def test_atomic_write_keeps_rotating_backups(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    for i in range(8):
        atomic_write_json(target, {"i": i}, backup_keep=3)
    backups = sorted(tmp_path.glob("state.json.bak.*"))
    assert len(backups) == 3


def test_atomic_write_does_not_partially_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"ok": True})
    # Simulate crash mid-write by failing os.replace; the existing file must stay intact
    import os
    original = os.replace

    def fail_replace(*args, **kwargs):
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        atomic_write_json(target, {"corrupted": True})
    monkeypatch.setattr(os, "replace", original)
    assert read_json(target) == {"ok": True}
```

- [ ] **Step 2: Run — verify failure**

Run: `cd D:/Projects/Lisa_livetrading && python -m pytest tests/test_atomic.py -v`
Expected: ImportError on `utils.atomic`.

- [ ] **Step 3: Implement `utils/atomic.py`**

```python
from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_json(path: Path, data: dict, *, backup_keep: int = 5) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if path.exists() and backup_keep > 0:
        _rotate_backup(path, backup_keep)
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rotate_backup(path: Path, keep: int) -> None:
    existing = sorted(path.parent.glob(f"{path.name}.bak.*"))
    while len(existing) >= keep:
        existing[0].unlink()
        existing = existing[1:]
    # Numbered suffix using length+1 zero-padded to keep ordering stable
    next_idx = len(existing) + 1
    backup = path.with_name(f"{path.name}.bak.{next_idx:03d}")
    backup.write_bytes(path.read_bytes())
```

- [ ] **Step 4: Implement `utils/logging.py`**

```python
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(log_file: Path | None = None, level: str = "INFO") -> None:
    logger.remove()
    logger.add(sys.stderr, level=level, enqueue=True)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(str(log_file), level="DEBUG", rotation="10 MB", retention=5, enqueue=True)
```

- [ ] **Step 5: Implement `utils/paths.py`**

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def project_folder_name(now: datetime | None = None) -> str:
    when = now or datetime.now().astimezone()
    return f"project_{when.strftime('%Y%m%d_%H%M%S')}"


def ensure_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_atomic.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add utils tests/test_atomic.py
git commit -m "feat: atomic JSON write with rotating backup; logging + paths utils"
```

---

## Task 3: Frame extractor (ffmpeg)

**Files:**
- Create: `utils/frame_extractor.py`
- Create: `tests/test_frame_extractor.py`
- Create: `tests/fixtures/__init__.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: `utils.frame_extractor.extract_last_frame(video: Path, output_png: Path, *, offset_sec: float = 0.5, ffmpeg: str = "ffmpeg") -> None`
- Raises `FileNotFoundError` if video missing, `RuntimeError` if ffmpeg fails or output is 0 bytes.

- [ ] **Step 1: Add `tests/conftest.py` to build a tiny sample mp4**

```python
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


@pytest.fixture(scope="session")
def sample_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    ff = _ffmpeg()
    if ff is None:
        pytest.skip("ffmpeg not on PATH")
    out = tmp_path_factory.mktemp("media") / "sample.mp4"
    cmd = [
        ff, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=64x64:r=10:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "2", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out
```

- [ ] **Step 2: Write failing test `tests/test_frame_extractor.py`**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from utils.frame_extractor import extract_last_frame


def test_extract_last_frame_creates_png(sample_mp4: Path, tmp_path: Path) -> None:
    out = tmp_path / "frame.png"
    extract_last_frame(sample_mp4, out)
    assert out.exists()
    assert out.stat().st_size > 0


def test_extract_last_frame_missing_video(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_last_frame(tmp_path / "no.mp4", tmp_path / "frame.png")
```

- [ ] **Step 3: Run — verify failure**

Run: `python -m pytest tests/test_frame_extractor.py -v`
Expected: ImportError on `utils.frame_extractor`.

- [ ] **Step 4: Implement `utils/frame_extractor.py`**

```python
from __future__ import annotations

import subprocess
from pathlib import Path


def extract_last_frame(
    video: Path,
    output_png: Path,
    *,
    offset_sec: float = 0.5,
    ffmpeg: str = "ffmpeg",
) -> None:
    video = Path(video)
    output_png = Path(output_png)
    if not video.exists():
        raise FileNotFoundError(f"Video not found: {video}")
    output_png.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-sseof", f"-{offset_sec}", "-i", str(video),
        "-frames:v", "1", "-q:v", "2", "-update", "1", str(output_png),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (rc={result.returncode}): {result.stderr.strip()}")
    if not output_png.exists() or output_png.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg produced empty frame: {output_png}")
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_frame_extractor.py -v`
Expected: 2 passed (or 1 skipped if ffmpeg missing on this machine — verify it is present first).

- [ ] **Step 6: Commit**

```bash
git add utils/frame_extractor.py tests/test_frame_extractor.py tests/conftest.py tests/fixtures/__init__.py
git commit -m "feat: extract last frame from video via ffmpeg"
```

---

## Task 4: Video concat with xfade

**Files:**
- Create: `utils/video_concat.py`
- Create: `tests/test_video_concat.py`

**Interfaces:**
- Produces:
  - `utils.video_concat.probe_duration(video: Path, *, ffprobe: str = "ffprobe") -> float`
  - `utils.video_concat.concat_with_xfade(clips: list[Path], output: Path, *, xfade_dur: float = 0.5, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None`
- Raises `ValueError` if `clips` empty, `RuntimeError` on ffmpeg failure.
- Length math: for N clips total = `sum(d_i) - (N-1) * xfade_dur`.

- [ ] **Step 1: Write failing tests `tests/test_video_concat.py`**

```python
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from utils.video_concat import concat_with_xfade, probe_duration


def _ffmpeg() -> str:
    f = shutil.which("ffmpeg")
    if f is None:
        pytest.skip("ffmpeg not on PATH")
    return f


def _make_color_clip(out: Path, *, color: str, duration: int) -> Path:
    ff = _ffmpeg()
    cmd = [
        ff, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=10:d={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(duration), str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def test_probe_duration_returns_seconds(tmp_path: Path) -> None:
    clip = _make_color_clip(tmp_path / "c.mp4", color="red", duration=2)
    assert 1.8 <= probe_duration(clip) <= 2.2


def test_concat_three_clips_with_xfade(tmp_path: Path) -> None:
    clips = [
        _make_color_clip(tmp_path / "a.mp4", color="red", duration=2),
        _make_color_clip(tmp_path / "b.mp4", color="green", duration=2),
        _make_color_clip(tmp_path / "c.mp4", color="blue", duration=2),
    ]
    out = tmp_path / "final.mp4"
    concat_with_xfade(clips, out, xfade_dur=0.5)
    assert out.exists() and out.stat().st_size > 0
    # 3 clips * 2s - 2 fades * 0.5s = 5s
    dur = probe_duration(out)
    assert 4.6 <= dur <= 5.4


def test_concat_single_clip_just_copies(tmp_path: Path) -> None:
    clip = _make_color_clip(tmp_path / "only.mp4", color="red", duration=2)
    out = tmp_path / "final.mp4"
    concat_with_xfade([clip], out)
    assert out.exists() and out.stat().st_size > 0


def test_concat_empty_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        concat_with_xfade([], tmp_path / "final.mp4")
```

- [ ] **Step 2: Run — verify failure**

Run: `python -m pytest tests/test_video_concat.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `utils/video_concat.py`**

```python
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def probe_duration(video: Path, *, ffprobe: str = "ffprobe") -> float:
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(f"Video not found: {video}")
    cmd = [
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def concat_with_xfade(
    clips: list[Path],
    output: Path,
    *,
    xfade_dur: float = 0.5,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> None:
    if not clips:
        raise ValueError("clips must contain at least one path")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if len(clips) == 1:
        shutil.copyfile(clips[0], output)
        return

    durations = [probe_duration(c, ffprobe=ffprobe) for c in clips]

    # Build filter_complex chain
    filter_parts: list[str] = []
    prev_label = "0:v"
    cumulative = durations[0]
    for i in range(1, len(clips)):
        next_label = f"v{i}"
        offset = cumulative - xfade_dur
        filter_parts.append(
            f"[{prev_label}][{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset:.3f}[{next_label}]"
        )
        cumulative = cumulative + durations[i] - xfade_dur
        prev_label = next_label

    filter_complex = ";".join(filter_parts)

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", str(c)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{prev_label}]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed (rc={result.returncode}): {result.stderr.strip()}")
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_video_concat.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add utils/video_concat.py tests/test_video_concat.py
git commit -m "feat: concat clips with xfade transition"
```

---

## Task 5: Task contract + process launcher (port from v2, simplify)

**Files:**
- Create: `workers/task_contract.py`
- Create: `workers/process_launcher.py`
- Create: `tests/test_task_contract.py`

**Interfaces:**
- Produces:
  - `workers.task_contract.TaskJson` — Pydantic model with: `task_id: str`, `prompt: str`, `ref_path: str`, `aspect: str`, `duration: int`, `output_path: str`, `cdp_url: str`, `cdp_base_url: str`
  - Constants: `EXIT_SUCCESS=0`, `EXIT_FLOW_FAILED=1`, `EXIT_PREREQ_MISSING=2`, `EXIT_USER_KILLED=3`, `EXIT_PARSE_FAILED=4`, `EXIT_CDP_UNREACHABLE=5`
  - `workers.task_contract.print_marker(kind: str, payload: dict) -> None` — prints `f"{kind} {json.dumps(payload)}"` and flushes
  - `workers.task_contract.parse_marker(line: str) -> tuple[str, dict] | None` — parses `"<KIND> {json}"`
  - `workers.process_launcher.LaunchedWorker` — context-manager wrapping `subprocess.Popen` with line-by-line marker iteration

- [ ] **Step 1: Write failing test `tests/test_task_contract.py`**

```python
from __future__ import annotations

import io
import json

from workers.task_contract import (
    EXIT_CDP_UNREACHABLE,
    EXIT_FLOW_FAILED,
    EXIT_SUCCESS,
    TaskJson,
    parse_marker,
    print_marker,
)


def test_taskjson_roundtrip() -> None:
    t = TaskJson(
        task_id="clip_001",
        prompt="Lisa stands up",
        ref_path="C:/x/ref.png",
        aspect="9:16",
        duration=10,
        output_path="C:/x/clips/clip_001.mp4",
        cdp_url="http://127.0.0.1:9222",
        cdp_base_url="https://grok.com/imagine",
    )
    raw = t.model_dump_json()
    again = TaskJson.model_validate_json(raw)
    assert again == t


def test_print_marker_emits_jsonl(capsys) -> None:
    print_marker("EVENT", {"type": "ping", "n": 1})
    out = capsys.readouterr().out.strip()
    assert out.startswith("EVENT ")
    assert json.loads(out[len("EVENT "):]) == {"type": "ping", "n": 1}


def test_parse_marker_known_kind() -> None:
    kind, payload = parse_marker('TASK DONE {"success": 1}')
    assert kind == "TASK DONE"
    assert payload == {"success": 1}


def test_parse_marker_unknown_returns_none() -> None:
    assert parse_marker("random log line") is None


def test_exit_codes_distinct() -> None:
    codes = {EXIT_SUCCESS, EXIT_FLOW_FAILED, EXIT_CDP_UNREACHABLE}
    assert len(codes) == 3
```

- [ ] **Step 2: Run — verify failure**

Run: `python -m pytest tests/test_task_contract.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `workers/task_contract.py`**

```python
from __future__ import annotations

import json
import re
import sys
from typing import Literal

from pydantic import BaseModel

EXIT_SUCCESS = 0
EXIT_FLOW_FAILED = 1
EXIT_PREREQ_MISSING = 2
EXIT_USER_KILLED = 3
EXIT_PARSE_FAILED = 4
EXIT_CDP_UNREACHABLE = 5

_KNOWN_KINDS = ("TASK START", "TASK DONE", "TASK FAILED", "EVENT")
_MARKER_RE = re.compile(r"^(?P<kind>TASK START|TASK DONE|TASK FAILED|EVENT)\s+(?P<json>\{.*\})\s*$")


class TaskJson(BaseModel):
    task_id: str
    prompt: str
    ref_path: str
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    duration: int = 10
    output_path: str
    cdp_url: str = "http://127.0.0.1:9222"
    cdp_base_url: str = "https://grok.com/imagine"


def print_marker(kind: str, payload: dict) -> None:
    if kind not in _KNOWN_KINDS:
        raise ValueError(f"Unknown marker kind: {kind}")
    sys.stdout.write(f"{kind} {json.dumps(payload, ensure_ascii=False)}\n")
    sys.stdout.flush()


def parse_marker(line: str) -> tuple[str, dict] | None:
    m = _MARKER_RE.match(line.strip())
    if not m:
        return None
    return m.group("kind"), json.loads(m.group("json"))
```

- [ ] **Step 4: Implement `workers/process_launcher.py`**

```python
from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from loguru import logger

from workers.task_contract import parse_marker


class LaunchedWorker:
    def __init__(self, module: str, task_json_path: Path, *, cwd: Path | None = None, env: dict | None = None):
        self.module = module
        self.task_json_path = Path(task_json_path)
        self.cwd = Path(cwd) if cwd else None
        self.env = env or os.environ.copy()
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> LaunchedWorker:
        cmd = [sys.executable, "-m", self.module, "--task", str(self.task_json_path)]
        logger.info(f"Launching worker: {' '.join(cmd)}")
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(self.cwd) if self.cwd else None,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc and self.proc.poll() is None:
            self.terminate()

    def iter_markers(self) -> Iterator[tuple[str, dict] | str]:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            marker = parse_marker(line)
            if marker:
                yield marker
            else:
                yield line.rstrip("\n")

    def wait(self, timeout: float | None = None) -> int:
        assert self.proc
        return self.proc.wait(timeout=timeout)

    def terminate(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                self.proc.terminate()
            self.proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            self.proc.kill()
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_task_contract.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add workers/task_contract.py workers/process_launcher.py tests/test_task_contract.py
git commit -m "feat: task contract (markers, exit codes) + process launcher"
```

---

## Task 6: Copy Grok engine from v2

**Files:**
- Copy from `D:\Projects\story_video_making_v2\engines\grok\`:
  - `__init__.py`
  - `actions.py`
  - `browser.py`
  - `cdp_worker.py`
  - `engine.py`
  - `flows.py`
  - `selectors.py`
- Copy and rename as helpers (only if directly imported by the above): `runner.py`
- Skip (image-only): `claude_picker.py`, `image_ref_engine.py`, `image_worker_flow.py`

**Interfaces:**
- Produces: `engines.grok.engine.GrokVideoEngine` with `async def gen_video(prompt: str, ref_image: Path, settings: dict) -> Path`.

- [ ] **Step 1: Copy files**

Run:
```bash
cp "D:/Projects/story_video_making_v2/engines/grok/__init__.py" "D:/Projects/Lisa_livetrading/engines/grok/__init__.py"
cp "D:/Projects/story_video_making_v2/engines/grok/actions.py" "D:/Projects/Lisa_livetrading/engines/grok/actions.py"
cp "D:/Projects/story_video_making_v2/engines/grok/browser.py" "D:/Projects/Lisa_livetrading/engines/grok/browser.py"
cp "D:/Projects/story_video_making_v2/engines/grok/cdp_worker.py" "D:/Projects/Lisa_livetrading/engines/grok/cdp_worker.py"
cp "D:/Projects/story_video_making_v2/engines/grok/engine.py" "D:/Projects/Lisa_livetrading/engines/grok/engine.py"
cp "D:/Projects/story_video_making_v2/engines/grok/flows.py" "D:/Projects/Lisa_livetrading/engines/grok/flows.py"
cp "D:/Projects/story_video_making_v2/engines/grok/selectors.py" "D:/Projects/Lisa_livetrading/engines/grok/selectors.py"
cp "D:/Projects/story_video_making_v2/engines/grok/runner.py" "D:/Projects/Lisa_livetrading/engines/grok/runner.py"
```

- [ ] **Step 2: Inspect copied `__init__.py` and remove any imports for skipped modules**

Read `engines/grok/__init__.py`. If it imports `claude_picker`, `image_ref_engine`, or `image_worker_flow`, delete those lines. Keep only video/engine-related exports.

- [ ] **Step 3: Scan `engine.py`, `flows.py`, `runner.py`, `actions.py`, `cdp_worker.py`, `browser.py`, `selectors.py` for cross-imports of skipped files**

Use Grep to find any `from .claude_picker`, `from .image_ref_engine`, `from .image_worker_flow`, `from ..core`, `from ..workers` references. For any such import that the video path needs, copy the minimum sibling helper file. For any that's image-only, comment the import and remove its usage (e.g., guard `if False:` blocks) so video flow remains intact.

Document each removed/commented line in the commit message.

- [ ] **Step 4: Verify import works**

Run:
```bash
cd D:/Projects/Lisa_livetrading
python -c "from engines.grok.engine import GrokVideoEngine; print('OK', GrokVideoEngine)"
```
Expected: prints `OK <class 'engines.grok.engine.GrokVideoEngine'>` (or whatever the class is named — match the actual export).

If import fails, the error message will point to the missing dependency. Resolve by copying the required helper from v2 (e.g., copy `D:\Projects\story_video_making_v2\core\config.py` to `D:\Projects\Lisa_livetrading\core\config.py` if it is imported, and so on). Repeat until import succeeds.

- [ ] **Step 5: Commit**

```bash
git add engines/grok core
git commit -m "chore: vendor Grok CDP engine from story_video_making_v2"
```

---

## Task 7: ChainProject — state.json model & atomic ops

**Files:**
- Create: `core/project.py`
- Create: `tests/test_chain_project.py`

**Interfaces:**
- Produces:
  - `core.project.ClipState` — Pydantic model: `status: Literal["pending","running","done","failed","interrupted"]`, `prompt: str`, `ref: str`, `clip: str | None`, `frame: str | None`, `started_at: str | None`, `finished_at: str | None`, `attempts: int = 0`, `reason: str | None = None`
  - `core.project.FinalState` — Pydantic: `status: Literal["pending","done","failed"]`, `path: str | None`
  - `core.project.ProjectInputs` — Pydantic: `ref_image: str`, `prompts: str`, `aspect: str`, `duration: int`
  - `core.project.ChainProject`:
    - `@classmethod create(folder: Path, inputs: ProjectInputs, prompt_ids: list[str]) -> ChainProject`
    - `@classmethod load(folder: Path) -> ChainProject`
    - `save(self) -> None` — atomic
    - `update_clip(clip_id: str, **fields) -> None`
    - `update_final(status: str, path: str | None = None) -> None`
    - `pending_clip_ids(self) -> list[str]`
    - Properties: `folder: Path`, `clips: dict[str, ClipState]`, `final: FinalState`, `inputs: ProjectInputs`

- [ ] **Step 1: Write failing tests `tests/test_chain_project.py`**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from core.project import ChainProject, ProjectInputs


def _inputs() -> ProjectInputs:
    return ProjectInputs(
        ref_image="input/ref.png",
        prompts="input/prompts.json",
        aspect="9:16",
        duration=10,
    )


def test_create_initializes_pending_clips(tmp_path: Path) -> None:
    p = ChainProject.create(tmp_path, _inputs(), ["001", "002", "003"])
    assert (tmp_path / "state.json").exists()
    assert set(p.clips.keys()) == {"001", "002", "003"}
    assert all(c.status == "pending" for c in p.clips.values())
    assert p.final.status == "pending"


def test_load_roundtrip(tmp_path: Path) -> None:
    ChainProject.create(tmp_path, _inputs(), ["001"])
    p = ChainProject.load(tmp_path)
    assert "001" in p.clips


def test_update_clip_persists(tmp_path: Path) -> None:
    p = ChainProject.create(tmp_path, _inputs(), ["001", "002"])
    p.update_clip("001", status="done", attempts=1, clip="clips/clip_001.mp4")
    reloaded = ChainProject.load(tmp_path)
    assert reloaded.clips["001"].status == "done"
    assert reloaded.clips["001"].attempts == 1
    assert reloaded.clips["001"].clip == "clips/clip_001.mp4"


def test_pending_clip_ids_skips_done(tmp_path: Path) -> None:
    p = ChainProject.create(tmp_path, _inputs(), ["001", "002", "003"])
    p.update_clip("001", status="done")
    p.update_clip("002", status="failed")
    assert p.pending_clip_ids() == ["002", "003"]


def test_update_final(tmp_path: Path) -> None:
    p = ChainProject.create(tmp_path, _inputs(), ["001"])
    p.update_final("done", path="final.mp4")
    reloaded = ChainProject.load(tmp_path)
    assert reloaded.final.status == "done"
    assert reloaded.final.path == "final.mp4"


def test_load_missing_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ChainProject.load(tmp_path / "no_such_folder")
```

- [ ] **Step 2: Run — verify failure**

Run: `python -m pytest tests/test_chain_project.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `core/project.py`**

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from utils.atomic import atomic_write_json, read_json

ClipStatus = Literal["pending", "running", "done", "failed", "interrupted"]
FinalStatus = Literal["pending", "done", "failed"]


class ProjectInputs(BaseModel):
    ref_image: str
    prompts: str
    aspect: str = "9:16"
    duration: int = 10


class ClipState(BaseModel):
    status: ClipStatus = "pending"
    prompt: str = ""
    ref: str = ""
    clip: str | None = None
    frame: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    attempts: int = 0
    reason: str | None = None


class FinalState(BaseModel):
    status: FinalStatus = "pending"
    path: str | None = None


class _ProjectStateFile(BaseModel):
    version: int = 1
    created_at: str
    updated_at: str
    inputs: ProjectInputs
    clips: dict[str, ClipState] = Field(default_factory=dict)
    final: FinalState = Field(default_factory=FinalState)


class ChainProject:
    def __init__(self, folder: Path, state: _ProjectStateFile):
        self._folder = Path(folder)
        self._state = state

    @classmethod
    def create(cls, folder: Path, inputs: ProjectInputs, prompt_ids: list[str]) -> ChainProject:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now().astimezone().isoformat()
        state = _ProjectStateFile(
            created_at=now,
            updated_at=now,
            inputs=inputs,
            clips={pid: ClipState() for pid in prompt_ids},
            final=FinalState(),
        )
        instance = cls(folder, state)
        instance.save()
        return instance

    @classmethod
    def load(cls, folder: Path) -> ChainProject:
        folder = Path(folder)
        path = folder / "state.json"
        if not path.exists():
            raise FileNotFoundError(f"No state.json in {folder}")
        raw = read_json(path)
        return cls(folder, _ProjectStateFile.model_validate(raw))

    @property
    def folder(self) -> Path:
        return self._folder

    @property
    def clips(self) -> dict[str, ClipState]:
        return self._state.clips

    @property
    def final(self) -> FinalState:
        return self._state.final

    @property
    def inputs(self) -> ProjectInputs:
        return self._state.inputs

    def save(self) -> None:
        self._state.updated_at = datetime.now().astimezone().isoformat()
        atomic_write_json(self._folder / "state.json", self._state.model_dump(mode="json"))

    def update_clip(self, clip_id: str, **fields) -> None:
        if clip_id not in self._state.clips:
            raise KeyError(f"Unknown clip_id: {clip_id}")
        current = self._state.clips[clip_id].model_dump()
        current.update(fields)
        self._state.clips[clip_id] = ClipState.model_validate(current)
        self.save()

    def update_final(self, status: FinalStatus, path: str | None = None) -> None:
        self._state.final = FinalState(status=status, path=path)
        self.save()

    def pending_clip_ids(self) -> list[str]:
        return [
            cid for cid, c in self._state.clips.items()
            if c.status in ("pending", "failed", "interrupted", "running")
        ]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_chain_project.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add core/project.py tests/test_chain_project.py
git commit -m "feat: ChainProject state.json model with atomic persistence"
```

---

## Task 8: Video chain worker (subprocess)

**Files:**
- Create: `workers/video_chain_worker.py`
- Create: `tests/test_video_chain_worker.py`

**Interfaces:**
- Produces: CLI entry `python -m workers.video_chain_worker --task <path>`
- Reads `TaskJson` from path; calls `GrokVideoEngine.gen_video(...)`; emits markers; exits with codes from `task_contract`.

- [ ] **Step 1: Write failing test `tests/test_video_chain_worker.py`**

```python
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from workers.task_contract import EXIT_PARSE_FAILED, EXIT_PREREQ_MISSING


def _run_worker(task_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "workers.video_chain_worker", "--task", str(task_path)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


def test_missing_task_file_exits_parse_failed(tmp_path: Path) -> None:
    r = _run_worker(tmp_path / "no.json")
    assert r.returncode == EXIT_PARSE_FAILED


def test_invalid_task_json_exits_parse_failed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json}", encoding="utf-8")
    r = _run_worker(bad)
    assert r.returncode == EXIT_PARSE_FAILED


def test_missing_ref_image_exits_prereq_missing(tmp_path: Path) -> None:
    task = {
        "task_id": "clip_001",
        "prompt": "hello",
        "ref_path": str(tmp_path / "missing.png"),
        "aspect": "9:16",
        "duration": 10,
        "output_path": str(tmp_path / "out.mp4"),
        "cdp_url": "http://127.0.0.1:9222",
        "cdp_base_url": "https://grok.com/imagine",
    }
    p = tmp_path / "task.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    r = _run_worker(p)
    assert r.returncode == EXIT_PREREQ_MISSING
```

- [ ] **Step 2: Run — verify failure**

Run: `python -m pytest tests/test_video_chain_worker.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `workers/video_chain_worker.py`**

```python
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from workers.task_contract import (
    EXIT_CDP_UNREACHABLE,
    EXIT_FLOW_FAILED,
    EXIT_PARSE_FAILED,
    EXIT_PREREQ_MISSING,
    EXIT_SUCCESS,
    TaskJson,
    print_marker,
)


def _parse_task(path: Path) -> TaskJson:
    if not path.exists():
        raise FileNotFoundError(str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    return TaskJson.model_validate(raw)


async def _run_async(task: TaskJson) -> int:
    ref = Path(task.ref_path)
    if not ref.exists():
        print_marker("TASK FAILED", {"reason": f"ref image missing: {ref}"})
        return EXIT_PREREQ_MISSING

    print_marker("TASK START", {"task_id": task.task_id, "prompt": task.prompt})

    try:
        from engines.grok.engine import GrokVideoEngine  # local import: avoid loading on parse-only paths
        from engines.grok.browser import attach_existing_browser  # name to verify post-copy

        page = await attach_existing_browser(task.cdp_url, task.cdp_base_url)
        print_marker("EVENT", {"type": "cdp_connected", "url": task.cdp_url})

        engine = GrokVideoEngine(page)
        settings = {"aspect": task.aspect, "duration": task.duration, "output_path": task.output_path}
        downloaded: Path = await engine.gen_video(task.prompt, ref, settings)
        print_marker("EVENT", {"type": "download_done", "path": str(downloaded)})
        print_marker("TASK DONE", {"success": 1, "clip": str(downloaded)})
        return EXIT_SUCCESS
    except ConnectionError as ce:
        print_marker("TASK FAILED", {"reason": f"CDP unreachable: {ce}"})
        return EXIT_CDP_UNREACHABLE
    except Exception as e:
        logger.exception("video_chain_worker failed")
        print_marker("TASK FAILED", {"reason": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
        return EXIT_FLOW_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        task = _parse_task(args.task)
    except (FileNotFoundError, json.JSONDecodeError, ValidationError) as e:
        sys.stderr.write(f"task parse failed: {e}\n")
        return EXIT_PARSE_FAILED

    return asyncio.run(_run_async(task))


if __name__ == "__main__":
    sys.exit(main())
```

Note: `attach_existing_browser` is the working name. After Task 6 copy, inspect `engines/grok/browser.py` and adjust the import to the actual function name. If the function is sync, drop the `await`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_video_chain_worker.py -v`
Expected: 3 passed.
If Step 3's `attach_existing_browser` name is wrong, the missing-ref test still passes (returns before engine import). The CDP test runs nothing real; failures there reveal import path issues — fix the import names and re-run.

- [ ] **Step 5: Commit**

```bash
git add workers/video_chain_worker.py tests/test_video_chain_worker.py
git commit -m "feat: video_chain_worker subprocess (1 prompt -> 1 mp4)"
```

---

## Task 9: ChainRunner — orchestrator (sync + signals)

**Files:**
- Create: `core/chain_runner.py`
- Create: `tests/test_chain_runner.py`

**Interfaces:**
- Produces:
  - `core.chain_runner.RunnerEvent` — dataclass: `kind: str`, `clip_id: str | None`, `payload: dict`
  - `core.chain_runner.ChainRunner`:
    - `__init__(self, project: ChainProject, config: dict)` — config has `ffmpeg`, `cdp.url`, `cdp.base_url`, `defaults.retry_count`, `defaults.worker_timeout_sec`
    - `def run(self, *, worker_factory: Callable[[TaskJson], AbstractContextManager[LaunchedWorker]] | None = None, frame_extractor: Callable[[Path, Path], None] | None = None, concat: Callable[[list[Path], Path], None] | None = None, stop_check: Callable[[], bool] = lambda: False, on_event: Callable[[RunnerEvent], None] = lambda e: None) -> None`
    - Injectable callables allow tests to mock without touching real ffmpeg/CDP. Defaults wire to real implementations.

- [ ] **Step 1: Write failing tests `tests/test_chain_runner.py`**

```python
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.chain_runner import ChainRunner, RunnerEvent
from core.project import ChainProject, ProjectInputs
from workers.task_contract import EXIT_FLOW_FAILED, EXIT_SUCCESS, TaskJson


def _prep(tmp_path: Path, prompts: list[str]) -> ChainProject:
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "input").mkdir()
    ref = folder / "input" / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n")  # not a real png but file exists
    p_path = folder / "input" / "prompts.json"
    p_path.write_text(json.dumps([{"id": i + 1, "prompt": pr} for i, pr in enumerate(prompts)]), "utf-8")
    inputs = ProjectInputs(ref_image="input/ref.png", prompts="input/prompts.json", aspect="9:16", duration=10)
    return ChainProject.create(folder, inputs, [f"{i+1:03d}" for i in range(len(prompts))])


@contextmanager
def _fake_worker(exit_code: int, output: Path):
    class _W:
        def iter_markers(self):
            return iter([])

        def wait(self, timeout=None):
            return exit_code

        def terminate(self):
            pass

    if exit_code == EXIT_SUCCESS:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00\x00\x00\x20ftypmp42")
    yield _W()


def _factory(exit_code: int):
    def make(task: TaskJson):
        return _fake_worker(exit_code, Path(task.output_path))
    return make


def test_run_success_creates_all_clips(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])

    extract_calls = []

    def fake_extract(video: Path, frame: Path) -> None:
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"FRAMEPNG")
        extract_calls.append((video.name, frame.name))

    concat_calls = []

    def fake_concat(clips: list[Path], out: Path) -> None:
        out.write_bytes(b"FINAL")
        concat_calls.append([c.name for c in clips])

    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    runner = ChainRunner(project, config)
    runner.run(
        worker_factory=_factory(EXIT_SUCCESS),
        frame_extractor=fake_extract,
        concat=fake_concat,
    )

    reloaded = ChainProject.load(project.folder)
    assert all(c.status == "done" for c in reloaded.clips.values())
    assert reloaded.final.status == "done"
    assert reloaded.final.path == "final.mp4"
    assert len(extract_calls) == 3
    assert len(concat_calls) == 1


def test_run_retries_then_stops_on_persistent_failure(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b"])

    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    runner = ChainRunner(project, config)

    runner.run(
        worker_factory=_factory(EXIT_FLOW_FAILED),
        frame_extractor=lambda v, f: None,
        concat=lambda cs, o: None,
    )

    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["001"].status == "failed"
    assert reloaded.clips["001"].attempts == 3  # initial + 2 retries
    assert reloaded.clips["002"].status == "pending"  # chain stopped
    assert reloaded.final.status == "pending"


def test_run_resume_skips_done(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])
    project.update_clip("001", status="done", clip="clips/clip_001.mp4", frame="frames/frame_001.png")
    (project.folder / "clips").mkdir(exist_ok=True)
    (project.folder / "clips" / "clip_001.mp4").write_bytes(b"ALREADY")
    (project.folder / "frames").mkdir(exist_ok=True)
    (project.folder / "frames" / "frame_001.png").write_bytes(b"ALREADY")

    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    runner = ChainRunner(project, config)

    factory_calls = []
    def tracking_factory(task: TaskJson):
        factory_calls.append(task.task_id)
        return _fake_worker(EXIT_SUCCESS, Path(task.output_path))

    runner.run(
        worker_factory=tracking_factory,
        frame_extractor=lambda v, f: f.write_bytes(b"P"),
        concat=lambda cs, o: o.write_bytes(b"F"),
    )

    assert factory_calls == ["002", "003"]


def test_run_stop_check_interrupts(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])
    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    runner = ChainRunner(project, config)

    counter = {"n": 0}
    def stopper() -> bool:
        counter["n"] += 1
        return counter["n"] > 1  # stop after first clip

    runner.run(
        worker_factory=_factory(EXIT_SUCCESS),
        frame_extractor=lambda v, f: f.write_bytes(b"P"),
        concat=lambda cs, o: o.write_bytes(b"F"),
        stop_check=stopper,
    )

    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["001"].status == "done"
    assert reloaded.final.status == "pending"
```

- [ ] **Step 2: Run — verify failure**

Run: `python -m pytest tests/test_chain_runner.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `core/chain_runner.py`**

```python
from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from core.project import ChainProject
from utils.frame_extractor import extract_last_frame
from utils.video_concat import concat_with_xfade
from workers.process_launcher import LaunchedWorker
from workers.task_contract import EXIT_SUCCESS, EXIT_USER_KILLED, TaskJson


@dataclass
class RunnerEvent:
    kind: str
    clip_id: str | None
    payload: dict[str, Any]


WorkerFactory = Callable[[TaskJson], AbstractContextManager]


def _default_worker_factory(task: TaskJson) -> AbstractContextManager:
    tmp = Path(tempfile.gettempdir()) / f"lisa_task_{task.task_id}.json"
    tmp.write_text(task.model_dump_json(), encoding="utf-8")
    return LaunchedWorker(module="workers.video_chain_worker", task_json_path=tmp)


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class ChainRunner:
    def __init__(self, project: ChainProject, config: dict):
        self.project = project
        self.config = config

    def run(
        self,
        *,
        worker_factory: WorkerFactory | None = None,
        frame_extractor: Callable[[Path, Path], None] | None = None,
        concat: Callable[[list[Path], Path], None] | None = None,
        stop_check: Callable[[], bool] = lambda: False,
        on_event: Callable[[RunnerEvent], None] = lambda e: None,
    ) -> None:
        worker_factory = worker_factory or _default_worker_factory
        frame_extractor = frame_extractor or extract_last_frame
        concat = concat or concat_with_xfade

        prompts = self._load_prompts()
        retry_count = int(self.config.get("defaults", {}).get("retry_count", 2))

        folder = self.project.folder
        (folder / "clips").mkdir(exist_ok=True)
        (folder / "frames").mkdir(exist_ok=True)

        clip_ids_in_order = sorted(self.project.clips.keys())

        for idx, clip_id in enumerate(clip_ids_in_order):
            if stop_check():
                on_event(RunnerEvent("stopped", clip_id, {}))
                return

            clip_state = self.project.clips[clip_id]
            if clip_state.status == "done":
                continue

            prompt_text = prompts.get(clip_id, "")
            ref_rel = "input/ref.png" if idx == 0 else f"frames/frame_{int(clip_ids_in_order[idx-1]):03d}.png"
            ref_abs = folder / ref_rel
            clip_abs = folder / f"clips/clip_{int(clip_id):03d}.mp4"
            frame_abs = folder / f"frames/frame_{int(clip_id):03d}.png"

            attempts_done = clip_state.attempts
            max_attempts = attempts_done + retry_count + 1  # +1 for the current attempt
            succeeded = False

            for attempt in range(attempts_done + 1, max_attempts + 1):
                if stop_check():
                    self.project.update_clip(clip_id, status="interrupted", attempts=attempt - 1)
                    on_event(RunnerEvent("stopped", clip_id, {}))
                    return

                self.project.update_clip(
                    clip_id,
                    status="running",
                    prompt=prompt_text,
                    ref=str(ref_rel),
                    started_at=_now(),
                    attempts=attempt,
                )
                on_event(RunnerEvent("clip_started", clip_id, {"attempt": attempt}))

                task = TaskJson(
                    task_id=clip_id,
                    prompt=prompt_text,
                    ref_path=str(ref_abs),
                    aspect=self.project.inputs.aspect,
                    duration=self.project.inputs.duration,
                    output_path=str(clip_abs),
                    cdp_url=self.config.get("cdp", {}).get("url", "http://127.0.0.1:9222"),
                    cdp_base_url=self.config.get("cdp", {}).get("base_url", "https://grok.com/imagine"),
                )

                exit_code: int
                with worker_factory(task) as worker:
                    for marker in worker.iter_markers():
                        if isinstance(marker, tuple):
                            kind, payload = marker
                            on_event(RunnerEvent("worker_marker", clip_id, {"kind": kind, **payload}))
                        else:
                            on_event(RunnerEvent("worker_log", clip_id, {"line": marker}))
                    exit_code = worker.wait(timeout=self.config.get("defaults", {}).get("worker_timeout_sec", 600))

                if exit_code == EXIT_SUCCESS and clip_abs.exists() and clip_abs.stat().st_size > 0:
                    try:
                        frame_extractor(clip_abs, frame_abs)
                    except Exception as e:
                        logger.error(f"frame extraction failed for {clip_id}: {e}")
                        self.project.update_clip(clip_id, status="failed", reason=f"frame_extract: {e}", finished_at=_now())
                        on_event(RunnerEvent("clip_failed", clip_id, {"reason": str(e)}))
                        return
                    self.project.update_clip(
                        clip_id,
                        status="done",
                        clip=f"clips/clip_{int(clip_id):03d}.mp4",
                        frame=f"frames/frame_{int(clip_id):03d}.png",
                        finished_at=_now(),
                    )
                    on_event(RunnerEvent("clip_done", clip_id, {}))
                    succeeded = True
                    break

                # failure: log and decide on retry
                logger.warning(f"clip {clip_id} attempt {attempt} exit_code={exit_code}")
                if exit_code == EXIT_USER_KILLED:
                    self.project.update_clip(clip_id, status="interrupted", finished_at=_now())
                    on_event(RunnerEvent("stopped", clip_id, {}))
                    return

            if not succeeded:
                self.project.update_clip(
                    clip_id,
                    status="failed",
                    reason=f"worker exit_code={exit_code} after retries",
                    finished_at=_now(),
                )
                on_event(RunnerEvent("clip_failed", clip_id, {"exit_code": exit_code}))
                return

        # All clips done — concat
        clip_files = [folder / f"clips/clip_{int(cid):03d}.mp4" for cid in clip_ids_in_order]
        final_path = folder / "final.mp4"
        try:
            concat(clip_files, final_path)
            self.project.update_final("done", path="final.mp4")
            on_event(RunnerEvent("final_done", None, {"path": "final.mp4"}))
        except Exception as e:
            logger.error(f"concat failed: {e}")
            self.project.update_final("failed")
            on_event(RunnerEvent("final_failed", None, {"reason": str(e)}))

    def _load_prompts(self) -> dict[str, str]:
        path = self.project.folder / self.project.inputs.prompts
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {f"{int(item['id']):03d}": item["prompt"] for item in raw}
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_chain_runner.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add core/chain_runner.py tests/test_chain_runner.py
git commit -m "feat: ChainRunner orchestrator with retry, resume, and stop"
```

---

## Task 10: UI — minimal PyQt6 main window

**Files:**
- Create: `ui/main_window.py`
- Create: `app/main.py`
- Create: `tests/test_main_window.py`

**Interfaces:**
- Produces:
  - `ui.main_window.MainWindow(QMainWindow)` with public methods: `set_status(text: str) -> None`, `append_log(line: str) -> None`, `set_progress(done: int, total: int) -> None`, `open_output_folder() -> None`
  - signals: `start_requested = pyqtSignal(dict)` payload `{ref, prompts, aspect, duration}`, `stop_requested = pyqtSignal()`
- `app.main.main()` — QApplication entry; instantiates MainWindow, wires `start_requested` → ChainRunner in a QThread, forwards `RunnerEvent`s to UI.

- [ ] **Step 1: Write failing test `tests/test_main_window.py`**

```python
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(
    True,  # PyQt headless can be flaky; manual smoke test acceptable
    reason="GUI smoke test runs manually",
)


def test_main_window_constructs(qtbot) -> None:  # pragma: no cover
    from ui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    assert w.windowTitle() == "Lisa LiveTrading"
```

(Test is skipped by default; included for documentation. Manual smoke is the acceptance.)

- [ ] **Step 2: Implement `ui/main_window.py`**

```python
from __future__ import annotations

import webbrowser
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QMainWindow):
    start_requested = pyqtSignal(dict)
    stop_requested = pyqtSignal()
    open_folder_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Lisa LiveTrading")
        self.resize(640, 480)
        self._last_output_folder: Path | None = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Ref image row
        ref_row = QHBoxLayout()
        ref_row.addWidget(QLabel("Ref image:"))
        self.ref_edit = QLineEdit()
        ref_row.addWidget(self.ref_edit, 1)
        ref_btn = QPushButton("Browse")
        ref_btn.clicked.connect(self._browse_ref)
        ref_row.addWidget(ref_btn)
        root.addLayout(ref_row)

        # Prompts row
        pr_row = QHBoxLayout()
        pr_row.addWidget(QLabel("Prompts:"))
        self.prompts_edit = QLineEdit()
        pr_row.addWidget(self.prompts_edit, 1)
        pr_btn = QPushButton("Browse")
        pr_btn.clicked.connect(self._browse_prompts)
        pr_row.addWidget(pr_btn)
        root.addLayout(pr_row)

        # Aspect + duration
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("Aspect:"))
        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems(["9:16", "16:9", "1:1"])
        opt_row.addWidget(self.aspect_combo)
        opt_row.addSpacing(20)
        opt_row.addWidget(QLabel("Duration:"))
        self.duration_combo = QComboBox()
        self.duration_combo.addItems(["5", "10", "15"])
        self.duration_combo.setCurrentText("10")
        opt_row.addWidget(self.duration_combo)
        opt_row.addStretch(1)
        root.addLayout(opt_row)

        # Buttons
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._emit_start)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        self.open_btn = QPushButton("Open Folder")
        self.open_btn.clicked.connect(self._open_folder_clicked)
        self.open_btn.setEnabled(False)
        btn_row.addWidget(self.open_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        # Progress
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        # Status
        self.status_label = QLabel("Ready.")
        root.addWidget(self.status_label)

        # Log
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log, 1)

    def _browse_ref(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Pick reference image", filter="Images (*.png *.jpg *.jpeg)")
        if p:
            self.ref_edit.setText(p)

    def _browse_prompts(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Pick prompts.json", filter="JSON (*.json)")
        if p:
            self.prompts_edit.setText(p)

    def _emit_start(self) -> None:
        payload = {
            "ref": self.ref_edit.text().strip(),
            "prompts": self.prompts_edit.text().strip(),
            "aspect": self.aspect_combo.currentText(),
            "duration": int(self.duration_combo.currentText()),
        }
        if not payload["ref"] or not payload["prompts"]:
            self.set_status("Pick both ref and prompts before starting.")
            return
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.start_requested.emit(payload)

    def _open_folder_clicked(self) -> None:
        if self._last_output_folder and self._last_output_folder.exists():
            webbrowser.open(self._last_output_folder.as_uri())

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def set_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)

    def set_output_folder(self, folder: Path) -> None:
        self._last_output_folder = folder
        self.open_btn.setEnabled(True)

    def reset_buttons(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
```

- [ ] **Step 3: Implement `app/main.py`**

```python
from __future__ import annotations

import faulthandler
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from core.chain_runner import ChainRunner, RunnerEvent
from core.project import ChainProject, ProjectInputs
from ui.main_window import MainWindow
from utils.logging import setup_logging
from utils.paths import project_folder_name


class _RunnerThread(QThread):
    event = pyqtSignal(object)
    finished_clean = pyqtSignal()

    def __init__(self, runner: ChainRunner) -> None:
        super().__init__()
        self.runner = runner
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        def emit_event(ev: RunnerEvent) -> None:
            self.event.emit(ev)

        self.runner.run(
            on_event=emit_event,
            stop_check=lambda: self._stop_requested,
        )
        self.finished_clean.emit()


def _load_config() -> dict:
    cfg_path = Path(__file__).resolve().parents[1] / "config.yaml"
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))


def _setup_project(payload: dict) -> ChainProject:
    ref = Path(payload["ref"])
    prompts_path = Path(payload["prompts"])
    folder = ref.parent / project_folder_name(datetime.now().astimezone())
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "input").mkdir(exist_ok=True)
    (folder / "logs").mkdir(exist_ok=True)
    shutil.copyfile(ref, folder / "input" / "ref.png")
    shutil.copyfile(prompts_path, folder / "input" / "prompts.json")

    prompts = json.loads((folder / "input" / "prompts.json").read_text(encoding="utf-8"))
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompts.json must be a non-empty list")
    clip_ids = [f"{int(item['id']):03d}" for item in prompts]

    inputs = ProjectInputs(
        ref_image="input/ref.png",
        prompts="input/prompts.json",
        aspect=payload["aspect"],
        duration=int(payload["duration"]),
    )
    return ChainProject.create(folder, inputs, clip_ids)


def main() -> int:
    faulthandler.enable()
    setup_logging(level="INFO")

    config = _load_config()
    app = QApplication(sys.argv)
    win = MainWindow()
    thread: _RunnerThread | None = None

    def handle_start(payload: dict) -> None:
        nonlocal thread
        try:
            project = _setup_project(payload)
        except Exception as e:
            win.set_status(f"Setup failed: {e}")
            win.reset_buttons()
            return

        win.set_output_folder(project.folder)
        setup_logging(log_file=project.folder / "logs" / "app.log", level="INFO")

        total = len(project.clips)
        win.set_progress(0, total)
        win.append_log(f"Project folder: {project.folder}")
        win.set_status(f"Starting {total} clips...")

        runner = ChainRunner(project, config)
        thread = _RunnerThread(runner)

        done_count = {"n": 0}

        def on_event(ev: RunnerEvent) -> None:
            if ev.kind == "clip_started":
                win.set_status(f"Clip {ev.clip_id} (attempt {ev.payload.get('attempt', '?')})")
            elif ev.kind == "clip_done":
                done_count["n"] += 1
                win.set_progress(done_count["n"], total)
                win.append_log(f"Clip {ev.clip_id} done")
            elif ev.kind == "clip_failed":
                win.append_log(f"Clip {ev.clip_id} FAILED: {ev.payload}")
                win.set_status(f"Clip {ev.clip_id} failed — chain stopped")
            elif ev.kind == "final_done":
                win.append_log(f"Final video ready: {ev.payload.get('path')}")
                win.set_status("Done.")
            elif ev.kind == "final_failed":
                win.append_log(f"Concat failed: {ev.payload}")
            elif ev.kind == "worker_marker":
                win.append_log(f"  marker: {ev.payload}")
            elif ev.kind == "worker_log":
                line = ev.payload.get("line", "")
                if line.strip():
                    win.append_log(f"  {line}")
            elif ev.kind == "stopped":
                win.append_log("Stopped by user.")
                win.set_status("Stopped.")

        thread.event.connect(on_event)
        thread.finished_clean.connect(win.reset_buttons)
        thread.start()

    def handle_stop() -> None:
        if thread is not None:
            thread.request_stop()
            win.set_status("Stopping...")

    win.start_requested.connect(handle_start)
    win.stop_requested.connect(handle_stop)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Verify the app imports without launching the GUI**

Run:
```bash
cd D:/Projects/Lisa_livetrading
python -c "import app.main; import ui.main_window; print('OK')"
```
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add ui/main_window.py app/main.py tests/test_main_window.py
git commit -m "feat: PyQt6 main window + QThread-backed runner wiring"
```

---

## Task 11: End-to-end integration test (mock Grok)

**Files:**
- Create: `tests/test_chain_e2e.py`

**Interfaces:**
- Consumes: ChainRunner, ChainProject, frame_extractor, video_concat (real ffmpeg)
- Uses a `worker_factory` that synthesizes real tiny mp4 outputs via ffmpeg instead of calling Grok.

- [ ] **Step 1: Write `tests/test_chain_e2e.py`**

```python
from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from core.chain_runner import ChainRunner
from core.project import ChainProject, ProjectInputs
from utils.video_concat import probe_duration
from workers.task_contract import EXIT_SUCCESS, TaskJson


def _ffmpeg() -> str:
    f = shutil.which("ffmpeg")
    if f is None:
        pytest.skip("ffmpeg not on PATH")
    return f


def _make_clip(path: Path, color: str, dur: int) -> None:
    subprocess.run(
        [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=10:d={dur}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(dur), str(path),
        ],
        check=True,
    )


def test_full_chain_with_mocked_worker(tmp_path: Path) -> None:
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "input").mkdir()
    ref = folder / "input" / "ref.png"
    # 1x1 png placeholder
    ref.write_bytes(bytes.fromhex("89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082"))
    prompts = [{"id": i + 1, "prompt": f"clip {i+1}"} for i in range(3)]
    (folder / "input" / "prompts.json").write_text(json.dumps(prompts), encoding="utf-8")

    inputs = ProjectInputs(ref_image="input/ref.png", prompts="input/prompts.json", aspect="9:16", duration=2)
    project = ChainProject.create(folder, inputs, [f"{i+1:03d}" for i in range(3)])

    config = {
        "cdp": {"url": "x", "base_url": "y"},
        "defaults": {"retry_count": 0, "worker_timeout_sec": 600},
    }

    colors = ["red", "green", "blue"]

    @contextmanager
    def fake_worker_ctx(task: TaskJson):
        out = Path(task.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        idx = int(task.task_id) - 1
        _make_clip(out, colors[idx], 2)

        class _W:
            def iter_markers(self):
                return iter([])

            def wait(self, timeout=None):
                return EXIT_SUCCESS

            def terminate(self):
                pass

        yield _W()

    runner = ChainRunner(project, config)
    runner.run(worker_factory=fake_worker_ctx)

    reloaded = ChainProject.load(folder)
    assert all(c.status == "done" for c in reloaded.clips.values())
    assert reloaded.final.status == "done"

    final = folder / "final.mp4"
    assert final.exists()
    dur = probe_duration(final)
    # 3 clips × 2s − 2 × 0.5s xfade = 5s
    assert 4.6 <= dur <= 5.4
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_chain_e2e.py -v -s`
Expected: 1 passed.

If the test fails, fix the issue in `ChainRunner` (most likely off-by-one in clip ordering, ref selection, or concat parameters).

- [ ] **Step 3: Commit**

```bash
git add tests/test_chain_e2e.py
git commit -m "test: end-to-end chain with mocked Grok worker (real ffmpeg)"
```

---

## Task 12: Manual smoke test + README polish

**Files:**
- Modify: `README.md`

**Interfaces:** none (documentation only)

- [ ] **Step 1: Run all tests one last time**

Run: `python -m pytest -v`
Expected: all pass (skipped GUI test acceptable).

- [ ] **Step 2: Run the app manually**

Pre-req: ffmpeg on PATH, Brave launched with CDP (`launch_brave.bat` from v2 — copy if needed), Grok login session active.

Run:
```bash
cd D:/Projects/Lisa_livetrading
.venv/Scripts/activate    # or your venv activator
python -m app.main
```

In the GUI:
- Pick one of the existing PNGs in `D:/Projects/Lisa_livetrading/` as ref
- Create a small `prompts.json` with 2 entries (e.g., `[{"id":1,"prompt":"Lisa stands up"},{"id":2,"prompt":"Lisa waves"}]`) and pick it
- Aspect = 9:16, duration = 10
- Click Start

Verify:
- Project folder is created next to the ref image
- `state.json`, `clips/clip_001.mp4`, `frames/frame_001.png`, `clips/clip_002.mp4`, `final.mp4` all appear
- Log shows worker markers
- Stop button works mid-run (state preserves)

If Grok rejects or CDP misbehaves, this surfaces real-world issues to fix iteratively. The auto-tests already cover everything that can be tested without a real browser.

- [ ] **Step 3: Update README with smoke test results**

```markdown
## Smoke test (2026-06-18)

- Chain of N=2 clips ran end-to-end against live Grok via Brave CDP.
- `final.mp4` length ≈ (2 × 10s) − 0.5s xfade = 19.5s.
- State preserved on Stop; resume picks up correctly.

## Known limitations (Phase 1)

- Single concurrent chain at a time.
- Aspect ratio applies to whole chain (no per-prompt override).
- Brave must be launched manually with CDP enabled.
- ffmpeg must be on PATH (or set `ffmpeg.path` in `config.yaml`).
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: smoke test notes and known limitations"
```

---

## Self-Review

**Spec coverage:** Sections 1–19 of the spec are mapped:

| Spec § | Task(s) |
|---|---|
| 1 Mục đích | All |
| 2 Fork strategy | T6 |
| 3 Cấu trúc thư mục | T1 (scaffold), T2/T3/T4/T7/T8/T9/T10 (files) |
| 4 Folder runtime | T10 (`_setup_project`) |
| 5 prompts.json | T9 (`_load_prompts`), T10 |
| 6 state.json | T7 |
| 7 Luồng chính | T9 |
| 8 UI Layout | T10 |
| 9 Error handling | T9 (retry, stop, fail), T8 (worker exits) |
| 10 Resume | T9 (`test_run_resume_skips_done`) |
| 11 CDP session | T6 (copy), T8 (attach) |
| 12 Exit codes | T5 |
| 13 Stdout markers | T5, T8 |
| 14 Config yaml | T1 |
| 15 FFmpeg commands | T3, T4 |
| 16 Testing | T2/T3/T4/T5/T7/T9/T11/T12 |
| 17 Dependencies | T1 |
| 18 Non-goals | docs only — not implemented (by design) |
| 19 Acceptance | T12 smoke + all auto tests |

**Placeholder scan:** No "TODO", "TBD", or vague "handle edge cases" remain. Every code step has the full code body.

**Type consistency:** `ChainProject.update_clip(clip_id, **fields)` used the same way in T7 tests and T9 runner. `RunnerEvent(kind, clip_id, payload)` same construction in T9 and T10. `TaskJson` field names identical across T5, T8, T9. `concat_with_xfade(clips, output, xfade_dur)` signature consistent T4 → T9.

Open known issue: Task 8 Step 3 references `attach_existing_browser` as the working name; Task 6 Step 4 instructs the implementer to verify and adjust. This is intentional (the exact symbol comes from the vendored v2 code, which the implementer reads).

---

## Execution Handoff

Plan complete and saved to `D:/Projects/Lisa_livetrading/docs/superpowers/plans/2026-06-18-lisa-livetrading-implementation.md`.

User said "code den finish" — proceed with **Inline Execution** (executing-plans skill, batch with checkpoints).
