from __future__ import annotations

import faulthandler
import json
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from core.chain_runner import ChainRunner, RunnerEvent
from core.config import load_config as _core_load_config
from core.project import ChainProject, ProjectInputs
from ui.main_window import MainWindow
from utils.frame_extractor import extract_last_frame
from utils.logging import setup_logging
from utils.paths import project_folder_name

_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".avi"}


class _RunnerThread(QThread):
    event = pyqtSignal(object)
    finished_clean = pyqtSignal()
    crashed = pyqtSignal(str)

    def __init__(self, runner: ChainRunner) -> None:
        super().__init__()
        self.runner = runner
        self._stop_requested = False
        self._current_worker = None

    def request_stop(self) -> None:
        logger.info("[thread] request_stop() called")
        self._stop_requested = True
        w = self._current_worker
        if w is not None:
            try:
                logger.info("[thread] terminating running worker subprocess")
                w.terminate()
            except Exception as e:
                logger.warning(f"[thread] worker terminate failed: {e}")

    def run(self) -> None:
        logger.info("[thread] _RunnerThread.run() started")

        def emit_event(ev: RunnerEvent) -> None:
            self.event.emit(ev)

        def on_worker_started(w) -> None:
            self._current_worker = w

        try:
            self.runner.run(
                on_event=emit_event,
                stop_check=lambda: self._stop_requested,
                on_worker_started=on_worker_started,
            )
            logger.info("[thread] runner.run() returned cleanly")
        except Exception:
            tb = traceback.format_exc()
            logger.error(f"[thread] Runner crashed:\n{tb}")
            self.crashed.emit(tb)
        finally:
            self._current_worker = None
            logger.info("[thread] emitting finished_clean")
            self.finished_clean.emit()


def _keep_worker_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    # Drop Playwright actionability dumps: HTML overlays, locator chains,
    # stack-trace continuations. They arrive as many lines, each starting
    # with whitespace + "- " or with raw HTML/JS code.
    if s.startswith("<") or s.startswith("- ") or s.startswith("at "):
        return False
    if "<div " in s or "</div>" in s or "intercepts pointer events" in s:
        return False
    return True


def _load_config() -> dict:
    return _core_load_config()


def _setup_project(payload: dict) -> ChainProject:
    logger.info(f"[setup] payload={payload}")
    ref = Path(payload["ref"])
    prompts_path = Path(payload["prompts"])
    if not ref.exists():
        raise FileNotFoundError(f"input file not found: {ref}")
    if not prompts_path.exists():
        raise FileNotFoundError(f"prompts.json not found: {prompts_path}")
    folder = ref.parent / project_folder_name(datetime.now().astimezone())
    logger.info(f"[setup] project folder={folder}")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "input").mkdir(exist_ok=True)
    (folder / "logs").mkdir(exist_ok=True)

    ref_target = folder / "input" / "ref.png"
    if ref.suffix.lower() in _VIDEO_SUFFIXES:
        logger.info(f"[setup] input is video — extracting last frame from {ref}")
        shutil.copyfile(ref, folder / "input" / f"source{ref.suffix.lower()}")
        extract_last_frame(ref, ref_target, offset_sec=0.5)
        logger.info(f"[setup] wrote ref frame {ref_target}")
    else:
        shutil.copyfile(ref, ref_target)
    shutil.copyfile(prompts_path, folder / "input" / "prompts.json")
    logger.info("[setup] copied input + prompts into project")

    prompts = json.loads((folder / "input" / "prompts.json").read_text(encoding="utf-8"))
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompts.json must be a non-empty list")
    logger.info(f"[setup] parsed prompts: {len(prompts)} items")

    limit = int(payload.get("limit") or 0)
    if limit > 0:
        prompts = prompts[:limit]
        (folder / "input" / "prompts.json").write_text(
            json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"[setup] sliced to first {limit} prompts")

    clip_ids = [f"{int(item['id']):03d}" for item in prompts]
    logger.info(f"[setup] clip_ids={clip_ids}")

    inputs = ProjectInputs(
        ref_image="input/ref.png",
        prompts="input/prompts.json",
        aspect=payload["aspect"],
        duration=int(payload["duration"]),
    )
    project = ChainProject.create(folder, inputs, clip_ids)
    logger.info("[setup] ChainProject created, state.json written")
    return project


def main() -> int:
    faulthandler.enable()
    setup_logging(level="INFO")

    def _hook(exc_type, exc, tb):
        logger.error("Uncaught: " + "".join(traceback.format_exception(exc_type, exc, tb)))
    sys.excepthook = _hook

    config = _load_config()
    app = QApplication(sys.argv)
    win = MainWindow()
    state: dict[str, _RunnerThread | None] = {"thread": None}

    def handle_start(payload: dict) -> None:
        logger.info("[gui] Start clicked")
        try:
            project = _setup_project(payload)
        except Exception as e:
            logger.exception("[gui] _setup_project failed")
            win.append_log(f"[Setup error] {type(e).__name__}: {e}")
            win.set_status(f"Setup failed: {e}")
            win.reset_buttons()
            return

        win.set_output_folder(project.folder)
        setup_logging(log_file=project.folder / "logs" / "app.log", level="INFO")

        total = len(project.clips)
        win.set_progress(0, total)
        win.append_log(f"Project folder: {project.folder}")
        win.set_status(f"Starting {total} clips...")

        runner = ChainRunner(project, config)
        thread = _RunnerThread(runner)
        state["thread"] = thread

        done_count = {"n": 0}

        def on_event(ev: RunnerEvent) -> None:
            if ev.kind == "clip_started":
                win.set_status(f"Clip {ev.clip_id} (attempt {ev.payload.get('attempt', '?')})")
            elif ev.kind == "clip_done":
                done_count["n"] += 1
                win.set_progress(done_count["n"], total)
                win.append_log(f"Clip {ev.clip_id} done")
            elif ev.kind == "clip_failed":
                win.append_log(f"Clip {ev.clip_id} FAILED: {ev.payload}")
                win.set_status(f"Clip {ev.clip_id} failed - chain stopped")
            elif ev.kind == "final_done":
                win.append_log(f"Final video ready: {ev.payload.get('path')}")
                win.set_status("Done.")
            elif ev.kind == "final_failed":
                win.append_log(f"Concat failed: {ev.payload}")
            elif ev.kind == "worker_marker":
                p = dict(ev.payload)
                kind = p.pop("kind", "marker")
                # Don't echo prompt text; loguru file has it if needed.
                p.pop("prompt", None)
                p.pop("trace", None)
                if "reason" in p and len(str(p["reason"])) > 200:
                    p["reason"] = str(p["reason"])[:200] + "..."
                win.append_log(f"  [{kind}] {p}" if p else f"  [{kind}]")
            elif ev.kind == "worker_log":
                line = ev.payload.get("line", "").rstrip()
                if _keep_worker_line(line):
                    if len(line) > 200:
                        line = line[:200] + "..."
                    win.append_log(f"  {line}")
            elif ev.kind == "stopped":
                win.append_log("Stopped by user.")
                win.set_status("Stopped.")

        def on_crash(tb: str) -> None:
            win.append_log(f"[CRASH]\n{tb}")
            win.set_status("Crashed - see log")

        thread.event.connect(on_event)
        thread.finished_clean.connect(win.reset_buttons)
        thread.crashed.connect(on_crash)
        thread.start()

    def handle_stop() -> None:
        thread = state["thread"]
        if thread is not None:
            thread.request_stop()
            win.set_status("Stopping...")

    def handle_close() -> None:
        logger.info("[gui] window close requested")
        thread = state["thread"]
        if thread is not None and thread.isRunning():
            logger.info("[gui] stopping running thread before exit")
            thread.request_stop()
            if not thread.wait(3000):
                logger.warning("[gui] thread did not finish in 3s — forcing terminate")
                # Force-kill any worker subprocess that's still alive
                w = getattr(thread, "_current_worker", None)
                if w is not None:
                    try:
                        w.terminate()
                    except Exception as e:
                        logger.warning(f"[gui] force-kill worker failed: {e}")
                thread.terminate()
                thread.wait(1000)
        logger.info("[gui] quitting QApplication")
        QApplication.quit()

    win.start_requested.connect(handle_start)
    win.stop_requested.connect(handle_stop)
    win.close_requested.connect(handle_close)
    win.show()

    rc = app.exec()
    logger.info(f"[gui] app.exec returned {rc}")
    # Belt-and-suspenders: if any non-daemon thread or stale subprocess is
    # still holding the interpreter alive after exec returns, force exit.
    import os as _os
    _os._exit(rc)


if __name__ == "__main__":
    sys.exit(main())
