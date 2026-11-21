#!/usr/bin/env python3
"""Head-browser driver for the .cfdproj geometry source.

Loads the project named by CFDPROJ_PATH through the real "Load CFD Geometry" dialog, then isolates the racks so the shot can be
compared against the same project in the STLGen Three.js editor: walls and the top floor
are hidden, which is what lets the camera see the rack interiors and the server faces.

Run:

    CFDPROJ_PATH=/abs/path.cfdproj HEAD_URL=http://localhost:8081 \\
    HEAD_SHOT_DIR=/tmp/cmp DISPLAY=:99 $BROWSER_USE_PY scripts/head_verify_cfdproj.py

Waits are fixed windows, not polls: the WebRTC command channel is one-way, so the page
never learns when Kit finished. Cross-check the receipts in /tmp/dc_cfd_demo.kit.log.
"""
import asyncio
import os
import pathlib
import shutil
import sys

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_verify_cfdproj")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m):
    print(f"[cfdproj] {m}", flush=True)


async def shot(page, name):
    p = os.path.join(SHOT, name)
    await page.screenshot(path=p)
    log(f"shot -> {p}")
    return p


async def click_text(page, text, timeout=8000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}: {str(e)[:90]}")
        return False


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME,
            headless=False,                       # TRUE HEAD (Xvfb :99)
            args=["--disable-gpu", "--use-gl=swiftshader",   # software decode, no GPU
                  "--no-sandbox", "--disable-dev-shm-usage",
                  "--window-size=1920,1080",
                  "--autoplay-policy=no-user-gesture-required"],
        )
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("console", lambda m: log(f"  console[{m.type}] {m.text[:110]}")
                if m.type == "error" else None)

        log(f"open {URL}")
        await page.goto(URL, wait_until="domcontentloaded")
        await asyncio.sleep(12)                   # WebRTC connect
        await shot(page, "c01_connected.png")

        if not await click_text(page, "Load CFD Geometry"):
            log("FATAL: load button not found")
            await browser.close()
            return 1
        # The button opens a file dialog on the Kit host — the source is a RUNTIME
        # choice, not a startup env var. Type the .cfdproj path straight into the path
        # field rather than clicking through directories.
        await asyncio.sleep(3)
        await shot(page, "c01b_dialog.png")
        proj = os.environ.get("CFDPROJ_PATH", "")
        typed = await page.evaluate(
            """(p) => {
                const el = document.querySelector('.ds-dlg-path');
                if (!el) return 'NO_PATH_BOX';
                Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                      .set.call(el, p);
                el.dispatchEvent(new Event('input',  { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
                return 'OK ' + el.value;
            }""", proj)
        log(f"path field: {typed}")
        await asyncio.sleep(2)
        await shot(page, "c01c_path_typed.png")
        # Generated geometry is far cheaper to place than the GTC asset references, but
        # Hydra's first-frame sync still dominates, and it scales with quad count —
        # Base2-9 authors 2801 quads against Base_leakage's 88. Override per case.
        await asyncio.sleep(float(os.environ.get("HEAD_GEO_WAIT", "90")))
        await shot(page, "c02_geometry.png")

        # Model-tree state, read back from the DOM rather than assumed. The rows come
        # from the manifest Kit writes, so this is also the check that the manifest
        # arrived at all.
        labels = await page.eval_on_selector_all(
            ".tree .node-label", "els => els.map(e => e.textContent.trim())")
        log(f"tree ({len(labels)}): {labels[:14]}")
        mf = await page.evaluate(
            "() => fetch('/api/scene-tree').then(r => r.json())"
            "      .then(j => j.error ? 'ERR ' + j.error"
            "        : j.source + ' ' + j.project + ' objects=' + j.objects + ' ['"
            "          + j.categories.map(c => c.id + ':' + c.count).join(' ') + ']')"
            "      .catch(e => 'FETCH ' + e)")
        log(f"manifest: {mf}")

        # Expand every category + the first rack, so the shot shows the real hierarchy
        # (issue 6: the tree must have the editor's levels, not one flat list).
        if os.environ.get("HEAD_EXPAND"):
            n = await page.evaluate(
                """() => {
                    let hit = 0;
                    for (let pass = 0; pass < 3; pass++) {
                        for (const t of document.querySelectorAll('.node-twisty')) {
                            if (t.textContent.trim() === '▸') { t.click(); hit++; }
                        }
                    }
                    return hit;
                }""")
            log(f"expanded {n} node(s)")
            await asyncio.sleep(1)
            await shot(page, "c02b_tree_expanded.png")
            rows = await page.eval_on_selector_all(
                "li.tree-node > .node-label",
                "els => els.map(e => e.textContent.trim())")
            log(f"tree rows after expand ({len(rows)}):")
            for r in rows[:26]:
                log(f"    {r}")

        # Hide the groups that occlude the racks. Match the VISIBLE tree label, not the
        # internal group name — the tree renders GeoGroup.label ("Room walls"), while the
        # backend command takes GeoGroup.name ("Walls"), and matching on the latter finds
        # nothing.
        # HEAD_HIDE="none" shows everything — needed to verify that floors and walls
        # RENDER at all, which hiding them by default had never actually demonstrated.
        # Labels are now the EDITOR's category strings ("Wall (WALL)"), not the old
        # hardcoded GTC ones ("Room walls") — matching on the stale text found nothing
        # and reported "0 toggled" while the ceiling kept occluding the scene.
        _hide_env = os.environ.get("HEAD_HIDE", "")
        _groups = ([] if _hide_env == "none"
                   else _hide_env.split("|") if _hide_env
                   else ["Wall (WALL)", "Floor (FLOOR)", "Baffle (BAFFLE)"])
        for group in _groups:
            n = await page.evaluate(
                """(g) => {
                    // The row class is `tree-node`, not `node`, and the label text is
                    // the group name PLUS an icon glyph and sometimes a count badge —
                    // so match by containment, not equality. The earlier selector found
                    // nothing and reported "0 toggled" on every run without failing,
                    // which is why the ceiling kept occluding the scene.
                    const rows = [...document.querySelectorAll('li.tree-node')];
                    let hit = 0;
                    for (const r of rows) {
                        const lab = r.querySelector('.node-label');
                        const cb  = r.querySelector('input.node-vis');
                        if (lab && cb && lab.textContent.includes(g) && cb.checked) {
                            cb.click(); hit++;
                        }
                    }
                    return hit;
                }""", group)
            log(f"hide {group}: {n} checkbox(es) toggled")
            await asyncio.sleep(4)
        await shot(page, "c03_racks_isolated.png" if _groups else "c03_all_shown.png")

        # HEAD_SHOW: tick a group that came up UNCHECKED and prove it appears. This is
        # the check that matters for objects the project disables — the editor hides
        # them, but OV must still be able to draw them on demand.
        for group in (os.environ.get("HEAD_SHOW", "") or "").split("|"):
            if not group:
                continue
            n = await page.evaluate(
                """(g) => {
                    const rows = [...document.querySelectorAll('li.tree-node')];
                    let hit = 0;
                    for (const r of rows) {
                        const lab = r.querySelector('.node-label');
                        const cb  = r.querySelector('input.node-vis');
                        if (lab && cb && lab.textContent.includes(g) && !cb.checked) {
                            cb.click(); hit++;
                        }
                    }
                    return hit;
                }""", group)
            log(f"show {group}: {n} checkbox(es) toggled")
            await asyncio.sleep(5)
        if os.environ.get("HEAD_SHOW"):
            await shot(page, "c04_shown.png")

        await browser.close()
        log("DONE (browser closed)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
