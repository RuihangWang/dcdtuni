#!/usr/bin/env python3
"""Head-browser verification of the STREAMLINES feature (native kit-cae
CreateCaeVizStreamlines, GPU Warp RK4(5)). Drives the REAL UI on Xvfb :99:

  1. Load CFD Geometry + simulate (dataset)
  2. Add Streamlines            -> draggable blue seed sphere    -> sl_1_seed.png
  3. Apply                      -> T-coloured streamlines        -> sl_2_streamlines_T.png
  4. Colour: |U|                -> velocity-coloured streamlines -> sl_3_streamlines_U.png
  5. Colour: Temp (back to T)
  6. Show ROI Box + Z front-half + Clip -> streamlines confined  -> sl_4_streamlines_zclip.png

The decisive receipt is the Kit log 'streamlines generated field=... roi=... extent=[...]'.
The Z-clip acceptance = the extent's Z max <= ~9.6 (the ROI box front half), proving lines
do not exceed the ROI ("ROI is god").
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_sl")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = os.environ.get("KIT_LOG", "/tmp/dc_cfd_demo.kit.log")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)

SET_REACT = """
([sel, idx, v]) => {
    const el = document.querySelectorAll(sel)[idx];
    if (!el) return 'NO_EL';
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
          .set.call(el, String(v));
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return 'OK ' + el.type + '=' + el.value;
}
"""


def log(m): print(f"[sl] {m}", flush=True)
def ksize():
    try: return os.path.getsize(KITLOG)
    except OSError: return 0
def khas(pat, pos):
    try:
        with open(KITLOG, "rb") as f:
            f.seek(pos); d = f.read().decode("utf-8", "replace")
        return re.search(pat, d) is not None
    except OSError: return False
def kgrep(pat, pos=0):
    try:
        with open(KITLOG, "rb") as f:
            f.seek(pos); d = f.read().decode("utf-8", "replace")
        return [ln for ln in d.splitlines() if re.search(pat, ln)]
    except OSError: return []


async def wait_k(page, pat, label, pos, max_s=300):
    for i in range(max_s // 5):
        await page.wait_for_timeout(5000)
        if khas(pat, pos):
            log(f"[kitlog] '{label}' ~{(i+1)*5}s"); return True
    log(f"[kitlog] '{label}' NOT seen in {max_s}s"); return False


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'"); return True
    except Exception as e:
        log(f"click '{text}' FAILED: {type(e).__name__}"); return False


async def click_button(page, name, timeout=8000):
    """Click a BUTTON by exact trimmed text via JS (so 'Apply' in the help note is not
    matched, and a native .click() fires React's onClick without Playwright's
    actionability wait). Reports OK / DISABLED / NOT_FOUND for diagnosis."""
    r = await page.evaluate(
        """(t) => {
            const btns = [...document.querySelectorAll('button')];
            const exact = btns.filter(x => x.textContent.trim() === t);
            if (!exact.length) return 'NOT_FOUND';
            const tgt = exact.find(x => !x.disabled);
            if (!tgt) return 'DISABLED';
            tgt.click();
            return 'OK';
        }""", name)
    log(f"js-click button '{name}' -> {r}")
    return r == "OK"


async def set_box(page, mn, mx):
    # ROI section is the FIRST .roi-box -> its 6 inputs are indices 0..5.
    vals = [mn[0], mx[0], mn[1], mx[1], mn[2], mx[2]]
    for idx, v in enumerate(vals):
        await page.evaluate(SET_REACT, [".roi-box input[type=number]", idx, v])
        await page.wait_for_timeout(300)
    log(f"set ROI box {tuple(mn)}..{tuple(mx)}")


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, headless=False,
            args=["--disable-gpu","--use-gl=swiftshader","--no-sandbox","--disable-dev-shm-usage",
                  "--window-size=1920,1080","--autoplay-policy=no-user-gesture-required"])
        page = await (await b.new_context(viewport={"width":1920,"height":1080})).new_page()
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"goto note: {type(e).__name__}")
        await page.wait_for_timeout(11000)

        # --- scene + dataset ---
        await click_text(page, "Load CFD Geometry")
        await page.wait_for_timeout(2500)
        await page.evaluate(
            "() => { const r=[...document.querySelectorAll('.ds-dlg-file')]"
            ".find(x=>x.textContent.includes('GTC2025.json')); if(r)"
            " r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); }")
        loaded = False
        for i in range(70):
            await page.wait_for_timeout(5000)
            rows = await page.evaluate("() => document.querySelectorAll('.tree .node-name').length")
            if rows: loaded = True; log(f"scene loaded ({rows} rows) ~{(i+1)*5}s"); break
        if not loaded:
            log("scene NOT loaded; abort"); await b.close(); return

        await click_text(page, "Post-processing"); await page.wait_for_timeout(1500)
        await click_text(page, "simulate"); await page.wait_for_timeout(2500)
        pos = ksize()
        await page.evaluate(
            "() => { const rows=[...document.querySelectorAll('.ds-dlg-file')];"
            " const r=rows.find(x=>{const n=x.querySelector('.ds-dlg-name');"
            "  return n && n.textContent.trim().endsWith('.vtu');})||rows[0];"
            " if(r) r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); }")
        await wait_k(page, r"load_dataset OK", "load_dataset OK", pos, 600)
        for i in range(24):
            await page.wait_for_timeout(5000)
            dis = await page.evaluate(
                "() => { const x=[...document.querySelectorAll('button.seg-btn')]"
                ".find(b=>b.textContent.trim()=='Temp'); return x?x.disabled:'NB'; }")
            if dis is False: break

        # --- 1. Add Streamlines -> seed sphere preview ---
        pos = ksize()
        await click_button(page, "Add Streamlines")
        await wait_k(page, r"seed sphere visible=True", "seed sphere shown", pos, 40)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/sl_1_seed.png")

        # --- 2. Apply -> T-coloured streamlines ---
        dbg = await page.evaluate(
            "() => [...document.querySelectorAll('button')]"
            ".map(b => b.textContent.trim() + (b.disabled ? '[disabled]' : ''))"
            ".filter(t => /Apply|Streamlines|Colour|Seed|Re-apply|Tracing/.test(t))")
        log(f"streamline buttons: {dbg}")
        pos = ksize()
        await click_button(page, "Apply")
        ok2 = await wait_k(page, r"streamlines generated field=T", "streamlines T", pos, 240)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/sl_2_streamlines_T.png")
        e2 = kgrep(r"streamlines generated field=T roi=False", pos)
        log("T extent: " + str(e2[-1:] if e2 else "NONE"))

        # --- 3. Colour: |U| -> velocity-coloured ---
        pos = ksize()
        await click_button(page, "Colour: |U|")
        await wait_k(page, r"streamlines generated field=U", "streamlines U", pos, 240)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/sl_3_streamlines_U.png")

        # --- 4. back to Temp, then ROI Z front-half + Clip ---
        pos = ksize()
        await click_button(page, "Colour: Temp")
        await wait_k(page, r"streamlines generated field=T", "streamlines T (2)", pos, 240)
        await page.wait_for_timeout(2000)

        await click_text(page, "Show ROI Box"); await page.wait_for_timeout(2500)
        await set_box(page, (0, 0, 0), (31.2, 5, 9.6))   # Z front half
        await page.wait_for_timeout(1500)
        pos = ksize()
        await click_text(page, "Clip Volume to ROI")
        await wait_k(page, r"streamlines generated field=T roi=True", "streamlines Z-CLIP", pos, 300)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=f"{SHOT}/sl_4_streamlines_zclip.png")

        # --- receipts ---
        log("================= KIT-LOG RECEIPTS =================")
        for pat in [r"seed sphere visible=True", r"streamlines generated field=T roi=False",
                    r"streamlines generated field=U roi=False",
                    r"streamlines generated field=T roi=True"]:
            hits = kgrep(pat)
            log(f"[{pat}] -> " + (hits[-1] if hits else "NONE"))
        log("===================================================")
        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
