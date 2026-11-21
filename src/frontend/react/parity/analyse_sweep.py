#!/usr/bin/env python3
"""Summarise a click-sweep run: what every control did, and which did nothing.

A NO_EFFECT is not automatically a defect — a checkbox that is already on, a
button whose panel is empty, a control whose only job is to render something the
snapshot cannot see. But every one of them has to be EXPLAINED, because "the
button does nothing" is exactly the defect this whole migration kept finding.
"""
import json, os, sys
from collections import Counter, defaultdict

SP = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(SP, 'exercise_results.json')))

print(f'{len(rows)} controls exercised\n')
print('outcomes:')
for outcome, n in Counter(r['outcome'] for r in rows).most_common():
    print(f'  {n:4d}  {outcome}')

dead = [r for r in rows if r['outcome'] == 'NO_EFFECT']
print(f'\n=== {len(dead)} NO_EFFECT (each needs an explanation) ===')
by_tab = defaultdict(list)
for r in dead:
    by_tab[r['tab']].append(r)
for tab, rs in by_tab.items():
    print(f'\n-- {tab} ({len(rs)}) --')
    for r in rs:
        print(f"  {r['path'][:44]:46s} {r.get('label','')[:26]:28s} {r.get('action','')}")

bad = [r for r in rows if r['outcome'].startswith('ERROR') or r['outcome'] == 'PROBE_FAILED']
if bad:
    print(f'\n=== {len(bad)} ERROR / PROBE_FAILED ===')
    for r in bad:
        print(f"  {r['path'][:44]:46s} {r['outcome'][:60]}")

nr = [r for r in rows if r.get('restored') is False]
print(f'\n{len(nr)} controls did not restore (the sweep reloaded after each)')
