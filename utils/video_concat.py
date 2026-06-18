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
