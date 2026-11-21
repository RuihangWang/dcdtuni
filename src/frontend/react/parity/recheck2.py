#!/usr/bin/env python3
"""Re-test the remaining ambiguous controls with inputs they can actually accept."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import ev, evj, SP

ev(open(os.path.join(SP, 'lib.js')).read())

def proj():
    v = ev("JSON.stringify(window.__dcdtuni.getProject().runDefaults.simulationConfig)")
    return json.loads(v) if isinstance(v, str) else v

def goto_solver():
    ev("(function(){document.querySelectorAll('.left-tab-btn')[2].click(); return 1;})()")
    time.sleep(1.2)

goto_solver()

print('--- residual fields, with a VALID value (the sweep fed them "0.0001X") ---')
labels = ['p_rgh', 'U', 'T', 'k / ε']
for i, key in enumerate(['p_rgh', 'U', 'T', 'k_epsilon']):
    before = proj().get('residualControl', {}).get(key)
    r = ev("(function(){var rows=document.querySelectorAll('#solver-settings-panel .prop-row');"
           "var hits=[]; rows.forEach(function(x){var l=x.querySelector('label');"
           "if(l && /p_rgh|U \\(|T \\(|k \\//.test(l.textContent)) hits.push(x);});"
           f"var el=hits[{i}] && hits[{i}].querySelector('input'); if(!el) return 'NO_EL';"
           "var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
           "el.focus(); s.call(el,'3e-6'); el.dispatchEvent(new Event('input',{bubbles:true})); el.blur();"
           "return 'typed 3e-6';})()")
    time.sleep(0.6)
    after = proj().get('residualControl', {}).get(key)
    print(f'  residualControl.{key:10s} {before} -> {after}   {"OK" if after == 3e-06 else "FAILED"}   ({r})')
    ev('window.__dcdtuni.undo(); 1'); time.sleep(0.3)

print('\n--- solverType radio: click the OTHER option (the sweep clicked the checked one) ---')
before = proj().get('solverType')
ev("(function(){var r=document.querySelectorAll('#solver-settings-panel input[name=slvType]');"
   "r[1].click(); return 1;})()")
time.sleep(0.8)
after = proj().get('solverType')
print(f'  solverType {before} -> {after}   {"OK" if after == "transient" else "FAILED"}')
ev("(function(){var r=document.querySelectorAll('#solver-settings-panel input[name=slvType]');"
   "r[0].click(); return 1;})()")
time.sleep(0.6)
print(f'  restored -> {proj().get("solverType")}')
