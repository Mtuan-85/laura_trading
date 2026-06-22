from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from core.chain_runner import ChainRunner
from core.project import ChainProject, ProjectInputs
from core.skin_check import SkinResult
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


def _with_image_edit(project: ChainProject, prompt: str = "fixed image edit") -> None:
    edit_path = project.folder / "input" / "image_edit.json"
    edit_path.write_text(json.dumps({"prompt": prompt}), encoding="utf-8")
    project.inputs.image_edit = "input/image_edit.json"
    project.save()


class _FakeWorker:
    """Worker stub that handles split image_edit / video tasks."""

    def __init__(
        self,
        *,
        outcomes: dict[tuple[str, str], dict] | None = None,
        default_ok: bool = True,
        on_send: callable | None = None,
        markers: dict[tuple[str, str], list[tuple[str, dict]]] | None = None,
    ) -> None:
        # outcomes / markers keyed by (task_id, kind)
        self.outcomes = outcomes or {}
        self.default_ok = default_ok
        self.calls: list[tuple[str, str]] = []  # (task_id, kind)
        self.tasks: list[TaskJson] = []
        self._on_send = on_send
        self._markers = markers or {}

    def send_task(self, task: TaskJson, *, on_marker=lambda m: None, stop_check=lambda: False):
        self.calls.append((task.task_id, task.kind))
        self.tasks.append(task)
        if self._on_send is not None:
            self._on_send(task)
        for m in self._markers.get((task.task_id, task.kind), []):
            on_marker(m)
        outcome = self.outcomes.get((task.task_id, task.kind))
        if outcome is None:
            outcome = (
                {"ok": True, "attempts": 1}
                if self.default_ok
                else {"ok": False, "reason": "flow_failed", "attempts": 3}
            )
        if outcome.get("ok"):
            out_path = Path(task.output_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if task.kind == "video":
                out_path.write_bytes(b"\x00\x00\x00\x20ftypmp42")
            else:
                out_path.write_bytes(b"REFINED")
        return outcome

    def terminate(self) -> None:
        pass


def _factory(worker: _FakeWorker):
    @contextmanager
    def make():
        yield worker
    return make


def _ok_skin(*_a, **_kw) -> SkinResult:
    return SkinResult(skin=99, lighting=90, verdict="pass", rationale="match")


def _bad_skin(*_a, **_kw) -> SkinResult:
    return SkinResult(skin=40, lighting=70, verdict="ask", rationale="too dark")


def _noop_stitch(*_a, **_kw) -> None:
    return None


def test_run_success_creates_all_clips(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])
    _with_image_edit(project)

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
        skin_checker=_ok_skin,
        skin_stitcher=_noop_stitch,
    )

    reloaded = ChainProject.load(project.folder)
    assert all(c.status == "done" for c in reloaded.clips.values())
    assert reloaded.final.status == "done"
    assert reloaded.final.path == "final.mp4"
    assert len(extract_calls) == 3
    assert len(concat_calls) == 1
    # Clip 001 (idx=0) has no image_edit; clips 002, 003 do.
    assert worker.calls == [
        ("001", "video"),
        ("002", "image_edit"), ("002", "video"),
        ("003", "image_edit"), ("003", "video"),
    ]


def test_run_retries_then_stops_on_persistent_failure(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b"])

    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    worker = _FakeWorker(default_ok=False)
    runner = ChainRunner(project, config)
    runner.run(
        worker_factory=_factory(worker),
        frame_extractor=lambda v, f: None,
        concat=lambda cs, o: None,
        skin_checker=_ok_skin,
        skin_stitcher=_noop_stitch,
    )

    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["001"].status == "failed"
    assert reloaded.clips["001"].attempts == 3
    assert reloaded.clips["002"].status == "pending"
    assert reloaded.final.status == "pending"
    assert worker.calls == [("001", "video")]


def test_run_resume_skips_done(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])
    _with_image_edit(project)
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
        skin_checker=_ok_skin,
        skin_stitcher=_noop_stitch,
    )

    assert worker.calls == [
        ("002", "image_edit"), ("002", "video"),
        ("003", "image_edit"), ("003", "video"),
    ]


def test_resolution_downgrade_abort_stops_chain(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])
    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    worker = _FakeWorker(
        default_ok=True,
        markers={("001", "video"): [("EVENT", {"type": "resolution_downgrade", "actual_p": 480, "expected_p": 720})]},
    )
    runner = ChainRunner(project, config)
    calls: list[tuple[str, int]] = []

    def on_downgrade(clip_id: str, actual_p: int) -> str:
        calls.append((clip_id, actual_p))
        return "abort"

    runner.run(
        worker_factory=_factory(worker),
        frame_extractor=lambda v, f: f.write_bytes(b"P"),
        concat=lambda cs, o: o.write_bytes(b"F"),
        on_resolution_downgrade=on_downgrade,
        skin_checker=_ok_skin,
        skin_stitcher=_noop_stitch,
    )

    assert calls == [("001", 480)]
    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["001"].status == "failed"
    assert reloaded.clips["001"].reason == "user_aborted_after_480p"
    assert reloaded.clips["002"].status == "pending"
    assert reloaded.final.status == "pending"
    assert worker.calls == [("001", "video")]


def test_resolution_downgrade_accept_is_sticky(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])
    _with_image_edit(project)
    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    worker = _FakeWorker(
        default_ok=True,
        markers={
            ("001", "video"): [("EVENT", {"type": "resolution_downgrade", "actual_p": 480, "expected_p": 720})],
            ("002", "video"): [("EVENT", {"type": "resolution_downgrade", "actual_p": 480, "expected_p": 720})],
        },
    )
    runner = ChainRunner(project, config)
    calls: list[tuple[str, int]] = []

    def on_downgrade(clip_id: str, actual_p: int) -> str:
        calls.append((clip_id, actual_p))
        return "accept"

    runner.run(
        worker_factory=_factory(worker),
        frame_extractor=lambda v, f: f.write_bytes(b"P"),
        concat=lambda cs, o: o.write_bytes(b"F"),
        on_resolution_downgrade=on_downgrade,
        skin_checker=_ok_skin,
        skin_stitcher=_noop_stitch,
    )

    assert calls == [("001", 480)]
    assert runner.accept_480p is True
    reloaded = ChainProject.load(project.folder)
    assert all(c.status == "done" for c in reloaded.clips.values())
    assert reloaded.final.status == "done"


def test_no_downgrade_event_skips_prompt(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a"])
    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    worker = _FakeWorker(default_ok=True)
    runner = ChainRunner(project, config)
    calls: list[tuple[str, int]] = []

    runner.run(
        worker_factory=_factory(worker),
        frame_extractor=lambda v, f: f.write_bytes(b"P"),
        concat=lambda cs, o: o.write_bytes(b"F"),
        on_resolution_downgrade=lambda cid, ap: calls.append((cid, ap)) or "abort",
        skin_checker=_ok_skin,
        skin_stitcher=_noop_stitch,
    )

    assert calls == []
    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["001"].status == "done"


def test_run_stop_check_interrupts(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])
    config = {"ffmpeg": "ffmpeg", "cdp": {"url": "x", "base_url": "y"}, "defaults": {"retry_count": 2, "worker_timeout_sec": 600}}
    worker = _FakeWorker(default_ok=True)
    runner = ChainRunner(project, config)

    counter = {"n": 0}
    def stopper() -> bool:
        counter["n"] += 1
        return counter["n"] > 1

    runner.run(
        worker_factory=_factory(worker),
        frame_extractor=lambda v, f: f.write_bytes(b"P"),
        concat=lambda cs, o: o.write_bytes(b"F"),
        stop_check=stopper,
        skin_checker=_ok_skin,
        skin_stitcher=_noop_stitch,
    )

    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["001"].status == "done"
    assert reloaded.final.status == "pending"


def test_default_media_tools_use_configured_paths(tmp_path: Path, monkeypatch) -> None:
    project = _prep(tmp_path, ["a"])
    config = {
        "ffmpeg": {"path": "custom-ffmpeg", "ffprobe_path": "custom-ffprobe"},
        "cdp": {"url": "x", "base_url": "y"},
        "defaults": {"retry_count": 0, "worker_timeout_sec": 600},
    }
    worker = _FakeWorker(default_ok=True)
    calls: dict[str, object] = {}

    def fake_extract(video, frame, *, ffmpeg):
        calls["extract"] = ffmpeg
        frame.write_bytes(b"P")

    def fake_concat(clips, output, *, ffmpeg, ffprobe):
        calls["concat"] = (ffmpeg, ffprobe)
        output.write_bytes(b"F")

    monkeypatch.setattr("core.chain_runner.extract_last_frame", fake_extract)
    monkeypatch.setattr("core.chain_runner.concat_with_xfade", fake_concat)

    ChainRunner(project, config).run(
        worker_factory=_factory(worker),
        skin_checker=_ok_skin,
        skin_stitcher=_noop_stitch,
    )

    assert calls == {
        "extract": "custom-ffmpeg",
        "concat": ("custom-ffmpeg", "custom-ffprobe"),
    }


def test_second_clip_runs_image_edit_then_video(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["first", "second"])
    _with_image_edit(project, "fixed image edit")
    worker = _FakeWorker()

    ChainRunner(
        project,
        {"cdp": {"url": "x"}, "defaults": {"retry_count": 0, "worker_timeout_sec": 600}},
    ).run(
        worker_factory=_factory(worker),
        frame_extractor=lambda _v, f: f.write_bytes(b"RAW"),
        concat=lambda _cs, o: o.write_bytes(b"FINAL"),
        skin_checker=_ok_skin,
        skin_stitcher=_noop_stitch,
    )

    by_kind = {(t.task_id, t.kind): t for t in worker.tasks}
    assert ("001", "video") in by_kind
    assert by_kind[("001", "video")].ref_path.endswith("input\\ref.png") or by_kind[("001", "video")].ref_path.endswith("input/ref.png")
    edit = by_kind[("002", "image_edit")]
    assert edit.image_edit_prompt == "fixed image edit"
    assert edit.ref_path.endswith("frame_001.png")
    assert edit.output_path.endswith("refined_001.jpg")
    video = by_kind[("002", "video")]
    assert video.ref_path.endswith("refined_001.jpg")
    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["002"].refined_ref == "refined/refined_001.jpg"


def test_resume_reuses_existing_refined_frame(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["first", "second"])
    _with_image_edit(project)
    project.update_clip(
        "001", status="done",
        clip="clips/clip_001.mp4", frame="frames/frame_001.png",
    )
    (project.folder / "frames").mkdir(exist_ok=True)
    (project.folder / "frames" / "frame_001.png").write_bytes(b"RAW")
    (project.folder / "refined").mkdir(exist_ok=True)
    refined = project.folder / "refined" / "refined_001.jpg"
    refined.write_bytes(b"EXISTING")
    worker = _FakeWorker()

    ChainRunner(
        project,
        {"cdp": {"url": "x"}, "defaults": {"retry_count": 0, "worker_timeout_sec": 600}},
    ).run(
        worker_factory=_factory(worker),
        frame_extractor=lambda _v, f: f.write_bytes(b"RAW"),
        concat=lambda _cs, o: o.write_bytes(b"FINAL"),
        skin_checker=_ok_skin,
        skin_stitcher=_noop_stitch,
    )

    # Image_edit task NOT sent again — cached refined still passes skin check.
    assert worker.calls == [("002", "video")]
    assert refined.read_bytes() == b"EXISTING"


def test_skin_low_accept_continues(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b"])
    _with_image_edit(project)
    config = {"cdp": {"url": "x"}, "defaults": {"retry_count": 0, "worker_timeout_sec": 600}}
    worker = _FakeWorker()
    asks: list[tuple[str, int]] = []

    def on_skin(clip_id, result, stitched):
        asks.append((clip_id, result.skin))
        return "accept"

    ChainRunner(project, config).run(
        worker_factory=_factory(worker),
        frame_extractor=lambda _v, f: f.write_bytes(b"P"),
        concat=lambda _cs, o: o.write_bytes(b"F"),
        skin_checker=_bad_skin,
        skin_stitcher=_noop_stitch,
        on_skin_low=on_skin,
    )

    assert asks == [("002", 40)]
    assert worker.calls == [
        ("001", "video"),
        ("002", "image_edit"), ("002", "video"),
    ]
    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["002"].status == "done"


def test_skin_low_retry_then_pass(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b"])
    _with_image_edit(project)
    config = {"cdp": {"url": "x"}, "defaults": {"retry_count": 0, "worker_timeout_sec": 600}}
    worker = _FakeWorker()
    skin_calls = {"n": 0}

    def skin_seq(_stitched):
        skin_calls["n"] += 1
        if skin_calls["n"] == 1:
            return SkinResult(skin=40, lighting=70, verdict="ask", rationale="bad")
        return SkinResult(skin=98, lighting=92, verdict="pass", rationale="good")

    user_calls: list[str] = []

    def on_skin(clip_id, result, stitched):
        user_calls.append(clip_id)
        return "retry"

    ChainRunner(project, config).run(
        worker_factory=_factory(worker),
        frame_extractor=lambda _v, f: f.write_bytes(b"P"),
        concat=lambda _cs, o: o.write_bytes(b"F"),
        skin_checker=skin_seq,
        skin_stitcher=_noop_stitch,
        on_skin_low=on_skin,
    )

    assert user_calls == ["002"]
    # image_edit sent twice (1 failed skin, 1 passed), then video.
    assert worker.calls == [
        ("001", "video"),
        ("002", "image_edit"),
        ("002", "image_edit"),
        ("002", "video"),
    ]
    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["002"].status == "done"


def test_skin_low_abort_fails_clip(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b", "c"])
    _with_image_edit(project)
    config = {"cdp": {"url": "x"}, "defaults": {"retry_count": 0, "worker_timeout_sec": 600}}
    worker = _FakeWorker()

    def on_skin(clip_id, result, stitched):
        return "abort"

    ChainRunner(project, config).run(
        worker_factory=_factory(worker),
        frame_extractor=lambda _v, f: f.write_bytes(b"P"),
        concat=lambda _cs, o: o.write_bytes(b"F"),
        skin_checker=_bad_skin,
        skin_stitcher=_noop_stitch,
        on_skin_low=on_skin,
    )

    assert worker.calls == [
        ("001", "video"),
        ("002", "image_edit"),
    ]
    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["002"].status == "failed"
    assert reloaded.clips["002"].reason.startswith("user_aborted_skin_")
    assert reloaded.clips["003"].status == "pending"
    assert reloaded.final.status == "pending"


def test_skin_low_retry_exhausted_fails(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b"])
    _with_image_edit(project)
    config = {
        "cdp": {"url": "x"},
        "defaults": {"retry_count": 0, "worker_timeout_sec": 600},
        "skin_check": {"enabled": True, "threshold": 95, "max_retries": 1, "timeout_s": 10},
    }
    worker = _FakeWorker()
    calls: list[str] = []

    def on_skin(clip_id, result, stitched):
        calls.append(clip_id)
        return "retry"

    ChainRunner(project, config).run(
        worker_factory=_factory(worker),
        frame_extractor=lambda _v, f: f.write_bytes(b"P"),
        concat=lambda _cs, o: o.write_bytes(b"F"),
        skin_checker=_bad_skin,
        skin_stitcher=_noop_stitch,
        on_skin_low=on_skin,
    )

    # max_retries=1 → 2 attempts max. Both fail skin → both retry → exhausted.
    assert calls == ["002", "002"]
    assert worker.calls == [
        ("001", "video"),
        ("002", "image_edit"),
        ("002", "image_edit"),
    ]
    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["002"].status == "failed"
    assert reloaded.clips["002"].reason.startswith("skin_check_retries_exhausted_")


def test_skin_check_disabled_skips_check(tmp_path: Path) -> None:
    project = _prep(tmp_path, ["a", "b"])
    _with_image_edit(project)
    config = {
        "cdp": {"url": "x"},
        "defaults": {"retry_count": 0, "worker_timeout_sec": 600},
        "skin_check": {"enabled": False},
    }
    worker = _FakeWorker()
    skin_called = {"n": 0}

    def skin_fn(_stitched):
        skin_called["n"] += 1
        return _bad_skin()

    ChainRunner(project, config).run(
        worker_factory=_factory(worker),
        frame_extractor=lambda _v, f: f.write_bytes(b"P"),
        concat=lambda _cs, o: o.write_bytes(b"F"),
        skin_checker=skin_fn,
        skin_stitcher=_noop_stitch,
        on_skin_low=lambda *a: "abort",
    )

    assert skin_called["n"] == 0
    reloaded = ChainProject.load(project.folder)
    assert reloaded.clips["002"].status == "done"
