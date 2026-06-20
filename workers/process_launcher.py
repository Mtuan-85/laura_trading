"""Long-lived worker launcher.

Spawns the worker subprocess once per chain. Each clip is submitted via
`send_task()` over stdin; markers are streamed back via stdout. Shutdown is
explicit (`shutdown()`); `terminate()` is the nuclear option for the Stop
button or window close.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from loguru import logger

from workers.task_contract import TaskJson, parse_marker


class LaunchedWorker:
    """Long-lived worker process holding one GrokConnection.

    Lifecycle::

        with LaunchedWorker(...) as worker:
            result = worker.send_task(task, on_marker=...)
            ...
        # exit: graceful shutdown, then 3s wait, then terminate.
    """

    def __init__(
        self,
        module: str = "workers.video_chain_worker",
        *,
        max_attempts: int = 3,
        cwd: Path | None = None,
        env: dict | None = None,
    ):
        self.module = module
        self.max_attempts = max_attempts
        self.cwd = Path(cwd) if cwd else None
        self.env = env or os.environ.copy()
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "LaunchedWorker":
        cmd = [
            sys.executable, "-X", "utf8", "-m", self.module,
            "--max-attempts", str(self.max_attempts),
        ]
        logger.info(f"[launcher] cmd={' '.join(cmd)}")
        env = dict(self.env)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            )
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(self.cwd) if self.cwd else None,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as e:
            logger.error(f"[launcher] Popen failed: {type(e).__name__}: {e}")
            raise
        logger.info(f"[launcher] spawned pid={self.proc.pid}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    def send_task(
        self,
        task: TaskJson,
        *,
        on_marker: Callable[[tuple[str, dict] | str], None] = lambda _m: None,
        stop_check: Callable[[], bool] = lambda: False,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        """Send one task; stream stdout until TASK DONE/FAILED; return outcome.

        `timeout_sec` is per-task (one send_task call): if no terminal marker
        arrives within that wall-clock budget, the worker is terminated and
        the chain stops. None disables the watchdog.

        Returns one of:
            {"ok": True,  "path": str, "attempts": int}
            {"ok": False, "reason": str, "attempts": int}
            {"ok": False, "reason": "worker_died", "attempts": 0}
            {"ok": False, "reason": "stopped", "attempts": 0}
            {"ok": False, "reason": "timeout", "attempts": 0}
        """
        if self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
            return {"ok": False, "reason": "worker_not_started", "attempts": 0}

        payload = {"cmd": "task", "task": json.loads(task.model_dump_json())}
        try:
            self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            logger.error(f"[launcher] stdin write failed: {e}")
            return {"ok": False, "reason": "worker_died", "attempts": 0}

        timed_out = {"flag": False}
        watchdog: threading.Timer | None = None
        if timeout_sec is not None and timeout_sec > 0:
            def _fire_timeout() -> None:
                logger.warning(
                    f"[launcher] task={task.task_id} exceeded {timeout_sec}s — terminating worker"
                )
                timed_out["flag"] = True
                self.terminate()
            watchdog = threading.Timer(timeout_sec, _fire_timeout)
            watchdog.daemon = True
            watchdog.start()

        try:
            for line in self.proc.stdout:
                if stop_check():
                    self.terminate()
                    return {"ok": False, "reason": "stopped", "attempts": 0}
                marker = parse_marker(line)
                if marker:
                    kind, data = marker
                    on_marker(marker)
                    if kind == "TASK DONE":
                        return {
                            "ok": True,
                            "path": data.get("clip"),
                            "attempts": int(data.get("attempts", 1)),
                        }
                    if kind == "TASK FAILED":
                        return {
                            "ok": False,
                            "reason": str(data.get("reason", "unknown")),
                            "attempts": int(data.get("attempts", 0)),
                        }
                else:
                    on_marker(line.rstrip("\n"))
        finally:
            if watchdog is not None:
                watchdog.cancel()

        # stdout closed before TASK DONE/FAILED — either we hit the watchdog or
        # the worker died on its own.
        if timed_out["flag"]:
            return {"ok": False, "reason": "timeout", "attempts": 0}
        return {"ok": False, "reason": "worker_died", "attempts": 0}

    def shutdown(self, timeout: float = 3.0) -> None:
        """Send shutdown command; wait briefly; then terminate if still alive."""
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            if self.proc.stdin is not None and not self.proc.stdin.closed:
                self.proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                self.proc.stdin.flush()
                self.proc.stdin.close()
        except (BrokenPipeError, OSError) as e:
            logger.debug(f"[launcher] shutdown write ignored: {e}")
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(f"[launcher] worker pid={self.proc.pid} ignored shutdown — terminating")
            self.terminate()

    def terminate(self) -> None:
        """Force-kill the worker tree. Safe to call repeatedly."""
        if not self.proc or self.proc.poll() is not None:
            return
        pid = self.proc.pid
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5,
                )
            except Exception as e:
                logger.warning(f"[launcher] taskkill failed pid={pid}: {e}")
                try:
                    self.proc.kill()
                except Exception:
                    pass
        else:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                self.proc.kill()
        logger.info(f"[launcher] terminated pid={pid}")

    # --- legacy helpers (kept so old tests / scripts don't break) ---

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
