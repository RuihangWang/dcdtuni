#!/usr/bin/env python3
"""Read runDefaults out of the React editor, for the value-alignment comparison."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import ev, SP, login_js

ev("(function(){" + login_js(('admin', 'WIN#@desk')) +
   "window.prompt=function(){return '/mnt/lv/home/litianyu/mycode/dcdtuni/case/Base_leakage/Base_leakage.cfdproj';};"
   "document.querySelectorAll('.file-btn')[0].click(); return 'ok';})()")
time.sleep(4)
v = ev("JSON.stringify((function(){var p=window.__dcdtuni.getProject();"
       "return {mesh:p.runDefaults.meshConfig, sim:p.runDefaults.simulationConfig};})())")
cfg = json.loads(v) if isinstance(v, str) else v
json.dump(cfg, open(os.path.join(SP, 'react_probe.json'), 'w'), ensure_ascii=False, indent=1)
print(f'react: {len(cfg.get("mesh",{}))} mesh keys, {len(cfg.get("sim",{}))} sim keys')
