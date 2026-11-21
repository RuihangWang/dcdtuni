#!/usr/bin/env python3
"""Head-browser verification of the page-refresh-resets-the-viewport feature.

  1. Connect (fresh page)  -> the app fires resetScene once -> rs_1 (empty viewport).
  2. Load CFD Geometry     -> rs_2 (scene revealed).
  3. simulate + Temp       -> rs_3 (a live IndeX volume exists — the risky teardown case).
  4. page.reload()         -> the app fires resetScene AGAIN -> rs_4 MUST be empty again
                              (geometry + grid + volume all gone), and Kit MUST still be
                              alive (no crash from retiring the live volume).
  5. Load CFD Geometry     -> rs_5 (fast re-reveal; the floor grid comes back too).

Receipts: two "scene reset to clean slate" lines (connect + reload); 8301 still bound
after the reload (no crash).
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_reset")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = os.environ.get("KIT_LOG", "/tmp/dc_cfd_demo.kit.log")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m): print(f"[rs] {m}", flush=True)
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
            d = f.read().decode("utf-8", "replace")
        return len(re.findall(pat, d))
    except OSError: return -1


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
    r = await page.evaluate(
        """(t) => {
            const btns = [...document.querySelectorAll('button')];
            const exact = btns.filter(x => x.textContent.trim() === t);
            if (!exact.length) return 'NOT_FOUND';
            const tgt = exact.find(x => !x.disabled);
            if (!tgt) return 'DISABLED';
            tgt.click(); return 'OK';
        }""", name)
    log(f"js-click button '{name}' -> {r}")
    return r == "OK"


async def load_geometry(page):
    await click_text(page, "Load CFD Geometry")
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

        # --- 1. fresh connect -> resetScene fires ---
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"goto note: {type(e).__name__}")
        await wait_k(page, r"scene reset to clean slate", "reset on connect", 0, 60)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/rs_1_fresh.png")
        log(f"resets so far (want 1): {kcount(r'scene reset to clean slate')}")

        # --- 2. load geometry ---
        await load_geometry(page)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/rs_2_loaded.png")

        # --- 3. simulate + Temp volume (the risky live-volume teardown case) ---
        await click_text(page, "Post-processing"); await page.wait_for_timeout(1500)
        spos = ksize()
        await click_button(page, "simulate"); await page.wait_for_timeout(2500)
        await page.evaluate(
            "() => { const rows=[...document.querySelectorAll('.ds-dlg-file')];"
            " const r=rows.find(x=>{const n=x.querySelector('.ds-dlg-name');"
            "  return n && n.textContent.trim().endsWith('.vtu');})||rows[0];"
            " if(r) r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); }")
        await wait_k(page, r"load_dataset OK", "load_dataset OK", spos, 600)
        for i in range(24):
            await page.wait_for_timeout(5000)
            dis = await page.evaluate(
                "() => { const x=[...document.querySelectorAll('button.seg-btn')]"
                ".find(b=>b.textContent.trim()=='Temp'); return x?x.disabled:'NB'; }")
            if dis is False: break
        tpos = ksize()
        await click_button(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T", "volume T", tpos, 240)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/rs_3_volume.png")

        # --- 4. RELOAD the page -> resetScene fires again -> viewport must be EMPTY ---
        log("reloading page (simulating a browser refresh)…")
        try:
            await page.reload(wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"reload note: {type(e).__name__}")
        rpos = ksize()
        await wait_k(page, r"scene reset to clean slate", "reset after reload", rpos, 90)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=f"{SHOT}/rs_4_after_refresh.png")
        log(f"total resets (want 2): {kcount(r'scene reset to clean slate')}")

        # --- 5. reload geometry after the refresh (fast re-reveal + grid back) ---
        await load_geometry(page)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/rs_5_reloaded.png")

        log("================= RECEIPTS =================")
        log(f"scene reset count (want 2): {kcount(r'scene reset to clean slate')}")
        log(f"volume rebuilt count       : {kcount(r'volume rebuilt field=')}")
        log(f"crashes/tracebacks in log  : {kcount(r'Traceback|Segmentation|core dumped')}")
        log("===========================================")
        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
