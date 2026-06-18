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
