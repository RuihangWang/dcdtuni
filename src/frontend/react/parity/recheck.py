#!/usr/bin/env python3
"""Re-test the 22 NO_EFFECT controls with a snapshot that can actually see them.

The sweep's fingerprint covered project data, input values and control count. It
did NOT cover CSS classes, panel geometry or the WebGL scene — so a toolbar
toggle that only flips an `active` class, a panel collapse that only changes a
width, and a grid toggle that only adds a Three.js object all read as
"no effect". That is a hole in the measurement, not a verdict on the control.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import ev, evj, SP, login_js

RICH = """(function(){
  function box(id){var e=document.getElementById(id); if(!e) return null;
    var r=e.getBoundingClientRect(); return Math.round(r.width)+'x'+Math.round(r.height)+'@'+Math.round(r.x);}
  var cls={};
  document.querySelectorAll('#toolbar button, .left-tab-btn, .file-btn, .btn').forEach(function(e,i){
    cls[(e.id||('n'+i))]= (e.className||'').indexOf('active')>=0 ? 'active':'';});
  var scene = 0, meshes = 0;
  var stats = document.querySelector('.viewport-stats');
  if (stats) { var m=/meshes:\\s*(\\d+)/.exec(stats.textContent); if(m) meshes=+m[1]; }
  var cv = document.querySelector('#center-view canvas');
  return JSON.stringify({
    classes: JSON.stringify(cls),
    left: box('left-panel'), right: box('right-panel'),
    toolbar: box('toolbar'), center: box('center-view'),
    toolbarCollapsed: !!(document.getElementById('toolbar')||{}).classList &&
                      document.getElementById('toolbar').classList.contains('collapsed'),
    toolbarContentVisible: !!(document.getElementById('toolbar-content') &&
                             document.getElementById('toolbar-content').getBoundingClientRect().height>0),
    meshes: meshes,
    canvasSize: cv ? cv.width+'x'+cv.height : 'none',
    gridBadge: (document.querySelector('.tool-inline .badge')||{}).textContent||'',
    leftHidden: !!(document.getElementById('left-panel')||{}).hidden,
    rightHidden: !!(document.getElementById('right-panel')||{}).hidden
  });
})()"""

CASES = [
    ('#toolbar-collapse-btn',        'click', None,  '折叠工具栏'),
    ('#left-collapse-btn',           'click', None,  '折叠左面板'),
    ('#right-collapse-btn',          'click', None,  '折叠右面板'),
    ("__label:旋转吸附",              'click', None,  '旋转吸附'),
    ("__label:🧲 面吸附",             'click', None,  '面吸附'),
    ("__label:📏 边对齐",             'click', None,  '边对齐'),
    ("__label:📐 网格吸附",            'click', None,  '网格吸附'),
    ("__label:显示 XOZ 网格",          'click', None,  '显示 XOZ 网格'),
    ("__label:💡 灯光",               'click', None,  '灯光'),
    ('#btn-mode-air',                'click', None,  '风冷模拟(已激活)'),
]

FIND_BY_LABEL = """(function(txt){
  var els = document.querySelectorAll('#toolbar button, .file-btn, .btn, .left-tab-btn');
  for (var i=0;i<els.length;i++){ if ((els[i].textContent||'').trim() === txt) return window.__V.pathOf(els[i]); }
  return null;
})(%s)"""

def snap():
    v = ev(RICH)
    return json.loads(v) if isinstance(v, str) else v

def main():
    ev(open(os.path.join(SP, 'lib.js')).read())
    ev("(function(){" + login_js(('admin', 'WIN#@desk')) +
       "window.prompt=function(){return '/mnt/lv/home/litianyu/mycode/dcdtuni/case/Base_leakage/Base_leakage.cfdproj';};"
       "document.querySelectorAll('.file-btn')[0].click(); return 'ok';})()")
    time.sleep(4)

    out = []
    for path, mode, arg, name in CASES:
        if path.startswith('__label:'):
            p = ev(FIND_BY_LABEL % json.dumps(path.split(':', 1)[1]))
            if not p or p == 'null':
                out.append((name, 'NOT FOUND', '')); print(f'{name:16s} NOT FOUND'); continue
            path = p
        before = snap()
        ev(f"window.__V.act({json.dumps(path)}, {json.dumps(mode)}, {json.dumps(arg)})")
        time.sleep(0.8)
        after = snap()
        diffs = [k for k in before if before.get(k) != after.get(k)]
        detail = '; '.join(f'{k}: {before.get(k)} -> {after.get(k)}'
                           for k in diffs if k != 'classes')[:150]
        if 'classes' in diffs:
            b = json.loads(before['classes']); a = json.loads(after['classes'])
            flipped = [k for k in b if b.get(k) != a.get(k)]
            detail = (f'active class flipped on {flipped}; ' + detail)[:200]
        verdict = 'EFFECT' if diffs else 'STILL NO EFFECT'
        out.append((name, verdict, detail))
        print(f'{name:16s} {verdict:16s} {detail}', flush=True)
        # restore
        ev(f"window.__V.act({json.dumps(path)}, {json.dumps(mode)}, {json.dumps(arg)})")
        time.sleep(0.6)

    json.dump(out, open(os.path.join(SP, 'recheck.json'), 'w'), ensure_ascii=False, indent=1)

main()
