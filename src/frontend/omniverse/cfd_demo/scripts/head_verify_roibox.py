#!/usr/bin/env python3
"""Reproduce the user's empty-clip-box report and find the TRUE cause.

Sequence (assumes scene + dataset already loaded by head_verify_fullflow, but we
do the full load to be self-contained):
  1. load scene, load internal.vtu, Temp.
  2. Show ROI box, Clip ON (box at default/full).
  3. Move box to (0,0,0)..(5,5,5)  -- the USER's corner box. Shot r1.
  4. Move box to (10,0,6)..(20,5,14) -- a known-WARM mid-room box. Shot r2.

Decisive read = the kit-log 'volume rebuilt ... extent=' after each box move:
  * extent collapses to the new box each time -> box-change DOES re-voxelise;
    an empty r1 then means the corner is just cool/transparent (colormap issue).
  * extent stays at the old/previous box -> box-change-while-clipped does NOT
    re-voxelise (the rel-unchanged bug) -> the volume is stale.
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_roibox")
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
    if (!el) return 'NO_EL ' + sel + ' #' + idx;
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
          .set.call(el, String(v));
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return 'OK #' + idx + '=' + el.value;
}
"""


def log(m): print(f"[roibox] {m}", flush=True)
def ksize():
    try: return os.path.getsize(KITLOG)
    except OSError: return 0
def khas(pat, pos):
    try:
        with open(KITLOG, "rb") as f:
            f.seek(pos); d = f.read().decode("utf-8", "replace")
        return re.search(pat, d) is not None
    except OSError: return False
def kgrep(pat, pos):
    """Return matching lines after byte pos."""
    try:
        with open(KITLOG, "rb") as f:
            f.seek(pos); d = f.read().decode("utf-8", "replace")
        return [ln for ln in d.splitlines() if re.search(pat, ln)]
    except OSError: return []


async def wait_k(page, pat, label, pos, max_s=300):
    for i in range(max_s // 5):
        await page.wait_for_timeout(5000)
        if khas(pat, pos):
            log(f"[kitlog] '{label}' after ~{(i+1)*5}s"); return True
    log(f"[kitlog] '{label}' NOT seen in {max_s}s"); return False


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'"); return True
    except Exception as e:
        log(f"click '{text}' FAILED: {type(e).__name__}"); return False


async def set_box(page, mn, mx):
    """idx order: Wmin,Wmax,Hmin,Hmax,Dmin,Dmax = 0..5."""
    vals = [mn[0], mx[0], mn[1], mx[1], mn[2], mx[2]]
    for idx, v in enumerate(vals):
        r = await page.evaluate(SET_REACT, [".roi-box input[type=number]", idx, v])
        await page.wait_for_timeout(250)
    log(f"set box {tuple(mn)}..{tuple(mx)}")


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
        await page.wait_for_timeout(10000)

        # Load scene
        await click_text(page, "Load CFD Geometry")
        await page.wait_for_timeout(2500)
        await page.evaluate(
            "() => { const r=[...document.querySelectorAll('.ds-dlg-file')]"
            ".find(x=>x.textContent.includes('GTC2025.json')); if(r)"
            " r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); }")
        for i in range(60):
            await page.wait_for_timeout(5000)
            rows = await page.evaluate("() => document.querySelectorAll('.tree .node-name').length")
            if rows: log(f"scene loaded ({rows} rows) ~{(i+1)*5}s"); break

        # Load dataset
        await click_text(page, "Post-processing"); await page.wait_for_timeout(1200)
        await click_text(page, "Load dataset"); await page.wait_for_timeout(2500)
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

        # Temp
        pos = ksize()
        await click_text(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T roi=False", "Temp", pos, 300)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=f"{SHOT}/r0_temp.png")

        # Show ROI box + Clip ON (box still full/default)
        await click_text(page, "Show ROI Box"); await page.wait_for_timeout(2500)
        pos = ksize()
        await click_text(page, "Clip Volume to ROI")
        await wait_k(page, r"volume rebuilt field=T roi=True", "Clip ON (default box)", pos, 300)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=f"{SHOT}/r0b_clip_on_defaultbox.png")
        log("EXTENT after clip-on(default): " + str(kgrep(r"volume rebuilt field=T roi=True", pos)[-1:]))

        # ---- USER'S BOX: (0,0,0)..(5,5,5) while clip is ON ----
        pos = ksize()
        await set_box(page, (0,0,0), (5,5,5))
        seen = await wait_k(page, r"volume rebuilt field=T roi=True", "rebuild after USER box", pos, 200)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/r1_user_box_0-5.png")
        ext1 = kgrep(r"volume rebuilt field=T roi=True", pos)
        log(f"USER BOX (0,0,0)..(5,5,5): rebuild_fired={seen}")
        log("  EXTENT: " + str(ext1[-1:] if ext1 else "NO REBUILD LINE"))

        # ---- KNOWN-WARM BOX: (10,0,6)..(20,5,14) while clip is ON ----
        pos = ksize()
        await set_box(page, (10,0,6), (20,5,14))
        seen2 = await wait_k(page, r"volume rebuilt field=T roi=True", "rebuild after WARM box", pos, 200)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/r2_warm_box_10-20.png")
        ext2 = kgrep(r"volume rebuilt field=T roi=True", pos)
        log(f"WARM BOX (10,0,6)..(20,5,14): rebuild_fired={seen2}")
        log("  EXTENT: " + str(ext2[-1:] if ext2 else "NO REBUILD LINE"))

        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
