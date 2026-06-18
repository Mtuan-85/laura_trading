from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from workers.task_contract import (
    EXIT_CDP_UNREACHABLE,
    EXIT_FLOW_FAILED,
    EXIT_PARSE_FAILED,
    EXIT_PREREQ_MISSING,
    EXIT_SUCCESS,
    TaskJson,
    print_marker,
)


def _parse_task(path: Path) -> TaskJson:
    if not path.exists():
        raise FileNotFoundError(str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    return TaskJson.model_validate(raw)


async def _run_async(task: TaskJson) -> int:
    ref = Path(task.ref_path)
    if not ref.exists():
        print_marker("TASK FAILED", {"reason": f"ref image missing: {ref}"})
        return EXIT_PREREQ_MISSING

    print_marker("TASK START", {"task_id": task.task_id, "prompt": task.prompt})

    from engines.grok.browser import GrokConnection
    from engines.grok.engine import GrokVideoEngine

    conn = GrokConnection()
    try:
        try:
            await conn.connect(task.cdp_url)
        except Exception as ce:
            print_marker("TASK FAILED", {"reason": f"CDP unreachable: {ce}"})
            return EXIT_CDP_UNREACHABLE

        tabs = await conn.list_tabs(grok_only=True)
        if not tabs:
            print_marker("TASK FAILED", {"reason": "no grok tab open"})
            return EXIT_CDP_UNREACHABLE
        await conn.select_tab(int(tabs[0]["index"]))
        print_marker("EVENT", {"type": "cdp_connected", "url": task.cdp_url})

        engine = GrokVideoEngine(conn.page)
        settings = {
            "aspect": task.aspect,
            "duration": f"{task.duration}s",
            "output_path": task.output_path,
        }
        downloaded = await engine.gen_video(task.prompt, ref, settings)
        print_marker("EVENT", {"type": "download_done", "path": str(downloaded)})
        print_marker("TASK DONE", {"success": 1, "clip": str(downloaded)})
        return EXIT_SUCCESS
    except Exception as e:
        logger.exception("video_chain_worker failed")
        print_marker("TASK FAILED", {"reason": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
        return EXIT_FLOW_FAILED
    finally:
        try:
            await conn.disconnect()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        task = _parse_task(args.task)
    except (FileNotFoundError, json.JSONDecodeError, ValidationError) as e:
        sys.stderr.write(f"task parse failed: {e}\n")
        return EXIT_PARSE_FAILED

    return asyncio.run(_run_async(task))


if __name__ == "__main__":
    sys.exit(main())
