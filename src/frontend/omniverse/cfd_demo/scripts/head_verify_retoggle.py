#!/usr/bin/env python3
"""Prove the Off->Temp RE-TOGGLE re-renders the temperature volume (colormapSource fix).

The full head_verify.py waits only 7s after the final Temp click (sized for a cheap
show/hide). With the colormapSource fix the re-toggle now does a FULL ~75s re-voxelise
(teardown + recreate), so 7s catches a still-rebuilding empty frame. This driver waits
the full voxelise window on the re-toggle and shoots intermediate frames so the volume
is captured AFTER it finishes. Same true-head launch recipe as head_verify.py.

Run:
  HEAD_URL=http://localhost:8081 HEAD_SHOT_DIR=/tmp/head_retoggle \
  DISPLAY=:99 $BROWSER_USE_PY \
      src/frontend/omniverse/cfd_demo/scripts/head_verify_retoggle.py
"""
import asyncio
import shutil
import pathlib
import os

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_retoggle")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[retoggle] {m}", flush=True)


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}: {str(e)[:80]}")
        return False


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

        await click_text(page, "Load CFD Geometry")
        log("waiting 175s for scene sync...")
        await page.wait_for_timeout(175000)
        await page.screenshot(path=f"{SHOT}/rt01_geometry.png")

        # First Temp selection (baseline — known to work).
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)
        await click_text(page, "Temp")
        log("waiting 78s for first voxelisation...")
        await page.wait_for_timeout(78000)
        await page.screenshot(path=f"{SHOT}/rt02_temp_first.png")
        log("shot rt02_temp_first")

        # Hide all geometry so only the volume is in frame.
        await click_text(page, "Geometry")
        await page.wait_for_timeout(1500)
        hid = await page.eval_on_selector_all(
            ".tree input.node-vis",
            "els => { let k=0; els.forEach(b=>{ if(b.checked){ b.click(); k++; } }); return k; }",
        )
        log(f"unchecked {hid} geometry rows")
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/rt03_isolated.png")

        # Off.
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)
        await click_text(page, "Off")
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/rt04_off.png")
        log("shot rt04_off")

        # *** THE RE-TOGGLE *** — Temp again, wait the FULL re-voxelise window.
        await click_text(page, "Temp")
        for t in (30, 55, 85):
            await page.wait_for_timeout((t - (0 if t == 30 else (30 if t == 55 else 55))) * 1000)
            await page.screenshot(path=f"{SHOT}/rt05_retoggle_{t}s.png")
            log(f"shot rt05_retoggle_{t}s")

        await browser.close()
        log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
