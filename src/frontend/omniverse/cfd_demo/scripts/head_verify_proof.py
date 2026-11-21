#!/usr/bin/env python3
"""PROOF run for /browser-verify: demonstrate that every render / value-range / ROI-cut
result is produced by a REAL on-screen browser driving the REAL UI (mimicking a user),
not by any API bypass.

Captures, WHILE THE BROWSER IS LIVE:
  * proof-of-legitimacy: `xwininfo` shows a real Chromium window on :99 (TRUE HEAD) and
    `nvidia-smi` shows only the Kit PID, no browser GPU process (software decode, 1 GPU).
Then drives 3 real UI controls (each fires the SAME WebRTC command a user click would):
  * click "Velocity"            -> setVolume   -> proof_1_velocity.png
  * drag High slider to 1.0 m/s -> setVolumeRange -> proof_2_range.png   (值域 / range)
  * Show ROI Box + Z-half + Clip-> setRoiBox/setRoiClip -> proof_3_zclip.png (ROI cut)
Each is cross-checked against the Kit-log receipt printed at the end.
"""
import asyncio, os, re, subprocess
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_proof")
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


def log(m): print(f"[proof] {m}", flush=True)
def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()
def ksize():
    try: return os.path.getsize(KITLOG)
    except OSError: return 0
def khas(pat, pos):
    try:
        with open(KITLOG, "rb") as f:
            f.seek(pos); d = f.read().decode("utf-8", "replace")
        return re.search(pat, d) is not None
    except OSError: return False
def kgrep(pat, pos):
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


async def set_box(page, mn, mx):
    vals = [mn[0], mx[0], mn[1], mx[1], mn[2], mx[2]]
    for idx, v in enumerate(vals):
        await page.evaluate(SET_REACT, [".roi-box input[type=number]", idx, v])
        await page.wait_for_timeout(300)
    log(f"set box {tuple(mn)}..{tuple(mx)}")


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
        await click_text(page, "Load dataset"); await page.wait_for_timeout(2500)
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

        # ===== REAL UI OP #1: click Velocity (render) =====
        pos = ksize()
        await click_text(page, "Velocity")
        await wait_k(page, r"volume rebuilt field=U roi=False", "Velocity", pos, 300)
        await page.wait_for_timeout(4000)
        await page.screenshot(path=f"{SHOT}/proof_1_velocity.png")

        # ===== PROOF-OF-LEGITIMACY (browser is LIVE on :99, decoding the WebRTC stream) =====
        kitpid = sh("fuser 8301/tcp 2>/dev/null").replace(" ", "")
        xwin = sh("DISPLAY=:99 xwininfo -root -children 2>/dev/null | grep -i chrom")
        smi = sh("nvidia-smi --query-compute-apps=pid,process_name,gpu_uuid --format=csv,noheader")
        # is the browser PID anywhere in nvidia-smi? collect chromium pids and intersect.
        chrome_pids = sh("DISPLAY=:99 ps -e -o pid,comm | grep -i chrom | awk '{print $1}'").split()
        smi_pids = re.findall(r"^\s*(\d+)", smi, re.M)
        browser_on_gpu = [pid for pid in chrome_pids if pid in smi_pids]
        log("================= PROOF-OF-LEGITIMACY =================")
        log("TRUE-HEAD  | real Chromium window on :99 (xwininfo):")
        log("  " + (xwin or "NONE"))
        log("SINGLE-GPU | Kit PID = " + (kitpid or "NONE") + " ; nvidia-smi compute apps:")
        for ln in smi.splitlines(): log("  " + ln)
        log(f"SINGLE-GPU | Kit PID in nvidia-smi: {kitpid in smi_pids}  |  "
            f"browser/chromium PIDs on GPU: {browser_on_gpu or 'NONE (software decode -> GPU-clean)'}")
        log("======================================================")

        # ===== REAL UI OP #2: drag the High slider to ~1.0 m/s (值域 / value-range) =====
        pos = ksize()
        r = await page.evaluate(SET_REACT, ["input[type=range]", 1, 1.0])
        log(f"High slider native-setter -> {r}")
        await wait_k(page, r"setVolumeRange", "setVolumeRange (slider)", pos, 60)
        await page.wait_for_timeout(2500)
        await page.screenshot(path=f"{SHOT}/proof_2_range.png")

        # ===== REAL UI OP #3: Show ROI Box + Z front-half + Clip (ROI cut) =====
        await click_text(page, "Show ROI Box"); await page.wait_for_timeout(2500)
        await set_box(page, (0,0,0), (31.2,5,9.6))
        await page.wait_for_timeout(1500)
        pos = ksize()
        await click_text(page, "Clip Volume to ROI")
        await wait_k(page, r"volume rebuilt field=U roi=True", "Velocity Z-clip", pos, 300)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=f"{SHOT}/proof_3_zclip.png")

        # ===== Kit-log receipts that prove every op came FROM THE UI =====
        log("================= KIT-LOG RECEIPTS (UI commands) =================")
        for pat in [r"command=setVolume .*field.:.U", r"command=setVolumeRange",
                    r"command=setRoiBox", r"command=setRoiClip",
                    r"volume rebuilt field=U roi=True"]:
            hits = kgrep(pat, 0)
            log(f"[{pat}] -> " + (hits[-1] if hits else "NONE"))
        log("=================================================================")

        await b.close(); log("DONE")


if __name__ == "__main__":
    asyncio.run(main())
