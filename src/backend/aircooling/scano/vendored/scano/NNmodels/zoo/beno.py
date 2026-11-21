"""BENO, Boundary-Embedded Neural Operators (Wang, Li, Dwivedi, Hara, Wu; ICLR 2024), adapted from the
MIT-licensed upstream (AI4Science-WestlakeU/beno@6ac99f3, BE_MPNN.py and transformer.py; verbatim copies and
licence in scano/NNmodels/vendor/beno/, not imported here).

Structure as upstream HeteroGNS: two message-passing branches from a Green's-function split of a linear elliptic
problem -- branch 1 (upstream G1) sees the source with the boundary values zeroed, branch 2 (G2) sees zero source
with the boundary values. Each branch has its own encoder, `depth` InteractionNetwork blocks and decoder, and the
prediction is the sum of the two decoded fields. Every block carries its own boundary Transformer (Conv1d token
embedding with circular padding plus a sinusoidal position encoding by record order, full softmax self-attention,
global max pooling over the records); the pooled vector is repeated and concatenated to every node's update input.

The superposition u = A(source) + B(boundary values) is exact only for linear PDEs. It holds for the olbc Poisson
problem; for non-linear PDEs (RANS, buoyant flow) the split is not valid and BENO is at best an ansatz there.

Framework interface: model(node_feat [1,N,F], ctx_w [1,N], bnd_feat [1,NB,Fb], bnd_w [1,NB], edges [2,E]) -> [1,N],
with `edges` the mesh edges induced on the passed points (needs_edges = True). There is no global state over the
interior points: ctx_w and bnd_w are not used, and the boundary path always sees the full record set, so the
prediction depends on the sample only through the truncated message-passing neighbourhoods.

Inputs on olbc (scano.Main.olbc_common): node features [x, y, udf, source, is_boundary, onehot(3), g, alpha],
boundary records [x, y, nx, ny, onehot(3), g, alpha]. Branch 1 zeroes g (node column 8, record column 7) and keeps
the type and alpha, which define the operator rather than the data; branch 2 zeroes the source (node column 3 and
the source on the edges). The column indices are constructor arguments.

Changes to upstream, and nothing else (tests/test_zoo_beno.py checks parity with the vendored code):
  1. the boundary tokens are all Fb record columns (upstream: 3 = x, y, g); with 3-column records this is
     upstream's enc_in = 3;
  2. the records are sorted inside the model by polar angle about their centroid (unweighted mean position,
     summed in sorted order so that it does not depend on the record order), starting at -pi, ties broken by
     radius; records with equal angle and radius keep their input order. Upstream's records arrive in order along the closed curve and its
     encoder depends on that order (circular Conv1d, position encoding by index); ours arrive in mesh order (the
     olbc `bidx` is not along the circle). The sort makes the output invariant to the record order. It follows
     the curve for domains that are star-shaped about the centroid (the olbc disc); it is not defined in 3D.
     For records already in angular order starting at -pi it is the identity;
  3. node features are the framework's: udf replaces upstream's structured-grid dist2bd_x/y, and upstream's
     smoothed source and its gradients are not added (the olbc source is shared by all cases). Edge features are
     upstream's [pos_s, pos_r, source_s, source_r, |pos_s - pos_r|] (7 in 2-D) on the passed edges, sender =
     edges[0], receiver = edges[1] (PyG flow). The graph is the framework's induced mesh graph, as for mgn, not
     upstream's kNN(10) + self-loops + Delaunay edges;
  4. branch 2's edge features carry zero source. Upstream's train.py builds one edge_attr (with the source at both
     ends) and gives it to both G1 and G2, so its branch 2 still sees the source through the edges; zeroing it is
     the split the paper states, and it makes u = A(source) + B(g) structural. This is a data-side change; the
     network is upstream's;
  5. dead state dropped: upstream's DataEmbedding_wo_temp builds four frozen temporal-embedding tables that its
     forward never uses (the same random numbers are still drawn at construction, so the same seed gives the same
     parameters), and a 5000 x d_model position-encoding buffer, here computed on the fly with the same float32
     operations (no 5000-record cap). Upstream checkpoints load after dropping those keys;
  6. PyG MessagePassing replaced by explicit gathers and a mean over incoming edges (a node without incoming edges
     aggregates 0, as in PyG); one case per forward, as upstream (batch 1);
  7. 3-D (R0-S, decision d58, 10-08; olbc is untouched): a closed surface has no curve order, so the records are put
     in the order of a Hilbert curve through the fixed coordinate box (pos_range, the dataset's; hilbert_order, 2^16
     cells per axis, records in one cell by their columns), a locality-preserving one-dimensional order that does not
     depend on the input order or on the other records. The circular Conv1d then mixes records that are mostly spatial
     neighbours; where the curve leaves the surface and comes back, neighbours in the sequence can be far apart (the
     full self-attention and the max pooling after it do not depend on the order). Columns (COLUMNS): R0-S has no
     volume source, so branch 2 zeroes nothing, the edges carry zero source on both branches, and branch 1 sees the
     geometry, the types and alpha with the values zeroed (node column 8, record column 9). The R0-S problem is a
     linear convection-diffusion operator with boundary data only, so u = A(0) + B(g) is still the split the paper
     states; on DCTS (buoyant RANS) superposition does not hold and BENO is not run (run_config rejects it).

Kept as upstream although unusual: an InteractionNetwork never updates the edge state. The edge MLP output is only
the message, and PyG hands the *input* edge features to update(), so a block returns e + e: the edge features
double at every block. The boundary Transformer's FFN width is 16 whatever d_model; the MLPs have 3 hidden layers
and SiLU, the setting upstream's train.py runs with (its parse_args([]) branch always applies).

Config mapping: width = latent = MLP hidden = boundary_dim (all 128 upstream), depth = message-passing blocks per
branch (10 upstream), heads = boundary Transformer heads (2 upstream). width 52, depth 5, heads 2 gives 786,826
parameters on olbc (the c37 channel model has 786,321); the upstream size (128/10/2, 3-column records) gives
8,542,402 trainable parameters, as upstream.
"""
from __future__ import annotations

import math

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

NMLP_LAYERS = 3                   # train.py: nmlp_layers
TRANS_LAYERS = 3                  # train.py: trans_layer
D_FF = 16                         # upstream Transformer default d_ff (InteractionNetwork does not set it)
TEMPORAL_TABLES = (24, 7, 32, 13)  # hour, weekday, day, month tables of upstream's DataEmbedding_wo_temp
HILBERT_BITS = 16                 # 3-D record order (change 7): 2^16 cells per axis of the coordinate box

# Columns of the two datasets BENO runs on (d58): the source columns zeroed for branch 2, the node column whose value
# sits on the edges (None = no source: zeros), the boundary-value columns of the nodes and of the records zeroed for
# branch 1. olbc = the constructor defaults (cfg_common.DATASETS / olbc_common); r0s = r0s_common: node [x, y, z,
# d_w, is_face, one-hot type (3), value, alpha], record [x, y, z, nx, ny, nz, one-hot type (3), value, alpha], no
# volume source (the R0-S problem is driven by its boundary data alone).
COLUMNS = {'olbc': {'source_cols': (3,), 'edge_source_col': 3, 'node_value_cols': (8,), 'record_value_cols': (7,)},
           'r0s': {'source_cols': (), 'edge_source_col': None, 'node_value_cols': (8,), 'record_value_cols': (9,)}}


def beno_columns(dataset):
    """Constructor keyword arguments of the dataset's columns (COLUMNS); dcts has none (run_config rejects BENO there)."""
    if dataset not in COLUMNS:
        raise ValueError(f'BENO has no column map for dataset {dataset!r} (olbc and r0s only, decision d58)')
    return dict(COLUMNS[dataset])


def hilbert_keys(cells, bits):
    """Hilbert-curve index of integer points cells [N, d] (0 <= cells < 2^bits), int64 [N]: Skilling's transform of
    the axes to the transposed index ("Programming the Hilbert curve", AIP Conf. Proc. 707, 2004), then its bits
    interleaved from the top level down (d x bits <= 62)."""
    x = cells.astype(np.int64).copy()
    n = x.shape[1]
    m = 1 << (bits - 1)
    q = m
    while q > 1:                                        # inverse undo
        p = q - 1
        for i in range(n):
            hit = (x[:, i] & q) != 0
            x[hit, 0] ^= p                              # invert the low bits of axis 0
            t = (x[~hit, 0] ^ x[~hit, i]) & p           # or exchange the low bits of axes 0 and i
            x[~hit, 0] ^= t
            x[~hit, i] ^= t
        q >>= 1
    for i in range(1, n):                               # Gray encode
        x[:, i] ^= x[:, i - 1]
    t = np.zeros(len(x), np.int64)
    q = m
    while q > 1:
        t[(x[:, n - 1] & q) != 0] ^= q - 1
        q >>= 1
    x ^= t[:, None]
    key = np.zeros(len(x), np.int64)
    for b in range(bits - 1, -1, -1):
        for i in range(n):
            key = (key << 1) | ((x[:, i] >> b) & 1)
    return key


def hilbert_order(records, space_dim, pos_range, bits=HILBERT_BITS):
    """Permutation of the records [NB, C] along the Hilbert curve of their positions (the first space_dim columns)
    quantised in the fixed box pos_range^space_dim (not the records' own bounding box, so a record's key does not
    depend on the others). Records in the same cell are ordered by all their columns (lexicographic), so the result is
    the same for any input order; identical records are interchangeable. Computed on the CPU in float64."""
    r = records.detach().to('cpu', torch.float64).numpy()
    lo, hi = pos_range
    cells = np.clip(np.floor((r[:, :space_dim] - lo) / (hi - lo) * (1 << bits)), 0, (1 << bits) - 1)
    order = np.lexsort(r.T[::-1])                       # by column 0, then 1, ... (lexsort sorts by the last key)
    order = order[np.argsort(hilbert_keys(cells[order], bits), kind='stable')]
    return torch.as_tensor(order, device=records.device)


def build_mlp(input_size, hidden_layer_sizes, output_size=None, output_activation=nn.Identity,
              activation=nn.ReLU):
    """upstream build_mlp (same module names and creation order)."""
    layer_sizes = [input_size] + list(hidden_layer_sizes)
    if output_size:
        layer_sizes.append(output_size)
    nlayers = len(layer_sizes) - 1
    act = [activation for _ in range(nlayers)]
    act[-1] = output_activation
    mlp = nn.Sequential()
    for i in range(nlayers):
        mlp.add_module('NN-' + str(i), nn.Linear(layer_sizes[i], layer_sizes[i + 1]))
        mlp.add_module('Act-' + str(i), act[i]())
    return mlp


def _mlp_ln(in_dim, hidden, out_dim, nmlp, activation):
    return nn.Sequential(build_mlp(in_dim, [hidden] * nmlp, out_dim, activation=activation), nn.LayerNorm(out_dim))


def position_encoding(length, d_model, device=None):
    """Rows 0..length-1 of upstream's PositionalEmbedding buffer, with the same float32 operations."""
    position = torch.arange(0, length, device=device).float().unsqueeze(1)
    div_term = (torch.arange(0, d_model, 2, device=device).float() * -(math.log(10000.0) / d_model)).exp()
    pe = torch.zeros(length, d_model, device=device)
    pe[:, 0::2] = torch.sin(position * div_term)
    pe[:, 1::2] = torch.cos(position * div_term)
    return pe[None]


class TokenEmbedding(nn.Module):
    def __init__(self, c_in, d_model):
        super().__init__()
        self.tokenConv = nn.Conv1d(in_channels=c_in, out_channels=d_model, kernel_size=3, padding=1,
                                   padding_mode='circular', bias=False)
        nn.init.kaiming_normal_(self.tokenConv.weight, mode='fan_in', nonlinearity='leaky_relu')

    def forward(self, x):
        return self.tokenConv(x.permute(0, 2, 1)).transpose(1, 2)


class DataEmbedding(nn.Module):
    """upstream DataEmbedding_wo_temp: token embedding + position encoding (dropout 0)."""

    def __init__(self, c_in, d_model):
        super().__init__()
        self.d_model = d_model
        self.value_embedding = TokenEmbedding(c_in, d_model)
        for rows in TEMPORAL_TABLES:        # upstream's unused nn.Embedding tables draw these at init (change 5)
            torch.empty(rows, d_model).normal_()

    def forward(self, x):
        return self.value_embedding(x) + position_encoding(x.shape[1], self.d_model, x.device)


class AttentionLayer(nn.Module):
    """upstream AttentionLayer around FullAttention(mask_flag=False), dropout 0."""

    def __init__(self, d_model, n_heads):
        super().__init__()
        d_keys = d_model // n_heads
        self.query_projection = nn.Linear(d_model, d_keys * n_heads)
        self.key_projection = nn.Linear(d_model, d_keys * n_heads)
        self.value_projection = nn.Linear(d_model, d_keys * n_heads)
        self.out_projection = nn.Linear(d_keys * n_heads, d_model)
        self.n_heads = n_heads

    def forward(self, x):
        B, L, _ = x.shape
        H = self.n_heads
        q = self.query_projection(x).view(B, L, H, -1)
        k = self.key_projection(x).view(B, L, H, -1)
        v = self.value_projection(x).view(B, L, H, -1)
        scale = 1. / math.sqrt(q.shape[-1])
        a = torch.softmax(scale * torch.einsum('blhe,bshe->bhls', q, k), dim=-1)
        out = torch.einsum('bhls,bshd->blhd', a, v).contiguous().view(B, L, -1)
        return self.out_projection(out)


class EncoderLayer(nn.Module):
    """upstream EncoderLayer (post-norm, FFN as two kernel-1 Conv1d, gelu, dropout 0)."""

    def __init__(self, d_model, n_heads, d_ff):
        super().__init__()
        self.attention = AttentionLayer(d_model, n_heads)
        self.conv1 = nn.Conv1d(in_channels=d_model, out_channels=d_ff, kernel_size=1)
        self.conv2 = nn.Conv1d(in_channels=d_ff, out_channels=d_model, kernel_size=1)
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)

    def forward(self, x):
        x = x + self.attention(x)
        y = x = self.norm1(x)
        y = F.gelu(self.conv1(y.transpose(-1, 1)))
        y = self.conv2(y).transpose(-1, 1)
        return self.norm2(x + y)


class TransformerEncoder(nn.Module):
    def __init__(self, d_model, n_heads, d_ff, n_layers):
        super().__init__()
        self.attn_layers = nn.ModuleList([EncoderLayer(d_model, n_heads, d_ff) for _ in range(n_layers)])
        self.norm = nn.LayerNorm(d_model)

    def forward(self, x):
        for layer in self.attn_layers:
            x = layer(x)
        return self.norm(x)


class BoundaryTransformer(nn.Module):
    """upstream Transformer: records [1, L, C] in the given order -> global max over the records [1, d_model]."""

    def __init__(self, enc_in, d_model, n_heads=2, enc_layers=TRANS_LAYERS, d_ff=D_FF):
        super().__init__()
        self.enc_embedding = DataEmbedding(enc_in, d_model)
        self.encoder = TransformerEncoder(d_model, n_heads, d_ff, enc_layers)

    def forward(self, tokens):
        return self.encoder(self.enc_embedding(tokens)).max(dim=1).values


class InteractionNetwork(nn.Module):
    """upstream InteractionNetwork: message from [x_receiver, x_sender, e], mean at the receiver, node update from
    [aggregate, x, pooled boundary], residual on the nodes; the edge state is returned doubled (see docstring)."""

    def __init__(self, latent, hidden, boundary_dim, bnd_in, heads, nmlp, trans_layers, activation):
        super().__init__()
        self.node_fn = _mlp_ln(latent + latent + boundary_dim, hidden, latent, nmlp, activation)
        self.edge_fn = _mlp_ln(3 * latent, hidden, latent, nmlp, activation)
        self.boundary_fn = BoundaryTransformer(bnd_in, boundary_dim, heads, trans_layers)

    def forward(self, x, edge_index, e, boundary):
        b = self.boundary_fn(boundary)                                              # [1, boundary_dim]
        s, r = edge_index[0], edge_index[1]
        msg = self.edge_fn(torch.cat([x[r], x[s], e], dim=-1))
        agg = x.new_zeros(x.shape[0], msg.shape[1]).index_add_(0, r, msg)
        deg = x.new_zeros(x.shape[0]).index_add_(0, r, x.new_ones(r.shape[0]))
        agg = agg / deg.clamp(min=1)[:, None]
        x_new = self.node_fn(torch.cat([agg, x, b.expand(x.shape[0], -1)], dim=-1))
        return x_new + x, e + e


class Encoder(nn.Module):
    def __init__(self, node_in, edge_in, latent, hidden, nmlp, activation):
        super().__init__()
        self.node_fn = _mlp_ln(node_in, hidden, latent, nmlp, activation)
        self.edge_fn = _mlp_ln(edge_in, hidden, latent, nmlp, activation)
        self.node_fn_inbd = _mlp_ln(node_in, hidden, latent, nmlp, activation)
        self.edge_fn_inbd = _mlp_ln(edge_in, hidden, latent, nmlp, activation)


class Processor(nn.Module):
    def __init__(self, latent, hidden, boundary_dim, bnd_in, heads, steps, nmlp, trans_layers, activation):
        super().__init__()
        args = (latent, hidden, boundary_dim, bnd_in, heads, nmlp, trans_layers, activation)
        self.gnn_stacks = nn.ModuleList([InteractionNetwork(*args) for _ in range(steps)])
        self.gnn_stacks_inbd = nn.ModuleList([InteractionNetwork(*args) for _ in range(steps)])


class Decoder(nn.Module):
    def __init__(self, latent, out_dim, hidden, nmlp, activation):
        super().__init__()
        self.node_fn = build_mlp(latent, [hidden] * nmlp, out_dim, activation=activation)
        self.node_fn_inbd = build_mlp(latent, [hidden] * nmlp, out_dim, activation=activation)


class BENO(nn.Module):
    needs_edges = True

    def __init__(self, node_in, bnd_in, width=52, depth=5, heads=2, out_dim=1, space_dim=2,
                 source_cols=(3,), edge_source_col=3, node_value_cols=(8,), record_value_cols=(7,),
                 nmlp_layers=NMLP_LAYERS, trans_layers=TRANS_LAYERS, activation=nn.SiLU, pos_range=None):
        super().__init__()
        if space_dim not in (2, 3):
            raise ValueError('BENO orders the boundary records by polar angle (2-D) or along a Hilbert curve (3-D)')
        if space_dim == 3 and pos_range is None:
            raise ValueError('BENO in 3-D orders the records in the fixed coordinate box pos_range: pass it')
        if width % 2 or width % heads:
            raise ValueError('BENO needs an even width (sinusoidal position encoding) divisible by heads')
        self.space_dim = space_dim
        self.pos_range = None if pos_range is None else (float(pos_range[0]), float(pos_range[1]))
        self.source_cols, self.edge_source_col = tuple(source_cols), edge_source_col
        self.node_value_cols, self.record_value_cols = tuple(node_value_cols), tuple(record_value_cols)
        edge_in = 2 * space_dim + 3
        self._encoder = Encoder(node_in, edge_in, width, width, nmlp_layers, activation)
        self._processor = Processor(width, width, width, bnd_in, heads, depth, nmlp_layers, trans_layers, activation)
        self._decoder = Decoder(width, out_dim, width, nmlp_layers, activation)

    # -- upstream network ------------------------------------------------------------------------------------
    def _branches(self, x, edge_index, edge_features, boundary, x_inbd, edge_inbd_index, edge_inbd_features,
                  boundary_inbd):
        enc = self._encoder
        x, e = enc.node_fn(x), enc.edge_fn(edge_features)
        x_inbd, e_inbd = enc.node_fn_inbd(x_inbd), enc.edge_fn_inbd(edge_inbd_features)
        b, b_inbd = boundary[None].float(), boundary_inbd[None].float()
        for gnn in self._processor.gnn_stacks:
            x, e = gnn(x, edge_index, e, b)
        for gnn in self._processor.gnn_stacks_inbd:
            x_inbd, e_inbd = gnn(x_inbd, edge_inbd_index, e_inbd, b_inbd)
        return self._decoder.node_fn(x), self._decoder.node_fn_inbd(x_inbd)

    def hetero_forward(self, x, edge_index, edge_features, boundary, x_inbd, edge_inbd_index, edge_inbd_features,
                       boundary_inbd):
        """upstream HeteroGNS.forward on its G1 / G2 tensors (records [NB, C] in the given order) -> [N, out]."""
        u1, u2 = self._branches(x, edge_index, edge_features, boundary, x_inbd, edge_inbd_index,
                                edge_inbd_features, boundary_inbd)
        return u1 + u2

    # -- framework side --------------------------------------------------------------------------------------
    def boundary_order(self, records):
        """Permutation putting the records in polar-angle order about their centroid, ties by radius (change 2).
        The centroid is summed in sorted order, so it is bitwise the same for any record order: otherwise a record
        on the branch cut (angle pi) can jump between the ends of the sequence with the summation-order noise.
        3-D (change 7): the order along a Hilbert curve of the record positions in the box pos_range."""
        if self.space_dim == 3:
            return hilbert_order(records, self.space_dim, self.pos_range)
        p = records[:, :2]
        d = p - p.sort(dim=0).values.mean(0)
        by_radius = torch.argsort(d.norm(dim=-1), stable=True)
        angle = torch.atan2(d[by_radius, 1], d[by_radius, 0])
        return by_radius[torch.argsort(angle, stable=True)]

    @staticmethod
    def edge_features(pos, source, edges):
        """upstream MeshGenerator.attributes: [pos_s, pos_r, source_s, source_r, |pos_s - pos_r|]."""
        s, r = edges[0], edges[1]
        dist = (pos[s] - pos[r]).norm(dim=-1, keepdim=True)
        return torch.cat([pos[s], pos[r], source[s, None], source[r, None], dist], dim=-1)

    def branches(self, node_feat, bnd_feat, edges):
        """(u1, u2), each [N] ([N, out_dim] when out_dim > 1): branch 1 (source, BC values zeroed) and branch 2
        (BC values, source zeroed)."""
        if node_feat.shape[0] != 1:
            raise ValueError('BENO runs one case per forward')
        x = node_feat[0]
        rec = bnd_feat[0][self.boundary_order(bnd_feat[0])]
        x1, x2, rec1 = x.clone(), x.clone(), rec.clone()
        if self.node_value_cols:
            x1[:, list(self.node_value_cols)] = 0.0
        if self.source_cols:
            x2[:, list(self.source_cols)] = 0.0
        if self.record_value_cols:
            rec1[:, list(self.record_value_cols)] = 0.0
        pos = x[:, :self.space_dim]
        # no source column (R0-S, d58: the PDE has no volume source): the source entries of the edges are zero
        source = x[:, self.edge_source_col] if self.edge_source_col is not None else x.new_zeros(x.shape[0])
        e1 = self.edge_features(pos, source, edges)
        e2 = self.edge_features(pos, torch.zeros_like(source), edges)          # change 4
        u1, u2 = self._branches(x1, edges, e1, rec1, x2, edges, e2, rec)
        if u1.shape[-1] != 1:
            return u1, u2
        return u1.squeeze(-1), u2.squeeze(-1)

    def forward(self, node_feat, ctx_w=None, bnd_feat=None, bnd_w=None, edges=None):
        u1, u2 = self.branches(node_feat, bnd_feat, edges)
        return (u1 + u2)[None]
