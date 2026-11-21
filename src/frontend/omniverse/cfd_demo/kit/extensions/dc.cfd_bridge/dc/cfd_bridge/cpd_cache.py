"""The cpd_cell cache: ROI queries without reading the whole result, and an incremental
record of which cells already have values.

This is requirement 3.1 (and the backend half of requirement 2) from
docs/superpowers/specs/2026-08-09-roi-as-simulation-unit.md.

WHY IT EXISTS. The first ROI extraction re-read the entire 122 MB .vtu and recomputed all
609,990 cell centres on every drag, then cut. That satisfies "Kit only receives the ROI"
but not "we only READ the ROI" — the global read was still happening, one layer down.
This splits the two: a small per-mesh LAYOUT file answers "which cells are in this box?"
in milliseconds without touching the result, and the values are fetched for those cells
only.

THE IDENTITY IS ARITHMETIC, NOT A LOOKUP.

    cell i          -> cpd i
    boundary face f -> cpd nCells + (f - nInternalFaces)

Verified on Base_leakage: internal.vtu's cell order is bit-identical to OpenFOAM's 964/T,
max abs diff 0.0 over 609,990 cells, NumberOfCells == nCells. So the VTU cell index, the
OpenFOAM cell index and SCANO's sampling index are the same integer, and a cache keyed on
it is shared by every consumer.

VALIDITY IS PER MESH, NOT PER CASE. Cell order is invariant for one mesh but NOT across a
re-mesh or a different nProcs — reconstructParMesh merges in rank order. The layout is
therefore fingerprinted and refused on mismatch rather than silently reused.

WHAT IS AND IS NOT PROVEN. The ROI query and the fill bookkeeping below are exact and
cheap. The premise that a partially filled cache can be COMPLETED by inferring one ROI at
a time is NOT established: restricting inference to a small box was measured to cost
+0.406 nRMSE against a 0.077 fail threshold (commit 8da9599), and only recovers when the
pivot cloud is spread back over most of the domain. So `filled`/`commit` are correct
machinery whose accuracy premise is supplied from outside — by a whole-domain pass, by
the box+anchor mitigation, or by a model retrained on spatially restricted pools. Nothing
here silently assumes otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os


class CpdLayout:
    """Per-mesh geometry index: centroids plus a uniform bin grid over them.

    Small enough to keep resident (9.2 MB of centroids + 3.1 MB of bins for Base_leakage's
    609,990 cells, against the 122 MB result it replaces for query purposes).
    """

    def __init__(self, centroids, nbins=64):
        import numpy as np
        self.centroid = np.ascontiguousarray(centroids, dtype=np.float32)
        self.n = int(self.centroid.shape[0])
        lo = self.centroid.min(axis=0)
        hi = self.centroid.max(axis=0)
        span = np.maximum(hi - lo, 1e-9)
        self.origin = lo.astype(np.float32)
        self.nbins = int(nbins)
        self.inv_step = (self.nbins / span).astype(np.float32)
        idx = np.clip(((self.centroid - self.origin) * self.inv_step).astype(np.int32),
                      0, self.nbins - 1)
        key = (idx[:, 0] * self.nbins + idx[:, 1]) * self.nbins + idx[:, 2]
        order = np.argsort(key, kind="stable").astype(np.int32)
        self.bin_items = order
        counts = np.bincount(key, minlength=self.nbins ** 3)
        self.bin_offsets = np.concatenate(
            [np.zeros(1, dtype=np.int64), np.cumsum(counts)]).astype(np.int64)

    def query(self, mn, mx):
        """cpd ids whose centroid lies in the AABB. Exact — the bins only prune."""
        import numpy as np
        lo = np.asarray(mn, dtype=np.float32)
        hi = np.asarray(mx, dtype=np.float32)
        b0 = np.clip(((lo - self.origin) * self.inv_step).astype(np.int32),
                     0, self.nbins - 1)
        b1 = np.clip(((hi - self.origin) * self.inv_step).astype(np.int32),
                     0, self.nbins - 1)
        chunks = []
        for bx in range(b0[0], b1[0] + 1):
            for by in range(b0[1], b1[1] + 1):
                s = (bx * self.nbins + by) * self.nbins + b0[2]
                e = (bx * self.nbins + by) * self.nbins + b1[2]
                a = self.bin_offsets[s]
                b = self.bin_offsets[e + 1]
                if b > a:
                    chunks.append(self.bin_items[a:b])
        if not chunks:
            return np.empty(0, dtype=np.int64)
        cand = np.concatenate(chunks)
        c = self.centroid[cand]
        keep = ((c >= lo) & (c <= hi)).all(axis=1)
        return cand[keep].astype(np.int64)

    def save(self, path):
        import numpy as np
        np.savez(path, centroid=self.centroid, bin_items=self.bin_items,
                 bin_offsets=self.bin_offsets, origin=self.origin,
                 inv_step=self.inv_step, nbins=np.int32(self.nbins))

    @classmethod
    def load(cls, path):
        import numpy as np
        with np.load(path) as z:
            obj = cls.__new__(cls)
            obj.centroid = z["centroid"]
            obj.bin_items = z["bin_items"]
            obj.bin_offsets = z["bin_offsets"]
            obj.origin = z["origin"]
            obj.inv_step = z["inv_step"]
            obj.nbins = int(z["nbins"])
            obj.n = int(obj.centroid.shape[0])
        return obj


def mesh_id(source_path, n_cells):
    """Fingerprint tying a layout to one mesh. Refuse on mismatch, never reuse blindly.

    Includes the source file's identity because a re-mesh or a different nProcs changes
    cell order (reconstructParMesh merges in rank order), and a layout that silently
    survived that would return the WRONG cells for every query.
    """
    st = os.stat(source_path)
    return hashlib.sha1(
        f"{os.path.realpath(source_path)}|{st.st_size}|{st.st_mtime_ns}|{n_cells}"
        .encode()).hexdigest()[:16]


class CpdCache:
    """Layout + an optional per-generation value store with exactly-once bookkeeping.

    `filled` is deliberately separate from "has a value": it records cells COMMITTED as
    pivots, which is what makes `todo()` exact. A cell touched only as a halo neighbour
    must not count as done — that distinction is the whole of the no-duplicate-computation
    requirement, and conflating the two silently double-computes or, worse, freezes a
    half-converged value.
    """

    def __init__(self, root, mid, layout):
        self.root = root
        self.mid = mid
        self.layout = layout
        self.dir = os.path.join(root, mid)
        os.makedirs(self.dir, exist_ok=True)

    @classmethod
    def open(cls, root, source_path, centroids_fn, n_cells=None):
        """Load or build the layout for `source_path`. `centroids_fn()` is only called on
        a cache miss, so a hit never touches the result file at all."""
        import numpy as np
        os.makedirs(root, exist_ok=True)
        cen = None
        if n_cells is None:
            cen = centroids_fn()
            n_cells = int(cen.shape[0])
        mid = mesh_id(source_path, n_cells)
        lp = os.path.join(root, mid, "layout.npz")
        if os.path.exists(lp):
            return cls(root, mid, CpdLayout.load(lp))
        if cen is None:
            cen = centroids_fn()
        os.makedirs(os.path.join(root, mid), exist_ok=True)
        lay = CpdLayout(np.asarray(cen))
        lay.save(lp)
        return cls(root, mid, lay)

    # ── generation = one operating point ────────────────────────────────────────
    # Two operating points are two different fields; merging them is nonsense, so the
    # directory key makes that structural rather than a convention someone must remember.

    def gen_dir(self, gen_id):
        d = os.path.join(self.dir, f"gen_{gen_id}")
        os.makedirs(d, exist_ok=True)
        return d

    def _paths(self, gen_id):
        d = self.gen_dir(gen_id)
        return (os.path.join(d, "sum.npy"), os.path.join(d, "counts.npy"),
                os.path.join(d, "filled.npy"), os.path.join(d, "meta.json"))

    def state(self, gen_id, nfields=5):
        """Open (creating if needed) the value store for one operating point.

        Stores the SUM and the COUNT, never the mean. Sum+count is associative and
        restart-safe: a later ROI overlapping an earlier one merges by `sum += new;
        counts += 1` with no knowledge of history, and the mean is derived at read. It is
        also the same arithmetic SCANO's own TTA accumulator and multi-rank all-reduce
        use, so this is that operation at a longer timescale rather than a new scheme.
        """
        import numpy as np
        sp, cp, fp, _ = self._paths(gen_id)
        n = self.layout.n
        if os.path.exists(sp):
            return (np.load(sp, mmap_mode="r+"), np.load(cp, mmap_mode="r+"),
                    np.load(fp, mmap_mode="r+"))
        s = np.lib.format.open_memmap(sp, mode="w+", dtype=np.float32, shape=(n, nfields))
        c = np.lib.format.open_memmap(cp, mode="w+", dtype=np.uint16, shape=(n,))
        f = np.lib.format.open_memmap(fp, mode="w+", dtype=bool, shape=(n,))
        s[:] = 0.0
        c[:] = 0
        f[:] = False
        return s, c, f

    def todo(self, gen_id, ids):
        """The subset of `ids` not yet committed. This is the no-duplicate-computation
        contract: everything else has been done and must not be recomputed."""
        import numpy as np
        _, _, filled = self.state(gen_id)
        ids = np.asarray(ids, dtype=np.int64)
        return ids[~np.asarray(filled)[ids]]

    def commit(self, gen_id, ids, values):
        """Accumulate values for `ids` and mark them filled.

        Only ever call this with PIVOTS — cells the producer actually solved for — never
        with halo/context cells that were merely present in the input. A halo cell's value
        is computed from a truncated neighbourhood and committing it would freeze a value
        the next ROI could have produced properly.
        """
        import numpy as np
        s, c, f = self.state(gen_id)
        ids = np.asarray(ids, dtype=np.int64)
        v = np.asarray(values, dtype=np.float32)
        np.add.at(s, ids, v)
        np.add.at(c, ids, 1)
        f[ids] = True
        s.flush()
        c.flush()
        f.flush()

    def read(self, gen_id, ids):
        """Mean value per id, plus a validity mask. Cells with count 0 are NOT zero —
        they are UNKNOWN, and returning 0.0 for them is how a partially filled domain gets
        rendered as a room full of 0 K air."""
        import numpy as np
        s, c, _ = self.state(gen_id)
        ids = np.asarray(ids, dtype=np.int64)
        cnt = np.asarray(c)[ids].astype(np.float32)
        valid = cnt > 0
        out = np.asarray(s)[ids] / np.maximum(cnt, 1)[:, None]
        return out, valid

    def coverage(self, gen_id):
        import numpy as np
        _, _, f = self.state(gen_id)
        filled = int(np.asarray(f).sum())
        return filled, self.layout.n, filled / max(1, self.layout.n)

    def write_meta(self, gen_id, **kw):
        _, _, _, mp = self._paths(gen_id)
        filled, total, frac = self.coverage(gen_id)
        with open(mp, "w") as fh:
            json.dump({"mesh_id": self.mid, "gen_id": gen_id, "n_cpd": total,
                       "n_filled": filled, "coverage": frac, **kw}, fh)
