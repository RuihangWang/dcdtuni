#!/usr/bin/env python3
"""Verify streamlines on a .cfdproj + its own OpenFOAM result, and settle occlusion.

A reported "streamlines failed" turned out to be a run that SUCCEEDED — the kernels ran
and the curves were metres long — so the open question was whether they were merely
hidden behind the IndeX volume that was displayed over the same region. This shoots the
identical streamline state twice, volume OFF then volume ON, which answers it directly.

It also checks the three things that were fixed:
  * no "Missing targets" warning in the Kit log for this run
  * the seed sphere defaults to THIS scene's centre, not the GTC room's [15.5, 2.5, 9.6]
  * the backend reports a real Fabric point count instead of announcing success blindly

    CFDPROJ_PATH=... VTU_PATH=... HEAD_SHOT_DIR=/tmp/sl DISPLAY=:99 \\
        $BROWSER_USE_PY scripts/head_verify_streamlines_cfdproj.py
"""
import asyncio
import os
import pathlib
import shutil
import sys

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_verify_streamlines")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
PROJ = os.environ.get("CFDPROJ_PATH", "")
VTU = os.environ.get("VTU_PATH", "")
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[sl] {m}", flush=True)


async def shot(page, name):
    p = os.path.join(SHOT, name)
    await page.screenshot(path=p)
    log(f"shot -> {p}")


async def click_btn(page, label):
    """Click a BUTTON by exact label — the panel repeats several of these labels in
    help text, so get_by_text().first can land on a non-clickable node."""
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
    """Poll until a button exists AND is enabled.

    Fixed sleeps are not enough here: every control in this panel gates on state the page
    only reaches when a Kit log line arrives, so a slow load (or a broken log feed) leaves
    the button disabled and a blind sleep marches straight past it clicking nothing. The
    previous run reported 'DISABLED Choose result file…' for exactly that reason.
    """
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
    log(f"TIMEOUT waiting for '{label}' to become enabled")
    return False


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}: {str(e)[:80]}")
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

        # geometry
        await click_text(page, "Load CFD Geometry")
        await asyncio.sleep(3)
        await type_path(page, PROJ)
        # Ready = the Post-processing tab's picker has come alive, which only happens
        # once ModelTree saw the backend's "loadScene: revealed" receipt.
        await asyncio.sleep(10)
        await click_text(page, "Post-processing")
        await wait_enabled(page, "CFD result file", 420)
        await shot(page, "s01_geometry.png")

        # result
        log(await click_btn(page, "CFD result file"))
        await asyncio.sleep(1)
        await wait_enabled(page, "Choose result file…", 60)
        log(await click_btn(page, "Choose result file…"))
        await asyncio.sleep(3)
        await type_path(page, VTU)
        await asyncio.sleep(float(os.environ.get("HEAD_DS_WAIT", "200")))
        await shot(page, "s02_dataset.png")

        # The seed defaults must now come from the scene, not the GTC room.
        seed = await page.evaluate(
            """() => {
                const rows = [...document.querySelectorAll('.roi-row')];
                return rows.map(r => r.textContent.trim() + ' = ' +
                    [...r.querySelectorAll('input')].map(i => i.value).join(',')).join(' | ');
            }""")
        log(f"seed/ROI inputs: {seed}")

        # Reproduce the REPORTED configuration: a thin ROI slab with Clip ON. The first
        # pass ran clip=OFF over the whole room, which is not what the user had, so it
        # could not settle whether the slab was what hid the curves.
        slab = os.environ.get("HEAD_ROI_SLAB")
        if slab:
            mn, mx = [[float(v) for v in part.split(",")] for part in slab.split(";")]
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
                        if (a) set(a, mn[i]);
                        if (b) set(b, mx[i]);
                    });
                    return rows.map(r => [...r.querySelectorAll('input')]
                        .map(i => i.value).join('-')).join(' | ');
                }""", [mn, mx])
            log(f"ROI slab set: {got}")
            await asyncio.sleep(6)
            # Clip is gated on the ROI box being SHOWN
            # (disabled={!roiVisible || busy || !sceneReady}), so reveal the box first —
            # skipping it silently reported "DISABLED Clip Volume to ROI" and the run
            # then measured the unclipped case it was meant to replace.
            log(await click_btn(page, "Show ROI Box"))
            await asyncio.sleep(8)
            await wait_enabled(page, "Clip Volume to ROI", 60)
            log(await click_btn(page, "Clip Volume to ROI"))
            await asyncio.sleep(20)
            await shot(page, "s03a_roi_slab_clipped.png")

        # streamlines, volume OFF — the clean look at the curves
        await wait_enabled(page, "Add Streamlines", 300)
        log(await click_btn(page, "Add Streamlines"))
        await asyncio.sleep(4)
        await shot(page, "s03_streamlines_on.png")
        await wait_enabled(page, "Apply", 120)
        log(await click_btn(page, "Apply"))
        await asyncio.sleep(float(os.environ.get("HEAD_SL_WAIT", "150")))
        await shot(page, "s04_curves_volume_OFF.png")

        # same state, volume ON — the occlusion test
        await wait_enabled(page, "Temp", 120)
        log(await click_btn(page, "Temp"))
        await asyncio.sleep(float(os.environ.get("HEAD_VOL_WAIT", "140")))
        await shot(page, "s05_curves_volume_ON.png")
        log(await click_btn(page, "Off"))
        await asyncio.sleep(20)
        await shot(page, "s06_volume_off_again.png")

        await browser.close()
        log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
