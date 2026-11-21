#!/usr/bin/env python3
"""Round 2 of the viewport-drag verification — with the gizmo handles located precisely.

Round 1 proved the streamlines render inside the ROI and that the new
"Re-render Dragged Region" button reaches the backend, but its drag MISSED the gizmo
(it hit the floor and merely changed the selection), so the drag-adoption path was
never exercised. Handle coordinates below were measured off the round-1 screenshot:

  seed gizmo origin  (989, 526)
  +Y arrow (green)   tip (989, 465)
  +X arrow (red)     tip (1037, 554)   <- grabbed here; X has the most ROI freedom
  +Z arrow (blue)    tip (941, 551)

Two independent drags, each on its own selected prim (only one prim is selected at a
time, so the ROI box must be dragged BEFORE streamlines steal the selection):

  A. ROI box drag   -> "Re-render Dragged Region" -> receipt "ROI from viewport -> min="
  B. seed ball drag -> "Re-apply"                 -> receipt "seed from viewport -> center="
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_drag2")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = os.environ.get("KIT_LOG", "/tmp/dc_cfd_demo.kit.log")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)

SEED_ORIGIN = (989, 526)
SEED_X_ARROW = (1025, 547)      # along the red +X arrow, short of the tip
SEED_X_DIR = (0.86, 0.50)       # screen direction of +X at this camera

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


def log(m): print(f"[d2] {m}", flush=True)
def ksize():
    try: return os.path.getsize(KITLOG)
    except OSError: return 0
def kread(pos=0):
    try:
        with open(KITLOG, "rb") as f:
            f.seek(pos); return f.read().decode("utf-8", "replace")
    except OSError: return ""
def khas(pat, pos): return re.search(pat, kread(pos)) is not None
def kgrep(pat, pos=0):
    return [ln for ln in kread(pos).splitlines() if re.search(pat, ln)]


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
    log(f"js-click '{name}' -> {r}")
    return r == "OK"


async def set_box(page, mn, mx):
    for idx, v in enumerate([mn[0], mx[0], mn[1], mx[1], mn[2], mx[2]]):
        await page.evaluate(SET_REACT, [".roi-box input[type=number]", idx, v])
        await page.wait_for_timeout(300)
    log(f"set ROI box {tuple(mn)}..{tuple(mx)}")


async def roi_inputs(page):
    return await page.evaluate(
        "() => [...document.querySelectorAll('.roi-box input[type=number]')]"
        ".map(e => parseFloat(e.value))")


async def drag(page, x0, y0, dx, dy, steps=30):
    """Real mouse drag over the streamed viewport (stream.ts forwards these to Kit)."""
    await page.mouse.move(x0, y0)
    await page.wait_for_timeout(500)
    await page.mouse.down()
    await page.wait_for_timeout(400)
    for i in range(1, steps + 1):
        await page.mouse.move(x0 + dx * i / steps, y0 + dy * i / steps)
        await page.wait_for_timeout(45)
    await page.wait_for_timeout(400)
    await page.mouse.up()
    await page.wait_for_timeout(2000)
    log(f"drag ({x0},{y0}) -> ({int(x0+dx)},{int(y0+dy)})")


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, headless=False,
            args=["--disable-gpu", "--use-gl=swiftshader", "--no-sandbox",
                  "--disable-dev-shm-usage", "--window-size=1920,1080",
                  "--autoplay-policy=no-user-gesture-required"])
        page = await (await b.new_context(
            viewport={"width": 1920, "height": 1080})).new_page()
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log(f"goto note: {type(e).__name__}")
        await page.wait_for_timeout(12000)

        # ---- scene + dataset (geometry is cached from the previous run -> fast) ----
        await click_text(page, "Load CFD Geometry")
        await page.wait_for_timeout(2500)
        await page.evaluate(
            "() => { const r=[...document.querySelectorAll('.ds-dlg-file')]"
            ".find(x=>x.textContent.includes('GTC2025.json')); if(r)"
            " r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); }")
        loaded = False
        for i in range(70):
            await page.wait_for_timeout(5000)
            rows = await page.evaluate(
                "() => document.querySelectorAll('.tree .node-name').length")
            if rows:
                loaded = True; log(f"scene loaded ({rows} rows) ~{(i+1)*5}s"); break
        if not loaded:
            log("scene NOT loaded; abort"); await b.close(); return

        await click_text(page, "Post-processing"); await page.wait_for_timeout(1500)
        await click_text(page, "simulate"); await page.wait_for_timeout(2500)
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

        # ================= A. ROI BOX DRAG (bug 1) =================
        await click_text(page, "Show ROI Box"); await page.wait_for_timeout(2500)
        await set_box(page, (0, 0, 0), (31.0, 5.0, 9.6))
        await page.wait_for_timeout(1500)
        await click_text(page, "Clip Volume to ROI")
        await page.wait_for_timeout(6000)
        await page.screenshot(path=f"{SHOT}/a1_roi_selected.png")
        before_roi = await roi_inputs(page)
        log(f"ROI inputs before drag: {before_roi}")

        # The ROI box is the selected prim now, so its gizmo owns the origin area.
        pos = ksize()
        await drag(page, SEED_ORIGIN[0], SEED_ORIGIN[1], 140, 80)
        await page.screenshot(path=f"{SHOT}/a2_roi_after_drag.png")
        await click_button(page, "⟳ Re-render Dragged Region")
        a_sync = await wait_k(page, r"syncFromViewport", "syncFromViewport", pos, 90)
        a_bake = await wait_k(page, r"ROI from viewport -> min=", "ROI drag baked", pos, 60)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=f"{SHOT}/a3_roi_rerendered.png")
        log(f"ROI inputs after re-render: {await roi_inputs(page)}")

        # ================= B. SEED BALL DRAG (bug 2) =================
        pos = ksize()
        await click_button(page, "Add Streamlines")
        await wait_k(page, r"seed sphere visible=True", "seed sphere shown", pos, 60)
        await page.wait_for_timeout(4000)
        pos = ksize()
        await click_button(page, "Apply")
        await wait_k(page, r"streamlines generated field=T roi=True",
                     "streamlines traced", pos, 300)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/b1_seed_before.png")

        # Grab the RED +X arrow and pull along +X.
        pos_drag = ksize()
        await drag(page, SEED_X_ARROW[0], SEED_X_ARROW[1],
                   SEED_X_DIR[0] * 230, SEED_X_DIR[1] * 230)
        await page.screenshot(path=f"{SHOT}/b2_seed_after_drag.png")

        # Re-apply — the ball must NOT snap back to its default centre.
        pos = ksize()
        await click_button(page, "Re-apply")
        b_adopt = await wait_k(page, r"seed from viewport -> center=",
                               "seed drag adopted", pos, 120)
        await wait_k(page, r"streamlines generated field=T roi=True",
                     "streamlines re-traced", pos, 300)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=f"{SHOT}/b3_seed_after_reapply.png")

        log("================= RECEIPTS =================")
        for label, pat in [
            ("A syncFromViewport", r"syncFromViewport"),
            ("A ROI drag baked",   r"ROI from viewport -> min="),
            ("B seed drag adopted", r"seed from viewport -> center="),
            ("B streamlines(roi)",  r"streamlines generated field=T roi=True"),
            ("seed placements",     r"seed sphere visible=True"),
            ("errors",              r"\[Error\].*cfd_bridge"),
        ]:
            hits = kgrep(pat)
            log(f"{label}: " + (hits[-1].strip()[:200] if hits else "NONE"))
        log("===========================================")
        log(f"a_sync={a_sync} a_bake={a_bake} b_adopt={b_adopt}")
        await b.close(); log("DONE (browser closed)")


if __name__ == "__main__":
    asyncio.run(main())
