#!/usr/bin/env python3
"""True HEAD-browser driver for verifying the Omniverse CFD web demo.

WHY THIS EXISTS
---------------
`browser-use` ignores its own `--headed` flag and always launches
`--headless=new --ozone-platform=headless --use-angle=vulkan` — i.e. a HEADLESS
Chromium that also grabs a GPU process. The user forbids headless and mandates a
single GPU for the whole demo. So we bypass browser-use and drive the SAME
bundled Chromium through Playwright with `headless=False` on the Xvfb `:99`
display and `--disable-gpu` (pure software — no GPU touched). This is a REAL
browser window (verifiable via `xwininfo`), it decodes the WebRTC H.264 stream in
software, and `page.screenshot()` captures exactly what the viewport shows — the
real stream, not OV's built-in capture.

Run:  DISPLAY=:99 <browser-use-env python> head_verify.py <step>
Steps are sequential phases; each screenshots to the scratchpad.
"""
import asyncio
import shutil
import pathlib
import os
import sys
import time

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_verify")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[head_verify] {m}", flush=True)


async def click_text(page, text, timeout=8000):
    """Click the first visible element whose text matches; report success."""
    try:
        loc = page.get_by_text(text, exact=False).first
        await loc.click(timeout=timeout)
        log(f"clicked '{text}'")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}: {str(e)[:80]}")
        return False


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME,
            headless=False,                      # *** TRUE HEAD ***
            args=[
                "--disable-gpu",                 # software only — no GPU
                "--use-gl=swiftshader",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await ctx.new_page()
        page.on("console", lambda msg: None)

        log(f"goto {URL}")
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001
            log(f"goto note: {type(e).__name__} (continuing — WebRTC keeps load open)")
        await page.wait_for_timeout(10000)       # let the WebRTC stream connect
        await page.screenshot(path=f"{SHOT}/h01_connected.png")
        log("shot h01_connected (proves head browser shows the live stream)")

        # --- Load the CFD geometry (the green button) ---
        await click_text(page, "Load CFD Geometry")
        log("waiting 170s for GB300 scene sync...")
        await page.wait_for_timeout(170000)
        await page.screenshot(path=f"{SHOT}/h02_geometry.png")
        log("shot h02_geometry")

        # --- Post-processing -> Temp volume ---
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(2000)
        await click_text(page, "Temp")
        log("waiting 75s for voxelisation (1.35M-cell .vtu is heavier)...")
        await page.wait_for_timeout(75000)
        await page.screenshot(path=f"{SHOT}/h03_temp_volume.png")
        log("shot h03_temp_volume")

        # --- Isolate the volume: hide all geometry checkboxes ---
        await click_text(page, "Geometry")
        await page.wait_for_timeout(1500)
        hid = await page.eval_on_selector_all(
            ".tree input.node-vis",
            "els => { let k=0; els.forEach(b=>{ if(b.checked){ b.click(); k++; } }); return k; }",
        )
        log(f"unchecked {hid} geometry rows")
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/h04_volume_isolated.png")
        log("shot h04_volume_isolated")

        # --- Off-vs-Temp proof (same camera, geometry hidden): Off must be EMPTY ---
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)
        await click_text(page, "Off")
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/h05_off_isolated.png")
        log("shot h05_off_isolated (volume OFF -> must be empty)")
        await click_text(page, "Temp")
        await page.wait_for_timeout(7000)
        await page.screenshot(path=f"{SHOT}/h06_temp_isolated.png")
        log("shot h06_temp_isolated (volume ON again)")

        await browser.close()
        log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
