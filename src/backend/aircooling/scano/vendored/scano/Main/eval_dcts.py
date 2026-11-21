"""Test-set evaluation of DCTS runs (dataset dcts; docs/dcts_jupiter_design_2026-10-01.md): 'train on samples,
deploy on the whole mesh', the DCTS counterpart of eval_cfg (olbc) and eval_r0s (R0-S).

    PYTHONPATH=src python -m scano.Main.eval_dcts --run <case>/Logger/runs/<Variant>_s17 [--output eval_v1] \
        [--allow-smoke]
    EVAL_MODULE=scano.Main.eval_dcts EVAL_ONLY=1 bash cases/run_variants.sh <case> 17 <variant> ...

Deployments on the test cases of the cache's meta.json (case index = position in its test_cases):
  primary      the full-mesh prediction the result tables report, named in summary['primary']:
                 decoupled runs: `full`, the mesh cache (one pass, context = every cell);
                 coupled runs:   `tta@rho`, refine's inference at the training (rho, khop): the case's pivot pool in
                                 chunks of the training budget, a k-hop subgraph per chunk, predictions averaged by
                                 count (cfg_common.tta), as refine deployed its DCTS models;
  full         coupled runs only: one pass over the whole mesh with the whole mesh as context (reported, not primary);
  full_fresh   decoupled runs with --fresh-full only (c129, mock review round 7): one pass over the whole mesh
               whose state comes from one fresh draw of the training anchor count instead of every cell (AB-UPT's
               linear-cost deployment); its own generator, so the other modes draw as without it;
  scatter@rho  ONE subgraph at the training (rho, khop): the draw-th chunk of refine's inference pool;
  box@rho      ONE subgraph of refine's ROI box draw at the same budget (epoch = draw).
  <kind>@rho+halo<h>  model.message_passing runs only (c76): the same single subgraph read off a prediction on the
               subgraph and its h-hop mesh halo (h = 3 x message_passing x depth, zoo/fvgn_gn.py; gn_common.gn_halo),
               so the GN neighbourhoods of its nodes are complete; anchors from a generator seeded by (seed, case,
               draw, kind), so the regular modes draw exactly as without it.
The single-subgraph draws 0..D-1 depend only on the case and the draw (fixed); decoupled runs read fresh random
anchors per draw (the training distribution) from the evaluation seed, and context.anchor_cap (abupt) draws from it
too. A mode whose khop is not 2 carries it in its name (eval_cfg.mode_key).

Per mode and case (results[mode]):
  err_<f>, err_mean    cfg_common.field_errors: volume-weighted over the cells of the mode's nodes, normalised by the
                       truth's variation about the training mean (boundary-face nodes have no volume);
  rmse_<f>, mae_<f>    the same cells, volume-weighted root-mean-square and mean absolute error in PHYSICAL units
                       (predictions de-normalised with the train-only statistics): u, v, w in m/s, T in K, p_rgh in the
                       file's unit, m^2/s^2: the solver is Boussinesq (transportProperties nu, beta, TRef), so p_rgh is
                       the kinematic pressure (Pa after multiplying by a reference density); summary['units'];
  d_rep_<f>, d_rep_mean  ||p - primary||_w / ||y - mu||_w on the same nodes, the distance to the model's own primary
                       prediction in the unit of err (||p - primary|| / ||primary|| would be dominated by T near 300 K);
  coverage, cell_coverage  single subgraphs; mean_count  TTA; seconds, and peak GPU memory per mode.
Per-patch outputs (per_patch.npz; data for the engineering decision task, no task metric here): for every non-empty
patch of geom_type "patch" (server inlets / outlets, CRAC supply / return; role from the cache's case.json), the
area-weighted means of T, |U| (the mean of the speed, not the speed of the mean velocity) and p_rgh, for the CFD
truth and for the full-mesh predictions (primary, and the coupled one-pass full), computed two ways: over the patch's
boundary-face nodes (`face`: the targets there are the converged boundary values of the solver,
e.g. the fixed supply temperature, the zero-gradient value at a server inlet) and over the owner cells of those faces
(`owner`, via cpd|neighbor_cell). Rows: case index, case name, patch name, role, number of faces, area. Decision d57
(10-08): the engineering tables read the server-inlet temperatures on the owner cells (zero-gradient: the CFD face and
owner values agree to a few mK; Analysis.dcts_hot_inlets), the face values alongside; both are written here as before.
Timing of the primary deployment, per case (summary['timing'], CUDA-synchronised): load = use_case of the case from the
cache (np.load, host-to-device, feature and record assembly); prepare = from the start of the deployment to the first
model forward (context weights, node features, patch records, the first TTA subgraph); forward = the model forwards
(summed over TTA chunks); between = the rest of a TTA pass between forwards (subgraphs, accumulation); finalize = after
the last forward (de-normalisation, TTA division); the three-way split preprocess = load + prepare + between, forward,
postprocess = finalize. metrics_s (errors and patch means) is evaluation, not inference; the coupled one-pass `full`
deployment in between is timed apart (results['full']['seconds']) and left out of it.
Memory: every full-mesh forward passes the whole mesh (about 1.4M nodes) through the backbone at once. The slice
backbones (slice, transolver, sca, geotransolver) and gnot cannot be split over the points without changing them
(each layer's global state is a weighted mean over all points, which the next layer reads); abupt's queries are
independent given its anchors (capped by context.anchor_cap) and the per-record channel and LFE attention are split
over the queries when large (NNmodels/attention.sdpa). The measured peaks are in the design document (section 3.2).
A canary recomputes the last training validation (tolerance 2%, every scalar and every per-field value). Evaluations
are never overwritten: the output directory is created only after everything succeeded, and summary.json is written
last. A run trained in smoke mode (SCANO_DCTS_SMOKE=1; its config's data_provenance) is refused, as is smoke mode itself,
unless --allow-smoke (dcts_common.refuse_smoke).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from ..Datasets.dcts import FIELDS
from ..Utils.run_config import canonical
from .cfg_common import (_stat, anchors_for, decoupled, field_errors, model_for, open_data, predict, set_matmul, sha,
                         source_record, tta, use_case, validate, jump_record)
from .eval_cfg import mode_key, summarize
from .gn_common import gn_halo
from .dcts_common import refuse_smoke
from .olbc_common import single_subgraph

KINDS = ('scatter', 'box')
ALL_KINDS = ('scatter', 'box', 'roi')         # 'roi': compact regions (c112 amendment 2)
CANARY_TOL = 0.02
UNITS = {'u': 'm/s', 'v': 'm/s', 'w': 'm/s', 'T': 'K',
         'p_rgh': 'm^2/s^2 (kinematic p_rgh of the Boussinesq solver; x reference density for Pa)'}
PATCH_QUANTITIES = ('T', 'speed', 'p_rgh')          # speed = |U|
TIMING = ('load_s', 'prepare_s', 'forward_s', 'between_s', 'finalize_s', 'total_s', 'preprocess_s', 'postprocess_s',
          'metrics_s')


class TimedModel(torch.nn.Module):
    """The model with every forward timed (CUDA-synchronised before and after); calls = [(start, end), ...]."""

    def __init__(self, inner, device):
        super().__init__()
        self.inner, self.device = inner, device
        self.needs_edges = getattr(inner, 'needs_edges', False)
        self.needs_record_area = getattr(inner, 'needs_record_area', False)
        self.message_passing = getattr(inner, 'message_passing', 0)     # c76: GN blocks (cfg_common._forward reads it)
        self.message_passing_hops = getattr(inner, 'message_passing_hops', 0)
        self.server_route = getattr(inner, 'server_route', False)     # B2: cfg_common._forward passes the route rows
        self.calls = []

    def sync(self):
        if self.device.type == 'cuda':
            torch.cuda.synchronize()

    def forward(self, *args, **kwargs):
        self.sync()
        t0 = time.perf_counter()
        out = self.inner(*args, **kwargs)
        self.sync()
        self.calls.append((t0, time.perf_counter()))
        return out


def field_drep(data, pred, ref_pred, truth, nodes):
    """[F] distances ||pred - ref_pred||_w / ||truth - mu||_w per field, w = the cell measure on `nodes` (the
    denominator of field_errors)."""
    w = data.area[nodes].double()
    mu = _stat(data.mean, truth.double())
    num = torch.sqrt((w[:, None] * (pred.double() - ref_pred.double()) ** 2).sum(0))
    den = torch.sqrt((w[:, None] * (truth.double() - mu) ** 2).sum(0))
    return (num / den).cpu().numpy()


def physical_errors(data, pred, truth, nodes):
    """([F] RMSE, [F] MAE) in physical units over the cells of `nodes`, volume-weighted."""
    w = data.area[nodes].double()
    d = pred.double() - truth.double()
    ws = w.sum()
    rmse = torch.sqrt((w[:, None] * d ** 2).sum(0) / ws)
    mae = (w[:, None] * d.abs()).sum(0) / ws
    return rmse.cpu().numpy(), mae.cpu().numpy()


def patch_selection(table):
    """Indices (patch index order) of the non-empty patches of geom_type "patch"."""
    return [i for i, p in enumerate(table) if p['geom_type'] == 'patch' and p['n_faces'] > 0]


def patch_means(data, field, select):
    """([P, 3] face means, [P, 3] owner-cell means, [P] areas) of T, |U|, p_rgh for the patches `select` of the current
    case; `field` [N, 5] on the whole mesh (physical units)."""
    f = field.double()
    q = torch.stack([f[:, 4], torch.linalg.vector_norm(f[:, :3], dim=1), f[:, 3]], 1)
    ncell = data.n_cells
    w = data.face_area.double()
    pid = data.patch_ids.to(f.device)
    n_p = len(data.patch_table)
    wsum = torch.zeros(n_p, dtype=f.dtype, device=f.device).index_add_(0, pid, w)
    face = torch.zeros(n_p, 3, dtype=f.dtype, device=f.device).index_add_(0, pid, q[ncell:] * w[:, None])
    own = torch.zeros(n_p, 3, dtype=f.dtype, device=f.device).index_add_(0, pid, q[data.owner_cells] * w[:, None])
    sel = torch.as_tensor(select, dtype=torch.long, device=f.device)
    return ((face[sel] / wsum[sel, None]).cpu().numpy(), (own[sel] / wsum[sel, None]).cpu().numpy(),
            wsum[sel].cpu().numpy())


def record_jump(r, mode, rec):
    """boundary.server_jump runs: the fixed-point record of one deployment (cfg_common.jump_record) as per-case
    values of `mode`: jump_passes, jump_last_change_K, jump_converged (1 / 0). Nothing for every other run."""
    if rec is None:
        return
    r.add(mode, 'jump_passes', rec['passes'])
    r.add(mode, 'jump_last_change_K', rec['last_change_K'])
    r.add(mode, 'jump_converged', float(rec['converged']))


def jump_summary(cfg, acc):
    """summary['server_jump']: None for runs without boundary.server_jump; else the cap, the tolerance and per mode the
    per-case passes and last changes and the number of deployments that hit the cap with the change still >= tol."""
    from .cfg_common import setting
    if setting(cfg, 'boundary', 'server_jump') == 'none':
        return None
    out = {'cap': setting(cfg, 'boundary', 'server_jump_passes'), 'tol_K': setting(cfg, 'boundary', 'server_jump_tol'),
           'modes': {}}
    for m, d in acc.items():
        if 'jump_passes' in d:
            out['modes'][m] = {'passes': d['jump_passes'], 'last_change_K': d['jump_last_change_K'],
                               'capped_unconverged': int(sum(1 for x in d['jump_converged'] if x == 0.0))}
    return out


def check_canary(canary, recorded, tol=CANARY_TOL):
    """Every scalar and every per-field value of the recomputed validation within tol (relative) of the recorded."""
    bad = []
    for k, v in canary.items():
        ref = recorded[k]
        pairs = zip(v, ref) if isinstance(v, (list, tuple)) else [(v, ref)]
        for i, (a, b) in enumerate(pairs):
            if abs(a - b) > tol * max(abs(b), 1e-9):
                bad.append(f'{k}[{i}]: {a} vs recorded {b}')
    if bad:
        raise AssertionError('canary: ' + '; '.join(bad))


class Recorder:
    def __init__(self, device):
        self.acc, self.peak, self.device = {}, {}, device

    def add(self, mode, key, value):
        self.acc.setdefault(mode, {}).setdefault(key, []).append(float(value))

    def fields(self, mode, prefix, values, mean=True):
        for f, v in zip(FIELDS, values):
            self.add(mode, f'{prefix}_{f}', v)
        if mean:
            self.add(mode, f'{prefix}_mean', float(np.mean(values)))

    def scores(self, data, mode, pred, truth, nodes, ref=None):
        self.fields(mode, 'err', field_errors(data, pred, truth, nodes))
        rmse, mae = physical_errors(data, pred, truth, nodes)
        self.fields(mode, 'rmse', rmse, mean=False)
        self.fields(mode, 'mae', mae, mean=False)
        if ref is not None:
            self.fields(mode, 'd_rep', field_drep(data, pred, ref, truth, nodes))

    def start(self):
        if self.device.type == 'cuda':
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
        return time.perf_counter()

    def stop(self, mode, t0):
        if self.device.type == 'cuda':
            torch.cuda.synchronize()
            self.peak[mode] = max(self.peak.get(mode, 0), torch.cuda.max_memory_allocated())
        t = time.perf_counter() - t0
        self.add(mode, 'seconds', t)
        return t


def evaluate(run, output='eval_v1', draws=2, max_cases=50, seed=261001, allow_uncommitted=False, allow_smoke=False,
             fresh_full=False, energy=False):
    src = source_record(['Main/eval_dcts.py'] + (['Main/eval_dcts_energy.py'] if energy else []))
    if src['sources_uncommitted'] and not allow_uncommitted:
        raise SystemExit(f"uncommitted sources: {src['sources_uncommitted']} (commit first, or --allow-uncommitted)")
    run = Path(run)
    out = run / output
    if out.exists():
        raise SystemExit(f'{out} exists (evaluations are never overwritten)')
    ck = torch.load(run / 'checkpoint.pt', map_location='cpu', weights_only=False)
    rec = ck['config']
    cfg = canonical(rec['run_config'])
    ds = cfg['dataset']
    if ds['name'] != 'dcts':
        raise SystemExit(f"eval_dcts evaluates dcts runs only; {ds['name']} runs: eval_cfg (olbc) or eval_r0s (r0s)")
    refuse_smoke(rec, allow_smoke, f'eval_dcts {run}')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    set_matmul(cfg)                    # train.matmul of the run (fp32 = TF32 disabled, as always)
    inner = model_for(cfg).to(device)
    inner.load_state_dict(ck['model'])
    inner.eval()

    tdata, _, _ = open_data(cfg, 'train', device, rec['stats'])
    val_cases = np.arange(rec['n_train'], rec['n_train'] + min(ds['val_cases'], ds['val_count']))
    canary = validate(inner, tdata, cfg, val_cases)
    check_canary(canary, json.loads((run / 'status.json').read_text())['final_val'])
    del tdata
    if device.type == 'cuda':
        torch.cuda.empty_cache()

    model = TimedModel(inner, device).eval()
    data, _, n_test = open_data(cfg, 'test', device, rec['stats'])
    cases = list(range(min(max_cases, n_test)))
    rng = np.random.default_rng(seed)
    s = cfg['sampling']
    rho, khop = s['sample_ratio'], s['khop']
    dec = decoupled(cfg)
    primary = 'full' if dec else mode_key('tta', rho, khop)
    patch_modes = [primary] + ([] if dec else ['full'])
    halo = getattr(inner, 'message_passing_hops', 0)               # c76: the GN path's hops (0 = no halo modes)
    r = Recorder(device)
    timing = {k: [] for k in TIMING}
    rows = {k: [] for k in ('case_index', 'case_name', 'patch_name', 'role', 'n_faces', 'area', 'truth_face',
                            'truth_owner')}
    for m in patch_modes:
        rows[f'{m}__pred_face'], rows[f'{m}__pred_owner'] = [], []
    if energy:                                 # c129 G5: boundary fluxes of the primary prediction (eval_dcts_energy)
        from .eval_dcts_energy import case_rows, new_rows
        erows = new_rows()
    with torch.no_grad():
        for c in cases:
            data.drop()                                  # the load is timed: nothing of this case is cached
            t0 = r.start()
            use_case(data, c)
            load = r.stop('_load', t0)
            allnodes = torch.arange(data.n, device=device)
            truth = data.target(c, allnodes)
            model.calls = []
            t0 = r.start()
            if dec:
                pred = predict(model, data, cfg, c, allnodes, full_context=True, rng=rng)
            else:
                pred, count = tta(model, data, cfg, c, rho, khop, rng)
                r.add(primary, 'mean_count', float(count.mean()))
            total = r.stop(primary, t0)
            if dec:                                  # B1: the fixed point of the primary deployment, per case
                record_jump(r, primary, jump_record(data, cfg))
            t_end = t0 + total
            fwd = sum(b - a for a, b in model.calls)
            prepare, finalize = model.calls[0][0] - t0, t_end - model.calls[-1][1]
            between = total - prepare - finalize - fwd
            tm = time.perf_counter()
            r.scores(data, primary, pred, truth, allnodes)
            full_preds = {primary: pred}
            t_full = 0.0                       # the coupled one-pass deployment: results['full'], not metrics_s
            if not dec:
                t1 = r.start()
                one = predict(model, data, cfg, c, allnodes, rng=rng)
                t_full = r.stop('full', t1)
                r.scores(data, 'full', one, truth, allnodes, ref=pred)
                full_preds['full'] = one
            if dec and fresh_full:             # c129 (R7): the whole mesh with the state of one fresh anchor draw of the
                t1 = r.start()                 # training count, from its own generator (the other modes draw unchanged)
                one = predict(model, data, cfg, c, allnodes, anchors_for(cfg, data, np.random.default_rng([seed, c, 29])),
                              rng=np.random.default_rng([seed, c, 30]))
                t_full += r.stop('full_fresh', t1)
                r.scores(data, 'full_fresh', one, truth, allnodes, ref=pred)
            select = patch_selection(data.patch_table)
            tf, to, area = patch_means(data, truth, select)
            for i, k in zip(select, range(len(select))):
                p = data.patch_table[i]
                rows['case_index'].append(c)
                rows['case_name'].append(data.case_name)
                rows['patch_name'].append(p['name'])
                rows['role'].append(p.get('role') or '')
                rows['n_faces'].append(p['n_faces'])
                rows['area'].append(area[k])
            rows['truth_face'].append(tf)
            rows['truth_owner'].append(to)
            for m in patch_modes:
                pf, po, _ = patch_means(data, full_preds[m], select)
                rows[f'{m}__pred_face'].append(pf)
                rows[f'{m}__pred_owner'].append(po)
            if device.type == 'cuda':
                torch.cuda.synchronize()
            for key, v in (('load_s', load), ('prepare_s', prepare), ('forward_s', fwd), ('between_s', between),
                           ('finalize_s', finalize), ('total_s', load + total),
                           ('preprocess_s', load + prepare + between), ('postprocess_s', finalize),
                           ('metrics_s', time.perf_counter() - tm - t_full)):
                timing[key].append(v)
            if energy:                         # after the timing, so that no timed figure includes it
                case_rows(erows, data, truth, pred)
            for kind in KINDS:
                key = mode_key(kind, rho, khop)
                hkey = f'{key}+halo{halo}'
                for d in range(draws):
                    nodes = single_subgraph(data, c, kind, rho, khop, d)
                    t0 = r.start()
                    p = predict(model, data, cfg, c, nodes, anchors_for(cfg, data, rng), rng=rng)
                    record_jump(r, key, jump_record(data, cfg))
                    r.stop(key, t0)
                    r.scores(data, key, p, truth[nodes], nodes, ref=pred[nodes])
                    r.add(key, 'coverage', nodes.numel() / data.n)
                    r.add(key, 'cell_coverage', float((nodes < data.n_cells).sum()) / data.n_cells)
                    if halo:                     # c76: the same subgraph read off a prediction on its GN halo
                        hn = gn_halo(data, nodes, halo)
                        at = torch.searchsorted(hn, nodes)
                        hrng = np.random.default_rng([seed, c, d, KINDS.index(kind)])
                        t0 = r.start()
                        p = predict(model, data, cfg, c, hn, anchors_for(cfg, data, hrng), rng=hrng)[at]
                        record_jump(r, hkey, jump_record(data, cfg))
                        r.stop(hkey, t0)
                        r.scores(data, hkey, p, truth[nodes], nodes, ref=pred[nodes])
                        r.add(hkey, 'coverage', nodes.numel() / data.n)
                        r.add(hkey, 'halo_coverage', hn.numel() / data.n)

    acc = {m: d for m, d in r.acc.items() if not m.startswith('_')}
    results = {m: {k: summarize(v) for k, v in d.items()} for m, d in acc.items()}
    for m, b in r.peak.items():
        if m in results:
            results[m]['peak_gpu_bytes'] = int(b)
    summary = {'run': run.name, 'fingerprint': rec['fingerprint'], 'describe': rec['describe'],
               'seed': cfg['train']['seed'], 'parameters': rec['parameters'], 'test_cases': len(cases),
               'case_names': [data.names[c] for c in cases], 'draws': draws, 'eval_seed': seed,
               'fresh_full': bool(fresh_full and dec),
               **({'energy': {'file': 'energy.npz', 'mode': primary}} if energy else {}),
               'primary': primary, 'primary_kind': 'mesh cache (decoupled, one pass)' if dec else
               f'refine TTA at the training rho {rho:g}, khop {khop} (coupled)',
               'fields': list(FIELDS), 'units': UNITS, 'results': results,
               'timing': {'primary': primary, **{k: summarize(v) for k, v in timing.items()},
                          'device': str(device), 'gpu': torch.cuda.get_device_name(0) if device.type == 'cuda' else None},
               'per_patch': {'file': 'per_patch.npz', 'quantities': list(PATCH_QUANTITIES), 'modes': patch_modes,
                             'rows': len(rows['patch_name']), 'selection': 'non-empty patches of geom_type "patch"',
                             'primary_location': 'owner = the owner cells of the faces (d57, the engineering tables); '
                                                 'face = the boundary-face nodes, reported alongside'},
               'canary': canary, 'data_provenance': getattr(data, 'provenance', None), 'device': str(device),
               'gpu': torch.cuda.get_device_name(0) if device.type == 'cuda' else None,
               **({'server_jump': jump_summary(cfg, acc)} if jump_summary(cfg, acc) else {}),   # B1 runs only
               'train_git_commit': rec['git_commit'], **src, 'checkpoint_sha256': sha(run / 'checkpoint.pt'),
               'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    patch_arrays = {k: (np.concatenate(v) if k.endswith(('_face', '_owner')) else np.asarray(v))
                    for k, v in rows.items()}
    patch_arrays['quantities'] = np.asarray(PATCH_QUANTITIES)
    out.mkdir(exist_ok=False)          # only now, so a failed evaluation leaves nothing that blocks a retry
    np.savez(out / 'per_case.npz', case_names=np.asarray(summary['case_names']),
             **{f'{m}__{k}'.replace('@', '_at_'): np.asarray(v) for m, d in acc.items() for k, v in d.items()},
             **{f'timing__{k}': np.asarray(v) for k, v in timing.items()})
    np.savez(out / 'per_patch.npz', **{k.replace('@', '_at_'): v for k, v in patch_arrays.items()})
    if energy:
        np.savez(out / 'energy.npz', **{k: np.asarray(v) for k, v in erows.items()})
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')     # last: its presence = complete
    return summary


COVERAGE_RATIOS = (0.005, 0.01, 0.02, 0.05, 0.1, 0.2)   # c101: the sampling ratios of the coverage sweep


def evaluate_coverage(run, output='eval_cov_v1', ratios=COVERAGE_RATIOS, draws=2, max_cases=50, seed=261001,
                      allow_uncommitted=False, allow_smoke=False, halo=False, primary_on_pass=False, kinds=KINDS):
    """c101 (owner request of 10-11: error against the sampling rate on the million-cell rooms): the primary
    deployment of `evaluate` and, for every ratio in `ratios`, ONE scattered subgraph and ONE region-of-interest box
    at that ratio and the training khop (draws per kind and case; the same single_subgraph as the scatter@rho /
    box@rho modes of `evaluate`, which are the training-ratio point of this sweep), with err_<f>, d_rep_<f> against the
    model's own primary prediction, coverage and cell_coverage per mode. Decoupled runs read fresh anchors per draw.
    No per-patch outputs and no timing split; the canary and the never-overwrite rule are those of `evaluate`.
    halo=True (c101 amendment of 10-11, eval_cov_v2): for message-passing runs also the halo twin <mode>+halo<h> of
    every pass, read off a prediction on the pass and its h-hop mesh halo, as `evaluate` does (c76).
    primary_on_pass=True (c112, owner request of 10-13: the error of a region pass beside the error of the whole-mesh
    prediction on the same cells): for every pass also <mode>+primary, the model's own primary prediction read on the
    pass's nodes (err_<f>, rmse_<f>, mae_<f> on the same cells; no new forward), so that region and whole mesh are
    compared on identical cells. kinds (c112 amendment 2): the kinds of single pass, 'scatter' and 'box' by default,
    'roi' the compact regions of olbc_common.compact_region (the share of the cells inside one box, no growth)."""
    src = source_record(['Main/eval_dcts.py'])
    if src['sources_uncommitted'] and not allow_uncommitted:
        raise SystemExit(f"uncommitted sources: {src['sources_uncommitted']} (commit first, or --allow-uncommitted)")
    run = Path(run)
    out = run / output
    if out.exists():
        raise SystemExit(f'{out} exists (evaluations are never overwritten)')
    ck = torch.load(run / 'checkpoint.pt', map_location='cpu', weights_only=False)
    rec = ck['config']
    cfg = canonical(rec['run_config'])
    ds = cfg['dataset']
    if ds['name'] != 'dcts':
        raise SystemExit(f"eval_dcts evaluates dcts runs only; {ds['name']} runs: eval_cfg (olbc) or eval_r0s (r0s)")
    refuse_smoke(rec, allow_smoke, f'eval_dcts {run}')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    set_matmul(cfg)
    model = model_for(cfg).to(device)
    model.load_state_dict(ck['model'])
    model.eval()
    tdata, _, _ = open_data(cfg, 'train', device, rec['stats'])
    val_cases = np.arange(rec['n_train'], rec['n_train'] + min(ds['val_cases'], ds['val_count']))
    canary = validate(model, tdata, cfg, val_cases)
    check_canary(canary, json.loads((run / 'status.json').read_text())['final_val'])
    del tdata
    if device.type == 'cuda':
        torch.cuda.empty_cache()
    data, _, n_test = open_data(cfg, 'test', device, rec['stats'])
    cases = list(range(min(max_cases, n_test)))
    rng = np.random.default_rng(seed)
    s = cfg['sampling']
    rho, khop = s['sample_ratio'], s['khop']
    dec = decoupled(cfg)
    primary = 'full' if dec else mode_key('tta', rho, khop)
    ratios = sorted({float(x) for x in ratios})
    halo = getattr(model, 'message_passing_hops', 0) if halo else 0     # c101 amendment: GN runs, halo twins
    r = Recorder(device)
    with torch.no_grad():
        for c in cases:
            use_case(data, c)
            allnodes = torch.arange(data.n, device=device)
            truth = data.target(c, allnodes)
            if dec:
                pred = predict(model, data, cfg, c, allnodes, full_context=True, rng=rng)
            else:
                pred, count = tta(model, data, cfg, c, rho, khop, rng)
                r.add(primary, 'mean_count', float(count.mean()))
            r.scores(data, primary, pred, truth, allnodes)
            for q in ratios:
                for kind in kinds:
                    key = mode_key(kind, q, khop)
                    for d in range(draws):
                        nodes = single_subgraph(data, c, kind, q, khop, d)
                        p = predict(model, data, cfg, c, nodes, anchors_for(cfg, data, rng), rng=rng)
                        r.scores(data, key, p, truth[nodes], nodes, ref=pred[nodes])
                        r.add(key, 'coverage', nodes.numel() / data.n)
                        r.add(key, 'cell_coverage', float((nodes < data.n_cells).sum()) / data.n_cells)
                        if primary_on_pass:      # c112: the whole-mesh (primary) prediction on the same cells
                            r.scores(data, f'{key}+primary', pred[nodes], truth[nodes], nodes)
                        if halo:                 # the same pass read off a prediction on its GN halo (c76 rule)
                            hkey = f'{key}+halo{halo}'
                            hn = gn_halo(data, nodes, halo)
                            at = torch.searchsorted(hn, nodes)
                            hrng = np.random.default_rng([seed, c, d, ALL_KINDS.index(kind), int(round(q * 1e4))])
                            p = predict(model, data, cfg, c, hn, anchors_for(cfg, data, hrng), rng=hrng)[at]
                            r.scores(data, hkey, p, truth[nodes], nodes, ref=pred[nodes])
                            r.add(hkey, 'coverage', nodes.numel() / data.n)
                            r.add(hkey, 'halo_coverage', hn.numel() / data.n)
    acc = {m: d for m, d in r.acc.items() if not m.startswith('_')}
    results = {m: {k: summarize(v) for k, v in d.items()} for m, d in acc.items()}
    summary = {'run': run.name, 'fingerprint': rec['fingerprint'], 'describe': rec['describe'],
               'seed': cfg['train']['seed'], 'parameters': rec['parameters'], 'test_cases': len(cases),
               'case_names': [data.names[c] for c in cases], 'draws': draws, 'eval_seed': seed, 'ratios': ratios,
               'training_ratio': rho, 'khop': khop, 'primary': primary, 'halo': halo, 'fields': list(FIELDS),
               'results': results,
               'canary': canary, 'data_provenance': getattr(data, 'provenance', None), 'device': str(device),
               'gpu': torch.cuda.get_device_name(0) if device.type == 'cuda' else None,
               'train_git_commit': rec['git_commit'], **src, 'checkpoint_sha256': sha(run / 'checkpoint.pt'),
               'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    out.mkdir(exist_ok=False)
    np.savez(out / 'per_case.npz', case_names=np.asarray(summary['case_names']),
             **{f'{m}__{k}'.replace('@', '_at_'): np.asarray(v) for m, d in acc.items() for k, v in d.items()})
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')     # last: its presence = complete
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--output', default='eval_v1')
    ap.add_argument('--draws', type=int, default=2, help='single-subgraph draws per kind and case')
    ap.add_argument('--max-cases', type=int, default=50)
    ap.add_argument('--seed', type=int, default=261001)
    ap.add_argument('--allow-uncommitted', action='store_true', help='smoke tests only')
    ap.add_argument('--allow-smoke', action='store_true',
                    help='evaluate a run trained in smoke mode (development only, never citable)')
    ap.add_argument('--halo', action='store_true', help='with --ratios: also the GN halo twins (c101 amendment)')
    ap.add_argument('--primary-on-pass', action='store_true',
                    help='with --ratios: also the primary prediction read on every pass (c112)')
    ap.add_argument('--kinds', nargs='+', choices=ALL_KINDS, default=list(KINDS),
                    help='with --ratios: the kinds of single pass (roi: compact regions, c112 amendment 2)')
    ap.add_argument('--fresh-full', action='store_true',
                    help='decoupled runs: also full_fresh, the whole mesh with the state of one fresh draw of the '
                         'training anchor count (c129; d_rep against the primary mesh-cache pass)')
    ap.add_argument('--energy', action='store_true',
                    help='also energy.npz: per-patch volume and enthalpy fluxes of the primary prediction and the truth '
                         '(c129 G5; eval_dcts_energy)')
    ap.add_argument('--ratios', type=float, nargs='+', default=None,
                    help='c101 coverage sweep (evaluate_coverage) at these sampling ratios instead of the eval_v1 '
                         'deployments; output default eval_cov_v1')
    args = ap.parse_args()
    if args.ratios:
        out = args.output if args.output != 'eval_v1' else 'eval_cov_v1'
        summary = evaluate_coverage(args.run, out, args.ratios, args.draws, args.max_cases, args.seed,
                                    args.allow_uncommitted, args.allow_smoke, args.halo, args.primary_on_pass,
                                    tuple(args.kinds))
        print(json.dumps({m: {k: round(v['mean'], 4) for k, v in d.items() if isinstance(v, dict)
                              and k in ('err_mean', 'd_rep_mean', 'coverage')} for m, d in summary['results'].items()}))
        return
    summary = evaluate(args.run, args.output, args.draws, args.max_cases, args.seed, args.allow_uncommitted,
                       args.allow_smoke, args.fresh_full, args.energy)
    print(json.dumps({m: {k: round(v['mean'], 4) for k, v in d.items() if isinstance(v, dict) and k.endswith('_mean')}
                      for m, d in summary['results'].items()}))
    t = summary['timing']
    print(json.dumps({k: round(t[k]['mean'], 3) for k in TIMING}))


if __name__ == '__main__':
    main()
