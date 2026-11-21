#!/usr/bin/env python3
"""Locate each ACU in the recorded viewport, in pixels, by hiding it and looking.

The animations need to point at "this unit failed" and "these took over", which means
knowing where each ACU lands in the rendered frame. Kit gives no camera matrix over the
one-way command channel, and fitting a projection from hand-picked correspondences would
put invented positions on a published figure.

So the render answers the question itself: untick one ACU in the model tree, screenshot,
and diff against the reference. Whatever changed IS that unit, and the centroid of the
changed pixels is where to draw the marker. Exact by construction, no calibration, and it
re-derives itself if the camera ever moves.

The camera must match the recording exactly, so this uses the same crop, the same
geometry load and the same ceiling-hiding step as `hotspot_record.py`.

    DISPLAY=:99 python3 hotspot_acu_positions.py
"""
import asyncio
import json
import os
import pathlib
import shutil
import sys

import numpy as np
from PIL import Image

REPO = pathlib.Path(__file__).resolve().parents[5]
CASE = pathlib.Path(os.environ.get("HOTSPOT_CASE", REPO / "case/GTC"))
OUT = pathlib.Path(os.environ.get("HOTSPOT_ACU_JSON", CASE / "hotspot_acu_positions.json"))
TMP = pathlib.Path(os.environ.get("HOTSPOT_ACU_TMP", "/tmp/hotspot_acu"))
CFDPROJ = os.environ.get("HOTSPOT_CFDPROJ", str(CASE / "GTC_2025.cfdproj"))
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
VIEW = {"x": 356, "y": 40, "width": 1168, "height": 800}
GEO_WAIT = float(os.environ.get("HOTSPOT_GEO_WAIT", "90"))

# Tree row name -> the uid the .cfdrun boundary bindings use.
ROW_TO_UID = {
    "left_fan_wall_room_crac_1": "m_51",
    "left_fan_wall_room_crac_2": "m_52",
    "left_fan_wall_room_crac_3": "m_53",
    "right_fan_wall_room_crac_1": "m_54",
    "right_fan_wall_room_crac_2": "m_55",
    "right_fan_wall_room_crac_3": "m_56",
}


def log(m):
    print(f"[acu] {m}", flush=True)


async def click_text(page, text, timeout=10000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


async def type_path(page, value):
    return await page.evaluate(
        """(p) => {
            const el = document.querySelector('.ds-dlg-path');
            if (!el) return 'NO_PATH_BOX';
            Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                  .set.call(el, p);
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            return 'OK';
        }""", value)


async def toggle_row(page, needle):
    """Click the visibility checkbox of the tree row whose label contains `needle`."""
    return await page.evaluate(
        """(n) => {
            for (const r of document.querySelectorAll('li.tree-node')) {
                const l = r.querySelector('.node-label');
                const c = r.querySelector('input.node-vis');
                if (l && c && l.textContent.includes(n)) { c.click(); return c.checked; }
            }
            return 'NOT_FOUND';
        }""", needle)


def changed_centroid(ref_path, shot_path, thr=25, min_pixels=200):
    """Where the pixels that changed are CONCENTRATED, between two viewport shots.

    A plain centroid does not work here. Hiding one ACU also nudges the path-traced
    lighting across the whole room, so at a low threshold tens of thousands of faintly
    changed pixels swamp the few thousand that are the unit itself, and all six ACUs
    "locate" to the middle of the frame. Raising the threshold alone is fragile — the far
    units change far fewer pixels than the near ones — so the pixels are binned and the
    fullest 3x3 neighbourhood wins. That is the unit; the diffuse relighting never
    concentrates anywhere.
    """
    a = np.asarray(Image.open(ref_path).convert("L"), dtype=np.int16)
    b = np.asarray(Image.open(shot_path).convert("L"), dtype=np.int16)
    ys, xs = np.nonzero(np.abs(a - b) > thr)
    if len(xs) < min_pixels:
        return None, len(xs)
    h, w = a.shape
    NX, NY = 32, 22
    bx = np.clip((xs * NX) // w, 0, NX - 1)
    by = np.clip((ys * NY) // h, 0, NY - 1)
    hist = np.zeros((NY, NX), dtype=np.int32)
    np.add.at(hist, (by, bx), 1)
    pad = np.pad(hist, 1)
    box = sum(pad[i:i + NY, j:j + NX] for i in range(3) for j in range(3))
    cy, cx = np.unravel_index(int(np.argmax(box)), box.shape)
    keep = (np.abs(bx - cx) <= 1) & (np.abs(by - cy) <= 1)
    if keep.sum() < min_pixels:
        return None, int(keep.sum())
    return (int(xs[keep].mean()), int(ys[keep].mean())), int(keep.sum())


async def main():
    TMP.mkdir(parents=True, exist_ok=True)
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=CHROME, headless=False,
            args=["--disable-gpu", "--use-gl=swiftshader", "--no-sandbox",
                  "--disable-dev-shm-usage", "--window-size=1920,1080",
                  "--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(14000)

        await click_text(page, "Load CFD Geometry")
        await page.wait_for_timeout(3000)
        await type_path(page, CFDPROJ)
        await page.wait_for_timeout(int(GEO_WAIT * 1000))
        # Same occluders the recorder hides, so the reference frame matches the animation.
        await page.evaluate(
            """() => {
                for (const r of document.querySelectorAll('li.tree-node')) {
                    const l = r.querySelector('.node-label');
                    const c = r.querySelector('input.node-vis');
                    if (l && c && c.checked && /ceiling/i.test(l.textContent)) c.click();
                }
            }""")
        await page.wait_for_timeout(6000)

        ref = TMP / "ref.png"
        await page.screenshot(path=str(ref), clip=VIEW)
        log(f"reference -> {ref}")

        positions = {}
        for row, uid in ROW_TO_UID.items():
            state = await toggle_row(page, row)
            if state == "NOT_FOUND":
                log(f"{uid}: tree row '{row}' not found")
                continue
            await page.wait_for_timeout(5000)
            shot = TMP / f"{uid}_hidden.png"
            await page.screenshot(path=str(shot), clip=VIEW)
            pt, n = changed_centroid(ref, shot)
            await toggle_row(page, row)        # put it back
            await page.wait_for_timeout(4000)
            if pt is None:
                log(f"{uid}: only {n} pixels changed — not located")
                continue
            positions[uid] = {"x": pt[0], "y": pt[1], "pixels": n}
            log(f"{uid} ({row}) -> ({pt[0]}, {pt[1]})  {n} px")

        OUT.write_text(json.dumps({"view": VIEW, "acus": positions}, indent=2))
        log(f"{len(positions)}/6 located -> {OUT}")
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
