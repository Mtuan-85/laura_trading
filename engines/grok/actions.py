"""Atomic actions on grok.com/imagine page.

Each action is async, takes a Patchright Page (+ params), returns
{"ok": bool, ...}. Errors are returned, not raised — runner decides
whether to retry or skip. See MASTER_grok_automation.md §9 for the
full action catalog.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger as log
from patchright.async_api import Page

from engines.grok import selectors as SEL


# --- Human-like helpers ------------------------------------------------------

_TYPING_DELAYS = {
    "fast": (15, 60),
    "human": (40, 110),
    "slow": (80, 200),
}

# Minimal human-mimic pauses (~+15% over base typing time). Cheap signals
# only: per-char variance + short punctuation break + rare micro-hiccup.
# Word/thinking/shift-key pauses intentionally omitted (too costly).
_PUNC_PAUSE_RANGE = (0.08, 0.15)
_MICRO_HICCUP_PROB = 0.03
_MICRO_HICCUP_RANGE = (0.04, 0.10)
_POST_TYPING_RANGE = (0.10, 0.20)


async def human_pause(min_ms: int = 800, max_ms: int = 2500) -> None:
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def human_type(page: Page, selector: str, text: str, speed: str = "fast") -> None:
    lo_ms, hi_ms = _TYPING_DELAYS.get(speed, _TYPING_DELAYS["fast"])
    # Click once to focus, clear any leftover content, then type via keyboard.
    # page.type re-resolves + re-checks actionability on every keystroke and
    # times out on TipTap; keyboard.type writes to the focused element directly.
    await page.locator(selector).first.click()
    await asyncio.sleep(random.uniform(0.3, 0.6))
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    await asyncio.sleep(0.1)
    # Plain Enter submits the Grok form; split on \n and use Shift+Enter
    # for soft line breaks within the prompt.
    lines = text.split("\n")
    for line_idx, line in enumerate(lines):
        for char in line:
            # Per-keystroke delay sampled fresh each char → kills the
            # constant-cadence signal that fixed-delay typing exposes.
            await page.keyboard.type(char)
            await asyncio.sleep(random.uniform(lo_ms, hi_ms) / 1000.0)
            if char in ".,!?;:":
                await asyncio.sleep(random.uniform(*_PUNC_PAUSE_RANGE))
            if random.random() < _MICRO_HICCUP_PROB:
                await asyncio.sleep(random.uniform(*_MICRO_HICCUP_RANGE))
        if line_idx < len(lines) - 1:
            await page.keyboard.press("Shift+Enter")
    await asyncio.sleep(random.uniform(*_POST_TYPING_RANGE))


async def human_click(locator) -> None:
    try:
        await locator.scroll_into_view_if_needed()
    except Exception:
        pass
    await asyncio.sleep(random.uniform(0.2, 0.5))
    try:
        await locator.hover()
        await asyncio.sleep(random.uniform(0.1, 0.3))
    except Exception:
        pass
    await locator.click()


# --- Navigation --------------------------------------------------------------


async def ensure_at(page: Page, url_fragment: str) -> dict[str, Any]:
    """Navigate to /imagine if not there. Robust recovery from /post/{uuid}."""
    target = url_fragment if url_fragment.startswith("/") else f"/{url_fragment}"
    current = page.url or ""

    if target in current:
        log.debug(f"Already at {target}")
    else:
        log.info(f"Navigating from {current} to {target}")
        navigated = False
        if "/post/" in current:
            try:
                back = page.locator(SEL.BACK)
                if await back.count() > 0:
                    await back.first.click(timeout=5000)
                    await page.wait_for_url(
                        re.compile(r".*/imagine(?!/post)"), timeout=10000
                    )
                    navigated = True
            except Exception as e:
                log.warning(f"Back click failed: {e}")
        if not navigated:
            try:
                await page.goto(
                    f"https://grok.com{target}",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception as e:
                return {"ok": False, "reason": f"goto failed: {e}"}

    try:
        await page.locator(SEL.MODE_GROUP).wait_for(state="visible", timeout=15000)
        await asyncio.sleep(0.5)
    except Exception as e:
        return {"ok": False, "reason": f"page not ready at {target}, url={page.url}: {e}"}

    return {"ok": True}


async def wait_url_match(page: Page, pattern: str, timeout: int = 30000) -> dict[str, Any]:
    try:
        await page.wait_for_url(re.compile(pattern), timeout=timeout)
        return {"ok": True, "url": page.url}
    except Exception as e:
        return {"ok": False, "reason": f"wait_url_match: {e}"}


# --- Input -------------------------------------------------------------------


async def verify_input_empty(page: Page, timeout: int = 5000) -> dict[str, Any]:
    try:
        await page.wait_for_selector(SEL.PROMPT_INPUT_EMPTY, timeout=timeout)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": f"input not empty: {e}"}


async def fill_prompt(
    page: Page,
    text: str,
    speed: str = "fast",
    fast_mode: bool = False,
    stop_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    try:
        if fast_mode:
            await _fast_paste_prompt(page, text, stop_event=stop_event)
        else:
            await human_type(page, SEL.PROMPT_INPUT, text, speed=speed)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": f"fill_prompt: {e}"}


async def _fast_paste_prompt(
    page: Page,
    text: str,
    stop_event: asyncio.Event | None = None,
) -> None:
    await page.locator(SEL.PROMPT_INPUT).first.click()
    await asyncio.sleep(0.3)
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    await asyncio.sleep(0.1)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            await page.keyboard.insert_text(line)
        if i < len(lines) - 1:
            await page.keyboard.press("Shift+Enter")
    for _ in range(5):
        if stop_event is not None and stop_event.is_set():
            raise asyncio.CancelledError("stop requested during fast_paste settle")
        await asyncio.sleep(1)


async def click_submit(page: Page) -> dict[str, Any]:
    try:
        btn = page.locator(SEL.SUBMIT).first
        await human_click(btn)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": f"click_submit: {e}"}


# --- Mode / settings ---------------------------------------------------------


async def set_mode(page: Page, value: str) -> dict[str, Any]:
    """Click Image or Video radio. Anchors via Generation mode container —
    direct aria-label radios only work on /post/{uuid}, not /imagine main page.
    """
    target_text = value.strip().capitalize()
    try:
        group = page.locator(SEL.MODE_GROUP)
        await group.wait_for(state="visible", timeout=10000)
        btn = group.locator(f'button:has-text("{target_text}")').first
        await btn.click()
        await asyncio.sleep(0.5)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": f"set_mode: {e}"}


async def set_quality(page: Page, value: str) -> dict[str, Any]:
    """Click Speed or Quality preset by text-filter on role=radio buttons."""
    target = value.strip().lower()
    try:
        btns = page.locator(SEL.QUALITY_RADIO)
        n = await btns.count()
        for i in range(n):
            r = btns.nth(i)
            text = (await r.text_content() or "").strip()
            if text.lower() == target:
                if (await r.get_attribute("aria-checked")) == "true":
                    return {"ok": True, "already": True}
                await r.click()
                await asyncio.sleep(0.5)
                return {"ok": True}
        return {"ok": False, "reason": f"quality '{value}' not found"}
    except Exception as e:
        return {"ok": False, "reason": f"set_quality: {e}"}


async def set_aspect(page: Page, value: str) -> dict[str, Any]:
    try:
        await page.locator(SEL.ASPECT_TRIGGER).first.click()
        await asyncio.sleep(0.4)
        options = page.locator(SEL.ASPECT_OPTION)
        n = await options.count()
        for i in range(n):
            text = (await options.nth(i).text_content() or "").strip()
            if text.startswith(value):
                await options.nth(i).click()
                await asyncio.sleep(0.3)
                return {"ok": True}
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return {"ok": False, "reason": f"aspect '{value}' not found"}
    except Exception as e:
        return {"ok": False, "reason": f"set_aspect: {e}"}


async def _click_radio_in_group(page: Page, group_selector: str, label: str) -> dict[str, Any]:
    try:
        group = page.locator(group_selector)
        await group.wait_for(state="visible", timeout=10000)
        radios = group.locator('button[role="radio"]')
        n = await radios.count()
        for i in range(n):
            r = radios.nth(i)
            text = (await r.text_content() or "").strip().lower()
            if text == label.lower():
                if (await r.get_attribute("aria-checked")) == "true":
                    return {"ok": True, "already": True}
                await r.click()
                await asyncio.sleep(0.3)
                return {"ok": True}
        return {"ok": False, "reason": f"'{label}' not found in {group_selector}"}
    except Exception as e:
        return {"ok": False, "reason": f"radio click: {e}"}


async def set_video_resolution(page: Page, value: str) -> dict[str, Any]:
    return await _click_radio_in_group(page, SEL.VIDEO_RES_GROUP, value)


async def set_video_duration(page: Page, value: str) -> dict[str, Any]:
    return await _click_radio_in_group(page, SEL.VIDEO_DUR_GROUP, value)


# --- Image generation polling ------------------------------------------------


async def submit_and_wait_ready(
    page: Page,
    target_count: int = 4,
    timeout_ms: int = 60000,
) -> dict[str, Any]:
    """Submit prompt + wait for `target_count` ready listitems in NEW masonry.

    Ready = listitem has no <canvas> child. Tracks masonry by index
    (initial count + .nth) to avoid `.last` race when sections accumulate.
    """
    try:
        initial_sections = await page.locator(SEL.MASONRY_PREFIX).count()
    except Exception as e:
        return {"ok": False, "reason": f"count masonry: {e}"}

    try:
        await page.locator(SEL.SUBMIT).first.click()
    except Exception as e:
        return {"ok": False, "reason": f"submit click: {e}"}

    log.info(f"Submitted. Đợi masonry #{initial_sections}...")
    start = time.time() * 1000
    last_ready = 0

    while (time.time() * 1000 - start) < timeout_ms:
        try:
            current = await page.locator(SEL.MASONRY_PREFIX).count()
        except Exception:
            await asyncio.sleep(0.5)
            continue

        if current <= initial_sections:
            err = await detect_error(page)
            if err:
                return {"ok": False, "ready_count": 0, **err}
            await asyncio.sleep(0.5)
            continue

        new_masonry = page.locator(SEL.MASONRY_PREFIX).nth(initial_sections)
        listitems = new_masonry.locator(SEL.LIST_ITEM)

        try:
            total = await listitems.count()
        except Exception:
            await asyncio.sleep(0.5)
            continue

        ready = 0
        for i in range(total):
            try:
                if await listitems.nth(i).locator("canvas").count() == 0:
                    ready += 1
            except Exception:
                pass

        if ready != last_ready:
            log.debug(f"Masonry #{initial_sections}: {ready}/{total} ready (no canvas)")
            last_ready = ready

        if ready >= target_count:
            log.info(f"Masonry #{initial_sections} ready: {ready}/{target_count}")
            return {
                "ok": True,
                "ready_count": ready,
                "masonry_index": initial_sections,
            }

        err = await detect_error(page)
        if err:
            return {"ok": False, "ready_count": ready, **err}

        await asyncio.sleep(1)

    return {"ok": False, "reason": "timeout", "ready_count": last_ready}


async def click_image(
    page: Page,
    idx: int,
    masonry_index: int | None = None,
) -> dict[str, Any]:
    """Click the Nth ready (no-canvas) listitem in target masonry."""
    try:
        if masonry_index is not None:
            masonry = page.locator(SEL.MASONRY_PREFIX).nth(masonry_index)
        else:
            masonry = page.locator(SEL.MASONRY_PREFIX).last

        listitems = masonry.locator(SEL.LIST_ITEM)
        total = await listitems.count()

        ready_indices: list[int] = []
        for i in range(total):
            if await listitems.nth(i).locator("canvas").count() == 0:
                ready_indices.append(i)

        if not ready_indices:
            # Canvas overlay can come back during high-res rendering after
            # initial "ready" snapshot. Fallback: pick any img.opacity-1 in
            # the masonry directly and click its listitem ancestor.
            images = masonry.locator("img.opacity-1")
            img_count = await images.count()
            if img_count == 0:
                return {"ok": False, "reason": "no ready listitem and no img.opacity-1 found"}
            fallback_idx = min(max(idx, 0), img_count - 1)
            parent_li = images.nth(fallback_idx).locator(
                'xpath=ancestor::*[@role="listitem"]'
            ).first
            await parent_li.click(timeout=5000)
            log.warning(
                f"click_image fallback: clicked via img.opacity-1 ancestor (idx={fallback_idx})"
            )
            return {"ok": True, "fallback": True}

        if idx < 0 or idx >= len(ready_indices):
            log.warning(f"click_image idx {idx} OOR ({len(ready_indices)} ready), fallback 0")
            idx = 0

        real_idx = ready_indices[idx]
        target_img = listitems.nth(real_idx).locator("img.opacity-1").first
        await target_img.click()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": f"click_image: {e}"}


async def click_back(page: Page) -> dict[str, Any]:
    try:
        await page.locator(SEL.BACK).first.click()
        await page.wait_for_url(re.compile(r".*/imagine(?!/post)"), timeout=10000)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "reason": f"click_back: {e}"}


# --- Candidates debug log + pick --------------------------------------------


def _candidates_dir(debug_dir: Path, counter: int) -> Path:
    d = Path(debug_dir) / f"{counter:04d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def save_candidates_log(
    page: Page,
    debug_dir: Path,
    counter: int,
    prompt_text: str,
    masonry_index: int,
    target_count: int,
    pick_mode: str,
) -> dict[str, Any]:
    """Save debug log: strip.png + 0.png..N.png + prompt.txt + meta.json."""
    cdir = _candidates_dir(debug_dir, counter)

    try:
        await page.screenshot(path=str(cdir / "strip.png"))
    except Exception as e:
        log.warning(f"strip screenshot failed: {e}")

    masonry = page.locator(SEL.MASONRY_PREFIX).nth(masonry_index)
    listitems = masonry.locator(SEL.LIST_ITEM)
    try:
        total = await listitems.count()
    except Exception:
        total = 0

    saved_paths: list[Path] = []
    for i in range(min(total, target_count)):
        try:
            if await listitems.nth(i).locator("canvas").count():
                continue
            img = listitems.nth(i).locator("img.opacity-1").first
            img_bytes = await img.screenshot()
            p = cdir / f"{i}.png"
            p.write_bytes(img_bytes)
            saved_paths.append(p)
        except Exception as e:
            log.warning(f"candidate {i} screenshot failed: {e}")

    try:
        (cdir / "prompt.txt").write_text(prompt_text, encoding="utf-8")
    except Exception as e:
        log.warning(f"prompt.txt write failed: {e}")

    meta = {
        "counter": counter,
        "prompt": prompt_text,
        "target_count": target_count,
        "masonry_index": masonry_index,
        "saved_candidates": len(saved_paths),
        "pick_mode": pick_mode,
        "timestamp": datetime.now().isoformat(),
    }
    (cdir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"Đã lưu {len(saved_paths)} candidates vào {cdir}")
    return {"ok": True, "saved": len(saved_paths), "dir": str(cdir), "paths": saved_paths}


async def write_pick_log(debug_dir: Path, counter: int, pick_data: dict[str, Any]) -> None:
    try:
        cdir = _candidates_dir(debug_dir, counter)
        pick_data = {**pick_data, "timestamp": datetime.now().isoformat()}
        (cdir / "pick.json").write_text(
            json.dumps(pick_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        log.warning(f"pick.json write failed: {e}")


# --- Upload ------------------------------------------------------------------


async def _wait_upload_preview_ready(page: Page, timeout_ms: int = 60000) -> bool:
    start = time.time()
    while (time.time() - start) * 1000 < 5000:
        try:
            popup_visible = await page.locator(SEL.UPLOAD_POPUP_BTN).count()
        except Exception:
            popup_visible = 0
        if popup_visible == 0:
            break
        await asyncio.sleep(0.5)

    selectors = [
        ".query-bar img",
        '[class*="query-bar"] img',
        'form img:not([alt="Generated image"])',
        '.query-bar [class*="preview"]',
    ]
    while (time.time() - start) * 1000 < timeout_ms:
        for sel in selectors:
            try:
                if await page.locator(sel).count() > 0:
                    log.info(f"Upload preview detected: {sel}")
                    await asyncio.sleep(1.0)
                    return True
            except Exception:
                pass
        await asyncio.sleep(1)
    return False


async def upload_ref_if_present(
    page: Page,
    ref_path: Path | None = None,
    ref_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Upload reference image(s). Single (legacy) or multi (image-flow refs).

    ref_paths takes precedence when provided. Capped at 5 (Grok limit).
    """
    paths: list[Path] = []
    if ref_paths:
        paths = [Path(p) for p in ref_paths if p]
    elif ref_path:
        paths = [Path(ref_path)]

    if not paths:
        return {"ok": True, "skipped": True}

    if len(paths) > 5:
        log.warning(f"Truncating {len(paths)} refs to max 5")
        paths = paths[:5]

    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        return {"ok": False, "reason": f"ref file(s) không tồn tại: {missing}"}

    try:
        log.info(f"Đang upload {len(paths)} ref(s): {[p.name for p in paths]}")
        upload_btn = page.locator(SEL.UPLOAD).first
        await upload_btn.wait_for(state="visible", timeout=10000)
        await upload_btn.click()
        await asyncio.sleep(0.5)

        if await page.locator(SEL.FILE_INPUT).count() == 0:
            return {"ok": False, "reason": "không tìm thấy <input type='file'> sau khi click Upload"}

        file_input = page.locator(SEL.FILE_INPUT).first
        await file_input.set_input_files([str(p) for p in paths])

        log.info(f"Đợi preview ref hiện ra ({len(paths)} files)...")
        timeout = 30000 + (len(paths) - 1) * 5000
        if not await _wait_upload_preview_ready(page, timeout_ms=timeout):
            fallback_s = 30 + (len(paths) - 1) * 5
            log.warning(f"Không detect preview, fallback sleep {fallback_s}s")
            await asyncio.sleep(fallback_s)

        log.info(f"Đã upload {len(paths)} ref(s): {[p.name for p in paths]}")
        return {"ok": True, "paths": [str(p) for p in paths]}
    except Exception as e:
        return {"ok": False, "reason": f"upload_ref: {e}"}


# --- Video polling -----------------------------------------------------------


async def wait_video_ready(
    page: Page,
    initial_wait_s: int = 30,
    timeout_ms: int = 600000,
) -> dict[str, Any]:
    """Wait for video gen: fixed initial sleep + poll until done.

    Grok needs ~10-15s after submit before "Generating X%" overlay renders;
    polling immediately gives false positives (no overlay yet → looks "ready").
    Confirm with positive #sd-video signal too.
    """
    pattern = re.compile(r"Generating\s+\d+%")
    pct_pattern = re.compile(r"(\d+)%")

    log.info(f"Đợi {initial_wait_s}s cho overlay xuất hiện...")
    await asyncio.sleep(initial_wait_s)

    log.info("Polling video completion...")
    start = time.time()
    last_pct = -1

    while (time.time() - start) * 1000 < timeout_ms:
        try:
            overlays = page.locator("div").filter(has_text=pattern)
            count = await overlays.count()
        except Exception:
            count = -1

        if count == 0:
            try:
                video_count = await page.locator(SEL.VIDEO_ELEMENT).count()
            except Exception:
                video_count = 0
            if video_count > 0:
                log.info("Video sẵn sàng (#sd-video xuất hiện)")
                await asyncio.sleep(1.0)
                return {"ok": True}

        if count > 0:
            try:
                text = await overlays.first.text_content()
                m = pct_pattern.search(text or "")
                if m:
                    pct = int(m.group(1))
                    if pct != last_pct:
                        log.info(f"Video tiến độ: {pct}%")
                        last_pct = pct
            except Exception:
                pass

        err = await detect_error(page)
        if err:
            return {"ok": False, "last_progress": last_pct, **err}

        await asyncio.sleep(2)

    return {"ok": False, "reason": "gen_timeout", "last_progress": last_pct}


async def wait_image_ready(
    page: Page,
    initial_wait_s: int = 30,
    timeout_ms: int = 120000,
) -> dict[str, Any]:
    """Wait for image-with-refs gen: fixed sleep + poll overlay gone + download visible.

    Mirrors ``wait_video_ready``. The fixed sleep matters because the ref-upload
    preview makes the Download button visible from T=0 — polling immediately
    would download the ref, not the generated image. The overlay-gone +
    download-visible double-check is the "image truly ready" signal.
    """
    overlay_pattern = re.compile(r"Generating\s+\d+%")

    log.info(f"wait_image_ready: fixed sleep {initial_wait_s}s before polling...")
    await asyncio.sleep(initial_wait_s)

    log.info("Polling for image gen completion...")
    start = time.time()
    poll_budget_ms = max(0, timeout_ms - initial_wait_s * 1000)

    while (time.time() - start) * 1000 < poll_budget_ms:
        try:
            overlays = page.locator("div").filter(has_text=overlay_pattern)
            overlay_count = await overlays.count()
        except Exception:
            overlay_count = -1

        if overlay_count == 0:
            try:
                btn = page.locator(SEL.DOWNLOAD).first
                if await btn.count() > 0 and await btn.is_visible():
                    log.info("Image ready (overlay gone, download button visible)")
                    await asyncio.sleep(1.0)
                    return {"ok": True}
            except Exception:
                pass

        err = await detect_error(page)
        if err:
            return {"ok": False, **err}

        await asyncio.sleep(2)

    return {"ok": False, "reason": "gen_timeout"}


# --- Download ----------------------------------------------------------------


async def download_to(page: Page, output_path: Path, timeout_ms: int = 60000) -> dict[str, Any]:
    """Click Download, save to exact `output_path`. Retries once on transient
    timeout (Brave CDP can drop the download event sporadically)."""
    log.info(f">>> Đang download → {output_path}")
    last_err: str | None = None
    for attempt in (1, 2):
        try:
            btn = page.locator(SEL.DOWNLOAD)
            if await btn.count() == 0:
                return {"ok": False, "reason": f"download button not found at {page.url}"}

            async with page.expect_download(timeout=timeout_ms) as dl_info:
                await btn.first.click()
            dl = await dl_info.value
            suggested = dl.suggested_filename or "out.bin"
            log.debug(f"Server suggested filename: {suggested}")

            target = Path(output_path)
            if not target.suffix and "." in suggested:
                target = target.with_suffix("." + suggested.rsplit(".", 1)[-1])

            target.parent.mkdir(parents=True, exist_ok=True)
            await dl.save_as(str(target))

            if not target.exists():
                return {"ok": False, "reason": "file_not_saved"}

            size_kb = target.stat().st_size / 1024
            log.info(f"<<< Đã lưu: {target} ({size_kb:.1f} KB)")
            return {"ok": True, "path": str(target)}
        except Exception as e:
            last_err = str(e)
            if attempt == 1:
                log.warning(f"download attempt 1 fail: {e} — retry...")
                await asyncio.sleep(2)
                continue
            return {"ok": False, "reason": f"download: {e}"}
    return {"ok": False, "reason": f"download: {last_err}"}


# --- Error detection ---------------------------------------------------------


async def detect_error(page: Page) -> dict[str, Any] | None:
    try:
        toasts = page.locator(SEL.TOAST)
        n = await toasts.count()
        for i in range(n):
            text = (await toasts.nth(i).text_content() or "").lower()
            if any(k in text for k in ("rate limit", "too many", "quota", "slow down")):
                return {"type": "rate_limit", "msg": text, "reason": "rate_limit"}
            if any(k in text for k in ("violat", "policy", "inappropriate", "not allowed", "blocked")):
                return {"type": "policy_fail", "msg": text, "reason": "policy_fail"}
    except Exception:
        pass
    return None


# --- Debug screenshot --------------------------------------------------------


async def safe_screenshot(page: Page, target: Path) -> None:
    """Best-effort screenshot for failure diagnostics. Never raises."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(target))
        log.debug(f"Screenshot lỗi: {target}")
    except Exception as e:
        log.debug(f"safe_screenshot failed: {e}")
