"""The SCANO forward on a DCTS cache case through the vendored code in src/backend/aircooling/scano/vendored (never edited here).

Two deployments (predict's mode):
  whole  one forward with every node as query and context, as in the paper's tables; its memory grows with the room
         (a DCTS room of 1.39 M nodes peaks at about 11 GB), so a room several times larger does not fit one card;
  tta    the paper's test-time aggregation (scano cfg_common.tta): the node pool in chunks, each chunk's k-hop
         subgraph predicted with fresh anchors drawn as in training (context.anchor_design), predictions averaged by
         how often each node was covered; `passes` repeats it over other permutations. A chunk holds as many pivots
         as one training draw on a training room (sampling.sample_ratio x TRAINING_NODES), not that ratio of this
         room, so every forward is the size the model was trained on and the memory no longer grows with the room.
Matrix products run in TF32 (DCDTUNI_SCANO_MATMUL=fp32 restores float32): faster with unchanged error
(docs/benchmarks/scano-ai-2026-10-27).
"""
from __future__ import annotations

import json
import os
import sys
import time
import types
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

SCANO_INFRA = Path(__file__).resolve().parent / 'vendored'   # holds the verbatim SCANO package (scano/)
CHECKPOINTS = Path(__file__).resolve().parent / 'checkpoints'
EVAL_SEED = 261001                     # the anchor-cap draw of dcts_timing_exclusive (unused without a cap)
TRAINING_NODES = 1_387_891             # nodes of a DCTS training room (DCTS_0112: 897361 cells + 490530 faces)
#: TTA anchors of a room larger than a training room are capped at a training room's count, down to this share of
#: the trained fraction: x1/10 left the four DCTS rooms' error within +0.002 (docs/benchmarks/scano-tta-2026-10-29).
MIN_ANCHOR_SCALE = 0.1


def anchor_scale_for(n_nodes: int) -> float:
    """The share of the trained anchor fraction TTA draws on a room of n_nodes (1 up to a training room)."""
    return max(MIN_ANCHOR_SCALE, min(1.0, TRAINING_NODES / max(1, int(n_nodes))))
MODES = ('whole', 'tta')


def import_scano():
    """The vendored ``scano`` package (SCANO_INFRA on sys.path; Python < 3.11 gets tomli or a stub as tomllib, which
    only run_config's TOML loader reads and inference never calls)."""
    if 'tomllib' not in sys.modules:
        try:
            import tomllib  # noqa: F401
        except ModuleNotFoundError:
            try:
                import tomli as tomllib_shim
            except ModuleNotFoundError:
                tomllib_shim = types.ModuleType('tomllib')

                def _no_toml(*_a, **_k):
                    raise RuntimeError('TOML variants are not read by the dcdtuni inference (needs Python >= 3.11)')
                tomllib_shim.load = tomllib_shim.loads = _no_toml
            sys.modules['tomllib'] = tomllib_shim
    path = str(SCANO_INFRA)
    if path not in sys.path:
        sys.path.insert(0, path)
    import scano
    if Path(scano.__file__).resolve().parent != (SCANO_INFRA / 'scano').resolve():
        raise ImportError(f'scano imported from {scano.__file__}, not the vendored copy in {SCANO_INFRA}')
    return scano


class _float32:
    """torch's default dtype float32 inside (the model and the vendored data class build tensors with it; a process
    that set float64, e.g. the liquid solver's tests, would get double weights or inputs)."""

    def __enter__(self):
        import torch
        self.saved = torch.get_default_dtype()
        torch.set_default_dtype(torch.float32)

    def __exit__(self, *exc):
        import torch
        torch.set_default_dtype(self.saved)
        return False


class ScanoModel:
    """One checkpoint on one device."""

    def __init__(self, run_dir: Path, device: Optional[str] = None):
        with _float32():
            self._init(run_dir, device)

    def _init(self, run_dir: Path, device: Optional[str]) -> None:
        import torch
        import_scano()
        from scano.Main.cfg_common import decoupled, model_for, set_matmul
        from scano.Utils.run_config import canonical
        t0 = time.perf_counter()
        self.run_dir = Path(run_dir)
        ck = torch.load(self.run_dir / 'checkpoint.pt', map_location='cpu', weights_only=False)
        self.record = ck['config']
        self.cfg = canonical(self.record['run_config'])
        if self.cfg['dataset']['name'] != 'dcts' or not decoupled(self.cfg):
            raise ValueError(f'{run_dir}: not a decoupled DCTS run (one whole-room forward is its deployment)')
        self.device = torch.device(device or ('cuda' if torch.cuda.is_available() else 'cpu'))
        set_matmul(self.cfg)
        self.matmul = os.environ.get('DCDTUNI_SCANO_MATMUL', 'tf32')
        if self.matmul not in ('tf32', 'fp32'):
            raise ValueError(f'DCDTUNI_SCANO_MATMUL={self.matmul!r} (tf32 or fp32)')
        torch.backends.cuda.matmul.allow_tf32 = self.matmul == 'tf32'
        torch.backends.cudnn.allow_tf32 = self.matmul == 'tf32'
        self.model = model_for(self.cfg).to(self.device)
        self.model.load_state_dict(ck['model'])
        self.model.eval()
        self.stats = self.record['stats']
        self.load_seconds = time.perf_counter() - t0

    def anchor_cfg(self, scale: float) -> Dict:
        """The run configuration with its anchor fraction (context.anchor_ratio, or sampling.sample_ratio when that is
        0) times `scale`: TTA's anchor cap (memory.py). A fixed anchor count (anchor_design other than "sample") is
        not a fraction and is left alone."""
        if scale >= 1.0 or (self.cfg.get('context') or {}).get('anchor_design') != 'sample':
            return self.cfg
        import copy
        cfg = copy.deepcopy(self.cfg)
        ratio = cfg['context'].get('anchor_ratio') or cfg['sampling']['sample_ratio']
        cfg['context']['anchor_ratio'] = float(ratio) * float(scale)
        return cfg

    def tta_chunk(self) -> int:
        """Pivots per TTA chunk: one training draw on a training room (module docstring)."""
        return max(1, int(TRAINING_NODES * float(self.cfg['sampling']['sample_ratio'])))

    def predict(self, *args, **kwargs) -> Dict:
        """_predict under float32 (see there)."""
        with _float32():
            return self._predict(*args, **kwargs)

    def _predict(self, cache_root: Path, case: str, with_error: bool = False, release_memory: bool = True,
                  mode: str = 'whole', passes: int = 1, chunk: Optional[int] = None,
                 progress: Optional[Callable[[str], None]] = None, anchor_scale: float = 1.0) -> Dict:
        """{'pred': [N, 5] float32 physical u, v, w, p_rgh, T at every node (cells, then boundary faces), 'timing',
        'mode', 'tta' ({'passes', 'chunk', 'forwards', 'min_count', 'max_rows'} in tta mode), 'err' (per-field
        relative L2 against the case's target.npy, when with_error)} of one cache case. The draws are seeded, so a
        request repeated gives the same prediction. release_memory (the default) gives the allocator's cached blocks
        back after the run: the worker shares its GPU, and keeping them saves nothing measurable (DCTS_0390, whole
        room: 1.02 s with, 1.05 s without, 2026-10-29)."""
        import torch
        from scano.Main.cfg_common import anchors_for, field_errors, predict, use_case
        from scano.Main.dcts_common import GtcData, data_options
        from scano.Datasets.refine_sampling import inference_pool
        if mode not in MODES:
            raise ValueError(f'mode {mode!r} (one of {MODES})')
        passes = max(1, int(passes))
        t0 = time.perf_counter()
        data = GtcData(Path(cache_root), [case], self.stats, self.device, provenance={'dcdtuni': 'inference'},
                       **data_options(self.cfg))
        sync = torch.cuda.synchronize if self.device.type == 'cuda' else (lambda: None)
        if self.device.type == 'cuda':
            torch.cuda.reset_peak_memory_stats(self.device)
        info, ok = None, False
        try:
            with torch.no_grad():
                use_case(data, 0)
                sync()
                t1 = time.perf_counter()
                nodes = torch.arange(data.n, device=self.device)
                if mode == 'whole':
                    pred = predict(self.model, data, self.cfg, 0, nodes, full_context=True,
                                   rng=np.random.default_rng(EVAL_SEED))
                else:
                    khop = int(self.cfg['sampling']['khop'])
                    size = int(chunk or self.tta_chunk())
                    cfg = self.anchor_cfg(anchor_scale)
                    total = torch.zeros((data.n, 5), device=self.device)
                    count = torch.zeros(data.n, device=self.device)
                    n_chunks = -(-data.n // size)
                    forwards, max_rows, step = 0, 0, max(1, n_chunks * passes // 10)
                    shrunk = 0
                    floor = max(1, size // 8)
                    expected = n_chunks * passes
                    for r in range(passes):
                        rng = np.random.default_rng(EVAL_SEED + r)
                        pool = inference_pool(r, data.n)
                        start = 0
                        while start < data.n:
                            sub = data.subgraph(pool[start:start + size].to(self.device), khop)
                            anchors = anchors_for(cfg, data, rng)
                            oom = False
                            try:
                                p = predict(self.model, data, cfg, 0, sub, anchors, rng=rng)
                            except torch.OutOfMemoryError:
                                if size <= floor:
                                    raise
                                oom = True
                            if oom:
                                # the estimate was short (memory.py): the rest of the room in smaller chunks (freed
                                # outside the handler, whose traceback held this forward's tensors)
                                del sub, anchors
                                torch.cuda.empty_cache()
                                size = max(floor, size // 2)
                                shrunk += 1
                                expected = forwards + -(-(data.n - start) // size) + (passes - 1 - r) * -(-data.n // size)
                                if progress is not None:
                                    progress(f'[SCANO] TTA chunk out of memory: continuing with {size} pivots per chunk')
                                continue
                            start += size
                            total.index_add_(0, sub, p.float())
                            count.index_add_(0, sub, torch.ones(sub.numel(), device=self.device))
                            forwards += 1
                            max_rows = max(max_rows, int(sub.numel()) + (int(anchors.numel()) if anchors is not None
                                                                        else 0))
                            if progress is not None and (forwards % step == 0 or forwards == expected):
                                progress(f'[SCANO] TTA {forwards}/{expected} forwards '
                                         f'({time.perf_counter() - t1:.1f} s)')
                    pred = total / count.clamp(min=1)[:, None]
                    info = {'passes': passes, 'chunk': size, 'forwards': forwards, 'khop': khop, 'anchor_scale': anchor_scale,
                            'chunk_shrunk': shrunk,
                            'min_count': int(count.min().item()), 'max_rows': max_rows}
                sync()
                t2 = time.perf_counter()
                out = {'pred': pred.float().cpu().numpy(), 'mode': mode, 'tta': info,
                       'timing': {'load_s': t1 - t0, 'forward_s': t2 - t1,
                                  'peak_gpu_bytes': int(torch.cuda.max_memory_allocated(self.device))
                                  if self.device.type == 'cuda' else None},
                       'n_nodes': int(data.n), 'n_cells': int(data.n_cells)}
                if with_error:
                    err = field_errors(data, pred, data.target(0, nodes), nodes)
                    out['err'] = [float(e) for e in err]
                    out['err_mean'] = float(np.mean(out['err']))
            ok = True
        finally:
            data.drop()
            del data
            # a failed forward (out of memory) leaves its blocks cached: give them back, whatever release_memory says
            if self.device.type == 'cuda' and (release_memory or not ok):
                torch.cuda.empty_cache()
        return out
