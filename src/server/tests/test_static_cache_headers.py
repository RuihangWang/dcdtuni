"""Textures may be cached by the browser; code and pages may not.

The shared material pool (`/textures/`, about 20 MB per page load) only changes
with a commit, so the browser may keep it and revalidate with Last-Modified
(answered 304). Scripts and pages stay `no-store` so an edit is always picked
up. Run from the repository root with ``--basetemp=_tmp/...``.
"""
from __future__ import annotations

import http.client
import http.server
import threading
from contextlib import contextmanager

import server


@contextmanager
def static_harness():
    class Handler(server.STLServerHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(server.HTML_DIR), **kwargs)

        def log_message(self, *_args):
            pass

    listener = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    listener.daemon_threads = True
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()

    def get(path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', listener.server_port, timeout=5)
        try:
            connection.request('GET', path, headers=dict(headers or {}))
            response = connection.getresponse()
            response.read()
            return response.status, dict(response.getheaders())
        finally:
            connection.close()
    try:
        yield get
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)


def test_textures_are_cacheable_and_revalidate(monkeypatch):
    monkeypatch.setattr(server, 'APP_MODE', 'react')
    with static_harness() as get:
        status, headers = get('/textures/materials.json')
        assert status == 200
        assert headers['Cache-Control'] == 'no-cache'
        assert 'Pragma' not in headers and 'Expires' not in headers
        assert headers['Last-Modified']
        status, _ = get('/textures/materials.json', {'If-Modified-Since': headers['Last-Modified']})
        assert status == 304


def test_scripts_stay_uncached(monkeypatch):
    monkeypatch.setattr(server, 'APP_MODE', 'react')
    with static_harness() as get:
        status, headers = get('/js/material_library.js')
        assert status == 200
        assert 'no-store' in headers['Cache-Control']
