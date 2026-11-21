#!/usr/bin/env python3
"""Phase B v2 — click every control, record what changed, and RESTORE THE BASELINE.

v1 restored project data but not UI state, so the first collapse header hid the
six file buttons behind it and every one of them reported "missing". Restoring
has to cover what the action actually changed:

  project data  -> undo N times
  checkbox/radio-> click again
  select/text   -> write the original value back
  toggle click  -> click again (collapse headers, tree expanders, tabs)

and the live element table is re-read whenever the DOM changed, because a
structural path only addresses the tree it was computed from.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import ev, evj, SP, login_js

LIB = open(os.path.join(SP, 'lib.js')).read()
TABS = ['geometry', 'mesh', 'solver', 'transient', 'postprocess', 'system']
# Exercised separately, never in the sweep — each is minutes long or acts
# outside the browser: a mesh/solve/transient run, a `git pull` on the working
# tree, a server re-exec that kills any job in flight, or a logout that would
# end the session the sweep is running in.
SLOW = {'#mesh-run-btn', '#solve-run-btn', '#transient-solve-btn',
        '#system-upgrade-btn', '#system-restart-btn', '#system-logout-btn'}

# Controls whose work is a server round trip or a GPU rebuild. The default 0.5s
# observation window reported every one of them as NO_EFFECT — checkMesh takes
# ~15s, a streamline set ~25s, a 128^3 volume resample over a minute — which
# reads as "the button is dead" and is the single biggest source of false
# alarms this sweep produces.
ASYNC_WAIT = {
    '#btn-init-postprocess': 14, '#check-mesh-btn': 18, '#pp-add-streamline': 28,
    '#pp-apply-slice': 16, '#pp-apply-volume': 90, '#pp-field-select': 8,
    '#pp-timestep': 8, '#pp-log-run': 6, '#system-refresh-btn': 4,
    '#btn-auto-detect-refinement': 3, '#btn-screenshot': 4, '#pp-ai-start': 5,
    '#pp-ai-demo-weights': 3, '#pp-clear-all': 3, '#pp-apply-clim': 4,
}

def install():
    ev(LIB)

def reload_app():
    ev("try{localStorage.setItem('stlgen-lang','zh-CN')}catch(e){}; location.reload(); 1")
    time.sleep(6)
    install()

PROJECT = '/mnt/lv/home/litianyu/mycode/dcdtuni/case/Base_leakage/Base_leakage.cfdproj'


def load_project():
    """Load the baseline project WITHOUT going through the load dialog.

    The dialog is itself one of the controls this sweep exercises, and it is now
    a file browser rather than a `prompt()`, so driving it here would make the
    baseline depend on the thing being tested. The store's own loader is the
    same entry point the dialog calls.
    """
    ev("(function(){" + login_js(('admin', 'WIN#@desk')) +
       "var r=new XMLHttpRequest();r.open('GET','/api/read-file?path='"
       f"+encodeURIComponent({json.dumps(PROJECT)}),false);r.send();"
       "var d=JSON.parse(JSON.parse(r.responseText).content);"
       "if(window.__dcdtuni)window.__dcdtuni.loadProject(d," + json.dumps(PROJECT) + ");"
       "else window._applyProjectData(d);"
       "return 'ok';})()")
    time.sleep(3.5)

def goto(i):
    ev(f"(function(){{var b=document.querySelectorAll('.left-tab-btn')[{i}];"
       f"if(b.disabled) return 'DISABLED'; b.click(); return 'ok';}})()")
    time.sleep(0.9)

def plan(c):
    t, tag = c.get('type', ''), c.get('tag')
    if tag == 'select':
        opts = c.get('options') or []
        other = [o for o in opts if o != c.get('value')]
        return ('select', other[0]) if other else ('select', c.get('value'))
    if t in ('checkbox', 'radio'):
        return ('check', None)
    if t == 'range':
        try: cur = float(c.get('value') or 0)
        except ValueError: cur = 0
        return ('range', str(int(cur) + 10 if cur < 900 else int(cur) - 10))
    if t == 'number':
        try: cur = float(c.get('value') or 0)
        except ValueError: cur = 0
        return ('text', str(round(cur + 1, 4)))
    if tag == 'input':
        raw = c.get('value') or ''
        # A text input holding a NUMBER gets a numeric edit, not an appended
        # letter. The residual thresholds are type=text in BOTH editors so a
        # user can type "1e-4" — a number input rejects that mid-keystroke — and
        # appending 'X' made every one of them report NO_EFFECT while the field
        # was correctly refusing garbage. The sweep was measuring its own poke.
        try:
            cur = float(raw)
        except ValueError:
            return ('text', raw + 'X')
        return ('text', repr(cur * 10 if cur else 1))
    return ('click', None)

def classify(before, after, result):
    if not isinstance(before, dict) or not isinstance(after, dict):
        return 'PROBE_FAILED'
    if isinstance(result, str) and result in ('NO_ELEMENT', 'DISABLED', 'UNKNOWN_MODE'):
        return result
    # A probe can come back None: the page is mid-reload, or the eval channel
    # dropped one call. Treat it as a result to REPORT, not as a crash — the
    # sweep exists to reach the end and say what it saw, and dying at control 37
    # of 197 reports nothing about the other 160.
    if before is None or after is None:
        return 'PROBE_FAILED'
    if after.get('err') and after.get('err') != before.get('err'):
        return 'ERROR:' + after['err'][:50]
    tags = []
    if after.get('activeTab') != before.get('activeTab'): tags.append('TAB')
    if after.get('undo', -1) > before.get('undo', -1): tags.append('PROJECT')
    elif after.get('projectHash') != before.get('projectHash'): tags.append('PROJECT*')
    if after.get('inputsHash') != before.get('inputsHash'): tags.append('INPUTS')
    if after.get('nControls') != before.get('nControls'): tags.append('DOM')
    if after.get('logTop') != before.get('logTop'): tags.append('LOG')
    # Viewport-only effects are still effects. A grid preview, a camera move, a
    # streamline set: none of them touch the DOM or the project, and without
    # this the sweep files them next to genuinely dead controls.
    if after.get('scene') != before.get('scene'): tags.append('VIEWPORT')
    if after.get('toggles') != before.get('toggles') or after.get('btnLabels') != before.get('btnLabels'):
        tags.append('TOGGLE')
    if after.get('wrapper') != before.get('wrapper'): tags.append('SCENE_XFORM')
    if after.get('fps') != before.get('fps'): tags.append('FPS')
    if after.get('statusText') != before.get('statusText'): tags.append('STATUS')
    if after.get('bodyClass') != before.get('bodyClass'): tags.append('BODY')
    if after.get('selected') != before.get('selected'): tags.append('SELECTION')
    if after.get('modals') != before.get('modals'): tags.append('MODAL')
    if not after.get('canvasPresent'): tags.append('CANVAS_GONE')
    return '+'.join(tags) if tags else 'NO_EFFECT'

def restore(path, c, mode, before, after, tab_idx):
    """Put the app back the way it was, so the next path still addresses the same tree."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        # An empty snapshot, not False: the caller reads `.get()` off this, and
        # a bool there turns a probe hiccup into a crash 26 controls into a tab.
        return {}
    depth = after.get('undo', 0) - before.get('undo', 0)
    for _ in range(max(0, depth)):
        ev('window.__dcdtuni.undo(); 1')
    if after.get('activeTab') != before.get('activeTab'):
        goto(tab_idx)
    elif mode in ('check',):
        ev(f"window.__V.act({json.dumps(path)}, \"check\", null)")
    elif mode in ('select', 'text', 'range') and depth == 0:
        ev(f"window.__V.act({json.dumps(path)}, {json.dumps(mode)}, {json.dumps(c.get('value'))})")
    elif mode == 'click' and after.get('nControls') != before.get('nControls'):
        # collapse headers, tree expanders: idempotent in pairs
        ev(f"window.__V.act({json.dumps(path)}, \"click\", null)")
    time.sleep(0.4)
    return evj('window.__V.snap()')

def prepare_postprocess():
    """Load a run that HAS results, so the post-processing controls have data.

    The newest run in this case is an aborted solve with no time-step
    directories, and the panel defaults to it — so `开启后处理` correctly refused
    and all seventeen controls behind it had nothing to act on. That reported as
    seventeen dead controls, when the truth was one empty run.
    """
    ev("(function(){var t=document.querySelectorAll('.left-tab-btn');t[4].click();return 'pp'})()")
    time.sleep(2.5)
    picked = ev("(function(){var s=document.getElementById('pp-log-run');if(!s)return 'no run select';"
                "var runs=(window.__ppRuns||[]);var opts=[].slice.call(s.options);"
                "var good=opts.filter(function(o){return /11-22-50|07-40-46/.test(o.value)})[0]||opts[opts.length-1];"
                "var set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;"
                "set.call(s,good.value);s.dispatchEvent(new Event('change',{bubbles:true}));"
                "return good.value.slice(-30);})()")
    time.sleep(2)
    ev("(function(){var b=document.getElementById('btn-init-postprocess');if(b)b.click();return 'init'})()")
    time.sleep(14)
    print(f'  [prep] postprocess run={picked} -> '
          + str(ev("(function(){var e=document.getElementById('postprocess-init-status');"
                   "return e?e.textContent.trim().slice(0,60):''})()")), flush=True)


def main():
    reload_app()
    load_project()
    results, t0 = [], time.time()

    for ti, tab in enumerate(TABS):
        if tab == 'postprocess':
            prepare_postprocess()
        goto(ti); install()
        live = {c['path']: c for c in evj('window.__V.all()')}
        # exercise a control only on the FIRST tab it shows up on
        union = json.load(open(os.path.join(SP, 'react_union.json')))
        todo = [p for p in live if p in union and union[p]['tabs'][0] == tab]
        print(f'\n=== {tab} : {len(todo)} controls ===', flush=True)

        for i, p in enumerate(todo):
            if p in SLOW:
                results.append({'tab': tab, **live.get(p, {'path': p}), 'action': 'skipped',
                                'outcome': 'SEPARATE'})
                print(f'  [{i+1}/{len(todo)}] {p[:46]:48s} {"":24s} SEPARATE', flush=True)
                continue
            # re-resolve: an earlier action may have rebuilt this subtree
            c = evj(f"(function(){{var e=window.__V.find({json.dumps(p)}); if(!e) return null;"
                    f"return window.__V.all().filter(function(x){{return x.path==={json.dumps(p)};}})[0];}})()")
            if not c:
                results.append({'tab': tab, 'path': p, **union[p], 'action': '-', 'outcome': 'VANISHED'})
                print(f'  [{i+1}/{len(todo)}] {p[:46]:48s} {union[p]["label"][:22]:24s} -> VANISHED', flush=True)
                continue
            mode, arg = plan(c)
            # A snapshot must be a dict. `evj` can hand back False or a marker
            # string when an eval hiccups, and a bool here crashed a whole tab's
            # sweep 26 controls in — the run before this one died exactly there.
            def snap():
                v = evj('window.__V.snap()')
                return v if isinstance(v, dict) else {}
            before = snap()
            r = ev(f"window.__V.act({json.dumps(p)}, {json.dumps(mode)}, {json.dumps(arg)})")
            time.sleep(ASYNC_WAIT.get(p, 0.5))
            after = snap()
            outcome = classify(before, after, r)
            back = restore(p, c, mode, before, after, ti) or {}
            before = before or {}
            after = after or {}
            restored = (back.get('projectHash') == before.get('projectHash')
                        and back.get('nControls') == before.get('nControls')
                        and back.get('activeTab') == before.get('activeTab'))
            results.append({'tab': tab, **c, 'action': f'{mode}({arg})' if arg is not None else mode,
                            'result': r, 'outcome': outcome, 'restored': restored,
                            'undoBefore': before.get('undo'), 'undoAfter': after.get('undo')})
            print(f'  [{i+1}/{len(todo)}] {p[:46]:48s} {c["label"][:22]:24s} {mode:7s} -> {outcome}'
                  + ('' if restored else '  [NOT RESTORED]'), flush=True)
            if not restored:
                reload_app(); load_project(); goto(ti); install()
            json.dump(results, open(os.path.join(SP, 'exercise_results.json'), 'w'),
                      ensure_ascii=False, indent=1)

    print(f'\ndone in {time.time()-t0:.0f}s, {len(results)} controls', flush=True)

main()
