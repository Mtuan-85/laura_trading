from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.main import _project_cache_key, _resolve_project_from_selection, _setup_project
from core.project import ChainProject, ProjectInputs
from utils.paths import next_project_folder


def _write_prompts(path: Path, items: list[dict]) -> Path:
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


def _make_existing_project(tmp_path: Path) -> ChainProject:
    folder = tmp_path / "project_20260622_1200"
    (folder / "input").mkdir(parents=True)
    (folder / "input" / "ref.png").write_bytes(b"PNG")
    _write_prompts(folder / "input" / "prompts.json", [{"id": 1, "prompt": "a"}])
    return ChainProject.create(
        folder,
        ProjectInputs(
            ref_image="input/ref.png",
            prompts="input/prompts.json",
            aspect="9:16",
            duration=10,
        ),
        ["001"],
    )


def test_resolve_project_from_state_json(tmp_path: Path) -> None:
    project = _make_existing_project(tmp_path)
    assert _resolve_project_from_selection(project.folder / "state.json") == project.folder


def test_resolve_project_from_copied_prompts(tmp_path: Path) -> None:
    project = _make_existing_project(tmp_path)
    selected = project.folder / "input" / "prompts.json"
    assert _resolve_project_from_selection(selected) == project.folder


def test_setup_project_loads_existing_state_without_ref(tmp_path: Path) -> None:
    project = _make_existing_project(tmp_path)
    loaded = _setup_project(
        {"ref": "", "prompts": str(project.folder / "state.json"), "aspect": "9:16", "duration": 10},
        {},
    )
    assert loaded.folder == project.folder


def test_setup_project_rejects_duplicate_prompt_ids(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"PNG")
    prompts = _write_prompts(
        tmp_path / "prompts.json",
        [{"id": 1, "prompt": "a"}, {"id": "1", "prompt": "b"}],
    )
    with pytest.raises(ValueError, match="duplicate prompt id"):
        _setup_project(
            {"ref": str(ref), "prompts": str(prompts), "aspect": "9:16", "duration": 10},
            {},
        )
    assert not list(tmp_path.glob("project_*"))


def test_setup_video_uses_configured_ffmpeg(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"VIDEO")
    prompts = _write_prompts(tmp_path / "prompts.json", [{"id": 1, "prompt": "a"}])
    calls: list[str] = []

    def fake_extract(video, output, *, offset_sec, ffmpeg):
        calls.append(ffmpeg)
        output.write_bytes(b"PNG")

    monkeypatch.setattr("app.main.extract_last_frame", fake_extract)
    _setup_project(
        {"ref": str(source), "prompts": str(prompts), "aspect": "9:16", "duration": 10},
        {"ffmpeg": {"path": "custom-ffmpeg"}},
    )
    assert calls == ["custom-ffmpeg"]


def test_setup_project_copies_image_edit_prompt(tmp_path: Path, monkeypatch) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"PNG")
    prompts = _write_prompts(tmp_path / "prompts.json", [{"id": 1, "prompt": "a"}])
    source_edit = tmp_path / "source_image_edit.json"
    source_edit.write_text(
        json.dumps({"prompt": "fixed edit", "skin_tone_profile": {"ignored": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.main.IMAGE_EDIT_SOURCE", source_edit)

    project = _setup_project(
        {"ref": str(ref), "prompts": str(prompts), "aspect": "9:16", "duration": 10},
        {},
    )

    copied = json.loads((project.folder / "input" / "image_edit.json").read_text("utf-8"))
    assert copied["prompt"] == "fixed edit"
    assert project.inputs.image_edit == "input/image_edit.json"


def test_setup_project_slices_prompts_by_start_and_count(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"PNG")
    prompts = _write_prompts(
        tmp_path / "prompts.json",
        [{"id": i, "prompt": f"prompt {i}"} for i in range(1, 51)],
    )

    project = _setup_project(
        {
            "ref": str(ref),
            "prompts": str(prompts),
            "aspect": "9:16",
            "duration": 10,
            "prompt_start": 21,
            "prompt_count": 10,
        },
        {},
    )

    copied = json.loads((project.folder / "input" / "prompts.json").read_text("utf-8"))
    assert [item["id"] for item in copied] == list(range(21, 31))
    assert list(project.clips.keys()) == [f"{i:03d}" for i in range(21, 31)]


def test_project_cache_key_includes_ref_and_prompt_range(tmp_path: Path) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"PNG")
    prompts = _write_prompts(tmp_path / "prompts.json", [{"id": 1, "prompt": "a"}])

    base_payload = {
        "ref": str(ref),
        "prompts": str(prompts),
        "aspect": "9:16",
        "duration": 10,
        "prompt_start": 1,
        "prompt_count": 10,
    }

    assert _project_cache_key(base_payload) != _project_cache_key(
        {**base_payload, "prompt_start": 11}
    )
    assert _project_cache_key(base_payload) != _project_cache_key(
        {**base_payload, "prompt_count": 0}
    )
    assert _project_cache_key(base_payload) != _project_cache_key(
        {**base_payload, "ref": str(tmp_path / "other.png")}
    )


def test_next_project_folder_uses_minute_and_collision_suffix(tmp_path: Path) -> None:
    now = datetime(2026, 6, 22, 12, 34, 56, tzinfo=timezone.utc)
    first = next_project_folder(tmp_path, now)
    assert first.name == "project_20260622_1234"
    first.mkdir()
    assert next_project_folder(tmp_path, now).name == "project_20260622_1234_02"
