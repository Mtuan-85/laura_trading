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
