#!/usr/bin/env python3
"""HEAD-browser verification for the Operational Log panel + floating colorbar.

These are PURE UI features (React state + the Vite /api/kit-log SSE) — they do NOT
need the ~6-min WebRTC geometry load. We load the page, exercise the log dock and
colorbar, and screenshot each step. Same true-head launch recipe as head_verify.py
(headless=False on Xvfb :99, --disable-gpu software decode — zero GPU).

Run:
  HEAD_URL=http://localhost:8081 HEAD_SHOT_DIR=/tmp/head_logpanel \
  DISPLAY=:99 $BROWSER_USE_PY \
      src/frontend/omniverse/cfd_demo/scripts/head_verify_logpanel.py
"""
import asyncio
import shutil
import pathlib
import os

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_logpanel")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[head_logpanel] {m}", flush=True)


# Set a React controlled input's value so onChange actually fires (native setter).
SET_REACT = """
([sel, idx, v]) => {
    const el = document.querySelectorAll(sel)[idx];
    if (!el) return 'NO_EL ' + sel + ' #' + idx;
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
          .set.call(el, String(v));
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return 'OK ' + el.type + '=' + el.value;
}
"""


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}: {str(e)[:80]}")
        return False


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME,
            headless=False,
            args=[
                "--disable-gpu", "--use-gl=swiftshader",
                "--no-sandbox", "--disable-dev-shm-usage",
                "--window-size=1920,1080",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await ctx.new_page()

        log(f"goto {URL}")
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001
            log(f"goto note: {type(e).__name__} (continuing)")
        # Give the SSE backlog + WebRTC a few seconds to populate the log dock.
        await page.wait_for_timeout(8000)

        # 1) Log dock present + populated by the Kit-log SSE backlog?
        present = await page.evaluate(
            "() => { const p=document.querySelector('.logpanel');"
            " const rows=document.querySelectorAll('.logline').length;"
            " return p? ('rows='+rows+' h='+Math.round(p.getBoundingClientRect().height)) : 'NO_PANEL'; }"
        )
        log(f"[1] log dock: {present}")
        await page.screenshot(path=f"{SHOT}/lp01_dock_present.png")

        # 2) Fire a UI command and confirm it lands as a CMD line.
        #    Toggle a geometry visibility checkbox -> setObjectVisible -> logCmd.
        r = await page.eval_on_selector_all(
            ".tree input.node-vis",
            "els => { if(!els.length) return 'NO_CHECKBOX'; els[0].click(); return 'clicked'; }",
        )
        log(f"[2] toggled first geometry checkbox: {r}")
        await page.wait_for_timeout(1200)
        cmd_seen = await page.evaluate(
            "() => Array.from(document.querySelectorAll('.logline.lvl-cmd .ll-msg'))"
            ".map(e=>e.textContent).slice(-5)"
        )
        log(f"[2] recent CMD lines: {cmd_seen}")
        await page.screenshot(path=f"{SHOT}/lp02_cmd_logged.png")

        # 3) Colorbar: switch to Post-processing, pick Temp -> floating colorbar appears.
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1200)
        await click_text(page, "Temp")
        await page.wait_for_timeout(1500)
        cb = await page.evaluate(
            "() => { const c=document.querySelector('.colorbar-float');"
            " return c? (c.querySelector('.colorbar-label').textContent+' @ '+"
            "Math.round(c.getBoundingClientRect().left)+','+Math.round(c.getBoundingClientRect().top)) : 'NO_COLORBAR'; }"
        )
        log(f"[3] colorbar: {cb}")
        await page.screenshot(path=f"{SHOT}/lp03_colorbar_shown.png")

        # 4) Drag the colorbar from its corner toward the centre.
        box = await page.evaluate(
            "() => { const c=document.querySelector('.colorbar-float'); if(!c) return null;"
            " const r=c.getBoundingClientRect(); return [r.left+r.width/2, r.top+8]; }"
        )
        if box:
            await page.mouse.move(box[0], box[1])
            await page.mouse.down()
            await page.mouse.move(box[0] - 500, box[1] - 250, steps=12)
            await page.mouse.up()
            await page.wait_for_timeout(600)
            moved = await page.evaluate(
                "() => { const c=document.querySelector('.colorbar-float'); const r=c.getBoundingClientRect();"
                " return Math.round(r.left)+','+Math.round(r.top); }"
            )
            log(f"[4] colorbar dragged to: {moved}")
        await page.screenshot(path=f"{SHOT}/lp04_colorbar_dragged.png")

        # 5) Double-click the colorbar -> range-edit dialog; change High; Apply.
        await page.evaluate(
            "() => { const c=document.querySelector('.colorbar-float');"
            " c.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); }"
        )
        await page.wait_for_timeout(700)
        dlg = await page.evaluate(
            "() => document.querySelector('.cb-modal') ? 'OPEN' : 'NO_DIALOG'"
        )
        log(f"[5] range dialog: {dlg}")
        await page.screenshot(path=f"{SHOT}/lp05_range_dialog.png")
        # set High input (2nd number input in the modal) and Apply
        sr = await page.evaluate(SET_REACT, [".cb-modal input[type=number]", 1, 312.0])
        log(f"[5] set High input: {sr}")
        await click_text(page, "Apply")
        await page.wait_for_timeout(800)
        applied = await page.evaluate(
            "() => { const t=document.querySelectorAll('.colorbar-ticks span');"
            " return t.length? t[t.length-1].textContent : 'NO_TICKS'; }"
        )
        log(f"[5] colorbar high tick after Apply: {applied}")
        await page.screenshot(path=f"{SHOT}/lp06_range_applied.png")

        # 6) Resize the log dock: drag its top edge UP ~120px.
        rz = await page.evaluate(
            "() => { const h=document.querySelector('.logpanel-resize'); if(!h) return null;"
            " const r=h.getBoundingClientRect(); return [r.left+r.width/2, r.top+3]; }"
        )
        h_before = await page.evaluate(
            "() => Math.round(document.querySelector('.logpanel').getBoundingClientRect().height)")
        if rz:
            await page.mouse.move(rz[0], rz[1])
            await page.mouse.down()
            await page.mouse.move(rz[0], rz[1] - 160, steps=14)
            await page.mouse.up()
            await page.wait_for_timeout(500)
        h_after = await page.evaluate(
            "() => Math.round(document.querySelector('.logpanel').getBoundingClientRect().height)")
        log(f"[6] log dock height {h_before} -> {h_after} after resize-up")
        await page.screenshot(path=f"{SHOT}/lp07_resized.png")

        # 7) Collapse the log dock.
        await page.evaluate(
            "() => document.querySelector('.logpanel-collapse').click()")
        await page.wait_for_timeout(500)
        coll = await page.evaluate(
            "() => { const p=document.querySelector('.logpanel');"
            " return (p.classList.contains('collapsed')?'collapsed ':'expanded ')+Math.round(p.getBoundingClientRect().height); }"
        )
        log(f"[7] after collapse click: {coll}")
        await page.screenshot(path=f"{SHOT}/lp08_collapsed.png")

        await browser.close()
        log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
