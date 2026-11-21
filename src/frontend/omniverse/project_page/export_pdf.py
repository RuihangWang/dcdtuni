#!/usr/bin/env python3
"""Export the project page to PDF.

A straight page.pdf() of this page loses things, so the DOM is prepared first:

* The two interactive charts (warm/cold cost, 1/2/6-GPU split) can only show one state at
  a time. In a PDF nobody can press the toggle, so each is rendered TWICE — every state
  side by side — rather than silently shipping half the information.
* <details> is expanded: collapsed content prints as a bare summary line otherwise.
* The fixed nav, the floating back-to-top button, the lightbox and the "点击放大" hints are
  interaction-only chrome and are dropped.
* Figures get break-inside: avoid so a plot never straddles a page boundary.

Two outputs, because they serve different purposes:
  <name>.pdf         A4 landscape, paginated — the one to send or print.
  <name>_单页.pdf     one continuous page as tall as the document — the design exactly as
                     it looks in a browser, for screen reading.

The page width matters: below 900 CSS px the responsive rules collapse the grids, so the
continuous variant is rendered at the desktop width and the A4 one gets narrowed
max-widths injected instead of being scaled down.
"""
import asyncio, os, pathlib, shutil, sys

URL = os.environ.get("PAGE_URL", "http://localhost:8090/?rec=1")
OUT_DIR = os.environ.get("PDF_OUT", str(pathlib.Path(__file__).resolve().parent / "export"))
CHROME = os.environ.get("PAGE_CHROME") or shutil.which("chrome") or str(
    pathlib.Path.home() / ".cache/ms-playwright/chromium-1223/chrome-linux64/chrome")
os.makedirs(OUT_DIR, exist_ok=True)


def log(m): print(f"[pdf] {m}", flush=True)


PRINT_CSS = """
nav, #totop, #lb { display: none !important; }
.fbox::after { display: none !important; }          /* “点击放大” 在纸上没有意义 */
.abzoom { display: none !important; }
header { padding-top: 40px !important; }
/* 图与卡片不许跨页断开 */
figure, .fbox, .card, .contrib .c, .techgrid .t, .tta, .cite, .gal figure {
  break-inside: avoid; page-break-inside: avoid;
}
section { break-inside: auto; }
h2, h3 { break-after: avoid; page-break-after: avoid; }
/* 双状态并排 */
.pdf-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; align-items: start; }
.pdf-pair > div { min-width: 0; }
.pdf-label { text-align: center; font-size: 13px; font-weight: 700; margin: 0 0 10px;
             color: var(--nv); letter-spacing: .04em; }
.seg { display: none !important; }                  /* 按钮在 PDF 里是死的 */
"""


async def prepare(page):
    """Expand everything, then duplicate each interactive chart into all of its states."""
    await page.add_style_tag(content=PRINT_CSS)

    await page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")

    # 成本对比：把热启动与冷启动并排。
    # 必须「先切状态 → 等渲染完 → 再克隆」。renderBars 在脚本初始化时就把 #bars 捕成了
    # 闭包变量，不会按 id 重新查询，所以先克隆再想办法让克隆体重绘是行不通的 —— 那样
    # 两栏会是同一组数字，而且不报错。
    cols = []
    for label, mode in (("热启动（特征已就绪）", "warm"), ("冷启动（新边界条件）", "cold")):
        await page.evaluate(
            "(m) => { var b = document.querySelector('#speed .seg button[data-mode=\"'+m+'\"]');"
            " if (b) b.click(); }", mode)
        await page.wait_for_timeout(1500)          # 条宽是 1.1s 过渡，等它走完
        html = await page.evaluate(
            "() => document.getElementById('bars').outerHTML")
        note = await page.evaluate(
            "() => document.getElementById('barnote').textContent")
        cols.append({"label": label, "html": html, "note": note})

    await page.evaluate("""(cols) => {
        var bars = document.getElementById('bars');
        var note = document.getElementById('barnote');
        var pair = document.createElement('div');
        pair.className = 'pdf-pair';
        cols.forEach(function(c){
            var col = document.createElement('div');
            col.innerHTML = '<p class="pdf-label">' + c.label + '</p>' + c.html
                + '<p class="barnote">' + c.note + '</p>';
            var d = col.querySelector('[id]');
            if (d) d.removeAttribute('id');
            pair.appendChild(col);
        });
        bars.parentNode.insertBefore(pair, bars);
        bars.style.display = 'none';
        if (note) note.style.display = 'none';
    }""", cols)

    # 多卡示意：1 / 2 / 6 卡三态纵向排开
    await page.evaluate("""() => {
        var box = document.querySelector('.tta');
        if (!box) return;
        var host = box.parentNode;
        var made = [];
        [1, 2, 6].forEach(function(r){
            var btn = document.querySelector('.seg button[data-r="' + r + '"]');
            if (btn) btn.click();
            var clone = box.cloneNode(true);
            clone.querySelectorAll('[id]').forEach(function(e){ e.removeAttribute('id'); });
            var wrap = document.createElement('div');
            wrap.style.marginTop = '18px';
            wrap.innerHTML = '<p class="pdf-label">' + r + ' 张卡</p>';
            wrap.appendChild(clone);
            made.push(wrap);
        });
        made.forEach(function(w){ host.insertBefore(w, box); });
        box.style.display = 'none';
    }""")

    # A/B 对比滑块：PDF 里拖不动，摆到正中
    await page.evaluate("""() => {
        var h = document.getElementById('ab-handle');
        var t = document.getElementById('ab-top');
        if (t) t.style.width = '50%';
        if (h) h.style.left = '50%';
    }""")

    # 演示地址在页面上是运行时按 location.hostname 拼的，导出时会被固化成导出机的地址。
    # 印 localhost 对读者无用，印内网 IP 又把机构网段写进了一份会外传的文档 —— 双盲评审下
    # 不必要的暴露。改成通用写法，并去掉按钮的 href（PDF 里点了也去不到任何地方）。
    await page.evaluate("""() => {
        var u = document.getElementById('demo-url');
        if (u) u.textContent = 'http://<演示主机>:8081/';
        ['btn-demo', 'btn-demo-top', 'btn-demo-foot'].forEach(function(id){
            var b = document.getElementById(id);
            if (b) b.removeAttribute('href');
        });
    }""")

    await page.wait_for_timeout(1200)


async def main():
    from playwright.async_api import async_playwright
    name = os.environ.get("PDF_NAME", "SCANO_项目主页")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, executable_path=CHROME,
            args=["--disable-gpu", "--use-gl=swiftshader"])

        # ── 连续单页：桌面宽度，页高等于文档高 ─────────────────────────
        page = await (await browser.new_context(
            viewport={"width": 1440, "height": 1000})).new_page()
        await page.goto(URL, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(2500)
        await prepare(page)
        h = await page.evaluate("() => document.documentElement.scrollHeight")
        one = os.path.join(OUT_DIR, f"{name}_单页.pdf")
        await page.pdf(path=one, width="1440px", height=f"{h + 40}px",
                       print_background=True, margin={"top": "0", "bottom": "0",
                                                      "left": "0", "right": "0"})
        log(f"单页  {one}  ({h + 40}px 高)")
        await page.close()

        # ── A4 横向分页 ────────────────────────────────────────────────
        page = await (await browser.new_context(
            viewport={"width": 1440, "height": 1000})).new_page()
        await page.goto(URL, wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(2500)
        await prepare(page)
        # A4 横向可用宽约 1046px，把两个栏宽收进去，避免整页缩放导致正文过小
        await page.add_style_tag(content=
            ":root{--maxw:820px !important; --widew:1030px !important}"
            ".bleed{width:min(1030px,calc(100vw - 40px)) !important}")
        await page.wait_for_timeout(600)
        a4 = os.path.join(OUT_DIR, f"{name}.pdf")
        await page.pdf(path=a4, format="A4", landscape=True, print_background=True,
                       margin={"top": "12mm", "bottom": "12mm",
                               "left": "10mm", "right": "10mm"})
        log(f"A4    {a4}")
        await browser.close()

    for f in (a4, one):
        log(f"  {os.path.basename(f)}  {os.path.getsize(f) / 1048576:.1f} MB")


asyncio.run(main())
