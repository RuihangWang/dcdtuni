"""Keep hidden software rendering from competing with CFD worker ranks."""
import json
import subprocess
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import _browser_frame_budget_script, _browser_max_fps

_RUNNER = r"""
const fs = require('node:fs'), vm = require('node:vm');
const script = JSON.parse(fs.readFileSync(0, 'utf8'));
const native = new Map(), events = [], errors = [];
let serial = 100;
const window = {
 requestAnimationFrame(cb) { const id = serial++; native.set(id, cb); return id; },
 cancelAnimationFrame(id) { native.delete(id); },
};
const context = {window};
vm.runInNewContext(script, context);
async function tick(t) {
 const ids = [...native.keys()];
 for (const id of ids) {
   const cb = native.get(id);
   if (!cb) continue;
   native.delete(id);
   try { cb(t); } catch (e) { errors.push(e.message); }
   // Native RAF callbacks are separate script invocations. Drain the
   // microtask queue after each invocation, including nested Promise chains.
   await new Promise(resolve => setImmediate(resolve));
 }
}
(async () => {
 let second;
 window.requestAnimationFrame(function(t) {
   events.push(['first', t, this === window]);
   Promise.resolve().then(() => Promise.resolve())
     .then(() => { events.push(['microtask']); window.cancelAnimationFrame(second); });
   window.requestAnimationFrame(t => events.push(['nested', t]));
 });
 second = window.requestAnimationFrame(t => events.push(['cancelled', t]));
 window.requestAnimationFrame(t => events.push(['peer', t]));
 await tick(10); await tick(26); await tick(100); await tick(259);
 await tick(260); await tick(276);
 const cancelled = window.requestAnimationFrame(t => events.push(['later-cancelled', t]));
 await tick(510);
 window.cancelAnimationFrame(cancelled);
 const nativeAfterCancel = native.size;
 window.requestAnimationFrame(() => { throw new Error('expected callback error'); });
 window.requestAnimationFrame(t => events.push(['after-error', t]));
 await tick(760); await tick(776);
 let invalidCallback = false;
 try { window.requestAnimationFrame(null); } catch(e) { invalidCallback = e.name === 'TypeError'; }
 process.stdout.write(JSON.stringify({events, errors, nativeAfterCancel, invalidCallback, remaining: native.size}));
})().catch(e => { console.error(e); process.exitCode = 1; });
"""

def test_callbacks_keep_native_microtask_cancel_and_exception_semantics():
    run = subprocess.run(["node", "-e", _RUNNER],
        input=json.dumps(_browser_frame_budget_script(4)),
        text=True, capture_output=True, check=True)
    result = json.loads(run.stdout)
    assert result["events"] == [
        ["first", 26, True], ["microtask"], ["peer", 26],
        ["nested", 276], ["after-error", 776]]
    assert result["errors"] == ["expected callback error"]
    assert result["nativeAfterCancel"] == 0
    assert result["remaining"] == 0
    assert result["invalidCallback"] is True

def test_budget_defaults_only_apply_to_software_worker(monkeypatch):
    monkeypatch.delenv("DCDTUNI_BROWSER_MAX_FPS", raising=False)
    monkeypatch.delenv("DCDTUNI_BROWSER_SOFTWARE", raising=False)
    assert _browser_max_fps() == 0
    monkeypatch.setenv("DCDTUNI_BROWSER_SOFTWARE", "1")
    assert _browser_max_fps() == 4
    monkeypatch.setenv("DCDTUNI_BROWSER_MAX_FPS", "0")
    assert _browser_max_fps() == 0
    assert _browser_frame_budget_script(0) == ""
    monkeypatch.setenv("DCDTUNI_BROWSER_MAX_FPS", "15")
    assert _browser_max_fps() == 15

@pytest.mark.parametrize("raw", ["nan", "inf", "-1", ".5", "61", "wrong"])
def test_invalid_budget_is_explicit(monkeypatch, raw):
    monkeypatch.setenv("DCDTUNI_BROWSER_MAX_FPS", raw)
    with pytest.raises(ValueError, match="DCDTUNI_BROWSER_MAX_FPS"):
        _browser_max_fps()


_PAUSE_RUNNER = r"""
const fs = require('node:fs'), vm = require('node:vm');
const script = JSON.parse(fs.readFileSync(0, 'utf8'));
const native = new Map(), events = [];
let serial = 100;
const window = {
 requestAnimationFrame(cb) { const id = serial++; native.set(id, cb); return id; },
 cancelAnimationFrame(id) { native.delete(id); },
};
vm.runInNewContext(script, {window});
async function tick(t) {
 for (const id of [...native.keys()]) {
   const cb = native.get(id);
   if (!cb) continue;
   native.delete(id); cb(t);
   await new Promise(resolve => setImmediate(resolve));
 }
}
(async () => {
 const control = window.__dcdtuniAutomationFrames;
 const cancelled = window.requestAnimationFrame(t => events.push(['cancelled', t]));
 window.requestAnimationFrame(t => {
   events.push(['first', t]);
   window.requestAnimationFrame(t => events.push(['nested', t]));
 });
 await tick(10);
 window.requestAnimationFrame(t => events.push(['newer', t]));
 control.pause(); control.pause();
 window.cancelAnimationFrame(String(cancelled));
 await tick(26);
 const pausedPending = native.size;
 control.resume(); control.resume();
 await tick(100); await tick(116);
 control.pause();
 await tick(500);
 const pausedNested = native.size;
 control.resume();
 await tick(600); await tick(616);
 process.stdout.write(JSON.stringify({events, pausedPending, pausedNested, remaining: native.size}));
})().catch(e => { console.error(e); process.exitCode = 1; });
"""

def test_idle_pause_suspends_native_work_and_retains_cancelable_pending_callbacks():
    run = subprocess.run(["node", "-e", _PAUSE_RUNNER],
        input=json.dumps(_browser_frame_budget_script(4)),
        text=True, capture_output=True, check=True)
    result = json.loads(run.stdout)
    assert result == {"events": [["first", 116], ["newer", 116], ["nested", 616]],
        "pausedPending": 0, "pausedNested": 0, "remaining": 0}
