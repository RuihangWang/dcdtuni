#!/usr/bin/env python3
"""Phase C — read the same values out of the HTML editor, for the same project.

Value alignment is checked against the DATA both apps write, not against the two
DOMs: the panels are laid out differently and comparing widgets would only prove
they look alike. What matters is that `runDefaults` ends up the same.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drive import ev, evj, SP, login_js
from alignment import MAP, FIELD_MAP

LOAD = ("(function(){try{"
        + login_js(('admin', 'WIN#@desk')) +
        "var base='/mnt/lv/home/litianyu/mycode/dcdtuni/case/Base_leakage';"
        "var c=new XMLHttpRequest();c.open('POST','/api/set-case-dir',false);"
        "c.setRequestHeader('Content-Type','application/json');"
        "c.send(JSON.stringify({caseDir:base}));"
        "var r=new XMLHttpRequest();r.open('GET','/api/read-file?path='+encodeURIComponent(base+'/Base_leakage.cfdproj'),false);r.send();"
        "window._applyProjectData(JSON.parse(JSON.parse(r.responseText).content));"
        "return 'loaded '+window.allModels().length;}catch(e){return 'ERR '+e.message;}})()")


def read_config(app):
    """Pull runDefaults out of whichever app is loaded."""
    if app == 'html':
        js = ("JSON.stringify({mesh: state.runDefaults.meshConfig,"
              " sim: state.runDefaults.simulationConfig})")
    else:
        js = ("JSON.stringify((function(){var p=window.__dcdtuni.getProject();"
              "return {mesh:p.runDefaults.meshConfig, sim:p.runDefaults.simulationConfig};})())")
    v = ev(js)
    return json.loads(v) if isinstance(v, str) else v


def dig(obj, path):
    cur = obj
    for part in path.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return '<absent>'
        cur = cur[part]
    return cur


def main():
    print('--- HTML editor ---')
    print(ev(LOAD))
    time.sleep(2.5)
    ev("window.switchLeftTab('mesh'); 1"); time.sleep(1.0)
    html_cfg = read_config('html')
    html_ids = evj(
        "(function(){var out={};"
        "['ms-base-size','ms-max-cells','ms-parallel','ms-procs','ms-mesh-mode','ms-first-cell',"
        "'ms-rl-0','ms-rl-1','ms-feat-default','ms-rack-region','ms-acu-0','ms-acu-1','ms-feat-acu',"
        "'ms-box-0','ms-box-1','ms-feat-box','ms-pw-0','ms-pw-1','ms-feat-pw','ms-bfl-0','ms-bfl-1',"
        "'ms-feat-baffle','ms-detect-base','slv-loc-x','slv-loc-y','slv-loc-z','loc-visible-cb']"
        ".forEach(function(id){var e=document.getElementById(id);"
        "out[id]= e ? (e.type==='checkbox'?String(e.checked):String(e.value)) : '<missing>';});"
        "return out;})()")
    ev("window.switchLeftTab('solver'); 1"); time.sleep(1.0)
    html_solver_ids = evj(
        "(function(){var out={};"
        "['slv-endtime','slv-writeinterval','slv-write-precision','slv-end-time-s','slv-start-from',"
        "'slv-delta-t','slv-max-delta-t','slv-max-co','slv-write-interval-s','slv-ddt-scheme',"
        "'slv-cn-coeff','slv-n-outer','slv-n-correctors','slv-parallel','slv-procs','slv-gx','slv-gy',"
        "'slv-gz','slv-nonortho','slv-nnonortho','slv-div-U','slv-div-T','slv-div-ke','slv-laplacian',"
        "'slv-sngrad','slv-relax-p','slv-relax-u','slv-relax-t','slv-relax-ke','slv-res-prgh',"
        "'slv-res-U','slv-res-T','slv-res-kepsilon']"
        ".forEach(function(id){var e=document.getElementById(id);"
        "out[id]= e ? (e.type==='checkbox'?String(e.checked):String(e.value)) : '<missing>';});"
        "return out;})()")
    html_ids.update(html_solver_ids)
    json.dump({'config': html_cfg, 'ids': html_ids},
              open(os.path.join(SP, 'html_probe.json'), 'w'), ensure_ascii=False, indent=1)
    print(f'html: {len(html_cfg.get("mesh", {}))} mesh keys, {len(html_cfg.get("sim", {}))} sim keys, '
          f'{len(html_ids)} widgets read')


main()
