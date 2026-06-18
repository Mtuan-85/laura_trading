"""Worker-local Patchright/CDP helpers for Grok provider processes."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse
import re

from patchright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)


def kill_stale_cdp_clients(port: int) -> int:
    """Kill stale Patchright node.exe clients connected to the target CDP port."""
    if os.environ.get("STORY_VIDEO_KILL_STALE_CDP", "").strip() != "1":
        return 0
    killed = 0
    try:
        netstat = subprocess.run(
            ["netstat", "-ano"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return killed

    pids: set[str] = set()
    for line in netstat.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "ESTABLISHED":
            continue
        local_addr = parts[1]
        foreign_addr = parts[2]
        if not (
            _endpoint_matches_port(local_addr, port)
            or _endpoint_matches_port(foreign_addr, port)
        ):
            continue
        pids.add(parts[-1])

    for pid in pids:
        try:
            task = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            continue

        if "node.exe" not in task.stdout.lower():
            continue
        if not _looks_like_patchright_client(pid):
            continue

        try:
            result = subprocess.run(
                ["taskkill", "/F", "/PID", pid],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            continue
        if result.returncode == 0:
            killed += 1

    return killed


def _looks_like_patchright_client(pid: str) -> bool:
    try:
        proc = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                f"ProcessId={pid}",
                "get",
                "CommandLine",
                "/VALUE",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        command_line = ""
    else:
        command_line = proc.stdout.lower()
    if not command_line:
        command_line = _command_line_from_powershell(pid)
    return "patchright" in command_line or "playwright" in command_line


def _command_line_from_powershell(pid: str) -> str:
    command = (
        f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    return proc.stdout.lower()


def _endpoint_matches_port(endpoint: str, port: int) -> bool:
    if endpoint.startswith("["):
        match = re.match(r"^\[[^\]]+\]:(\d+)$", endpoint)
    else:
        match = re.match(r"^[^:]+:(\d+)$", endpoint)
    return bool(match and int(match.group(1)) == port)


def _port_from_url(url: str) -> int:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"CDP URL must include a valid port: {url}") from exc
    if port is None:
        raise ValueError(f"CDP URL must include a port: {url}")
    return port


@dataclass
class WorkerCdpSession:
    pw: Playwright
    browser: Browser
    context: BrowserContext
    page: Page

    async def close(self) -> None:
        try:
            try:
                await self.browser.close()
            except Exception:
                pass
        finally:
            await self.pw.stop()


async def connect_worker_cdp(cdp_url: str, base_url: str) -> WorkerCdpSession:
    port = _port_from_url(cdp_url)
    kill_stale_cdp_clients(port)

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=15_000)
        if not browser.contexts:
            raise RuntimeError("Browser has no contexts")
        context = browser.contexts[0]
        page = await get_or_open_tab(context, base_url)
        return WorkerCdpSession(pw=pw, browser=browser, context=context, page=page)
    except Exception:
        await pw.stop()
        raise


async def get_or_open_tab(context: BrowserContext, base_url: str) -> Page:
    for page in context.pages:
        if (page.url or "").startswith(base_url):
            await page.bring_to_front()
            return page

    for page in context.pages:
        if _is_blank_or_newtab(page.url or ""):
            await page.goto(base_url, wait_until="domcontentloaded")
            await page.bring_to_front()
            return page

    page = await context.new_page()
    await page.goto(base_url, wait_until="domcontentloaded")
    await page.bring_to_front()
    return page


def _is_blank_or_newtab(url: str) -> bool:
    return url in {"", "about:blank", "chrome://newtab/", "chrome://new-tab-page/"}
