from __future__ import annotations

from datetime import datetime
from pathlib import Path


def project_folder_name(now: datetime | None = None) -> str:
    when = now or datetime.now().astimezone()
    return f"project_{when.strftime('%Y%m%d_%H%M%S')}"


def ensure_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path
