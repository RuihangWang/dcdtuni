#!/usr/bin/env python3
"""Frame ONE rack close-up in the OV viewport, for side-by-side against the editor.

The rack internals (server top/bottom faces, the inlet/outlet recess, the U-division
seams on a blank panel) are millimetres of detail on a 1 m box. A whole-room shot cannot
show whether they are right, so this drives the OV camera onto a single rack at the same
eye point the STLGen editor is put at, and screenshots.

It drives the SHIPPED control — a double-click on the model-tree row, which fires
focusObject and makes Kit frame the viewport on that object. Going through the real UI
rather than a test-only hook is what makes this evidence about the demo.

    CFDPROJ_PATH=... HEAD_FOCUS=Default_Rack_1 HEAD_HIDE="Floor (FLOOR)|Wall (WALL)" \\
        DISPLAY=:99 $BROWSER_USE_PY scripts/head_verify_rack_closeup.py
"""
import asyncio
import json
import os
import pathlib
import shutil
import sys

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_verify_rack")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
PROJ = os.environ.get("CFDPROJ_PATH", "")
FOCUS = os.environ.get("HEAD_FOCUS", "Default_Rack_1")
HIDE = [g for g in os.environ.get("HEAD_HIDE", "Floor (FLOOR)|Wall (WALL)").split("|") if g]
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[rack] {m}", flush=True)


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

        for g in HIDE:
            r = await page.evaluate(
                """(t) => {
                    for (const r of document.querySelectorAll('li.tree-node')) {
                        const lab = r.querySelector('.node-label');
                        const cb = r.querySelector('input.node-vis');
                        if (lab && cb && lab.textContent.includes(t) && cb.checked) {
                            cb.click(); return 'hid ' + t;
                        }
                    }
                    return 'no row / already off: ' + t;
                }""", g)
            log(r)
            await asyncio.sleep(4)

        # Double-click the row -> focusObject -> Kit frames the viewport on it. Going
        # through the real UI (not a private hook) is what makes this a check of the
        # shipped control rather than of a test-only back door.
        hit = await page.evaluate(
            """(t) => {
                for (const r of document.querySelectorAll('li.tree-node')) {
                    const lab = r.querySelector('.node-label');
                    if (lab && lab.textContent.includes(t)) {
                        lab.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
                        return 'dblclick ' + t;
                    }
                }
                return 'NO ROW ' + t;
            }""", FOCUS)
        log(f"focus: {hit}")
        await asyncio.sleep(8)
        out = os.path.join(SHOT, "r01_rack_closeup.png")
        await page.screenshot(path=out)
        log(f"shot -> {out}")
        log(json.dumps({"focus": FOCUS, "hid": HIDE}))
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
