"""The mesh graph of model.message_passing (the FVGN GN blocks of the SCANO trunk, zoo/fvgn_gn.py) on every dataset:
c76 on dcts, generalised to r0s and olbc for decision d54 (10-08: the SCANO trunk on every dataset is GN + slice
attention, the trunk without GN the ablation).

The GN blocks need one undirected edge per mesh pair (the two-way logic is inside the blocks: EdgeLift lifts the edge
and its reverse, the node block reads the first half of an edge's features where the node sends and the second half
where it receives) and per edge the raw features [offset sender - receiver (3), its length], in units of EDGE_REF.
gn_graph(data) gives them per dataset:

  dcts   the mesh faces of the current case as the cache stores them, one directed edge per face (owner cell ->
        neighbour cell, owner cell -> boundary-face node; GtcData.face_edges), positions = cell centroids and
        boundary-face centres in metres; EDGE_REF = 0.05 m (c76 probe: median edge length near the racks 4.5 cm).
        Exactly the c76 code (dcts-local ea93de7), moved here unchanged.
  r0s   the MESSAGE graph of mesh_learn.npz (message_edge_index: 95,232 cell-cell faces and 6,144 boundary face <->
        owner cell pairs, stored in both directions; 202,752 entries), deduplicated to one edge per node pair
        (101,376), oriented from the lower to the higher node index, except that a face edge always runs owner cell ->
        boundary-face node as on dcts (cells and faces are interleaved in the R0-S node order). Positions = the node
        centroids (cell centres, face centres) of the unit cube. EDGE_REF = 1/32, the nominal spacing of the 32^3
        learning mesh: its graded cells give a median edge length of 0.0309 (cell-cell median 0.0309, face-owner
        median 0.0051, the half-width of the wall cells; probe of 10-08), so a typical cell-cell edge has length
        feature ~1. It is a constant of the dataset, not of the mesh: on the D4 meshes (16^3, reverse-graded 40^3,
        64^3) the same physical offset gives the same feature, so the GN reads the change of resolution as what it
        is (descriptive in the stage-1 rule). gn-d4 (10-08): those D4 inputs lie outside the training range; the
        evaluation-time switch set_edge_norm (default off) maps them back (section at the end of this module).
  olbc  cpd|neighbor_cell (stored in both directions; 146,522 entries), deduplicated to 73,261 pairs, oriented from
        the lower to the higher node index (all nodes are cells); positions (x, y, 0) of the 16,859 cell centroids;
        EDGE_REF = 0.014, the median edge length of the olbc mesh (0.0143, probe of 10-08) to two digits
        (tests/test_gn_main.py checks both constants against the stored meshes).

Fixed constants rather than a median computed at run time: the scale is part of the model's input definition, so it
must not move with the mesh a run is deployed on (D4) or with a cache rebuild; the probe that justifies each value is
in the test. The deduplication keeps every pair once, so a node's neighbour sums count each neighbour once, as on
dcts (one edge per face) and as FVGN's undirected face list; the bidirectional lists would count every neighbour
twice and give every node two copies of each edge.

gn_edges(data, query) induces the graph on the query points; gn_halo(data, nodes, hops) is the deployment halo that
makes the GN neighbourhoods of `nodes` complete (h = 3 x message_passing x depth hops, zoo/fvgn_gn.HOPS_PER_BLOCK).
predict_on (cfg_common) queries a support set with the context of `nodes` (coupled) or the anchors (decoupled).
"""
from __future__ import annotations

import numpy as np
import torch

from ..Datasets.refine_sampling import khop_nodes

EDGE_REF = {'dcts': 0.05, 'r0s': 1.0 / 32.0, 'olbc': 0.014}


def dataset_of(data):
    """'dcts', 'r0s' or 'olbc' for a data object of the framework (GtcData, R0SData, OlbcData)."""
    from .dcts_common import GtcData
    from .olbc_common import OlbcData
    from .r0s_common import R0SData
    if isinstance(data, GtcData):
        return 'dcts'
    if isinstance(data, R0SData):
        return 'r0s'
    if isinstance(data, OlbcData):
        return 'olbc'
    raise TypeError(f'model.message_passing: no GN graph for data objects of type {type(data).__name__}')


def undirected(edge_index, n):
    """One edge per node pair of an edge list (either or both directions stored; self-loops dropped), oriented from
    the lower to the higher node index, sorted by (sender, receiver): [2, E] long on edge_index's device."""
    a, b = edge_index[0], edge_index[1]
    lo, hi = torch.minimum(a, b), torch.maximum(a, b)
    keep = lo != hi
    key = torch.unique(lo[keep] * n + hi[keep])           # sorted
    return torch.stack([key // n, key % n])


class GnGraph:
    """edges [2, E] (one per pair), pos [N, 3] (the centroids, float32), edge_ref (float), on the data object's device;
    scale: None (the trained definition), or [E, 1] float32 factors of the deployment-time edge normalisation
    (set_edge_norm, gn-d4)."""

    def __init__(self, edges, pos, edge_ref, scale=None):
        self.edges, self.pos, self.edge_ref, self.scale = edges, pos, edge_ref, scale


def gn_graph(data):
    """The GN graph of `data` (of its current case on dcts). r0s / olbc: built once per data object and cached on it
    (a D4 mesh of R0-S is its own data object with its own graph). With a deployment-time edge normalisation
    (set_edge_norm, evaluation only) the same edges with per-edge factors, cached per normalisation."""
    kind = dataset_of(data)
    if kind == 'dcts':                                   # per-case meshes: the c76 arrays, nothing cached here
        return GnGraph(data.face_edges, data._cur().pos_device, EDGE_REF['dcts'])
    g = data.__dict__.get('_gn_graph')
    if g is None:
        edges = undirected(data.edge_index, data.n)
        if kind == 'r0s':                               # a face edge runs owner cell -> boundary-face node (dcts)
            flip = (data.kind[edges[0]] != 0) & (data.kind[edges[1]] == 0)
            edges = torch.where(flip[None], edges.flip(0), edges)
        pos = data.centroid_cpu.to(data.edge_index.device)          # float32 as the cache stores it
        g = data._gn_graph = GnGraph(edges, pos, EDGE_REF[kind])
    norm = data.__dict__.get('gn_edge_norm')
    if norm is None:                                    # the default: the trained definition, unchanged
        return g
    cache = data.__dict__.setdefault('_gn_graph_norm', {})
    if norm not in cache:
        scale = _edge_scale(data, g, norm)
        cache[norm] = g if scale is None else GnGraph(g.edges, g.pos, g.edge_ref, scale)
    return cache[norm]


def gn_edges(data, query):
    """(edges [2, E], raw [E, 4]) of model.message_passing for the query points `query` (distinct node indices of the
    current case): the GN graph's edges with both ends among them, relabelled to positions in `query`, and the raw
    edge features [offset sender - receiver, its length] / EDGE_REF of the dataset (with a deployment-time edge
    normalisation, set_edge_norm: the offset times the edge's factor, and its length)."""
    from torch_geometric.utils import subgraph
    g = gn_graph(data)
    if g.scale is None:
        edges = subgraph(query, g.edges, relabel_nodes=True, num_nodes=data.n)[0]
        pos = g.pos[query]
        rel = (pos[edges[0]] - pos[edges[1]]) / g.edge_ref
        return edges, torch.cat([rel, torch.linalg.vector_norm(rel, dim=1, keepdim=True)], 1)
    edges, scale = subgraph(query, g.edges, edge_attr=g.scale, relabel_nodes=True, num_nodes=data.n)
    pos = g.pos[query]
    rel = (pos[edges[0]] - pos[edges[1]]) / g.edge_ref * scale
    return edges, torch.cat([rel, torch.linalg.vector_norm(rel, dim=1, keepdim=True)], 1)


# ---------------------------------------------------------------------------------------------------------
# deployment-time edge normalisation (gn-d4, 10-08; evaluation only, default off)
# ---------------------------------------------------------------------------------------------------------
# The GN edge features are offset / EDGE_REF with EDGE_REF a constant of the dataset (1/32 on R0-S). On the learning
# mesh every face-owner edge has length 0.163 and the cell-cell lengths cover 0.36-1.65; on the D4 meshes the same
# definition gives face-owner 0.356 (coarse, reverse) or 0.078 (truth) and coarse cell-cell lengths near 2: inputs
# the edge lift never saw. set_edge_norm maps a deployment mesh's edges into the learning mesh's feature range at
# evaluation time (the trained weights and the learning mesh's features are unchanged):
#   dataset      the trained definition (the default; set_edge_norm removes any other)
#   nominal      EDGE_REF scaled by the deployment mesh's nominal spacing: offset x n_deploy / (n_learn EDGE_REF)
#                (n = cells per axis: coarse 16, reverse 40, truth 64; learning 32)
#   local        per edge, offset x h_learn / h_deploy, the ratio of the learning mesh's to the deployment mesh's
#                local spacing along the edge (R0-S edges are axis-aligned): a cell-cell edge at the deployment face
#                line x has length h_learn(x) / EDGE_REF (h_learn(x) = the learning mesh's centre distance across
#                x, linear between its interior lines: inside the learning cell-cell range); a face-owner edge has
#                the learning mesh's wall-cell value (half the learning wall cell, 0.163)
#   local_faces  'local' on the face-owner edges only (cell-cell edges as trained)      } localisation of the
#   local_cells  'local' on the cell-cell edges only (face-owner edges as trained)      } cause (diagnostic)
#   factor:<s>   every edge's offset times s (diagnostic: the learning mesh with D4-like inputs)
# On the learning mesh itself nominal and local* are the identity by construction (no factor is applied).
EDGE_NORMS = ('dataset', 'nominal', 'local')
EDGE_NORMS_DIAGNOSTIC = ('local_faces', 'local_cells', 'factor:<s>')


def check_edge_norm(norm):
    """`norm` if it names a deployment-time edge normalisation (EDGE_NORMS, EDGE_NORMS_DIAGNOSTIC), else ValueError."""
    if norm in EDGE_NORMS or norm in ('local_faces', 'local_cells'):
        return norm
    if isinstance(norm, str) and norm.startswith('factor:'):
        try:
            s = float(norm.split(':', 1)[1])
        except ValueError:
            s = float('nan')
        if s > 0:
            return norm
    raise ValueError(f'unknown GN edge normalisation {norm!r}: choose from {EDGE_NORMS + EDGE_NORMS_DIAGNOSTIC}')


def set_edge_norm(data, norm, lines=None, learn_lines=None):
    """Evaluation-time switch of the GN edge features of an R0-S data object (gn-d4). `lines`: the mesh lines of
    `data`'s mesh (one axis; R0-S meshes are tensor-product meshes with the same lines on every axis), `learn_lines`:
    the learning mesh's (both needed by every normalisation but 'dataset' and 'factor:<s>'). 'dataset' restores the
    trained definition."""
    check_edge_norm(norm)
    if norm == 'dataset':
        data.__dict__.pop('gn_edge_norm', None)
        return
    if dataset_of(data) != 'r0s':
        raise ValueError(f'GN edge normalisation {norm!r} is defined for the R0-S mesh family only (olbc has one mesh; '
                         'dcts trains on per-case meshes with one EDGE_REF)')
    if not norm.startswith('factor:'):
        if lines is None or learn_lines is None:
            raise ValueError(f'GN edge normalisation {norm!r} needs the mesh lines of the data and of the learning mesh')
        data.gn_mesh_lines = np.asarray(lines, np.float64)
        data.gn_learn_lines = np.asarray(learn_lines, np.float64)
    data.gn_edge_norm = norm


def _centre_distances(lines):
    """(interior line positions, centre distance across each: the mean width of its two cells) of a mesh's lines."""
    w = np.diff(lines)
    return lines[1:-1], 0.5 * (w[:-1] + w[1:])


def edge_spacing_ratio(data, g):
    """[E] float64 h_learn / h_deploy of every edge of the R0-S GN graph g on data's mesh, and [E] bool face-owner
    mask. Cell-cell edges: x = the deployment line between the two cells (the line nearest the centres' midpoint),
    h_deploy = the centre distance across it, h_learn = the learning mesh's centre distance at x, linear between the
    learning mesh's interior lines (exact on a learning line; the end values beyond them), so the edge's length
    feature stays inside the learning mesh's cell-cell range. Face-owner edges: the wall cell's width on each mesh."""
    lines, learn = data.gn_mesh_lines, data.gn_learn_lines
    pos = g.pos.detach().cpu().double().numpy()
    s, r = (t.cpu().numpy() for t in g.edges)
    axis = np.abs(pos[s] - pos[r]).argmax(1)
    face = (data.kind[g.edges[1]] != 0).cpu().numpy()                   # face edges run owner cell -> face node
    ratio = np.empty(len(s))
    cc = ~face
    mid = 0.5 * (pos[s[cc], axis[cc]] + pos[r[cc], axis[cc]])
    j = np.clip(np.searchsorted(lines, mid), 1, len(lines) - 1)
    near = np.where(np.abs(lines[j - 1] - mid) < np.abs(lines[j] - mid), j - 1, j)
    if ((near < 1) | (near > len(lines) - 2)).any():
        raise ValueError('edge_spacing_ratio: a cell-cell edge does not cross an interior mesh line')
    _, cd = _centre_distances(lines)
    xl, cl = _centre_distances(learn)
    ratio[cc] = np.interp(lines[near], xl, cl) / cd[near - 1]
    wall_hi = pos[r[face], axis[face]] > 0.5                           # the face node's wall: low or high end
    w, wl = np.diff(lines), np.diff(learn)
    ratio[face] = np.where(wall_hi, wl[-1] / w[-1], wl[0] / w[0])
    return ratio, face


def _edge_scale(data, g, norm):
    """[E, 1] float32 factors of a deployment-time edge normalisation on data's mesh, or None for the identity (the
    learning mesh under nominal / local*)."""
    if norm.startswith('factor:'):
        return torch.full((g.edges.shape[1], 1), float(norm.split(':', 1)[1]), device=g.edges.device)
    lines, learn = data.gn_mesh_lines, data.gn_learn_lines
    if np.array_equal(lines, learn):
        return None
    if norm == 'nominal':
        f = np.full(g.edges.shape[1], (len(lines) - 1) / (len(learn) - 1))
    else:
        ratio, face = edge_spacing_ratio(data, g)
        f = ratio if norm == 'local' else np.where(face if norm == 'local_faces' else ~face, ratio, 1.0)
    return torch.tensor(f, dtype=torch.float32, device=g.edges.device)[:, None]


def gn_halo(data, nodes, hops):
    """Sorted node ids of `nodes` and every node within `hops` mesh hops of them on the current case: the deployment
    halo that makes the GN neighbourhoods of `nodes` complete (read the nodes' predictions at
    torch.searchsorted(halo, nodes)). Hops are counted on data.edge_index, the message graph stored in both directions
    (dcts: the faces both ways; r0s: message_edge_index; olbc: cpd|neighbor_cell), whose node pairs are the GN graph's
    (tests/test_gn_main.py)."""
    out = khop_nodes(nodes, hops, data.edge_index, data.n)
    if not torch.equal(out[torch.searchsorted(out, nodes)], nodes):
        raise ValueError('gn_halo: the halo lost a node of the subgraph')
    return out
