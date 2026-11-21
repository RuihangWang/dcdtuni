#!/usr/bin/env python3
"""Exercise the 8 controls the sweep never reached — the two 地板 (FLOOR) tree rows.

They were missed because the sweep resolves paths from a table captured at the
start of the tab, and an earlier reload (triggered when a control could not be
restored) rebuilt the tree with a different collapsed state. Re-resolving them
live is the fix.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import ev, evj, SP, login_js

ev(open(os.path.join(SP, 'lib.js')).read())
ev("(function(){" + login_js(('admin', 'WIN#@desk')) +
   "window.prompt=function(){return '/mnt/lv/home/litianyu/mycode/dcdtuni/case/Base_leakage/Base_leakage.cfdproj';};"
   "document.querySelectorAll('.file-btn')[0].click(); return 'ok';})()")
time.sleep(4)
ev("(function(){document.querySelectorAll('.left-tab-btn')[0].click(); return 1;})()")
time.sleep(1)

def snap():
    v = ev("JSON.stringify(window.__V.snap())")
    return json.loads(v) if isinstance(v, str) else v

# find the two floor rows by NAME, not by structural path
FIND = """(function(name, what){
  var rows = document.querySelectorAll('#tree-container .node-label');
  for (var i=0;i<rows.length;i++){
    if ((rows[i].textContent||'').indexOf(name) >= 0) {
      if (what === 'row') return window.__V.pathOf(rows[i]);
      if (what === 'cb')  { var c = rows[i].querySelector('input.node-visibility-toggle'); return c ? window.__V.pathOf(c) : null; }
      var bs = rows[i].querySelectorAll('.node-action-btn');
      return bs.length ? window.__V.pathOf(bs[what === 'dup' ? 0 : 1]) : null;
    }
  }
  return null;
})(%s, %s)"""

out = []
for name in ['Raised_Floor_copy', 'Room_Floor']:
    for what, mode in [('row', 'click'), ('cb', 'check'), ('dup', 'click'), ('del', 'click')]:
        p = ev(FIND % (json.dumps(name), json.dumps(what)))
        if not p or p == 'null':
            print(f'{name:20s} {what:4s} NOT FOUND'); out.append((name, what, 'NOT FOUND', '')); continue
        before = snap()
        r = ev(f"window.__V.act({json.dumps(p)}, {json.dumps(mode)}, null)")
        time.sleep(0.6)
        after = snap()
        tags = []
        if after.get('undo', -1) > before.get('undo', -1): tags.append('PROJECT')
        if after.get('inputsHash') != before.get('inputsHash'): tags.append('INPUTS')
        if after.get('nControls') != before.get('nControls'): tags.append('DOM')
        if after.get('logTop') != before.get('logTop'): tags.append('LOG')
        outcome = '+'.join(tags) or 'NO_EFFECT'
        print(f'{name:20s} {what:4s} {mode:6s} -> {outcome:26s} log={after.get("logTop","")[:44]}')
        out.append((name, what, outcome, after.get('logTop', '')[:60]))
        for _ in range(max(0, after.get('undo', 0) - before.get('undo', 0))):
            ev('window.__dcdtuni.undo(); 1')
        time.sleep(0.4)

json.dump(out, open(os.path.join(SP, 'finish8.json'), 'w'), ensure_ascii=False, indent=1)
