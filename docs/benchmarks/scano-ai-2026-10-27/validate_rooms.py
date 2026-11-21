"""Mesh DCTS example rooms with the current main, run the AI solve through a server, score against the CFD target.

    python validate_rooms.py <server url> <room dir> ... [--model scano_c118_s18]

Each room dir holds <name>.cfdproj (case/DCTS/make_dcts_project.py) and its number <NNNN> at the end of the name;
the reference is _tmp/scano_pkg/examples/case_<NNNN>/model_input_cache. Prints one JSON line per room.
"""
import argparse
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('url')
    ap.add_argument('rooms', nargs='+')
    ap.add_argument('--model', default='scano_c118_s18')
    a = ap.parse_args()
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, body=None):
        req = urllib.request.Request(a.url + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
        with op.open(req, timeout=600) as r:
            return json.loads(r.read())
    cred = (REPO / 'src/server/auth-local/admin_credentials').read_text().split('\n')
    call('/api/auth/login', {'username': cred[0], 'password': cred[1]})
    for room in a.rooms:
        room = Path(room).resolve()
        name = room.name
        num = name[-4:]
        t0 = time.time()
        mesh = subprocess.run([sys.executable, '-m', 'backend.api', 'air', str(room / f'{name}.cfdproj'), '--mesh-only',
                               '--mesh-engine', 'snappy', '--mesh-processes', '24', '--max-processes', '24'],
                              cwd=REPO / 'src', capture_output=True, text=True)
        res = json.loads(mesh.stdout[mesh.stdout.index('{'):]) if '{' in mesh.stdout else {}
        if res.get('status') != 'succeeded':
            print(json.dumps({'room': name, 'stage': 'mesh', 'error': mesh.stdout[-800:] + mesh.stderr[-800:]}), flush=True)
            continue
        t_mesh = time.time() - t0
        call('/api/set-case-dir', {'caseDir': str(room)})
        cfdrun = next((room / 'model').glob('*.cfdrun')).read_text()
        job = call('/api/ai/scano/run', {'cfdrunJson': cfdrun, 'cfdrunFilename': f'{name}.cfdrun', 'stlFiles': {},
                                         'model': a.model, 'mode': 'exact'})
        t1 = time.time()
        while True:
            st = call(f"/api/job-status?job_id={job['jobId']}")
            if st['done']:
                break
            time.sleep(2)
        t_ai = time.time() - t1
        r = st.get('scanoResult') or {}
        if st['returncode'] != 0:
            print(json.dumps({'room': name, 'stage': 'ai', 'returncode': st['returncode']}), flush=True)
            continue
        base = Path(r['base'])                  # mode exact: the request's state is the first iteration's own
        ev = subprocess.run([sys.executable, str(HERE / 'eval_base.py'), str(base), 't1',
                             str(REPO / f'_tmp/scano_pkg/examples/case_{num}/model_input_cache'), '--model', a.model],
                            capture_output=True, text=True)
        line = [l for l in ev.stdout.splitlines() if 'err_mean' in l]
        print(json.dumps({'room': name, 'cells': r.get('nCells'), 'mesh_s': round(t_mesh, 1), 'ai_job_s': round(t_ai, 1),
                          'timing': r.get('timing'), 'warnings': r.get('warnings'), 'eval': line[-1] if line else ev.stderr[-600:]}),
              flush=True)


if __name__ == '__main__':
    main()
