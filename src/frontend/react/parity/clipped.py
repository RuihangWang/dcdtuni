#!/usr/bin/env python3
"""Controls that are IN the DOM but clipped outside the panel that holds them.

`id_diff.py` and `label_diff.py` both answer "does the control exist", and both
say yes for a control that is present, enabled, correctly labelled and sitting
100px past the right edge of a panel with `overflow: hidden`. That is not a
theoretical gap: the volume-rendering section shipped that way — its field and
colour-map selects, its two limit boxes and its Apply button were all off the
panel, so the section could not be used at all, and the sweep called every one
of them fine.

The test is horizontal only. A control scrolled out of view VERTICALLY is
normal — the panels scroll — but nothing in these panels scrolls sideways, so a
box whose left or right edge falls outside its panel is being clipped away.

    python3 clipped.py            # the app browser-use is currently pointed at
    python3 clipped.py --expand   # tick every section checkbox open first
    python3 clipped.py --expand --pp-init   # ... and unlock the postprocess panel

Each tab prints how many controls it SCANNED as well as how many were clipped:
a zero over a small scan count is not a pass, it is a region the sweep never
reached. The volume section is the standing example — it lives behind
`开启后处理`, so without `--pp-init` it is invisible to this check and the run
reports a clean zero over a panel whose controls are all off-screen.
"""
import json, os, subprocess, sys, time

TABS = ['geometry', 'mesh', 'solver', 'transient', 'postprocess', 'system']
# Panels that clip: everything that owns a column of controls in either app.
PANELS = ['#left-panel', '#right-panel', '.panel']


def ev(js):
    r = subprocess.run(['browser-use', 'eval', js], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith('result:'):
            return line[len('result:'):].strip()
    return ''


def is_react():
    # By the API each app exposes, not by a class name: BOTH apps mark their tab
    # buttons `.left-tab-btn`, and the legacy carries hidden duplicates of them
    # that a positional click lands on instead of the real tab.
    return ev("(function(){return window.__dcdtuni?'y':'n';})()") == 'y'


PP_INIT = """(function(){
 var run=document.getElementById('pp-log-run');
 if(!run||!run.options.length) return 'no runs';
 var btn=document.getElementById('btn-init-postprocess');
 if(!btn) return 'no init button';
 if(getComputedStyle(document.getElementById('postprocess-controls')||{style:{}}).display!=='none')
   return 'already initialised';
 btn.click();
 return 'init fired on '+run.options[run.selectedIndex].textContent;
})()"""

# A run whose solver never wrote a time step fails init, and the panel then
# stays locked — try each record until one opens the controls.
PP_PICK = """(function(i){
 var run=document.getElementById('pp-log-run');
 if(!run||i>=run.options.length) return 'no more runs';
 var set=Object.getOwnPropertyDescriptor(
   window.HTMLSelectElement.prototype,'value').set;
 set.call(run, run.options[i].value);
 run.dispatchEvent(new Event('change',{bubbles:true}));
 if(window.onLogRunChange) window.onLogRunChange();
 return 'picked '+run.options[i].textContent;
})"""

SCAN = """(function(){
 var out=[];
 document.querySelectorAll('input,select,button,textarea').forEach(function(e){
   var r=e.getBoundingClientRect();
   if(r.width<=0||r.height<=0) return;
   // EVERY clipping ancestor, not just the nearest. `overflow: hidden` is what
   // turns an overflowing child into an invisible one — and the nearest clipper
   // is often innocent: an oversized `fieldset.prop-group` CONTAINS its own
   // controls perfectly well while itself hanging out of the panel, so stopping
   // at the first one reports a clean pass over a section nobody can use.
   var p=e.parentElement;
   while(p && p!==document.body){
     var cs=getComputedStyle(p);
     if(cs.overflowX!=='visible'){
       var pr=p.getBoundingClientRect();
       if(pr.width>0 && (r.right>pr.right+1 || r.left<pr.left-1)){
         out.push({el:(e.id||e.name||e.tagName+':'+(e.textContent||'').trim().slice(0,16)),
                   clip:(p.id||p.className||p.tagName).toString().slice(0,28),
                   el_x:Math.round(r.left), el_r:Math.round(r.right),
                   box_x:Math.round(pr.left), box_r:Math.round(pr.right)});
         break;
       }
     }
     p=p.parentElement;
   }
 });
 return JSON.stringify({scanned:document.querySelectorAll('input,select,button,textarea').length,
                        clipped:out});
})()"""

EXPAND = """(function(){
 var n=0;
 document.querySelectorAll('input[type=checkbox]').forEach(function(c){
   // Only the checkboxes that REVEAL a block of controls, found by their id:
   // both apps name them `*-show`.
   if(/-show$/.test(c.id) && !c.checked){ c.click(); n++; }
 });
 return 'expanded '+n;
})()"""


def switch(tab, i, react):
    if react:
        ev(f"(function(){{var b=document.querySelectorAll('.left-tab-btn');if(b[{i}])b[{i}].click();return 'ok';}})()")
    else:
        ev(f"(function(){{window.switchLeftTab&&window.switchLeftTab('{tab}');return 'ok';}})()")
    time.sleep(1.2)


def unlock_postprocess():
    """Open the post-processing controls, which are `display: none` until a run
    is loaded — and which hold the volume section, the densest block of controls
    in either app."""
    for i in range(8):
        print('  ' + ev(f'({PP_PICK})({i})'), flush=True)
        time.sleep(1.5)
        print('  ' + ev(PP_INIT), flush=True)
        for _ in range(30):
            time.sleep(4)
            shown = ev("(function(){var c=document.getElementById('postprocess-controls');"
                       "return c?getComputedStyle(c).display:'none';})()")
            err = ev("(function(){var e=document.getElementById('pp-error');"
                     "return e?e.textContent.slice(0,60):'';})()")
            if shown != 'none':
                print('  postprocess controls open', flush=True)
                return True
            if err:
                print(f'  init failed: {err}', flush=True)
                break
    print('  could not open the postprocess controls', flush=True)
    return False


def main():
    expand = '--expand' in sys.argv
    react = is_react()
    app = 'react' if react else 'html'
    total = 0
    for i, tab in enumerate(TABS):
        switch(tab, i, react)
        if tab == 'postprocess' and '--pp-init' in sys.argv:
            unlock_postprocess()
        if expand:
            ev(EXPAND)
            time.sleep(0.8)
        try:
            result = json.loads(ev(SCAN) or '{}')
        except Exception:
            result = {}
        bad = result.get('clipped', [])
        total += len(bad)
        print(f'{app} {tab}: {len(bad)} clipped of {result.get("scanned", 0)} scanned',
              flush=True)
        for b in bad:
            print(f"    {b['el']}  [{b['el_x']}..{b['el_r']}]  clipped by "
                  f"{b['clip']} [{b['box_x']}..{b['box_r']}]")
    print(f'\ntotal controls clipped out of their panel: {total}')
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
