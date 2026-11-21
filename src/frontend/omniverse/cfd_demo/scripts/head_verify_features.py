#!/usr/bin/env python3
"""HEAD-browser verification of the opacity slider + ROI box inputs + 0.6m grid.

Same true-head approach as head_verify.py (Playwright headless=False on Xvfb :99,
--disable-gpu software decode — browser-use --headed is broken and grabs a GPU).
This driver OPERATES the new controls (drag opacity, type ROI bounds, clip) and
screenshots each state so the result is visually verifiable, per the user's
"operate then screenshot" requirement.
"""
import asyncio
import shutil
import pathlib
import os

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_verify_feat")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[head_feat] {m}", flush=True)


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}: {str(e)[:80]}")
        return False


# Set a React-controlled <input> (range or number) and fire input+change so
# React's onChange runs. Plain .value= bypasses React's synthetic event system.
SET_REACT = """([sel, idx, v]) => {
    const els = document.querySelectorAll(sel);
    const el = els[idx];
    if (!el) return 'NO_EL ' + sel + ' #' + idx + ' (have ' + els.length + ')';
    const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, String(v));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return 'OK ' + el.type + '=' + el.value;
}"""


async def set_react(page, selector, idx, value):
    r = await page.evaluate(SET_REACT, [selector, idx, value])
    log(f"set {selector}#{idx}={value} -> {r}")


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME, headless=False,
            args=["--disable-gpu", "--use-gl=swiftshader", "--no-sandbox",
                  "--disable-dev-shm-usage", "--window-size=1920,1080",
                  "--autoplay-policy=no-user-gesture-required"],
        )
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await ctx.new_page()
        log(f"goto {URL}")
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001
            log(f"goto note: {type(e).__name__}")
        await page.wait_for_timeout(10000)
        await page.screenshot(path=f"{SHOT}/hf01_connected.png")

        # --- Load geometry (also reveals the 0.6m grid) ---
        await click_text(page, "Load CFD Geometry")
        log("waiting 170s for scene sync...")
        await page.wait_for_timeout(170000)
        await page.screenshot(path=f"{SHOT}/hf02_geometry_grid.png")
        log("shot hf02_geometry_grid (grid should read on the floor now)")

        # --- OPACITY: select Server racks, drag opacity to 20%, screenshot ---
        await click_text(page, "Server racks")
        await page.wait_for_timeout(1500)
        n_range = await page.eval_on_selector_all(
            "input[type='range']", "els => els.length")
        log(f"visible range inputs on Geometry tab: {n_range}")
        await set_react(page, "input[type='range']", 0, 0.2)   # opacity slider
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/hf03_opacity_20.png")
        log("shot hf03_opacity_20 (racks should be translucent)")
        await set_react(page, "input[type='range']", 0, 1.0)   # restore
        await page.wait_for_timeout(2500)
        await page.screenshot(path=f"{SHOT}/hf04_opacity_100.png")

        # --- isolate the volume: hide all geometry ---
        hid = await page.eval_on_selector_all(
            ".tree input.node-vis",
            "els => { let k=0; els.forEach(b=>{ if(b.checked){ b.click(); k++; } }); return k; }")
        log(f"unchecked {hid} geometry rows")

        # --- VOLUME on (Temp), full domain ---
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)
        await click_text(page, "Temp")
        log("waiting 75s for voxelisation...")
        await page.wait_for_timeout(75000)
        await page.screenshot(path=f"{SHOT}/hf05_volume_full.png")
        log("shot hf05_volume_full (full-domain volume)")

        # --- ROI INPUTS: shrink the box to a central sub-region, show it ---
        n_num = await page.eval_on_selector_all(
            ".roi-box input[type='number']", "els => els.length")
        log(f"ROI number inputs: {n_num} (expect 6)")
        await click_text(page, "Show ROI Box")
        await page.wait_for_timeout(2000)
        # order: [Wmin,Wmax, Hmin,Hmax, Dmin,Dmax]; shrink W and D, keep H full
        for idx, val in [(0, 8), (1, 22), (4, 5), (5, 14)]:
            await set_react(page, ".roi-box input[type='number']", idx, val)
            await page.wait_for_timeout(400)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=f"{SHOT}/hf06_roi_box.png")
        log("shot hf06_roi_box (box shrunk to centre sub-region)")

        # --- CLIP to the ROI: volume must shrink to the box ---
        # The clip triggers a FULL volume teardown + re-voxelisation (the full-domain
        # voxelisation alone took ~75 s), so a 10 s wait screenshots it mid-rebuild
        # (empty) — the likely cause of the user's "clip shows no flow field". Wait
        # well past the rebuild and take intermediate shots to see it reappear clipped.
        await click_text(page, "Clip Volume to ROI")
        for secs in (30, 60, 95):
            log(f"waiting for clip re-voxelisation... (t={secs}s)")
            await page.wait_for_timeout(30000 if secs == 30 else 30000 if secs == 60 else 35000)
            await page.screenshot(path=f"{SHOT}/hf07_roi_clip_{secs}s.png")
            log(f"shot hf07_roi_clip_{secs}s")

        await browser.close()
        log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
