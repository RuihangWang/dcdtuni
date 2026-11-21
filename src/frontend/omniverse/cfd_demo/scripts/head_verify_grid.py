#!/usr/bin/env python3
"""Sweep the native viewport-grid settings and screenshot each, to settle whether the
Kit grid can draw CELLS in this headless stream — not just its two axis lines.

Background. The repo carried a hand-built USD BasisCurves grid, justified by a comment
asserting the native grid "renders only its axis lines, no cells" under --no-window.
That claim was never tested against the settings: the app ships CENTIMETRE, Z-UP
defaults (scale 100, spacing 1000, plane XY), so on this metre, Y-up stage a native grid
left at defaults would put its first cell line 100 km away while still drawing the
origin axes — which looks exactly like "only axis lines".

So sweep `scale` (and the fade distances, which are the other candidate for swallowing
the cells) and read the frames.

    CFDPROJ_PATH=... HEAD_SHOT_DIR=/tmp/grid DISPLAY=:99 \\
        $BROWSER_USE_PY scripts/head_verify_grid.py
"""
import asyncio
import json
import os
import pathlib
import shutil
import sys

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_verify_grid")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
PROJ = os.environ.get("CFDPROJ_PATH", "")
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)

# Each entry is one setGrid payload to try. The interesting axis is `scale`: if the
# cells appear at 1.0 the "no cells headless" claim was really a unit mismatch.
SWEEP = [
    # Finest cell that still draws. Cell size tracks spacing x scale; 0.1 and 0.06 both
    # rendered nothing, so bisect the gap rather than assume the floor is 1.0.
    ("f10", {"enabled": True, "plane": "XZ", "majorInterval": 5, "lineWidth": 2.0,
             "lineColor": [1.0, 1.0, 1.0], "fadeStart": 40.0, "fadeEnd": 80.0,
             "scale": 10.0, "spacing": 1.0}),
    ("f5", {"spacing": 0.5}),
    ("f3", {"spacing": 0.3}),
    ("f2", {"spacing": 0.2}),
]


def log(m):
    print(f"[grid] {m}", flush=True)


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

        await page.get_by_text("Load CFD Geometry", exact=False).first.click(timeout=8000)
        await asyncio.sleep(3)
        await page.evaluate(
            """(p) => {
                const el = document.querySelector('.ds-dlg-path');
                Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                      .set.call(el, p);
                el.dispatchEvent(new Event('input',  { bubbles: true }));
                el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            }""", PROJ)
        await asyncio.sleep(float(os.environ.get("HEAD_GEO_WAIT", "80")))
        await page.screenshot(path=os.path.join(SHOT, "g0_as_built.png"))
        log(f"shot -> {SHOT}/g0_as_built.png")

        # The page has no Grid control yet; drive the command channel directly. This is a
        # PROBE, so reaching past the UI is the point — it isolates the backend question
        # (can the native grid draw cells?) from any UI wiring.
        for name, payload in SWEEP:
            sent = await page.evaluate(
                """async (args) => {
                    const m = await import('/src/streamMessages.ts');
                    await m.sendCommand('setGrid', args);
                    return 'sent ' + JSON.stringify(args);
                }""", payload)
            log(f"{name}: {sent}")
            await asyncio.sleep(5)
            out = os.path.join(SHOT, f"{name}.png")
            await page.screenshot(path=out)
            log(f"shot -> {out}")

        await browser.close()
        log("DONE " + json.dumps([n for n, _ in SWEEP]))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
