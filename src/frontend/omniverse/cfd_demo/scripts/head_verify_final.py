#!/usr/bin/env python3
"""Head-browser verification of the user's three asks:
  1. Hide ONLY the RoomShell (racks stay visible) -> is the Temp field vivid?
        fv0_temp_default.png   (racks visible, only RoomShell hidden)
  2. Autoscale button -> fits the colour domain to the field's full min/max.
        fv1_autoscale.png      (+ kit-log setVolumeRange receipt)
  3. ROI clip to the X-axis LEFT half -> only half the field renders, drawn vivid.
        fv2_clip_xhalf.png     (+ 'volume rebuilt ... extent=' receipt X ~ [0,15.6])

Run:
  HEAD_URL=http://localhost:8081 HEAD_SHOT_DIR=/tmp/head_final \
  DISPLAY=:99 $BROWSER_USE_PY \
      src/frontend/omniverse/cfd_demo/scripts/head_verify_final.py
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_final")
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


def log(m): print(f"[final] {m}", flush=True)
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

        # (1) Temp — only the RoomShell is hidden; racks STAY visible.
        pos = ksize()
        await click_text(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T roi=False", "Temp", pos, 300)
        log("hide receipt: " + str(kgrep(r"geometry hidden for volume", pos)[-1:]))
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/fv0_temp_default.png")

        # (2) Autoscale — fit the colour domain to the field's full min/max.
        pos = ksize()
        await click_text(page, "Autoscale")
        await wait_k(page, r"command=setVolumeRange", "autoscale setVolumeRange", pos, 60)
        log("autoscale receipt: " + str(kgrep(r"command=setVolumeRange", pos)[-1:]))
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/fv1_autoscale.png")

        # Put the range back to the vivid default by re-selecting Temp (Off -> Temp)
        await click_text(page, "Off"); await page.wait_for_timeout(1500)
        pos = ksize()
        await click_text(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T roi=False", "Temp again", pos, 300)
        await page.wait_for_timeout(3000)

        # (3) ROI clip to the X-axis LEFT half.
        await click_text(page, "Show ROI Box"); await page.wait_for_timeout(2500)
        await set_box(page, (0, 0, 0), (15.6, 5, 19.2))
        await page.wait_for_timeout(1500)
        pos = ksize()
        await click_text(page, "Clip Volume to ROI")
        await wait_k(page, r"volume rebuilt field=T roi=True", "Clip X-left half", pos, 300)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/fv2_clip_xhalf.png")
        log("clip extent: " + str(kgrep(r"volume rebuilt field=T roi=True", pos)[-1:]))

        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
