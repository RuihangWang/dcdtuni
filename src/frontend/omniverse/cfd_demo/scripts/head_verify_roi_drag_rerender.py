#!/usr/bin/env python3
"""Round 4 — the user's EXACT bug-1 scenario, end to end.

"I clip with the ROI and render the field with Temp; then I drag the ROI box in the
viewport and there is no button to redraw the result for the new region."

Round 3 proved the drag is baked into the clip bounds and echoed back to the UI inputs,
but it never re-voxelised: its Hide ROI Box step silently turns the clip OFF (toggleRoi
releases the clip when hiding), so at sync time clip=False and there was correctly
nothing to rebuild. This run never hides the box, so the clip stays ON:

    Show ROI Box (selects it -> gizmo)  ->  set bounds  ->  Temp  ->  Clip ON
    ->  drag the box's +Y arrow  ->  "Re-render Dragged Region"

Acceptance (all three):
    "ROI from viewport -> min=..."          the drag was baked into the clip region
    "volume rebuilt field=T roi=True"       it RE-VOXELISED inside the new region
    the 6 ROI inputs changed                the echo reached React
"""
import asyncio, os, re
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_drag4")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = os.environ.get("KIT_LOG", "/tmp/dc_cfd_demo.kit.log")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)

# ROI-box gizmo +Y arrow — the grab that actually took hold in round 3.
ROI_Y_ARROW = (889, 366)

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


def log(m): print(f"[d4] {m}", flush=True)
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


async def click_button(page, name):
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


async def roi_inputs(page):
    return await page.evaluate(
        "() => [...document.querySelectorAll('.roi-box input[type=number]')]"
        ".map(e => parseFloat(e.value))")


async def drag(page, x0, y0, dx, dy, steps=30):
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

        await click_text(page, "Load CFD Geometry")
        await page.wait_for_timeout(2500)
        await page.evaluate(
            "() => { const r=[...document.querySelectorAll('.ds-dlg-file')]"
            ".find(x=>x.textContent.includes('GTC2025.json')); if(r)"
            " r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); }")
        for i in range(70):
            await page.wait_for_timeout(5000)
            if await page.evaluate(
                    "() => document.querySelectorAll('.tree .node-name').length"):
                log(f"scene loaded ~{(i+1)*5}s"); break
        else:
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

        # Show the box ONCE (this selects it -> gizmo attaches). Never hide it again:
        # hiding releases the clip, which is what defeated round 3.
        await click_text(page, "Show ROI Box"); await page.wait_for_timeout(3000)
        # Pin a known box (Kit restarted, so it is back at its base AABB).
        for idx, v in enumerate([0, 31.0, 0, 5.0, 0, 9.6]):
            await page.evaluate(SET_REACT, [".roi-box input[type=number]", idx, v])
            await page.wait_for_timeout(300)
        await page.wait_for_timeout(2000)

        pos = ksize()
        await click_button(page, "Temp")
        await wait_k(page, r"volume rebuilt field=T", "Temp volume built", pos, 240)
        await page.wait_for_timeout(4000)

        pos = ksize()
        await click_text(page, "Clip Volume to ROI")
        await wait_k(page, r"volume rebuilt field=T roi=True", "clipped volume", pos, 240)
        await page.wait_for_timeout(6000)

        # No re-select workaround any more: _do_rebuild_volume now restores the selection
        # it had before the rebuild, so the ROI box keeps its gizmo across showing a field
        # and toggling the clip. If this run bakes a drag, that fix is what made it
        # reachable (rounds 4-5 grabbed nothing because the volume had stolen selection).
        await page.screenshot(path=f"{SHOT}/e1_clipped_volume.png")
        before = await roi_inputs(page)
        log(f"ROI inputs BEFORE drag: {before}")

        # ---- THE SCENARIO: drag the box, then press the new button ----
        # The +Y arrow shaft is only a few px wide and sits at x=885 (measured off the
        # round-6 zoom); earlier rounds grabbed x=889 and slid past it. Walk down the
        # shaft until one grab takes hold.
        baked = rebuilt = False
        for gy in (330, 350, 370, 385):
            pos = ksize()
            await drag(page, 885, gy, 0, -70)
            await page.screenshot(path=f"{SHOT}/e2_after_drag_y{gy}.png")
            await click_button(page, "⟳ Re-render Dragged Region")
            if await wait_k(page, r"ROI from viewport -> min=",
                            f"ROI drag baked (y={gy})", pos, 30):
                baked = True
                rebuilt = await wait_k(page, r"volume rebuilt field=T roi=True",
                                       "RE-VOXELISED in new ROI", pos, 300)
                break
            log(f"grab y={gy} missed the arrow; trying next point")
        await page.wait_for_timeout(8000)
        await page.screenshot(path=f"{SHOT}/e3_rerendered.png")
        after = await roi_inputs(page)
        log(f"ROI inputs AFTER re-render: {after}")

        log("================= RECEIPTS =================")
        for label, pat in [
            ("sync state",      r"syncFromViewport clip="),
            ("ROI drag baked",  r"ROI from viewport -> min="),
            ("volume rebuilt",  r"volume rebuilt field=T roi=True"),
            ("errors",          r"\[Error\].*cfd_bridge"),
        ]:
            hits = kgrep(pat)
            log(f"{label}: " + (hits[-1].strip()[:190] if hits else "NONE"))
        log("===========================================")
        log(f"baked={baked} rebuilt={rebuilt} inputs_changed={before != after}")
        await b.close(); log("DONE (browser closed)")


if __name__ == "__main__":
    asyncio.run(main())
