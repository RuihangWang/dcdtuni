#!/usr/bin/env python3
"""Capture the 4 deliverable screenshots on the FIXED Kit (rotating-path re-voxelisation):
  1. roi4_1_temp.png         — Temperature volume (full)
  2. roi4_2_velocity.png     — Velocity |U| volume (full)
  3. roi4_3_velocity_roi.png — Velocity clipped to ROI (X left half)
  4. roi4_4_temp_roi.png     — Temperature clipped to ROI (X left half)

Geometry hidden for clean field views. ROI = LEFT half X[0,15.6] of the room AABB
X[0,31.2] Y[0,5] Z[0,19.2]. Decisive reads = the kit-log 'volume rebuilt ... roi=...'.
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_roi4")
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
    return 'OK #' + idx + '=' + el.value;
}
"""


def log(m): print(f"[roi4] {m}", flush=True)
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


async def set_box(page, mn, mx):
    vals = [mn[0], mx[0], mn[1], mx[1], mn[2], mx[2]]
    for idx, v in enumerate(vals):
        await page.evaluate(SET_REACT, [".roi-box input[type=number]", idx, v])
        await page.wait_for_timeout(300)
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
        await page.wait_for_timeout(11000)

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

        # Hide all geometry so ONLY the volume shows against the dark background.
        await click_text(page, "Geometry"); await page.wait_for_timeout(1200)
        hid = await page.eval_on_selector_all(
            ".tree input.node-vis",
            "els => { let k=0; els.forEach(b=>{ if(b.checked){ b.click(); k++; } }); return k; }")
        log(f"unchecked {hid} geometry rows")
        await click_text(page, "Post-processing"); await page.wait_for_timeout(1500)

        # 1. Temperature (full)
        pos = ksize()
        await click_text(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T roi=False", "Temp full", pos, 300)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/roi4_1_temp.png")

        # 2. Velocity (full)
        pos = ksize()
        await click_text(page, "Velocity")
        await wait_k(page, r"volume rebuilt field=U roi=False", "Velocity full", pos, 300)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/roi4_2_velocity.png")

        # 3. Velocity clipped to ROI (LEFT half)
        await click_text(page, "Show ROI Box"); await page.wait_for_timeout(2500)
        await set_box(page, (0,0,0), (15.6,5,19.2))
        await page.wait_for_timeout(1500)
        pos = ksize()
        await click_text(page, "Clip Volume to ROI")
        ok = await wait_k(page, r"volume rebuilt field=U roi=True", "Velocity CLIP (left half)", pos, 300)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/roi4_3_velocity_roi.png")
        e3 = kgrep(r"volume rebuilt field=U roi=True", pos)
        log("velocity-clip extent: " + str(e3[-1:] if e3 else "NONE"))

        # 4. Temperature clipped (clip state persists across the field switch)
        pos = ksize()
        await click_text(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T roi=True", "Temp CLIP (left half)", pos, 300)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/roi4_4_temp_roi.png")
        e4 = kgrep(r"volume rebuilt field=T roi=True", pos)
        log("temp-clip extent: " + str(e4[-1:] if e4 else "NONE"))

        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
