"""Universal flow runner — executes a declarative flow against a Grok page."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from loguru import logger as log
from patchright.async_api import Page

from engines.grok import actions
from engines.grok.flows import FLOWS


class StopRequested(Exception):
    pass


class FlowRunner:
    """Execute one or many prompts through a flow definition.

    Config dict keys (all optional unless flow explicitly resolves them):
        aspect, quality, resolution, duration  — Grok UI settings
        target_count                            — image candidates to wait for
        wait_timeout_s                          — image gen timeout (sec)
        video_timeout_s                         — video gen timeout (sec)
        typing_speed                            — fast | human | slow
        output_path                             — where download_to writes
        debug_dir                               — candidates logging root (Path | None)
        image candidate flows always pick the first completed candidate.
    """

    def __init__(
        self,
        page: Page,
        config: dict[str, Any],
        prompts: list[dict[str, Any]],
        log_cb: Callable[[str], None] | None = None,
    ) -> None:
        self.page = page
        self.config = config
        self.prompts = prompts
        self.log_cb = log_cb
        self.state: dict[str, Any] = {
            "counter": 0,
            "current_prompt": None,
            "vars": {},
            "errors": [],
            "downloaded": [],
            "warnings": [],
        }
        self._stop = asyncio.Event()

    # Public control --------------------------------------------------------

    def request_stop(self) -> None:
        self._stop.set()

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise StopRequested()

    def _log(self, msg: str) -> None:
        log.info(msg)
        if self.log_cb:
            try:
                self.log_cb(msg)
            except Exception:
                pass

    # Param resolution ------------------------------------------------------

    def _resolved_value(self, step: dict[str, Any]) -> Any:
        if "from_prompt" in step:
            return (self.state.get("current_prompt") or {}).get(step["from_prompt"])
        if "from_config" in step:
            return self.config.get(step["from_config"])
        if "from_var" in step:
            return self._resolve_var(step["from_var"])
        return step.get("value")

    def _resolve_var(self, key: str) -> Any:
        if "." in key:
            parts = key.split(".")
            val: Any = self.state["vars"].get(parts[0])
            for p in parts[1:]:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    return None
            return val
        return self.state["vars"].get(key)

    # Step dispatch ---------------------------------------------------------

    async def _exec_step(self, step: dict[str, Any]) -> dict[str, Any]:
        action = step["action"]
        page = self.page

        if action == "ensure_at":
            return await actions.ensure_at(page, step["url"])

        if action == "set_mode":
            return await actions.set_mode(page, self._resolved_value(step))

        if action == "set_quality":
            return await actions.set_quality(page, self._resolved_value(step))

        if action == "set_aspect":
            return await actions.set_aspect(page, self._resolved_value(step))

        if action == "set_video_resolution":
            return await actions.set_video_resolution(page, self._resolved_value(step))

        if action == "verify_video_resolution":
            expected = self._resolved_value(step) or self.config.get("resolution", "720p")
            res = await actions.verify_video_resolution(page, expected)
            if res.get("ok") and res.get("downgrade"):
                self.state["warnings"].append(
                    {
                        "type": "resolution_downgrade",
                        "actual_p": res["actual_p"],
                        "expected_p": res["expected_p"],
                    }
                )
            return res

        if action == "set_video_duration":
            return await actions.set_video_duration(page, self._resolved_value(step))

        if action == "verify_input_empty":
            return await actions.verify_input_empty(page)

        if action == "fill_prompt":
            speed = self.config.get("typing_speed", "fast")
            fast_mode = bool(self.config.get("fast_mode", False))
            stop_event = self.config.get("stop_event")
            return await actions.fill_prompt(
                page, self._resolved_value(step),
                speed=speed, fast_mode=fast_mode, stop_event=stop_event,
                typed_prefix=self.config.get("prompt_typed_prefix"),
                paste_suffix=self.config.get("prompt_paste_suffix"),
            )

        if action == "click_submit":
            return await actions.click_submit(page)

        if action == "human_pause":
            await actions.human_pause(step.get("min_ms", 800), step.get("max_ms", 2500))
            return {"ok": True}

        if action == "submit_and_wait_ready":
            tc_key = step.get("target_count_from_config", "target_count")
            target_count = int(self.config.get(tc_key, 4))
            timeout_ms = int(self.config.get("wait_timeout_s", 60)) * 1000
            res = await actions.submit_and_wait_ready(
                page, target_count=target_count, timeout_ms=timeout_ms
            )
            if "save_to" in step and res.get("ok"):
                self.state["vars"][step["save_to"]] = {
                    "ready_count": res.get("ready_count"),
                    "masonry_index": res.get("masonry_index"),
                }
            return res

        if action == "save_candidates_log":
            debug_dir = self.config.get("debug_dir")
            if not debug_dir:
                return {"ok": True, "skipped": True}
            tc_key = step.get("target_count_from_config", "target_count")
            target_count = int(self.config.get(tc_key, 4))
            counter = self.state["counter"]
            cp = self.state.get("current_prompt") or {}
            ready = self.state["vars"].get("ready_result") or {}
            masonry_index = ready.get("masonry_index", 0) if isinstance(ready, dict) else 0
            res = await actions.save_candidates_log(
                page,
                debug_dir=Path(debug_dir),
                counter=counter,
                prompt_text=cp.get("text", ""),
                masonry_index=int(masonry_index),
                target_count=target_count,
            )
            self.state["vars"]["candidate_paths"] = res.get("paths") or []
            return res

        if action == "pick_image":
            idx = await self._pick_image()
            if "save_to" in step:
                self.state["vars"][step["save_to"]] = idx
            return {"ok": True, "value": idx}

        if action == "upload_ref_if_present":
            ref = self._resolved_value(step)
            if isinstance(ref, (list, tuple)):
                paths = [Path(p) for p in ref if p]
                return await actions.upload_ref_if_present(page, ref_paths=paths)
            return await actions.upload_ref_if_present(
                page, ref_path=Path(ref) if ref else None
            )

        if action == "wait_video_ready":
            initial = int(self.config.get("video_initial_wait_s", 20))
            timeout_ms = int(self.config.get("video_timeout_s", 600)) * 1000
            return await actions.wait_video_ready(
                page, initial_wait_s=initial, timeout_ms=timeout_ms
            )

        if action == "click_image":
            idx = self._resolved_value(step)
            masonry_index = None
            if "masonry_from_var" in step:
                masonry_index = self._resolve_var(step["masonry_from_var"])
            elif "masonry_index" in step:
                masonry_index = step["masonry_index"]
            return await actions.click_image(page, int(idx), masonry_index=masonry_index)

        if action == "wait_url_match":
            return await actions.wait_url_match(page, step["pattern"])

        if action == "click_back":
            return await actions.click_back(page)

        if action == "download_to":
            output_path = self._resolved_value(step) or self.config.get("output_path")
            if not output_path:
                return {"ok": False, "reason": "download_to: missing output_path"}
            res = await actions.download_to(page, Path(output_path))
            if res.get("ok"):
                self.state["downloaded"].append(res["path"])
            return res

        return {"ok": False, "reason": f"unknown action: {action}"}

    async def _pick_image(self) -> int:
        debug_dir = self.config.get("debug_dir")
        counter = self.state["counter"]
        choice = 0
        pick_data = {"choice": choice, "mode": "auto", "reason": "First image (auto)"}

        if debug_dir:
            await actions.write_pick_log(Path(debug_dir), counter, pick_data)
        return int(choice)

    # Run loops -------------------------------------------------------------

    async def run(self, flow_key: str) -> dict[str, Any]:
        flow = FLOWS.get(flow_key)
        if not flow:
            return {"ok": False, "reason": f"unknown flow: {flow_key}"}

        self._log(f"Bắt đầu flow: {flow['name']}")
        try:
            if flow.get("loop_per_prompt"):
                await self._run_per_prompt(flow)
            else:
                await self._run_steps(flow["steps"])
        except StopRequested:
            self._log("Đã dừng theo yêu cầu")
            return {"ok": False, "reason": "stopped", "state": self.state}

        self._log(
            f"Hoàn tất: {len(self.state['downloaded'])}/{len(self.prompts)} prompt OK"
        )
        return {"ok": True, "state": self.state}

    async def _run_per_prompt(self, flow: dict[str, Any]) -> None:
        total = len(self.prompts)
        for i, prompt in enumerate(self.prompts, start=1):
            self._check_stop()
            self.state["counter"] = i
            self.state["current_prompt"] = prompt
            self.state["vars"].clear()
            self._log(f"[{i}/{total}] Prompt: {prompt.get('text', '')[:80]}...")
            try:
                await self._run_steps(flow["steps"])
            except StopRequested:
                raise
            except Exception as e:
                self.state["errors"].append(
                    {"prompt_id": prompt.get("id"), "type": "exception", "msg": str(e)}
                )
                self._log(f"[{i}/{total}] ❌ exception: {e}")

            if i < total:
                try:
                    if "/post/" in (self.page.url or ""):
                        self._log("Recovery: quay lại /imagine...")
                        await self.page.goto(
                            "https://grok.com/imagine",
                            wait_until="domcontentloaded",
                            timeout=15000,
                        )
                        await asyncio.sleep(2)
                except Exception as recovery_err:
                    self._log(f"Recovery thất bại: {recovery_err}")

    async def _run_steps(self, steps: list[dict[str, Any]]) -> None:
        for step in steps:
            self._check_stop()
            res = await self._exec_step(step)
            if not res.get("ok") and not res.get("skipped"):
                reason = res.get("reason") or res.get("type") or "unknown"
                self._log(f"  ⚠ step '{step['action']}' fail: {reason}")
                self.state["errors"].append(
                    {
                        "prompt_id": (self.state.get("current_prompt") or {}).get("id"),
                        "step": step["action"],
                        "reason": reason,
                    }
                )
                return
