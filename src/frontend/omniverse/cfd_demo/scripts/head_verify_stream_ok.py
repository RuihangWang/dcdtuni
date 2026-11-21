#!/usr/bin/env python3
"""Minimal single-client stream health check after a Kit restart. Proves the WebRTC
video streams to ONE fresh browser (no NVST_R_BUSY, not a black frame) and the scene
renders. Connect -> screenshot the live viewport -> Load CFD Geometry -> screenshot the
placed scene. Then CLOSE so the single client slot is free for the real user.
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_stream")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = os.environ.get("KIT_LOG", "/tmp/dc_cfd_demo.kit.log")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m): print(f"[st] {m}", flush=True)
def ksize():
    try: return os.path.getsize(KITLOG)
    except OSError: return 0
def kcount(pat, pos=0):
    try:
        with open(KITLOG, "rb") as f:
            f.seek(pos); d = f.read().decode("utf-8", "replace")
        return len(re.findall(pat, d))
    except OSError: return -1


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'"); return True
    except Exception as e:
        log(f"click '{text}' FAILED: {type(e).__name__}"); return False


async def main():
    from playwright.async_api import async_playwright
    connect_pos = ksize()
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, headless=False,
            args=["--disable-gpu","--use-gl=swiftshader","--no-sandbox","--disable-dev-shm-usage",
                  "--window-size=1920,1080","--autoplay-policy=no-user-gesture-required"])
        page = await (await b.new_context(viewport={"width":1920,"height":1080})).new_page()
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"goto note: {type(e).__name__}")
        await page.wait_for_timeout(14000)   # let WebRTC connect + first frames arrive
        await page.screenshot(path=f"{SHOT}/st_1_connected.png")
        busy_at_connect = kcount(r"NVST_R_BUSY", connect_pos)
        log(f"NVST_R_BUSY since my connect (want 0): {busy_at_connect}")

        # Load geometry -> confirm the stream renders the placed scene.
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
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/st_2_scene.png")
        log(f"scene loaded: {loaded}")
        log(f"total NVST_R_BUSY during my session (want ~0): {kcount(r'NVST_R_BUSY', connect_pos)}")
        await b.close(); log("DONE (browser closed — slot free for the real user)")


if __name__ == "__main__":
    asyncio.run(main())
