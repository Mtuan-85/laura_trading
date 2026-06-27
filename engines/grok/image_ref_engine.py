"""Direct Grok image-edit flow for one or more reference images.

Reference-image edits produce one result on /imagine/post/{uuid}; they do not
use the masonry candidate picker used by text-to-image generation.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from loguru import logger as log
from patchright.async_api import Page

from engines.grok import actions as A
from engines.grok import selectors as SEL


class GrokImageRefEngine:
    def __init__(self, page: Page) -> None:
        self.page = page
        self._stop_event: asyncio.Event | None = None

    def set_stop_event(self, event: asyncio.Event | None) -> None:
        self._stop_event = event

    def _check_stop(self) -> None:
        if self._stop_event is not None and self._stop_event.is_set():
            raise asyncio.CancelledError("Stopped by user")

    async def gen_image_with_refs(
        self,
        *,
        scene_id: str,
        prompt: str,
        ref_paths: list[Path],
        output_path: Path,
        aspect: str,
        wait_timeout_s: int = 120,
        fast_mode: bool = False,
    ) -> dict[str, Any]:
        try:
            self._check_stop()
            result = await A.ensure_at(self.page, "/imagine")
            if not result.get("ok"):
                return {"ok": False, "reason": f"ensure_at: {result.get('reason')}"}

            self._check_stop()
            result = await A.set_mode(self.page, "image")
            if not result.get("ok"):
                return {"ok": False, "reason": f"set_mode: {result.get('reason')}"}

            self._check_stop()
            result = await A.upload_ref_if_present(self.page, ref_paths=ref_paths)
            if not result.get("ok"):
                return {"ok": False, "reason": f"upload_refs: {result.get('reason')}"}

            # Upload resets Grok's aspect to Original, so apply it afterwards.
            self._check_stop()
            await asyncio.sleep(0.5)
            result = await A.set_aspect(self.page, aspect)
            if not result.get("ok"):
                log.warning(f"[{scene_id}] set_aspect failed: {result.get('reason')}")

            self._check_stop()
            result = await A.fill_prompt(
                self.page,
                prompt,
                fast_mode=fast_mode,
                stop_event=self._stop_event,
            )
            if not result.get("ok"):
                return {"ok": False, "reason": f"fill_prompt: {result.get('reason')}"}

            self._check_stop()
            result = await A.click_submit(self.page)
            if not result.get("ok"):
                return {"ok": False, "reason": f"click_submit: {result.get('reason')}"}

            ready = await self._wait_image_ready(timeout_s=wait_timeout_s)
            if not ready:
                return {"ok": False, "reason": "timeout waiting image ready"}

            self._check_stop()
            result = await A.download_to(self.page, output_path)
            if not result.get("ok"):
                return {"ok": False, "reason": f"download: {result.get('reason')}"}

            self._check_stop()
            try:
                back = self.page.locator(SEL.BACK).first
                if await back.count() > 0:
                    await back.click()
                    await self.page.wait_for_url("**/imagine", timeout=10000)
            except Exception:
                await self.page.goto("https://grok.com/imagine", timeout=15000)

            return {"ok": True, "path": str(output_path)}
        except asyncio.CancelledError:
            return {"ok": False, "reason": "cancelled"}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    async def _wait_image_ready(
        self,
        *,
        initial_wait_s: int = 30,
        timeout_s: int = 120,
    ) -> bool:
        try:
            await self.page.wait_for_url("**/imagine/post/**", timeout=20000)
        except Exception as exc:
            log.warning(f"Image edit URL did not reach /post/: {exc}")

        for _ in range(initial_wait_s):
            self._check_stop()
            await asyncio.sleep(1)

        overlay_pattern = re.compile(r"Generating\s+\d+%")
        deadline = time.monotonic() + max(0, timeout_s - initial_wait_s)
        while time.monotonic() < deadline:
            self._check_stop()
            try:
                overlays = self.page.locator("div").filter(has_text=overlay_pattern)
                overlay_count = await overlays.count()
            except Exception:
                overlay_count = -1

            if overlay_count == 0:
                try:
                    download = self.page.locator(SEL.DOWNLOAD).first
                    if await download.count() > 0 and await download.is_visible():
                        await asyncio.sleep(1)
                        return True
                except Exception:
                    pass

            if await A.detect_error(self.page):
                return False
            await asyncio.sleep(2)
        return False
