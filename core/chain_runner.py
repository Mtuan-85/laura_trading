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
from core.skin_check import SkinResult, check_skin, stitch_side_by_side
from utils.frame_extractor import extract_last_frame
from utils.video_concat import align_and_concat
from workers.process_launcher import LaunchedWorker
from workers.task_contract import TaskJson


@dataclass
class RunnerEvent:
    kind: str
    clip_id: str | None
    payload: dict[str, Any]


WorkerFactory = Callable[[], AbstractContextManager]


def _default_worker_factory_for(max_attempts: int) -> WorkerFactory:
    def factory() -> AbstractContextManager:
        return LaunchedWorker(max_attempts=max_attempts)
    return factory


def _now() -> str:
    return datetime.now().astimezone().isoformat()


SkinChecker = Callable[[Path], SkinResult]
SkinStitcher = Callable[[Path, Path, Path], None]
OnSkinLow = Callable[[str, SkinResult, Path], str]


class ChainRunner:
    def __init__(self, project: ChainProject, config: dict):
        self.project = project
        self.config = config
        self.accept_480p = False

    def run(
        self,
        *,
        worker_factory: WorkerFactory | None = None,
        frame_extractor: Callable[[Path, Path], None] | None = None,
        concat: Callable[[list[Path], Path], None] | None = None,
        stop_check: Callable[[], bool] = lambda: False,
        on_event: Callable[[RunnerEvent], None] = lambda e: None,
        on_worker_started: Callable[[Any], None] = lambda w: None,
        on_resolution_downgrade: Callable[[str, int], str] = lambda _cid, _ap: "abort",
        on_skin_low: OnSkinLow = lambda _cid, _r, _p: "abort",
        skin_checker: SkinChecker | None = None,
        skin_stitcher: SkinStitcher | None = None,
    ) -> None:
        defaults_cfg = self.config.get("defaults", {})
        retry_count = int(defaults_cfg.get("retry_count", 2))
        max_attempts = retry_count + 1
        timeout_raw = defaults_cfg.get("worker_timeout_sec", 900)
        try:
            worker_timeout = float(timeout_raw) if timeout_raw else None
        except (TypeError, ValueError):
            worker_timeout = 900.0
        worker_factory = worker_factory or _default_worker_factory_for(max_attempts)

        media_cfg = self.config.get("ffmpeg", {})
        if isinstance(media_cfg, dict):
            ffmpeg_path = str(media_cfg.get("path", "ffmpeg"))
            ffprobe_path = str(media_cfg.get("ffprobe_path", "ffprobe"))
        else:
            ffmpeg_path = str(media_cfg or "ffmpeg")
            ffprobe_path = "ffprobe"
        frame_extractor = frame_extractor or (
            lambda video, frame: extract_last_frame(video, frame, ffmpeg=ffmpeg_path)
        )
        ref_image_abs = self.project.folder / self.project.inputs.ref_image
        concat = concat or (
            lambda clips, output: align_and_concat(
                clips, ref_image_abs, output,
                ffmpeg=ffmpeg_path, ffprobe=ffprobe_path,
            )
        )

        skin_cfg = self.config.get("skin_check") or {}
        skin_enabled = bool(skin_cfg.get("enabled", True))
        skin_threshold = int(skin_cfg.get("threshold", 95))
        skin_max_retries = int(skin_cfg.get("max_retries", 2))
        skin_timeout_s = int(skin_cfg.get("timeout_s", 120))
        skin_checker = skin_checker or (
            lambda p: check_skin(p, threshold=skin_threshold, timeout_s=skin_timeout_s)
        )
        skin_stitcher = skin_stitcher or (
            lambda ref, refined, out: stitch_side_by_side(
                ref, refined, out, ffmpeg=ffmpeg_path
            )
        )

        logger.info("[runner] loading prompts")
        prompts = self._load_prompts()
        image_edit_prompt = self._load_image_edit_prompt()
        logger.info(f"[runner] loaded {len(prompts)} prompts, max_attempts={max_attempts}")

        folder = self.project.folder
        (folder / "clips").mkdir(exist_ok=True)
        (folder / "frames").mkdir(exist_ok=True)
        (folder / "refined").mkdir(exist_ok=True)
        (folder / "debug" / "skin").mkdir(parents=True, exist_ok=True)

        clip_ids_in_order = sorted(self.project.clips.keys())
        logger.info(f"[runner] starting chain over {len(clip_ids_in_order)} clips: {clip_ids_in_order}")

        cdp_url = self.config.get("cdp", {}).get("url", "http://127.0.0.1:9222")
        cdp_base_url = self.config.get("cdp", {}).get("base_url", "https://grok.com/imagine")

        with worker_factory() as worker:
            on_worker_started(worker)

            for idx, clip_id in enumerate(clip_ids_in_order):
                if stop_check():
                    on_event(RunnerEvent("stopped", clip_id, {}))
                    return

                clip_state = self.project.clips[clip_id]
                if clip_state.status == "done":
                    continue

                prompt_input = prompts.get(clip_id, {"prompt": ""})
                prompt_text = str(prompt_input.get("prompt", ""))
                ref_rel = "input/ref.png" if idx == 0 else f"frames/frame_{int(clip_ids_in_order[idx-1]):03d}.png"
                ref_abs = folder / ref_rel
                clip_abs = folder / f"clips/clip_{int(clip_id):03d}.mp4"
                frame_abs = folder / f"frames/frame_{int(clip_id):03d}.png"
                refined_rel = (
                    None
                    if idx == 0
                    else f"refined/refined_{int(clip_ids_in_order[idx-1]):03d}.jpg"
                )
                refined_abs = folder / refined_rel if refined_rel else None

                self.project.update_clip(
                    clip_id,
                    status="running",
                    prompt=prompt_text,
                    ref=str(ref_rel),
                    started_at=_now(),
                )
                on_event(RunnerEvent("clip_started", clip_id, {"attempt": 1}))

                # --- Phase 1: image_edit (only for clips with a refined target) ---
                attempts_total = 0
                if refined_abs is not None and image_edit_prompt:
                    edit_outcome = self._run_image_edit_with_skin_check(
                        worker=worker,
                        clip_id=clip_id,
                        ref_abs=ref_abs,
                        refined_abs=refined_abs,
                        image_edit_prompt=image_edit_prompt,
                        cdp_url=cdp_url,
                        cdp_base_url=cdp_base_url,
                        worker_timeout=worker_timeout,
                        stop_check=stop_check,
                        on_event=on_event,
                        skin_enabled=skin_enabled,
                        skin_threshold=skin_threshold,
                        skin_max_retries=skin_max_retries,
                        skin_checker=skin_checker,
                        skin_stitcher=skin_stitcher,
                        on_skin_low=on_skin_low,
                        debug_dir=folder / "debug" / "skin",
                    )
                    attempts_total += int(edit_outcome.get("attempts", 0))
                    if edit_outcome.get("reason") == "stopped":
                        self.project.update_clip(
                            clip_id, status="interrupted",
                            finished_at=_now(), attempts=attempts_total,
                        )
                        on_event(RunnerEvent("stopped", clip_id, {}))
                        return
                    if not edit_outcome.get("ok"):
                        reason = edit_outcome.get("reason", "image_edit_failed")
                        logger.warning(f"[runner] clip={clip_id} image_edit failed: {reason}")
                        self.project.update_clip(
                            clip_id, status="failed", reason=reason,
                            finished_at=_now(), attempts=attempts_total,
                        )
                        on_event(RunnerEvent("clip_failed", clip_id, {"reason": reason}))
                        return

                video_ref_abs = refined_abs if (refined_abs and refined_abs.exists()) else ref_abs

                # --- Phase 2: video gen ---
                video_task = TaskJson(
                    task_id=clip_id,
                    kind="video",
                    ref_path=str(video_ref_abs),
                    output_path=str(clip_abs),
                    cdp_url=cdp_url,
                    cdp_base_url=cdp_base_url,
                    aspect=self.project.inputs.aspect,
                    duration=self.project.inputs.duration,
                    prompt=prompt_text,
                    prompt_typed_prefix=prompt_input.get("typed_prefix"),
                    prompt_paste_suffix=prompt_input.get("paste_suffix"),
                )

                logger.info(f"[runner] sending video clip={clip_id} (timeout={worker_timeout}s)")
                clip_meta: dict[str, Any] = {"downgrade": False, "actual_p": None}
                outcome = _send_task_with_optional_timeout(
                    worker, video_task, on_event, clip_id, stop_check, worker_timeout,
                    clip_meta=clip_meta,
                )
                logger.info(f"[runner] clip={clip_id} video outcome={outcome}")

                if outcome.get("reason") == "stopped":
                    attempts_total += int(outcome.get("attempts", 0))
                    self.project.update_clip(
                        clip_id, status="interrupted",
                        finished_at=_now(), attempts=attempts_total,
                    )
                    on_event(RunnerEvent("stopped", clip_id, {}))
                    return

                attempts_total += int(outcome.get("attempts", 1))

                if outcome.get("ok") and clip_abs.exists() and clip_abs.stat().st_size > 0:
                    if clip_meta["downgrade"] and not self.accept_480p:
                        actual_p = int(clip_meta["actual_p"] or 0)
                        logger.warning(
                            f"[runner] clip={clip_id} downgraded to {actual_p}p; pausing for user"
                        )
                        decision = on_resolution_downgrade(clip_id, actual_p)
                        if decision == "accept":
                            self.accept_480p = True
                            logger.info(f"[runner] user accepted {actual_p}p — sticky for rest of chain")
                        else:
                            reason = f"user_aborted_after_{actual_p}p"
                            self.project.update_clip(
                                clip_id, status="failed", reason=reason,
                                finished_at=_now(), attempts=attempts_total,
                            )
                            on_event(RunnerEvent("clip_failed", clip_id, {"reason": reason}))
                            return

                    try:
                        frame_extractor(clip_abs, frame_abs)
                    except Exception as e:
                        logger.error(f"frame extraction failed for {clip_id}: {e}")
                        self.project.update_clip(
                            clip_id, status="failed",
                            reason=f"frame_extract: {e}",
                            finished_at=_now(), attempts=attempts_total,
                        )
                        on_event(RunnerEvent("clip_failed", clip_id, {"reason": str(e)}))
                        return
                    self.project.update_clip(
                        clip_id,
                        status="done",
                        clip=f"clips/clip_{int(clip_id):03d}.mp4",
                        frame=f"frames/frame_{int(clip_id):03d}.png",
                        refined_ref=refined_rel,
                        finished_at=_now(),
                        attempts=attempts_total,
                    )
                    on_event(RunnerEvent("clip_done", clip_id, {}))
                    continue

                reason = outcome.get("reason", "unknown")
                logger.warning(f"[runner] clip={clip_id} failed: {reason}")
                self.project.update_clip(
                    clip_id, status="failed", reason=reason,
                    finished_at=_now(), attempts=attempts_total,
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

    # ------------------------------------------------------------------
    # image_edit + skin_check loop
    # ------------------------------------------------------------------

    def _run_image_edit_with_skin_check(
        self,
        *,
        worker: Any,
        clip_id: str,
        ref_abs: Path,
        refined_abs: Path,
        image_edit_prompt: str,
        cdp_url: str,
        cdp_base_url: str,
        worker_timeout: float | None,
        stop_check: Callable[[], bool],
        on_event: Callable[[RunnerEvent], None],
        skin_enabled: bool,
        skin_threshold: int,
        skin_max_retries: int,
        skin_checker: SkinChecker,
        skin_stitcher: SkinStitcher,
        on_skin_low: OnSkinLow,
        debug_dir: Path,
    ) -> dict[str, Any]:
        """Run image_edit, then skin-check loop. Returns combined outcome."""
        attempts_total = 0
        # If a refined file already exists from a previous run, still run skin
        # check on it once (it might be the bad one that interrupted the prior
        # session). Skip generation; go straight to check.
        cached = refined_abs.exists() and refined_abs.stat().st_size > 0
        max_attempts_skin = skin_max_retries + 1

        for skin_attempt in range(1, max_attempts_skin + 1):
            if stop_check():
                return {"ok": False, "reason": "stopped", "attempts": attempts_total}

            if not cached:
                edit_task = TaskJson(
                    task_id=clip_id,
                    kind="image_edit",
                    ref_path=str(ref_abs),
                    output_path=str(refined_abs),
                    cdp_url=cdp_url,
                    cdp_base_url=cdp_base_url,
                    aspect=self.project.inputs.aspect,
                    image_edit_prompt=image_edit_prompt,
                )
                logger.info(
                    f"[runner] sending image_edit clip={clip_id} "
                    f"skin_attempt={skin_attempt}/{max_attempts_skin}"
                )
                outcome = _send_task_with_optional_timeout(
                    worker, edit_task, on_event, clip_id, stop_check, worker_timeout,
                )
                attempts_total += int(outcome.get("attempts", 1))
                if outcome.get("reason") == "stopped":
                    return {"ok": False, "reason": "stopped", "attempts": attempts_total}
                if not outcome.get("ok"):
                    return {
                        "ok": False,
                        "reason": f"image_edit: {outcome.get('reason', 'unknown')}",
                        "attempts": attempts_total,
                    }
                if not refined_abs.exists() or refined_abs.stat().st_size == 0:
                    return {
                        "ok": False,
                        "reason": "image_edit produced no file",
                        "attempts": attempts_total,
                    }
            cached = False  # subsequent loop iterations must re-generate

            if not skin_enabled:
                return {"ok": True, "attempts": attempts_total}

            stitched = debug_dir / f"clip_{int(clip_id):03d}_attempt_{skin_attempt}.jpg"
            try:
                skin_stitcher(ref_abs, refined_abs, stitched)
            except Exception as e:
                logger.warning(f"[runner] stitch failed: {e}")
                result = SkinResult(
                    skin=0, lighting=0, verdict="ask",
                    rationale=f"stitch failed: {e}",
                )
            else:
                result = skin_checker(stitched)

            on_event(RunnerEvent("skin_check", clip_id, {
                "attempt": skin_attempt,
                "skin": result.skin,
                "lighting": result.lighting,
                "verdict": result.verdict,
                "rationale": result.rationale,
                "stitched": str(stitched),
            }))
            logger.info(
                f"[runner] clip={clip_id} skin={result.skin} lighting={result.lighting} "
                f"verdict={result.verdict}"
            )

            if result.verdict == "pass":
                return {"ok": True, "attempts": attempts_total}

            # verdict == "ask": user decides
            verdict = on_skin_low(clip_id, result, stitched)
            logger.info(f"[runner] clip={clip_id} skin user decision={verdict}")
            if verdict == "accept":
                return {"ok": True, "attempts": attempts_total}
            if verdict == "abort":
                return {
                    "ok": False,
                    "reason": f"user_aborted_skin_{result.skin}",
                    "attempts": attempts_total,
                }
            # verdict == "retry": delete the bad refined file and loop.
            try:
                if refined_abs.exists():
                    refined_abs.unlink()
            except OSError as e:
                logger.warning(f"[runner] failed to unlink {refined_abs}: {e}")
            if skin_attempt >= max_attempts_skin:
                return {
                    "ok": False,
                    "reason": f"skin_check_retries_exhausted_{result.skin}",
                    "attempts": attempts_total,
                }

        return {"ok": False, "reason": "skin_check_loop_exit", "attempts": attempts_total}

    def _load_prompts(self) -> dict[str, dict[str, str | None]]:
        path = self.project.folder / self.project.inputs.prompts
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            f"{int(item['id']):03d}": _format_prompt_input(item["prompt"])
            for item in raw
        }

    def _load_image_edit_prompt(self) -> str | None:
        rel = self.project.inputs.image_edit
        if not rel:
            return None
        path = self.project.folder / rel
        raw = json.loads(path.read_text(encoding="utf-8"))
        try:
            return _compose_image_edit_prompt(raw)
        except ValueError as e:
            raise ValueError(f"{path}: {e}") from None


def _compose_image_edit_prompt(raw: object) -> str:
    """Combine `prompt` with optional `skin_tone_profile` flattened into prose.

    The bare prompt comes first; if `skin_tone_profile` is a dict, a
    natural-language English block describing the profile is appended so the
    full instruction reaches Grok's image-edit textbox.
    """
    if not isinstance(raw, dict):
        raise ValueError("image_edit JSON must be an object")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("must contain a non-empty 'prompt'")
    parts = [prompt.strip()]
    profile = raw.get("skin_tone_profile")
    if isinstance(profile, dict):
        flattened = _flatten_skin_tone_profile(profile)
        if flattened:
            parts.append(flattened)
    return "\n\n".join(parts)


def _flatten_skin_tone_profile(profile: dict) -> str:
    lines: list[str] = []
    description = profile.get("description")
    if isinstance(description, str) and description.strip():
        lines.append(f"Skin tone reference: {description.strip()}.")

    palette = profile.get("skin_palette")
    if isinstance(palette, dict) and palette:
        entries = ", ".join(
            f"{key.replace('_', ' ')} {value}" for key, value in palette.items()
        )
        lines.append(f"Skin palette (hex) — {entries}.")

    luminance = profile.get("skin_luminance_rgb_scale")
    if isinstance(luminance, dict) and luminance:
        entries = ", ".join(
            f"{key.removesuffix('_Y').replace('_', ' ')} {value}"
            for key, value in luminance.items()
        )
        lines.append(f"Skin luminance (Y on 0-255 scale) — {entries}.")

    lighting = profile.get("lighting")
    if isinstance(lighting, dict) and lighting:
        for key, value in lighting.items():
            if isinstance(value, str) and value.strip():
                label = key.replace("_", " ")
                lines.append(f"Lighting ({label}): {value.strip()}.")

    return "\n".join(lines)


def _send_task_with_optional_timeout(
    worker: Any,
    task: TaskJson,
    on_event: Callable[[RunnerEvent], None],
    clip_id: str,
    stop_check: Callable[[], bool],
    timeout_sec: float | None,
    *,
    clip_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def on_marker(item) -> None:
        if clip_meta is not None and isinstance(item, tuple):
            kind, payload = item
            if kind == "EVENT" and payload.get("type") == "resolution_downgrade":
                clip_meta["downgrade"] = True
                clip_meta["actual_p"] = payload.get("actual_p")
        _emit_marker(on_event, clip_id, item)

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
    return _format_prompt_input(p)["prompt"] or ""


def _format_prompt_input(p: Any) -> dict[str, str | None]:
    if isinstance(p, str):
        return {"prompt": p, "typed_prefix": None, "paste_suffix": None}

    def labelled(label: str, key: str) -> str | None:
        value = p.get(key, "")
        if not isinstance(value, str) or not value.strip():
            return None
        return f"{label}: {value.strip()}"

    character = p.get("character", "")
    typed_prefix = character.strip() if isinstance(character, str) and character.strip() else None
    rest = [
        labelled("Action", "action"),
        labelled("Emotion", "emotion"),
        labelled("Camera", "camera"),
        labelled("Sound", "sound"),
        labelled("Negative", "negative_prompt"),
    ]
    paste_parts = [part for part in rest if part]
    cleaned = ([typed_prefix] if typed_prefix else []) + paste_parts
    return {
        "prompt": "\n\n".join(cleaned),
        "typed_prefix": typed_prefix,
        "paste_suffix": "\n\n".join(paste_parts) if paste_parts else None,
    }
