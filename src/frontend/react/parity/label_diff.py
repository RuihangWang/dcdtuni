#!/usr/bin/env python3
"""Which CONTROL LABELS exist in one editor and not the other, per left tab.

`id_diff.py` compares DOM ids, which is exact but blind to controls the legacy
page never gave an id — its rotate/mirror buttons, for instance, are addressed
only by their inline `onclick`. This compares the visible TEXT of every
button, label and option instead, which is what a user actually looks for.

Text is normalised (whitespace collapsed, digits dropped) because counts and
measurements differ between two apps showing the same project: "机柜 (RACK)4"
and "机柜 (RACK)12" are the same control.

    python3 label_diff.py react   # with browser-use on the React dev server
    python3 label_diff.py html    # with browser-use on STL_generate.html
    python3 label_diff.py report
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import ev, SP

TABS = ['geometry', 'mesh', 'solver', 'transient', 'postprocess', 'system']

COLLECT = """(function(){
  var out = [];
  // `.collapse-header` and `.prop-title` are in here because the legacy writes
  // some controls as a plain <label> inside a clickable div and this app writes
  // the same control as the div itself — without them a section that exists in
  // both reads as missing from one.
  document.querySelectorAll(
    'button, label, option, .tool-btn, .btn, .file-btn, .collapse-header, .prop-title'
  ).forEach(function(el){
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    var text = (el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (!text) return;
    out.push(text);
  });
  return JSON.stringify([...new Set(out)]);
})()"""


def norm(text):
    """Collapse the parts that legitimately differ between the two apps."""
    text = re.sub(r'\d+(\.\d+)?', '#', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def collect(app):
    out = {}
    for i, tab in enumerate(TABS):
        if app == 'react':
            ev(f"(function(){{var b=document.querySelectorAll('.left-tab-btn');if(b[{i}])b[{i}].click();return 'ok';}})()")
        else:
            ev(f"(function(){{window.switchLeftTab('{tab}');return 'ok';}})()")
        time.sleep(1.2)
        raw = ev(COLLECT)
        try:
            labels = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            labels = []
        out[tab] = sorted({norm(t) for t in labels})
        print(f'{app} {tab}: {len(out[tab])} labels', flush=True)
    json.dump(out, open(os.path.join(SP, f'labels_{app}.json'), 'w'), ensure_ascii=False, indent=1)


def report():
    react = json.load(open(os.path.join(SP, 'labels_react.json')))
    html = json.load(open(os.path.join(SP, 'labels_html.json')))
    # A label present on ANY React tab counts: the two apps put some controls on
    # different tabs, and that is a layout question, not a missing feature.
    everywhere = {t for tab in TABS for t in react.get(tab, [])}
    total = 0
    for tab in TABS:
        missing = [t for t in html.get(tab, []) if t not in everywhere]
        total += len(missing)
        print(f'\n=== {tab} ===  react {len(react.get(tab, []))} / html {len(html.get(tab, []))}')
        for m in missing:
            print(f'    MISSING: {m}')
    print(f'\nlabels in HTML and nowhere in React: {total}')


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'report'
    if mode == 'report':
        report()
    else:
        collect(mode)
