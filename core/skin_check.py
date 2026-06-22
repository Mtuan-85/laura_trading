"""Skin-similarity check between original ref and Grok-refined image.

Stitches the two images side-by-side with ffmpeg, then asks Claude CLI (via
subscription, same pattern as engines/grok/claude_picker.py) to compare skin
tone. Returns a structured verdict so the caller can auto-pass on high
similarity or prompt the user on borderline / low matches.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger as log

CLAUDE_CMD = "claude"
DEFAULT_THRESHOLD = 95
DEFAULT_TIMEOUT_S = 120
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@dataclass(frozen=True)
class SkinResult:
    skin: int           # 0..100 skin-tone similarity
    lighting: int       # 0..100 lighting similarity (informational)
    verdict: str        # "pass" | "ask"
    rationale: str
    raw_stdout: str = ""


def stitch_side_by_side(
    ref: Path,
    refined: Path,
    out: Path,
    *,
    ffmpeg: str = "ffmpeg",
    height: int = 768,
) -> None:
    """Combine ref + refined into one image via `ffmpeg hstack`.

    Both inputs are scaled to a common height first so hstack accepts them.
    Output format is inferred from `out` suffix (jpg recommended).
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    filter_str = (
        f"[0:v]scale=-1:{height}[a];"
        f"[1:v]scale=-1:{height}[b];"
        f"[a][b]hstack=inputs=2"
    )
    cmd = [
        ffmpeg, "-y",
        "-i", str(ref),
        "-i", str(refined),
        "-filter_complex", filter_str,
        "-q:v", "3",
        str(out),
    ]
    log.info(f"[skin] stitch: {ref.name} + {refined.name} -> {out.name}")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(
            f"ffmpeg hstack failed (rc={r.returncode}): {r.stderr[-400:] if r.stderr else ''}"
        )


def _build_instruction(stitched: Path, threshold: int) -> str:
    return f"""So sánh hai bức ảnh trong file dưới đây. File là 1 ảnh ghép side-by-side: BÊN TRÁI là ảnh REF gốc, BÊN PHẢI là ảnh đã được Grok chỉnh sửa.

PATH: {stitched.absolute()}

Đọc file bằng Read tool. Đánh giá:
1. SKIN TONE: màu da nhân vật (mặt, cổ, tay) hai bên giống nhau bao nhiêu phần trăm? Cảnh báo điển hình: Grok đôi khi đổi sai sang da đen hoặc tối hơn nhiều.
2. LIGHTING: ánh sáng căn phòng / hướng sáng có khớp không (thông tin tham khảo, không phải tiêu chí pass/fail).

Output STRICT JSON, không markdown, không text thừa:
{{"skin_similarity": <int 0..100>, "lighting_similarity": <int 0..100>, "verdict": "pass"|"ask", "rationale": "<1-2 câu tiếng Việt>"}}

QUY TẮC verdict:
- skin_similarity >= {threshold} → "pass"
- skin_similarity <  {threshold} → "ask"
"""


def _parse_result(stdout: str, threshold: int) -> SkinResult:
    text = (stdout or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

    data: dict | None = None
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None

    if not isinstance(data, dict):
        return SkinResult(
            skin=0, lighting=0, verdict="ask",
            rationale=f"parse failed; raw={text[:120]!r}",
            raw_stdout=stdout,
        )

    skin = _clamp_int(data.get("skin_similarity"), 0, 100, fallback=0)
    lighting = _clamp_int(data.get("lighting_similarity"), 0, 100, fallback=0)
    rationale = str(data.get("rationale", "")).strip() or "(no rationale)"
    # Trust verdict from Claude only if it matches the rule; recompute otherwise.
    declared = str(data.get("verdict", "")).lower()
    verdict = "pass" if skin >= threshold else "ask"
    if declared and declared != verdict:
        log.warning(
            f"[skin] Claude verdict={declared!r} disagrees with rule (skin={skin}, "
            f"threshold={threshold}); using rule={verdict!r}"
        )
    return SkinResult(
        skin=skin, lighting=lighting, verdict=verdict,
        rationale=rationale, raw_stdout=stdout,
    )


def _clamp_int(v, lo: int, hi: int, *, fallback: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, n))


def _run_claude_cli_blocking(instruction: str, timeout_s: int) -> tuple[int, str]:
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    for key in PROXY_ENV_KEYS:
        env.pop(key, None)
    try:
        r = subprocess.run(
            [CLAUDE_CMD, "--print", "--dangerously-skip-permissions"],
            input=instruction,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_s,
            env=env,
        )
        return r.returncode, r.stdout or ""
    except FileNotFoundError:
        log.warning("[skin] Claude CLI không tồn tại trong PATH")
        return -1, ""
    except subprocess.TimeoutExpired:
        log.warning(f"[skin] Claude CLI timeout sau {timeout_s}s")
        return -2, ""


def check_skin(
    stitched: Path,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> SkinResult:
    """Ask Claude CLI to evaluate skin tone similarity from a stitched image.

    Always returns a SkinResult — never raises. Any failure (CLI missing,
    timeout, parse error) yields skin=0/verdict=ask so the user is prompted.
    """
    if not stitched.exists() or stitched.stat().st_size == 0:
        return SkinResult(
            skin=0, lighting=0, verdict="ask",
            rationale=f"stitched image missing: {stitched}",
        )
    instruction = _build_instruction(stitched, threshold)
    rc, out = _run_claude_cli_blocking(instruction, timeout_s)
    if rc != 0:
        reason = {
            -1: "Claude CLI not installed",
            -2: f"Claude CLI timeout ({timeout_s}s)",
        }.get(rc, f"Claude CLI rc={rc}")
        return SkinResult(
            skin=0, lighting=0, verdict="ask",
            rationale=reason, raw_stdout=out,
        )
    return _parse_result(out, threshold)
