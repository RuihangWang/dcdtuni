#!/usr/bin/env python3
"""HEAD-browser verification of the full gated flow + ROI clip-to-box fix.

  1. Default: NO geometry list; Post-processing dataset load is gated.
  2. "Load CFD Geometry" -> scene-JSON dialog lists GTC2025.json.
  3. Pick it -> backend builds + reveals -> geometry list appears.
  4. Post-processing: "Load dataset" now enabled -> pick internal.vti -> controls enable.
  5. Temp -> volume renders.
  6. ROI clip -> volume restricts to the box (cache-drop removal).

Same true-head recipe as head_verify.py. Run:
  HEAD_URL=http://localhost:8081 HEAD_SHOT_DIR=/tmp/head_fullflow \
  DISPLAY=:99 $BROWSER_USE_PY \
      src/frontend/omniverse/cfd_demo/scripts/head_verify_fullflow.py
"""
import asyncio
import shutil
import pathlib
import os

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_fullflow")
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


KITLOG = os.environ.get("KIT_LOG", "/tmp/dc_cfd_demo.kit.log")


def log(m):
    print(f"[fullflow] {m}", flush=True)


def kitlog_size():
    try:
        return os.path.getsize(KITLOG)
    except OSError:
        return 0


def kitlog_has(pattern, since_pos):
    """True if regex `pattern` appears in the kit log after byte `since_pos`."""
    import re
    try:
        with open(KITLOG, "rb") as f:
            f.seek(since_pos)
            data = f.read().decode("utf-8", "replace")
        return re.search(pattern, data) is not None
    except OSError:
        return False


async def wait_kitlog(page, pattern, label, max_s=300):
    """Poll the kit log (5s cadence) until `pattern` appears, up to max_s. The .vtu
    voxelise has no UI completion signal, so the backend receipt is ground truth."""
    pos = wait_kitlog._pos  # byte offset captured by the caller via mark_kitlog()
    for i in range(max_s // 5):
        await page.wait_for_timeout(5000)
        if kitlog_has(pattern, pos):
            log(f"[kitlog] '{label}' after ~{(i + 1) * 5}s")
            return True
    log(f"[kitlog] '{label}' NOT seen within {max_s}s")
    return False


def mark_kitlog():
    wait_kitlog._pos = kitlog_size()


wait_kitlog._pos = 0


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


async def tree_rows(page):
    return await page.evaluate(
        "() => document.querySelectorAll('.tree .node-name').length")


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

        # 1) Default: geometry list empty + dataset load gated.
        empty = await page.evaluate(
            "() => ({ tree: document.querySelectorAll('.tree .node-name').length,"
            " emptyHint: !!document.querySelector('.tree-empty') })")
        log(f"[default] tree rows={empty['tree']} emptyHint={empty['emptyHint']}")
        await page.screenshot(path=f"{SHOT}/cv01_default_empty.png")

        # 2) Open the scene dialog.
        await click_text(page, "Load CFD Geometry")
        await page.wait_for_timeout(2500)
        dlg = await page.evaluate(
            "() => { const d=document.querySelector('.ds-dlg'); if(!d) return 'NO_DLG';"
            " return JSON.stringify([...document.querySelectorAll('.ds-dlg-file .ds-dlg-name')].map(e=>e.textContent)); }")
        log(f"[scene dlg] files: {dlg}")
        await page.screenshot(path=f"{SHOT}/cv02_scene_dialog.png")

        # 3) Pick GTC2025.json. Double-click the row -> onPick (a bubbling dblclick;
        # a scripted .click() does NOT reach React's portal-rooted delegation).
        sel = await page.evaluate(
            "() => { const r=[...document.querySelectorAll('.ds-dlg-file')]"
            ".find(x=>x.textContent.includes('GTC2025.json')); if(!r) return 'NO_ROW';"
            " r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); return 'dblclicked'; }")
        log(f"[scene dlg] dblclick GTC2025.json: {sel}")
        log("loadScene fired; polling for geometry list (build ~2-3min)...")
        built = False
        for i in range(60):  # up to ~300s
            await page.wait_for_timeout(5000)
            rows = await tree_rows(page)
            if rows and rows > 0:
                built = True
                log(f"[scene] geometry list appeared ({rows} rows) after ~{(i+1)*5}s")
                break
        await page.screenshot(path=f"{SHOT}/cv03_scene_loaded.png")
        if not built:
            log("[scene] geometry list did NOT appear within ~300s")

        # 4) Post-processing: dataset load now enabled.
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)
        ld = await page.evaluate(
            "() => { const b=[...document.querySelectorAll('button')].find(x=>/Load dataset/.test(x.textContent));"
            " return b? ('disabled='+b.disabled) : 'NO_BTN'; }")
        log(f"[postproc] Load dataset button: {ld}")
        await page.screenshot(path=f"{SHOT}/cv04_postproc_unlocked.png")

        # 5) Load dataset dialog -> the .vtu (the real ~1M-cell unstructured mesh).
        await click_text(page, "Load dataset")
        await page.wait_for_timeout(2500)
        dfiles = await page.evaluate(
            "() => JSON.stringify([...document.querySelectorAll('.ds-dlg-file .ds-dlg-name')].map(e=>e.textContent))")
        log(f"[data dlg] files: {dfiles}")
        mark_kitlog()
        dsel = await page.evaluate(
            "() => { const rows=[...document.querySelectorAll('.ds-dlg-file')];"
            " const r=rows.find(x=>{const n=x.querySelector('.ds-dlg-name');"
            "   return n && n.textContent.trim().endsWith('.vtu');}) || rows[0];"
            " if(!r) return 'NO_ROW';"
            " r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));"
            " return 'dblclicked:'+((r.querySelector('.ds-dlg-name')||{}).textContent||''); }")
        log(f"[data dlg] dblclick dataset: {dsel}")
        log("load_dataset fired (large .vtu — read+voxelise can take minutes)...")
        # The blocking overlay should now cover the app; confirm it exists.
        await page.wait_for_timeout(1500)
        ov = await page.evaluate("() => !!document.querySelector('.load-overlay')")
        log(f"[blocking-ux] load-overlay present during load: {ov}")
        await page.screenshot(path=f"{SHOT}/cv05a_load_overlay.png")
        # Ground truth = backend receipt; .vti was seconds, .vtu is minutes.
        await wait_kitlog(page, r"load_dataset OK", "load_dataset OK", max_s=600)
        # Then the UI flips status->ready and enables the controls.
        enabled = False
        for i in range(24):  # up to ~120s after the receipt
            await page.wait_for_timeout(5000)
            if (await temp_disabled(page)) is False:
                enabled = True
                log(f"[data] controls ENABLED after ~{(i+1)*5}s")
                break
        await page.screenshot(path=f"{SHOT}/cv05_dataset_loaded.png")

        # 6) Temp -> volume renders (wait for the backend rebuild receipt, not a guess).
        mark_kitlog()
        await click_text(page, "Temp")
        await wait_kitlog(page, r"volume rebuilt field=T roi=False", "Temp voxelise", max_s=300)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/cv06_temp.png")

        # 7) ROI clip-to-box.
        await click_text(page, "Geometry")
        await page.wait_for_timeout(1500)
        hid = await page.eval_on_selector_all(
            ".tree input.node-vis",
            "els => { let k=0; els.forEach(b=>{ if(b.checked){ b.click(); k++; } }); return k; }")
        log(f"unchecked {hid} geometry rows")
        await page.wait_for_timeout(3000)
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)
        await click_text(page, "Show ROI Box")
        await page.wait_for_timeout(4000)
        for idx, val in ((0, 10), (1, 20), (4, 6), (5, 14)):
            r = await page.evaluate(SET_REACT, [".roi-box input[type=number]", idx, val])
            log(f"set ROI input {r}")
            await page.wait_for_timeout(400)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=f"{SHOT}/cv07_roibox_shrunk.png")
        # Clip -> re-voxelise WITHIN the box. Wait for the roi=True rebuild receipt,
        # then screenshot: the volume must now be restricted to the box.
        mark_kitlog()
        await click_text(page, "Clip Volume to ROI")
        await wait_kitlog(page, r"volume rebuilt field=T roi=True", "ROI clip voxelise", max_s=300)
        for t in (10, 40, 70):
            await page.wait_for_timeout((t if t == 10 else 30) * 1000)
            await page.screenshot(path=f"{SHOT}/cv08_clipped_{t}s.png")
            log(f"shot cv08_clipped_{t}s")

        await browser.close()
        log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
