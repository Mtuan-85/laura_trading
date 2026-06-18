"""High-level adapters that implement engines.base Protocols.

GrokImageEngine and GrokVideoEngine wrap the FlowRunner so that callers (UI
workers) speak only in terms of `gen_image(prompt, settings, ref_image)` and
`gen_video(prompt, ref_image, settings)` — no flow/runner/page leakage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger as log
from patchright.async_api import Page

from engines.grok import claude_picker
from engines.grok.runner import FlowRunner

# Image candidate count by Grok preset (verified production behavior).
TARGET_COUNT_BY_QUALITY = {"quality": 4, "speed": 8}


class _GrokEngineBase:
    """Shared plumbing — both engines drive the same connected page."""

    def __init__(self, page: Page) -> None:
        if page is None:
            raise ValueError("Page chưa được set — gọi GrokConnection.select_tab() trước")
        self.page = page


class GrokImageEngine(_GrokEngineBase):
    """ImageEngine adapter for grok.com/imagine.

    settings dict:
        aspect: "16:9" | "9:16"           (required)
        quality: "speed" | "quality"      (default "quality")
        output_path: Path | str           (required — where the .jpg lands)
        pick_mode: "auto" | "claude"      (default "auto")
        topic: str                        (used when pick_mode == "claude")
        style: str                        (used when pick_mode == "claude")
        typing_speed: "fast"|"human"|"slow" (default "fast")
        wait_timeout_s: int               (default 60)
        debug_dir: Path | None            (candidates log root, optional)
        fast_mode: bool                   (default False — paste prompt thay human_type)
        stop_event: asyncio.Event | None  (default None — để fast_paste check stop trong 5s settle)
    """

    async def gen_image(
        self,
        prompt: str,
        settings: dict[str, Any],
        ref_image: Path | None = None,
    ) -> Path:
        output_path = settings.get("output_path")
        if not output_path:
            raise ValueError("settings.output_path bắt buộc cho gen_image")

        quality = settings.get("quality", "quality")
        config: dict[str, Any] = {
            "aspect": settings.get("aspect", "16:9"),
            "quality": quality,
            "target_count": TARGET_COUNT_BY_QUALITY.get(quality, 4),
            "typing_speed": settings.get("typing_speed", "fast"),
            "wait_timeout_s": settings.get("wait_timeout_s", 60),
            "output_path": Path(output_path),
            "debug_dir": settings.get("debug_dir"),
            "pick_mode": settings.get("pick_mode", "auto"),
            "pick_fn": claude_picker.pick_best,
            "topic": settings.get("topic", ""),
            "style": settings.get("style", ""),
            "fast_mode": bool(settings.get("fast_mode", False)),
            "stop_event": settings.get("stop_event"),
        }

        prompts = [{"id": "single", "text": prompt, "ref": ref_image}]
        flow_key = "image_to_image" if ref_image else "text_to_image"

        runner = FlowRunner(self.page, config, prompts)
        result = await runner.run(flow_key)

        if not result.get("ok"):
            raise RuntimeError(f"gen_image fail: {result.get('reason')}")
        downloaded = result["state"].get("downloaded") or []
        if not downloaded:
            errors = result["state"].get("errors") or []
            raise RuntimeError(
                f"gen_image fail: không có file download. errors={errors[:3]}"
            )
        return Path(downloaded[0])

    async def pick_best(
        self,
        candidates: list[Path],
        prompt: str,
        topic: str,
        style: str,
    ) -> int:
        return await claude_picker.pick_best(candidates, prompt, topic, style)


class GrokVideoEngine(_GrokEngineBase):
    """VideoEngine adapter (image-to-video flow).

    settings dict:
        aspect: "16:9" | "9:16"        (required)
        resolution: "480p" | "720p"    (default "720p")
        duration: "6s" | "10s"         (default "10s")
        output_path: Path | str        (required — where the .mp4 lands)
        typing_speed: "fast"|"human"|"slow" (default "fast")
        video_timeout_s: int           (default 600)
        video_initial_wait_s: int      (default 20)
        fast_mode: bool                (default False — paste prompt thay human_type)
        stop_event: asyncio.Event | None (default None — để fast_paste check stop trong 5s settle)
    """

    async def gen_video(
        self,
        prompt: str,
        ref_image: Path,
        settings: dict[str, Any],
    ) -> Path:
        if not ref_image:
            raise ValueError("Grok video flow yêu cầu ref_image (image-to-video)")
        ref = Path(ref_image)
        if not ref.exists():
            raise FileNotFoundError(f"ref_image không tồn tại: {ref}")

        output_path = settings.get("output_path")
        if not output_path:
            raise ValueError("settings.output_path bắt buộc cho gen_video")

        config: dict[str, Any] = {
            "aspect": settings.get("aspect", "16:9"),
            "resolution": settings.get("resolution", "720p"),
            "duration": settings.get("duration", "10s"),
            "typing_speed": settings.get("typing_speed", "fast"),
            "video_timeout_s": settings.get("video_timeout_s", 600),
            "video_initial_wait_s": settings.get("video_initial_wait_s", 20),
            "output_path": Path(output_path),
            "fast_mode": bool(settings.get("fast_mode", False)),
            "stop_event": settings.get("stop_event"),
        }

        prompts = [{"id": "single", "text": prompt, "ref": ref}]
        runner = FlowRunner(self.page, config, prompts)
        result = await runner.run("image_to_video")

        if not result.get("ok"):
            raise RuntimeError(f"gen_video fail: {result.get('reason')}")
        downloaded = result["state"].get("downloaded") or []
        if not downloaded:
            errors = result["state"].get("errors") or []
            raise RuntimeError(
                f"gen_video fail: không có file download. errors={errors[:3]}"
            )
        return Path(downloaded[0])
