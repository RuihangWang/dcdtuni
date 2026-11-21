"""Shared pieces of the config-driven train/eval entries (train_cfg, eval_cfg).

Data and sampling are those of c37 (scano.Main.olbc_common, scano.Datasets.refine_sampling), imported
unchanged; this module only adds what the run config switches: the context measure, the decoupled context,
and whether BC fields stay on the node features. With the c37 settings (measure area, node fields on) every
function here computes exactly what olbc_common computes (tests/test_zoo.py).

Datasets: `open_data` and `sample_nodes` dispatch on dataset.name. olbc goes through the functions it always
used (bit-identical); r0s (R0-S, scano.Main.r0s_common) brings its own data class with the same interface and
adds the uniform-subset sampler (sampling.regime = "uniform", scano.Datasets.subset_sampling), the A2/A3 keys
(boundary.record_sample, context.measure = "area_ht", context.anchor_design, train.loss_weight; their inclusion
probabilities are attached by open_data) and its validation metric (volume-weighted relative L2 over the cells,
design section 5.1; olbc keeps the node-count relative L2). Keys added after a run was trained are read with their
defaults (`setting`), so configurations stored in older checkpoints keep working.

A1 designs (r0s only, c60; r0s_common.design_nodes, scano.Datasets.design_sampling): sampling.faces = "stratified"
(the native draw's cells plus a stratified draw of the boundary faces, every patch seen), sampling.regime =
"partition" (one block plus a k-hop halo per draw; `scored_mask` marks the block, the only nodes the loss and the
matched validation read) and train.loss_weight = "ht" (count-measure Horvitz-Thompson: the loss is
(1/N) sum_{i in S} e_i / pi_i, unbiased for the whole-mesh node mean at fixed predictions, with the design's inclusion
probabilities r0s_common.design_pi; not Proposition C's V / pi form).
With these keys at their defaults every function here computes exactly what it computed before.

Several fields (DATASETS[name]['out_dim'] > 1, DCTS: u, v, w, p_rgh, T): the model returns [P, F], the data
object's `target` returns [P, F] and its `mean` / `std` are scalars or per-field [F] (floats, sequences or tensors).
`train_loss` is the per-field mean squared error in units of each field's std, averaged over the fields (train.loss =
"rel_l2", decision d50: the per-field relative L2 error about the training mean, `rel_l2_loss`); `metric`
averages `field_errors` (per field: error norm / norm of the target's variation about its training mean, weighted
by the cell measure), and validation also reports the per-field values. With one field (out_dim absent = 1, olbc
and r0s) every function computes exactly what it computed before (tools/check_branch_identity.py).

Per-case meshes (DCTS, scano.Main.dcts_common): every case has its own mesh, so the data object has a
`use_case(case)` method and its mesh attributes (n, area, subgraph, ...) refer to the current case. `use_case(data,
case)` here calls it (and does nothing for olbc / r0s, whose data objects have none); sample_nodes, predict, tta and
the per-case loop of validate_cases call it first, and every per-case quantity (the node range of the full mesh, the
anchors) is built after it. context.anchor_cap (abupt on dcts; AB-UPT subsamples its anchors) caps the number of
points with positive context weight per forward: a uniform random subset keeps its weights, drawn from the random
generator the caller passes (`rng`), so training, validation and evaluation draws are reproducible.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import numpy as np
import torch

from ..Datasets.olbc import load as olbc_load
from ..Datasets.patch_records import aggregate_records_by_patch
from ..Datasets.refine_sampling import inference_chunks, validate_regime_cfg
from ..Datasets.sampling_tricks import add_missed_groups, masked_records, mixed_ratio
from ..Datasets.subset_sampling import uniform_nodes
from ..NNmodels.zoo import build_model
from ..Utils import run_config
from ..Utils.paths import git_head
from .olbc_common import OlbcData, draw_anchors, rel_l2, train_nodes, train_stats

# node_in / bnd_in: feature widths; bc_cols: node-feature columns holding BC values (zeroed when node_bc off);
# space_dim / pos_range: coordinate columns and their fixed range (read by abupt, gnot and mgn);
# out_dim: predicted fields per node, read with .get(..., 1) (absent = 1: olbc and r0s predict one scalar field).
# type_cols (r0s): the (D, N, R) type one-hot columns of the node features and of the records, which
# boundary.types = "none" and boundary.type_encoding replace in front of the model (zoo/type_encoding.py).
DATASETS = {'olbc': {'node_in': 10, 'bnd_in': 9, 'bc_cols': (5, 10), 'space_dim': 2, 'pos_range': (-1.0, 1.0)},
            'r0s': {'node_in': 10, 'bnd_in': 11, 'bc_cols': (5, 10), 'space_dim': 3, 'pos_range': (0.0, 1.0),
                    'type_cols': ((5, 8), (6, 9))},
            # DCTS (scano.Main.dcts_common): five fields [u, v, w, p_rgh, T] in 3-D; node = x', y', z', unit
            # normal, kind one-hot (cell, wall, patch, cyclic), 5 BC values; record = x', y', z', unit normal, kind
            # one-hot (wall, patch, cyclic), 5 BC values; coordinates by the fixed training transform (the training
            # geometries lie in [0, 1] along their longest axis)
            'dcts': {'node_in': 15, 'bnd_in': 14, 'bc_cols': (10, 15), 'space_dim': 3, 'pos_range': (0.0, 1.0),
                    'out_dim': 5}}

SOURCES = ['Utils/run_config.py', 'NNmodels/zoo/__init__.py', 'NNmodels/zoo/slice_operator.py',
           'NNmodels/zoo/transolver.py', 'NNmodels/zoo/transolver_pp.py', 'NNmodels/zoo/lfe_route.py', 'NNmodels/lfe_adapter.py',
           'NNmodels/zoo/sca.py', 'NNmodels/zoo/gnot.py', 'NNmodels/zoo/mgn.py', 'NNmodels/zoo/geotransolver.py',
           'NNmodels/zoo/abupt.py', 'NNmodels/zoo/beno.py',
           'Main/cfg_common.py', 'Main/train_cfg.py', 'Main/olbc_common.py', 'Datasets/olbc.py',
           'Datasets/refine_sampling.py', 'Main/r0s_common.py', 'Datasets/r0s.py', 'Datasets/subset_sampling.py',
           'Datasets/patch_records.py', 'Datasets/dcts.py', 'NNmodels/attention.py', 'Main/dcts_common.py',
           'NNmodels/zoo/area_slices.py', 'NNmodels/zoo/type_encoding.py', 'Datasets/dcts_source.py',
           'Datasets/sampling_tricks.py']
SOURCES += ['Datasets/design_sampling.py']          # A1 designs (c60)
SOURCES += ['NNmodels/zoo/fvgn_gn.py', 'Main/gn_common.py']     # model.message_passing (c76, d54)
SOURCES += ['NNmodels/zoo/aerogto.py']                           # AeroGTO host (d58)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_record(extra=()):
    """sha256 of every source the run depends on, the commit, and whether any of them differs from it."""
    pkg = Path(__file__).parents[1]
    files = [f'src/scano/{s}' for s in list(SOURCES) + list(extra)]
    root = pkg.parents[1]
    commit = git_head(root)                         # None in the code release (no git): the sha256 identify the sources
    dirty = subprocess.run(['git', 'status', '--porcelain', '--', *files], cwd=root, capture_output=True,
                           text=True, check=True).stdout.strip() if commit else ''
    return {'source_sha256': {f: sha(root / f) for f in files}, 'git_commit': commit,
            'sources_uncommitted': dirty.splitlines()}


def setting(cfg, sec, key):
    """cfg[sec][key], or its default for a key added after the configuration was stored (old checkpoints)."""
    return cfg[sec].get(key, run_config.DEFAULTS[sec][key])


def set_matmul(cfg):
    """train.matmul: fp32 disables TF32 for CUDA matmuls and cuDNN (what every entry did before the key existed),
    tf32 allows it. Returns the precision note recorded in config.json."""
    tf32 = setting(cfg, 'train', 'matmul') == 'tf32'
    torch.backends.cuda.matmul.allow_tf32 = tf32
    torch.backends.cudnn.allow_tf32 = tf32
    return 'float32; TF32 enabled (CUDA matmul and cuDNN)' if tf32 else 'float32; TF32 disabled'


def open_data(cfg, split, device, stats=None):
    """(data, stats, n_cases) of `split` for dataset.name; stats from the training cases of the train split
    when None (olbc: olbc_common.train_stats, as train_cfg always did). r0s: the inclusion probabilities that
    area_ht / v_over_pi need are attached here (r0s_common.attach_design), so training, selection and evaluation
    all read the same ones."""
    data, stats, n_cases = _open_data(cfg, split, device, stats)
    if setting(cfg, 'sampling', 'faces') == 'adjacent':     # c123: k-hop draws plus the faces of their cells
        data.face_closure = True
    return data, stats, n_cases


def _open_data(cfg, split, device, stats=None):
    ds = cfg['dataset']
    if ds['name'] == 'dcts':                        # per-case meshes, train-only statistics (scano.Main.dcts_common)
        from .dcts_common import open_dcts
        return open_dcts(cfg, split, device, stats)
    if ds['name'] == 'olbc':
        mesh, arrays = olbc_load(ds['data_dir'], split)
        n_cases = int(arrays['sol'].shape[0])
        if stats is None:
            stats = train_stats(mesh, arrays, n_cases - ds['val_count'])
        return OlbcData(mesh, arrays, stats, device), stats, n_cases
    from .r0s_common import attach_design, attach_oracle_code, open_r0s
    data, stats, n_cases = open_r0s(ds['data_dir'], split, device, ds['val_count'], stats)
    attach_design(data, cfg)
    attach_oracle_code(data, cfg)
    if setting(cfg, 'boundary', 'type_encoding') != 'onehot':
        # the frozen type-name embeddings the text arms read (c57): recorded with the run (config.json
        # data_provenance) and checked on resume like any other data provenance
        from ..NNmodels.zoo.type_encoding import table_record
        data.provenance = {'type_text': table_record()}
    return data, stats, n_cases


def feature_widths(cfg):
    """(node_in, bnd_in) of the run: the dataset's, widened by the role one-hot when boundary.types = "role" (dcts);
    "none" keeps the widths of "kind" (dcts_common.feature_widths; on r0s the type columns are zeroed in front of the
    model, zoo/type_encoding.py)."""
    spec = DATASETS[cfg['dataset']['name']]
    if cfg['boundary'].get('oracle_code', 0):          # r0s: one record holding the case's oracle code (R29 E4)
        return spec['node_in'], int(cfg['boundary']['oracle_code'])
    types, source = cfg['boundary'].get('types', 'kind'), cfg['boundary'].get('source', 'none')
    if (types == 'kind' and source == 'none') or cfg['dataset']['name'] != 'dcts':
        return spec['node_in'], spec['bnd_in']
    from .dcts_common import feature_widths as dcts_widths
    return dcts_widths(types, source)


def model_for(cfg):
    """The run's model for its dataset's feature widths, coordinate range and number of fields. On r0s with
    boundary.types = "none" or boundary.type_encoding text / const_text the backbone is wrapped by the type encoding
    (zoo/type_encoding.py; with the defaults the backbone is returned as it always was)."""
    spec = DATASETS[cfg['dataset']['name']]
    node_in, bnd_in = feature_widths(cfg)
    model = build_model(cfg, node_in, bnd_in, space_dim=spec['space_dim'], pos_range=spec['pos_range'],
                        out_dim=spec.get('out_dim', 1))
    if 'type_cols' in spec:
        from ..NNmodels.zoo.type_encoding import wrap
        model = wrap(model, cfg['boundary'].get('types', 'kind'), setting(cfg, 'boundary', 'type_encoding'),
                     *spec['type_cols'])
    return model


def _stat(value, like):
    """A normalisation statistic for tensors like `like`: Python numbers are returned unchanged (the one-field
    path computes exactly as before); per-field sequences / arrays / tensors become a tensor on like's device."""
    if isinstance(value, (int, float)):
        return value
    return torch.as_tensor(value, dtype=like.dtype, device=like.device)


def train_loss(data, cfg, pred, target, nodes, case=None, epoch=None):
    """The training loss of one draw (train.loss). mse (default): squared error in units of the training std, the mean
    over the nodes (weighted by loss_weights when train.loss_weight asks for it). Several fields ([P, F]): that mean
    per field, then the average over the fields. One field: exactly the expression train_cfg used before this helper
    existed. rel_l2: rel_l2_loss with the same node weights.
    A1 (c60): `case` and `epoch` of the draw are read by train.loss_weight = "ht" (the case's strata) and by
    sampling.regime = "partition" (the block)."""
    w = (loss_weights(data, cfg, nodes) if case is None and epoch is None
         else loss_weights(data, cfg, nodes, case, epoch))         # A1 (c60): the draw's case and epoch
    if setting(cfg, 'train', 'loss') == 'rel_l2':
        return rel_l2_loss(data, pred, target, w)
    sq = ((pred - target) / _stat(data.std, pred)) ** 2
    if sq.dim() == 1:
        return torch.mean(sq) if w is None else torch.mean(w * sq)
    per_field = sq.mean(0) if w is None else (w[:, None] * sq).mean(0)
    return per_field.mean()


# rel_l2_loss: the denominator's additive term per unit node weight, in units of the field's training variance
REL_L2_EPS = 1e-8


def rel_l2_loss(data, pred, target, w=None):
    """train.loss = "rel_l2" (decision d50): per field f the relative L2 error of the draw about the training mean,
        loss_f = sqrt(sum_n w_n (p_nf - y_nf)^2) / sqrt(sum_n w_n (y_nf - mu_f)^2 + eps_f),
        eps_f  = REL_L2_EPS * s_f^2 * sum_n w_n,
    and the mean of loss_f over the fields (one field, pred [P]: loss_f itself). mu_f / s_f = the training mean / std
    (data.mean / data.std, scalars or per field); w = the per-node weights of the mse (None = 1; loss_weights). The
    ratio is the denominator of the validation metric field_errors (the variation of the target about the training
    mean, so a field with a large offset such as T near 300 K is not drowned by it), not squared (the LpLoss of the
    neural-operator literature), with the node weights of the training loss instead of the cell measure.
    Computed in units of s_f (p - y and y - mu divided by s_f, which leaves the ratio unchanged), so eps is
    dimensionless: it only counts when the RMS of y - mu over the draw is below about 1e-4 s_f, and it keeps the loss
    and its gradient finite when the target equals the training mean on every node of the draw. The numerator is a
    vector norm, whose gradient is 0 (not NaN) where the error vanishes. Multiplying p - mu and y - mu by a constant
    leaves the loss unchanged (up to the eps term); so does any change of units of a field."""
    s = _stat(data.std, pred)
    d = (pred - target) / s                       # error, in units of the training std
    v = (target - _stat(data.mean, pred)) / s     # the target's variation about the training mean, same units
    if w is None:
        num = torch.linalg.vector_norm(d, dim=0)
        den = (v ** 2).sum(0)
        total = float(pred.shape[0])
    else:
        r = torch.sqrt(w) if d.dim() == 1 else torch.sqrt(w)[:, None]
        num = torch.linalg.vector_norm(r * d, dim=0)
        den = (w * v ** 2 if v.dim() == 1 else w[:, None] * v ** 2).sum(0)
        total = w.sum()
    per_field = num / torch.sqrt(den + REL_L2_EPS * total)
    return per_field.mean()


def use_case(data, case):
    """Make `case` the current case of a data object with per-case meshes (GtcData.use_case); no-op otherwise."""
    switch = getattr(data, 'use_case', None)
    if switch is not None:
        switch(int(case))


def a1_design(cfg):
    """Whether the run draws an A1 design (r0s: partition + halo, or stratified boundary faces)."""
    return cfg['sampling']['regime'] == 'partition' or setting(cfg, 'sampling', 'faces') not in ('native', 'adjacent')


def sample_nodes(data, cfg, case, epoch):
    """One draw of the nominal sampler: the frozen refine sampler (scatter / roi, then k-hop), or the uniform subset,
    at sampling.sample_ratio. Validation, selection and evaluation draw with it; training draws with train_nodes_for,
    which is this function unless an A8 trick (sampling.rho_mix, sampling.stratify) is set."""
    return sample_nodes_at(data, cfg, case, epoch, cfg['sampling']['sample_ratio'])


def sample_nodes_at(data, cfg, case, epoch, ratio):
    """The nominal sampler's draw of (case, epoch) at sampling ratio `ratio`."""
    use_case(data, case)
    s = cfg['sampling']
    if a1_design(cfg):          # A1 (c60): r0s_common.design_nodes (partition: the block and its halo, scored_mask)
        from .r0s_common import design_nodes
        return design_nodes(data, s, case, epoch)[0]
    if s['regime'] == 'uniform':
        return uniform_nodes(data.node_keys, case, epoch, ratio).to(data.device)
    if setting(cfg, 'sampling', 'halo'):      # c125: the k-hop draw grown by a halo (context only; scored_mask)
        return train_nodes(data, case, epoch, ratio, s['khop'] + s['halo'], None)
    return train_nodes(data, case, epoch, ratio, s['khop'], roi_config(cfg))


def train_ratio(cfg, case, epoch):
    """The sampling ratio of the training draw of (case, epoch): sampling.sample_ratio, or the per-draw ratio of
    sampling.rho_mix (sampling_tricks.mixed_ratio)."""
    s = cfg['sampling']
    mix = setting(cfg, 'sampling', 'rho_mix')
    if not mix:
        return s['sample_ratio']
    return mixed_ratio(case, epoch, mix, s['rho_mix_draw'])


def train_nodes_for(data, cfg, case, epoch):
    """The nodes of one training draw: sample_nodes, at the mixed ratio of sampling.rho_mix when set, topped up with one
    face of every patch it missed when sampling.stratify = "patch" (data.node_groups: r0s)."""
    if not setting(cfg, 'sampling', 'rho_mix') and setting(cfg, 'sampling', 'stratify') == 'none':
        return sample_nodes(data, cfg, case, epoch)                   # every run without the A8 keys
    nodes = sample_nodes_at(data, cfg, case, epoch, train_ratio(cfg, case, epoch))
    if setting(cfg, 'sampling', 'stratify') == 'patch':
        nodes = add_missed_groups(nodes, data.node_groups(case), data.node_keys, case, epoch)
    return nodes


def bc_mask_nodes(data, cfg, case, epoch):
    """boundary.node_bc_mask = p > 0: the boundary-face nodes whose BC fields are hidden in the training draw of (case,
    epoch) (the face nodes of sampling_tricks.masked_records; r0s), else None."""
    p = setting(cfg, 'boundary', 'node_bc_mask')
    if not p:
        return None
    return data.face_node[masked_records(data.record_keys, case, epoch, p).to(data.device)]


def train_forward(model, data, cfg, case, epoch, rng, draw=False):
    """(nodes, prediction) of one training draw of `case`, keyed by `epoch`: the nodes (train_nodes_for), the decoupled
    anchors from `rng`, the record subset (boundary.record_sample) and the hidden BC fields (boundary.node_bc_mask).
    Without the A8 keys this is exactly what train_cfg did before they existed (the same calls in the same order).
    draw=True returns (nodes, prediction, anchors, records): the paired-intervention step (dcts, dataset.pairs_dir)
    evaluates the room's pair on the room's own draw (dcts_common.pair_term); the calls and their order are the same."""
    nodes = train_nodes_for(data, cfg, case, epoch)
    anchors = anchors_for(cfg, data, rng)                 # the same calls in the same order as before the jump keys
    records = record_subset(data, cfg, case, epoch)
    bc_masked = bc_mask_nodes(data, cfg, case, epoch)
    mode = setting(cfg, 'boundary', 'server_jump')
    if mode == 'none':
        pred = predict(model, data, cfg, case, nodes, anchors, records=records, rng=rng, bc_masked=bc_masked)
        return (nodes, pred, anchors, records) if draw else (nodes, pred)
    # boundary.server_jump (dcts; run_config): the outlet T boundary values of this training draw
    t_out = None
    if mode == 'teacher':
        if jump_draw(case, epoch) < setting(cfg, 'boundary', 'server_jump_p'):
            t_out = teacher_outlet_T(data, case)
    else:                                               # unrolled: a no-grad pass with the cached values first
        state = None if rng is None else rng.bit_generator.state
        with torch.no_grad(), outlet_inputs(data, None):
            first = predict(model, data, cfg, case, nodes, anchors, records=records, rng=rng, bc_masked=bc_masked,
                            jump='pass')
        if state is not None:                           # the training pass draws what a single pass would draw
            rng.bit_generator.state = state
        t_out = server_inlet_T(data, nodes, first) + data.server_map()['dT']
    with outlet_inputs(data, t_out):
        pred = predict(model, data, cfg, case, nodes, anchors, records=records, rng=rng, bc_masked=bc_masked,
                       jump='pass')
    return (nodes, pred, anchors, records) if draw else (nodes, pred)


# --- server jump condition (boundary.server_jump; dcts; docs/dcts_inlets_2026-10-08.md B1) -----------------------------
JUMP_SALT = 0x5E7A11        # the teacher draw key of (case, epoch)
T_FIELD = 4                 # dcts field order u, v, w, p_rgh, T


def jump_draw(case, epoch):
    """Uniform [0, 1) number of training draw (case, epoch) for the teacher probability (fixed per draw, independent of
    every other random stream)."""
    return float(np.random.default_rng([JUMP_SALT, int(case), int(epoch)]).random())


class outlet_inputs:
    """Context manager: while active, every _forward of `data` reads the server-outlet T boundary values `t_out` ([S]
    kelvin per server of data.server_map(); NaN = keep the cached value; None = the cached values) in the node features
    and the records. Restores the previous setting on exit."""

    def __init__(self, data, t_out):
        self.data, self.t_out = data, t_out

    def __enter__(self):
        self.saved = getattr(self.data, '_jump_outlet_T', None)
        self.data._jump_outlet_T = self.t_out
        return self.data

    def __exit__(self, *exc):
        self.data._jump_outlet_T = self.saved
        return False


def server_inlet_T(data, read, out):
    """[S] float64 inlet temperature of every server (K): the area-weighted mean of the predictions `out` [P, F]
    (rows = the node ids `read`) over the owner cells of its inlet faces; NaN for a server none of whose owner cells
    is in `read`. Reads predictions only."""
    m = data.server_map()
    pos = torch.full((data.n,), -1, dtype=torch.long, device=out.device)
    pos[read] = torch.arange(read.numel(), device=out.device)
    p = pos[m['in_owner']]
    ok = p >= 0
    srv, w = m['in_server'][ok], m['in_area'][ok]
    num = torch.zeros(m['n'], dtype=torch.float64, device=out.device).index_add_(0, srv, out[p[ok], T_FIELD].double() * w)
    den = torch.zeros(m['n'], dtype=torch.float64, device=out.device).index_add_(0, srv, w)
    t = num / den.clamp_min(1e-300)
    return torch.where(den > 0, t, torch.full_like(t, float('nan')))


def teacher_outlet_T(data, case):
    """[S] true inlet temperature + dT of every server of training case `case` (the area-weighted target over its inlet
    faces): the teacher input of boundary.server_jump = "teacher". Training only (it reads the target)."""
    m = data.server_map()
    y = data.target(case, data.n_cells + m['in_face'])[:, T_FIELD].double()
    num = torch.zeros(m['n'], dtype=torch.float64, device=y.device).index_add_(0, m['in_server'], y * m['in_area'])
    den = torch.zeros(m['n'], dtype=torch.float64, device=y.device).index_add_(0, m['in_server'], m['in_area'])
    return num / den + m['dT']


def outlet_override(data, cfg, pts, feat, bf, records, t_out):
    """(node features, records) with the server-outlet T boundary values replaced by t_out (outlet_inputs): the
    records of the outlet faces and, when boundary.node_bc is on, the outlet-face rows among pts."""
    from .dcts_common import BC_T_NODE, BC_T_REC
    if records is not None:
        raise ValueError('boundary.server_jump does not combine with a record subset')
    m = data.server_map()
    mean, std = data.mean[T_FIELD], data.std[T_FIELD]
    z = (t_out.to(m['dT'].device, torch.float64) - mean) / std
    zf = z[m['out_server']]
    ok = torch.isfinite(zf)
    bf = bf.clone()
    bf[m['out_face'][ok], BC_T_REC] = zf[ok].to(bf.dtype)
    if cfg['boundary']['node_bc']:
        val = torch.full((data.n,), float('nan'), dtype=torch.float64, device=pts.device)
        val[data.n_cells + m['out_face'][ok]] = zf[ok]
        v = val[pts]
        rows = torch.isfinite(v)
        feat[rows, BC_T_NODE] = v[rows].to(feat.dtype)
    return feat, bf


def route_rows(data, pts, query_rows):
    """boundary.server_route: one row per server-inlet face whose owner cell is among the leading query rows of pts
    (the row of that cell, its first occurrence; a cell owning k inlet faces of a server counts k times, and a cell
    owning faces of two servers belongs to both: per face, so the map is deterministic), the server of each face, and
    the outlet-face records of every server (SliceOperator.forward's `route`)."""
    m = data.server_map()
    q = pts.numel() if query_rows is None else int(query_rows)
    head = pts[:q]
    pos = torch.full((data.n,), q, dtype=torch.long, device=pts.device)
    pos.scatter_reduce_(0, head, torch.arange(q, device=pts.device), reduce='amin')    # first occurrence
    p = pos[m['in_owner']]
    ok = p < q
    rows, srv = p[ok], m['in_server'][ok]
    return {'rows': rows, 'row_server': srv, 'n_servers': m['n'], 'out_rec': m['out_face'],
            'out_server': m['out_server']}


def jump_fixed_point(model, data, cfg, case, nodes, anchors=None, full_context=False, records=None, *, rng=None,
                     bc_masked=None):
    """The deployment of a boundary.server_jump run: pass 1 with the cached outlet values; then up to
    server_jump_passes - 1 more passes, each with the previous pass's inlet temperatures + dT (server_inlet_T on the
    predicted nodes; a server without an inlet owner cell among them keeps the cached value), stopping once the outlet
    input would change by less than server_jump_tol kelvin. Every pass sees the same anchors, records and (rng state
    restored) cap draw. Reads predictions only, never a target. data.jump_record = {passes, last_change_K: the largest
    change (K) of the outlet input the next pass would read against the one the last pass read, measured after the last
    pass; converged: last_change_K < tol (False when the cap stopped the iteration); cap; tol_K} (jump_record)."""
    use_case(data, case)
    read = torch.arange(data.n, device=data.device) if (full_context and decoupled(cfg)) else nodes
    dT = data.server_map()['dT']
    passes, tol = setting(cfg, 'boundary', 'server_jump_passes'), setting(cfg, 'boundary', 'server_jump_tol')
    state = None if rng is None else rng.bit_generator.state

    def one(t_out):
        if state is not None:
            rng.bit_generator.state = state
        with outlet_inputs(data, t_out):
            return _predict(model, data, cfg, case, read, anchors, full_context, records, rng=rng, bc_masked=bc_masked)
    out, prev, used, change = one(None), None, 1, None
    while True:                         # the outlet input the next pass would read, against the one this pass read
        t_out = server_inlet_T(data, read, out) + dT
        if prev is not None:
            d = (t_out - prev).abs()
            d = d[torch.isfinite(d)]
            change = float(d.max()) if d.numel() else 0.0
            if change < tol:
                break
        if used >= passes:              # the cap: the last change is still at least tol (not converged)
            break
        out, prev, used = one(t_out), t_out, used + 1
    data.jump_record = {'passes': used, 'last_change_K': change, 'converged': change is not None and change < tol,
                        'cap': passes, 'tol_K': tol}
    if read is not nodes:
        out = out[nodes]
    return out


def jump_record(data, cfg):
    """The fixed-point record of the last deployment of a boundary.server_jump run (jump_fixed_point), None for every
    other run."""
    if setting(cfg, 'boundary', 'server_jump') == 'none':
        return None
    return getattr(data, 'jump_record', None)


def consistency_loss(data, pred, nodes, pred2, nodes2):
    """Mean squared difference of two predictions of one case on the nodes both draws hold, in units of the training
    std (several fields [P, F]: per field, then averaged, as train_loss); 0 when the draws share no node. Gradients flow
    through both predictions."""
    pos = torch.full((data.n,), -1, dtype=torch.long, device=nodes2.device)
    pos[nodes2] = torch.arange(nodes2.numel(), device=nodes2.device)
    j = pos[nodes]
    shared = j >= 0
    if not bool(shared.any()):
        return torch.zeros((), device=pred.device)
    sq = ((pred[shared] - pred2[j[shared]]) / _stat(data.std, pred)) ** 2
    return sq.mean() if sq.dim() == 1 else sq.mean(0).mean()


def roi_config(cfg):
    s = cfg['sampling']
    if s['regime'] != 'roi':
        return None
    roi = {'p_scatter': s['p_scatter']}
    validate_regime_cfg(roi)
    return roi


def decoupled(cfg):
    return cfg['context']['mode'] == 'decoupled'


def anchors_for(cfg, data, rng):
    if not decoupled(cfg):
        return None
    design = setting(cfg, 'context', 'anchor_design')
    if design == 'sample':                                       # c117: a second draw of the nominal sampler
        return sample_anchors(cfg, data, rng)
    if setting(cfg, 'context', 'anchor_pool') == 'nodes':      # uniform design (run_config), cells and faces
        return torch.as_tensor(rng.choice(data.n, cfg['context']['anchors'], replace=False), device=data.device)
    if design == 'volume_pps':
        from .r0s_common import draw_anchors_pps
        return draw_anchors_pps(data, cfg['context']['anchors'], rng)
    if design == 'stratified':
        from .r0s_common import draw_anchors_stratified
        return draw_anchors_stratified(data, cfg['context']['anchors'], rng)
    if design == 'stratified_volume':
        from .r0s_common import draw_anchors_stratified_volume
        return draw_anchors_stratified_volume(data, cfg['context']['anchors'], rng)
    return draw_anchors(data, cfg['context']['anchors'], rng)


def sample_anchors(cfg, data, rng):
    """context.anchor_design = "sample" (c117): the anchors are a second draw of the nominal sampler, independent of
    the query draw: a uniform subset of the nodes, or the k-hop subgraph of uniformly drawn pivots (the scatter draw of
    refine's sampler, its pivots a uniform subset), at context.anchor_ratio (0: sampling.sample_ratio), from the
    generator `rng`; sorted node ids.
    They are weighted by the measure like a coupled context (anchor_weights) and pass the GN blocks on their own
    induced subgraph (_forward)."""
    s = cfg['sampling']
    ratio = setting(cfg, 'context', 'anchor_ratio') or s['sample_ratio']
    n = max(1, int(data.n * ratio))                              # budget_of / sample_budget
    if n >= data.n:
        return torch.arange(data.n, device=data.device)
    pick = torch.as_tensor(np.sort(rng.choice(data.n, n, replace=False)), device=data.device)
    if s['regime'] == 'uniform':
        return pick
    return data.subgraph(pick, s['khop']).to(data.device)


def anchor_gn(cfg):
    """Whether the context-only rows (decoupled anchors, the mesh cache's context copy) pass the GN blocks on their own
    induced subgraph (context.anchor_design = "sample", c117); otherwise they carry the trunk without GN (c76)."""
    return decoupled(cfg) and setting(cfg, 'context', 'anchor_design') == 'sample'


def anchor_nodes(cfg, data):
    """The nodes decoupled anchors are drawn from: every node (cells and boundary faces) under context.anchor_pool =
    "nodes", else r0s_common.anchor_pool (R0-S and DCTS: the cells; olbc: every node)."""
    from .r0s_common import anchor_pool
    return np.arange(data.n) if setting(cfg, 'context', 'anchor_pool') == 'nodes' else anchor_pool(data)


def anchor_weights(data, cfg, anchors):
    """Context weights of decoupled anchors: the measure (uniform anchors, Hajek), 1 for volume-PPS and for
    volume-stratified anchors (Hansen-Hurwitz: the draw already carries the volume), or the measure times the relative
    stratum size for spatially stratified anchors (Hajek with 1 / pi; the measure itself when the count divides the
    cells)."""
    design = setting(cfg, 'context', 'anchor_design')
    if design in ('volume_pps', 'stratified_volume'):
        return torch.ones(anchors.numel(), device=data.device)
    w = context_weights(data, anchors, cfg['context']['measure'])
    if design == 'stratified':
        from .r0s_common import stratified_factor
        w = w * stratified_factor(data, anchors, anchors.numel())
    return w


def context_weights(data, nodes, measure):
    if measure == 'area':
        return data.area[nodes]
    if measure == 'area_ht':          # r0s: V / pi of the design that drew the nodes (data.ht_pi, attach_design)
        return data.area[nodes] / data.ht_pi[nodes]
    return torch.ones(nodes.numel(), device=data.device)


def record_subset(data, cfg, case, epoch):
    """Indices of the records the record routes read in one training draw (boundary.record_sample < 1), else None."""
    rate = setting(cfg, 'boundary', 'record_sample')
    if rate >= 1:
        return None
    from .r0s_common import record_draw
    return record_draw(data, case, epoch, rate)


def scored_mask(data, cfg, case, epoch, nodes):
    """Bool mask over the `nodes` of draw (case, epoch) marking the nodes the loss and the matched validation read:
    the block under sampling.regime = "partition" (its halo is context only); the k-hop draw itself under
    sampling.halo > 0 (c125); None (all of them) otherwise."""
    s = cfg['sampling']
    if setting(cfg, 'sampling', 'halo'):
        if case is None or epoch is None:
            raise ValueError('sampling.halo: the loss needs the case and epoch of the draw')
        core = train_nodes(data, int(case), int(epoch), s['sample_ratio'], s['khop'], None)
        return torch.isin(nodes, core.to(nodes.device))
    if cfg['sampling']['regime'] != 'partition':
        return None
    if case is None or epoch is None:
        raise ValueError('sampling.regime = "partition": the loss needs the case and epoch of the draw')
    from ..Datasets.design_sampling import block_of_draw, blocks_for
    from .r0s_common import partition_blocks
    k = blocks_for(cfg['sampling']['sample_ratio'])
    block = torch.as_tensor(partition_blocks(data, k), device=data.device)
    return block[nodes] == block_of_draw(int(case), int(epoch), k)


def loss_weights(data, cfg, nodes, case=None, epoch=None):
    """Per-node weights of the supervised loss: design_loss_weights, times the server-zone factor of
    train.zone_weight (dcts; run_config) when it is above 1: the nodes of data.server_zone(train.zone_radius) weigh
    zone_weight, the others 1, and the product is normalised to mean 1 over the draw. At zone_weight = 1 (the default,
    every run before the key) this is design_loss_weights itself, the same object."""
    w = design_loss_weights(data, cfg, nodes, case, epoch)
    zw = setting(cfg, 'train', 'zone_weight')
    if zw == 1.0:
        return w
    zone = data.server_zone(setting(cfg, 'train', 'zone_radius'))[nodes]
    f = 1.0 + (zw - 1.0) * zone.to(torch.float32)
    w = f if w is None else w * f
    return w / w.mean().clamp_min(1e-30)


def design_loss_weights(data, cfg, nodes, case=None, epoch=None):
    """Per-node weights of the supervised loss (train.loss_weight = v_over_pi: V / pi of the training design;
    inv_pi: 1 / pi, A8's IPW (c61); both normalised to mean 1 over the sampled nodes), or None for the plain mean.
    A1 (c60): sampling.regime = partition: 1 / (fraction of the draw in the block) on the block, 0 on the halo (the
    mean over the block); train.loss_weight = ht: |S| / (N pi_i), so the mean over the draw S is
    (1/N) sum_{i in S} e_i / pi_i, the Horvitz-Thompson estimate of the whole-mesh node mean (pi of the case's design,
    r0s_common.design_pi)."""
    mask = scored_mask(data, cfg, case, epoch, nodes)
    if mask is not None:
        m = mask.float()
        return m * (m.numel() / m.sum().clamp_min(1.0))
    kind = setting(cfg, 'train', 'loss_weight')
    if kind == 'uniform':
        return None
    if kind == 'ht':
        if case is None:
            raise ValueError('train.loss_weight = "ht": the loss needs the case of the draw (its strata)')
        from .r0s_common import design_pi
        return nodes.numel() / (data.n * design_pi(data, cfg['sampling'], int(case))[nodes])
    if kind == 'inv_pi':
        w = 1.0 / data.pi_train[nodes]
    else:
        w = data.area[nodes] / data.pi_train[nodes]
    return w / w.mean().clamp_min(1e-30)


def metric(data, cfg):
    """Validation / selection error, called as metric(pred, ref, nodes) -> float.
    One field (pred [P]): olbc = relative L2 over the nodes (unchanged); r0s = volume-weighted relative L2 over the
    cells (design section 5.1; boundary faces have zero volume and cell volumes differ ~130x, so the node-count norm
    would effectively be a near-wall metric). Several fields (pred [P, F]): the mean over the fields of
    `field_errors`."""
    if cfg['dataset']['name'] != 'r0s':
        one = lambda pred, ref, nodes: rel_l2(pred, ref)       # noqa: E731
    else:
        def one(pred, ref, nodes):
            w = data.area[nodes]
            return float(torch.sqrt((w * (pred - ref) ** 2).sum() / (w * ref ** 2).sum()))

    def err(pred, ref, nodes):
        if pred.dim() == 1:
            return one(pred, ref, nodes)
        return float(field_errors(data, pred, ref, nodes).mean())
    return err


def field_errors(data, pred, ref, nodes):
    """[F] errors of a several-field prediction (numpy float64). For each field f
        sqrt(sum_n w_n (p_nf - y_nf)^2) / sqrt(sum_n w_n (y_nf - mu_f)^2),
    mu_f = the training mean of field f (data.mean, scalar or per field), w = the data object's cell measure data.area
    (cell volume, zero on boundary-face nodes) when it has one, else 1. Dividing by the variation about the training
    mean instead of by |y| keeps a field with a large offset from vanishing: 1-2 K of error on a temperature near
    300 K is ~5e-3 relative to |T| but of order one relative to the temperature variation."""
    p, y = pred.double(), ref.double()
    w = getattr(data, 'area', None)
    w = torch.ones(p.shape[0], dtype=p.dtype, device=p.device) if w is None else w[nodes].double()
    mu = _stat(data.mean, p)
    num = torch.sqrt((w[:, None] * (p - y) ** 2).sum(0))
    den = torch.sqrt((w[:, None] * (y - mu) ** 2).sum(0))
    return (num / den).cpu().numpy()


def node_features(data, cfg, case, pts, bc_masked=None):
    """Node features of `pts`; the BC columns are zero on every node when boundary.node_bc is off, and on the nodes of
    `bc_masked` (a training draw's hidden boundary faces, boundary.node_bc_mask) otherwise."""
    f = data.node_features(case, pts)
    if not cfg['boundary']['node_bc']:
        a, b = DATASETS[cfg['dataset']['name']]['bc_cols']
        f[:, a:b] = 0.0
        extra = getattr(data, 'source_bc_cols', None)      # dcts boundary.source F1 columns (BC data on boundary points)
        if extra is not None:
            f[:, extra[0]:extra[1]] = 0.0
    elif bc_masked is not None:
        a, b = DATASETS[cfg['dataset']['name']]['bc_cols']
        f[torch.isin(pts, bc_masked), a:b] = 0.0
    return f


def cap_context(cfg, ctx, rng):
    """context.anchor_cap: when more than `cap` points have a positive context weight, a uniform random subset of
    `cap` of them (drawn with `rng`, a numpy Generator) keeps its weights and the others get 0. cap 0 (default):
    `ctx` unchanged and `rng` untouched."""
    cap = setting(cfg, 'context', 'anchor_cap')
    if not cap:
        return ctx
    pos = torch.nonzero(ctx > 0).flatten()
    if pos.numel() <= cap:
        return ctx
    if rng is None:
        raise ValueError('context.anchor_cap needs a random generator; this call passes none (rng=...)')
    keep = pos[torch.as_tensor(np.sort(rng.choice(pos.numel(), cap, replace=False)), device=ctx.device)]
    out = torch.zeros_like(ctx)
    out[keep] = ctx[keep]
    return out


def predict(model, data, cfg, case, nodes, anchors=None, full_context=False, records=None, *, rng=None,
            bc_masked=None, jump=None):
    """Denormalised predictions at `nodes` for one case (_predict). jump (boundary.server_jump, dcts): None = the
    deployment rule: one pass with the cached outlet values (server_jump none, every run before the key) or the
    fixed point of jump_fixed_point; 'pass' = one pass with the outlet values outlet_inputs sets (training)."""
    if jump is None and setting(cfg, 'boundary', 'server_jump') != 'none':
        return jump_fixed_point(model, data, cfg, case, nodes, anchors, full_context, records, rng=rng,
                                bc_masked=bc_masked)
    if jump not in (None, 'pass'):
        raise ValueError(f'predict: jump {jump!r} (None or "pass")')
    return _predict(model, data, cfg, case, nodes, anchors, full_context, records, rng=rng, bc_masked=bc_masked)


def _predict(model, data, cfg, case, nodes, anchors=None, full_context=False, records=None, *, rng=None,
             bc_masked=None):
    """Denormalised predictions at `nodes` for one case.

    coupled:   context = the nodes themselves;
    decoupled: context = `anchors`, or every node when full_context (the mesh cache used at deployment).
    Context weights follow cfg.context.measure (anchors: anchor_weights). `records` (indices) restricts the
    boundary records the record routes read (boundary.record_sample; None = all). `rng`: the generator of the
    context.anchor_cap draw (only read when the cap is active). `bc_masked` (node ids, training only): boundary-face
    nodes whose BC fields are hidden (boundary.node_bc_mask; None = none).
    """
    use_case(data, case)
    measure = cfg['context']['measure']
    gn = getattr(model, 'message_passing', 0)          # c76: GN on the query rows; the context rows carry no GN
    if decoupled(cfg) and full_context:
        allnodes = torch.arange(data.n, device=data.device)
        pts, ctx = allnodes, cap_context(cfg, context_weights(data, allnodes, measure), rng)
        # the mesh cache: a query copy (GN / graph host edges) and a context copy of every node, as in training where
        # the anchors are context-only rows (decoupled graph hosts: AeroGTO with model.aerogto_box = "domain", c95).
        # Under anchor_gn (c117 / c118) the context copy passes the same GN on the same whole mesh as the query copy, so
        # the two copies are equal row by row and one pass with every node as query and context gives the same
        # prediction (tests/test_anchor_sample.py) at half the memory and time (a DCTS room: c118 pilot, 10-16).
        if getattr(model, 'needs_edges', False) or (gn and not anchor_gn(cfg)):
            ctx = torch.cat([torch.zeros_like(ctx), ctx])
            out = _forward(model, data, cfg, case, torch.cat([allnodes, allnodes]), ctx, records, bc_masked,
                           query_rows=allnodes.numel())
            return out[:allnodes.numel()][nodes]
        out = _forward(model, data, cfg, case, pts, ctx, records, bc_masked)
        return out[nodes]
    if gn and setting(cfg, 'model', 'gn_context') == 'mesh':     # d54: the GN always on the whole mesh
        return predict_on(model, data, cfg, case, nodes, torch.arange(data.n, device=data.device), anchors, records,
                          rng=rng, bc_masked=bc_masked)
    if decoupled(cfg):
        pts = torch.cat([nodes, anchors])
        ctx = torch.cat([torch.zeros(nodes.numel(), device=data.device), anchor_weights(data, cfg, anchors)])
    else:
        pts, ctx = nodes, context_weights(data, nodes, measure)
    ctx = cap_context(cfg, ctx, rng)
    q = nodes.numel() if decoupled(cfg) else None                # model.message_passing: the leading query rows
    return _forward(model, data, cfg, case, pts, ctx, records, bc_masked, query_rows=q)[:nodes.numel()]


def predict_on(model, data, cfg, case, nodes, support, anchors=None, records=None, *, rng=None, bc_masked=None):
    """Denormalised predictions at `nodes` read off one forward whose query rows are `support` (sorted, distinct node
    ids containing every node of `nodes`), with the context `predict` gives `nodes`: the anchors (decoupled; equal to
    predict(..., support, anchors)[at]) or `nodes` themselves (coupled: the other support rows are queries with
    context weight 0, so the global state is the one of `nodes`). With model.message_passing the GN blocks see the
    mesh graph among the support rows: a GN halo (predict_halo) or the whole mesh (model.gn_context = "mesh") makes
    the GN neighbourhoods of `nodes` complete without changing their context (d54). boundary.server_jump runs deploy
    through predict's fixed point and are refused here (dcts, where predict_on / predict_halo are not used)."""
    if setting(cfg, 'boundary', 'server_jump') != 'none':
        raise ValueError('predict_on / predict_halo do not deploy boundary.server_jump runs; use predict (fixed point)')
    use_case(data, case)
    at = torch.searchsorted(support, nodes)
    if not torch.equal(support[at.clamp_max(support.numel() - 1)], nodes):
        raise ValueError('predict_on: the support must be sorted and contain every node')
    if decoupled(cfg):
        pts = torch.cat([support, anchors])
        ctx = torch.cat([torch.zeros(support.numel(), device=data.device), anchor_weights(data, cfg, anchors)])
        q = support.numel()
    else:
        pts, q = support, None
        ctx = torch.zeros(support.numel(), device=data.device)
        ctx[at] = context_weights(data, nodes, cfg['context']['measure'])
    ctx = cap_context(cfg, ctx, rng)
    return _forward(model, data, cfg, case, pts, ctx, records, bc_masked, query_rows=q)[:support.numel()][at]


def gn_halo_hops(model, cfg):
    """Hops of the deployment halo of a model.message_passing run (3 x m x depth, zoo/fvgn_gn.py), 0 when there is
    nothing to complete: no GN blocks, or model.gn_context = "mesh" (the GN already sees the whole mesh)."""
    if setting(cfg, 'model', 'gn_context') == 'mesh':
        return 0
    return int(getattr(model, 'message_passing_hops', 0))


def predict_halo(model, data, cfg, case, nodes, hops, anchors=None, records=None, *, rng=None):
    """The +halo<h> deployments of eval_cfg / eval_r0s (d54; eval_dcts has its own, c76): `nodes` read off a forward on
    `nodes` and their `hops`-hop mesh halo (gn_common.gn_halo), with the context of `nodes` (predict_on): the GN
    neighbourhoods of the nodes are complete, so the difference from predict(..., nodes, anchors) is the GN's
    truncation at the subgraph border alone (the same anchors, the same context)."""
    from .gn_common import gn_halo
    use_case(data, case)
    return predict_on(model, data, cfg, case, nodes, gn_halo(data, nodes, hops), anchors, records, rng=rng)


def patch_records(data, case):
    """(bnd_feat [P, Fb], bnd_w [P]) of one case under boundary.records = "patch": the face records of
    data.boundary(case) aggregated per patch by data.record_patch_ids(case) (scano.Datasets.patch_records), computed
    once per case on the CPU in float64 and cached on the data object (on its device)."""
    if getattr(data, 'records_hook', None) is not None:
        raise ValueError('boundary.records = "patch" does not support a records_hook: the hook (eval_r0s K1 / D4 '
                         'record transforms) replaces the face records, and their patch ids are unknown')
    ids_of = getattr(data, 'record_patch_ids', None)
    if ids_of is None:
        raise ValueError(f'boundary.records = "patch" needs per-record patch ids, but {type(data).__name__} has no '
                         'record_patch_ids(case)')
    cache = data.__dict__.setdefault('_patch_record_cache', {})
    key = int(case)
    if key not in cache:
        bf, bw = data.boundary(case)
        feat, w, _ = aggregate_records_by_patch(bf.detach().cpu(), bw.detach().cpu(), ids_of(case).cpu())
        cache[key] = (feat.to(data.device), w.to(data.device))
    return cache[key]


def subset_weights(bw, records, kind):
    """Weights of a record subset (boundary.record_sample, eval_r0s REC). With slice tokens the subset's weights are
    scaled to the full total, so the slice masses (and the unnormalised read) do not shrink with the fraction read;
    every other route is invariant to a global factor of the weights, and keeps them as they are."""
    w = bw[records]
    return w * (bw.sum() / w.sum()) if kind == 'slices' else w


def _forward(model, data, cfg, case, pts, ctx, records=None, bc_masked=None, query_rows=None):
    """One model forward on the points pts with context weights ctx. query_rows (model.message_passing): the leading
    rows that are queries and get the GN blocks on their induced mesh edges (None = every row: a coupled context);
    the rows after them are context-only (decoupled anchors, the mesh cache's context copy)."""
    if setting(cfg, 'boundary', 'records') == 'patch':
        if records is not None:            # run_config rejects record_sample < 1 with patch records
            raise ValueError('boundary.records = "patch" does not combine with a record subset')
        bf, bw = patch_records(data, case)
    else:
        bf, bw = data.boundary(case)
        if records is not None:
            bf, bw = bf[records], subset_weights(bw, records, setting(cfg, 'boundary', 'records'))
    kw = {}
    if getattr(model, 'needs_edges', False):       # graph backbones: mesh edges induced on the passed points
        from torch_geometric.utils import subgraph    # (decoupled, c95: on the query rows only; the anchors after
        ids = pts if query_rows is None else pts[:int(query_rows)]          # them are edge-free context rows)
        kw['edges'] = subgraph(ids, data.edge_index, relabel_nodes=True, num_nodes=data.n)[0]
    if getattr(model, 'message_passing', 0):     # c76 / d54: the mesh graph among the query rows (gn_common.gn_edges)
        from .gn_common import gn_edges
        q = pts.numel() if query_rows is None else int(query_rows)
        kw['edges'], kw['edge_raw'] = gn_edges(data, pts[:q])
        if q < pts.numel() and anchor_gn(cfg):    # c117: the context rows pass the GN on their own induced subgraph,
            e2, r2 = gn_edges(data, pts[q:])      # disjoint from the queries' (no edge joins the two row blocks)
            kw['edges'] = torch.cat([kw['edges'], e2 + q], 1)
            kw['edge_raw'] = torch.cat([kw['edge_raw'], r2])
            q = pts.numel()
        kw['query_rows'] = q
    if getattr(model, 'needs_record_area', False):     # outlet slices partition by the physical face areas
        area = data.face_area
        kw['bnd_area'] = (area if records is None else area[records])[None]
    feat = node_features(data, cfg, case, pts, bc_masked)
    t_out = getattr(data, '_jump_outlet_T', None)      # boundary.server_jump: outlet values set by outlet_inputs
    if t_out is not None:
        feat, bf = outlet_override(data, cfg, pts, feat, bf, records, t_out)
    if getattr(model, 'server_route', False):          # boundary.server_route: inlet rows and outlet records
        kw['route'] = route_rows(data, pts, query_rows)
    out = model(feat[None], ctx[None], bf[None], bw[None], **kw)[0]
    return out * _stat(data.std, out) + _stat(data.mean, out)


def tta(model, data, cfg, case, sample_ratio, khop, rng):
    """refine inference (as olbc_common.refine_tta): the case's pivot pool in budget chunks, a k-hop subgraph
    per chunk, predictions accumulated over every subgraph node and divided by the per-node count. Several fields:
    the accumulator is [N, F] (allocated on the first chunk)."""
    use_case(data, case)
    total = None
    count = torch.zeros(data.n, device=data.device)
    for piv in inference_chunks(case, data.n, sample_ratio):
        nodes = data.subgraph(piv, khop)
        p = predict(model, data, cfg, case, nodes, anchors_for(cfg, data, rng), rng=rng)
        if total is None:
            total = torch.zeros((data.n,) + tuple(p.shape[1:]), device=data.device)
        total.index_add_(0, nodes, p)
        count.index_add_(0, nodes, torch.ones(nodes.numel(), device=data.device))
    c = count.clamp(min=1)
    return total / (c if total.dim() == 1 else c[:, None]), count


class full_mesh_design:
    """Context manager: while the whole mesh is passed, context.measure = "area_ht" uses pi = 1 (every node is in
    the deployment), i.e. the plain volume measure the full-mesh deployment D2 reports. No-op without area_ht
    (olbc and every other r0s run: data.ht_pi does not exist)."""

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        self.saved = getattr(self.data, 'ht_pi', None)
        if self.saved is not None:
            self.data.ht_pi = torch.ones(self.data.n, device=self.data.device)
        return self.data

    def __exit__(self, *exc):
        if self.saved is not None:
            self.data.ht_pi = self.saved
        return False


def validate_cases(model, data, cfg, cases):
    """Per-case validation errors (lists, in the order of `cases`). full: every node (decoupled: fresh anchors,
    the training distribution; area_ht: pi = 1, as the full-mesh deployment); matched: the refine training draw of
    epoch 0 for each validation case index (and its record draw when boundary.record_sample < 1). Error = `metric`
    (r0s: volume-weighted over the cells). The model's train/eval mode is restored on exit (review 2026-09-30:
    it used to be left in train mode, so evaluations that run the canary first deployed in train mode)."""
    was_training = model.training
    model.eval()
    rng = np.random.default_rng(12345)
    err = metric(data, cfg)
    full, matched = [], []
    fields = {'full_fields': [], 'matched_fields': []}     # several fields: field_errors per case

    def score(out, key, pred, ref, nodes):
        out.append(err(pred, ref, nodes))
        if pred.dim() == 2:
            fields[key].append(field_errors(data, pred, ref, nodes).tolist())
    try:
        with torch.no_grad():
            for c in cases:
                use_case(data, c)                  # per-case meshes: n and the anchor pool belong to case c
                allnodes = torch.arange(data.n, device=data.device)
                with full_mesh_design(data):
                    score(full, 'full_fields', predict(model, data, cfg, c, allnodes, anchors_for(cfg, data, rng),
                                                       rng=rng),
                          data.target(c, allnodes), allnodes)
                nodes = sample_nodes(data, cfg, int(c), 0)
                pred = predict(model, data, cfg, c, nodes, anchors_for(cfg, data, rng),
                               records=record_subset(data, cfg, int(c), 0), rng=rng)
                keep = scored_mask(data, cfg, int(c), 0, nodes)      # partition: the block only (None: every node)
                if keep is not None:
                    pred, nodes = pred[keep], nodes[keep]
                score(matched, 'matched_fields', pred, data.target(c, nodes), nodes)
    finally:
        model.train(was_training)
    return {'full': full, 'matched': matched, **{k: v for k, v in fields.items() if v}}


def validate(model, data, cfg, cases):
    """As train_olbc.validate: the means of validate_cases (several fields: also the per-field means)."""
    per = validate_cases(model, data, cfg, cases)
    out = {'full': float(np.mean(per['full'])), 'matched': float(np.mean(per['matched']))}
    for key in ('full_fields', 'matched_fields'):
        if key in per:
            out[key] = np.mean(per[key], 0).tolist()
    return out
