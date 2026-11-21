"""olbc Poisson (Operator Learning with Boundary Conditions) -> canonical Gen-FVGN H5.

olbc ships each dataset as a NetCDF/HDF5 file (train.nc/test.nc) holding many samples that
SHARE one unstructured triangular mesh (fix=True). Per sample only the boundary condition
(a random Dirichlet/Neumann/Robin partition + values) and the solution vary; the mesh, the
signed-distance field, and the (constant) source f are shared. Variable-length datatypes:
each sample is indexed explicitly at [i, 0, c] (see olbc readme).

We map each sample onto the codebase's cpd_cell layout so the existing
HomoGraph_dataset -> sampling -> dual-stream/baseline -> sweep stack runs unchanged:

  * interior nodes  (|sdf|>0)              cpd|cell_type = 0   (NORMAL)
  * boundary nodes  (|sdf|=0, D/N/R)       cpd|cell_type = WALL (=3)

field_set="olbc" predicts the scalar solution u (1 channel, stored in sol_cpd_cell). The
per-boundary-node BC (type code 0=D/1=N/2=R, value g, Robin coeff alpha) is stored in
olbc|bc [N,3]; the dataset turns it into the cross-attention boundary KV per arm
(numeric=[g,alpha] / onehot=[g,alpha,onehot3] / text=[g,alpha,type_emb384]).

The kNN graph + shared geometry are computed ONCE per .nc file and reused for every sample
(the mesh is shared), so per-sample H5 writes are cheap.
"""
import json
import os
import h5py
import numpy as np

WALL = 3  # any nonzero cpd|cell_type marks a boundary node for HomoGraph_dataset's boundary mask.
# BC type codes
TYPE_D, TYPE_N, TYPE_R = 0, 1, 2


def _knn_edges(pos_xy: np.ndarray, k: int) -> np.ndarray:
    """Undirected k-NN edge list [2, E] (both directions, deduplicated)."""
    from sklearn.neighbors import NearestNeighbors
    k_eff = min(k + 1, len(pos_xy))
    _, idx = NearestNeighbors(n_neighbors=k_eff).fit(pos_xy).kneighbors(pos_xy)
    src = np.repeat(np.arange(len(pos_xy)), idx.shape[1] - 1)
    dst = idx[:, 1:].reshape(-1)
    e = np.stack([src, dst], 0)
    e = np.concatenate([e, e[::-1]], axis=1)
    return np.unique(e, axis=1).astype(np.int64)


def _vget(f, path, i, c=0):
    """Read one sample of a variable-length olbc array -> 1-D float array."""
    return np.asarray(f[path][i, 0, c]).ravel()


def load_shared_mesh(nc_path: str, knn_k: int = 8) -> dict:
    """Read the shared geometry (coords, sdf, sdf_grad, source, kNN edges) from sample 0.

    olbc fix=True => coordinates/sdf/source are identical across samples. We read them once.
    """
    f = h5py.File(nc_path, "r")
    x = np.asarray(f["coordinates"][0, 0, 0]).ravel()
    y = np.asarray(f["coordinates"][0, 0, 1]).ravel()
    pos_xy = np.stack([x, y], 1).astype(np.float32)
    n = len(pos_xy)
    pos3 = np.concatenate([pos_xy, np.zeros((n, 1), np.float32)], axis=1)
    sdf = _vget(f, "interior/sdf", 0).reshape(n, 1).astype(np.float32)
    gx = _vget(f, "interior/sdf_grad", 0, 0)
    gy = _vget(f, "interior/sdf_grad", 0, 1)
    grad = np.stack([gx, gy], 1).astype(np.float32)
    gnorm = np.linalg.norm(grad, axis=1, keepdims=True)
    unv = grad / np.clip(gnorm, 1e-9, None)
    unv3 = np.concatenate([unv, np.zeros((n, 1), np.float32)], axis=1).astype(np.float32)
    source = _vget(f, "interior/source", 0).reshape(n, 1).astype(np.float32)
    edges = _knn_edges(pos_xy, knn_k)
    n_samples = f["interior/solution"].shape[0]
    f.close()
    pv_cells_node = np.empty((2 * n,), dtype=np.int64)
    pv_cells_node[0::2] = 1
    pv_cells_node[1::2] = np.arange(n, dtype=np.int64)
    pv_cells_type = np.full((n,), 1, dtype=np.int64)  # VTK_VERTEX
    return dict(n=n, pos3=pos3, sdf=sdf, unv3=unv3, source=source, edges=edges,
                n_samples=int(n_samples), pv_cells_node=pv_cells_node, pv_cells_type=pv_cells_type)


def _read_sample_bc(f, i, n):
    """Per-boundary-node BC: returns (cell_type[n], olbc_bc[n,3]=(type,g,alpha), n_per_type dict)."""
    cell_type = np.zeros((n,), dtype=np.int64)
    bc = np.zeros((n, 3), dtype=np.float32)  # (type_code, g, alpha)
    counts = {}
    for tname, tcode in (("dirichlet", TYPE_D), ("neumann", TYPE_N), ("robin", TYPE_R)):
        ip = f"boundaries/0/{tname}/indices"
        gp = f"boundaries/0/{tname}/g"
        idx = _vget(f, ip, i).astype(np.int64)
        counts[tname] = int(idx.size)
        if idx.size == 0:
            continue
        g = _vget(f, gp, i).astype(np.float32)
        cell_type[idx] = WALL
        bc[idx, 0] = float(tcode)
        bc[idx, 1] = g
        if tname == "robin":
            a = _vget(f, "boundaries/0/robin/alpha", i).astype(np.float32)
            bc[idx, 2] = a
    return cell_type, bc, counts


def olbc_nc_to_h5(nc_path: str, mesh: dict, sample_idx: int, name: str, out_h5: str) -> dict:
    """Convert ONE olbc sample -> canonical H5 (mesh fields come from the shared `mesh` dict)."""
    n = mesh["n"]
    f = h5py.File(nc_path, "r")
    sol = _vget(f, "interior/solution", sample_idx).reshape(1, n, 1).astype(np.float32)  # [1,N,1]
    cell_type, bc, counts = _read_sample_bc(f, sample_idx, n)
    f.close()

    s = np.flatnonzero(cell_type == WALL)
    patch_list = {
        "boundary": {
            "start_idx_cpd_cell": int(s.min()) if s.size else 0,
            "end_idx_cpd_cell": int(s.max()) + 1 if s.size else 0,
            "type": "mixed_bc",
            "n": int(s.size),
        }
    }
    bc_meta = {"n_dirichlet": counts["dirichlet"], "n_neumann": counts["neumann"],
               "n_robin": counts["robin"], "name": name}

    with h5py.File(out_h5, "w") as h:
        h.attrs["layout_version"] = 2
        h.attrs["case_name"] = name
        h.attrs["dimension"] = "2D"
        h.attrs["num_cpd_cell"] = n
        h.attrs["patch_list"] = json.dumps(patch_list)
        h.attrs["olbc_bc_meta"] = json.dumps(bc_meta)
        h.create_dataset("cpd|centroid", data=mesh["pos3"])
        h.create_dataset("node|pos", data=mesh["pos3"])
        h.create_dataset("cpd|cell_type", data=cell_type)
        h.create_dataset("face|face_type", data=cell_type)
        # int32 edges (loader casts to .long() on read); the HomoGraph data-driven path reads only
        # cpd|neighbor_cell (cpd|neighbor_cell_x is an FVsolver/hetero-graph artifact, unused here),
        # so we DROP the redundant _x copy — it was 35% of the per-sample file (mesh shared, so the
        # edge list is replicated in every sample's H5; halving it matters at 3000+ samples).
        h.create_dataset("cpd|neighbor_cell", data=mesh["edges"].astype(np.int32))
        h.create_dataset("cpd|cell_udf", data=mesh["sdf"])
        h.create_dataset("unv_cpd_cell", data=mesh["unv3"])
        h.create_dataset("cells_face_normal", data=mesh["unv3"])
        h.create_dataset("cells_face", data=np.arange(n, dtype=np.int64))
        h.create_dataset("pv_cells_node", data=mesh["pv_cells_node"])
        h.create_dataset("pv_cells_type", data=mesh["pv_cells_type"])
        h.create_dataset("source_cpd_cell", data=mesh["source"])   # node input feature [N,1]
        h.create_dataset("olbc|bc", data=bc)                        # [N,3] (type, g, alpha)
        h.create_dataset("sol_cpd_cell", data=sol)                 # target [1,N,1]
        h.create_dataset("time_list", data=np.array([1.0], np.float32))

    u = sol[0, :, 0]
    stats = {"sol": {"sum": float(u.sum()), "sum_sq": float((u.astype(np.float64) ** 2).sum()),
                     "min": float(u.min()), "max": float(u.max()), "count": int(n)}}
    stats.update({"case_name": name, "num_cpd_cell": n, "num_surface": int(s.size)})
    return stats
