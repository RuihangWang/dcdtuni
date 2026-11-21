#!/usr/bin/env python3
"""Which DOM ids exist in one editor and not the other, per left tab.

An id is the only stable name a control has across the two apps: labels are
translated and class names are styling. This does NOT prove behaviour — a
present id can still do nothing — but a MISSING id is a control the React app
does not have at all, which is the gap list this sweep exists to produce.

Run with the browser pointed at each app in turn:

    python3 id_diff.py react   # with browser-use on the React dev server
    python3 id_diff.py html    # with browser-use on STL_generate.html
    python3 id_diff.py report
"""
import json, os, subprocess, sys, time

SP = os.environ.get('DCDTUNI_SCRATCH', '/tmp')
TABS = ['geometry', 'mesh', 'solver', 'transient', 'postprocess', 'system']


def ev(js):
    r = subprocess.run(['browser-use', 'eval', js], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith('result:'):
            return line[len('result:'):].strip()
    return ''


def make_transient(app):
    """Put the solver in transient mode so the 瞬态 tab is reachable.

    BOTH apps hide #tab-btn-transient while the solver is steady (it is the
    legacy's own rule), so sweeping that tab without this lands on whatever tab
    was already open and reports the transient panel's ids as missing — a
    rendering fact dressed up as a gap.
    """
    if app == 'react':
        ev("(function(){window.__dcdtuni.setRunConfig('simulationConfig','solverType','transient');"
           "return 'transient';})()")
    else:
        ev("(function(){window.__dcdtuni.setSolverType&&window.__dcdtuni.setSolverType('transient');return 'transient';})()")
    time.sleep(1.0)


def collect(app):
    out = {}
    for i, tab in enumerate(TABS):
        if tab == 'transient':
            make_transient(app)
        if app == 'react':
            ev(f"(function(){{var b=document.querySelectorAll('.left-tab-btn');if(b[{i}])b[{i}].click();return 'ok';}})()")
        else:
            # Call the switcher directly. Clicking by [onclick*=switchLeftTab]
            # matched hidden duplicate buttons once a project was loaded, so
            # every tab reported the geometry id set.
            ev(f"(function(){{window.switchLeftTab('{tab}');return 'ok';}})()")
        time.sleep(1.2)
        # Visible AND present-but-hidden, reported separately. React UNMOUNTS a
        # panel it is not showing while the legacy page hides it, so a hidden id
        # is a control that exists — counting it as missing would report a gap
        # that is a rendering strategy.
        ids = ev("(function(){var vis=[],hid=[];document.querySelectorAll('[id]').forEach(function(e){"
                 "var r=e.getBoundingClientRect();(r.width>0&&r.height>0?vis:hid).push(e.id)});"
                 "return JSON.stringify({visible:[...new Set(vis)],hidden:[...new Set(hid)]});})()")
        try:
            parsed = json.loads(ids)
            out[tab] = sorted(parsed['visible'])
            out[tab + ':hidden'] = sorted(parsed['hidden'])
        except Exception:
            out[tab] = []
            out[tab + ':hidden'] = []
        print(f'{app} {tab}: {len(out[tab])} visible, {len(out[tab + ":hidden"])} hidden',
              flush=True)
    json.dump(out, open(os.path.join(SP, f'ids_{app}.json'), 'w'), indent=1)


def report():
    react = json.load(open(os.path.join(SP, 'ids_react.json')))
    html = json.load(open(os.path.join(SP, 'ids_html.json')))
    total_missing = 0
    for tab in TABS:
        r, h = set(react.get(tab, [])), set(html.get(tab, []))
        r_hidden = set(react.get(tab + ':hidden', []))
        missing = sorted(h - r - r_hidden)
        hidden_here = sorted((h - r) & r_hidden)
        extra = sorted(r - h)
        total_missing += len(missing)
        print(f'\n=== {tab} ===  react {len(r)} / html {len(h)}')
        if missing:
            print(f'  MISSING in React ({len(missing)}):')
            for m in missing:
                print(f'    {m}')
        if hidden_here:
            print(f'  present but hidden in React ({len(hidden_here)}): '
                  + ', '.join(hidden_here[:8]) + (' …' if len(hidden_here) > 8 else ''))
        if extra:
            print(f'  react-only ({len(extra)}): {", ".join(extra[:12])}'
                  + (' …' if len(extra) > 12 else ''))
    print(f'\ntotal ids present in HTML and missing from React: {total_missing}')


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'report'
    if mode == 'report':
        report()
    else:
        collect(mode)
