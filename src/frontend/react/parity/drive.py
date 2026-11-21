#!/usr/bin/env python3
"""Drive browser-use to click every control in the React editor and record what changed.

One `browser-use eval` per action rather than one big script: React re-renders
after a handler returns, so a synchronous eval that acts and then reads gets the
PRE-render DOM. Separate calls put a real round trip between act and observe.
"""
import json, subprocess, sys, time, os

BU = os.path.expanduser('~/.local/bin/browser-use')
SP = os.path.dirname(os.path.abspath(__file__))

# The administrator login every probe sends: the DCDTUNI_ADMIN_USER /
# DCDTUNI_ADMIN_PASSWORD pair, else the launcher's local-mode file, else the
# pair the probe passes (src/frontend/scripts/admin_login.py).
sys.path.insert(0, os.path.join(SP, '..', '..', 'scripts'))
from admin_login import login_js  # noqa: E402,F401


def ev(js, timeout=120):
    """Run one eval; return the parsed value or a marker string."""
    p = subprocess.run([BU, 'eval', js], capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or '') + (p.stderr or '')
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('result:'):
            body = line[len('result:'):].strip()
            try:
                return json.loads(body)
            except Exception:
                # browser-use prints Python-repr for objects; try that
                try:
                    import ast
                    return ast.literal_eval(body)
                except Exception:
                    return body
    return {'__no_result__': out.strip()[:400]}


def evj(js, timeout=120):
    """Eval something that returns JSON.stringify(...) so we get real JSON back."""
    v = ev('JSON.stringify(' + js + ')', timeout=timeout)
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {'__parse_fail__': v[:400]}
    return v


def screenshot(name):
    subprocess.run([BU, 'screenshot', os.path.join(SP, name)], capture_output=True, text=True)
    return os.path.join(SP, name)
