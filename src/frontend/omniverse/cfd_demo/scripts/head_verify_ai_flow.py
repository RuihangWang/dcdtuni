#!/usr/bin/env python3
"""Head-browser verification of the AI-prediction flow, end to end.

Five confirmation points, each screenshotted and each cross-checked against a
programmatic signal — a DOM read, the AI worker's log, or the Kit log. Nothing is
claimed from "the code looks right".

  a1  three tabs exist: Geometry / AI Prediction / Post-processing
  a2  selecting a rack shows its REAL per-server sliders (5-7 slots, power + flow,
      with the derived deltaT the model is actually conditioned on)
  a3  the AI Prediction tab reports the worker online, its GPUs, and TTA state
  a4  pressing simulate runs a real prediction and a .vtu lands in
      logger/inference_results  (~40-60 s; worker log carries the receipt)
  a5  Post-processing "Start post-processing" loads that prediction and the volume
      renders in the WebRTC viewport

Run with the Playwright env on the Xvfb :99 display — never headless browser-use, which
renders the WebRTC viewport black and grabs a GPU process.
"""
import asyncio, json, os, re, time
import shutil
import pathlib

SHOT = os.environ.get("HEAD_SHOT_DIR", "/tmp/head_ai")
URL = os.environ.get("HEAD_URL", "http://localhost:8081")
KITLOG = "/tmp/dc_cfd_demo.kit.log"
AILOG = "/tmp/dc_cfd_ai_worker.log"
# The A3 (numerical) bundle — the one the demo serves. This pointed at the
# GTC_TransFVGN_FronzenText tree long after the worker stopped writing there, so the
# "new vtu" receipt reported [] on every run that actually succeeded.
# Derived from this file's own location for the same reason as CHROME below.
_DEMO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # cfd_demo/
RESULTS = os.environ.get(
    "DC_CFD_AI_RESULTS",
    os.path.join(_DEMO, "SCANO/GTC_A3_Numerical/logger/inference_results"))
# Resolved, not hardcoded: an absolute /mnt/lv/home/<user>/... path both breaks on any
# other machine and writes the host user name into a file that ships with the repo.
CHROME = os.environ.get("HEAD_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)


def log(m): print(f"[ai] {m}", flush=True)
def size(p):
    try: return os.path.getsize(p)
    except OSError: return 0
def tail(p, pos=0):
    try:
        with open(p, "rb") as f:
            f.seek(pos); return f.read().decode("utf-8", "replace")
    except OSError: return ""


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


async def click_text(page, text):
    try:
        await page.get_by_text(text, exact=False).first.click(timeout=8000)
        log(f"clicked '{text}'"); return True
    except Exception as e:
        log(f"click '{text}' FAILED {type(e).__name__}"); return False


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME, headless=False,
            args=["--disable-gpu", "--use-gl=swiftshader", "--no-sandbox",
                  "--disable-dev-shm-usage", "--window-size=1920,1080",
                  "--autoplay-policy=no-user-gesture-required"])
        page = await (await b.new_context(
            viewport={"width": 1920, "height": 1080})).new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(12000)

        # ── a1: three tabs ───────────────────────────────────────────────────
        tabs = await page.evaluate(
            "() => [...document.querySelectorAll('.tabs .tab')].map(t=>t.textContent.trim())")
        log(f"a1 tabs = {tabs}")
        await page.screenshot(path=f"{SHOT}/a1_tabs.png")
        assert "AI Prediction" in tabs, f"AI Prediction tab missing: {tabs}"

        # ── scene ────────────────────────────────────────────────────────────
        await click_text(page, "Load CFD Geometry")
        # Wait for the listing to actually render before double-clicking. The AI tab now
        # polls /api/ai-status, so first paint is slower than it used to be and a fixed
        # 2.5 s sleep raced the fetch — the dblclick landed on an empty list and the
        # dialog just sat there.
        for _ in range(20):
            await page.wait_for_timeout(1000)
            if await page.evaluate(
                    "() => !!document.querySelector('.ds-dlg-file')"):
                break
        picked_json = await page.evaluate(
            "() => { const r=[...document.querySelectorAll('.ds-dlg-file')]"
            ".find(x=>x.textContent.includes('GTC2025.json')); if(!r) return 'NO_ROW';"
            " r.dispatchEvent(new MouseEvent('dblclick',{bubbles:true})); return 'OK'; }")
        log(f"scene json dblclick -> {picked_json}")
        for i in range(70):
            await page.wait_for_timeout(5000)
            if await page.evaluate("() => document.querySelectorAll('.tree .node-name').length"):
                log(f"scene loaded ~{(i+1)*5}s"); break
        else:
            log("scene NOT loaded; abort"); await b.close(); return

        # ── a2: per-server sliders on a rack ─────────────────────────────────
        # Racks live in .rack-sublist, revealed by the Racks row's .node-twisty.
        exp = await page.evaluate(
            """() => {
                const rows = [...document.querySelectorAll('.node-label')];
                const r = rows.find(x => /Server racks/i.test(x.textContent));
                if (!r) return 'NO_RACKS_ROW';
                const tw = r.querySelector('.node-twisty');
                if (tw) { tw.click(); return 'TWISTY'; }
                r.click(); return 'ROW';
            }""")
        await page.wait_for_timeout(1500)
        picked = await page.evaluate(
            """() => {
                const r = document.querySelector('.rack-sublist .node-label');
                if (!r) return null;
                r.click();
                return r.textContent.trim();
            }""")
        await page.wait_for_timeout(1800)
        slots = await page.evaluate("() => document.querySelectorAll('.slot').length")
        ranges = await page.evaluate(
            "() => document.querySelectorAll('.slot input[type=range]').length")
        dts = await page.evaluate(
            "() => [...document.querySelectorAll('.slot-dt')].slice(0,3).map(x=>x.textContent.trim())")
        log(f"a2 expand={exp} picked={picked} slots={slots} sliders={ranges} dT={dts}")
        await page.screenshot(path=f"{SHOT}/a2_server_sliders.png")

        # ── a3: AI tab status ────────────────────────────────────────────────
        await click_text(page, "AI Prediction")
        await page.wait_for_timeout(2500)
        status_txt = await page.evaluate(
            "() => (document.querySelector('.ai-status')||{}).textContent || ''")
        bundle_txt = await page.evaluate(
            "() => (document.querySelector('.ai-path')||{}).textContent || ''")
        log(f"a3 worker = {status_txt.strip()}")
        log(f"a3 bundle = {bundle_txt.strip()[-70:]}")
        await page.screenshot(path=f"{SHOT}/a3_ai_tab.png")

        # ── a4: run a prediction ─────────────────────────────────────────────
        before = set(os.listdir(RESULTS)) if os.path.isdir(RESULTS) else set()
        ai_pos = size(AILOG)
        t0 = time.monotonic()
        ok = await click_button(page, "simulate")
        if not ok:
            log("a4 simulate button not clickable"); await page.screenshot(path=f"{SHOT}/a4_fail.png")
        for i in range(90):
            await page.wait_for_timeout(5000)
            if re.search(r"\[ai_worker\] OK ", tail(AILOG, ai_pos)):
                log(f"a4 worker receipt after ~{(i+1)*5}s"); break
        await page.wait_for_timeout(4000)
        after = set(os.listdir(RESULTS)) if os.path.isdir(RESULTS) else set()
        new = sorted(x for x in (after - before) if x.endswith((".vtu", ".vti")))
        log(f"a4 new vtu = {new}  ({time.monotonic()-t0:.0f}s)")
        res_txt = await page.evaluate(
            "() => (document.querySelector('.ai-result')||{}).textContent || ''")
        log(f"a4 ui result = {res_txt.strip()[:160]}")
        await page.screenshot(path=f"{SHOT}/a4_predicted.png")

        # ── a4c: drag an ACU supply-temperature slider, then predict ─────────
        # This is the demo's headline interaction. ACU supply temperature is the strong
        # lever: dropping it 20 -> 14 C moved the predicted field by -0.94 K mean with
        # 84.7% of points changing, where scaling every server's power by +30% moved it
        # only 0.02 K. Verify the UI path end to end, not just the worker.
        await click_text(page, "Geometry")
        await page.wait_for_timeout(1000)
        await page.evaluate(
            """() => { const r=[...document.querySelectorAll('.node-label')]
               .find(x=>/ACU units/i.test(x.textContent)); if(r) r.click(); }""")
        await page.wait_for_timeout(1500)
        n_acu = await page.evaluate("() => document.querySelectorAll('.slot').length")
        set_ok = await page.evaluate(
            """() => {
                const rows=[...document.querySelectorAll('.slot')];
                let n=0;
                for (const row of rows) {
                    const r=row.querySelector('input[type=range]');
                    if(!r) continue;
                    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value')
                          .set.call(r, '14');
                    r.dispatchEvent(new Event('input',{bubbles:true}));
                    r.dispatchEvent(new Event('change',{bubbles:true}));
                    n++;
                }
                return n;
            }""")
        log(f"a4c ACU rows={n_acu} sliders set to 14C={set_ok}")
        await page.screenshot(path=f"{SHOT}/a4c_acu_sliders.png")
        await click_text(page, "AI Prediction")
        await page.wait_for_timeout(1500)
        ai_pos3 = size(AILOG)
        await click_button(page, "simulate")
        for i in range(120):
            await page.wait_for_timeout(5000)
            if re.search(r"\[ai_worker\] OK ", tail(AILOG, ai_pos3)):
                log(f"a4c prediction after ~{(i+1)*5}s"); break
        res3 = await page.evaluate(
            "() => (document.querySelector('.ai-result')||{}).textContent || ''")
        log(f"a4c result = {res3.strip()[:150]}")
        await page.screenshot(path=f"{SHOT}/a4c_predicted.png")

        # ── a4b: switch scenario and predict again ───────────────────────────
        # The whole point of the demo is that a different operating point yields a
        # different field, so verify that rather than assuming it.
        opts = await page.evaluate(
            "() => [...document.querySelectorAll('.scenario-sel option')].map(o=>o.value)")
        log(f"a4b scenarios = {opts}")
        if len(opts) > 1:
            await page.select_option(".scenario-sel", opts[-1])
            await page.wait_for_timeout(800)
            ai_pos2 = size(AILOG)
            await click_button(page, "simulate")
            for i in range(90):
                await page.wait_for_timeout(5000)
                if re.search(r"\[ai_worker\] OK ", tail(AILOG, ai_pos2)):
                    log(f"a4b second prediction after ~{(i+1)*5}s"); break
            res2 = await page.evaluate(
                "() => (document.querySelector('.ai-result')||{}).textContent || ''")
            log(f"a4b result = {res2.strip()[:160]}")
            await page.screenshot(path=f"{SHOT}/a4b_scenario2.png")

        # ── a5: post-processing renders it ───────────────────────────────────
        await click_text(page, "Post-processing")
        await page.wait_for_timeout(1500)
        kit_pos = size(KITLOG)
        await click_button(page, "Start post-processing")
        for i in range(72):
            await page.wait_for_timeout(5000)
            if "load_dataset OK" in tail(KITLOG, kit_pos):
                log(f"a5 load_dataset OK ~{(i+1)*5}s"); break
        await page.wait_for_timeout(6000)
        await click_button(page, "Temp")
        for i in range(48):
            await page.wait_for_timeout(5000)
            if "volume rebuilt field=T" in tail(KITLOG, kit_pos):
                log(f"a5 volume rebuilt ~{(i+1)*5}s"); break
        await page.wait_for_timeout(8000)
        await page.screenshot(path=f"{SHOT}/a5_volume.png")

        log("================ RECEIPTS ================")
        log(f"tabs: {tabs}")
        log(f"slots={slots} sliders={ranges}")
        log(f"worker: {status_txt.strip()}")
        log(f"new vtu: {new}")
        for pat in (r"\[ai_worker\] OK .*", r"load_dataset OK", r"volume rebuilt field=T[^\\n]{0,90}"):
            src = AILOG if "ai_worker" in pat else KITLOG
            hits = re.findall(pat, tail(src))
            log(f"  [{pat[:28]}] -> {hits[-1][:130] if hits else 'NONE'}")
        log("=========================================")
        await b.close(); log("DONE (browser closed)")


if __name__ == "__main__":
    asyncio.run(main())
