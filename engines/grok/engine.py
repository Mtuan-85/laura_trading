"""High-level adapters that implement engines.base Protocols.

GrokVideoEngine wraps the FlowRunner so callers speak only in terms of
`gen_video(prompt, ref_image, settings)` — no flow/runner/page leakage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger as log
from patchright.async_api import Page

from engines.grok.runner import FlowRunner


class _GrokEngineBase:
    """Shared plumbing — both engines drive the same connected page."""

    def __init__(self, page: Page) -> None:
        if page is None:
            raise ValueError("Page chưa được set — gọi GrokConnection.select_tab() trước")
        self.page = page
        self.last_warnings: list[dict[str, Any]] = []


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
            "prompt_typed_prefix": settings.get("prompt_typed_prefix"),
            "prompt_paste_suffix": settings.get("prompt_paste_suffix"),
        }

        prompts = [{"id": "single", "text": prompt, "ref": ref}]
        runner = FlowRunner(self.page, config, prompts)
        result = await runner.run("image_to_video")
        self.last_warnings = list(result.get("state", {}).get("warnings") or [])

        if not result.get("ok"):
            raise RuntimeError(f"gen_video fail: {result.get('reason')}")
        downloaded = result["state"].get("downloaded") or []
        if not downloaded:
            errors = result["state"].get("errors") or []
            raise RuntimeError(
                f"gen_video fail: không có file download. errors={errors[:3]}"
            )
        return Path(downloaded[0])
