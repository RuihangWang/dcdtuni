#!/usr/bin/env python3
"""HEAD-browser verification: dataset file-picker gating + ROI clip-to-box fix.

Flow (single fresh-Kit pass):
  1. Load CFD Geometry (scene builds geometry only — no auto-ingested dataset).
  2. Post-processing tab: assert the field controls are DISABLED (gated).
  3. Click "Load dataset…" -> the file dialog lists server datasets.
  4. Pick internal.vti -> Open -> backend load_dataset + voxelise.
  5. Assert controls ENABLE once the backend logs "load_dataset OK".
  6. Temp -> volume renders.
  7. Show ROI Box -> shrink to a central box -> Clip -> volume restricts to the box
     (the cache-drop removal: voxeliser honours the ROI, no "No source dataset found").

Same true-head launch recipe as head_verify.py.
Run:
  HEAD_URL=http://localhost:8081 HEAD_SHOT_DIR=/tmp/head_loaddlg \
  DISPLAY=:99 $BROWSER_USE_PY \
      src/frontend/omniverse/cfd_demo/scripts/head_verify_loaddlg.py
"""
import asyncio
import shutil
import pathlib
import os

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_loaddlg")
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
    print(f"[loaddlg] {m}", flush=True)


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}: {str(e)[:80]}")
        return False


async def temp_disabled(page):
    return await page.evaluate(
        "() => { const b=[...document.querySelectorAll('button.seg-btn')]"
        ".find(x=>x.textContent.trim()=='Temp'); return b? b.disabled : 'NO_BTN'; }")


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

        # Post-processing tab: controls must be GATED (Temp disabled).
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)
        dis = await temp_disabled(page)
        log(f"[gate] Temp disabled before load? {dis}")
        await page.screenshot(path=f"{SHOT}/ld01_gated.png")

        # Open the file dialog.
        await click_text(page, "Load dataset")
        await page.wait_for_timeout(2500)
        dlg = await page.evaluate(
            "() => { const d=document.querySelector('.ds-dlg'); if(!d) return 'NO_DLG';"
            " const files=[...document.querySelectorAll('.ds-dlg-file .ds-dlg-name')].map(e=>e.textContent);"
            " return JSON.stringify(files); }")
        log(f"[dialog] files: {dlg}")
        await page.screenshot(path=f"{SHOT}/ld02_dialog.png")

        # Select internal.vti and Open.
        sel = await page.evaluate(
            "() => { const r=[...document.querySelectorAll('.ds-dlg-file')]"
            ".find(x=>x.textContent.includes('internal.vti')); if(!r) return 'NO_ROW';"
            " r.click(); return 'selected'; }")
        log(f"[dialog] select internal.vti: {sel}")
        await page.wait_for_timeout(600)
        await page.screenshot(path=f"{SHOT}/ld03_selected.png")
        await click_text(page, "Open")
        log("clicked Open -> load_dataset fired; polling for controls to enable...")

        # Poll up to ~150s for the controls to enable (backend load + voxelise).
        enabled = False
        for i in range(30):
            await page.wait_for_timeout(5000)
            d = await temp_disabled(page)
            if d is False:
                enabled = True
                log(f"[load] controls ENABLED after ~{(i+1)*5}s")
                break
        if not enabled:
            log("[load] controls did NOT enable within ~150s")
        await page.screenshot(path=f"{SHOT}/ld04_after_load.png")

        # Temp -> volume renders.
        await click_text(page, "Temp")
        log("waiting 78s for voxelisation...")
        await page.wait_for_timeout(78000)
        await page.screenshot(path=f"{SHOT}/ld05_temp.png")

        # Isolate volume (hide geometry).
        await click_text(page, "Geometry")
        await page.wait_for_timeout(1500)
        hid = await page.eval_on_selector_all(
            ".tree input.node-vis",
            "els => { let k=0; els.forEach(b=>{ if(b.checked){ b.click(); k++; } }); return k; }")
        log(f"unchecked {hid} geometry rows")
        await page.wait_for_timeout(3000)
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)

        # Show ROI box, shrink to central region, Clip.
        await click_text(page, "Show ROI Box")
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/ld06_roibox.png")
        for idx, val in ((0, 10), (1, 20), (4, 6), (5, 14)):
            r = await page.evaluate(SET_REACT, [".roi-box input[type=number]", idx, val])
            log(f"set ROI input {r}")
            await page.wait_for_timeout(400)
        await page.wait_for_timeout(2500)
        await click_text(page, "Clip Volume to ROI")
        for t in (35, 65, 95):
            prev = 0 if t == 35 else (35 if t == 65 else 65)
            await page.wait_for_timeout((t - prev) * 1000)
            await page.screenshot(path=f"{SHOT}/ld07_clipped_{t}s.png")
            log(f"shot ld07_clipped_{t}s")

        await browser.close()
        log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
