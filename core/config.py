"""App-level config loader.

`config.json` at the **app root** (where main.py + launch_brave.bat live)
holds Brave launch params for the kill-and-relaunch retry path. This is NOT
per-project — it's per-install, since launch_brave.bat ships with the app.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

# core/ → repo root (one level up).
APP_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict[str, Any] = {
    "brave": {
        "launch_bat": "launch_brave.bat",
        "process_name": "brave.exe",
        "debug_port": 9222,
    }
}


def load_config(app_root: Path | None = None) -> dict[str, Any]:
    """Load config.json from APP_ROOT. Returns DEFAULT_CONFIG on missing/parse error."""
    root = Path(app_root) if app_root is not None else APP_ROOT
    p = root / "config.json"
    if not p.exists():
        return DEFAULT_CONFIG
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_CONFIG


async def wait_brave_ready(port: int = 9222, timeout: int = 30) -> bool:
    """Poll http://localhost:{port}/json/version until 200 or timeout.

    NOT a disconnect-diagnosis — just a startup gate after we (re)launch Brave.
    Uses raw asyncio sockets to avoid pulling in httpx as a hard dep.
    """
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=2.0
            )
            req = (
                f"GET /json/version HTTP/1.1\r\n"
                f"Host: localhost:{port}\r\n"
                f"Connection: close\r\n\r\n"
            )
            writer.write(req.encode("ascii"))
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            if b"200" in line:
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False
