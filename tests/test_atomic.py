from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.atomic import atomic_write_json, read_json


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"a": 1})
    assert target.exists()
    assert json.loads(target.read_text("utf-8")) == {"a": 1}


def test_atomic_write_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"a": 1})
    atomic_write_json(target, {"a": 2})
    assert read_json(target) == {"a": 2}


def test_atomic_write_keeps_rotating_backups(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    for i in range(8):
        atomic_write_json(target, {"i": i}, backup_keep=3)
    backups = sorted(tmp_path.glob("state.json.bak.*"))
    assert len(backups) == 3


def test_atomic_write_does_not_partially_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"ok": True})
    import os
    original = os.replace

    def fail_replace(*args, **kwargs):
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        atomic_write_json(target, {"corrupted": True})
    monkeypatch.setattr(os, "replace", original)
    assert read_json(target) == {"ok": True}
