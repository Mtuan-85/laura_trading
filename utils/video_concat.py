from __future__ import annotations

import shutil
import subprocess
import tempfile
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


def probe_has_audio(video: Path, *, ffprobe: str = "ffprobe") -> bool:
    cmd = [
        ffprobe, "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(video),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return bool(result.stdout.strip())


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
    audio_present = [probe_has_audio(c, ffprobe=ffprobe) for c in clips]

    filter_parts: list[str] = []
    for i, (duration, has_audio) in enumerate(zip(durations, audio_present)):
        filter_parts.append(f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[vsrc{i}]")
        if has_audio:
            filter_parts.append(
                f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[asrc{i}]"
            )
        else:
            filter_parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.6f},"
                f"asetpts=PTS-STARTPTS[asrc{i}]"
            )

    prev_label = "vsrc0"
    prev_audio = "asrc0"
    cumulative = durations[0]
    for i in range(1, len(clips)):
        next_label = f"v{i}"
        next_audio = f"a{i}"
        offset = cumulative - xfade_dur
        filter_parts.append(
            f"[{prev_label}][vsrc{i}]xfade=transition=fade:"
            f"duration={xfade_dur}:offset={offset:.3f}[{next_label}]"
        )
        filter_parts.append(
            f"[{prev_audio}][asrc{i}]acrossfade=d={xfade_dur}:c1=tri:c2=tri[{next_audio}]"
        )
        cumulative = cumulative + durations[i] - xfade_dur
        prev_label = next_label
        prev_audio = next_audio

    filter_complex = ";".join(filter_parts)

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", str(c)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", f"[{prev_label}]",
        "-map", f"[{prev_audio}]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed (rc={result.returncode}): {result.stderr.strip()}")


def _align_clip(
    src: Path,
    profile: dict,
    dst: Path,
    *,
    ffmpeg: str,
    ffprobe: str,
    match_lightness: bool,
    deplastic: bool,
) -> None:
    from utils.skin_profile import apply_video

    tmp_video = dst.with_name(dst.stem + ".novideo.mp4")
    apply_video(
        str(src), profile, str(tmp_video),
        match_lightness=match_lightness, deplastic=deplastic,
    )
    if probe_has_audio(src, ffprobe=ffprobe):
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(tmp_video), "-i", str(src),
            "-map", "0:v", "-map", "1:a", "-c", "copy", str(dst),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"audio mux failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        tmp_video.unlink(missing_ok=True)
    else:
        tmp_video.replace(dst)


def align_and_concat(
    clips: list[Path],
    ref: Path,
    output: Path,
    *,
    xfade_dur: float = 0.5,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    match_lightness: bool = True,
    deplastic: bool = True,
) -> None:
    if not clips:
        raise ValueError("clips must contain at least one path")
    ref = Path(ref)
    if not ref.exists():
        raise FileNotFoundError(f"Reference image not found: {ref}")

    from utils.skin_profile import extract_profile

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    profile = extract_profile(str(ref))

    workdir = Path(tempfile.mkdtemp(prefix="skin_align_", dir=str(output.parent)))
    try:
        aligned: list[Path] = []
        for i, clip in enumerate(clips):
            dst = workdir / f"a{i:03d}.mp4"
            _align_clip(
                Path(clip), profile, dst,
                ffmpeg=ffmpeg, ffprobe=ffprobe,
                match_lightness=match_lightness, deplastic=deplastic,
            )
            aligned.append(dst)
        concat_with_xfade(
            aligned, output,
            xfade_dur=xfade_dur, ffmpeg=ffmpeg, ffprobe=ffprobe,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
