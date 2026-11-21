#!/usr/bin/env python3
"""Capture a gallery of Omniverse viewport renders for the project page.

The project page needs to SHOW what the RTX viewport produces, not just describe it.
This drives the real demo through a head browser and screenshots the viewport region
after each visualisation settles.

Shots, in the order they are produced:
  g1_geometry   the RTX-rendered hall before any field is loaded
  g2_temp       temperature volume over the whole hall
  g3_velocity   velocity-magnitude volume
  g4_roi        temperature clipped to the region-of-interest box
  g5_streaml    streamlines traced from the seed sphere

Head browser on the Xvfb :99 display, software H.264 decode — never headless
browser-use, which renders the WebRTC viewport black and takes a GPU process.

The Kit streamer serves ONE client, so nothing else may hold :8301 while this runs.
"""
import asyncio, os, pathlib, shutil, sys

OUT = os.environ.get("GALLERY_DIR", "/tmp/ov_gallery")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = "/tmp/dc_cfd_demo.kit.log"
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path writes the host
# username into a file that ships with the repo, and the paper is under double-blind review.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
# The viewport sits between the two overlay panels. Cropping to it keeps the page's
# own chrome out of the gallery — these are meant to read as renders, not screenshots.
VIEW = {"x": 300, "y": 40, "width": 1310, "height": 850}
os.makedirs(OUT, exist_ok=True)


def log(m): print(f"[gal] {m}", flush=True)


async def click_btn(page, name, quiet=False):
    r = await page.evaluate(
        """(t) => {
            const b = [...document.querySelectorAll('button')]
                .filter(x => x.textContent.trim() === t);
            if (!b.length) return 'NOT_FOUND';
            const tgt = b.find(x => !x.disabled);
            if (!tgt) return 'DISABLED';
            tgt.click(); return 'OK';
        }""", name)
    if not quiet:
        log(f"click '{name}' -> {r}")
    return r == "OK"


async def shot(page, tag, wait_ms):
    await page.wait_for_timeout(wait_ms)
    p = f"{OUT}/{tag}.png"
    await page.screenshot(path=p, clip=VIEW)
    log(f"shot {tag}")
    return p


async def main():
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

        # ── scene ────────────────────────────────────────────────────────
        await page.get_by_text("Load CFD Geometry", exact=False).first.click(timeout=8000)
        for _ in range(20):
            await page.wait_for_timeout(1000)
            if await page.evaluate("() => !!document.querySelector('.ds-dlg-file')"):
                break
        await page.evaluate(
            "() => { const r=[...document.querySelectorAll('.ds-dlg-file')]"
            ".find(x=>x.textContent.includes('GTC2025.json'));"
            " if(r) r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); }")
        for i in range(70):
            await page.wait_for_timeout(5000)
            if await page.evaluate("() => document.querySelectorAll('.tree .node-name').length"):
                log(f"scene loaded ~{(i + 1) * 5}s"); break
        else:
            log("scene NOT loaded; abort"); await browser.close(); return

        # Collapse the side panels so the crop is pure viewport. They are React-driven,
        # so hide them in CSS rather than clicking anything that mutates demo state.
        await page.add_style_tag(content=".overlay{opacity:0 !important} "
                                         ".logdock,.oplog{opacity:0 !important}")
        await shot(page, "g1_geometry", 6000)

        # ── fields ───────────────────────────────────────────────────────
        await page.evaluate("() => document.querySelectorAll('.overlay')"
                            ".forEach(e => e.style.opacity = 1)")
        await page.get_by_text("Post-processing", exact=False).first.click()
        await page.wait_for_timeout(1200)
        if not await click_btn(page, "Start post-processing"):
            log("no dataset to load; abort"); await browser.close(); return
        await page.wait_for_timeout(20000)

        async def hide_panels():
            await page.evaluate("() => document.querySelectorAll('.overlay')"
                                ".forEach(e => e.style.opacity = 0)")

        async def show_panels():
            await page.evaluate("() => document.querySelectorAll('.overlay')"
                                ".forEach(e => e.style.opacity = 1)")

        await click_btn(page, "Temp")
        await hide_panels()
        await shot(page, "g2_temp", 38000)

        await show_panels()
        await click_btn(page, "Velocity")
        await hide_panels()
        await shot(page, "g3_velocity", 38000)

        # ── ROI-clipped temperature ──────────────────────────────────────
        await show_panels()
        await click_btn(page, "Temp")
        await page.wait_for_timeout(30000)
        await click_btn(page, "Show ROI Box")
        await page.wait_for_timeout(2500)
        await click_btn(page, "Clip Volume to ROI")
        await hide_panels()
        await shot(page, "g4_roi", 38000)

        # ── streamlines ──────────────────────────────────────────────────
        await show_panels()
        # Turn clipping back off so the streamlines trace the whole hall.
        await click_btn(page, "Clip Volume to ROI", quiet=True)
        await page.wait_for_timeout(12000)
        for label in ("Streamlines", "Apply", "Trace"):
            if await click_btn(page, label, quiet=True):
                await page.wait_for_timeout(3000)
        await hide_panels()
        await shot(page, "g5_streaml", 40000)

        await show_panels()
        await browser.close()

    log("=" * 40)
    for f in sorted(os.listdir(OUT)):
        log(f"  {f}  {os.path.getsize(os.path.join(OUT, f)) // 1024} KB")


asyncio.run(main())
