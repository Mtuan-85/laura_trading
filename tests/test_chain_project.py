from __future__ import annotations

from pathlib import Path

import pytest

from core.project import ChainProject, ProjectInputs


def _inputs() -> ProjectInputs:
    return ProjectInputs(
        ref_image="input/ref.png",
        prompts="input/prompts.json",
        aspect="9:16",
        duration=10,
    )


def test_create_initializes_pending_clips(tmp_path: Path) -> None:
    p = ChainProject.create(tmp_path, _inputs(), ["001", "002", "003"])
    assert (tmp_path / "state.json").exists()
    assert set(p.clips.keys()) == {"001", "002", "003"}
    assert all(c.status == "pending" for c in p.clips.values())
    assert p.final.status == "pending"


def test_load_roundtrip(tmp_path: Path) -> None:
    ChainProject.create(tmp_path, _inputs(), ["001"])
    p = ChainProject.load(tmp_path)
    assert "001" in p.clips


def test_update_clip_persists(tmp_path: Path) -> None:
    p = ChainProject.create(tmp_path, _inputs(), ["001", "002"])
    p.update_clip("001", status="done", attempts=1, clip="clips/clip_001.mp4")
    reloaded = ChainProject.load(tmp_path)
    assert reloaded.clips["001"].status == "done"
    assert reloaded.clips["001"].attempts == 1
    assert reloaded.clips["001"].clip == "clips/clip_001.mp4"


def test_pending_clip_ids_skips_done(tmp_path: Path) -> None:
    p = ChainProject.create(tmp_path, _inputs(), ["001", "002", "003"])
    p.update_clip("001", status="done")
    p.update_clip("002", status="failed")
    assert p.pending_clip_ids() == ["002", "003"]


def test_update_final(tmp_path: Path) -> None:
    p = ChainProject.create(tmp_path, _inputs(), ["001"])
    p.update_final("done", path="final.mp4")
    reloaded = ChainProject.load(tmp_path)
    assert reloaded.final.status == "done"
    assert reloaded.final.path == "final.mp4"


def test_load_missing_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ChainProject.load(tmp_path / "no_such_folder")
