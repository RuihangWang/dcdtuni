#!/usr/bin/env python3
"""Re-verify every id-bearing TEXT control against the legacy editor.

Why this exists
---------------
`act('text')` used `el.focus()` / `el.blur()`. Headless Chromium reports
`document.hasFocus() === false`, and in that state those methods dispatch NO
events at all — so React's commit-on-blur never fired. The harness also sent
only `input`, never `change`, so the legacy editor's inline `onchange=""` never
fired either. Both apps read back unchanged, and the sweep scored that as
agreement. Every text control's verdict from before that fix is vacuous, so
this re-runs them for real.

What it does
------------
Loads one project into both apps, then for each id-bearing text/number input:
perturbs it, reads `runDefaults` back, and restores. The two apps are compared
on what they WROTE, not on what the input shows — a field that displays the
right number while writing to the wrong key is exactly the failure this is for.

Usage
-----
    browser-use open http://localhost:$REACT_PORT/                   # the React app
    python3 text_recheck.py react
    browser-use open http://localhost:$HTML_PORT/STL_generate.html   # the legacy app
    python3 text_recheck.py html
    python3 text_recheck.py compare

Both servers come from ./src/start_server.sh; the page logs in with the
DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD pair, else the launcher's local-mode
file src/server/auth-local/admin_credentials (src/frontend/scripts/admin_login.py).
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# parity/ -> react/ -> frontend/ -> src/ -> repo root: FOUR levels, not three.
# Three lands on src/, and `src/case/Base_leakage` does not exist — the read
# returned an error body, JSON.parse threw, and browser-use reported a bare
# `None` that read like "the app is broken" rather than "the path is wrong".
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
BU = os.path.expanduser('~/.local/bin/browser-use')
sys.path.insert(0, os.path.join(HERE, '..', '..', 'scripts'))
from admin_login import login_js  # noqa: E402
CASE = os.path.join(REPO, 'case', 'Base_leakage')
PROJ = os.path.join(CASE, 'Base_leakage.cfdproj')

# Tab order is identical in both apps; index rather than label so a
# relabelling ("求解" -> "液冷求解" in liquid mode) cannot silently skip a tab.
TABS = ['geometry', 'mesh', 'solver', 'transient', 'postprocess', 'system']


def ev(js: str) -> str:
    r = subprocess.run([BU, 'eval', js], capture_output=True, text=True, timeout=180)
    out = (r.stdout or '') + (r.stderr or '')
    for line in out.splitlines():
        if line.startswith('result: '):
            return line[8:]
    return 'ERR:' + out.strip()[-200:]


def evj(js: str):
    raw = ev(js)
    try:
        return json.loads(raw)
    except Exception:
        return {'PARSE_FAIL': raw[:300]}


LIB = r"""
(function(){
  // headless: document.hasFocus() === false, so focus()/blur() dispatch NOTHING.
  // Every focus event has to be synthesised. All four are needed: React maps
  // onFocus/onBlur to focusin/focusout, the legacy editor's inline onchange=""
  // needs `change`.
  window.__T = {
    fields: function(){
      var out = [];
      document.querySelectorAll('input[type=number], input[type=text]').forEach(function(el){
        if (!el.id || el.disabled) return;
        if (!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) return;
        out.push({id: el.id, value: el.value, type: el.type});
      });
      return out;
    },
    set: function(id, v){
      var el = document.getElementById(id);
      if (!el) return 'MISSING';
      el.dispatchEvent(new FocusEvent('focusin', {bubbles:true}));
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value')
            .set.call(el, String(v));
      el.dispatchEvent(new Event('input', {bubbles:true}));
      el.dispatchEvent(new Event('change', {bubbles:true}));
      el.dispatchEvent(new FocusEvent('focusout', {bubbles:true}));
      return 'ok';
    },
    // runDefaults only: it is what the .cfdrun is built from, so a write that
    // lands anywhere else in the project is a write the solver never sees.
    runDefaults: function(st){
      return JSON.parse(JSON.stringify((st && st.runDefaults) || {}));
    }
  };
})(); 'installed'
"""


def state_expr(app: str) -> str:
    return 'window.__dcdtuni.getProject()' if app == 'react' else 'window.state'


def load_project(app: str) -> str:
    """Load the project WITHOUT going through the file dialog.

    The dialog is a control the sweep tests elsewhere; using it here would make
    a dialog regression look like a text-control regression. (The old harness
    overrode `window.prompt`, which stopped working the moment React grew a real
    file browser — and failed silently, inventorying a project-less app.)
    """
    # The DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD pair, else the launcher's
    # local-mode file, else the pair written here (src/frontend/scripts/admin_login.py).
    login = login_js(('admin', 'WIN#@desk'))
    read = (
        f"var c=new XMLHttpRequest();c.open('POST','/api/set-case-dir',false);"
        f"c.setRequestHeader('Content-Type','application/json');"
        f"c.send(JSON.stringify({{caseDir:{json.dumps(CASE)}}}));"
        f"var r=new XMLHttpRequest();"
        f"r.open('GET','/api/read-file?path='+encodeURIComponent({json.dumps(PROJ)}),false);r.send();"
        f"var d=JSON.parse(JSON.parse(r.responseText).content);"
    )
    apply_ = (
        f"window.__dcdtuni.loadProject(d, {json.dumps(PROJ)});"
        if app == 'react'
        else "window._applyProjectData(d);"
    )
    return ev(f"(function(){{{login}{read}{apply_}return 'loaded';}})()")


def perturb(value: str) -> str:
    """A value that differs from the current one, is legal, and is not a default.

    Scaling rather than replacing keeps it inside whatever min/max the field
    carries: doubling a level-2 refinement stays a valid level, while a fixed
    99 would be clamped and then read back as "no change".
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 'parity-probe'
    if v == 0:
        return '1'
    scaled = v * 1.5
    return str(int(scaled)) if float(value).is_integer() and scaled.is_integer() else f'{scaled:.6g}'


def await_ready(app: str, timeout: float = 30.0) -> bool:
    """Wait for the app to be scriptable before touching it.

    `browser-use open` returns before React has mounted, and a load fired into
    that gap throws on an undefined `window.__dcdtuni` — which browser-use
    reports as a bare `None`, and the sweep then walks a project-less app and
    finds zero controls on every tab. That reads exactly like "the app has no
    controls" rather than "the harness was early".
    """
    probe = (
        'typeof window.__dcdtuni'
        if app == 'react'
        else 'typeof window._applyProjectData'
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ev(probe) == 'function' or ev(probe) == 'object':
            return True
        time.sleep(1.0)
    return False


def run(app: str) -> None:
    if not await_ready(app):
        raise SystemExit(f'{app}: the app never became scriptable — is the server up?')
    ev(LIB)
    loaded = load_project(app)
    if loaded != 'loaded':
        raise SystemExit(f'{app}: project load failed ({loaded!r}) — refusing to sweep an empty app')
    time.sleep(2)

    # Both apps gate the mesh and solver tabs on the case being validated, and
    # a gated tab yields zero fields. Sweeping one app validated and the other
    # not compares a full panel against an empty one and reports every control
    # as "missing from the other side".
    ev('window.__dcdtuni.refreshServerInfo && window.__dcdtuni.refreshServerInfo(); 1')
    time.sleep(3)
    ev(LIB)

    results = []
    for i, tab in enumerate(TABS):
        r = ev(
            f"(function(){{var b=document.querySelectorAll('.left-tab-btn')[{i}];"
            f"if(!b) return 'NO_TAB'; if(b.disabled) return 'DISABLED'; b.click(); return 'ok';}})()"
        )
        time.sleep(1.0)
        ev(LIB)
        if r != 'ok':
            # A gated tab is not a comparison, it is a missing one. Say so
            # loudly: a DISABLED that goes unnoticed turns into "React has 26
            # controls HTML does not", which is the opposite of the truth.
            print(f'{tab:12s} {r}  <-- NOT SWEPT')
            results.append({'tab': tab, 'id': None, 'gated': r})
            continue
        fields = evj('JSON.stringify(window.__T.fields())')
        if not isinstance(fields, list):
            print(f'{tab:12s} FIELDS_FAIL {fields}')
            continue
        print(f'{tab:12s} {len(fields)} text/number fields')
        for f in fields:
            before = evj(f'JSON.stringify(window.__T.runDefaults({state_expr(app)}))')
            new = perturb(f['value'])
            ev(f'window.__T.set({json.dumps(f["id"])}, {json.dumps(new)})')
            after = evj(f'JSON.stringify(window.__T.runDefaults({state_expr(app)}))')
            changed = diff_keys(before, after)
            results.append(
                {'tab': tab, 'id': f['id'], 'from': f['value'], 'to': new, 'changed': changed}
            )
            # Restore, so the next field starts from the same project.
            ev(f'window.__T.set({json.dumps(f["id"])}, {json.dumps(f["value"])})')

    out = os.path.join(HERE, f'text_recheck_{app}.json')
    with open(out, 'w') as fh:
        json.dump(results, fh, indent=1, sort_keys=True)
    silent = [r for r in results if r.get('id') and not r['changed']]
    driven = [r for r in results if r.get('id')]
    print(f'\n{len(driven)} fields driven, {len(silent)} wrote nothing to runDefaults')
    if silent:
        # Not automatically a defect. A STAGING input — the transient event
        # editor's trigger time and target value — writes nothing until the
        # user presses "add event", and then it writes to a model's
        # timeVarying, never to runDefaults. Silence is only a defect when the
        # OTHER app's same control did write; `compare` is what decides that.
        print('  (silent, check against the other app rather than assuming a bug):')
        for r in silent:
            print(f"    {r['tab']}/{r['id']}")
    print('written', out)


def flatten(d, prefix=''):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f'{prefix}.{k}' if prefix else k))
    elif isinstance(d, list):
        out[prefix] = json.dumps(d)
    else:
        out[prefix] = d
    return out


def diff_keys(before, after):
    fb, fa = flatten(before), flatten(after)
    return sorted(
        f'{k}={fa.get(k)}' for k in set(fb) | set(fa) if fb.get(k) != fa.get(k)
    )


def compare() -> None:
    ra = json.load(open(os.path.join(HERE, 'text_recheck_react.json')))
    rb = json.load(open(os.path.join(HERE, 'text_recheck_html.json')))
    gated_a = [r['tab'] for r in ra if r.get('id') is None]
    gated_b = [r['tab'] for r in rb if r.get('id') is None]
    if gated_a or gated_b:
        print(f'GATED TABS — not compared: react={gated_a} html={gated_b}')
        print('  Nothing below covers them. Validate the case and re-run.\n')
    a = {(r['tab'], r['id']): r for r in ra if r.get('id')}
    b = {(r['tab'], r['id']): r for r in rb if r.get('id')}
    only_react = sorted(set(a) - set(b))
    only_html = sorted(set(b) - set(a))
    shared = sorted(set(a) & set(b))

    agree, disagree, both_silent = [], [], []
    for key in shared:
        ra, rb = a[key], b[key]
        if not ra['changed'] and not rb['changed']:
            both_silent.append(key)
        elif ra['changed'] == rb['changed']:
            agree.append(key)
        else:
            disagree.append((key, ra['changed'], rb['changed']))

    print(f'shared fields   : {len(shared)}')
    print(f'  agree         : {len(agree)}')
    print(f'  DISAGREE      : {len(disagree)}')
    print(f'  both silent   : {len(both_silent)}  (neither writes runDefaults — not evidence of parity)')
    print(f'only in React   : {len(only_react)}')
    print(f'only in HTML    : {len(only_html)}')
    for key, ca, cb in disagree:
        print(f'\n  {key[0]}/{key[1]}\n    react: {ca}\n    html : {cb}')
    if only_html:
        print('\nmissing from React:')
        for t, i in only_html:
            print(f'  {t}/{i}')
    if only_react:
        print('\nnot in HTML (React-only):')
        for t, i in only_react:
            print(f'  {t}/{i}')


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'compare'
    if mode == 'compare':
        compare()
    else:
        run(mode)
