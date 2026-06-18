from __future__ import annotations

import faulthandler
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from core.chain_runner import ChainRunner, RunnerEvent
from core.project import ChainProject, ProjectInputs
from ui.main_window import MainWindow
from utils.logging import setup_logging
from utils.paths import project_folder_name


class _RunnerThread(QThread):
    event = pyqtSignal(object)
    finished_clean = pyqtSignal()

    def __init__(self, runner: ChainRunner) -> None:
        super().__init__()
        self.runner = runner
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        def emit_event(ev: RunnerEvent) -> None:
            self.event.emit(ev)

        self.runner.run(
            on_event=emit_event,
            stop_check=lambda: self._stop_requested,
        )
        self.finished_clean.emit()


def _load_config() -> dict:
    cfg_path = Path(__file__).resolve().parents[1] / "config.yaml"
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))


def _setup_project(payload: dict) -> ChainProject:
    ref = Path(payload["ref"])
    prompts_path = Path(payload["prompts"])
    folder = ref.parent / project_folder_name(datetime.now().astimezone())
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "input").mkdir(exist_ok=True)
    (folder / "logs").mkdir(exist_ok=True)
    shutil.copyfile(ref, folder / "input" / "ref.png")
    shutil.copyfile(prompts_path, folder / "input" / "prompts.json")

    prompts = json.loads((folder / "input" / "prompts.json").read_text(encoding="utf-8"))
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompts.json must be a non-empty list")
    clip_ids = [f"{int(item['id']):03d}" for item in prompts]

    inputs = ProjectInputs(
        ref_image="input/ref.png",
        prompts="input/prompts.json",
        aspect=payload["aspect"],
        duration=int(payload["duration"]),
    )
    return ChainProject.create(folder, inputs, clip_ids)


def main() -> int:
    faulthandler.enable()
    setup_logging(level="INFO")

    config = _load_config()
    app = QApplication(sys.argv)
    win = MainWindow()
    state: dict[str, _RunnerThread | None] = {"thread": None}

    def handle_start(payload: dict) -> None:
        try:
            project = _setup_project(payload)
        except Exception as e:
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
                win.append_log(f"  marker: {ev.payload}")
            elif ev.kind == "worker_log":
                line = ev.payload.get("line", "")
                if line.strip():
                    win.append_log(f"  {line}")
            elif ev.kind == "stopped":
                win.append_log("Stopped by user.")
                win.set_status("Stopped.")

        thread.event.connect(on_event)
        thread.finished_clean.connect(win.reset_buttons)
        thread.start()

    def handle_stop() -> None:
        thread = state["thread"]
        if thread is not None:
            thread.request_stop()
            win.set_status("Stopping...")

    win.start_requested.connect(handle_start)
    win.stop_requested.connect(handle_stop)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
