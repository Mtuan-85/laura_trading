"""CDP browser connection — implements EngineConnection Protocol.

Wraps Patchright's connect_over_cdp. Lists/selects existing tabs (the user
keeps the browser open and logged in — we never spawn).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger as log
from patchright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from core.config import load_config, wait_brave_ready

GROK_HOST = "grok.com"


class GrokConnection:
    """Persistent CDP attach to an already-running Brave/Chrome instance.

    Lifecycle:
        conn = GrokConnection()
        await conn.connect("http://localhost:9222")
        tabs = await conn.list_tabs()
        await conn.select_tab(tabs[0]["index"])
        page = conn.page  # use this with the engines
        ...
        await conn.disconnect()
    """

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp_url: str | None = None
        self._tab_index: int | None = None

    @property
    def page(self) -> Page | None:
        return self._page

    # EngineConnection Protocol --------------------------------------------------

    async def connect(self, cdp_url: str) -> None:
        if await self.is_connected():
            return
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            await self._cleanup()
            raise RuntimeError(f"Không thể kết nối CDP {cdp_url}: {e}") from e

        contexts = self._browser.contexts
        if not contexts:
            await self._cleanup()
            raise RuntimeError("Browser không có context nào")
        self._context = contexts[0]
        self._cdp_url = cdp_url
        log.info(f"Đã kết nối CDP: {cdp_url}")

    async def disconnect(self) -> None:
        await self._cleanup()
        log.info("Đã ngắt kết nối CDP")

    async def is_connected(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    # Tab management (UI-driven) -------------------------------------------------

    async def list_tabs(self, grok_only: bool = True) -> list[dict[str, Any]]:
        if not self._context:
            return []
        tabs: list[dict[str, Any]] = []
        for idx, page in enumerate(self._context.pages):
            url = page.url or ""
            if grok_only and GROK_HOST not in url:
                continue
            try:
                title = await page.title()
            except Exception:
                title = "(untitled)"
            tabs.append({"index": idx, "title": title, "url": url})
        return tabs

    async def select_tab(self, index: int) -> dict[str, Any]:
        if not self._context:
            raise RuntimeError("Chưa kết nối")
        pages = self._context.pages
        if index < 0 or index >= len(pages):
            raise IndexError(f"Tab index {index} không hợp lệ (có {len(pages)} tab)")
        self._page = pages[index]
        self._tab_index = index
        try:
            await self._page.bring_to_front()
            title = await self._page.title()
        except Exception:
            title = "(untitled)"
        log.info(f"Đã chọn tab #{index}: {title}")
        return {"index": index, "title": title, "url": self._page.url}

    async def reconnect_cdp(self) -> None:
        """Re-attach CDP and re-select last tab.

        Used after kill+relaunch Brave: tab index is reset (only one tab —
        the one launch_brave.bat opened to grok.com/imagine).
        """
        url = self._cdp_url
        if not url:
            raise RuntimeError("reconnect_cdp: chưa từng connect")
        log.warning(f"Reconnect CDP {url}...")
        await self._cleanup()
        await self.connect(url)
        # After kill+relaunch the only Brave tab is whatever launch_brave.bat
        # opened (grok.com/imagine). Pick the first grok tab fresh.
        tabs = await self.list_tabs(grok_only=True)
        if not tabs:
            raise RuntimeError("reconnect_cdp: không có grok tab sau khi reconnect")
        await self.select_tab(int(tabs[0]["index"]))

    async def kill_and_relaunch_brave(self, project_root: Path | None = None) -> None:
        """Kill brave.exe + relaunch via launch_brave.bat + wait CDP + reconnect.

        Single recovery primitive for any disconnect / fail in workers.
        `project_root` is accepted for API stability but ignored — config is
        loaded from APP_ROOT (where launch_brave.bat actually ships).
        """
        from core.config import APP_ROOT
        cfg = load_config().get("brave", {})
        process_name = cfg.get("process_name", "brave.exe")
        debug_port = int(cfg.get("debug_port", 9222))
        bat_path = Path(cfg.get("launch_bat", "launch_brave.bat"))
        if not bat_path.is_absolute():
            bat_path = APP_ROOT / bat_path
        if not bat_path.exists():
            raise RuntimeError(f"launch_brave.bat không tồn tại: {bat_path}")

        log.warning(f"[BRAVE] Killing {process_name}...")
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", process_name],
                capture_output=True, timeout=10,
            )
        except Exception as e:
            log.warning(f"[BRAVE] taskkill ignored: {e}")
        # Drop the dead Patchright handles before launching the new Brave —
        # otherwise reconnect_cdp's _cleanup tries to talk to a dead websocket.
        try:
            await self._cleanup()
        except Exception:
            pass
        await asyncio.sleep(2)

        log.info(f"[BRAVE] Running {bat_path}")
        subprocess.Popen(
            [str(bat_path)], shell=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )

        log.info(f"[BRAVE] Waiting for CDP port {debug_port}...")
        ready = await wait_brave_ready(port=debug_port, timeout=30)
        if not ready:
            raise RuntimeError(f"Brave CDP port {debug_port} not ready after 30s")

        log.info("[BRAVE] CDP ready, reconnecting...")
        await self.reconnect_cdp()
        await asyncio.sleep(2)
        log.info("[BRAVE] OK")

    # ---------------------------------------------------------------------------

    async def _cleanup(self) -> None:
        try:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception as e:
                    log.debug(f"Browser close ignored: {e}")
            if self._pw is not None:
                await self._pw.stop()
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._pw = None
