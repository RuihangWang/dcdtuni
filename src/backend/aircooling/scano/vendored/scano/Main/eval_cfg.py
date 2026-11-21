"""Evaluation of a config-driven run: 'train on samples, deploy any way' (the c37 protocol, eval_olbc).

Deployment modes on the test split (case index = position in it):
  full             every node in one pass (decoupled: context = every node, the mesh cache);
  full_anchor      decoupled only: every node, context = the configured number of random anchors;
  tta@rho          refine's inference protocol at sample ratio rho (chunks of the pivot pool, k-hop subgraphs,
                   predictions averaged by count);
  scatter@rho      ONE subgraph: the draw-th chunk of that pool, no averaging;
  box@rho          ONE subgraph from refine's ROI box draw at the same budget (epoch = draw).
  roi@q            (c121, --roi-shares; then only these and full / full_anchor) a compact region of interest holding
                   the share q of the points: every point inside one axis-aligned box, nothing outside, no growth
                   (olbc_common.compact_region, as DCTS's regions); decoupled runs read fresh anchors; rel_l2_full =
                   the error of the `full` prediction on the same nodes (the region / whole-mesh ratio of c121).
  <kind>@rho+halo<h>  model.message_passing runs with gn_context = "subgraph" only (d54): the same single subgraph and
                   the same anchors, read off a prediction on the subgraph and its h-hop mesh halo (h = 3 x m x depth,
                   gn_common.gn_halo; cfg_common.predict_halo), so the GN neighbourhoods of its nodes are complete and
                   its D_rep is comparable with the no-GN twin's; d_trunc = relative distance between the prediction
                   without and with the halo on the subgraph's nodes (the GN truncation at the subgraph border). No
                   extra draw from the evaluation generator: every other mode draws exactly as without it.
The fixed grid (rho 0.05, 0.2 and TTA 0.5) always uses FIXED_KHOP = 2, so a mode name means the same deployment
for every run; when training used another sample ratio or khop, that (rho, khop) is added too, so every run is
also deployed exactly as it was trained. A mode whose khop is not 2 carries it in its name (`tta@0.005k1`).
Per mode: relative L2 against the truth on the covered nodes; D_rep = relative distance to the model's own
`full` prediction on the same nodes (for decoupled runs this is the anchor-vs-mesh-cache difference, an
empirical quantity); coverage. A canary recomputes the last training validation (tolerance 2%).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from ..Datasets.olbc import load
from ..NNmodels.zoo import build_model
from ..Utils.run_config import canonical
from .cfg_common import (DATASETS, anchors_for, decoupled, gn_halo_hops, predict, predict_halo, set_matmul, sha,
                         source_record, tta, validate)
from .olbc_common import OlbcData, compact_region, rel_l2, single_subgraph

TTA = (0.05, 0.2, 0.5)
SINGLE = (('scatter', 0.05), ('scatter', 0.2), ('box', 0.05), ('box', 0.2))
FIXED_KHOP = 2          # the khop of the fixed grid: refine's olbc setting, and that of every run in c37-c40


def deployment_modes(train_rho, train_khop=FIXED_KHOP):
    """([(rho, khop)] for TTA, [(kind, rho, khop)] for single subgraphs): the fixed grid at FIXED_KHOP, plus the
    training (rho, khop) first when it is not already in it."""
    tta = [(r, FIXED_KHOP) for r in TTA]
    single = [(kind, r, FIXED_KHOP) for kind, r in SINGLE]
    if train_rho < 1 and (train_rho, train_khop) not in tta:
        tta.insert(0, (train_rho, train_khop))
    if train_rho < 1 and ('scatter', train_rho, train_khop) not in single:
        single = [('scatter', train_rho, train_khop), ('box', train_rho, train_khop)] + single
    return tta, single


def mode_key(kind, rho, khop):
    return f'{kind}@{rho:g}' + ('' if khop == FIXED_KHOP else f'k{khop}')


def summarize(x):
    x = np.asarray(x, dtype=np.float64)
    return {'mean': float(x.mean()), 'median': float(np.median(x)), 'std': float(x.std()), 'n': int(x.size)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True)
    ap.add_argument('--output', default='eval_v1')
    ap.add_argument('--draws', type=int, default=3)
    ap.add_argument('--max-cases', type=int, default=256)
    ap.add_argument('--seed', type=int, default=260930)
    ap.add_argument('--allow-uncommitted', action='store_true', help='smoke tests only')
    ap.add_argument('--roi-shares', type=float, nargs='+', default=None,
                    help='c121: only compact regions holding these shares (and full / full_anchor), e.g. 0.02 0.05 0.12 0.3')
    args = ap.parse_args()
    src = source_record(['Main/eval_cfg.py'])
    if src['sources_uncommitted'] and not args.allow_uncommitted:
        raise SystemExit(f"uncommitted sources: {src['sources_uncommitted']} (commit first, or --allow-uncommitted)")

    run = Path(args.run)
    out = run / args.output
    if out.exists():
        raise SystemExit(f'{out} exists (evaluations are never overwritten)')
    ck = torch.load(run / 'checkpoint.pt', map_location='cpu', weights_only=False)
    rec = ck['config']
    cfg = canonical(rec['run_config'])
    if cfg['dataset']['name'] != 'olbc':
        raise SystemExit(f"eval_cfg evaluates olbc runs only; {cfg['dataset']['name']} runs have their own "
                         'evaluation (R0-S: eval_r0s, design section 5.1)')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    set_matmul(cfg)                    # train.matmul of the run (fp32 = TF32 disabled, as always)
    spec = DATASETS[cfg['dataset']['name']]
    model = build_model(cfg, spec['node_in'], spec['bnd_in']).to(device)
    model.load_state_dict(ck['model'])
    model.eval()
    ds = cfg['dataset']

    mesh, tr = load(ds['data_dir'], 'train')
    tdata = OlbcData(mesh, tr, rec['stats'], device)
    val_cases = np.arange(rec['n_train'], rec['n_train'] + min(ds['val_cases'], ds['val_count']))
    canary = validate(model, tdata, cfg, val_cases)
    status = json.loads((run / 'status.json').read_text())
    for k, v in canary.items():
        ref = status['final_val'][k]
        assert abs(v - ref) <= 0.02 * max(ref, 1e-9), f'canary {k}: {v} vs recorded {ref}'
    del tdata, tr

    _, te = load(ds['data_dir'], 'test')
    data = OlbcData(mesh, te, rec['stats'], device)
    cases = range(min(args.max_cases, te['sol'].shape[0]))
    rng = np.random.default_rng(args.seed)
    acc = {}

    def add(mode, key, value):
        acc.setdefault(mode, {}).setdefault(key, []).append(value)

    tta_modes, single_modes = deployment_modes(cfg['sampling']['sample_ratio'], cfg['sampling']['khop'])
    roi_shares = tuple(args.roi_shares or ())
    if roi_shares:                         # c121: the compact regions only (the other modes are in eval_v1)
        tta_modes, single_modes = [], []
    hops = gn_halo_hops(model, cfg)          # d54: GN runs get the +halo<h> twins of the single-subgraph modes
    allnodes = torch.arange(data.n, device=device)
    with torch.no_grad():
        for c in cases:
            truth = data.target(c, allnodes)
            full = predict(model, data, cfg, c, allnodes, full_context=True)
            add('full', 'rel_l2', rel_l2(full, truth))
            if decoupled(cfg):
                p = predict(model, data, cfg, c, allnodes, anchors_for(cfg, data, rng))
                add('full_anchor', 'rel_l2', rel_l2(p, truth))
                add('full_anchor', 'd_rep', rel_l2(p, full))
            for rho, k in tta_modes:
                p, count = tta(model, data, cfg, c, rho, k, rng)
                key = mode_key('tta', rho, k)
                add(key, 'rel_l2', rel_l2(p, truth))
                add(key, 'd_rep', rel_l2(p, full))
                add(key, 'mean_count', float(count.mean()))
            for kind, rho, k in single_modes:
                key = mode_key(kind, rho, k)
                for d in range(args.draws):
                    nodes = single_subgraph(data, c, kind, rho, k, d)
                    anchors = anchors_for(cfg, data, rng)
                    p = predict(model, data, cfg, c, nodes, anchors)
                    add(key, 'rel_l2', rel_l2(p, truth[nodes]))
                    add(key, 'd_rep', rel_l2(p, full[nodes]))
                    add(key, 'coverage', nodes.numel() / data.n)
                    if hops:                        # d54: the same subgraph and anchors on its GN halo
                        ph = predict_halo(model, data, cfg, c, nodes, hops, anchors)
                        hk = f'{key}+halo{hops}'
                        add(hk, 'rel_l2', rel_l2(ph, truth[nodes]))
                        add(hk, 'd_rep', rel_l2(ph, full[nodes]))
                        add(hk, 'coverage', nodes.numel() / data.n)
                        add(hk, 'd_trunc', rel_l2(p, ph))
            for q in roi_shares:                   # c121: compact regions, as single_modes (fresh anchors, GN halo)
                key = f'roi@{q:g}'
                for d in range(args.draws):
                    nodes = compact_region(data, c, q, d)
                    anchors = anchors_for(cfg, data, rng)
                    p = predict(model, data, cfg, c, nodes, anchors)
                    whole = rel_l2(full[nodes], truth[nodes])
                    add(key, 'rel_l2', rel_l2(p, truth[nodes]))
                    add(key, 'rel_l2_full', whole)
                    add(key, 'd_rep', rel_l2(p, full[nodes]))
                    add(key, 'coverage', nodes.numel() / data.n)
                    if hops:
                        ph = predict_halo(model, data, cfg, c, nodes, hops, anchors)
                        hk = f'{key}+halo{hops}'
                        add(hk, 'rel_l2', rel_l2(ph, truth[nodes]))
                        add(hk, 'rel_l2_full', whole)
                        add(hk, 'd_rep', rel_l2(ph, full[nodes]))
                        add(hk, 'coverage', nodes.numel() / data.n)
                        add(hk, 'd_trunc', rel_l2(p, ph))

    results = {m: {k: summarize(v) for k, v in d.items()} for m, d in acc.items()}
    summary = {'run': run.name, 'fingerprint': rec['fingerprint'], 'describe': rec['describe'],
               'seed': cfg['train']['seed'], 'parameters': rec['parameters'], 'test_cases': len(cases),
               'draws': args.draws, 'eval_seed': args.seed, 'results': results, 'canary': canary,
               **({'roi_shares': list(roi_shares)} if roi_shares else {}),
               **({'gn_halo_hops': hops} if hops else {}),
               'train_git_commit': rec['git_commit'], **source_record(['Main/eval_cfg.py']),
               'checkpoint_sha256': sha(run / 'checkpoint.pt'),
               'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    out.mkdir(exist_ok=False)          # only now, so a failed evaluation leaves nothing that blocks a retry
    # '+' (the +halo<h> modes of GN runs, d54) as eval_r0s writes it, so cfg_seed_table finds the keys; no other
    # mode name has one, so the keys of every run without GN are unchanged
    np.savez(out / 'per_case.npz', **{f'{m}__{k}'.replace('@', '_at_').replace('+', '_plus_'): np.asarray(v)
                                     for m, d in acc.items() for k, v in d.items()})
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')     # last: its presence = complete
    print(json.dumps({m: {k: round(v['mean'], 4) for k, v in d.items()} for m, d in results.items()}))


if __name__ == '__main__':
    main()
