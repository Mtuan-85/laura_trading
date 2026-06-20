"""Retry-with-kill-and-relaunch wrapper for the worker.

Ported from story_video_making_v2. Each attempt runs gen_factory(); on any
exception we kill+relaunch Brave (so the next attempt sees a clean page) and
retry. CancelledError propagates — never relaunches on user stop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger as log


GenFactory = Callable[[], Awaitable[Any]]
RefreshPage = Callable[[], None]
LogCb = Callable[[str], None]


async def run_with_retry(
    *,
    task_id: str,
    gen_factory: GenFactory,
    connection,
    project_root: Path | None = None,
    refresh_page: RefreshPage = lambda: None,
    log_cb: LogCb = lambda s: None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Run gen_factory up to max_attempts; kill+relaunch Brave between fails.

    Returns:
        {"ok": True,  "result": <gen result>, "attempts": int}
        {"ok": False, "last_error": str,      "attempts": int}
    """
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        log_cb(f"[ATTEMPT {attempt}/{max_attempts}] task={task_id}")
        try:
            result = await gen_factory()
            return {"ok": True, "result": result, "attempts": attempt}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_err = e
            log_cb(f"[FAIL {attempt}] task={task_id}: {e}")

        if attempt < max_attempts:
            try:
                log_cb("[BRAVE] kill+relaunch...")
                await connection.kill_and_relaunch_brave(project_root=project_root)
                refresh_page()
            except asyncio.CancelledError:
                raise
            except Exception as relaunch_err:
                log_cb(f"[RELAUNCH FAIL] {relaunch_err}")

    log.error(f"[MAX_RETRY] task={task_id} failed after {max_attempts} attempts (last={last_err})")
    return {
        "ok": False,
        "last_error": str(last_err) if last_err else "unknown",
        "attempts": max_attempts,
    }
