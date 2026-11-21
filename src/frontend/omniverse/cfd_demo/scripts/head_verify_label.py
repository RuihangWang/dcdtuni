#!/usr/bin/env python3
"""Quick head-browser check of the renamed Post-processing labels:
  heading "Dataset" -> "Drag&Sim", button "Load dataset" -> "simulate".
No scene load needed — the Post-processing tab is always clickable and the panel
stays mounted (button is just disabled until the scene loads, text still shows)."""
import asyncio, os
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_label")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m): print(f"[label] {m}", flush=True)


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
        try:
            await page.get_by_text("Post-processing", exact=False).first.click(timeout=8000)
            log("clicked 'Post-processing'")
        except Exception as e:
            log(f"tab click FAILED: {type(e).__name__}")
        await page.wait_for_timeout(2000)

        # Read the DOM ground-truth for the two labels.
        h3 = await page.evaluate(
            "() => { const h=[...document.querySelectorAll('.postproc h3')].map(x=>x.textContent); return h; }")
        btn = await page.evaluate(
            "() => { const b=[...document.querySelectorAll('.postproc button')]"
            ".map(x=>x.textContent.trim()); return b; }")
        log(f"postproc <h3> texts: {h3}")
        log(f"postproc <button> texts: {btn}")
        log(f"PASS heading 'Drag&Sim': {'Drag&Sim' in (h3 or [])}")
        log(f"PASS button 'simulate': {'simulate' in (btn or [])}")

        await page.screenshot(path=f"{SHOT}/label_postproc.png")
        # tight crop of the left panel for legibility
        try:
            el = await page.query_selector(".postproc")
            if el:
                await el.screenshot(path=f"{SHOT}/label_panel_crop.png")
                log("cropped .postproc panel")
        except Exception as e:
            log(f"crop note: {type(e).__name__}")
        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
