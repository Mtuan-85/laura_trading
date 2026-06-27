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
    kind: Literal["image_edit", "video"] = "video"
    ref_path: str
    output_path: str
    cdp_url: str = "http://127.0.0.1:9222"
    cdp_base_url: str = "https://grok.com/imagine"
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    # image_edit only
    image_edit_prompt: str | None = None
    # video only
    prompt: str = ""
    prompt_typed_prefix: str | None = None
    prompt_paste_suffix: str | None = None
    duration: int = 10


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
