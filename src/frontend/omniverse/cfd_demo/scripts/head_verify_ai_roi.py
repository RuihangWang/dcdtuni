#!/usr/bin/env python3
"""End-to-end browser proof of the ROI-only AI path — the item left unverified.

Two things were correct on disk but never seen in the live viewport:
  1. the ROI .vti the AI path now writes (28x smaller than the full field)
  2. the opt-in sim-ROI pivot restriction (box + 45,000 room-wide anchors)

"Correct on disk" is not the same as "IndeX voxelises a 41x14x26 ROI grid the way it does
the full one", and that gap is exactly where a silent rendering failure would live. This
drives the shipped UI: position the ROI, tick both boxes, predict, load the prediction,
show Temperature, screenshot.

    HEAD_SHOT_DIR=/tmp/air CFDPROJ_PATH=... DISPLAY=:99 \\
        $BROWSER_USE_PY scripts/head_verify_ai_roi.py
"""
import asyncio
import os
import pathlib
import shutil
import sys

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_verify_ai_roi")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
PROJ = os.environ.get("CFDPROJ_PATH", "")
# GTC room is 31 x 19.2 x 5; a box in the middle of the rack rows.
SLAB = os.environ.get("HEAD_ROI_SLAB", "8,0.5,2;16,3,7")
SIM = os.environ.get("HEAD_SIM_ROI", "1") not in ("0", "", "false")
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[air] {m}", flush=True)


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


async def check_by_label(page, needle, on=True):
    """Tick/untick the checkbox whose LABEL contains `needle`."""
    return await page.evaluate(
        """([t, want]) => {
            for (const l of document.querySelectorAll('label')) {
                if (!l.textContent.includes(t)) continue;
                const c = l.querySelector('input[type=checkbox]');
                if (!c) continue;
                if (c.disabled) return 'DISABLED ' + t;
                if (c.checked !== want) c.click();
                return `${t} -> ${c.checked}`;
            }
            return 'NO CHECKBOX ' + t;
        }""", [needle, on])


async def wait_enabled(page, label, timeout_s=420):
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


async def click_text(page, text):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=8000)
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}")
        return False


async def type_path(page, v):
    return await page.evaluate(
        """(p) => {
            const el = document.querySelector('.ds-dlg-path');
            if (!el) return 'NO_PATH_BOX';
            Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                  .set.call(el, p);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            return 'OK';
        }""", v)


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
        await wait_enabled(page, "CFD result file", 480)
        await shot(page, "a01_geometry.png")

        # Position the ROI — the AI panel reads the box drawn here.
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
        await asyncio.sleep(5)

        # AI tab: tick export-ROI, and (default) the sim-ROI pivot restriction.
        await click_text(page, "AI Prediction")
        await asyncio.sleep(2)
        log(await check_by_label(page, "Export the ROI only", True))
        if SIM:
            log(await check_by_label(page, "Also simulate on the ROI", True))
        await shot(page, "a02_ai_panel.png")

        # Predict. Whole-domain inference plus export is ~30 s; sim-ROI is faster.
        # The button is lower-case "simulate" ("Simulating…" while running). Guessing
        # four capitalised variants found nothing and the run carried on to load a STALE
        # prediction — a green DONE that proved nothing. Match exactly, and fail loudly.
        r = await click_btn(page, "simulate")
        log(f"predict: {r}")
        if not r.startswith("clicked"):
            log("FATAL: simulate button not found — aborting rather than "
                "verifying a stale result")
            await shot(page, "a03_no_button.png")
            await browser.close()
            return 1
        await asyncio.sleep(float(os.environ.get("HEAD_AI_WAIT", "180")))
        await shot(page, "a03_predicted.png")

        # Load that prediction and render it.
        await click_text(page, "Post-processing")
        await asyncio.sleep(2)
        log(await click_btn(page, "AI prediction"))
        await asyncio.sleep(1)
        await wait_enabled(page, "Load AI prediction", 60)
        log(await click_btn(page, "Load AI prediction"))
        await asyncio.sleep(float(os.environ.get("HEAD_DS_WAIT", "200")))
        await shot(page, "a04_loaded.png")

        # Hide the ceiling first. "Floors" carries the room's ceiling slab, which from
        # the default overhead camera covers the entire room — the first run produced a
        # frame showing nothing but the ceiling, with a healthy temperature legend, which
        # is a screenshot that proves nothing.
        await click_text(page, "Geometry")
        await asyncio.sleep(1)
        hid = await page.evaluate(
            """() => {
                let n = 0;
                for (const r of document.querySelectorAll('li.tree-node')) {
                    const lab = r.querySelector('.node-label');
                    const cb = r.querySelector('input.node-vis');
                    if (lab && cb && /Floor \(FLOOR\)|Wall \(WALL\)/.test(lab.textContent)
                        && cb.checked) { cb.click(); n++; }
                }
                return n;
            }""")
        log(f"hid {hid} occluding group(s)")
        await asyncio.sleep(8)
        await click_text(page, "Post-processing")
        await asyncio.sleep(2)

        await wait_enabled(page, "Temp", 120)
        log(await click_btn(page, "Temp"))
        await asyncio.sleep(float(os.environ.get("HEAD_VOL_WAIT", "140")))
        await shot(page, "a05_roi_volume.png")

        await browser.close()
        log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
