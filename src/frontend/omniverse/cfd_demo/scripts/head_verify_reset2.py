#!/usr/bin/env python3
"""Compact re-verify that a page refresh leaves a TRULY empty viewport (the floor/room
groups — siblings of /World/Geometry — are now hidden too).

  1. connect -> resetScene -> rs2_1 (empty).
  2. Load CFD Geometry -> rs2_2 (full scene incl. floor).
  3. page.reload() -> resetScene -> rs2_3 MUST be empty: no racks AND no floor slab.
  4. Load CFD Geometry -> rs2_4 (full scene restored).
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_reset2")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = os.environ.get("KIT_LOG", "/tmp/dc_cfd_demo.kit.log")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m): print(f"[rs2] {m}", flush=True)
def ksize():
    try: return os.path.getsize(KITLOG)
    except OSError: return 0
def khas(pat, pos):
    try:
        with open(KITLOG, "rb") as f:
            f.seek(pos); d = f.read().decode("utf-8", "replace")
        return re.search(pat, d) is not None
    except OSError: return False
def kcount(pat):
    try:
        with open(KITLOG, "rb") as f:
            return len(re.findall(pat, f.read().decode("utf-8", "replace")))
    except OSError: return -1


async def wait_k(page, pat, label, pos, max_s=120):
    for i in range(max_s // 5):
        await page.wait_for_timeout(5000)
        if khas(pat, pos):
            log(f"[kitlog] '{label}' ~{(i+1)*5}s"); return True
    log(f"[kitlog] '{label}' NOT seen in {max_s}s"); return False


async def load_geometry(page):
    try:
        await page.get_by_text("Load CFD Geometry", exact=False).first.click(timeout=8000)
    except Exception as e:
        log(f"click Load FAILED {type(e).__name__}")
    await page.wait_for_timeout(2500)
    await page.evaluate(
        "() => { const r=[...document.querySelectorAll('.ds-dlg-file')]"
        ".find(x=>x.textContent.includes('GTC2025.json')); if(r)"
        " r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); }")
    for i in range(70):
        await page.wait_for_timeout(5000)
        rows = await page.evaluate("() => document.querySelectorAll('.tree .node-name').length")
        if rows: log(f"scene loaded ({rows} rows) ~{(i+1)*5}s"); return True
    log("scene NOT loaded"); return False


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
        await wait_k(page, r"scene reset to clean slate", "reset on connect", 0, 60)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/rs2_1_fresh.png")

        await load_geometry(page)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/rs2_2_loaded.png")

        log("reloading page…")
        try:
            await page.reload(wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"reload note: {type(e).__name__}")
        rpos = ksize()
        await wait_k(page, r"scene reset to clean slate", "reset after reload", rpos, 90)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=f"{SHOT}/rs2_3_after_refresh.png")

        await load_geometry(page)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/rs2_4_reloaded.png")

        log(f"total resets (want 2): {kcount(r'scene reset to clean slate')}")
        log(f"crashes (want 0): {kcount(r'Traceback|Segmentation|core dumped')}")
        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
