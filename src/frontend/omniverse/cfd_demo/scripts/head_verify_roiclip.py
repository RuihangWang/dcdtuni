#!/usr/bin/env python3
"""Verify ROI clip renders the clipped temperature volume (post colormapSource fix).

The user flagged "ROI clip doesn't show the flow field". Clipping calls the SAME
_do_rebuild_volume(roi_enabled=True) teardown+recreate path that produced the empty
colormapSource — so the colormapSource fix should make the clipped volume render too.
Single fresh-Kit pass (geometry -> Temp -> Show ROI Box -> shrink box -> Clip), with a
full re-voxelise wait after the clip. Same true-head launch recipe as head_verify.py.

Run:
  HEAD_URL=http://localhost:8081 HEAD_SHOT_DIR=/tmp/head_roiclip \
  DISPLAY=:99 $BROWSER_USE_PY \
      src/frontend/omniverse/cfd_demo/scripts/head_verify_roiclip.py
"""
import asyncio
import shutil
import pathlib
import os

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_roiclip")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)

SET_REACT = """
([sel, idx, v]) => {
    const el = document.querySelectorAll(sel)[idx];
    if (!el) return 'NO_EL ' + sel + ' #' + idx;
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
          .set.call(el, String(v));
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return 'OK #' + idx + '=' + el.value;
}
"""


def log(m):
    print(f"[roiclip] {m}", flush=True)


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

        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)
        await click_text(page, "Temp")
        log("waiting 78s for full voxelisation...")
        await page.wait_for_timeout(78000)
        await page.screenshot(path=f"{SHOT}/rc01_temp_full.png")
        log("shot rc01_temp_full")

        # Hide geometry to isolate the volume.
        await click_text(page, "Geometry")
        await page.wait_for_timeout(1500)
        hid = await page.eval_on_selector_all(
            ".tree input.node-vis",
            "els => { let k=0; els.forEach(b=>{ if(b.checked){ b.click(); k++; } }); return k; }")
        log(f"unchecked {hid} geometry rows")
        await page.wait_for_timeout(3000)
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)

        # Show the ROI box.
        await click_text(page, "Show ROI Box")
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/rc02_roi_box.png")
        log("shot rc02_roi_box")

        # Shrink the ROI box to a central sub-region: X[10,20] Y[0,5] Z[6,14].
        # .roi-box number inputs order: Wmin,Wmax,Hmin,Hmax,Dmin,Dmax = idx 0..5.
        for idx, val in ((0, 10), (1, 20), (4, 6), (5, 14)):
            r = await page.evaluate(SET_REACT, [".roi-box input[type=number]", idx, val])
            log(f"set ROI input {r}")
            await page.wait_for_timeout(400)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=f"{SHOT}/rc03_box_shrunk.png")
        log("shot rc03_box_shrunk")

        # Clip the volume to the ROI box -> re-voxelise within the box.
        await click_text(page, "Clip Volume to ROI")
        for t in (30, 55, 90):
            prev = 0 if t == 30 else (30 if t == 55 else 55)
            await page.wait_for_timeout((t - prev) * 1000)
            await page.screenshot(path=f"{SHOT}/rc04_clipped_{t}s.png")
            log(f"shot rc04_clipped_{t}s")

        await browser.close()
        log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
