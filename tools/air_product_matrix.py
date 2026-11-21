"""Verify current project meshes through the real React browser/server API.

Start an isolated React service with src/start_server.sh, then set
DCDTUNI_MATRIX_USER and DCDTUNI_MATRIX_PASSWORD. All scratch and receipt paths
are constrained to this checkout. A deadline or return code alone is never a
successful mesh: final checkMesh and complete-pipeline timing must pass.
"""
from __future__ import annotations
import argparse, hashlib, http.cookiejar, json, os, pathlib, re, subprocess, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]


def under_root(value: str) -> pathlib.Path:
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('matrix paths must remain inside dcdtuni')
    return path


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Client:
    def __init__(self, base: str):
        if urllib.parse.urlparse(base).hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('use the server loopback URL on its host')
        self.base = base.rstrip('/')
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.call('/api/auth/login', {'username': os.environ['DCDTUNI_MATRIX_USER'],
                                     'password': os.environ['DCDTUNI_MATRIX_PASSWORD']})

    def call(self, path: str, data=None):
        raw = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.base + path, data=raw,
            headers={'Content-Type': 'application/json'} if raw is not None else {})
        try:
            response = self.opener.open(request, timeout=240)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'{path}: HTTP {exc.code}: {exc.read().decode()}') from exc
        with response:
            body = response.read().decode()
        return json.loads(body) if body.lstrip().startswith(('{', '[')) else body


def clock_stamp():
    return {"monotonicSeconds": time.monotonic(), "utc": datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', default='http://127.0.0.1:8396')
    parser.add_argument('--output', default='docs/benchmarks/air-product-presets-2026-10-05/matrix-current')
    parser.add_argument('--scratch', default='.codex-tmp/air-product-matrix-20261005')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--native-mode', choices=('reference', 'differential', 'region-differential', 'direct', 'production'), default='production', help='explicit native mode used by the isolated server; recorded with raw native output')
    parser.add_argument('--exclude', action='append', default=[], help='explicitly deferred source path; recorded in matrix metadata')
    parser.add_argument('projects', nargs='*', help='default: current case, room-template and experiment inputs')
    args = parser.parse_args()
    output, scratch = under_root(args.output), under_root(args.scratch)
    output.mkdir(parents=True, exist_ok=True); scratch.mkdir(parents=True, exist_ok=True)
    sources = args.projects or [p for p in subprocess.check_output(
        ['git', 'ls-files', 'case', 'docs/benchmarks/air-product-presets-2026-10-05/room-templates', 'docs/manuscript/paper_experiment'],
        cwd=ROOT, text=True).splitlines() if p.endswith('.cfdproj')]
    native = ROOT/'src/backend/aircooling/third_parties/dcCartMesh/platforms/linux64GccDPInt32Opt'
    binary_hashes = {str(p.relative_to(ROOT)): digest(p) for p in
        (native/'bin/dcCartMesh', native/'lib/libdcCartMesh.so')}
    records_path = output/'matrix.json'
    records = json.loads(records_path.read_text()) if args.resume and records_path.exists() else []
    previous = {r['source']: r for r in records}
    sources = [p for p in sources if p not in args.exclude]
    (output/'conditions.json').write_text(json.dumps({
        'server': args.server, 'excludedSources': args.exclude, 'nativeMode': args.native_mode,
        'acceptance': 'complete recorded source preparation, React export, browser close, API mesh/check and log retrieval <=300 s; final strict checkMesh success',
        'softwareBrowserMaxFps': os.environ.get('DCDTUNI_BROWSER_MAX_FPS'),
        'nativeResourceCondition': 'genuine React export, then explicit owned software Chromium close before each API mesh job',
        'nativeSha256': binary_hashes}, indent=2)+'\n')
    client = Client(args.server)
    for source in sources:
        sequence_started = time.monotonic()
        timing_events = {"sourcePreparationStarted": clock_stamp()}
        try:
            src = under_root(source); source_hash = digest(src)
        except (OSError, ValueError) as exc:
            record = {'source': source, 'accepted': False, 'failure': 'input unavailable: '+str(exc)}
            records = [r for r in records if r['source'] != source] + [record]
            records_path.write_text(json.dumps(records, indent=2)+'\n')
            print(json.dumps(record), flush=True)
            continue
        prior = previous.get(source)
        if (args.resume and prior and prior.get('accepted') is True and prior.get('sourceSha256') == source_hash
                and prior.get('nativeSha256') == binary_hashes):
            continue
        name = re.sub(r'[^A-Za-z0-9_-]', '_', src.stem) + '_' + hashlib.sha256(source.encode()).hexdigest()[:8]
        case = scratch/name; case.mkdir(parents=True, exist_ok=True)
        project = json.loads(src.read_text()); project.setdefault('meta', {})['casePath'] = str(case)
        proj_path = case/src.name; proj_path.write_text(json.dumps(project, indent=2)+'\n')
        record = {'source': source, 'sourceSha256': source_hash, 'nativeSha256': binary_hashes,
                  'caseDir': str(case.relative_to(ROOT)), 'accepted': False,
                  'timingEvents': timing_events}
        started = time.monotonic()
        timing_events["sourcePreparationFinished"] = clock_stamp()
        try:
            client.call('/api/set-case-dir', {'caseDir': str(case)})
            # export-case itself loads through React and waits for rebuilt geometry.
            export_started = time.monotonic()
            timing_events["reactExportRequestStarted"] = clock_stamp()
            record['export'] = client.call('/api/browser/export-case', {'projPath': str(proj_path)})
            timing_events['reactExportResponseReceived'] = clock_stamp()
            record['reactLoadAndExportWallSeconds'] = time.monotonic()-export_started
            if not record['export'].get('success'):
                raise RuntimeError(f'React export failed: {record["export"]}')
            for field in ('triSurfaceDir', 'cfdrunPath'):
                artifact = record['export'].get(field)
                if not artifact or not pathlib.Path(artifact).resolve().is_relative_to(case.resolve()):
                    raise RuntimeError(f'React export escaped the isolated case: {field}={artifact}')
            record['geometry'] = client.call('/api/browser/geometry-status')
            artifact = pathlib.Path(record['export']['cfdrunPath'])
            record['cfdrunSha256'] = digest(artifact)
            record['stlSha256'] = {str(p.relative_to(case)): digest(p) for p in sorted(pathlib.Path(record['export']['triSurfaceDir']).glob('*.stl'))}
            # Loading a project can update the server case pointer. Reset it
            # after export as well, before requesting a native job.
            record['caseSelection'] = client.call('/api/set-case-dir', {'caseDir': str(case)})
            # Release this owned software Chromium after the real React export.
            # Hidden rendering must not consume the native job's CPU budget.
            close_started = time.monotonic()
            timing_events["browserCloseRequestStarted"] = clock_stamp()
            record['browserClose'] = client.call('/api/browser/close', {})
            timing_events['browserCloseResponseReceived'] = clock_stamp()
            record['browserCloseWallSeconds'] = time.monotonic()-close_started
            if not record['browserClose'].get('ok'):
                raise RuntimeError(f'owned software browser close failed: {record["browserClose"]}')
            if any(digest(ROOT/path) != value for path, value in binary_hashes.items()):
                raise RuntimeError('native binary identity changed during this matrix; freeze or start a new receipt')
            mesh_start = time.monotonic()
            timing_events["meshRequestStarted"] = clock_stamp()
            record['job'] = client.call('/api/run-mesh', {'meshOnly': True, 'solveOnly': False})
            timing_events['meshSubmissionResponseReceived'] = clock_stamp()
            job_id = record['job'].get('jobId', record['job'].get('job_id'))
            if not job_id:
                raise RuntimeError(f'missing mesh job identity: {record["job"]}')
            while True:
                status = client.call('/api/job-status?job_id='+str(job_id))
                if status.get('done'):
                    break
                time.sleep(1)
            timing_events['meshDoneObserved'] = clock_stamp()
            record['meshWallSeconds'] = time.monotonic()-mesh_start
            record['meshStatus'] = status
            timing_events['rawLogRequestStarted'] = clock_stamp()
            log = client.call('/api/log-stream?job_id='+str(job_id))
            timing_events['rawLogResponseReceived'] = clock_stamp()
            log_path = output/(name+'.sse.log')
            log_path.write_text(log if isinstance(log, str) else json.dumps(log))
            record['log'] = str(log_path.relative_to(ROOT))
            # An earlier native/pipeline refusal is the primary failure;
            # a differential marker absent after that refusal is secondary.
            if status.get('returncode') != 0 or status.get('checkMeshOk') is not True:
                raise RuntimeError(f'mesh job rejected: returncode={status.get("returncode")}, checkMeshOk={status.get("checkMeshOk")}; see raw SSE')
            if args.native_mode == 'differential' and 'Direct corner audit: exact coordinates' not in log:
                raise RuntimeError('native differential audit was requested but is absent from fresh raw output')
            if args.native_mode == 'region-differential' and 'Region query audit: exact reference area/plane/thin results match' not in log:
                raise RuntimeError('native region differential audit was requested but is absent from fresh raw output')
            record['accepted'] = bool(status.get('returncode') == 0
                and status.get('checkMeshOk') is True and record['meshWallSeconds'] <= 300
                and time.monotonic()-sequence_started <= 300)
            if not record['accepted']:
                record['failure'] = 'missing/failed checkMesh, nonzero exit, or complete pipeline over 300 seconds'
        except Exception as exc:
            record['failure'] = str(exc)
        timing_events['recordedSequenceFinished'] = clock_stamp()
        record['exportAndMeshWallSeconds'] = time.monotonic()-started
        record['wholeProductSequenceWallSeconds'] = time.monotonic()-sequence_started
        if record['accepted'] and record['wholeProductSequenceWallSeconds'] > 300:
            record['accepted'] = False
            record['failure'] = 'complete recorded product sequence exceeds 300 seconds'
        records = [r for r in records if r['source'] != source] + [record]
        records_path.write_text(json.dumps(records, indent=2)+'\n')
        print(json.dumps({k: record[k] for k in ('source', 'accepted', 'meshWallSeconds', 'failure') if k in record}), flush=True)


if __name__ == '__main__':
    main()
