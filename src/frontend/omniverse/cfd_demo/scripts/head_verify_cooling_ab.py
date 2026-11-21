#!/usr/bin/env python3
"""Head-browser A/B: does lowering the CRAC set-point visibly cool the RENDERED room?

The numbers already say yes (-0.94 K mean over 99.9% of points), but a number in a log is
not the demo. This renders BOTH predictions through the real Kit viewport, at the same
camera and the same PINNED colour domain, and diffs the two frames. If the pinned domain
were wrong — or the volume silently reused a cached grid — the two frames would come out
pixel-identical and the demo would show nothing.

Inputs are two .vti files already produced by the worker; nothing is re-inferred here, so
the comparison isolates the RENDER. They are swapped in as logger/inference_results/
latest.vti, which is what "Start post-processing" loads.

Run on the Xvfb :99 display with the Playwright env — never headless browser-use, which
renders the WebRTC viewport black.
"""
import asyncio, os, pathlib, shutil, shutil, sys

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_ab")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
RESULTS = os.environ.get("AI_RESULTS_DIR") or str(
    pathlib.Path(__file__).resolve().parents[1]
    / "SCANO/GTC_A3_Numerical/logger/inference_results")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path writes the host
# username into a file that ships with the repo, and the paper is under double-blind review.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
# The viewport occupies the middle of the 1920x1080 window; the side panels are static
# chrome and would dilute the diff.
VIEWPORT_BOX = {"x": 300, "y": 60, "width": 1300, "height": 820}
os.makedirs(SHOT, exist_ok=True)


def log(m): print(f"[ab] {m}", flush=True)


async def click_button(page, name):
    r = await page.evaluate(
        """(t) => {
            const b = [...document.querySelectorAll('button')]
                .filter(x => x.textContent.trim() === t);
            if (!b.length) return 'NOT_FOUND';
            const tgt = b.find(x => !x.disabled);
            if (!tgt) return 'DISABLED';
            tgt.click(); return 'OK';
        }""", name)
    log(f"click '{name}' -> {r}")
    return r == "OK"


async def render(page, vti, tag):
    """Swap in a prediction and render it, returning the viewport screenshot path."""
    shutil.copyfile(vti, os.path.join(RESULTS, "latest.vti"))
    log(f"{tag}: latest.vti <- {os.path.basename(vti)}")
    if not await click_button(page, "Start post-processing"):
        return None
    await page.wait_for_timeout(20000)
    await click_button(page, "Temp")
    await page.wait_for_timeout(35000)
    path = f"{SHOT}/{tag}.png"
    await page.screenshot(path=path, clip=VIEWPORT_BOX)
    log(f"{tag}: shot -> {path}")
    return path


async def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: head_verify_cooling_ab.py <baseline.vti> <cooled.vti>")
    baseline, cooled = sys.argv[1], sys.argv[2]

    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False, executable_path=CHROME,
            args=["--disable-gpu", "--use-gl=swiftshader", "--window-size=1920,1080",
                  "--autoplay-policy=no-user-gesture-required"])
        page = await (await browser.new_context(
            viewport={"width": 1920, "height": 1080})).new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(12000)

        # The geometry entry point is a text element, not a <button> whose textContent
        # equals the label, so drive it the same way the AI-flow verifier does.
        await page.get_by_text("Load CFD Geometry", exact=False).first.click(timeout=8000)
        for _ in range(20):
            await page.wait_for_timeout(1000)
            if await page.evaluate("() => !!document.querySelector('.ds-dlg-file')"):
                break
        picked = await page.evaluate(
            "() => { const r=[...document.querySelectorAll('.ds-dlg-file')]"
            ".find(x=>x.textContent.includes('GTC2025.json')); if(!r) return 'NO_ROW';"
            " r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); return 'OK'; }")
        log(f"scene json dblclick -> {picked}")
        for i in range(70):
            await page.wait_for_timeout(5000)
            if await page.evaluate("() => document.querySelectorAll('.tree .node-name').length"):
                log(f"scene loaded ~{(i + 1) * 5}s"); break
        else:
            log("scene NOT loaded; abort"); await browser.close(); return

        await page.get_by_text("Post-processing", exact=False).first.click()
        await page.wait_for_timeout(1500)

        a = await render(page, baseline, "A_baseline")
        b = await render(page, cooled, "B_acu14")
        await browser.close()

    if not (a and b):
        log("RESULT: a render failed"); return
    import numpy as np
    from PIL import Image
    ia = np.asarray(Image.open(a).convert("RGB"), dtype=np.int16)
    ib = np.asarray(Image.open(b).convert("RGB"), dtype=np.int16)
    d = np.abs(ib - ia).sum(axis=2)
    changed = float((d > 12).mean()) * 100          # 12/765: above encoder noise
    # Cooler = the volume shifts toward the blue end of the ramp, so per-pixel the blue
    # channel should gain relative to red. That signed check is what separates "the render
    # changed" from "the render got COLDER".
    dr = float((ib[..., 0] - ia[..., 0]).mean())
    db = float((ib[..., 2] - ia[..., 2]).mean())
    log(f"RESULT: {changed:.1f}% of viewport pixels changed; "
        f"mean red {dr:+.2f}, mean blue {db:+.2f} (blue-ward = cooler)")
    log("VERDICT: " + ("cooling is VISIBLE" if changed > 2 and db - dr > 0
                       else "NOT visibly cooler — investigate"))


asyncio.run(main())
