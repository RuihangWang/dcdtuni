"""dcCart coarsening (third_parties/dcCartMesh/coarsen): patches, boundary faces and volume are kept exactly."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from backend.aircooling.third_parties.dcCartMesh.coarsen import coarsen as cz


def _write_ascii(path: Path, cls: str, obj: str, body: str, note: str = "") -> None:
    head = ("FoamFile\n{\n    version 2.0;\n    format ascii;\n    class " + cls + ";\n"
            + (f'    note "{note}";\n' if note else "") + f"    object {obj};\n}}\n"
            "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n")
    path.write_text(head + body)


def box_mesh(root: Path, n=(12, 8, 6), h=0.25, hole=((4, 6), (2, 5), (0, 4))) -> Path:
    """A serial polyMesh of n hex cells of edge h with an obstacle (the cells of ``hole`` removed). Patches: the
    floor, the ceiling, two side walls split in halves (four patches), the two end walls, and the obstacle's
    faces (one patch per side; it stands on the floor) -- 13 patches, as a hall's walls and racks would be."""
    nx, ny, nz = n
    solid = np.zeros(n, dtype=bool)
    (x0, x1), (y0, y1), (z0, z1) = hole
    solid[x0:x1, y0:y1, z0:z1] = True
    cid = -np.ones(n, dtype=np.int64)
    cid[~solid] = np.arange((~solid).sum())

    def pid(i, j, k):
        return (i * (ny + 1) + j) * (nz + 1) + k

    pts = np.array([[i * h, j * h, k * h] for i in range(nx + 1) for j in range(ny + 1) for k in range(nz + 1)])
    internal, bnd = [], {}

    def quad(ax, i, j, k):
        # the face normal to axis ax at lattice point (i, j, k), points ordered for +ax
        if ax == 0:
            return [pid(i, j, k), pid(i, j + 1, k), pid(i, j + 1, k + 1), pid(i, j, k + 1)]
        if ax == 1:
            return [pid(i, j, k), pid(i, j, k + 1), pid(i + 1, j, k + 1), pid(i + 1, j, k)]
        return [pid(i, j, k), pid(i + 1, j, k), pid(i + 1, j + 1, k), pid(i, j + 1, k)]

    def add_b(name, f, owner):
        bnd.setdefault(name, []).append((f, owner))

    for ax in range(3):
        for i in range(nx + (ax == 0)):
            for j in range(ny + (ax == 1)):
                for k in range(nz + (ax == 2)):
                    lo = [i, j, k]
                    lo[ax] -= 1
                    a = cid[tuple(lo)] if lo[ax] >= 0 else -1
                    b = cid[i, j, k] if [i, j, k][ax] < n[ax] else -1
                    f = quad(ax, i, j, k)
                    side = ("x", "y", "z")[ax]
                    if a >= 0 and b >= 0:
                        internal.append((f, a, b) if a < b else (f[::-1], b, a))
                    elif a >= 0:
                        name = {"x": "end_hi", "y": "side_hi_" + ("a" if i < nx // 2 else "b"),
                                "z": "ceiling"}[side] if [i, j, k][ax] == n[ax] else f"obstacle_{side}lo"
                        add_b(name, f, a)
                    elif b >= 0:
                        name = {"x": "end_lo", "y": "side_lo_" + ("a" if i < nx // 2 else "b"),
                                "z": "floor"}[side] if [i, j, k][ax] == 0 else f"obstacle_{side}hi"
                        add_b(name, f[::-1], b)
    internal.sort(key=lambda t: (t[1], t[2]))
    faces = [f for f, _, _ in internal]
    owner = [a for _, a, _ in internal]
    neigh = [b for _, _, b in internal]
    patches = []
    for name in sorted(bnd):
        patches.append((name, len(faces), len(bnd[name])))
        for f, o in bnd[name]:
            faces.append(f)
            owner.append(o)
    poly = root / "constant" / "polyMesh"
    poly.mkdir(parents=True)
    nc = int((~solid).sum())
    note = f"nPoints:{len(pts)} nCells:{nc} nFaces:{len(faces)} nInternalFaces:{len(internal)}"
    _write_ascii(poly / "points", "vectorField", "points",
                 f"{len(pts)}\n(\n" + "\n".join(f"({p[0]:.9g} {p[1]:.9g} {p[2]:.9g})" for p in pts) + "\n)\n")
    _write_ascii(poly / "faces", "faceList", "faces",
                 f"{len(faces)}\n(\n" + "\n".join("4(" + " ".join(map(str, f)) + ")" for f in faces) + "\n)\n")
    _write_ascii(poly / "owner", "labelList", "owner", f"{len(owner)}\n(\n" + "\n".join(map(str, owner)) + "\n)\n", note)
    _write_ascii(poly / "neighbour", "labelList", "neighbour",
                 f"{len(neigh)}\n(\n" + "\n".join(map(str, neigh)) + "\n)\n", note)
    entries = "".join(f"    {name}\n    {{\n        type wall;\n        inGroups 1(wall);\n        nFaces {nf};\n"
                      f"        startFace {s};\n    }}\n" for name, s, nf in patches)
    _write_ascii(poly / "boundary", "polyBoundaryMesh", "boundary", f"{len(patches)}\n(\n{entries})\n")
    return root


def _patch_faces(mesh_dir: Path):
    m = cz.read_rank(cz.rank_dirs(mesh_dir)[0])
    Sf, Cf, mag = cz.face_geometry(m.points, m.offsets, m.verts)
    out = {}
    for name, typ, s, n in m.patches:
        out[name] = (n, float(mag[s:s + n].sum()), np.round(Cf[s:s + n], 9).tolist())
    return m, out


@pytest.mark.parametrize("ratio", [4.0, 27.0])
def test_coarsening_keeps_every_patch_face_and_the_volume(tmp_path, ratio):
    fine = box_mesh(tmp_path / "fine")
    rec = cz.coarsen_mesh(fine, tmp_path / "coarse", ratio=ratio, level=None)
    assert rec["ok"], rec
    assert rec["cells"] < rec["fineCells"]
    assert rec["physicalPatches"] == 13 and rec["patchAudit"]["mismatchedPatches"] == []
    assert rec["volume"]["relErr"] < 1e-12
    assert rec["quality"]["cellClosednessRelMax"] < 1e-12 and rec["quality"]["negativeVolumeCells"] == 0
    # every boundary face is kept, in order, with its geometry
    mf, pf = _patch_faces(fine)
    mc, pc = _patch_faces(tmp_path / "coarse")
    assert pf == pc
    # the receipt and the fine-to-coarse map are written next to the mesh
    saved = json.loads((tmp_path / "coarse" / "dccart-coarsen.json").read_text())
    assert saved["cells"] == mc.n_cells
    lab = np.load(tmp_path / "coarse" / "coarsen" / "fineToCoarse_rank0.npy")
    assert lab.shape == (mf.n_cells,) and lab.max() + 1 == mc.n_cells


def test_coarse_mesh_passes_openfoam_checkmesh_topology(tmp_path):
    exe = shutil.which("checkMesh")
    if exe is None:
        pytest.skip("OpenFOAM checkMesh is not on PATH")
    fine = box_mesh(tmp_path / "fine")
    cz.coarsen_mesh(fine, tmp_path / "case", ratio=8.0)
    system = tmp_path / "case" / "system"
    system.mkdir()
    (system / "controlDict").write_text("FoamFile{version 2.0;format ascii;class dictionary;object controlDict;}\n"
                                        "application none;startTime 0;endTime 1;deltaT 1;writeControl timeStep;"
                                        "writeInterval 1;\n")
    (system / "fvSchemes").write_text("FoamFile{version 2.0;format ascii;class dictionary;object fvSchemes;}\n"
                                      "ddtSchemes{default steadyState;} gradSchemes{default Gauss linear;}\n"
                                      "divSchemes{default none;} laplacianSchemes{default Gauss linear corrected;}\n"
                                      "interpolationSchemes{default linear;} snGradSchemes{default corrected;}\n")
    (system / "fvSolution").write_text("FoamFile{version 2.0;format ascii;class dictionary;object fvSolution;}\n")
    done = subprocess.run([exe, "-case", str(tmp_path / "case")], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout[-2000:]
    # the topology checks all pass (geometric remarks on merged, non-convex cells are allowed)
    topo = done.stdout[done.stdout.index("Checking topology"):done.stdout.index("Checking geometry")]
    assert "***" not in topo and "Failed" not in topo, topo
    assert "Number of regions: 1" in topo or "Number of regions" not in topo, topo


def test_mesh_source_takes_the_hf_mesh_of_the_same_surfaces_only(tmp_path):
    """meshConfig.meshSource: an L1 / L0 project coarsens the HF case mesh made from the same surfaces; another
    surface snapshot, a changed HF mesh or a missing coarsenLevel stop the run."""
    from types import SimpleNamespace

    from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256
    from backend.aircooling.runner.stlgen_runner import STLGenRunner

    hf = tmp_path / "Base"
    box_mesh(hf / "Mesh")
    (hf / "source-surface-mesh-source.json").write_text(json.dumps(
        {"tri_surface_sha256": "abc", "mesh_sha256": mesh_identity_sha256(hf / "Mesh")}))
    level = tmp_path / "Base_L1"
    level.mkdir()

    def runner(surfaces):
        return SimpleNamespace(case_dir=level, _root_mesh_config={}, _stl_snapshot_sha256=lambda: surfaces)

    mc = {"coarsenLevel": "L1", "meshSource": "../Base"}
    assert STLGenRunner._hf_mesh_source(runner("abc"), mc) == (hf / "Mesh").resolve()
    assert STLGenRunner._hf_mesh_source(runner("abc"), {"coarsenLevel": "L1"}) is None
    with pytest.raises(RuntimeError, match="other surfaces"):
        STLGenRunner._hf_mesh_source(runner("xyz"), mc)
    with pytest.raises(ValueError, match="coarsenLevel"):
        STLGenRunner._hf_mesh_source(runner("abc"), {"meshSource": "../Base"})
    owner = hf / "Mesh" / "constant" / "polyMesh" / "owner"
    owner.write_text(owner.read_text() + "\n")
    with pytest.raises(RuntimeError, match="not the mesh its receipt names"):
        STLGenRunner._hf_mesh_source(runner("abc"), mc)
