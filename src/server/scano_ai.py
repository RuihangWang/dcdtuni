"""The server's AI worker: one long-lived ``backend.aircooling.scano.worker`` process on one GPU, chosen once
(DCDTUNI_SCANO_GPU = UUID, index or "cpu"; GPU 0 by default, the CPU on a host without one). Requests are serialised.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

SRC = Path(__file__).resolve().parents[1]
# Free memory the worker needs to start on a GPU: a DCTS room under TTA peaks at about 3.8 GiB (c118); each run
# then picks its deployment by the memory free at that moment (backend.aircooling.scano.memory).
MIN_FREE_MIB = int(os.environ.get('DCDTUNI_SCANO_MIN_FREE_MIB', '6000'))


def choose_gpu() -> Dict:
    """{'cuda_visible_devices', 'label'} for the worker (module docstring)."""
    want = os.environ.get('DCDTUNI_SCANO_GPU', '').strip() or '0'
    if want.lower() == 'cpu':
        return {'cuda_visible_devices': '', 'label': 'cpu (DCDTUNI_SCANO_GPU)'}
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid,name,memory.used,memory.total',
                              '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ''
    gpus = []
    for line in out.strip().splitlines():
        idx, uuid, name, used, total = [x.strip() for x in line.split(',')]
        gpus.append({'index': idx, 'uuid': uuid, 'name': name, 'used': int(used), 'total': int(total)})
    if not gpus:
        return {'cuda_visible_devices': '', 'label': 'cpu (no GPU)'}
    hit = [g for g in gpus if want in (g['index'], g['uuid'])]
    if not hit:
        raise RuntimeError(f'DCDTUNI_SCANO_GPU={want} matches no GPU ({[g["index"] for g in gpus]})')
    g = hit[0]
    free = g['total'] - g['used']
    if free < MIN_FREE_MIB:
        others = ', '.join(f"{x['index']}: {(x['total'] - x['used']) // 1024} GiB" for x in gpus)
        raise RuntimeError(f"GPU {g['index']} has {free // 1024} GiB free; the AI solve needs {-(-MIN_FREE_MIB // 1024)} GiB. "
                           f"Free memory per GPU: {others}. Choose another GPU with start_server.sh --ai-gpu N "
                           f"(or DCDTUNI_SCANO_GPU=N).")
    return {'cuda_visible_devices': g['uuid'], 'label': f"GPU {g['index']} {g['name']} ({g['uuid']})",
            'index': g['index'], 'uuid': g['uuid'], 'name': g['name']}


def gpu_reading(uuid: str, pid: Optional[int]) -> Optional[Dict]:
    """nvidia-smi now: {'index', 'uuid', 'name', 'totalMiB', 'usedMiB', 'freeMiB', 'utilization', 'workerMiB'}
    of one GPU (workerMiB: what the process pid holds on it), or None."""
    try:
        gpus = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu',
                               '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10).stdout
        apps = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,gpu_uuid,used_memory',
                               '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in gpus.strip().splitlines():
        idx, u, name, total, used, util = [x.strip() for x in line.split(',')]
        if u != uuid:
            continue
        worker = None
        for a in apps.strip().splitlines():
            parts = [x.strip() for x in a.split(',')]
            if len(parts) == 3 and pid is not None and parts[0] == str(pid) and parts[1] == uuid:
                worker = int(parts[2])
        total, used = int(total), int(used)
        return {'index': idx, 'uuid': u, 'name': name, 'totalMiB': total, 'usedMiB': used, 'freeMiB': total - used,
                'utilization': int(util) if util.isdigit() else None, 'workerMiB': worker}
    return None


class ScanoWorker:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()            # one request at a time
        self._sink: Optional[Callable[[str], None]] = None
        self._recent: List[str] = []
        self.device_label: Optional[str] = None
        self.gpu: Optional[Dict] = None          # choose_gpu of the running worker
        self.last_memory: Optional[Dict] = None  # the worker's last "memory" reply

    def _stderr_pump(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            line = line.rstrip()
            self._recent = (self._recent + [line])[-200:]
            sink = self._sink
            if sink is not None and line:
                sink(line)

    def _ensure(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        gpu = choose_gpu()
        env = {**os.environ, 'PYTHONUNBUFFERED': '1', 'CUDA_VISIBLE_DEVICES': gpu['cuda_visible_devices'],
               'OMP_NUM_THREADS': os.environ.get('OMP_NUM_THREADS', '8'),
               'PYTHONPATH': os.pathsep.join([str(SRC)] + [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep)
                                                          if p])}
        self._proc = subprocess.Popen([sys.executable, '-m', 'backend.aircooling.scano.worker'], cwd=str(SRC),
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, bufsize=1, env=env, start_new_session=True)
        self.device_label = gpu['label']
        self.gpu = gpu
        threading.Thread(target=self._stderr_pump, args=(self._proc,), daemon=True).start()
        return self._proc

    def request(self, req: Dict, sink: Optional[Callable[[str], None]] = None) -> Dict:
        with self._lock:
            return self._request(req, sink)

    def _request(self, req: Dict, sink: Optional[Callable[[str], None]] = None) -> Dict:
        try:
            proc = self._ensure()
        except RuntimeError as exc:          # no GPU to start on (choose_gpu): say why and which ones would do
            return {'ok': False, 'error': str(exc)}
        self._sink = sink
        try:
            proc.stdin.write(json.dumps(req) + '\n')
            proc.stdin.flush()
            line = proc.stdout.readline()
        finally:
            self._sink = None
        if not line:
            code = proc.poll()
            self._proc = None
            return {'ok': False, 'error': f'SCANO worker exited (code {code})', 'stderr': self._recent[-20:]}
        return json.loads(line)

    def try_request(self, req: Dict) -> Optional[Dict]:
        """request() when the worker is running and idle, else None (never waits, never starts it)."""
        if self._proc is None or self._proc.poll() is not None or not self._lock.acquire(blocking=False):
            return None
        try:
            return self._request(req)
        finally:
            self._lock.release()

    def memory(self) -> Optional[Dict]:
        """The worker's view of its GPU memory (its "memory" command) when idle; the last one while it is busy."""
        reply = self.try_request({'cmd': 'memory'})
        if reply and reply.get('ok'):
            self.last_memory = reply
        return self.last_memory

    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc is not None and self._proc.poll() is None else None

    def status(self) -> Dict:
        alive = self._proc is not None and self._proc.poll() is None
        return {'running': alive, 'device': self.device_label if alive else None, 'busy': self._lock.locked(),
                'recent': self._recent[-10:]}

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            proc.stdin.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()


WORKER = ScanoWorker()


_GRAPHS: Dict[str, tuple] = {}


def room_graph(base: Path):
    """The joined mesh graph of a work directory (pipeline.load_graph), kept per process while graph.pkl is
    unchanged."""
    from backend.aircooling.scano import pipeline
    path = Path(base) / 'graph.pkl'
    sig = (path.stat().st_mtime_ns, path.stat().st_size)
    hit = _GRAPHS.get(str(base))
    if hit is None or hit[0] != sig:
        if len(_GRAPHS) > 2:
            _GRAPHS.clear()
        hit = _GRAPHS[str(base)] = (sig, pipeline.load_graph(base))
    return hit[1]


def loaded_scano_record(cpp) -> Dict:
    """The scano_prediction.json of the AI result post-processing has loaded, or {}."""
    run = getattr(cpp, 'run_dir', None)
    if run is None or getattr(cpp, '_internal', None) is None:
        return {}
    rec = Path(run) / 'scano_prediction.json'
    try:
        return json.loads(rec.read_text()) if rec.is_file() else {}
    except (OSError, ValueError):
        return {}


def loaded_scano_base(cpp) -> Optional[str]:
    """The work directory of the AI result post-processing has loaded (its scano_prediction.json), or None."""
    data = loaded_scano_record(cpp)
    if data.get('base'):
        return data['base']
    return str(Path(data['cache_root']).parent) if data.get('cache_root') else None   # records before 10-27 13:30


def inject(cpp, base: Path, pred_path: Path) -> Dict:
    """Replace the fields of the AI result post-processing holds with a new prediction of the same mesh (the
    graph's node order is the reader's: cells rank by rank, each patch's faces rank by rank; checked on DCTS_0112:
    every cell's nearest graph centroid is its own index). Returns the post-processing identity."""
    import numpy as np
    graph = room_graph(base)
    pred = np.load(pred_path, mmap_mode='r')
    n = graph.n_cells
    cells = np.asarray(pred[:n])
    faces = np.asarray(pred[n:])
    order = np.argsort(graph.patch, kind='stable')
    bounds = np.searchsorted(graph.patch[order], np.arange(len(graph.patches) + 1))
    boundary = {}
    for k, p in enumerate(graph.patches):
        rows = order[bounds[k]:bounds[k + 1]]
        if rows.size:
            f = faces[rows]
            boundary[p.name] = {'U': f[:, :3], 'p_rgh': f[:, 3], 'T': f[:, 4]}
    cpp.replace_fields({'U': cells[:, :3], 'p_rgh': cells[:, 3], 'T': cells[:, 4]}, boundary)
    return cpp.current_identity()
