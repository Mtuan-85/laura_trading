from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from workers.task_contract import EXIT_PARSE_FAILED, EXIT_PREREQ_MISSING


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
