"""Vision-based image picker via Claude Code CLI subprocess.

Per SPEC §6: receives candidate paths + prompt + topic + style, returns the
best index. Uses the user's Pro/Max subscription quota (NOT the API key).

Requirements:
    - `claude` CLI installed and logged in (Pro/Max subscription)
    - ANTHROPIC_API_KEY env var MUST be unset/cleared (otherwise CLI bills API)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

from loguru import logger as log

CLAUDE_CMD = "claude"
DEFAULT_TIMEOUT = 180
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def _build_instruction(
    candidates: list[Path],
    prompt: str,
    topic: str,
    style: str,
) -> str:
    paths = "\n".join(f"  #{i}: {p.absolute()}" for i, p in enumerate(candidates))
    n = len(candidates) - 1
    return f"""Chọn ảnh tốt nhất trong các candidates để dùng cho video kể chuyện ngắn.

PROJECT TOPIC: "{topic}"
PROJECT STYLE: "{style}"
SCENE PROMPT: "{prompt}"

CANDIDATES (đánh số từ 0 tới {n}):
{paths}

Đọc từng ảnh bằng Read tool. Tiêu chí (priority order):
1. Style phải khớp project style ({style}) — quan trọng nhất, giữ nhất quán cả series
2. Khớp scene prompt
3. Composition đẹp, không lỗi (tay thừa, mặt méo, text hỏng)

Output STRICT JSON only, không markdown:
{{"choice": <int 0..{n}>, "rationale": "<ngắn gọn 1 câu>"}}
"""


def _parse_choice(stdout: str, max_idx: int) -> int | None:
    """Parse JSON or fallback to first digit; clamp to [0, max_idx]."""
    text = (stdout or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

    try:
        data = json.loads(text)
        choice = int(data.get("choice"))
        if 0 <= choice <= max_idx:
            return choice
    except Exception:
        pass

    m = re.search(r'"choice"\s*:\s*(\d+)', text)
    if m:
        c = int(m.group(1))
        if 0 <= c <= max_idx:
            return c

    for ch in text:
        if ch.isdigit():
            c = int(ch)
            if 0 <= c <= max_idx:
                return c

    return None


def _run_claude_cli_blocking(
    instruction: str,
    timeout: int,
) -> tuple[int, str]:
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)  # force subscription path
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
            timeout=timeout,
            env=env,
        )
        return r.returncode, r.stdout or ""
    except FileNotFoundError:
        log.warning("Claude CLI không tồn tại trong PATH")
        return -1, ""
    except subprocess.TimeoutExpired:
        log.warning(f"Claude CLI timeout sau {timeout}s")
        return -2, ""


async def pick_best(
    candidates: list[Path],
    prompt: str,
    topic: str,
    style: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> int:
    """Choose the best candidate index. Always returns a valid index — falls
    back to 0 on any failure so the pipeline never crashes.
    """
    if not candidates:
        return 0
    if len(candidates) == 1:
        return 0

    max_idx = len(candidates) - 1
    instruction = _build_instruction(candidates, prompt, topic, style)

    log.info(f"Claude pick: {len(candidates)} candidates cho prompt '{prompt[:50]}...'")
    rc, out = await asyncio.to_thread(_run_claude_cli_blocking, instruction, timeout)

    if rc != 0:
        log.warning(f"Claude CLI rc={rc}, fallback choice=0")
        return 0

    choice = _parse_choice(out, max_idx)
    if choice is None:
        log.warning(f"Claude output không parse được, fallback 0. Raw: {out[:200]!r}")
        return 0

    log.info(f"Claude pick: #{choice}")
    return choice
