"""Boundary fluxes of a DCTS run's whole-room prediction (c129; mock review round 7: reviewer 1 Q12, reviewer 2 M5e /
Q15): does the predicted field close the room's mass and energy balance?

For the run's primary deployment (as eval_dcts: the mesh cache in one pass for the decoupled runs, refine TTA at the
training ratio for the coupled ones) on the test rooms, and for the CFD truth, per patch of geom_type "patch" (supplies,
returns, server inlets and outlets): the volume flux sum_f (U_f . n_f) A_f and the enthalpy flux
rho cp sum_f (U_f . n_f)(T_f - T0) A_f over the patch's face nodes, with the cache's unit normals (normal.npy, outward)
and face areas, rho, cp and T0 = T_INIT of source.json; and the enthalpy flux of the predicted temperature carried
by the CFD's face fluxes (pred_h_at_truth_flux: the heat a cooling-unit estimate from the predicted return
temperatures gives when the flows are known). The checkpoint passes the canary as in eval_dcts first.
tools/r7_energy_flux.py reads the truth of every room from the cache; tools/r7_energy_pred.py tables these files.
    python -m scano.Main.eval_dcts_energy --run <run dir> [--output eval_energy_v1]
(eval_dcts --energy writes the same energy.npz beside its own outputs, from its primary deployment.)
Writes <run>/<output>/energy.npz (case_name, patch_name, role, truth_phi, truth_h, pred_phi, pred_h,
pred_h_at_truth_flux) and summary.json."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from ..Utils.run_config import canonical
from .cfg_common import (decoupled, model_for, open_data, predict, set_matmul, sha, source_record, tta, use_case,
                         validate)
from .dcts_common import refuse_smoke
from .eval_cfg import mode_key
from .eval_dcts import check_canary, patch_selection


def fluxes(data, field, select, normal, rho_cp, t0, flux_of=None):
    """([P] volume flux m^3/s, [P] enthalpy flux W, [P] enthalpy flux W at the face fluxes of `flux_of`) of the patches
    `select` of the current case; field and flux_of [N, 5] physical (flux_of None: the field's own fluxes)."""
    f = field.double()[data.n_cells:]
    a = data.face_area.double()
    phi = (f[:, :3] * normal).sum(1) * a
    phi_of = phi if flux_of is None else (flux_of.double()[data.n_cells:, :3] * normal).sum(1) * a
    pid = data.patch_ids.to(f.device)
    n_p = len(data.patch_table)
    sel = torch.as_tensor(select, dtype=torch.long, device=f.device)

    def per_patch(x):
        return torch.zeros(n_p, dtype=f.dtype, device=f.device).index_add_(0, pid, x)[sel].cpu().numpy()

    return per_patch(phi), per_patch(rho_cp * phi * (f[:, 4] - t0)), per_patch(rho_cp * phi_of * (f[:, 4] - t0))


ROWS = ('case_name', 'patch_name', 'role', 'truth_phi', 'truth_h', 'pred_phi', 'pred_h', 'pred_h_at_truth_flux')


def new_rows():
    return {k: [] for k in ROWS}


def case_rows(rows, data, truth, pred):
    """Append the current case's per-patch fluxes of `truth` and `pred` ([N, 5] physical) to `rows` (new_rows()).
    Constants from the case's source.json (dcts_source's when the cache has none), outward normals from normal.npy."""
    from ..Datasets import dcts_source as gs
    d = Path(data._cur().zkey)
    src = d / 'source.json'
    k = json.loads(src.read_text())['constants'] if src.is_file() else {'RHO': gs.RHO, 'CP': gs.CP, 'T_INIT': gs.T_INIT}
    rho_cp, t0 = k['RHO'] * k['CP'], k['T_INIT']
    normal = torch.tensor(np.load(d / 'normal.npy'), dtype=torch.float64, device=truth.device)
    select = patch_selection(data.patch_table)
    tv, th, _ = fluxes(data, truth, select, normal, rho_cp, t0)
    pv, ph, pht = fluxes(data, pred, select, normal, rho_cp, t0, flux_of=truth)
    for i in select:
        p = data.patch_table[i]
        rows['case_name'].append(data.case_name)
        rows['patch_name'].append(p['name'])
        rows['role'].append(p.get('role') or '')
    for key, v in (('truth_phi', tv), ('truth_h', th), ('pred_phi', pv), ('pred_h', ph), ('pred_h_at_truth_flux', pht)):
        rows[key].extend(v.tolist())


def evaluate(run, output='eval_energy_v1', max_cases=50, seed=261001, allow_uncommitted=False):
    src = source_record(['Main/eval_dcts_energy.py', 'Main/eval_dcts.py'])
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
        raise SystemExit('eval_dcts_energy evaluates dcts runs only')
    refuse_smoke(rec, False, f'eval_dcts_energy {run}')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    set_matmul(cfg)
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
    data, _, n_test = open_data(cfg, 'test', device, rec['stats'])
    cases = list(range(min(max_cases, n_test)))
    rng = np.random.default_rng(seed)
    s = cfg['sampling']
    dec = decoupled(cfg)
    primary = 'full' if dec else mode_key('tta', s['sample_ratio'], s['khop'])
    rows = new_rows()
    seconds = []
    with torch.no_grad():
        for c in cases:
            use_case(data, c)
            allnodes = torch.arange(data.n, device=device)
            truth = data.target(c, allnodes)
            t1 = time.perf_counter()
            if dec:
                pred = predict(inner, data, cfg, c, allnodes, full_context=True, rng=rng)
            else:
                pred, _ = tta(inner, data, cfg, c, s['sample_ratio'], s['khop'], rng)
            seconds.append(time.perf_counter() - t1)
            case_rows(rows, data, truth, pred)
    summary = {'run': run.name, 'fingerprint': rec['fingerprint'], 'describe': rec['describe'],
               'seed': cfg['train']['seed'], 'test_cases': len(cases), 'case_names': [data.names[c] for c in cases],
               'primary': primary, 'eval_seed': seed, 'canary': canary, 'deploy_seconds_mean': float(np.mean(seconds)),
               'normals': 'cache normal.npy (outward)', 'reference_temperature': 'T_INIT of source.json',
               'device': str(device), 'train_git_commit': rec['git_commit'], **src,
               'checkpoint_sha256': sha(run / 'checkpoint.pt'),
               'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    out.mkdir(exist_ok=False)
    np.savez(out / 'energy.npz', **{k: np.asarray(v) for k, v in rows.items()})
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--output', default='eval_energy_v1')
    ap.add_argument('--max-cases', type=int, default=50)
    ap.add_argument('--allow-uncommitted', action='store_true', help='smoke tests only')
    a = ap.parse_args()
    s = evaluate(a.run, a.output, a.max_cases, allow_uncommitted=a.allow_uncommitted)
    print(json.dumps({k: s[k] for k in ('run', 'primary', 'test_cases', 'deploy_seconds_mean')}))


if __name__ == '__main__':
    main()
