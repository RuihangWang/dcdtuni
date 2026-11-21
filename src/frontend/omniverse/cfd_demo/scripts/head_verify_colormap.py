#!/usr/bin/env python3
"""Focused head-browser check of the VOLUME colormap vividness (no clip steps).

  1. load scene (~120s), load internal.vtu (~30s), Temp -> volume.
  2. screenshot cv0_full (volume + grey racks, the user's view).
  3. hide ALL geometry (Geometry tab -> uncheck node-vis) so ONLY the volume shows
     against the dark background -> screenshot cv1_volume_only (clean colour read).
  4. Velocity -> cv2_velocity_full, hide stays -> cv3_velocity_only.

Decisive read = the kit-log 'volume rebuilt field=... domain_set=' receipt + the
pixel-colour analysis of cv1/cv3 (must show saturated blue->red, not grey haze).

Run:
  HEAD_URL=http://localhost:8081 HEAD_SHOT_DIR=/tmp/head_cmap \
  DISPLAY=:99 $BROWSER_USE_PY \
      src/frontend/omniverse/cfd_demo/scripts/head_verify_colormap.py
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_cmap")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = os.environ.get("KIT_LOG", "/tmp/dc_cfd_demo.kit.log")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m): print(f"[cmap] {m}", flush=True)
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

        # Temp -> full view
        pos = ksize()
        await click_text(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T roi=False", "Temp", pos, 300)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/cv0_temp_full.png")
        log("EXTENT/domain: " + str(kgrep(r"volume rebuilt field=T roi=False", pos)[-1:]))

        # Hide ALL geometry so only the volume shows (clean colour read)
        await click_text(page, "Geometry"); await page.wait_for_timeout(1200)
        hid = await page.eval_on_selector_all(
            ".tree input.node-vis",
            "els => { let k=0; els.forEach(b=>{ if(b.checked){ b.click(); k++; } }); return k; }")
        log(f"unchecked {hid} geometry rows")
        await page.wait_for_timeout(4000)
        await click_text(page, "Post-processing"); await page.wait_for_timeout(1200)
        await page.screenshot(path=f"{SHOT}/cv1_temp_volume_only.png")

        # Velocity -> full + isolated
        pos = ksize()
        await click_text(page, "Velocity")
        await wait_k(page, r"volume rebuilt field=U roi=False", "Velocity", pos, 300)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/cv2_velocity_volume_only.png")

        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
