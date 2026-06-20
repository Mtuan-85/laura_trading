from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from core.chain_runner import ChainRunner
from core.project import ChainProject, ProjectInputs
from workers.task_contract import TaskJson


def _prep(tmp_path: Path, prompts: list[str]) -> ChainProject:
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "input").mkdir()
    ref = folder / "input" / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n")
    p_path = folder / "input" / "prompts.json"
    p_path.write_text(json.dumps([{"id": i + 1, "prompt": pr} for i, pr in enumerate(prompts)]), "utf-8")
    inputs = ProjectInputs(ref_image="input/ref.png", prompts="input/prompts.json", aspect="9:16", duration=10)
    return ChainProject.create(folder, inputs, [f"{i+1:03d}" for i in range(len(prompts))])


class _FakeWorker:
    """Long-lived worker stub: records every send_task call, returns canned outcomes."""

    def __init__(
        self,
        outcomes: dict[str, dict] | None = None,
        default_ok: bool = True,
        on_send: callable | None = None,
    ) -> None:
        self.outcomes = outcomes or {}
        self.default_ok = default_ok
        self.calls: list[str] = []
        self._on_send = on_send

    def send_task(self, task: TaskJson, *, on_marker=lambda m: None, stop_check=lambda: False):
        self.calls.append(task.task_id)
        if self._on_send is not None:
            self._on_send(task)
        outcome = self.outcomes.get(task.task_id)
        if outcome is None:
            if self.default_ok:
                outcome = {"ok": True, "attempts": 1}
            else:
                outcome = {"ok": False, "reason": "flow_failed", "attempts": 3}
        if outcome.get("ok"):
            Path(task.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(task.output_path).write_bytes(b"\x00\x00\x00\x20ftypmp42")
        return outcome

    def terminate(self) -> None:
        pass


def _factory(worker: _FakeWorker):
    @contextmanager
    def make():
        yield worker
    return make


def test_run_success_creates_all_clips(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])

    extract_calls = []
    def fake_extract(video: Path, frame: Path) -> None:
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"FRAMEPNG")
        extract_calls.append((video.name, frame.name))

    concat_calls = []
    def fake_concat(clips: list[Path], out: Path) -> None:
        out.write_bytes(b"FINAL")
        concat_calls.append([c.name for c in clips])

    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    worker = _FakeWorker(default_ok=True)
    runner = ChainRunner(project, config)
    runner.run(
        worker_factory=_factory(worker),
        frame_extractor=fake_extract,
        concat=fake_concat,
    )

    reloaded = ChainProject.load(project.folder)
    assert all(c.status == "done" for c in reloaded.clips.values())
    assert reloaded.final.status == "done"
    assert reloaded.final.path == "final.mp4"
    assert len(extract_calls) == 3
    assert len(concat_calls) == 1
    assert worker.calls == ["001", "002", "003"]


def test_run_retries_then_stops_on_persistent_failure(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b"])

    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    worker = _FakeWorker(default_ok=False)
    runner = ChainRunner(project, config)
    runner.run(
        worker_factory=_factory(worker),
        frame_extractor=lambda v, f: None,
        concat=lambda cs, o: None,
    )

    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["001"].status == "failed"
    assert reloaded.clips["001"].attempts == 3
    assert reloaded.clips["002"].status == "pending"
    assert reloaded.final.status == "pending"
    # Worker only invoked once per clip — retries happen inside the worker now.
    assert worker.calls == ["001"]


def test_run_resume_skips_done(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])
    project.update_clip("001", status="done", clip="clips/clip_001.mp4", frame="frames/frame_001.png")
    (project.folder / "clips").mkdir(exist_ok=True)
    (project.folder / "clips" / "clip_001.mp4").write_bytes(b"ALREADY")
    (project.folder / "frames").mkdir(exist_ok=True)
    (project.folder / "frames" / "frame_001.png").write_bytes(b"ALREADY")

    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    worker = _FakeWorker(default_ok=True)
    runner = ChainRunner(project, config)
    runner.run(
        worker_factory=_factory(worker),
        frame_extractor=lambda v, f: f.write_bytes(b"P"),
        concat=lambda cs, o: o.write_bytes(b"F"),
    )

    assert worker.calls == ["002", "003"]


def test_run_stop_check_interrupts(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])
    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    worker = _FakeWorker(default_ok=True)
    runner = ChainRunner(project, config)

    # Returns True only AFTER the first clip succeeds: clip 001 → done,
    # clip 002 short-circuits at the outer stop_check before send_task.
    counter = {"n": 0}
    def stopper() -> bool:
        counter["n"] += 1
        return counter["n"] > 1

    runner.run(
        worker_factory=_factory(worker),
        frame_extractor=lambda v, f: f.write_bytes(b"P"),
        concat=lambda cs, o: o.write_bytes(b"F"),
        stop_check=stopper,
    )

    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["001"].status == "done"
    assert reloaded.final.status == "pending"
