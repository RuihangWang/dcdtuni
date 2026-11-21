#!/usr/bin/env python3
"""Browser verification of the SCANO project page — every module, every button.

Each check drives the real DOM and asserts on a value read back AFTER the
interaction, so "the handler is wired" is never inferred from the source. A
screenshot accompanies each one.

  p1  page loads; nav, hero and all figures resolve (no broken images)
  p2  every nav anchor scrolls to its section and takes the active underline
  p3  the speed chart renders bars, and the warm/cold toggle swaps the numbers
  p4  the A/B comparison handle drags and re-clips the overlay
  p5  clicking a figure opens the lightbox; Escape closes it
  p6  the BibTeX copy button reports success (works over plain HTTP too)
  p7  both demo buttons point at <this host>:8081, not a baked-in localhost
  p8  back-to-top appears after scrolling and returns to the top

Runs headless: this page is ordinary DOM, not the WebRTC viewport, so the
head-browser rule for the Omniverse demo does not apply here.
"""
import asyncio, os, pathlib, shutil, sys

URL = os.environ.get("PAGE_URL", "http://localhost:8090/")
SHOT = os.environ.get("PAGE_SHOT_DIR", "/tmp/page_shots")
# Resolved, never hardcoded. An absolute /mnt/lv/home/<user>/... path put the host
# username in a file that the static server used to hand out, and the paper is under
# double-blind review — that username is one search away from the authors.
CHROME = os.environ.get("PAGE_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(SHOT, exist_ok=True)

fails = []


def log(m): print(f"[page] {m}", flush=True)


def check(name, ok, detail=""):
    log(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")
    if not ok:
        fails.append(f"{name}: {detail}")


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, executable_path=CHROME,
            args=["--disable-gpu", "--use-gl=swiftshader"])
        page = await (await browser.new_context(
            viewport={"width": 1440, "height": 950})).new_page()

        console_errors = []
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        # ── p1: load + assets ────────────────────────────────────────
        await page.goto(URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(1500)

        title = await page.title()
        check("p1 页面标题", "SCANO" in title, title)

        nav = await page.evaluate(
            "() => [...document.querySelectorAll('.navlinks a')].map(a=>a.textContent.trim())")
        check("p1 导航 7 项", len(nav) == 7, str(nav))

        # Only images that actually declare a source can be "broken". The lightbox
        # <img> intentionally carries no src until a figure is clicked, and counting it
        # reported a phantom [None] failure on a page where every figure loaded.
        broken = await page.evaluate(
            "() => [...document.images]"
            ".filter(i => i.getAttribute('src'))"
            ".filter(i => !i.complete || i.naturalWidth === 0)"
            ".map(i => i.getAttribute('src'))")
        check("p1 无破图", not broken, str(broken))
        n_img = await page.evaluate("() => document.images.length")
        log(f"p1 图片数 = {n_img}")
        await page.screenshot(path=f"{SHOT}/p1_hero.png")

        # ── p2: nav anchors ──────────────────────────────────────────
        ids = ["case", "speed", "stack", "tta", "demo", "results", "cite"]
        bad = []
        for i, sid in enumerate(ids):
            await page.click(f'.navlinks a[href="#{sid}"]')
            await page.wait_for_timeout(1200)
            state = await page.evaluate(
                """(sid) => {
                    const s = document.getElementById(sid);
                    const r = s.getBoundingClientRect();
                    const a = document.querySelector('.navlinks a.active');
                    // The final section is shorter than the viewport, so the page hits
                    // its scroll limit before that section's top reaches the nav.
                    // Landing at the very bottom is the correct outcome there.
                    const atEnd = window.innerHeight + window.scrollY
                                  >= document.body.scrollHeight - 4;
                    return {near: Math.abs(r.top) < 140 || atEnd,
                            active: a ? a.getAttribute('href') : null};
                }""", sid)
            if not state["near"] or state["active"] != f"#{sid}":
                bad.append(f"{sid}({state})")
        check("p2 六个锚点定位 + 高亮", not bad, "; ".join(bad))
        await page.screenshot(path=f"{SHOT}/p2_nav.png")

        # ── p3: speed bars + toggle ──────────────────────────────────
        await page.click('.navlinks a[href="#speed"]')
        await page.wait_for_timeout(1500)
        warm = await page.evaluate(
            "() => [...document.querySelectorAll('#bars .val')].map(v=>v.textContent)")
        widths = await page.evaluate(
            "() => [...document.querySelectorAll('#bars .barfill')]"
            ".map(b => parseFloat(b.style.width) || 0)")
        check("p3 热启动条形已渲染", len(warm) == 6 and all(w > 0 for w in widths),
              f"{warm} widths={widths}")

        await page.click("#seg-cold")
        await page.wait_for_timeout(1400)
        cold = await page.evaluate(
            "() => [...document.querySelectorAll('#bars .val')].map(v=>v.textContent)")
        on = await page.evaluate(
            "() => document.querySelector('.seg button.on').textContent.trim()")
        check("p3 冷/热切换改变数值", cold != warm and "冷启动" in on,
              f"warm={warm} cold={cold} on={on}")
        await page.screenshot(path=f"{SHOT}/p3_speed_cold.png")
        await page.click("#seg-warm")
        await page.wait_for_timeout(800)

        # ── p4: A/B slider ───────────────────────────────────────────
        await page.click('.navlinks a[href="#demo"]')
        await page.wait_for_timeout(1200)
        # The comparison sits below the render gallery, so the section anchor alone
        # leaves it ~1500px off-screen and the synthetic mouse lands on nothing.
        await page.evaluate(
            "() => document.getElementById('ab')"
            ".scrollIntoView({block:'center', behavior:'instant'})")
        await page.wait_for_timeout(3400)   # let the intro sweep finish first
        box = await page.evaluate(
            "() => { const r = document.getElementById('ab').getBoundingClientRect();"
            " return {x:r.x, y:r.y, w:r.width, h:r.height}; }")
        before = await page.evaluate("() => document.getElementById('ab-top').style.width")
        await page.mouse.move(box["x"] + box["w"] * 0.5, box["y"] + box["h"] * 0.5)
        await page.mouse.down()
        await page.mouse.move(box["x"] + box["w"] * 0.22, box["y"] + box["h"] * 0.5, steps=12)
        await page.mouse.up()
        await page.wait_for_timeout(500)
        after = await page.evaluate("() => document.getElementById('ab-top').style.width")
        moved = abs(float(after.rstrip("%")) - float(before.rstrip("%"))) > 10
        check("p4 A/B 手柄可拖动", moved, f"{before} -> {after}")
        await page.screenshot(path=f"{SHOT}/p4_ab_dragged.png")

        # ── p5: lightbox ─────────────────────────────────────────────
        await page.click('.navlinks a[href="#speed"]')
        await page.wait_for_timeout(1200)
        await page.evaluate("() => document.querySelector('#speed .fbox').click()")
        await page.wait_for_timeout(700)
        opened = await page.evaluate(
            "() => document.getElementById('lb').classList.contains('open')")
        src = await page.evaluate("() => document.getElementById('lbimg').getAttribute('src')")
        check("p5 灯箱打开", opened and bool(src), f"open={opened} src={src}")
        await page.screenshot(path=f"{SHOT}/p5_lightbox.png")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
        closed = await page.evaluate(
            "() => !document.getElementById('lb').classList.contains('open')")
        check("p5 Esc 关闭灯箱", closed)

        # ── p6: copy BibTeX ──────────────────────────────────────────
        await page.click('.navlinks a[href="#cite"]')
        await page.wait_for_timeout(1200)
        await page.click("#btn-copy")
        await page.wait_for_timeout(600)
        label = await page.evaluate(
            "() => document.getElementById('btn-copy').textContent.trim()")
        check("p6 复制按钮反馈", "已复制" in label, label)
        await page.screenshot(path=f"{SHOT}/p6_cite.png")

        # ── p7: demo links ───────────────────────────────────────────
        host = await page.evaluate("() => location.hostname")
        hrefs = await page.evaluate(
            "() => ['btn-demo','btn-demo-top','btn-demo-foot'].map(i => "
            "document.getElementById(i).getAttribute('href'))")
        shown = await page.evaluate(
            "() => document.getElementById('demo-url').textContent")
        want = f"http://{host}:8081/"
        check("p7 三个演示按钮指向本机 :8081",
              all(h == want for h in hrefs) and shown == want,
              f"want={want} hrefs={hrefs} shown={shown}")

        # ── p7f: anonymity guards ────────────────────────────────────
        # The paper is under double-blind review. These used to be reachable and each one
        # is a de-anonymisation vector on its own: a host username in a served file, the
        # manuscript repo name (which maps to a self-citation in the references), the full
        # method PDF, and the architecture figure.
        must_404 = ["README.md", "serve.sh", "verify_page.py",
                    "assets/SCANO_paper.pdf", "assets/method.png",
                    "__pycache__/verify_page.cpython-310.pyc"]
        served = []
        for path in must_404:
            rr = await page.request.get(URL.rstrip("/") + "/" + path)
            if rr.status == 200:
                served.append(f"{path}({rr.status})")
        check("p7f 开发文件与论文全文均不可访问", not served, str(served))

        # The identifying strings are supplied at run time, never written down here.
        # Hardcoding them would publish, in a file this very check exists to protect,
        # exactly the two tokens a search would need to de-anonymise the submission.
        # Default pattern catches absolute paths, which are generic; pass the rest via
        #   PAGE_LEAK_PATTERNS='/mnt/[^\s"\x27]{4,}|<repo-name>|<user-name>'
        body = await page.evaluate("() => document.documentElement.outerHTML")
        import re as _re
        pattern = os.environ.get("PAGE_LEAK_PATTERNS", r"/mnt/[^\s\"\x27]{4,}")
        bad = _re.findall(pattern, body)
        check("p7f 页面源码不含用户名/私有仓库名/绝对路径", not bad, str(set(bad)))

        # ── p7b: A/B two frames must occupy the SAME box ─────────────
        # A width/height attribute pair plus a width:100% rule with no height rule left
        # the base frame at its attribute height, stretched sideways and 260px short of
        # its container — black showed under the un-wiped half.
        await page.evaluate("() => document.getElementById('ab').scrollIntoView()")
        await page.wait_for_timeout(3500)
        geo = await page.evaluate(
            """() => {
                const g = e => { const r = e.getBoundingClientRect();
                                 return [Math.round(r.width), Math.round(r.height)]; };
                return {ab: g(document.getElementById('ab')),
                        bot: g(document.getElementById('ab-bottom')),
                        top: g(document.getElementById('ab-top').querySelector('img'))};
            }""")
        fits = (abs(geo["bot"][1] - geo["ab"][1]) <= 4
                and abs(geo["top"][1] - geo["ab"][1]) <= 4
                and abs(geo["bot"][0] - geo["top"][0]) <= 4)
        check("p7b A/B 两帧同尺寸铺满容器", fits, str(geo))

        # ── p7c: the OV render gallery is present and loaded ─────────
        gal = await page.evaluate(
            "() => [...document.querySelectorAll('.gal img')]"
            ".map(i => [i.getAttribute('src').split('/').pop(), i.naturalWidth])")
        okgal = len(gal) >= 6 and all(w > 0 for _, w in gal)
        check("p7c OV 渲染画廊 6 图已加载", okgal, str(gal))

        # ── p7d: the collapsed academic figures open ─────────────────
        await page.click("#more-figs")
        await page.wait_for_timeout(900)
        opened_d = await page.evaluate(
            "() => document.querySelector('#results details').open")
        check("p7d 折叠的审稿向图可展开", opened_d)

        # ── p7e: multi-GPU TTA diagram redraws on rank change ────────
        await page.click('.navlinks a[href="#tta"]')
        await page.wait_for_timeout(1400)
        one = await page.evaluate(
            "() => ({rects: document.querySelectorAll('#tta-svg rect').length,"
            " stats: [...document.querySelectorAll('#tta-stats b')].map(b=>b.textContent)})")
        await page.click("#tta-2")
        await page.wait_for_timeout(900)
        two = await page.evaluate(
            "() => ({rects: document.querySelectorAll('#tta-svg rect').length,"
            " stats: [...document.querySelectorAll('#tta-stats b')].map(b=>b.textContent),"
            " on: document.querySelector('#tta .seg button.on').textContent.trim()})")
        # 1 card -> 2 pool rects + 1 reduce bar; 2 cards -> 4 + 1. Accumulated total must
        # stay 50 in both, which is the whole point of the section.
        redrew = (two["rects"] > one["rects"] and two["stats"] != one["stats"]
                  and one["stats"][2] == "50" and two["stats"][2] == "50")
        check("p7e 多卡示意图随卡数重绘且累积趟数不变", redrew, f"{one} -> {two}")

        # The speed bars must NOT react to the multi-GPU buttons: both sections use .seg.
        bars_after = await page.evaluate(
            "() => [...document.querySelectorAll('#bars .val')].map(v=>v.textContent)")
        check("p7e 加速条未被多卡按钮误触", len(bars_after) == 6, str(bars_after))

        # ── p8: back to top ──────────────────────────────────────────
        await page.evaluate("() => window.scrollTo(0, 3000)")
        await page.wait_for_timeout(700)
        vis = await page.evaluate(
            "() => document.getElementById('totop').classList.contains('show')")
        check("p8 回顶按钮出现", vis)
        await page.click("#totop")
        await page.wait_for_timeout(1500)
        y = await page.evaluate("() => window.scrollY")
        check("p8 回到顶部", y < 40, f"scrollY={y}")

        check("JS 无运行时错误", not console_errors, str(console_errors[:3]))

        await page.screenshot(path=f"{SHOT}/p9_full.png", full_page=True)
        log(f"整页截图 -> {SHOT}/p9_full.png")
        await browser.close()

    log("=" * 46)
    if fails:
        log(f"FAILED {len(fails)} 项:")
        for f in fails:
            log("  - " + f)
        sys.exit(1)
    log("全部通过")


asyncio.run(main())
