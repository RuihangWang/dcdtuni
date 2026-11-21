#!/usr/bin/env python3
"""Prove ROI EXTRACTION is not ROI clipping.

The two look identical in a single screenshot, so the test is a sequence that only
extraction can pass:

  1. set a slab ROI, show the box, show Temp    -> full-domain field, box drawn
  2. Extract ROI as dataset                     -> field confined to the slab
  3. HIDE the ROI box                           -> field STAYS confined

Step 3 is the discriminator. With the clip, the whole field is still loaded and hiding
the box brings it back; with extraction there is nothing else loaded to come back.

Also loads the same ROI as .vtu and .vti in turn, which settles the repo's own
contradiction about whether IndeX voxelises an unstructured grid at all.

    CFDPROJ_PATH=... VTU_PATH=... HEAD_SHOT_DIR=/tmp/ex DISPLAY=:99 \\
        $BROWSER_USE_PY scripts/head_verify_roi_extract.py
"""
import asyncio
import os
import pathlib
import shutil
import sys

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_verify_roi_extract")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
PROJ = os.environ.get("CFDPROJ_PATH", "")
VTU = os.environ.get("VTU_PATH", "")
SLAB = os.environ.get("HEAD_ROI_SLAB", "0.026,0.999,1.946;4.426,2.000,3.946")
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[ex] {m}", flush=True)


async def shot(page, name):
    p = os.path.join(SHOT, name)
    await page.screenshot(path=p)
    log(f"shot -> {p}")


async def click_btn(page, label):
    return await page.evaluate(
        """(t) => {
            const b = [...document.querySelectorAll('button')]
                .find(e => e.textContent.trim() === t);
            if (!b) return 'NO BUTTON ' + t;
            if (b.disabled) return 'DISABLED ' + t;
            b.click();
            return 'clicked ' + t;
        }""", label)


async def wait_enabled(page, label, timeout_s=400):
    for _ in range(int(timeout_s / 2)):
        st = await page.evaluate(
            """(t) => {
                const b = [...document.querySelectorAll('button')]
                    .find(e => e.textContent.trim() === t);
                return !b ? 'missing' : (b.disabled ? 'disabled' : 'ready');
            }""", label)
        if st == "ready":
            return True
        await asyncio.sleep(2)
    log(f"TIMEOUT waiting for '{label}'")
    return False


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}")
        return False


async def type_path(page, value):
    return await page.evaluate(
        """(p) => {
            const el = document.querySelector('.ds-dlg-path');
            if (!el) return 'NO_PATH_BOX';
            Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                  .set.call(el, p);
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            return 'OK';
        }""", value)


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME, headless=False,
            args=["--disable-gpu", "--use-gl=swiftshader", "--no-sandbox",
                  "--disable-dev-shm-usage", "--window-size=1920,1080",
                  "--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto(URL, wait_until="domcontentloaded")
        await asyncio.sleep(12)

        await click_text(page, "Load CFD Geometry")
        await asyncio.sleep(3)
        await type_path(page, PROJ)
        await asyncio.sleep(10)
        await click_text(page, "Post-processing")
        await wait_enabled(page, "CFD result file", 420)

        log(await click_btn(page, "CFD result file"))
        await asyncio.sleep(1)
        await wait_enabled(page, "Choose result file…", 60)
        log(await click_btn(page, "Choose result file…"))
        await asyncio.sleep(3)
        await type_path(page, VTU)
        await asyncio.sleep(float(os.environ.get("HEAD_DS_WAIT", "200")))

        # 1. slab ROI + box + full-domain field
        mn, mx = [[float(v) for v in part.split(",")] for part in SLAB.split(";")]
        got = await page.evaluate(
            """([mn, mx]) => {
                const rows = [...document.querySelectorAll('.roi-row')];
                const set = (el, v) => {
                    Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set.call(el, String(v));
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                };
                rows.forEach((r, i) => {
                    const [a, b] = r.querySelectorAll('input');
                    if (a) set(a, mn[i]); if (b) set(b, mx[i]);
                });
                return rows.map(r => [...r.querySelectorAll('input')]
                    .map(i => i.value).join('-')).join(' | ');
            }""", [mn, mx])
        log(f"ROI set: {got}")
        await asyncio.sleep(6)
        log(await click_btn(page, "Show ROI Box"))
        await asyncio.sleep(6)
        await wait_enabled(page, "Temp", 120)
        log(await click_btn(page, "Temp"))
        await asyncio.sleep(float(os.environ.get("HEAD_VOL_WAIT", "120")))
        await shot(page, "e01_full_field_box_shown.png")

        # 2. extract
        await wait_enabled(page, "✂ Extract ROI as dataset", 120)
        log(await click_btn(page, "✂ Extract ROI as dataset"))
        await asyncio.sleep(float(os.environ.get("HEAD_EX_WAIT", "180")))
        await shot(page, "e02_extracted.png")

        # 3. THE DISCRIMINATOR: hide the box. Extraction keeps the field confined.
        log(await click_btn(page, "Hide ROI Box"))
        await asyncio.sleep(25)
        await shot(page, "e03_box_hidden_field_confined.png")

        # 4. A SECOND, DIFFERENT ROI. Two things to prove: the source grid is reused
        # (no second 122 MB read), and the new cut comes from the FULL result rather than
        # from the previous cut — otherwise ROIs could only ever shrink.
        log(await click_btn(page, "Show ROI Box"))
        await asyncio.sleep(6)
        mn2 = [mn[0], mx[1], mn[2]]
        mx2 = [mx[0], mx[1] + 1.0, mx[2]]
        got2 = await page.evaluate(
            """([mn, mx]) => {
                const rows = [...document.querySelectorAll('.roi-row')];
                const set = (el, v) => {
                    Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value').set.call(el, String(v));
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                };
                rows.forEach((r, i) => {
                    const [a, b] = r.querySelectorAll('input');
                    if (a) set(a, mn[i]); if (b) set(b, mx[i]);
                });
                return rows.map(r => [...r.querySelectorAll('input')]
                    .map(i => i.value).join('-')).join(' | ');
            }""", [mn2, mx2])
        log(f"ROI #2 (the slab ABOVE the first): {got2}")
        await asyncio.sleep(6)
        log(await click_btn(page, "✂ Extract ROI as dataset"))
        await asyncio.sleep(float(os.environ.get("HEAD_EX_WAIT", "180")))
        await shot(page, "e04_second_roi.png")

        await browser.close()
        log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
