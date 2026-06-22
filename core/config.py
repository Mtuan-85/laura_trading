"""App-level config loader.

Reads `config.yaml` at APP_ROOT. Returns DEFAULT_CONFIG on missing/parse error
so the app never hard-crashes on a malformed config — failures are logged and
defaults fill in the gaps section-by-section.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml
from loguru import logger as log

# core/ → repo root (one level up).
APP_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG: dict[str, Any] = {
    "cdp": {
        "url": "http://127.0.0.1:9222",
        "profile_marker": "brave-grok-profile",
        "base_url": "https://grok.com/imagine",
    },
    "brave": {
        "debug_port": 9222,
        "exe": r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        "user_data_dir": r"D:\CDP_Browser\brave-grok-profile",
        "start_url": "https://grok.com/imagine",
        "profile_marker": "brave-grok-profile",
        "launch_bat": "launch_brave.bat",
        "process_name": "brave.exe",
    },
    "ffmpeg": {"path": "ffmpeg", "ffprobe_path": "ffprobe"},
    "defaults": {
        "aspect": "9:16",
        "duration": 10,
        "retry_count": 2,
        "worker_timeout_sec": 900,
    },
    "skin_check": {
        "enabled": True,
        "threshold": 95,
        "max_retries": 2,
        "timeout_s": 120,
    },
    "logging": {"level": "INFO", "file_rotation_mb": 10},
}


def _merge(defaults: dict, loaded: dict) -> dict:
    """Shallow-merge per top-level section: loaded keys win, missing keys fall back to defaults."""
    out: dict = {}
    for key in set(defaults) | set(loaded):
        d_val = defaults.get(key)
        l_val = loaded.get(key)
        if isinstance(d_val, dict) and isinstance(l_val, dict):
            merged = dict(d_val)
            merged.update(l_val)
            out[key] = merged
        elif l_val is not None:
            out[key] = l_val
        else:
            out[key] = d_val
    return out


def load_config(app_root: Path | None = None) -> dict[str, Any]:
    """Load config.yaml from APP_ROOT. Returns DEFAULT_CONFIG (merged) on missing/parse error."""
    root = Path(app_root) if app_root is not None else APP_ROOT
    p = root / "config.yaml"
    if not p.exists():
        log.warning(f"[config] {p} not found — using defaults")
        return dict(DEFAULT_CONFIG)
    try:
        loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        log.error(f"[config] parse error in {p}: {e} — using defaults")
        return dict(DEFAULT_CONFIG)
    if not isinstance(loaded, dict):
        log.error(f"[config] {p} did not parse to a dict — using defaults")
        return dict(DEFAULT_CONFIG)
    return _merge(DEFAULT_CONFIG, loaded)


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
