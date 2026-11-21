"""AirfRANS simulation -> canonical Gen-FVGN H5 (route A).

AirfRANS ships each simulation as a 2D point cloud (mesh nodes) with NO edges
(torch_geometric note: "agnostic to the simulation mesh"). We build a k-NN graph on
node positions and map the cloud onto the codebase's cpd_cell layout so the existing
HomoGraph_dataset -> sampling -> dual-stream/baseline -> sweep stack runs unchanged:

  * interior cells  -> volume nodes (surf == 0)            cpd|cell_type = 0 (NORMAL)
  * boundary faces  -> airfoil-surface nodes (surf == 1)   cpd|cell_type = WALL (!=0)

Fields are a single steady-state step: [u, v, p, nut] (4 channels, no w/T). nut is the
turbulent kinematic viscosity (>=0, multi-scale); the asinh transform is applied at the
dataset/normalizer layer (field_set="airfrans"), NOT here -- this file stores raw values.

Column layout of an `airfrans.dataset.load` sample row (12 cols, verified at process time):
    0:x 1:y | 2:Uinf_x 3:Uinf_y | 4:SDF | 5:nx 6:ny | 7:u 8:v 9:p 10:nut | 11:surf(0/1)
`assert_columns` re-checks this against a loaded array so a future format change fails loud.
"""
import json
import h5py
import numpy as np

WALL = 3  # any nonzero cpd|cell_type marks a boundary node for HomoGraph_dataset's boundary mask.

# column indices in an airfrans.dataset.load row
CX, CY, UIX, UIY, SDF, NX, NY, TU, TV, TP, TNUT, SURF = range(12)


def load_sim_array(root: str, name: str, T: float = 298.15) -> np.ndarray:
    """Build the canonical [N, 12] AirfRANS row for ONE simulation, memory-efficiently.

    Reproduces `airfrans.dataset.load`'s per-node concatenation
    (position, inlet_velocity, SDF, normals, velocity, pressure, nu_t, surface) for a single
    case, so a driver can stream 800 sims one at a time instead of loading them all into RAM.
    `root` must be the dir holding `manifest.json` and the per-sim folders (e.g. `.../AirfRANS/Dataset`).
    """
    from airfrans.simulation import Simulation

    sim = Simulation(root=root, name=name, T=T)
    inlet = (
        np.array([np.cos(sim.angle_of_attack), np.sin(sim.angle_of_attack)])
        * sim.inlet_velocity
    ).reshape(1, 2) * np.ones_like(sim.sdf)
    arr = np.concatenate(
        [sim.position, inlet, sim.sdf, sim.normals, sim.velocity, sim.pressure,
         sim.nu_t, sim.surface.reshape(-1, 1)],
        axis=-1,
    )
    return arr.astype(np.float32)


def assert_columns(arr: np.ndarray) -> None:
    """Fail loudly if the AirfRANS array is not the expected [N, 12] layout."""
    if arr.ndim != 2 or arr.shape[1] != 12:
        raise ValueError(f"AirfRANS row expected [N,12], got {arr.shape}; re-check column map.")
    surf = arr[:, SURF]
    uniq = np.unique(surf)
    if not np.all(np.isin(uniq, [0.0, 1.0])):
        raise ValueError(f"col {SURF} expected a 0/1 surface flag, found values {uniq[:6]}")


def _knn_edges(pos_xy: np.ndarray, k: int) -> np.ndarray:
    """Undirected k-NN edge list [2, E] (both directions, deduplicated)."""
    from sklearn.neighbors import NearestNeighbors
    k_eff = min(k + 1, len(pos_xy))
    _, idx = NearestNeighbors(n_neighbors=k_eff).fit(pos_xy).kneighbors(pos_xy)  # [N, k+1] incl self
    src = np.repeat(np.arange(len(pos_xy)), idx.shape[1] - 1)
    dst = idx[:, 1:].reshape(-1)
    e = np.stack([src, dst], 0)
    e = np.concatenate([e, e[::-1]], axis=1)                 # make undirected
    return np.unique(e, axis=1).astype(np.int64)


def airfrans_sim_to_h5(arr: np.ndarray, name: str, out_h5: str, knn_k: int = 16) -> dict:
    """Convert ONE AirfRANS sample array -> canonical H5. Returns a per-field stats dict.

    `arr` is the already-loaded [N, 12] row for this case (the driver loads the dataset once
    and passes per-case slices; loading the whole 1000-sim dataset per call would be wasteful).
    """
    assert_columns(arr)
    arr = arr.astype(np.float32)
    n = arr.shape[0]
    pos_xy = arr[:, [CX, CY]]
    pos3 = np.concatenate([pos_xy, np.zeros((n, 1), np.float32)], axis=1)
    unv3 = np.concatenate([arr[:, [NX, NY]], np.zeros((n, 1), np.float32)], axis=1)
    surf = arr[:, SURF] == 1
    cell_type = np.where(surf, WALL, 0).astype(np.int64)

    uvw = arr[:, [TU, TV]].reshape(1, n, 2)        # [1, N, 2]  (u, v) only -- 2D
    press = arr[:, [TP]].reshape(1, n, 1)          # [1, N, 1]
    nut = arr[:, [TNUT]].reshape(1, n, 1)          # [1, N, 1]  raw turbulent viscosity

    edges = _knn_edges(pos_xy, knn_k)
    # PyVista VTK_VERTEX (=1) cells: one single-point cell per node, so the point cloud is
    # exportable to VTU. Flat connectivity layout [1, i, 1, i+1, ...]; types all VTK_VERTEX.
    pv_cells_node = np.empty((2 * n,), dtype=np.int64)
    pv_cells_node[0::2] = 1
    pv_cells_node[1::2] = np.arange(n, dtype=np.int64)
    pv_cells_type = np.full((n,), 1, dtype=np.int64)  # VTK_VERTEX
    s = np.flatnonzero(surf)
    patch_list = {
        "airfoil": {
            "start_idx_cpd_cell": int(s.min()) if s.size else 0,
            "end_idx_cpd_cell": int(s.max()) + 1 if s.size else 0,
            "type": "wall",
            "n": int(surf.sum()),
        }
    }
    u_inf, v_inf = float(arr[0, UIX]), float(arr[0, UIY])
    speed = float((u_inf ** 2 + v_inf ** 2) ** 0.5)
    freestream = {
        "u_inf": u_inf, "v_inf": v_inf, "speed": speed,
        "aoa_deg": float(np.degrees(np.arctan2(v_inf, u_inf))),
        "name": name,
    }

    with h5py.File(out_h5, "w") as f:
        f.attrs["layout_version"] = 2
        f.attrs["case_name"] = name
        f.attrs["dimension"] = "2D"
        f.attrs["num_cpd_cell"] = n
        f.attrs["patch_list"] = json.dumps(patch_list)
        f.attrs["freestream"] = json.dumps(freestream)
        f.create_dataset("cpd|centroid", data=pos3)
        f.create_dataset("node|pos", data=pos3)
        f.create_dataset("cpd|cell_type", data=cell_type)
        f.create_dataset("face|face_type", data=cell_type)
        f.create_dataset("cpd|neighbor_cell", data=edges)
        f.create_dataset("cpd|neighbor_cell_x", data=edges)   # k-hop closure unused by the SCA path
        f.create_dataset("cpd|cell_udf", data=arr[:, [SDF]])
        f.create_dataset("unv_cpd_cell", data=unv3)
        f.create_dataset("cells_face_normal", data=unv3)
        f.create_dataset("cells_face", data=np.arange(n, dtype=np.int64))
        f.create_dataset("pv_cells_node", data=pv_cells_node)
        f.create_dataset("pv_cells_type", data=pv_cells_type)
        f.create_dataset("uvw_cpd_cell", data=uvw)
        f.create_dataset("press_cpd_cell", data=press)
        f.create_dataset("nut_cpd_cell", data=nut)
        f.create_dataset("time_list", data=np.array([1.0], np.float32))

    # Stats describe the NORMALIZATION space, not the raw H5 data: nut is stored raw but
    # read_field_stack applies asinh on read, so the model/normalizer see asinh(nut). Hence the
    # returned nut stats are on asinh(nut); u/v/p are stored and read as-is.
    fields = {
        "u": uvw[0, :, 0], "v": uvw[0, :, 1], "p": press[0, :, 0],
        "nut": np.arcsinh(nut[0, :, 0]),
    }
    stats = {
        k: {
            "sum": float(v.sum()),
            "sum_sq": float((v.astype(np.float64) ** 2).sum()),
            "min": float(v.min()),
            "max": float(v.max()),
            "count": int(n),
        }
        for k, v in fields.items()
    }
    stats.update({"case_name": name, "num_cpd_cell": n, "num_surface": int(surf.sum())})
    return stats
