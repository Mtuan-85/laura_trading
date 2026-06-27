from __future__ import annotations

import json
import asyncio
import subprocess
import sys
from pathlib import Path

import engines.grok.engine as grok_engine
import engines.grok.image_ref_engine as grok_image_ref_engine
from workers.task_contract import EXIT_PARSE_FAILED, EXIT_PREREQ_MISSING
from workers.task_contract import TaskJson
from workers import video_chain_worker


def _run_worker(task_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "workers.video_chain_worker", "--task", str(task_path)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


def test_missing_task_file_exits_parse_failed(tmp_path: Path) -> None:
    r = _run_worker(tmp_path / "no.json")
    assert r.returncode == EXIT_PARSE_FAILED


def test_invalid_task_json_exits_parse_failed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json}", encoding="utf-8")
    r = _run_worker(bad)
    assert r.returncode == EXIT_PARSE_FAILED


def test_missing_ref_image_exits_prereq_missing(tmp_path: Path) -> None:
    task = {
        "task_id": "clip_001",
        "kind": "video",
        "prompt": "hello",
        "ref_path": str(tmp_path / "missing.png"),
        "aspect": "9:16",
        "duration": 10,
        "output_path": str(tmp_path / "out.mp4"),
        "cdp_url": "http://127.0.0.1:9222",
        "cdp_base_url": "https://grok.com/imagine",
    }
    p = tmp_path / "task.json"
    p.write_text(json.dumps(task), encoding="utf-8")
    r = _run_worker(p)
    assert r.returncode == EXIT_PREREQ_MISSING


def test_task_contract_kind_defaults_to_video() -> None:
    task = TaskJson(
        task_id="001",
        ref_path="/tmp/r.png",
        output_path="/tmp/o.mp4",
        prompt="hi",
    )
    assert task.kind == "video"


def test_task_contract_supports_image_edit_kind() -> None:
    task = TaskJson(
        task_id="002",
        kind="image_edit",
        ref_path="/tmp/r.png",
        output_path="/tmp/refined.jpg",
        image_edit_prompt="fixed edit prompt",
    )
    assert task.kind == "image_edit"
    assert task.image_edit_prompt == "fixed edit prompt"


def test_worker_image_edit_task_calls_image_engine_only(tmp_path: Path, monkeypatch) -> None:
    raw = tmp_path / "raw.png"
    raw.write_bytes(b"RAW")
    refined = tmp_path / "refined.jpg"
    calls: list[tuple] = []
    markers: list[tuple[str, dict]] = []

    class FakeImageEngine:
        def __init__(self, page):
            self.page = page

        async def gen_image_with_refs(self, **kwargs):
            calls.append(("image", kwargs["prompt"], kwargs["ref_paths"]))
            kwargs["output_path"].write_bytes(b"REFINED")
            return {"ok": True, "path": str(kwargs["output_path"])}

    class FakeVideoEngine:
        def __init__(self, page):
            self.page = page
            self.last_warnings = []

        async def gen_video(self, *_a, **_kw):
            calls.append(("video_should_not_be_called",))
            return None

    async def fake_retry(**kwargs):
        result = await kwargs["gen_factory"]()
        return {"ok": True, "result": result, "attempts": 1}

    monkeypatch.setattr(grok_image_ref_engine, "GrokImageRefEngine", FakeImageEngine)
    monkeypatch.setattr(grok_engine, "GrokVideoEngine", FakeVideoEngine)
    monkeypatch.setattr(video_chain_worker, "run_with_retry", fake_retry)
    monkeypatch.setattr(video_chain_worker, "print_marker", lambda k, p: markers.append((k, p)))

    task = TaskJson(
        task_id="002",
        kind="image_edit",
        ref_path=str(raw),
        output_path=str(refined),
        image_edit_prompt="fixed edit",
        aspect="9:16",
    )
    conn = type("Conn", (), {"page": object()})()
    asyncio.run(video_chain_worker._process_task(conn, task, 1))

    assert calls == [("image", "fixed edit", [raw])]
    assert markers[-1][0] == "TASK DONE"
    assert markers[-1][1]["kind"] == "image_edit"


def test_worker_video_task_calls_video_engine_only(tmp_path: Path, monkeypatch) -> None:
    raw = tmp_path / "ref.jpg"
    raw.write_bytes(b"REF")
    output = tmp_path / "clip.mp4"
    calls: list[tuple] = []
    markers: list[tuple[str, dict]] = []

    class FakeImageEngine:
        def __init__(self, page):
            self.page = page

        async def gen_image_with_refs(self, **_kw):
            calls.append(("image_should_not_be_called",))
            return {"ok": True}

    class FakeVideoEngine:
        def __init__(self, page):
            self.page = page
            self.last_warnings = []

        async def gen_video(self, prompt, ref_image, settings):
            calls.append(("video", prompt, ref_image))
            output.write_bytes(b"VIDEO")
            return output

    async def fake_retry(**kwargs):
        result = await kwargs["gen_factory"]()
        return {"ok": True, "result": result, "attempts": 1}

    monkeypatch.setattr(grok_image_ref_engine, "GrokImageRefEngine", FakeImageEngine)
    monkeypatch.setattr(grok_engine, "GrokVideoEngine", FakeVideoEngine)
    monkeypatch.setattr(video_chain_worker, "run_with_retry", fake_retry)
    monkeypatch.setattr(video_chain_worker, "print_marker", lambda k, p: markers.append((k, p)))

    task = TaskJson(
        task_id="002",
        kind="video",
        ref_path=str(raw),
        output_path=str(output),
        prompt="video prompt",
        aspect="9:16",
        duration=10,
    )
    conn = type("Conn", (), {"page": object()})()
    asyncio.run(video_chain_worker._process_task(conn, task, 1))

    assert calls == [("video", "video prompt", raw)]
    assert markers[-1][0] == "TASK DONE"
    assert markers[-1][1]["kind"] == "video"
