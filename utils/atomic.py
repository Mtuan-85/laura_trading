from __future__ import annotations

import json
import os
from pathlib import Path


def atomic_write_json(path: Path, data: dict, *, backup_keep: int = 5) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if path.exists() and backup_keep > 0:
        _rotate_backup(path, backup_keep)
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rotate_backup(path: Path, keep: int) -> None:
    existing = sorted(path.parent.glob(f"{path.name}.bak.*"))
    while len(existing) >= keep:
        existing[0].unlink()
        existing = existing[1:]
    max_idx = 0
    for b in existing:
        try:
            n = int(b.suffix.lstrip("."))
            max_idx = max(max_idx, n)
        except ValueError:
            continue
    next_idx = max_idx + 1
    backup = path.with_name(f"{path.name}.bak.{next_idx:03d}")
    backup.write_bytes(path.read_bytes())
