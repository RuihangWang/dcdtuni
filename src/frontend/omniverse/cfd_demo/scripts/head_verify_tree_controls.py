#!/usr/bin/env python3
"""Head-browser proof that the model tree's controls actually DRIVE the viewport.

Two reported failures, one script:

  * "the Floor checkbox has no effect" — the tree used to be a hardcoded GTC list, so
    the row said `Floor` while a .cfdproj build creates `Floors`. Kit logged "unknown
    group" and did nothing. This unticks the row, screenshots, reticks, screenshots.
  * "the right panel's opacity option is nothing like the editor's" — selecting an
    object must open an Inspector with a working opacity slider.

Screenshots ARE the evidence; the Kit log lines are the cross-check. Run:

    CFDPROJ_PATH=/abs/path.cfdproj HEAD_SHOT_DIR=/tmp/tc DISPLAY=:99 \\
        $BROWSER_USE_PY scripts/head_verify_tree_controls.py
"""
import asyncio
import os
import pathlib
import shutil
import sys

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_verify_tree_controls")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
PROJ = os.environ.get("CFDPROJ_PATH", "")
# Tree label of the row to exercise, and the object to select for the opacity check.
ROW = os.environ.get("HEAD_ROW", "Floor (FLOOR)")
PICK = os.environ.get("HEAD_PICK", "Rack (RACK)")
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[tree] {m}", flush=True)


async def shot(page, name):
    p = os.path.join(SHOT, name)
    await page.screenshot(path=p)
    log(f"shot -> {p}")


# Click the checkbox on the row whose label CONTAINS `text`, and report what it went to.
CLICK_ROW = """(t) => {
    for (const r of document.querySelectorAll('li.tree-node')) {
        const lab = r.querySelector('.node-label');
        const cb = r.querySelector('input.node-vis');
        if (lab && cb && lab.textContent.includes(t)) {
            const before = cb.checked;
            cb.click();
            return `${t}: ${before} -> ${cb.checked}`;
        }
    }
    return `NO ROW MATCHING ${t}`;
}"""


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
        await shot(page, "t01_loaded.png")

        # ── 1. the reported-broken checkbox ─────────────────────────────────────────
        log(await page.evaluate(CLICK_ROW, ROW))
        await asyncio.sleep(6)
        await shot(page, "t02_row_off.png")
        log(await page.evaluate(CLICK_ROW, ROW))
        await asyncio.sleep(6)
        await shot(page, "t03_row_on.png")

        # ── 2. select an object -> Inspector -> opacity ─────────────────────────────
        clicked = await page.evaluate(
            """(t) => {
                for (const r of document.querySelectorAll('li.tree-node')) {
                    const lab = r.querySelector('.node-label');
                    if (lab && lab.textContent.includes(t)) { lab.click(); return 'ok ' + t; }
                }
                return 'NO ROW ' + t;
            }""", PICK)
        log(f"select: {clicked}")
        await asyncio.sleep(2)
        insp = await page.evaluate(
            "() => (document.querySelector('.inspector-panel')||{}).innerText || 'NO PANEL'")
        log("inspector:\n    " + "\n    ".join(insp.splitlines()[:14]))
        await shot(page, "t04_inspector.png")

        # React controlled input: a plain .value assignment does not fire onChange, so
        # the command never reaches Kit and the slider silently does nothing.
        set_op = await page.evaluate(
            """(v) => {
                const els = [...document.querySelectorAll('.inspector-panel input[type=range]')];
                if (!els.length) return 'NO SLIDER';
                const el = els[0];
                Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                      .set.call(el, String(v));
                el.dispatchEvent(new Event('input',  { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return 'opacity=' + el.value;
            }""", 0.3)
        log(f"slider: {set_op}")
        await asyncio.sleep(6)
        await shot(page, "t05_opacity_30.png")

        await browser.close()
        log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
