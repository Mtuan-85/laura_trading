from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from core.project import ChainProject
from utils.frame_extractor import extract_last_frame
from utils.video_concat import concat_with_xfade
from workers.process_launcher import LaunchedWorker
from workers.task_contract import EXIT_SUCCESS, EXIT_USER_KILLED, TaskJson


@dataclass
class RunnerEvent:
    kind: str
    clip_id: str | None
    payload: dict[str, Any]


WorkerFactory = Callable[[TaskJson], AbstractContextManager]


def _default_worker_factory(task: TaskJson) -> AbstractContextManager:
    tmp = Path(tempfile.gettempdir()) / f"lisa_task_{task.task_id}.json"
    tmp.write_text(task.model_dump_json(), encoding="utf-8")
    return LaunchedWorker(module="workers.video_chain_worker", task_json_path=tmp)


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
    ) -> None:
        worker_factory = worker_factory or _default_worker_factory
        frame_extractor = frame_extractor or extract_last_frame
        concat = concat or concat_with_xfade

        prompts = self._load_prompts()
        retry_count = int(self.config.get("defaults", {}).get("retry_count", 2))

        folder = self.project.folder
        (folder / "clips").mkdir(exist_ok=True)
        (folder / "frames").mkdir(exist_ok=True)

        clip_ids_in_order = sorted(self.project.clips.keys())

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

            attempts_done = clip_state.attempts
            max_attempts = attempts_done + retry_count + 1
            succeeded = False
            exit_code = -1

            for attempt in range(attempts_done + 1, max_attempts + 1):
                self.project.update_clip(
                    clip_id,
                    status="running",
                    prompt=prompt_text,
                    ref=str(ref_rel),
                    started_at=_now(),
                    attempts=attempt,
                )
                on_event(RunnerEvent("clip_started", clip_id, {"attempt": attempt}))

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

                with worker_factory(task) as worker:
                    for marker in worker.iter_markers():
                        if isinstance(marker, tuple):
                            kind, payload = marker
                            on_event(RunnerEvent("worker_marker", clip_id, {"kind": kind, **payload}))
                        else:
                            on_event(RunnerEvent("worker_log", clip_id, {"line": marker}))
                    exit_code = worker.wait(timeout=self.config.get("defaults", {}).get("worker_timeout_sec", 600))

                if exit_code == EXIT_SUCCESS and clip_abs.exists() and clip_abs.stat().st_size > 0:
                    try:
                        frame_extractor(clip_abs, frame_abs)
                    except Exception as e:
                        logger.error(f"frame extraction failed for {clip_id}: {e}")
                        self.project.update_clip(clip_id, status="failed", reason=f"frame_extract: {e}", finished_at=_now())
                        on_event(RunnerEvent("clip_failed", clip_id, {"reason": str(e)}))
                        return
                    self.project.update_clip(
                        clip_id,
                        status="done",
                        clip=f"clips/clip_{int(clip_id):03d}.mp4",
                        frame=f"frames/frame_{int(clip_id):03d}.png",
                        finished_at=_now(),
                    )
                    on_event(RunnerEvent("clip_done", clip_id, {}))
                    succeeded = True
                    break

                logger.warning(f"clip {clip_id} attempt {attempt} exit_code={exit_code}")
                if exit_code == EXIT_USER_KILLED:
                    self.project.update_clip(clip_id, status="interrupted", finished_at=_now())
                    on_event(RunnerEvent("stopped", clip_id, {}))
                    return

            if not succeeded:
                self.project.update_clip(
                    clip_id,
                    status="failed",
                    reason=f"worker exit_code={exit_code} after retries",
                    finished_at=_now(),
                )
                on_event(RunnerEvent("clip_failed", clip_id, {"exit_code": exit_code}))
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
        return {f"{int(item['id']):03d}": item["prompt"] for item in raw}
