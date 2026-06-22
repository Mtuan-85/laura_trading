from __future__ import annotations

import faulthandler
import json
import shutil
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.chain_runner import ChainRunner, RunnerEvent
from core.config import load_config as _core_load_config
from core.project import ChainProject, ProjectInputs
from core.skin_check import SkinResult
from ui.main_window import MainWindow
from utils.frame_extractor import extract_last_frame
from utils.logging import setup_logging
from utils.paths import next_project_folder

_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
IMAGE_EDIT_SOURCE = Path(__file__).resolve().parent.parent / "image_edit.json"


class _RunnerThread(QThread):
    event = pyqtSignal(object)
    finished_clean = pyqtSignal()
    crashed = pyqtSignal(str)
    ask_resolution_downgrade = pyqtSignal(str, int)
    ask_skin_low = pyqtSignal(str, object, str)

    def __init__(self, runner: ChainRunner) -> None:
        super().__init__()
        self.runner = runner
        self._stop_requested = False
        self._current_worker = None
        self._decision_event = threading.Event()
        self._decision: str | None = None

    def request_stop(self) -> None:
        logger.info("[thread] request_stop() called")
        self._stop_requested = True
        # Unblock any pending resolution-downgrade decision so the worker
        # thread can notice the stop and exit cleanly.
        self._decision = "abort"
        self._decision_event.set()
        w = self._current_worker
        if w is not None:
            try:
                logger.info("[thread] terminating running worker subprocess")
                w.terminate()
            except Exception as e:
                logger.warning(f"[thread] worker terminate failed: {e}")

    def on_decision(self, choice: str) -> None:
        """Called from main thread when user clicks a button in the popup."""
        self._decision = choice
        self._decision_event.set()

    def request_resolution_decision(self, clip_id: str, actual_p: int) -> str:
        """Called from this worker thread by ChainRunner. Blocks until the
        main thread shows the popup and the user picks a button."""
        self._decision = None
        self._decision_event.clear()
        self.ask_resolution_downgrade.emit(clip_id, actual_p)
        self._decision_event.wait()
        return self._decision or "abort"

    def request_skin_decision(self, clip_id: str, result, stitched: Path) -> str:
        self._decision = None
        self._decision_event.clear()
        self.ask_skin_low.emit(clip_id, result, str(stitched))
        self._decision_event.wait()
        return self._decision or "abort"

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
                on_resolution_downgrade=self.request_resolution_decision,
                on_skin_low=self.request_skin_decision,
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


def _resolve_project_from_selection(selection: Path) -> Path | None:
    selection = Path(selection)
    if selection.name.lower() == "state.json" and selection.exists():
        return selection.parent
    if (
        selection.name.lower() == "prompts.json"
        and selection.parent.name.lower() == "input"
        and (selection.parent.parent / "state.json").exists()
    ):
        return selection.parent.parent
    return None


def _validate_prompts(raw: object) -> tuple[list[dict], list[str]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("prompts.json must be a non-empty list")
    prompts: list[dict] = []
    clip_ids: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or "id" not in item or "prompt" not in item:
            raise ValueError(f"prompt item {index} must contain id and prompt")
        try:
            clip_id = f"{int(item['id']):03d}"
        except (TypeError, ValueError) as exc:
            raise ValueError(f"prompt item {index} has invalid id: {item.get('id')!r}") from exc
        if clip_id in seen:
            raise ValueError(f"duplicate prompt id: {item['id']}")
        seen.add(clip_id)
        prompts.append(item)
        clip_ids.append(clip_id)
    return prompts, clip_ids


def _ffmpeg_path(config: dict) -> str:
    value = config.get("ffmpeg", {})
    if isinstance(value, dict):
        return str(value.get("path", "ffmpeg"))
    return str(value or "ffmpeg")


def _ensure_project_image_edit(folder: Path) -> Path:
    target = folder / "input" / "image_edit.json"
    if target.exists():
        raw = json.loads(target.read_text(encoding="utf-8"))
    else:
        if not IMAGE_EDIT_SOURCE.exists():
            raise FileNotFoundError(f"image edit config not found: {IMAGE_EDIT_SOURCE}")
        raw = json.loads(IMAGE_EDIT_SOURCE.read_text(encoding="utf-8"))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(IMAGE_EDIT_SOURCE, target)
    prompt = raw.get("prompt") if isinstance(raw, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("image_edit.json must contain a non-empty 'prompt'")
    return target


def _setup_project(payload: dict, config: dict | None = None) -> ChainProject:
    logger.info(f"[setup] payload={payload}")
    config = config or {}
    prompts_path = Path(payload["prompts"])
    if not prompts_path.exists():
        raise FileNotFoundError(f"project state or prompts.json not found: {prompts_path}")

    existing_folder = _resolve_project_from_selection(prompts_path)
    if existing_folder is not None:
        project = ChainProject.load(existing_folder)
        stored_prompts = existing_folder / project.inputs.prompts
        raw = json.loads(stored_prompts.read_text(encoding="utf-8"))
        _validate_prompts(raw)
        _ensure_project_image_edit(existing_folder)
        if project.inputs.image_edit != "input/image_edit.json":
            project.inputs.image_edit = "input/image_edit.json"
            project.save()
        logger.info(f"[setup] resuming existing project={existing_folder}")
        return project

    ref = Path(payload["ref"])
    if not ref.exists():
        raise FileNotFoundError(f"input file not found: {ref}")

    raw_prompts = json.loads(prompts_path.read_text(encoding="utf-8"))
    prompts, _ = _validate_prompts(raw_prompts)
    limit = int(payload.get("limit") or 0)
    if limit > 0:
        prompts = prompts[:limit]
    prompts, clip_ids = _validate_prompts(prompts)

    folder = next_project_folder(ref.parent, datetime.now().astimezone())
    logger.info(f"[setup] project folder={folder}")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "input").mkdir(exist_ok=True)
    (folder / "logs").mkdir(exist_ok=True)

    ref_target = folder / "input" / "ref.png"
    if ref.suffix.lower() in _VIDEO_SUFFIXES:
        logger.info(f"[setup] input is video — extracting last frame from {ref}")
        shutil.copyfile(ref, folder / "input" / f"source{ref.suffix.lower()}")
        extract_last_frame(ref, ref_target, offset_sec=0.5, ffmpeg=_ffmpeg_path(config))
        logger.info(f"[setup] wrote ref frame {ref_target}")
    else:
        shutil.copyfile(ref, ref_target)
    (folder / "input" / "prompts.json").write_text(
        json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _ensure_project_image_edit(folder)
    logger.info("[setup] copied input + prompts into project")

    logger.info(f"[setup] parsed prompts: {len(prompts)} items")

    if limit > 0:
        logger.info(f"[setup] sliced to first {limit} prompts")

    logger.info(f"[setup] clip_ids={clip_ids}")

    inputs = ProjectInputs(
        ref_image="input/ref.png",
        prompts="input/prompts.json",
        image_edit="input/image_edit.json",
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
    state: dict[str, object | None] = {
        "thread": None,
        "prompt_selection": None,
        "project_folder": None,
    }

    def handle_start(payload: dict) -> None:
        logger.info("[gui] Start clicked")
        try:
            prompt_selection = str(Path(payload["prompts"]).resolve())
            cached_folder = state.get("project_folder")
            if (
                state.get("prompt_selection") == prompt_selection
                and isinstance(cached_folder, Path)
                and (cached_folder / "state.json").exists()
            ):
                project = ChainProject.load(cached_folder)
                logger.info(f"[gui] resuming cached project={cached_folder}")
            else:
                project = _setup_project(payload, config)
                state["prompt_selection"] = prompt_selection
                state["project_folder"] = project.folder
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
            elif ev.kind == "skin_check":
                p = ev.payload
                win.append_log(
                    f"  [skin_check] clip={ev.clip_id} attempt={p.get('attempt')} "
                    f"skin={p.get('skin')} lighting={p.get('lighting')} "
                    f"verdict={p.get('verdict')}"
                )
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

        def on_ask_skin_low(clip_id: str, result, stitched_path: str) -> None:
            box = QMessageBox(win)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Skin tone không khớp")
            box.setText(
                f"Clip {clip_id}: skin similarity {getattr(result, 'skin', 0)}%"
            )
            box.setInformativeText(
                f"Lighting: {getattr(result, 'lighting', 0)}%\n"
                f"Lý do: {getattr(result, 'rationale', '')}\n\n"
                f"Stitched: {stitched_path}\n\n"
                f"• Continue = dùng ảnh hiện tại, tiếp tục.\n"
                f"• Retry = bảo Grok làm lại image_edit.\n"
                f"• Abort = dừng toàn bộ chain."
            )
            accept_btn = box.addButton("Continue (dùng ảnh này)", QMessageBox.ButtonRole.AcceptRole)
            retry_btn = box.addButton("Retry image_edit", QMessageBox.ButtonRole.ActionRole)
            abort_btn = box.addButton("Abort chain", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(retry_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is accept_btn:
                choice = "accept"
            elif clicked is retry_btn:
                choice = "retry"
            else:
                choice = "abort"
            win.append_log(f"[skin] clip={clip_id} skin={getattr(result,'skin',0)} → user chose {choice}")
            thread.on_decision(choice)

        def on_ask_downgrade(clip_id: str, actual_p: int) -> None:
            box = QMessageBox(win)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Grok hạ resolution")
            box.setText(f"Grok hạ resolution xuống {actual_p}p")
            box.setInformativeText(
                f"Clip {clip_id}: Grok không serve 720p (có thể hết daily quota).\n"
                f"• Accept = tiếp tục chain ở {actual_p}p, không hỏi lại các clip sau.\n"
                f"• Abort = dừng toàn bộ chain."
            )
            accept_btn = box.addButton(
                f"Accept {actual_p}p (tiếp tục)", QMessageBox.ButtonRole.AcceptRole
            )
            abort_btn = box.addButton("Abort chain", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(abort_btn)
            box.exec()
            choice = "accept" if box.clickedButton() is accept_btn else "abort"
            win.append_log(f"[downgrade] clip={clip_id} actual={actual_p}p → user chose {choice}")
            thread.on_decision(choice)

        thread.event.connect(on_event)
        thread.finished_clean.connect(win.reset_buttons)
        thread.crashed.connect(on_crash)
        thread.ask_resolution_downgrade.connect(
            on_ask_downgrade, type=Qt.ConnectionType.QueuedConnection
        )
        thread.ask_skin_low.connect(
            on_ask_skin_low, type=Qt.ConnectionType.QueuedConnection
        )
        thread.start()

    def handle_stop() -> None:
        thread = state["thread"]
        if isinstance(thread, _RunnerThread):
            thread.request_stop()
            win.set_status("Stopping...")

    def handle_close() -> None:
        logger.info("[gui] window close requested")
        thread = state["thread"]
        if isinstance(thread, _RunnerThread) and thread.isRunning():
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
