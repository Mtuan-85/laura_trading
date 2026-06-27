from __future__ import annotations

from datetime import datetime
from pathlib import Path


def project_folder_name(now: datetime | None = None) -> str:
    when = now or datetime.now().astimezone()
    return f"project_{when.strftime('%Y%m%d_%H%M')}"


def next_project_folder(parent: Path, now: datetime | None = None) -> Path:
    parent = Path(parent)
    base_name = project_folder_name(now)
    candidate = parent / base_name
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = parent / f"{base_name}_{index:02d}"
        if not candidate.exists():
            return candidate
        index += 1


def ensure_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path
