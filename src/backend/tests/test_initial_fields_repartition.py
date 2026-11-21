"""AI initial fields given in the mesher's partition order still seed a solve that repartitioned the mesh
(reconstructParMesh + decomposePar to more ranks): Alibaba_hangchou meshed on 8 ranks, solved on 32."""
import numpy as np

from backend.aircooling.runner.initial_fields import RECONSTRUCTED_FROM, apply_initial_fields

HEADER = "FoamFile\n{\n    format ascii;\n    class %s;\n    object %s;\n}\n"


def _labels(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER % ("labelList", path.name) + f"{len(values)}\n(\n" + "\n".join(map(str, values)) + "\n)\n")


def _rank(run, k, cells, serial):
    poly = run / f"processor{k}" / "constant" / "polyMesh"
    for name in ("points", "faces", "boundary"):
        (poly / name).parent.mkdir(parents=True, exist_ok=True)
        (poly / name).write_text("x")
    _labels(poly / "owner", list(range(cells)))
    _labels(poly / "neighbour", [])
    _labels(poly / "cellProcAddressing", serial)
    zero = run / f"processor{k}" / "0"
    zero.mkdir()
    for f in ("U", "p_rgh", "T"):
        (zero / f).write_text(HEADER % ("volField", f) + "internalField   uniform 0;\nboundaryField {}\n")


def test_partition_order_fields_follow_the_reconstruction(tmp_path):
    run = tmp_path / "run"
    # the mesher's two partitions held serial cells [3, 0] and [1, 4, 2]
    saved = run / RECONSTRUCTED_FROM
    _labels(saved / "processor0.cellProcAddressing", [3, 0])
    _labels(saved / "processor1.cellProcAddressing", [1, 4, 2])
    # the solve's three partitions hold serial [0, 1], [2, 3], [4]
    for k, serial in enumerate(([0, 1], [2, 3], [4])):
        _rank(run, k, len(serial), serial)
    t = np.array([13.0, 10.0, 11.0, 14.0, 12.0])        # partition order: serial cell s has T = 10 + s
    npz = tmp_path / "init.npz"
    np.savez(npz, U=np.zeros((5, 3)), p_rgh=np.zeros(5), T=t, rank_cells=np.array([2, 3]))
    apply_initial_fields(run, npz)
    got = [(run / f"processor{k}" / "0" / "T").read_text().split("(")[1].split(")")[0].split() for k in range(3)]
    assert [[float(v) for v in g] for g in got] == [[10.0, 11.0], [12.0, 13.0], [14.0]]


def test_partition_order_without_the_reconstruction_is_refused(tmp_path):
    import pytest
    run = tmp_path / "run"
    for k, serial in enumerate(([0, 1], [2, 3], [4])):
        _rank(run, k, len(serial), serial)
    npz = tmp_path / "init.npz"
    np.savez(npz, U=np.zeros((5, 3)), p_rgh=np.zeros(5), T=np.zeros(5), rank_cells=np.array([2, 3]))
    with pytest.raises(ValueError, match="values for 2 partitions, the solve has 3"):
        apply_initial_fields(run, npz)
