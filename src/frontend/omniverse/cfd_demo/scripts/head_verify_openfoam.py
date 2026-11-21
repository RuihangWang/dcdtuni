#!/usr/bin/env python3
"""End-to-end: a .cfdproj's geometry AND its own OpenFOAM result, both in OV.

This is the question the GTC demo never answered. That demo pairs a fixed GTC scene with
one pre-converted `data/internal.vtu`, so it never showed that an ARBITRARY project built
in the STLGen editor, solved by this repo's own OpenFOAM pipeline, can be rendered in
Omniverse — geometry from the .cfdproj, field from `<case>/log/<ts>/VTK/…/internal.vtu`.

Two halves, verified separately because they fail separately:

  1. geometry   — Load CFD Geometry -> the .cfdproj
  2. flow field — Post-processing -> Load dataset -> the OpenFOAM .vtu -> Temperature

    CFDPROJ_PATH=/abs/case.cfdproj VTU_PATH=/abs/internal.vtu \\
    HEAD_SHOT_DIR=/tmp/of DISPLAY=:99 $BROWSER_USE_PY scripts/head_verify_openfoam.py
"""
import asyncio
import os
import pathlib
import shutil
import sys

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_verify_openfoam")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
PROJ = os.environ.get("CFDPROJ_PATH", "")
VTU = os.environ.get("VTU_PATH", "")
FIELD = os.environ.get("HEAD_FIELD", "Temp")   # the segmented button reads "Temp"
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[of] {m}", flush=True)


async def shot(page, name):
    p = os.path.join(SHOT, name)
    await page.screenshot(path=p)
    log(f"shot -> {p}")


async def type_path(page, value):
    """Type into the file dialog's path box. React controlled input: a plain .value
    assignment does not fire onChange, so use the native prototype setter."""
    return await page.evaluate(
        """(p) => {
            const el = document.querySelector('.ds-dlg-path');
            if (!el) return 'NO_PATH_BOX';
            Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                  .set.call(el, p);
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            return 'OK ' + el.value;
        }""", value)


async def click_seg(page, label):
    """Click a segmented BUTTON by its exact label.

    get_by_text() is ambiguous in this panel — "CFD result file" and "Velocity" each
    appear in a button and in nearby help text, so .first can land on a non-clickable
    text node and time out while the control sits right there unclicked.
    """
    return await page.evaluate(
        """(t) => {
            const b = [...document.querySelectorAll('button')]
                .find(e => e.textContent.trim() === t);
            if (!b) return 'NO BUTTON ' + t;
            if (b.disabled) return 'DISABLED ' + t;
            b.click();
            return 'clicked ' + t;
        }""", label)


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}: {str(e)[:90]}")
        return False


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME, headless=False,
            args=["--disable-gpu", "--use-gl=swiftshader", "--no-sandbox",
                  "--disable-dev-shm-usage", "--window-size=1920,1080",
                  "--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("console", lambda m: log(f"  console[error] {m.text[:110]}")
                if m.type == "error" else None)

        await page.goto(URL, wait_until="domcontentloaded")
        await asyncio.sleep(12)
        await shot(page, "o01_connected.png")

        # ── 1. geometry from the .cfdproj ───────────────────────────────────────────
        if not await click_text(page, "Load CFD Geometry"):
            await browser.close()
            return 1
        await asyncio.sleep(3)
        log(f"geometry path: {await type_path(page, PROJ)}")
        await asyncio.sleep(float(os.environ.get("HEAD_GEO_WAIT", "80")))
        await shot(page, "o02_geometry.png")

        # ── 2. the case's OWN OpenFOAM result ───────────────────────────────────────
        await click_text(page, "Post-processing")
        await asyncio.sleep(2)
        await shot(page, "o03_postproc_tab.png")
        # The button is "Load a different result…", not "Load dataset": this tab was
        # reworked AI-first, so "Start post-processing" now loads the newest AI
        # prediction and the file picker moved under the secondary button. Matching the
        # old label timed out and reported no button at all.
        # Pick the CFD-file source, then open its picker. The tab is a two-way switch
        # now, so the file path is not a fallback button any more.
        await click_text(page, "CFD result file")
        await asyncio.sleep(1)
        await shot(page, "o03b_source_file.png")
        if not await click_text(page, "Choose result file"):
            log("FATAL: no dataset picker button")
            await browser.close()
            return 1
        await asyncio.sleep(3)
        log(f"dataset path: {await type_path(page, VTU)}")
        # A 127 MB unstructured .vtu is read, converted and voxelised by omni.cae.dav;
        # there is no completion signal on the one-way channel, so wait a fixed window
        # and cross-check the Kit log.
        await asyncio.sleep(float(os.environ.get("HEAD_DS_WAIT", "180")))
        await shot(page, "o04_dataset_loaded.png")

        # ── 3. render the field as a volume ─────────────────────────────────────────
        await click_text(page, FIELD)
        await asyncio.sleep(float(os.environ.get("HEAD_VOL_WAIT", "120")))
        await shot(page, "o05_volume.png")

        # Isolate the field: hide the racks so the volume is not occluded by geometry.
        n = await page.evaluate(
            """() => {
                const rows = [...document.querySelectorAll('li.tree-node')];
                let hit = 0;
                for (const r of rows) {
                    const lab = r.querySelector('.node-label');
                    const cb = r.querySelector('input.node-vis');
                    if (lab && cb && /Rack \\(RACK\\)|ACU \\(ACU\\)/.test(lab.textContent)
                        && cb.checked) { cb.click(); hit++; }
                }
                return hit;
            }""")
        log(f"hid {n} geometry group(s) to expose the field")
        await asyncio.sleep(8)
        await shot(page, "o06_volume_isolated.png")

        # ── 4. the switch itself: back to AI, then back to the file ────────────────
        await click_text(page, "Geometry")
        await asyncio.sleep(1)
        roi = await page.evaluate(
            """() => [...document.querySelectorAll('.roi-row input')]
                     .map(e => e.value).join(',')""")
        log(f"(roi inputs are on the Post-processing tab) {roi}")
        await click_text(page, "Post-processing")
        await asyncio.sleep(1)
        roi = await page.evaluate(
            """() => [...document.querySelectorAll('.roi-row input')]
                     .map(e => e.value).join(',')""")
        log(f"ROI box bounds: {roi}")
        log(await click_seg(page, "AI prediction"))
        await asyncio.sleep(1)
        await shot(page, "o07_switched_to_ai.png")
        log(await click_seg(page, "CFD result file"))
        await asyncio.sleep(1)
        await shot(page, "o08_switched_back_to_file.png")
        state = await page.evaluate(
            """() => (document.querySelector('.postproc')||{}).innerText || 'NO PANEL'""")
        log("panel:\n    " + "\n    ".join(state.splitlines()[:12]))

        # ── 5. Velocity, which needs the derived Umag ──────────────────────────────
        log(await click_seg(page, "Velocity"))
        await asyncio.sleep(float(os.environ.get("HEAD_VOL_WAIT", "120")))
        await shot(page, "o09_velocity.png")

        await browser.close()
        log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
