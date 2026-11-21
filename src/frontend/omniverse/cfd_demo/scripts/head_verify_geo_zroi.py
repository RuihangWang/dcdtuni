#!/usr/bin/env python3
"""Re-capture WITH GEOMETRY VISIBLE (racks/ACUs shown; only the room-shell auto-hidden
by handle_set_volume) on the FIXED Kit, ROI cut in the Z direction (front half), plus a
colorbar range-adjustment demo.

Room AABB: X[0,31.2] Y[0,5] Z[0,19.2]. Z-half (front) = (0,0,0)..(31.2,5,9.6).

Shots:
  geo_1_temp.png            — Temperature (full), geometry visible
  geo_2_velocity.png        — Velocity |U| (full, domain 0-4.5), geometry visible
  geo_3_velocity_range.png  — SAME velocity, High slider tightened to 1.5 m/s (colorbar
                              range adjustment demo: the slow-air bulk now spreads across
                              the full ramp + the jets read solid red)
  geo_4_velocity_zroi.png   — Velocity clipped to Z front half
  geo_5_temp_zroi.png       — Temperature clipped to Z front half
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_geo_zroi")
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


def log(m): print(f"[geozroi] {m}", flush=True)
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


async def set_high(page, v):
    # CFD Volume Low/High are the only input[type=range] on the Post-processing tab.
    r = await page.evaluate(SET_REACT, ["input[type=range]", 1, v])
    log(f"set High slider -> {v}  ({r})")
    await page.wait_for_timeout(1200)


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

        # NOTE: geometry is intentionally LEFT VISIBLE. Clicking a field auto-hides only
        # the room-shell (handle_set_volume -> _set_geometry_hidden(['Room'])); racks/ACUs
        # stay as context.

        # 1. Temperature (full)
        pos = ksize()
        await click_text(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T roi=False", "Temp full", pos, 300)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/geo_1_temp.png")

        # 2. Velocity (full, default domain 0-4.5)
        pos = ksize()
        await click_text(page, "Velocity")
        await wait_k(page, r"volume rebuilt field=U roi=False", "Velocity full", pos, 300)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/geo_2_velocity.png")

        # 3. Colorbar range demo: tighten High to 1.5 m/s (live, no rebuild)
        await set_high(page, 1.5)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=f"{SHOT}/geo_3_velocity_range.png")
        rng = kgrep(r"setVolumeRange", pos)
        log("range cmds: " + str(rng[-2:] if rng else "NONE"))
        # reset High back to 4.5 so the clip shots use the default domain
        await set_high(page, 4.5)
        await page.wait_for_timeout(1500)

        # 4. Velocity clipped to Z FRONT HALF
        await click_text(page, "Show ROI Box"); await page.wait_for_timeout(2500)
        await set_box(page, (0,0,0), (31.2,5,9.6))
        await page.wait_for_timeout(1500)
        pos = ksize()
        await click_text(page, "Clip Volume to ROI")
        await wait_k(page, r"volume rebuilt field=U roi=True", "Velocity CLIP (Z front half)", pos, 300)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/geo_4_velocity_zroi.png")
        e4 = kgrep(r"volume rebuilt field=U roi=True", pos)
        log("velocity Z-clip extent: " + str(e4[-1:] if e4 else "NONE"))

        # 5. Temperature clipped (clip persists across the field switch)
        pos = ksize()
        await click_text(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T roi=True", "Temp CLIP (Z front half)", pos, 300)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/geo_5_temp_zroi.png")
        e5 = kgrep(r"volume rebuilt field=T roi=True", pos)
        log("temp Z-clip extent: " + str(e5[-1:] if e5 else "NONE"))

        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
