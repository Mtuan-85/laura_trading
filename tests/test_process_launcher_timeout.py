"""Per-task watchdog timeout in LaunchedWorker.send_task.

Uses a tiny inline Python worker (sleep + never emit a marker) so we exercise
the real Popen/stdout reader without standing up Patchright or any browser.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from workers.process_launcher import LaunchedWorker
from workers.task_contract import TaskJson


_SLEEP_WORKER_SRC = """
import sys, time
# Drain one stdin command, then sleep past any reasonable timeout without
# emitting a terminal marker. The launcher's watchdog should terminate us.
sys.stdin.readline()
time.sleep(30)
"""


def _make_task(tmp_path: Path) -> TaskJson:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n")
    return TaskJson(
        task_id="001",
        prompt="hi",
        ref_path=str(ref),
        aspect="9:16",
        duration=10,
        output_path=str(tmp_path / "out.mp4"),
        cdp_url="http://127.0.0.1:9222",
        cdp_base_url="https://grok.com/imagine",
    )


def test_send_task_times_out_and_terminates_worker(tmp_path: Path) -> None:
    src = tmp_path / "sleep_worker.py"
    src.write_text(_SLEEP_WORKER_SRC, encoding="utf-8")

    # Run our sleep script as the worker module via -c. We bypass LaunchedWorker's
    # default "python -m workers.video_chain_worker" by overriding the cmd in-place
    # after entering the context manager.
    worker = LaunchedWorker(module="workers.video_chain_worker", max_attempts=1)
    # Swap the cmd to our inline sleeper before __enter__ runs.
    import subprocess
    proc = subprocess.Popen(
        [sys.executable, str(src)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    worker.proc = proc

    task = _make_task(tmp_path)
    t0 = time.monotonic()
    outcome = worker.send_task(task, timeout_sec=1.0)
    elapsed = time.monotonic() - t0

    assert outcome == {"ok": False, "reason": "timeout", "attempts": 0}
    # Should fire close to the 1s budget, certainly under 5s.
    assert elapsed < 5.0
    # terminate() spawns taskkill async — wait briefly for the OS to reap it.
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    assert proc.poll() is not None
