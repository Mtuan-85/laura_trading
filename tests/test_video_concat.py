from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from utils.video_concat import align_and_concat, concat_with_xfade, probe_duration


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


def _make_color_clip_with_audio(out: Path, *, color: str, frequency: int) -> Path:
    ff = _ffmpeg()
    cmd = [
        ff, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=10:d=2",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def _has_audio(path: Path) -> bool:
    probe = shutil.which("ffprobe")
    if probe is None:
        pytest.skip("ffprobe not on PATH")
    result = subprocess.run(
        [
            probe, "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return bool(result.stdout.strip())


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


def test_concat_preserves_audio_stream(tmp_path: Path) -> None:
    clips = [
        _make_color_clip_with_audio(tmp_path / "a.mp4", color="red", frequency=440),
        _make_color_clip_with_audio(tmp_path / "b.mp4", color="blue", frequency=660),
    ]
    out = tmp_path / "final.mp4"
    concat_with_xfade(clips, out, xfade_dur=0.5)
    assert _has_audio(out)


def _make_skin_ref(out: Path) -> Path:
    ff = _ffmpeg()
    subprocess.run(
        [
            ff, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0xA87C6D:s=128x128:d=1",
            "-frames:v", "1", str(out),
        ],
        check=True,
    )
    return out


def _make_skin_clip_with_audio(out: Path, *, frequency: int, duration: int = 2) -> Path:
    ff = _ffmpeg()
    subprocess.run(
        [
            ff, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=0xA87C6D:s=128x128:r=10:d={duration}",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(out),
        ],
        check=True,
    )
    return out


def test_align_and_concat_keeps_audio_and_correct_duration(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    ref = _make_skin_ref(tmp_path / "ref.png")
    clips = [
        _make_skin_clip_with_audio(tmp_path / "c1.mp4", frequency=440),
        _make_skin_clip_with_audio(tmp_path / "c2.mp4", frequency=660),
    ]
    out = tmp_path / "final.mp4"
    align_and_concat(clips, ref, out, xfade_dur=0.5)
    assert out.exists() and out.stat().st_size > 0
    assert _has_audio(out)
    dur = probe_duration(out)
    assert 3.0 <= dur <= 4.0


def test_align_and_concat_works_without_audio(tmp_path: Path) -> None:
    pytest.importorskip("cv2")
    ref = _make_skin_ref(tmp_path / "ref.png")
    clips = [
        _make_color_clip(tmp_path / "a.mp4", color="0xA87C6D", duration=2),
        _make_color_clip(tmp_path / "b.mp4", color="0xA87C6D", duration=2),
    ]
    out = tmp_path / "final.mp4"
    align_and_concat(clips, ref, out, xfade_dur=0.5)
    assert out.exists() and out.stat().st_size > 0
