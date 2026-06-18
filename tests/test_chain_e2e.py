from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.chain_runner import ChainRunner
from core.project import ChainProject, ProjectInputs
from utils.video_concat import probe_duration
from workers.task_contract import EXIT_SUCCESS, TaskJson


def _ffmpeg() -> str:
    f = shutil.which("ffmpeg")
    if f is None:
        pytest.skip("ffmpeg not on PATH")
    return f


def _make_clip(path: Path, color: str, dur: int) -> None:
    subprocess.run(
        [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=64x64:r=10:d={dur}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(dur), str(path),
        ],
        check=True,
    )


def test_full_chain_with_mocked_worker(tmp_path: Path) -> None:
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "input").mkdir()
    ref = folder / "input" / "ref.png"
    ref.write_bytes(bytes.fromhex(
        "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C4890000"
        "000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082"
    ))
    prompts = [{"id": i + 1, "prompt": f"clip {i+1}"} for i in range(3)]
    (folder / "input" / "prompts.json").write_text(json.dumps(prompts), encoding="utf-8")

    inputs = ProjectInputs(ref_image="input/ref.png", prompts="input/prompts.json", aspect="9:16", duration=2)
    project = ChainProject.create(folder, inputs, [f"{i+1:03d}" for i in range(3)])

    config = {
        "cdp": {"url": "x", "base_url": "y"},
        "defaults": {"retry_count": 0, "worker_timeout_sec": 600},
    }

    colors = ["red", "green", "blue"]

    @contextmanager
    def fake_worker_ctx(task: TaskJson):
        out = Path(task.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        idx = int(task.task_id) - 1
        _make_clip(out, colors[idx], 2)

        class _W:
            def iter_markers(self):
                return iter([])

            def wait(self, timeout=None):
                return EXIT_SUCCESS

            def terminate(self):
                pass

        yield _W()

    runner = ChainRunner(project, config)
    runner.run(worker_factory=fake_worker_ctx)

    reloaded = ChainProject.load(folder)
    assert all(c.status == "done" for c in reloaded.clips.values())
    assert reloaded.final.status == "done"

    final = folder / "final.mp4"
    assert final.exists()
    dur = probe_duration(final)
    assert 4.6 <= dur <= 5.4
