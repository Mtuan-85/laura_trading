from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import json

from loguru import logger

from core.project import ChainProject
from utils.frame_extractor import extract_last_frame
from utils.video_concat import concat_with_xfade
from workers.process_launcher import LaunchedWorker
from workers.task_contract import TaskJson


@dataclass
class RunnerEvent:
    kind: str
    clip_id: str | None
    payload: dict[str, Any]


# Factory returns a context manager that yields a long-lived worker handle.
# The handle must expose:
#   - send_task(task, on_marker=..., stop_check=...) -> dict
#   - terminate()
WorkerFactory = Callable[[], AbstractContextManager]


def _default_worker_factory_for(max_attempts: int) -> WorkerFactory:
    def factory() -> AbstractContextManager:
        return LaunchedWorker(max_attempts=max_attempts)
    return factory


def _now() -> str:
    return datetime.now().astimezone().isoformat()


class ChainRunner:
    def __init__(self, project: ChainProject, config: dict):
        self.project = project
        self.config = config

    def run(
        self,
        *,
        worker_factory: WorkerFactory | None = None,
        frame_extractor: Callable[[Path, Path], None] | None = None,
        concat: Callable[[list[Path], Path], None] | None = None,
        stop_check: Callable[[], bool] = lambda: False,
        on_event: Callable[[RunnerEvent], None] = lambda e: None,
        on_worker_started: Callable[[Any], None] = lambda w: None,
    ) -> None:
        # retry_count of N means N+1 total attempts (one initial + N retries).
        defaults_cfg = self.config.get("defaults", {})
        retry_count = int(defaults_cfg.get("retry_count", 2))
        max_attempts = retry_count + 1
        timeout_raw = defaults_cfg.get("worker_timeout_sec", 600)
        try:
            worker_timeout = float(timeout_raw) if timeout_raw else None
        except (TypeError, ValueError):
            worker_timeout = 600.0
        worker_factory = worker_factory or _default_worker_factory_for(max_attempts)
        frame_extractor = frame_extractor or extract_last_frame
        concat = concat or concat_with_xfade

        logger.info("[runner] loading prompts")
        prompts = self._load_prompts()
        logger.info(f"[runner] loaded {len(prompts)} prompts, max_attempts={max_attempts}")

        folder = self.project.folder
        (folder / "clips").mkdir(exist_ok=True)
        (folder / "frames").mkdir(exist_ok=True)

        clip_ids_in_order = sorted(self.project.clips.keys())
        logger.info(f"[runner] starting chain over {len(clip_ids_in_order)} clips: {clip_ids_in_order}")

        with worker_factory() as worker:
            on_worker_started(worker)

            for idx, clip_id in enumerate(clip_ids_in_order):
                if stop_check():
                    on_event(RunnerEvent("stopped", clip_id, {}))
                    return

                clip_state = self.project.clips[clip_id]
                if clip_state.status == "done":
                    continue

                prompt_text = prompts.get(clip_id, "")
                ref_rel = "input/ref.png" if idx == 0 else f"frames/frame_{int(clip_ids_in_order[idx-1]):03d}.png"
                ref_abs = folder / ref_rel
                clip_abs = folder / f"clips/clip_{int(clip_id):03d}.mp4"
                frame_abs = folder / f"frames/frame_{int(clip_id):03d}.png"

                self.project.update_clip(
                    clip_id,
                    status="running",
                    prompt=prompt_text,
                    ref=str(ref_rel),
                    started_at=_now(),
                )
                on_event(RunnerEvent("clip_started", clip_id, {"attempt": 1}))

                task = TaskJson(
                    task_id=clip_id,
                    prompt=prompt_text,
                    ref_path=str(ref_abs),
                    aspect=self.project.inputs.aspect,
                    duration=self.project.inputs.duration,
                    output_path=str(clip_abs),
                    cdp_url=self.config.get("cdp", {}).get("url", "http://127.0.0.1:9222"),
                    cdp_base_url=self.config.get("cdp", {}).get("base_url", "https://grok.com/imagine"),
                )

                logger.info(f"[runner] sending clip={clip_id} to worker (timeout={worker_timeout}s)")
                outcome = _send_task_with_optional_timeout(
                    worker, task, on_event, clip_id, stop_check, worker_timeout
                )
                logger.info(f"[runner] clip={clip_id} outcome={outcome}")

                if outcome.get("reason") == "stopped":
                    self.project.update_clip(clip_id, status="interrupted", finished_at=_now(), attempts=outcome.get("attempts", 0))
                    on_event(RunnerEvent("stopped", clip_id, {}))
                    return

                attempts = int(outcome.get("attempts", 1))

                if outcome.get("ok") and clip_abs.exists() and clip_abs.stat().st_size > 0:
                    try:
                        frame_extractor(clip_abs, frame_abs)
                    except Exception as e:
                        logger.error(f"frame extraction failed for {clip_id}: {e}")
                        self.project.update_clip(clip_id, status="failed", reason=f"frame_extract: {e}", finished_at=_now(), attempts=attempts)
                        on_event(RunnerEvent("clip_failed", clip_id, {"reason": str(e)}))
                        return
                    self.project.update_clip(
                        clip_id,
                        status="done",
                        clip=f"clips/clip_{int(clip_id):03d}.mp4",
                        frame=f"frames/frame_{int(clip_id):03d}.png",
                        finished_at=_now(),
                        attempts=attempts,
                    )
                    on_event(RunnerEvent("clip_done", clip_id, {}))
                    continue

                # Failure path — worker already exhausted retries.
                reason = outcome.get("reason", "unknown")
                logger.warning(f"[runner] clip={clip_id} failed: {reason}")
                self.project.update_clip(
                    clip_id,
                    status="failed",
                    reason=reason,
                    finished_at=_now(),
                    attempts=attempts,
                )
                on_event(RunnerEvent("clip_failed", clip_id, {"reason": reason}))
                return

        clip_files = [folder / f"clips/clip_{int(cid):03d}.mp4" for cid in clip_ids_in_order]
        final_path = folder / "final.mp4"
        try:
            concat(clip_files, final_path)
            self.project.update_final("done", path="final.mp4")
            on_event(RunnerEvent("final_done", None, {"path": "final.mp4"}))
        except Exception as e:
            logger.error(f"concat failed: {e}")
            self.project.update_final("failed")
            on_event(RunnerEvent("final_failed", None, {"reason": str(e)}))

    def _load_prompts(self) -> dict[str, str]:
        path = self.project.folder / self.project.inputs.prompts
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {f"{int(item['id']):03d}": _format_prompt(item["prompt"]) for item in raw}


def _send_task_with_optional_timeout(
    worker: Any,
    task: TaskJson,
    on_event: Callable[[RunnerEvent], None],
    clip_id: str,
    stop_check: Callable[[], bool],
    timeout_sec: float | None,
) -> dict[str, Any]:
    """Call worker.send_task. Workers that don't accept `timeout_sec` (e.g. test fakes)
    fall back to the no-timeout signature so tests stay decoupled from the watchdog."""
    on_marker = lambda m: _emit_marker(on_event, clip_id, m)  # noqa: E731
    try:
        return worker.send_task(
            task, on_marker=on_marker, stop_check=stop_check, timeout_sec=timeout_sec
        )
    except TypeError:
        return worker.send_task(task, on_marker=on_marker, stop_check=stop_check)


def _emit_marker(on_event, clip_id: str, item: tuple[str, dict] | str) -> None:
    if isinstance(item, tuple):
        kind, payload = item
        if kind == "TASK FAILED":
            logger.error(f"[worker] clip={clip_id} TASK FAILED: {payload.get('reason')}")
        on_event(RunnerEvent("worker_marker", clip_id, {"kind": kind, **payload}))
    else:
        on_event(RunnerEvent("worker_log", clip_id, {"line": item}))


def _format_prompt(p: Any) -> str:
    if isinstance(p, str):
        return p
    parts = [
        p.get("character", ""),
        f"Action: {p.get('action', '')}",
        f"Emotion: {p.get('emotion', '')}",
        f"Camera: {p.get('camera', '')}",
        f"Sound: {p.get('sound', '')}",
        f"Negative: {p.get('negative_prompt', '')}",
    ]
    return "\n\n".join(s for s in parts if s.strip().rstrip(":"))
