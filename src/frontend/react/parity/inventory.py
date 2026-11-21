#!/usr/bin/env python3
"""Phase A — inventory every control on every tab of the React editor."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import ev, evj, SP, login_js

LIB = open(os.path.join(SP, 'lib.js')).read()

def install():
    ev(LIB)

TABS = ['geometry', 'mesh', 'solver', 'transient', 'postprocess', 'system']

install()

# Load a project first: without one the geometry tree is empty and the inventory
# misses ~100 controls, which then silently narrows the sweep.
ev("(function(){" + login_js(('admin', 'WIN#@desk')) +
   "window.prompt=function(){return '/mnt/lv/home/litianyu/mycode/dcdtuni/case/Base_leakage/Base_leakage.cfdproj';};"
   "document.querySelectorAll('.file-btn')[0].click(); return 'ok';})()")
time.sleep(4)
install()

inv = {}
for i, tab in enumerate(TABS):
    r = ev(f"(function(){{var b=document.querySelectorAll('.left-tab-btn')[{i}]; "
           f"if(b.disabled) return 'DISABLED'; b.click(); return 'ok';}})()")
    time.sleep(1.2)
    if r == 'DISABLED':
        inv[tab] = {'gated': True, 'controls': []}
        print(f'{tab:12s} GATED')
        continue
    install()  # a tab switch can replace the subtree; helpers live on window so this is cheap
    ctrls = evj('window.__V.all()')
    inv[tab] = {'gated': False, 'controls': ctrls}
    vis = [c for c in ctrls if c.get('visible')]
    print(f'{tab:12s} {len(ctrls):3d} controls ({len(vis)} visible, '
          f'{sum(1 for c in ctrls if c.get("disabled"))} disabled)')

json.dump(inv, open(os.path.join(SP, 'react_inventory.json'), 'w'), ensure_ascii=False, indent=1)

# union across tabs, keyed by path — the toolbar/right panel repeat on every tab
union = {}
for tab, d in inv.items():
    for c in d['controls']:
        union.setdefault(c['path'], {**c, 'tabs': []})['tabs'].append(tab)
json.dump(union, open(os.path.join(SP, 'react_union.json'), 'w'), ensure_ascii=False, indent=1)
print(f'\nunion: {len(union)} distinct controls')
by_region = {}
for c in union.values():
    by_region.setdefault(c['region'], 0)
    by_region[c['region']] += 1
for r, n in sorted(by_region.items(), key=lambda kv: -kv[1]):
    print(f'  {r:14s} {n}')
