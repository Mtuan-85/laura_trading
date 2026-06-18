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


def test_concat_three_clips_with_xfade(tmp_path: Path) -> None:
    clips = [
        _make_color_clip(tmp_path / "a.mp4", color="red", duration=2),
        _make_color_clip(tmp_path / "b.mp4", color="green", duration=2),
        _make_color_clip(tmp_path / "c.mp4", color="blue", duration=2),
    ]
    out = tmp_path / "final.mp4"
    concat_with_xfade(clips, out, xfade_dur=0.5)
    assert out.exists() and out.stat().st_size > 0
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
