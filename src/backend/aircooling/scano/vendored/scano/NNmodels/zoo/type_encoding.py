"""How the boundary type reaches the model on R0-S (boundary.types, boundary.type_encoding; c57, the type ablation A7;
PLAN section 3.2 "type encoding as an interface: one-hot (closed vocabulary) and a frozen text embedding (open
vocabulary)", section 4 H6, section 1.7 on OperatorCLIP's constant-text control).

R0-S carries the type of every boundary face as a one-hot over (D, N, R) (scano.Main.r0s_common: node-feature columns
5:8, zero on cells; record columns 6:9; insulated walls are N with zero flux). `wrap` puts one of four encodings in
front of a backbone, on whatever node features and records reach it (so the record transforms of the evaluation, K1
ladders and duplicated walls, are encoded the same way as the native records):

  onehot      (default) the backbone itself: no wrapper, the parameters and state-dict keys of every earlier run;
  none        the type columns are zero on every row (node features and records); widths and parameters as onehot.
              What stays: the value, alpha (> 0 only on Robin faces, so R remains recognisable from its coefficient),
              positions and normals. What goes is the D / N distinction: a face at value 0 is "Dirichlet at 0" or
              "no flux" alike, the confusion the D -> N type switch of the test set measures (T1);
  text        the type names TYPE_NAMES (Dirichlet, Neumann, Robin) embedded once by a frozen sentence encoder
              (data/type_text_v1/r0s_types.npz, tools/type_text_embed.py: sentence-transformers/all-MiniLM-L6-v2, mean
              pooling, unit norm, 384 numbers) form a frozen table E [3, 384] (a buffer, saved in the checkpoint); a
              learned linear map W [3 x 384] (no bias, zero-initialised) replaces the one-hot of a typed row by
              E[t] W^T (rows without a type, the cells, stay zero). 1,152 parameters on top of the backbone;
  const_text  the same with one fixed sentence (CONST_SENTENCE) for every type: E has three equal rows, so every typed
              row gets the same learned vector (OperatorCLIP's control: does a gain come from the type, or from having
              text at all?).

Zero-training remarks (they set what the arms can show):
  - W starts at zero, so at step 0 the text and const_text arms see no type, exactly as the none arm.
  - The first operation of every host on the node features and on the records is linear (an MLP's first layer), and
    E W^T is a free 3 x 3 map when the three names have linearly independent embeddings. In distribution the text arm
    therefore has the function class of the onehot arm (only the parametrisation and the optimisation differ), and
    const_text that of none plus a constant on the typed rows.
  - The arms differ in what they do with names never seen in training: `use_names` swaps E for one of the held-out
    name sets HELDOUT_NAMES (same physical types, other words; eval_r0s --type-names). One-hot has no slot for a new
    name (its counterpart is the none arm), and const_text ignores the names by construction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

TYPE_ORDER = ('D', 'N', 'R')                     # scano.Datasets.r0s D_TYPE, N_TYPE, R_TYPE: the one-hot columns
TYPE_NAMES = ('Dirichlet', 'Neumann', 'Robin')   # the training vocabulary of the text arm
CONST_SENTENCE = 'boundary condition'            # the constant-text control: one sentence for every type
# held-out names (evaluation only; registered with c57 before any run): the same three physical types, in words the
# text arm never saw. long = the names with their noun; physical = what the condition prescribes; openfoam = the
# solver's type names (the vocabulary of the DCTS cases); room = the machine-room reading of the R0-S design
# (docs/r0s_design_2026-09-30.md section 1.2: D = supply face, N = rack exhaust or heat source, R = envelope).
HELDOUT_NAMES = {
    'long': ('Dirichlet boundary condition', 'Neumann boundary condition', 'Robin boundary condition'),
    'physical': ('prescribed temperature', 'prescribed heat flux', 'convective heat exchange with the surroundings'),
    'openfoam': ('fixedValue', 'fixedGradient', 'mixed'),
    'room': ('supply air vent', 'server heat source', 'exterior wall exchanging heat with outdoor air'),
}
ENCODINGS = ('onehot', 'text', 'const_text')
TABLE_DIR = Path(__file__).resolve().parents[4] / 'data' / 'type_text_v1'      # repository data/, tracked
TABLE = TABLE_DIR / 'r0s_types.npz'
MANIFEST = TABLE_DIR / 'manifest.json'


def sentences():
    """{key: tuple of sentences} of every table the file holds (rows in TYPE_ORDER; 'const' has one row)."""
    out = {'train': TYPE_NAMES, 'const': (CONST_SENTENCE,)}
    out.update({f'heldout__{k}': v for k, v in HELDOUT_NAMES.items()})
    return out


def load_tables(path=TABLE):
    """{key: float32 array [rows, dim]} of the embedding file, checked against the sentences this module defines (a
    file written for other names is refused)."""
    with np.load(path, allow_pickle=False) as z:
        tables = {k: np.asarray(z[f'emb__{k}'], np.float32) for k in sentences()}
        for k, want in sentences().items():
            got = tuple(str(s) for s in z[f'text__{k}'])
            if got != tuple(want):
                raise ValueError(f'{path}: sentences of {k} are {got}, this code expects {tuple(want)}')
    return tables


def table_record(path=TABLE, manifest=MANIFEST):
    """Provenance of the embedding file: its sha256 and the encoder it was made with (from manifest.json)."""
    m = json.loads(Path(manifest).read_text())
    sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if sha != m['file_sha256']:
        raise ValueError(f'{path}: sha256 {sha} differs from manifest.json ({m["file_sha256"]})')
    return {'file': 'data/type_text_v1/r0s_types.npz', 'sha256': sha, 'encoder': m['encoder']['repo'],
            'revision': m['encoder']['revision'], 'pooling': m['encoder']['pooling'],
            'type_names': list(TYPE_NAMES), 'const_sentence': CONST_SENTENCE}


def type_table(encoding, names=None):
    """Frozen table E [3, dim] (float32 tensor) of an encoding: the training names (text), a held-out name set
    (text, names = a HELDOUT_NAMES key), or the constant sentence repeated (const_text)."""
    t = load_tables()
    if encoding == 'const_text':
        if names is not None:
            raise ValueError('const_text reads one sentence for every type: held-out names do not apply')
        return torch.as_tensor(np.repeat(t['const'], len(TYPE_ORDER), 0))
    if encoding != 'text':
        raise ValueError(f'no text table for type_encoding {encoding!r}')
    return torch.as_tensor(t['train'] if names is None else t[f'heldout__{names}'])


class TypeEncoded(nn.Module):
    """A backbone with the type one-hot of its inputs replaced (see the module docstring). Same call signature as the
    backbone: model(node_feat [B,N,F], ctx_w [B,N], bnd_feat [B,NB,Fb], bnd_w [B,NB], **kw)."""

    def __init__(self, inner, encoding, node_cols, record_cols):
        super().__init__()
        if encoding not in ('none', 'text', 'const_text'):
            raise ValueError(f'unknown type encoding {encoding!r}')
        self.inner = inner
        self.encoding = encoding
        self.node_cols, self.record_cols = tuple(node_cols), tuple(record_cols)
        n_types = self.node_cols[1] - self.node_cols[0]
        if n_types != len(TYPE_ORDER) or self.record_cols[1] - self.record_cols[0] != n_types:
            raise ValueError(f'type columns {node_cols} / {record_cols} do not hold a {len(TYPE_ORDER)}-type one-hot')
        self.needs_edges = getattr(inner, 'needs_edges', False)
        # model.message_passing (d54): cfg_common._forward reads these to pass the GN edges through **kw
        self.message_passing = getattr(inner, 'message_passing', 0)
        self.message_passing_hops = getattr(inner, 'message_passing_hops', 0)
        self.names = None
        if encoding != 'none':
            table = type_table(encoding)
            self.register_buffer('type_text', table.clone())                     # E [3, dim], frozen
            self.type_proj = nn.Linear(table.shape[1], n_types, bias=False)       # W [3, dim], learned
            nn.init.zeros_(self.type_proj.weight)

    def type_vectors(self, like=None):
        """[3, 3]: row t = the vector the one-hot of type t (TYPE_ORDER) is replaced by; zeros for 'none'. `like`: a
        tensor whose dtype and device the zeros take (default float32 on the CPU)."""
        if self.encoding == 'none':
            n = self.node_cols[1] - self.node_cols[0]
            return torch.zeros(n, n, dtype=getattr(like, 'dtype', torch.float32),
                               device=getattr(like, 'device', torch.device('cpu')))
        return self.type_proj(self.type_text)

    def _swap(self, f, cols, vec):
        a, b = cols
        return torch.cat([f[..., :a], f[..., a:b] @ vec, f[..., b:]], -1)

    def forward(self, x, ctx, bf, bw, **kw):
        vec = self.type_vectors(x).to(x.dtype)
        return self.inner(self._swap(x, self.node_cols, vec), ctx, self._swap(bf, self.record_cols, vec), bw, **kw)

    def use_names(self, names):
        """Evaluation with a held-out name set (HELDOUT_NAMES key): E becomes that set's embeddings, W is kept.
        Only for the text arm (one-hot has no slot for a new name; const_text ignores the names)."""
        if self.encoding != 'text':
            raise ValueError(f'held-out type names apply to type_encoding "text" only, not "{self.encoding}"')
        if names not in HELDOUT_NAMES:
            raise ValueError(f'unknown held-out name set {names!r}; choose from {sorted(HELDOUT_NAMES)}')
        self.type_text.copy_(type_table('text', names).to(self.type_text.device))
        self.names = names


def wrap(model, types, encoding, node_cols, record_cols):
    """The backbone itself for types "kind" with the one-hot encoding (every run before c57), else TypeEncoded."""
    if types not in ('kind', 'none') or encoding not in ENCODINGS:
        raise ValueError(f'boundary.types {types!r} / type_encoding {encoding!r} are not R0-S type encodings')
    if types == 'kind' and encoding == 'onehot':
        return model
    return TypeEncoded(model, 'none' if types == 'none' else encoding, node_cols, record_cols)
