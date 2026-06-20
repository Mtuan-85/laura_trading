"""CDP browser connection — implements EngineConnection Protocol.

Wraps Patchright's connect_over_cdp. Lists/selects existing tabs (the user
keeps the browser open and logged in — we never spawn).
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import urllib.error
import urllib.request
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

from core.brave_launcher import BraveLauncher
from core.config import load_config, wait_brave_ready

GROK_HOST = "grok.com"


def _port_from_url(cdp_url: str) -> int:
    m = re.search(r":(\d+)", cdp_url)
    return int(m.group(1)) if m else 9222


def kill_stale_cdp_clients(port: int) -> int:
    """Kill node.exe processes with ESTABLISHED connection to 127.0.0.1:PORT.
    Does NOT touch brave.exe. Returns number of killed PIDs."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5
        )
    except Exception as e:
        log.warning(f"[cdp] netstat failed, skip stale-kill: {e}")
        return 0
    candidate_pids: set[int] = set()
    for line in result.stdout.splitlines():
        if f"127.0.0.1:{port}" in line and "ESTABLISHED" in line:
            m = re.search(r"(\d+)\s*$", line.strip())
            if m:
                candidate_pids.add(int(m.group(1)))
    killed = 0
    for pid in candidate_pids:
        try:
            ps = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            continue
        if "node.exe" in ps.stdout.lower():
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                check=False, capture_output=True,
            )
            killed += 1
            log.info(f"[cdp] killed stale node.exe pid={pid}")
    return killed


def is_brave_alive(port: int, timeout: float = 2.0) -> bool:
    """Quick HTTP probe to /json/version. Returns True if CDP responds."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=timeout
        ) as r:
            return r.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


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

    def __init__(self, launcher: BraveLauncher | None = None) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp_url: str | None = None
        self._tab_index: int | None = None
        self._launcher: BraveLauncher | None = launcher

    @property
    def page(self) -> Page | None:
        return self._page

    # EngineConnection Protocol --------------------------------------------------

    async def connect(self, cdp_url: str) -> None:
        if await self.is_connected():
            return

        port = _port_from_url(cdp_url)
        # Anti-hang: kill stale node.exe drivers holding port from prev runs
        kill_stale_cdp_clients(port)
        # Lazy-build a launcher from config if caller didn't inject one
        if self._launcher is None:
            cfg = load_config().get("brave", {})
            # If cfg's debug_port differs from URL's port, URL wins (caller intent).
            if int(cfg.get("debug_port", port)) != port:
                cfg = {**cfg, "debug_port": port}
            self._launcher = BraveLauncher.from_config(cfg)
        # Auto-launch Brave if not running (only kills what we launched on stop)
        if not is_brave_alive(port):
            log.info(f"[cdp] Brave not running on {port}, auto-launching...")
            await asyncio.to_thread(self._launcher.ensure_running, 30.0)
        else:
            # Still call ensure_running so the launcher discovers/adopts PIDs.
            await asyncio.to_thread(self._launcher.ensure_running, 30.0)

        self._pw = await async_playwright().start()
        try:
            # MUST have timeout — without it, stale drivers hang forever
            self._browser = await self._pw.chromium.connect_over_cdp(
                cdp_url, timeout=15_000
            )
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
        if self._launcher is not None and self._launcher.owned:
            log.info("Stopping owned Brave instance...")
            await asyncio.to_thread(self._launcher.stop)
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
        """Recovery: kill OUR Brave (only what we launched) + relaunch + reconnect.

        Uses the tracked PIDs from BraveLauncher, never PowerShell guesswork.
        If we're attached to a Brave we didn't launch (owned=False), we refuse
        to kill it — just attempt a fresh reconnect.

        `project_root` is accepted for API stability but ignored.
        """
        if self._launcher is None:
            # connect() always builds one; this is defensive.
            raise RuntimeError("kill_and_relaunch_brave: no launcher configured")

        debug_port = self._launcher.debug_port
        kill_stale_cdp_clients(debug_port)
        # Drop dead Patchright handles before we touch the browser.
        try:
            await self._cleanup()
        except Exception:
            pass

        if not self._launcher.owned:
            log.warning(
                "[BRAVE] not owned (user-managed Brave) — reconnecting without kill"
            )
            await asyncio.sleep(1)
            await self.reconnect_cdp()
            return

        log.warning(
            f"[BRAVE] surgical kill+relaunch (pids={self._launcher.pids}, "
            f"profile={self._launcher.user_data_dir})..."
        )
        await asyncio.to_thread(self._launcher.relaunch, 30.0)

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
