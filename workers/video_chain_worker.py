"""Long-lived worker: holds one GrokConnection, processes tasks from stdin.

Protocol (line-delimited JSON over stdin/stdout):

  Parent -> Worker stdin:
      {"cmd": "task", "task": {...TaskJson...}}
      {"cmd": "shutdown"}

  Worker -> Parent stdout (text markers; see task_contract.print_marker):
      TASK START   {"task_id": "001", "prompt_chars": 412}
      EVENT        {"type": "cdp_connected", "url": "..."}
      TASK DONE    {"success": 1, "clip": "...", "attempts": 1}
      TASK FAILED  {"reason": "...", "attempts": 3}

Retries (kill+relaunch Brave between attempts) happen here, inside one process,
so subsequent attempts see a fresh page — not the same dirty tab the previous
attempt failed on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from workers._retry import run_with_retry
from workers.task_contract import (
    EXIT_CDP_UNREACHABLE,
    EXIT_PARSE_FAILED,
    EXIT_SUCCESS,
    TaskJson,
    print_marker,
)


def _read_stdin_line() -> str | None:
    """Blocking readline used via to_thread; returns None on EOF."""
    line = sys.stdin.readline()
    return line if line else None


async def _next_command() -> dict | None:
    line = await asyncio.to_thread(_read_stdin_line)
    if line is None:
        return None
    line = line.strip()
    if not line:
        return {}
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        logger.warning(f"bad stdin line: {e}")
        return {}


async def _process_task(conn, task: TaskJson, max_attempts: int) -> None:
    print_marker("TASK START", {
        "task_id": task.task_id,
        "kind": task.kind,
        "prompt_chars": len(task.prompt or task.image_edit_prompt or ""),
    })

    ref = Path(task.ref_path)
    if not ref.exists():
        print_marker("TASK FAILED", {"reason": f"ref image missing: {ref}", "attempts": 0})
        return

    from engines.grok.engine import GrokVideoEngine
    from engines.grok.image_ref_engine import GrokImageRefEngine

    engine = GrokVideoEngine(conn.page)
    image_engine = GrokImageRefEngine(conn.page)

    def _refresh_page() -> None:
        if conn.page is not None:
            engine.page = conn.page
            image_engine.page = conn.page

    output_path = Path(task.output_path)

    if task.kind == "image_edit":
        if not task.image_edit_prompt:
            print_marker("TASK FAILED", {
                "reason": "image_edit task missing image_edit_prompt",
                "attempts": 0,
            })
            return

        async def _factory():
            edit_result = await image_engine.gen_image_with_refs(
                scene_id=task.task_id,
                prompt=task.image_edit_prompt or "",
                ref_paths=[ref],
                output_path=output_path,
                aspect=task.aspect,
            )
            if not edit_result.get("ok"):
                raise RuntimeError(
                    f"image_edit failed: {edit_result.get('reason', 'unknown')}"
                )
            return output_path
    else:  # "video"
        async def _factory():
            settings = {
                "aspect": task.aspect,
                "duration": f"{task.duration}s",
                "output_path": str(output_path),
            }
            return await engine.gen_video(task.prompt, ref, settings)

    def _log(s: str) -> None:
        logger.info(s)
        print_marker("EVENT", {"type": "retry_log", "msg": s[:200]})

    outcome = await run_with_retry(
        task_id=task.task_id,
        gen_factory=_factory,
        connection=conn,
        refresh_page=_refresh_page,
        log_cb=_log,
        max_attempts=max_attempts,
    )

    if outcome["ok"]:
        if task.kind == "video":
            # Surface any warnings (e.g. resolution_downgrade) BEFORE TASK DONE
            # so the chain runner can act while still tied to this clip.
            for w in getattr(engine, "last_warnings", None) or []:
                print_marker("EVENT", w)
        print_marker("TASK DONE", {
            "success": 1,
            "kind": task.kind,
            "clip": str(outcome["result"]),
            "attempts": outcome["attempts"],
        })
    else:
        print_marker("TASK FAILED", {
            "reason": outcome.get("last_error", "unknown"),
            "kind": task.kind,
            "attempts": outcome["attempts"],
        })


async def _ensure_connection(conn, cdp_url: str) -> bool:
    """Connect + select first grok tab. Returns False on fail (marker emitted)."""
    if await conn.is_connected():
        return True
    try:
        await conn.connect(cdp_url)
    except Exception as ce:
        print_marker("TASK FAILED", {"reason": f"CDP unreachable: {ce}", "attempts": 0})
        return False

    tabs = await conn.list_tabs(grok_only=True)
    if not tabs:
        print_marker("TASK FAILED", {"reason": "no grok tab open", "attempts": 0})
        return False
    await conn.select_tab(int(tabs[0]["index"]))
    print_marker("EVENT", {"type": "cdp_connected", "url": cdp_url})
    return True


async def _run_loop(max_attempts: int) -> int:
    from engines.grok.browser import GrokConnection
    conn = GrokConnection()
    try:
        while True:
            cmd = await _next_command()
            if cmd is None:
                return EXIT_SUCCESS
            kind = cmd.get("cmd")
            if kind == "shutdown":
                return EXIT_SUCCESS
            if kind != "task":
                continue
            try:
                task = TaskJson.model_validate(cmd["task"])
            except (KeyError, ValidationError) as e:
                print_marker("TASK FAILED", {"reason": f"bad task payload: {e}", "attempts": 0})
                continue

            if not await _ensure_connection(conn, task.cdp_url):
                # CDP failure — next task may also fail, but let parent decide.
                continue

            try:
                await _process_task(conn, task, max_attempts)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("task processing crashed")
                reason = f"{type(e).__name__}: {e}"
                if len(reason) > 200:
                    reason = reason[:200] + "..."
                print_marker("TASK FAILED", {"reason": reason, "attempts": 0})
    finally:
        try:
            await conn.disconnect()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--task", type=Path,
        help="legacy one-shot mode: read a single TaskJson from this file and exit",
    )
    args = parser.parse_args(argv)

    if args.task is not None:
        return _legacy_single_task(args.task, args.max_attempts)
    return asyncio.run(_run_loop(args.max_attempts))


def _legacy_single_task(path: Path, max_attempts: int) -> int:
    """Back-compat: read one task file, run it, exit. Kept so we don't break
    anything that still calls `--task <file>`."""
    from workers.task_contract import EXIT_PREREQ_MISSING
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        task_shape = TaskJson.model_validate(raw)
    except (FileNotFoundError, json.JSONDecodeError, ValidationError) as e:
        sys.stderr.write(f"task parse failed: {e}\n")
        return EXIT_PARSE_FAILED

    if not Path(task_shape.ref_path).exists():
        print_marker("TASK FAILED", {"reason": f"ref image missing: {task_shape.ref_path}", "attempts": 0})
        return EXIT_PREREQ_MISSING

    async def _one_shot() -> int:
        from engines.grok.browser import GrokConnection
        conn = GrokConnection()
        try:
            task = TaskJson.model_validate(raw)
            if not await _ensure_connection(conn, task.cdp_url):
                return EXIT_CDP_UNREACHABLE
            try:
                await _process_task(conn, task, max_attempts)
            except Exception:
                logger.error(traceback.format_exc())
            return EXIT_SUCCESS
        finally:
            try:
                await conn.disconnect()
            except Exception:
                pass

    return asyncio.run(_one_shot())


if __name__ == "__main__":
    sys.exit(main())
