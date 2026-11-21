"""Model zoo for config-driven experiments: `build_model(cfg, node_in, bnd_in)` from a resolved run config
(scano.Utils.run_config). Every backbone takes the same inputs,

    model(node_feat [B,N,F], ctx_w [B,N], bnd_feat [B,NB,Fb], bnd_w [B,NB]) -> [B,N]      (out_dim = 1)
                                                                             -> [B,N,O]    (out_dim = O > 1)

so the data side, the sampler and the train/eval loops do not change with the backbone. `out_dim` (the number of
predicted fields, DATASETS[...]['out_dim'] in cfg_common; 1 for olbc and r0s) only sets the width of each backbone's
output layer: with out_dim = 1 every backbone creates the same parameters in the same order and computes exactly
what it computed before the argument existed (tools/check_branch_identity.py). New backbones and ported baselines
register here; see docs/framework.md. space_dim also reaches the slice-token hosts (slice, transolver, abupt, aerogto),
whose spatial slices (boundary.slice_assign = "spatial") read the record positions from the first space_dim record
columns. d58 (10-08): aerogto (AeroGTO host, zoo/aerogto.py) and BENO on R0-S (3-D record order, zoo/beno.py).
"""
from __future__ import annotations

from .abupt import ABUPT
from .aerogto import AeroGTO
from .beno import BENO, beno_columns
from .geotransolver import GeoTransolver
from .slice_operator import SliceOperator, attach_code_rng, count_parameters
from .gnot import GNOT
from .mgn import MGN
from .sca import SCA
from .transolver import Transolver
from .transolver_pp import TransolverPP


def _lfe(cfg):
    b = cfg['boundary']
    if b['lfe'] == 'none':
        return None
    return dict(cfg['lfe'], measure=b['lfe_measure'])


def _record_slices(cfg, space_dim=None):
    """Area-consistent slice tokens for the channel (boundary.records = slices; zoo/area_slices.py), else None.
    assign / pos_dims: learned slices, or slices grouped by spatial adjacency over the first space_dim record columns
    (boundary.slice_assign); lse_max: the clamp of the unnormalised read (boundary.slice_lse_max); outlet_slices /
    outlet_col: role-partitioned slices for the server-outlet records (boundary.outlet_slices, dcts). Configurations
    stored before these keys existed read their defaults (learned, 30 = area_slices.LSE_MAX, 0 = off)."""
    b = cfg['boundary']
    if b.get('records', 'face') != 'slices':
        return None
    out = {'g': b['slices'], 'unnormalized': b['slice_read'] == 'both', 'mass_ref': b['mass_ref'],
           'assign': b.get('slice_assign', 'learned'), 'lse_max': b.get('slice_lse_max', 30.0),
           'pos_dims': space_dim}
    if b.get('near_radius', 0.0) > 0:                 # c102: the near-field read of the face records beside the tokens
        out.update(near_radius=b['near_radius'], near_k=b.get('near_k', 64))
    if b.get('outlet_slices', 0):                      # dcts, boundary.types = "role": the outlet column of the records
        from ...Main.dcts_common import BND_IN, ROLES
        out.update(outlet_slices=b['outlet_slices'], outlet_col=BND_IN + ROLES.index('server_outlet'))
    return out


def _slice(cfg, node_in, bnd_in, out_dim, space_dim=None):
    m, b, c = cfg['model'], cfg['boundary'], cfg['context']
    # channel_every / global_dim (A4, E4), global_dropout (d54), message_passing (c76, d54): .get, so a configuration
    # stored before the keys existed builds as before
    return SliceOperator(node_in, bnd_in, m['width'], m['depth'], m['heads'], m['slices'],
                         channel=b['channel'], channel_measure=b['channel_measure'],
                         global_route=b['global'], global_measure=b['global_measure'], eps=c['eps'],
                         lfe=_lfe(cfg), out_dim=out_dim, record_slices=_record_slices(cfg, space_dim),
                         channel_every=b.get('channel_every', 1), global_dim=b.get('global_dim', 0),
                         global_dropout=b.get('global_dropout', 0.0),
                         message_passing=m.get('message_passing', 0),
                         server_route=b.get('server_route', 'none') != 'none',      # B2 (dcts_inlets_2026-10-08)
                         record_read=b.get('record_read', 'normalized'), mass_ref=b.get('mass_ref', 1.0),
                         global_pool=b.get('global_pool', 'mean'))


def _transolver(cfg, node_in, bnd_in, out_dim, space_dim=None):
    m, b, c = cfg['model'], cfg['boundary'], cfg['context']
    return Transolver(node_in, bnd_in, m['width'], m['depth'], m['heads'], m['slices'], mlp_ratio=m['mlp_ratio'],
                      eps=c['eps'], channel=b['channel'], channel_measure=b['channel_measure'], out_dim=out_dim,
                      lfe=_lfe(cfg), record_slices=_record_slices(cfg, space_dim),
                      channel_init=b.get('channel_init', 'host'))


def _sca(cfg, node_in, bnd_in, out_dim):
    m, b, c = cfg['model'], cfg['boundary'], cfg['context']
    return SCA(node_in, bnd_in, m['width'], m['depth'], m['heads'], m['slices'], mlp_ratio=m['mlp_ratio'],
               eps=c['eps'], channel_measure=b['bridge_measure'], out_dim=out_dim)


def _plugin(cfg, geo):
    """The SCANO channel plug-in of the hosts without a channel of their own (GNOT, GeoTransolver, MGN; c95, d63)."""
    b = cfg['boundary']
    return {'channel': b['channel'], 'channel_measure': b['channel_measure'], 'channel_every': b.get('channel_every', 1),
            'record_slices': _record_slices(cfg, geo['space_dim']) if b['channel'] != 'none' else None}


def _gnot(cfg, node_in, bnd_in, out_dim, **geo):
    m, b = cfg['model'], cfg['boundary']
    return GNOT(node_in, bnd_in, m['width'], m['depth'], m['heads'], experts=m['experts'], mlp_ratio=m['mlp_ratio'],
                bridge_measure=b['bridge_measure'], space_dim=geo['space_dim'], out_dim=out_dim, **_plugin(cfg, geo))


def _geotransolver(cfg, node_in, bnd_in, out_dim, space_dim=None):
    m, b, c = cfg['model'], cfg['boundary'], cfg['context']
    return GeoTransolver(node_in, bnd_in, m['width'], m['depth'], m['heads'], m['slices'], mlp_ratio=m['mlp_ratio'],
                         eps=c['eps'], bridge_measure=b['bridge_measure'], out_dim=out_dim,
                         **_plugin(cfg, {'space_dim': space_dim}))


def _abupt(cfg, node_in, bnd_in, out_dim, **geo):
    m, b = cfg['model'], cfg['boundary']
    return ABUPT(node_in, bnd_in, m['width'], m['depth'], m['heads'], mlp_ratio=m['mlp_ratio'], channel=b['channel'],
                 channel_measure=b['channel_measure'], space_dim=geo['space_dim'], pos_range=geo['pos_range'],
                 out_dim=out_dim, record_slices=_record_slices(cfg, geo['space_dim']),
                 channel_pos=b.get('channel_pos', 'raw'), lfe=_lfe(cfg),
                 channel_rope_lmin=b.get('channel_rope_lmin', 0.0))


def _transolver_pp(cfg, node_in, bnd_in, out_dim):
    m, b, c = cfg['model'], cfg['boundary'], cfg['context']
    return TransolverPP(node_in, bnd_in, m['width'], m['depth'], m['heads'], m['slices'], mlp_ratio=m['mlp_ratio'],
                        eps=c['eps'], channel=b['channel'], channel_measure=b['channel_measure'], out_dim=out_dim)


def _mgn(cfg, node_in, bnd_in, out_dim, **geo):
    m = cfg['model']
    return MGN(node_in, bnd_in, m['width'], m['depth'], space_dim=geo['space_dim'], out_dim=out_dim, heads=m['heads'],
               **_plugin(cfg, geo))


def _beno(cfg, node_in, bnd_in, out_dim, **geo):
    """olbc: exactly the earlier construction (2-D, polar record order, the olbc columns). r0s (d58): 3-D, records in
    Hilbert order in the fixed coordinate box, the R0-S value columns and no source (zoo/beno.py, beno_columns)."""
    m = cfg['model']
    if geo['space_dim'] == 2:
        return BENO(node_in, bnd_in, m['width'], m['depth'], m['heads'], out_dim=out_dim)
    return BENO(node_in, bnd_in, m['width'], m['depth'], m['heads'], out_dim=out_dim, space_dim=geo['space_dim'],
                pos_range=geo['pos_range'], **beno_columns(cfg['dataset']['name']))


def _aerogto(cfg, node_in, bnd_in, out_dim, **geo):
    """AeroGTO host (d58): model.slices = its learned query tokens, mlp_ratio x width = its MLP hidden size;
    model.aerogto_init the host initialisation (c81 addendum A1: aaai or author)."""
    m, b = cfg['model'], cfg['boundary']
    return AeroGTO(node_in, bnd_in, m['width'], m['depth'], m['heads'], tokens=m['slices'], mlp_ratio=m['mlp_ratio'],
                   space_dim=geo['space_dim'], channel=b['channel'], channel_measure=b['channel_measure'],
                   out_dim=out_dim, record_slices=_record_slices(cfg, geo['space_dim']),
                   init=m.get('aerogto_init', 'aaai'),
                   box=geo['pos_range'] if m.get('aerogto_box', 'sample') == 'domain' else None)


BACKBONES = {'mgn': _mgn, 'slice': _slice, 'transolver': _transolver, 'transolver_pp': _transolver_pp, 'sca': _sca, 'gnot': _gnot,
             'geotransolver': _geotransolver, 'abupt': _abupt, 'beno': _beno, 'aerogto': _aerogto}


# read the first space_dim node columns as coordinates (beno: its record order and edge features, d58)
GEOMETRY_AWARE = {'abupt', 'gnot', 'mgn', 'aerogto', 'beno'}
# boundary.slice_assign = "spatial": positions = first space_dim columns (geotransolver: its channel plug-in, c95)
SLICE_HOSTS = {'slice', 'transolver', 'geotransolver'}


def build_model(cfg, node_in, bnd_in, space_dim=2, pos_range=(-1.0, 1.0), out_dim=1):
    """space_dim / pos_range (olbc defaults: 2, [-1, 1]) reach only the backbones that read coordinates; out_dim
    (default 1: output [B,N], as always) is the number of predicted fields (output [B,N,out_dim] when > 1)."""
    name = cfg['model']['backbone']
    if not (isinstance(out_dim, int) and out_dim >= 1):
        raise ValueError(f'out_dim must be a positive integer, got {out_dim!r}')
    if name in GEOMETRY_AWARE:
        return BACKBONES[name](cfg, node_in, bnd_in, out_dim, space_dim=space_dim, pos_range=tuple(pos_range))
    if name in SLICE_HOSTS:
        return BACKBONES[name](cfg, node_in, bnd_in, out_dim, space_dim=space_dim)
    return BACKBONES[name](cfg, node_in, bnd_in, out_dim)


__all__ = ['BACKBONES', 'attach_code_rng', 'build_model', 'count_parameters']
