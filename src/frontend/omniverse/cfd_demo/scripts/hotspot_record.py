#!/usr/bin/env python3
"""Render the hotspot frames through the real Omniverse viewport.

For every `.vtu` that `hotspot_frames.py` produced, this loads it into the live Kit
session and screenshots the viewport twice — once as a temperature volume, once with
streamlines on top — so one dataset load feeds BOTH animations. Output:

    <shots>/thermal/f###.png      the volume alone
    <shots>/flow/f###.png         the volume plus streamlines coloured by T
    <shots>/record.json           what was captured, and the domain it was pinned to

Head browser, never headless. The viewport is a WebRTC H.264 stream, not WebGL:
`browser-use` renders it black and takes a GPU process. This is Playwright with
`headless=False` on the Xvfb `:99` display and software decode, the same recipe every
`head_verify_*.py` uses.

Two invariants carry the whole animation:

  * **The camera never moves.** Set once by the geometry load, then untouched. A
    drifting camera turns a field change into a camera change.
  * **The colour domain is pinned** to the p1-p99 union over all three anchors, re-sent
    after every `setVolume`. The React controls reset the domain to the CURRENT
    dataset's own p1-p99 on each field selection, so without this the map renormalises
    per frame and the hotspot's growth and removal both render as no change at all.

Commands go through `window.__dcSend` rather than the buttons — see the note beside
that hook in `web/src/streamMessages.ts`. Waiting is on the Kit log's own echoes
(`volume rebuilt`, `streamlines generated`), not on fixed sleeps, because the command
channel is one-way and a fixed sleep is either a stall or a truncated frame.

    HOTSPOT_SHOTS=/tmp/hotspot DISPLAY=:99 $BROWSER_USE_PY hotspot_record.py
    ... hotspot_record.py 0 5      # re-record frames 0..5 only
"""
import asyncio
import json
import os
import pathlib
import re
import shutil
import sys
import time

from PIL import Image, ImageChops, ImageStat

REPO = pathlib.Path(__file__).resolve().parents[5]
CASE = pathlib.Path(os.environ.get("HOTSPOT_CASE", REPO / "case/GTC"))
# Artefacts live under case/GTC/forOVdemo/, which .gitignore excludes: the anchors, the
# 31 interpolated frames and the viewport shots come to ~6.6 GB per scenario and have no
# business in git. Only the finished videos (copied into the project page's assets), the
# ACU position file and this code are tracked.
ART = pathlib.Path(os.environ.get("HOTSPOT_ART", CASE / "forOVdemo"))
FRAMES = pathlib.Path(os.environ.get("HOTSPOT_FRAMES", ART / "hotspot_frames"))
SHOTS = pathlib.Path(os.environ.get("HOTSPOT_SHOTS", ART / "hotspot_shots"))
CFDPROJ = os.environ.get("HOTSPOT_CFDPROJ", str(CASE / "GTC_2025.cfdproj"))
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = os.environ.get("KITLOG", "/tmp/dc_cfd_demo.kit.log")
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")

# The viewport sits between the two overlay panels; cropping to it keeps the page's own
# chrome out of the animation. Tighter than the gallery's box on both sides: reading the
# model tree expands the left panel past x=300, and the right inspector chip starts at
# about x=1528, so a gallery-width crop catches a strip of UI on every single frame.
VIEW = {"x": 356, "y": 40, "width": 1168, "height": 800}

# Room-filling seeding. The UI default is 27 seeds in a 2 m sphere, which is tuned for a
# small project and draws only a handful of curves at 31 x 19 m — the gallery had to go
# to 900 for the streamline shot to read as flow rather than as stray lines.
SEED = {"center": [15.5, 2.5, 9.6], "radius": 8.0, "nSeeds": 900, "maxSteps": 1500}

# The false ceiling and any ceiling-height slab: opaque, room-spanning, and directly
# between the camera and the field.
HIDE_PATTERN = os.environ.get("HOTSPOT_HIDE", "ceiling|false.?ceiling|吊顶")
GEO_WAIT = float(os.environ.get("HOTSPOT_GEO_WAIT", "90"))
VOL_TIMEOUT = int(os.environ.get("HOTSPOT_VOL_TIMEOUT", "240"))
# Re-recording only the thermal half is the common case once the flow frames are good:
# the streamline pass is the slow part and its output does not change.
SKIP_FLOW = bool(os.environ.get("HOTSPOT_SKIP_FLOW"))
SKIP_THERMAL = bool(os.environ.get("HOTSPOT_SKIP_THERMAL"))
# Hide the volume before tracing, so the flow animation is about the FLOW. Left on, the
# failure state renders as an opaque red block that swallows every curve -- the two
# animations then differ only in their caption for a third of their length.
FLOW_NO_VOLUME = bool(os.environ.get("HOTSPOT_FLOW_NO_VOLUME"))
# "The viewport is back to geometry only." Hiding the room shell alone moves the delta by
# ~2, so the bar sits just above that and well under a rendered volume's 8-20.
CLEAR_MAX_DELTA = float(os.environ.get("HOTSPOT_CLEAR_DELTA", "3.0"))
# Mean luminance change over the crop that counts as "the field is on screen".
# Measured, not guessed: showing the volume moves this by 8-20 depending on how hot the
# frame is, while merely hiding the room shell moves it by ~2. A 1.0 threshold therefore
# passed instantly on the shell change and captured bare geometry for a whole 31-frame
# run -- the volume then turned up in the streamline shot taken 15 s later, which is how
# the timing was finally pinned down. 5.0 sits above the shell artefact and below the
# faintest real volume (the cold S2 state).
VOL_MIN_DELTA = float(os.environ.get("HOTSPOT_VOL_DELTA", "5.0"))
SL_MIN_DELTA = float(os.environ.get("HOTSPOT_SL_DELTA", "0.5"))
SL_TIMEOUT = int(os.environ.get("HOTSPOT_SL_TIMEOUT", "300"))


def log(m):
    print(f"[rec] {m}", flush=True)


def ksize():
    try:
        return os.path.getsize(KITLOG)
    except OSError:
        return 0


def kfind(pattern, pos):
    """Return the matching Kit-log lines written after byte offset `pos`."""
    try:
        with open(KITLOG, "rb") as f:
            f.seek(pos)
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if re.search(pattern, ln)]


async def wait_kit(page, pattern, label, pos, max_s):
    """Poll the Kit log for a completion echo. Returns the line, or None on timeout."""
    t0 = time.time()
    while time.time() - t0 < max_s:
        await page.wait_for_timeout(2000)
        hits = kfind(pattern, pos)
        if hits:
            log(f"    {label} after {time.time() - t0:.0f}s :: {hits[-1][-110:]}")
            return hits[-1]
    log(f"    {label} NOT seen in {max_s}s")
    return None


def viewport_delta(a, b):
    """Mean absolute luminance difference between two viewport screenshots."""
    ia = Image.open(a).convert("L")
    ib = Image.open(b).convert("L")
    return ImageStat.Stat(ImageChops.difference(ia, ib)).mean[0]


async def wait_until_changed(page, baseline, tmp, label, min_delta, max_s):
    """Screenshot until the viewport visibly differs from `baseline`.

    The backend logs `volume rebuilt` when its own 30 app cycles are done, but the IndeX
    volume takes far longer than that to appear in the STREAMED frame. Screenshotting on
    the log line captured bare geometry every time -- an entire 16-frame run came back
    with no field in it, and the log said visible=True for all of them. So the readiness
    signal is the picture itself: poll until it stops looking like the geometry-only
    baseline. That doubles as the guard the run needed, because a frame that never
    changes is reported instead of silently shipped.
    """
    t0 = time.time()
    while time.time() - t0 < max_s:
        await page.screenshot(path=str(tmp), clip=VIEW)
        d = viewport_delta(baseline, tmp)
        if d >= min_delta:
            log(f"    {label} visible after {time.time() - t0:.0f}s (delta {d:.2f})")
            return d
        await page.wait_for_timeout(4000)
    log(f"    {label} NEVER appeared in {max_s}s (delta {d:.2f}) -- frame will be flagged")
    return None


async def wait_until_similar(page, baseline, tmp, label, max_delta, max_s):
    """Screenshot until the viewport looks like `baseline` again.

    Phase one of the two-phase readiness check. `loadDataset` removes the volume prim, so
    the viewport falls back to bare geometry before the next volume is built. Without
    waiting for that fall-back, the "is the volume up?" test passes on the PREVIOUS
    frame's volume, still on screen, and every frame captures the one before it -- which
    is exactly what happened: 31 thermal frames each differing from geometry by 15.8 but
    from each other by 1.9, i.e. one field rendered 31 times.
    """
    t0 = time.time()
    d = None
    while time.time() - t0 < max_s:
        await page.screenshot(path=str(tmp), clip=VIEW)
        d = viewport_delta(baseline, tmp)
        if d <= max_delta:
            log(f"    {label} after {time.time() - t0:.0f}s (delta {d:.2f})")
            return d
        await page.wait_for_timeout(3000)
    log(f"    {label} did NOT clear in {max_s}s (delta {d:.2f})")
    return None


async def send(page, command, args):
    r = await page.evaluate(
        """([c, a]) => {
            const f = window.__dcSend;
            if (!f) return 'NO_HOOK';
            f(c, a);
            return 'sent';
        }""", [command, args])
    if r == "NO_HOOK":
        raise SystemExit("window.__dcSend missing — is the Vite dev server serving the "
                         "build that carries the recording hook?")
    return r


async def click_text(page, text, timeout=10000):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=timeout)
        log(f"clicked '{text}'")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"click '{text}' FAILED: {type(e).__name__}")
        return False


async def type_path(page, value):
    """The file dialog is a controlled React input: a plain .value assignment fires no
    onChange, so go through the native prototype setter."""
    return await page.evaluate(
        """(p) => {
            const el = document.querySelector('.ds-dlg-path');
            if (!el) return 'NO_PATH_BOX';
            Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                  .set.call(el, p);
            el.dispatchEvent(new Event('input',  { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            return 'OK';
        }""", value)


async def tree_labels(page):
    """Every model-tree row label, so what gets hidden is chosen from what exists."""
    return await page.evaluate(
        """() => [...document.querySelectorAll('li.tree-node')].map(r => {
            const l = r.querySelector('.node-label'), c = r.querySelector('input.node-vis');
            return l ? (l.textContent.trim() + (c && c.checked ? ' [on]' : ' [off]')) : null;
        }).filter(Boolean)""")


async def hide_groups(page, pattern):
    """Untick every ticked tree row whose label matches `pattern`.

    The false ceiling is an opaque slab spanning the whole room. `setVolume` already
    hides the room SHELL, but on a .cfdproj-derived scene the ceiling is its own object
    and survives -- which renders as a grey lid with the entire field behind it. Going
    through the checkbox rather than setObjectVisible keeps the tree's state honest.
    """
    return await page.evaluate(
        """(pat) => {
            const re = new RegExp(pat, 'i');
            const hit = [];
            for (const r of document.querySelectorAll('li.tree-node')) {
                const l = r.querySelector('.node-label'), c = r.querySelector('input.node-vis');
                if (l && c && c.checked && re.test(l.textContent)) {
                    c.click(); hit.push(l.textContent.trim());
                }
            }
            return hit;
        }""", pattern)


async def main():
    meta = json.loads((FRAMES / "frames.json").read_text())
    vmin, vmax = meta["colourDomainK"]
    # Deliberately-wrong domain, for the control run that proves the pin is doing
    # anything at all. The backend's own _DISPLAY_DOMAIN is a CONSTANT (292.1-309.0 K,
    # tuned for AI predictions), so an unpinned animation does not renormalise per
    # frame -- it just renders every frame through the wrong window. That failure is
    # invisible in the log, so it has to be shown by rendering one frame twice.
    if os.environ.get("HOTSPOT_DOMAIN"):
        vmin, vmax = (float(x) for x in os.environ["HOTSPOT_DOMAIN"].split(","))
        log(f"DOMAIN OVERRIDE {vmin}..{vmax} K (control run)")
    frames = meta["frames"]
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else len(frames) - 1
    todo = [f for f in frames if lo <= f["index"] <= hi]
    log(f"{len(todo)} frame(s), colour domain pinned to {vmin:.2f}..{vmax:.2f} K")

    for sub in ("thermal", "flow"):
        (SHOTS / sub).mkdir(parents=True, exist_ok=True)

    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=CHROME, headless=False,
            args=["--disable-gpu", "--use-gl=swiftshader", "--no-sandbox",
                  "--disable-dev-shm-usage", "--window-size=1920,1080",
                  "--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(14000)
        log("stream connected (resetScene has fired for this page load)")

        # Geometry once. The camera it frames is the camera for every frame that
        # follows, so nothing after this may touch the view.
        await click_text(page, "Load CFD Geometry")
        await page.wait_for_timeout(3000)
        await type_path(page, CFDPROJ)
        await page.wait_for_timeout(int(GEO_WAIT * 1000))
        labels = await tree_labels(page)
        log(f"model tree ({len(labels)} rows): {labels}")
        hidden = await hide_groups(page, HIDE_PATTERN)
        log(f"hidden as occluders: {hidden or 'none matched'}")
        await page.wait_for_timeout(6000)
        geo_ref = SHOTS / "00_geometry.png"
        await page.screenshot(path=str(geo_ref), clip=VIEW)
        log(f"geometry loaded -> {geo_ref} (this is the no-volume reference)")

        record = {"colourDomainK": [vmin, vmax], "seed": SEED, "view": VIEW,
                  "peakRiseK": meta["peakRiseK"], "captured": []}
        for f in todo:
            i = f["index"]
            path = str((FRAMES / f["file"]).resolve())
            log(f"f{i:03d} [{f['segment']}] {f['from']}->{f['to']} a={f['alpha']}")

            pos = ksize()
            await send(page, "loadDataset", {"path": path})
            if not await wait_kit(page, r"load_dataset (OK|FAILED)", "dataset", pos, 240):
                log(f"    f{i:03d}: dataset never confirmed, skipping")
                continue

            # Volume, then the pinned domain — in that order. setVolume is what
            # re-voxelises; the range must land on the volume that exists.
            # Phase 1: the old volume must leave the screen before the new one is
            # judged present, or the check passes on a stale picture.
            await wait_until_similar(page, geo_ref, SHOTS / "_probe.png",
                                     "old volume cleared", CLEAR_MAX_DELTA, 90)

            pos = ksize()
            await send(page, "setVolume", {"field": "T", "visible": True})
            await wait_kit(page, r"volume rebuilt", "volume built", pos, VOL_TIMEOUT)
            # AFTER the rebuild, never with it. The backend pins its own display
            # domain to the CURRENT dataset's p1-p99 while building, so a range sent
            # alongside setVolume is simply overwritten -- measured: a request for
            # 292.0-320.0 K came back as domain_set=(292.1, 309.0), the frame's own
            # extent. Re-sending afterwards is what makes the domain hold across frames.
            # `handle_set_volume_range` writes the colormap prim's `domain` attribute and
            # returns; IndeX re-reads the transfer function with no dirty flag and the
            # handler logs nothing, so there is no echo to wait for. Settle instead, and
            # let the S0-vs-S1 smoke test be what proves the pin held.
            await send(page, "setVolumeRange", {"min": vmin, "max": vmax})
            vol_delta = await wait_until_changed(
                page, geo_ref, SHOTS / "_probe.png", "volume", VOL_MIN_DELTA, VOL_TIMEOUT)
            await page.wait_for_timeout(2000)
            thermal = (SHOTS / "_flowbase.png" if SKIP_THERMAL
                       else SHOTS / "thermal" / f"f{i:03d}.png")
            await page.screenshot(path=str(thermal), clip=VIEW)

            if SKIP_FLOW:
                record["captured"].append({
                    "index": i, "segment": f["segment"], "caption": f["caption"],
                    "alpha": f["alpha"], "peakC": f["peakC"],
                    "thermal": thermal.name, "flow": thermal.name,
                    "thermalBytes": thermal.stat().st_size, "flowBytes": 0,
                    "volumeDelta": vol_delta, "streamlineDelta": None})
                log(f"    f{i:03d} thermal only "
                    f"({thermal.stat().st_size // 1024} KB, delta {vol_delta})")
                continue

            if FLOW_NO_VOLUME:
                await send(page, "setVolume", {"field": "T", "visible": False})
                await wait_until_similar(page, geo_ref, SHOTS / "_probe.png",
                                         "volume hidden for flow", CLEAR_MAX_DELTA, 90)

            pos = ksize()
            await send(page, "setStreamlines", {
                "enabled": True, "field": "T", "seedCenter": SEED["center"],
                "seedRadius": SEED["radius"], "nSeeds": SEED["nSeeds"],
                "maxSteps": SEED["maxSteps"], "direction": "forward"})
            await wait_kit(page, r"streamlines (generated|FAILED)", "streamlines built",
                           pos, SL_TIMEOUT)
            sl_delta = await wait_until_changed(
                page, thermal, SHOTS / "_probe.png", "streamlines", SL_MIN_DELTA,
                SL_TIMEOUT)
            await page.wait_for_timeout(2000)
            flow = SHOTS / "flow" / f"f{i:03d}.png"
            await page.screenshot(path=str(flow), clip=VIEW)

            # Off again: the next frame's load tears them down anyway, but leaving them
            # on means the next volume renders behind a stale set of curves.
            await send(page, "setStreamlines", {
                "enabled": False, "field": "T", "seedCenter": SEED["center"],
                "seedRadius": SEED["radius"], "nSeeds": SEED["nSeeds"],
                "maxSteps": SEED["maxSteps"], "direction": "forward"})
            await page.wait_for_timeout(1500)

            record["captured"].append({
                "index": i, "segment": f["segment"], "caption": f["caption"],
                "alpha": f["alpha"], "peakC": f["peakC"],
                "thermal": f"f{i:03d}.png", "flow": flow.name,
                "thermalBytes": thermal.stat().st_size,
                "flowBytes": flow.stat().st_size,
                "volumeDelta": vol_delta, "streamlineDelta": sl_delta})
            if vol_delta is None:
                log(f"    f{i:03d}: NO VOLUME in this frame")
            log(f"    f{i:03d} captured "
                f"({thermal.stat().st_size // 1024} KB / {flow.stat().st_size // 1024} KB)")

        (SHOTS / "record.json").write_text(json.dumps(record, indent=2,
                                                      ensure_ascii=False))
        log(f"{len(record['captured'])}/{len(todo)} frames -> {SHOTS}")
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
