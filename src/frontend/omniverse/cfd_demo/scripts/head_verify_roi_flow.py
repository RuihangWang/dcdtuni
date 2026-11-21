#!/usr/bin/env python3
"""Head-browser verification of the NEW post-processing flow (ROI-first, deferred
field). Drives the REAL UI on Xvfb :99, exactly as a user would:

  1. Load CFD Geometry (Geometry tab)
  2. Post-processing tab
  3. Show ROI Box + set a HALF region (Z 0..9.6) + Clip on   [dataset NOT loaded yet]
        -> screenshot rf_1: the ROI box is drawn, NO flow field.
  4. simulate (load the dataset)
        -> screenshot rf_2: STILL no flow field (deferred-field requirement).
  5. Temp    -> screenshot rf_3: temperature field, CLIPPED to the ROI front half.
  6. Velocity-> screenshot rf_4: velocity field, CLIPPED to the ROI front half.

Decisive Kit-log receipts:
  - "ROI box created (pre-dataset)"           = ROI exists before any dataset.
  - between "load_dataset OK" and the Temp click there is NO "volume rebuilt"
                                                = simulate left the flow field OFF.
  - "volume rebuilt field=T roi=True"          = Temp voxelised CLIPPED to the ROI,
                                                  proving clip_enabled persisted the swap.
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_roi_flow")
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


def log(m): print(f"[rf] {m}", flush=True)
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
    """Native-click a BUTTON by EXACT trimmed text (so 'simulate' matches the button, not
    prose, and no Playwright actionability wait for a below-the-fold control)."""
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
    # The ROI section is the FIRST (and, with streamlines off, only) .roi-box -> its 6
    # number inputs are indices 0..5 = Xmin,Xmax,Ymin,Ymax,Zmin,Zmax.
    vals = [mn[0], mx[0], mn[1], mx[1], mn[2], mx[2]]
    for idx, v in enumerate(vals):
        await page.evaluate(SET_REACT, [".roi-box input[type=number]", idx, v])
        await page.wait_for_timeout(300)
    log(f"set ROI box {tuple(mn)}..{tuple(mx)}")


async def btn_states(page):
    return await page.evaluate(
        "() => [...document.querySelectorAll('button')]"
        ".map(b => b.textContent.trim() + (b.disabled ? '[disabled]' : ''))"
        ".filter(t => /Show ROI|Hide ROI|Clip|Clipping|Off|Temp|Velocity|simulate|Add Streamlines/.test(t))")


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

        # --- 1. Load geometry ---
        gpos = ksize()
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
        # Requirement: the ROI box exists BEFORE any dataset.
        roi_pre = kgrep(r"ROI box created \(pre-dataset\)", gpos)
        log("ROI-pre-dataset receipt: " + (roi_pre[-1] if roi_pre else "NONE"))

        # --- 2. Post-processing tab ---
        await click_text(page, "Post-processing"); await page.wait_for_timeout(1500)
        log("buttons @ post tab (pre-sim): " + str(await btn_states(page)))

        # --- 3. ROI: show + half region (Z front half) + Clip, BEFORE simulate ---
        await click_button(page, "Show ROI Box"); await page.wait_for_timeout(1500)
        await set_box(page, (0, 0, 0), (31.2, 5, 9.6))
        await page.wait_for_timeout(1200)
        clip_pos = ksize()
        await click_button(page, "Clip Volume to ROI"); await page.wait_for_timeout(2500)
        log("buttons after ROI+clip: " + str(await btn_states(page)))
        # There must be NO volume rebuild from setting clip before a dataset exists.
        pre_sim_vol = kgrep(r"volume rebuilt", clip_pos)
        log(f"volume rebuilds after clip (pre-sim), expect NONE: {len(pre_sim_vol)}")
        await page.screenshot(path=f"{SHOT}/rf_1_roi_clip_nofield.png")

        # --- 4. simulate (load the dataset) — must NOT auto-show a field ---
        spos = ksize()
        await click_button(page, "simulate"); await page.wait_for_timeout(2500)
        await page.evaluate(
            "() => { const rows=[...document.querySelectorAll('.ds-dlg-file')];"
            " const r=rows.find(x=>{const n=x.querySelector('.ds-dlg-name');"
            "  return n && n.textContent.trim().endsWith('.vtu');})||rows[0];"
            " if(r) r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); }")
        await wait_k(page, r"load_dataset OK", "load_dataset OK", spos, 600)
        # window between load_dataset OK and the Temp click:
        post_load_pos = ksize()
        await page.wait_for_timeout(6000)
        after_sim_vol = kgrep(r"volume rebuilt", post_load_pos)
        log(f"volume rebuilds after simulate (before Temp), expect NONE: {len(after_sim_vol)}")
        log("buttons after simulate: " + str(await btn_states(page)))
        await page.screenshot(path=f"{SHOT}/rf_2_after_simulate_nofield.png")

        # --- 5. Temp -> field CLIPPED to the ROI (roi=True proves clip persisted) ---
        tpos = ksize()
        await click_button(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T", "volume T", tpos, 240)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=f"{SHOT}/rf_3_temp_clipped.png")
        t_rebuild = kgrep(r"volume rebuilt field=T", tpos)
        log("Temp rebuild: " + (t_rebuild[-1] if t_rebuild else "NONE"))

        # --- 6. Velocity -> velocity field, still clipped ---
        upos = ksize()
        await click_button(page, "Velocity")
        await wait_k(page, r"volume rebuilt field=U", "volume U", upos, 240)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=f"{SHOT}/rf_4_velocity_clipped.png")
        u_rebuild = kgrep(r"volume rebuilt field=U", upos)
        log("Velocity rebuild: " + (u_rebuild[-1] if u_rebuild else "NONE"))

        # --- receipts summary ---
        log("================= RECEIPTS =================")
        log("ROI pre-dataset : " + (roi_pre[-1] if roi_pre else "NONE"))
        log(f"rebuilds pre-sim (want 0)        : {len(pre_sim_vol)}")
        log(f"rebuilds post-sim/pre-Temp (0)   : {len(after_sim_vol)}")
        log("Temp clipped    : " + (t_rebuild[-1] if t_rebuild else "NONE"))
        log("Velocity clipped: " + (u_rebuild[-1] if u_rebuild else "NONE"))
        log("===========================================")
        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
