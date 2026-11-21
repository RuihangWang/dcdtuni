#!/usr/bin/env python3
"""Phase D — turn the sweep and the HTML probe into an alignment verdict per control."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import SP
from alignment import MAP, FIELD_MAP

def load(name, default=None):
    p = os.path.join(SP, name)
    if not os.path.exists(p):
        return default
    return json.load(open(p))

res = load('exercise_results.json', [])
html = load('html_probe.json', {})
react_cfg = load('react_probe.json', {})

# ---- 1. did every control actually do something? -------------------------
DEAD = []          # a live control that changed nothing at all
DISABLED = []      # deliberately off
ERRORS = []
ACTED = []
for r in res:
    o = r.get('outcome', '')
    if o == 'DISABLED':
        DISABLED.append(r)
    elif o.startswith('ERROR'):
        ERRORS.append(r)
    elif o in ('NO_EFFECT', 'VANISHED', 'NO_ELEMENT'):
        DEAD.append(r)
    elif o == 'SEPARATE':
        pass
    else:
        ACTED.append(r)

print('=' * 78)
print('PHASE B — every React control clicked')
print('=' * 78)
print(f'  exercised          {len(res)}')
print(f'  produced an effect {len(ACTED)}')
print(f'  correctly disabled {len(DISABLED)}')
print(f'  NO EFFECT          {len(DEAD)}   <- each of these needs an explanation')
print(f'  raised an error    {len(ERRORS)}')
sep = [r for r in res if r.get('outcome') == 'SEPARATE']
print(f'  long jobs (separate) {len(sep)}: ' + ', '.join(r['path'] for r in sep))

if DEAD:
    print('\n  -- controls with NO observable effect --')
    for r in DEAD:
        print(f'    {r.get("tab",""):11s} {r.get("path","")[:44]:46s} {str(r.get("label",""))[:24]:26s} '
              f'{r.get("action","")[:14]:16s} {r.get("outcome","")}')
if ERRORS:
    print('\n  -- controls that raised an error --')
    for r in ERRORS:
        print(f'    {r.get("path","")[:44]:46s} {r.get("outcome","")}')

not_restored = [r for r in res if r.get('restored') is False]
print(f'\n  needed a reload to restore: {len(not_restored)}')

# ---- 2. value alignment against the HTML editor ---------------------------
if html and react_cfg:
    print('\n' + '=' * 78)
    print('PHASE C — same project, same values in both editors')
    print('=' * 78)
    def dig(o, path):
        cur = o
        for part in path.split('.'):
            if not isinstance(cur, dict) or part not in cur:
                return '<absent>'
            cur = cur[part]
        return cur
    agree = differ = missing = 0
    rows = []
    for key, (html_id, path) in sorted(FIELD_MAP.items()):
        bucket = 'mesh' if path.startswith('meshConfig') else 'sim'
        sub = path.split('.', 1)[1]
        h = dig(html.get('config', {}).get(bucket, {}), sub)
        r = dig(react_cfg.get(bucket, {}), sub)
        if h == '<absent>' and r == '<absent>':
            missing += 1; verdict = 'both absent'
        elif h == r:
            agree += 1; verdict = 'MATCH'
        else:
            differ += 1; verdict = 'DIFFER'
        rows.append((key, html_id or '(no HTML control)', h, r, verdict))
    print(f'  fields compared {len(rows)}:  MATCH {agree}   DIFFER {differ}   both absent {missing}')
    bad = [x for x in rows if x[4] == 'DIFFER']
    if bad:
        print('\n  -- fields that DIFFER --')
        for k, hid, h, r, _ in bad:
            print(f'    {k:34s} html={json.dumps(h,ensure_ascii=False)[:26]:28s} react={json.dumps(r,ensure_ascii=False)[:26]}')
    only_react = [x for x in rows if x[1] == '(no HTML control)']
    if only_react:
        print('\n  -- reachable in React, NOT reachable in the HTML editor --')
        for k, _, h, r, _ in only_react:
            print(f'    {k:34s} value={json.dumps(r,ensure_ascii=False)[:40]}')
else:
    print('\n(HTML probe not run yet — phase C skipped)')

json.dump({'dead': DEAD, 'errors': ERRORS, 'disabled': [r['path'] for r in DISABLED],
           'acted': len(ACTED)}, open(os.path.join(SP, 'verify_summary.json'), 'w'),
          ensure_ascii=False, indent=1)
