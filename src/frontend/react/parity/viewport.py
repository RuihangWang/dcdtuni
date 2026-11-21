#!/usr/bin/env python3
"""Resize the browser-use Chromium and measure the layout at that size.

browser-use has no viewport API, so this speaks CDP directly:
`Emulation.setDeviceMetricsOverride` changes what the page believes the window
is, which is what a media query, a vh unit and `window.innerWidth` all read.

Why this exists: "the UI does not adapt to window size" cannot be checked from
one screenshot at one size. Every claim about responsiveness needs the same
measurement repeated at several widths, and doing that by hand is how it stops
being done.
"""
import asyncio, json, sys, urllib.request
import websockets

def discover_port():
    """Find the CDP port of whatever Chromium browser-use is currently running.

    Hardcoding it does not survive the day: browser-use restarts its browser and
    the debug port changes with it, and the failure looks like a connection
    refused deep inside a measurement rather than "the browser moved".
    """
    import subprocess
    out = subprocess.run(['ss', '-ltnp'], capture_output=True, text=True).stdout
    ports = []
    for line in out.splitlines():
        if 'chrome' not in line.lower():
            continue
        addr = line.split()[3]
        try:
            ports.append(int(addr.rsplit(':', 1)[1]))
        except (ValueError, IndexError):
            continue
    for port in ports:
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/json/version', timeout=2)
            return port
        except Exception:
            continue
    raise SystemExit('no CDP endpoint found; is a browser-use browser running?')


def page_ws(port, want_url='localhost:8010'):
    tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=5))
    pages = [t for t in tabs if t.get('type') == 'page']
    for t in pages:
        if want_url in (t.get('url') or ''):
            return t['webSocketDebuggerUrl']
    if not pages:
        raise SystemExit('no page target; is a page open?')
    return pages[0]['webSocketDebuggerUrl']

async def _run(port, sizes, js):
    ws_url = page_ws(port)
    out = []
    async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
        i = 0
        async def call(method, params=None):
            nonlocal i
            i += 1
            await ws.send(json.dumps({'id': i, 'method': method, 'params': params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get('id') == i:
                    return msg
        for w, h in sizes:
            await call('Emulation.setDeviceMetricsOverride',
                       {'width': w, 'height': h, 'deviceScaleFactor': 1, 'mobile': False})
            await asyncio.sleep(1.2)
            r = await call('Runtime.evaluate',
                           {'expression': js, 'returnByValue': True, 'awaitPromise': True})
            val = (r.get('result', {}).get('result', {}) or {}).get('value')
            out.append({'size': f'{w}x{h}', 'result': val})
        await call('Emulation.clearDeviceMetricsOverride')
    return out

def measure(sizes, js, port=None):
    if port is None:
        port = discover_port()
    return asyncio.run(_run(port, sizes, js))

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != 'auto' else discover_port()
    js = sys.stdin.read()
    for row in measure([(1920,1080),(1600,900),(1366,768),(1280,720),(1100,700),(900,600)], js, port):
        print(row['size'], '->', row['result'])
