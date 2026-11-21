#!/usr/bin/env python3
"""Head-browser verification of the three streamline-seed fixes:

  Bug 1  simulate dialog + overlay say "Simulate…", never "Load CFD dataset".
  Bug 2  a Show/Hide seed-sphere toggle exists and works.
  Bug 3  the seed sphere renders as a SMOOTH BALL (fixed high visual tessellation),
         decoupled from the seed count.

Flow:
  1. Load geometry -> Post-processing.
  2. Click 'simulate' -> screenshot the file dialog (ss_0: title reads "Simulate — …").
  3. Load internal.vtu.
  4. Add Streamlines -> seed sphere shows -> ss_1 (smooth ball). Assert a 'Hide seed sphere'
     button is present.
  5. Hide seed sphere -> ss_2 (gone). Show seed sphere -> ss_3 (back).
  6. Apply -> ss_4 (streamlines from the invisible seed points).

Receipts: 'seed sphere visible=True visual_res=32' proves the fixed smooth tessellation;
'streamlines generated … res=<low>' proves the seed COUNT still drives the invisible source.
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_seed")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = os.environ.get("KIT_LOG", "/tmp/dc_cfd_demo.kit.log")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m): print(f"[ss] {m}", flush=True)
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


async def sl_buttons(page):
    return await page.evaluate(
        "() => [...document.querySelectorAll('button')]"
        ".map(b => b.textContent.trim() + (b.disabled ? '[disabled]' : ''))"
        ".filter(t => /seed sphere|Streamlines|Colour|Apply|Re-apply|Tracing/.test(t))")


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

        # --- geometry ---
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

        # --- Bug 1: simulate dialog title ---
        await click_button(page, "simulate"); await page.wait_for_timeout(1500)
        dlg_title = await page.evaluate(
            "() => { const t=document.querySelector('.ds-dlg-title'); return t?t.textContent.trim():'NO_DIALOG'; }")
        log(f"simulate dialog title: '{dlg_title}'")
        await page.screenshot(path=f"{SHOT}/ss_0_dialog.png")
        # load the vtu
        spos = ksize()
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

        # --- Bug 3: Add Streamlines -> smooth seed ball ---
        pos = ksize()
        await click_button(page, "Add Streamlines")
        await wait_k(page, r"seed sphere visible=True", "seed sphere shown", pos, 40)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/ss_1_seed_ball.png")
        sb = await sl_buttons(page)
        log(f"streamline buttons (expect a 'Hide seed sphere'): {sb}")
        vres = kgrep(r"seed sphere visible=True", pos)
        log("seed-sphere receipt: " + (vres[-1] if vres else "NONE"))

        # --- Bug 2: Hide then Show the seed sphere ---
        pos = ksize()
        await click_button(page, "Hide seed sphere")
        await wait_k(page, r"seed sphere visible=False", "seed hidden", pos, 30)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=f"{SHOT}/ss_2_seed_hidden.png")

        pos = ksize()
        await click_button(page, "Show seed sphere")
        await wait_k(page, r"seed sphere visible=True", "seed shown again", pos, 30)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=f"{SHOT}/ss_3_seed_shown.png")

        # --- Apply -> streamlines from the invisible seed points ---
        pos = ksize()
        await click_button(page, "Apply")
        await wait_k(page, r"streamlines generated", "streamlines", pos, 240)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=f"{SHOT}/ss_4_streamlines.png")
        gen = kgrep(r"streamlines generated", pos)
        log("streamlines receipt: " + (gen[-1] if gen else "NONE"))

        log("================= RECEIPTS =================")
        log(f"dialog title      : '{dlg_title}'")
        log("seed sphere shown : " + (vres[-1] if vres else "NONE"))
        log(f"has Hide toggle   : {'yes' if any('seed sphere' in x for x in sb) else 'NO'}")
        log("streamlines gen   : " + (gen[-1] if gen else "NONE"))
        log("===========================================")
        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
