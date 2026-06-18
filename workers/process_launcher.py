from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from loguru import logger

from workers.task_contract import parse_marker


class LaunchedWorker:
    def __init__(self, module: str, task_json_path: Path, *, cwd: Path | None = None, env: dict | None = None):
        self.module = module
        self.task_json_path = Path(task_json_path)
        self.cwd = Path(cwd) if cwd else None
        self.env = env or os.environ.copy()
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> LaunchedWorker:
        cmd = [sys.executable, "-m", self.module, "--task", str(self.task_json_path)]
        logger.info(f"Launching worker: {' '.join(cmd)}")
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(self.cwd) if self.cwd else None,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=creationflags,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc and self.proc.poll() is None:
            self.terminate()

    def iter_markers(self) -> Iterator[tuple[str, dict] | str]:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            marker = parse_marker(line)
            if marker:
                yield marker
            else:
                yield line.rstrip("\n")

    def wait(self, timeout: float | None = None) -> int:
        assert self.proc
        return self.proc.wait(timeout=timeout)

    def terminate(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.proc.terminate()
            self.proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            self.proc.kill()
