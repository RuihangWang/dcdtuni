"""Read real decomposed OpenFOAM data through the browser postprocess API."""

from pathlib import Path
import locale
import re
import shutil

import numpy as np
import pytest

from backend.aircooling.runner import postprocess as pp


def _foam_file(path: Path, kind: str, body: str, note: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "FoamFile\n{\n version 2.0;\n format ascii;\n"
        f" class {kind};\n object {path.name};\n{note}\n}}\n"
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n"
        + body
    )


@pytest.fixture
def partitioned_case(tmp_path):
    """Two adjacent hexahedra, one per rank, with a shared processor face."""
    (tmp_path / "constant").mkdir()
    _foam_file(tmp_path / "system/controlDict", "dictionary", "application simpleFoam;\nstartFrom startTime;\nstartTime 0;\nendTime 200;\ndeltaT 1;\nwriteControl timeStep;\nwriteInterval 100;\n")
    for rank in range(2):
        mesh = tmp_path / f"processor{rank}" / "constant/polyMesh"
        points = [(rank, 0, 0), (rank + 1, 0, 0), (rank + 1, 1, 0), (rank, 1, 0),
                  (rank, 0, 1), (rank + 1, 0, 1), (rank + 1, 1, 1), (rank, 1, 1)]
        _foam_file(mesh / "points", "vectorField", "8\n(\n" + "\n".join(f"({x} {y} {z})" for x, y, z in points) + "\n)\n")
        faces = ["4(0 3 2 1)", "4(4 5 6 7)", "4(0 1 5 4)", "4(3 7 6 2)"]
        left, right = "4(0 4 7 3)", "4(1 2 6 5)"
        faces += [left, right] if rank == 0 else [right, left]
        _foam_file(mesh / "faces", "faceList", "6\n(\n" + "\n".join(faces) + "\n)\n")
        _foam_file(mesh / "owner", "labelList", "6\n(\n0\n0\n0\n0\n0\n0\n)\n",
                   ' note "nPoints:8 nCells:1 nFaces:6 nInternalFaces:0";')
        _foam_file(mesh / "neighbour", "labelList", "0\n(\n)\n")
        interface = f"procBoundary{rank}to{1-rank}"
        _foam_file(mesh / "boundary", "polyBoundaryMesh", f"""2
(
    walls
    {{
        type wall;
        nFaces 5;
        startFace 0;
    }}
    {interface}
    {{
        type processor;
        nFaces 1;
        startFace 5;
        myProcNo {rank};
        neighbProcNo {1-rank};
        transform unknown;
    }}
)
""")
        for value in (0, 100, 200) if rank == 0 else (0, 100):
            time_dir = mesh.parent.parent / str(value)
            boundaries = f"boundaryField\n{{\nwalls {{type zeroGradient;}}\n{interface} {{type processor; value uniform 0;}}\n}}\n"
            _foam_file(time_dir / "T", "volScalarField",
                       f"dimensions [0 0 0 1 0 0 0];\ninternalField uniform {300 + 10*rank};\n" + boundaries)
            _foam_file(time_dir / "U", "volVectorField",
                       "dimensions [0 1 -1 0 0 0 0];\ninternalField uniform (1 0 0);\n"
                       + boundaries.replace("uniform 0", "uniform (1 0 0)"))
    return tmp_path


def test_preview_merges_patches_and_excludes_processor_interfaces(partitioned_case):
    loader = pp.MeshPreviewLoader(partitioned_case)
    binary, meta = loader.load()
    assert len(binary) > 0
    assert [patch["name"] for patch in meta] == ["walls"]
    assert meta[0]["nTriangles"] == 20
    assert loader.load()[0] is binary
    assert loader.read_counts() == {
        "nCells": 2, "nFaces": 11, "nInternalFaces": 1,
        "nBoundaryFaces": 10, "nBoundarySum": 10,
    }


def test_preview_cache_detects_a_change_in_any_rank(partitioned_case):
    cache = pp.MeshPreviewCache()
    first = cache.get(partitioned_case)
    assert cache.get(partitioned_case) is first
    points = partitioned_case / "processor1/constant/polyMesh/points"
    points.write_text(points.read_text() + "\n")
    assert cache.get(partitioned_case) is not first


def test_ascii_faces_can_span_multiple_lines(tmp_path):
    faces = tmp_path / "faces"
    _foam_file(faces, "faceList", "3\n(\n4(0 1 2 3)\n\n5\n(\n4\n5\n6\n7\n8\n)\n3(9 10 11)\n)\n")
    assert pp.MeshPreviewLoader._parse_faces_range(faces, 1, 2) == [[4, 5, 6, 7, 8], [9, 10, 11]]


def test_processor_cyclic_baffle_faces_are_kept(partitioned_case):
    pytest.importorskip("pyvista")
    for rank in range(2):
        boundary = partitioned_case / f"processor{rank}/constant/polyMesh/boundary"
        boundary.write_text(boundary.read_text().replace(
            "type processor;", "type processorCyclic;\n        referPatch walls;"))
    loader = pp.MeshPreviewLoader(partitioned_case)
    _, patches = loader.load()
    assert [patch["name"] for patch in patches] == ["walls"]
    assert patches[0]["nTriangles"] == 24
    assert loader.read_counts() == {
        "nCells": 2, "nFaces": 12, "nInternalFaces": 0,
        "nBoundaryFaces": 12, "nBoundarySum": 12,
    }
    data, _ = pp.read_openfoam_case(partitioned_case, 100)
    assert data["internalMesh"].n_cells == 2
    assert data["boundary"].keys() == ["walls"]
    assert data["boundary"]["walls"].n_cells == 12
    assert np.isfinite(data["boundary"]["walls"].point_data["T"]).all()
    assert not list(partitioned_case.glob(".pyvista-cyclic-*"))


def test_sparse_reader_view_preserves_patch_union_fields_and_cyclic_faces(partitioned_case):
    pytest.importorskip("pyvista")
    originals = {}
    for rank in range(2):
        rank_dir = partitioned_case / f"processor{rank}"
        interface = f"procBoundary{rank}to{1-rank}"
        names = ["left_walls", "right_walls", "door_master", "door_slave"]
        blocks = []
        for index, name in enumerate(names):
            n_faces = 5 if index == rank else 0
            start = 0 if index <= rank else 5
            blocks.append(f"{name}\n{{\n type wall;\n nFaces {n_faces};\n startFace {start};\n}}")
        reference = "door_master" if rank == 0 else "door_slave"
        blocks.append(f"{interface}\n{{\n type processorCyclic;\n nFaces 1;\n startFace 5;\n"
                      f" myProcNo {rank};\n neighbProcNo {1-rank};\n referPatch {reference};\n}}")
        boundary = rank_dir / "constant/polyMesh/boundary"
        _foam_file(boundary, "polyBoundaryMesh", "5\n(\n" + "\n".join(blocks) + "\n)\n")
        for field, kind, dimensions, value, cyclic_value in (
            ("T", "volScalarField", "[0 0 0 1 0 0 0]", str(300 + 10*rank), str(320 + 10*rank)),
            ("U", "volVectorField", "[0 1 -1 0 0 0 0]", f"({rank+1} 0 0)", f"({rank+1} 0 0)"),
        ):
            entries = "\n".join(f"{name} {{type zeroGradient;}}" for name in names)
            _foam_file(rank_dir / "100" / field, kind, f"dimensions {dimensions};\ninternalField uniform {value};\n"
                       f"boundaryField {{\n{entries}\n{interface} {{type processor; value uniform {cyclic_value};}}\n}}")
        for path in (boundary, rank_dir / "100/T", rank_dir / "100/U"):
            originals[path] = path.read_bytes()
    data, info = pp.read_openfoam_case(partitioned_case, 100)
    assert info["readerView"]["emptyPatchesRemoved"] == 6
    assert info["readerView"]["patchCountsBefore"] == [5, 5]
    assert info["readerView"]["patchCountsAfter"] == [2, 2]
    assert data["internalMesh"].n_cells == 2
    np.testing.assert_allclose(data["internalMesh"].cell_data["T"], [300, 310])
    np.testing.assert_allclose(data["internalMesh"].cell_data["U"], [[1, 0, 0], [2, 0, 0]])
    boundary = data["boundary"]
    assert set(boundary.keys()) == {"left_walls", "right_walls", "door_master", "door_slave"}
    assert [boundary[name].n_cells for name in ("left_walls", "right_walls", "door_master", "door_slave")] == [5, 5, 1, 1]
    np.testing.assert_allclose(boundary["door_master"].cell_data["T"], 320)
    np.testing.assert_allclose(boundary["door_slave"].cell_data["T"], 330)
    for path, original in originals.items():
        assert path.read_bytes() == original
    assert not list(partitioned_case.glob(".pyvista-*"))


def test_mesh_only_partition_can_be_read_at_zero(partitioned_case):
    pytest.importorskip("pyvista")
    import shutil
    for rank in range(2):
        for time_dir in (partitioned_case / f"processor{rank}").iterdir():
            if time_dir.name.isdigit():
                shutil.rmtree(time_dir)
    data, info = pp.read_openfoam_case(partitioned_case, 0)
    assert info["timeValue"] == 0
    assert data["internalMesh"].n_cells == 2


def test_native_reader_preserves_utf8_process_locale(partitioned_case):
    pytest.importorskip("pyvista")
    original = locale.setlocale(locale.LC_ALL)
    try:
        locale.setlocale(locale.LC_CTYPE, "C.UTF-8")
        expected = locale.setlocale(locale.LC_ALL)
        pp.read_openfoam_case(partitioned_case, 100)
        assert locale.setlocale(locale.LC_ALL) == expected
        text_file = partitioned_case / "unicode.txt"
        text_file.write_text("中文文本", encoding="utf-8")
        assert text_file.read_text() == "中文文本"
    finally:
        locale.setlocale(locale.LC_ALL, original)


def test_native_reader_restores_locale_on_failure(tmp_path, monkeypatch):
    original = locale.setlocale(locale.LC_ALL)

    def failed_read(*args, **kwargs):
        locale.setlocale(locale.LC_ALL, "C")
        raise RuntimeError("reader failed")

    monkeypatch.setattr(pp, "_read_openfoam_case", failed_read)
    with pytest.raises(RuntimeError, match="reader failed"):
        pp.read_openfoam_case(tmp_path)
    assert locale.setlocale(locale.LC_ALL) == original


def test_reader_loads_latest_common_time_and_physical_boundaries(partitioned_case):
    pytest.importorskip("pyvista")
    marker = partitioned_case / "pyvista_reader.foam"
    marker.write_text("user-owned marker")
    data, info = pp.read_openfoam_case(partitioned_case)
    assert {key: info[key] for key in ("sourceFormat", "decomposed", "timeValue")} == {
        "sourceFormat": "openfoam", "decomposed": True, "timeValue": 100.0,
    }
    assert info["pointNormalization"]["nCells"] == 2
    assert info["pointNormalization"]["internalPointsBefore"] == 16
    assert info["pointNormalization"]["internalPointsAfter"] == 12
    internal = data["internalMesh"]
    assert internal.n_cells == 2
    np.testing.assert_allclose(sorted(internal.cell_data["T"]), [300, 310])
    assert "U" in internal.point_data
    assert data["boundary"].keys() == ["walls"]
    assert data["boundary"]["walls"].n_cells == 10
    assert marker.read_text() == "user-owned marker"
    assert list(partitioned_case.glob("pyvista_*.foam")) == [marker]
    assert not (partitioned_case / "VTK").exists()
    with pytest.raises(ValueError, match="every mesh"):
        pp.read_openfoam_case(partitioned_case, 200)


def _assert_same_packed(a, b, where="dataset"):
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        assert isinstance(a, np.ndarray) and isinstance(b, np.ndarray), where
        assert a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b), where
    elif isinstance(a, dict):
        assert isinstance(b, dict) and a.keys() == b.keys(), where
        for key in a:
            _assert_same_packed(a[key], b[key], f"{where}.{key}")
    elif isinstance(a, (list, tuple)):
        assert type(a) is type(b) and len(a) == len(b), where
        for index, (x, y) in enumerate(zip(a, b)):
            _assert_same_packed(x, y, f"{where}[{index}]")
    else:
        assert a == b, (where, a, b)


def test_worker_processes_read_the_same_blocks_as_one_process(partitioned_case, monkeypatch):
    pytest.importorskip("pyvista")
    read = {}
    for processes in ("1", "2"):
        monkeypatch.setenv("DCDTUNI_PP_READ_PROCESSES", processes)
        data, _ = pp.read_openfoam_case(partitioned_case)
        boundary = data["boundary"]
        read[processes] = {"internal": pp._pack_vtk(data["internalMesh"]), "names": boundary.keys(),
                           "patches": [pp._pack_vtk(boundary[i]) for i in range(len(boundary.keys()))]}
    _assert_same_packed(read["1"], read["2"])


def test_packed_blocks_rebuild_exactly_with_polyhedra_and_arrays():
    pv = pytest.importorskip("pyvista")
    from vtkmodules.vtkCommonDataModel import VTK_POLYHEDRON

    # One hexahedron and one polyhedron (a cube given by its six faces).
    points = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
                      dtype=np.float32)
    faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [3, 7, 6, 2], [0, 4, 7, 3], [1, 2, 6, 5]]
    polyhedron = [len(faces)] + [value for face in faces for value in [len(face), *face]]
    grid = pv.UnstructuredGrid(np.array([8, 0, 1, 2, 3, 4, 5, 6, 7, len(polyhedron), *polyhedron]),
                               np.array([pv.CellType.HEXAHEDRON, VTK_POLYHEDRON], dtype=np.uint8), points)
    grid.cell_data["T"] = np.array([300.5, 301.25])
    grid.cell_data["U"] = np.arange(6, dtype=np.float32).reshape(2, 3)
    grid.point_data["id"] = np.arange(8, dtype=np.int32)
    grid.cell_data.active_scalars_name = "T"
    surface = grid.extract_surface(algorithm="dataset_surface")
    surface.cell_data["flag"] = np.arange(surface.n_cells, dtype=np.int64)
    for dataset in (grid, surface):
        packed = pp._pack_vtk(dataset)
        rebuilt = pp._unpack_vtk(packed)
        _assert_same_packed(packed, pp._pack_vtk(rebuilt))
        assert rebuilt.GetClassName() == dataset.GetClassName()
    assert pp._pack_vtk(grid)["faces"] is not None
    assert pp._pack_vtk(grid)["cellActive"] == {0: "T"}


def test_vtk_data_classes_wrap_without_numpy_overrides():
    """VTK's data_model overrides made every dropped getter wrapper a ghost."""
    pytest.importorskip("pyvista")
    pp._get_pyvista()
    from vtkmodules.vtkCommonDataModel import vtkCellData, vtkPolyData

    assert type(vtkPolyData().GetCellData()) is vtkCellData


def test_interface_interpolation_matches_serial_and_streamlines_cross_ranks(partitioned_case):
    pv = pytest.importorskip("pyvista")
    # Match a physical parallel field: U=(1,0,0) on rank 0 and (2,0,0) on
    # rank 1, with processor values sampled from the neighbouring rank.
    for rank in range(2):
        time_dir = partitioned_case / f"processor{rank}/100"
        interface = f"procBoundary{rank}to{1-rank}"
        for field, kind, dimensions, value, neighbour in (
            ("T", "volScalarField", "[0 0 0 1 0 0 0]", str(300 + 10*rank), str(310 - 10*rank)),
            ("U", "volVectorField", "[0 1 -1 0 0 0 0]", f"({1+rank} 0 0)", f"({2-rank} 0 0)"),
        ):
            _foam_file(time_dir / field, kind, f"dimensions {dimensions};\ninternalField uniform {value};\n"
                       f"boundaryField {{ walls {{type zeroGradient;}} {interface} {{type processor; value uniform {neighbour};}} }}")
    serial = partitioned_case / "serial-reference"
    mesh = serial / "constant/polyMesh"
    points = [(0,0,0), (1,0,0), (1,1,0), (0,1,0), (0,0,1), (1,0,1),
              (1,1,1), (0,1,1), (2,0,0), (2,1,0), (2,0,1), (2,1,1)]
    _foam_file(mesh / "points", "vectorField", "12\n(\n" + "\n".join(f"({x} {y} {z})" for x,y,z in points) + "\n)\n")
    faces = [[1, 2, 6, 5]]  # Shared internal face points out of owner cell 0.
    local_walls = [[0,3,2,1], [4,5,6,7], [0,1,5,4], [3,7,6,2]]
    for rank, mapping in enumerate(([0,1,2,3,4,5,6,7], [1,8,9,2,5,10,11,6])):
        for face in local_walls + ([[0,4,7,3]] if rank == 0 else [[1,2,6,5]]):
            faces.append([mapping[i] for i in face])
    _foam_file(mesh / "faces", "faceList", "11\n(\n" + "\n".join("4(" + " ".join(map(str, face)) + ")" for face in faces) + "\n)\n")
    _foam_file(mesh / "owner", "labelList", "11\n(\n" + "\n".join(map(str, [0]*6 + [1]*5)) + "\n)\n",
               'note "nPoints:12 nCells:2 nFaces:11 nInternalFaces:1";')
    _foam_file(mesh / "neighbour", "labelList", "1\n(\n1\n)\n")
    _foam_file(mesh / "boundary", "polyBoundaryMesh", "1\n(\n walls\n {\n type wall;\n nFaces 10;\n startFace 1;\n }\n)\n")
    _foam_file(serial / "system/controlDict", "dictionary", "application simpleFoam; startFrom startTime; startTime 0; endTime 100; deltaT 1; writeControl timeStep; writeInterval 100;")
    _foam_file(serial / "100/T", "volScalarField", "dimensions [0 0 0 1 0 0 0]; internalField nonuniform List<scalar> 2(300 310); boundaryField {walls {type zeroGradient;}}")
    _foam_file(serial / "100/U", "volVectorField", "dimensions [0 1 -1 0 0 0 0]; internalField nonuniform List<vector> 2((1 0 0) (2 0 0)); boundaryField {walls {type zeroGradient;}}")
    reference, _ = pp.read_openfoam_case(serial, 100)
    decomposed, info = pp.read_openfoam_case(partitioned_case, 100)
    for actual, expected in ((decomposed["internalMesh"], reference["internalMesh"]),
                             (decomposed["boundary"]["walls"], reference["boundary"]["walls"])):
        assert actual.n_points == expected.n_points == 12
        indices = np.flatnonzero(actual.points[:, 0] == 1)
        assert len(indices) == 4
        np.testing.assert_allclose(actual.point_data["T"][indices], 305)
        np.testing.assert_allclose(actual.point_data["U"][indices], np.tile([1.5, 0, 0], (4, 1)))
        actual_order = np.lexsort(actual.points.T)
        expected_order = np.lexsort(expected.points.T)
        for field in ("T", "U"):
            np.testing.assert_allclose(actual.point_data[field][actual_order], expected.point_data[field][expected_order])
    internal = decomposed["internalMesh"]
    assert internal.n_cells == info["pointNormalization"]["nCells"] == 2
    np.testing.assert_allclose(internal.cell_data["T"], [300, 310])
    np.testing.assert_allclose(internal.cell_data["U"], [[1, 0, 0], [2, 0, 0]])
    stream = internal.streamlines_from_source(pv.PolyData([[0.25, 0.5, 0.5]]), vectors="U",
                                             integration_direction="forward", max_steps=200,
                                             initial_step_length=0.05, max_step_length=0.1)
    assert stream.n_cells == 1
    assert stream.points[:, 0].min() < 1 < stream.points[:, 0].max()
    assert np.isfinite(stream.point_data["U"]).all()


def test_point_merge_retains_point_only_arrays_and_separate_physical_patches():
    pv = pytest.importorskip("pyvista")
    left = pv.ImageData(dimensions=(2, 2, 2)).cast_to_unstructured_grid()
    right = left.translate((1, 0, 0))
    mesh = left.merge(right, merge_points=False)
    mesh.point_data["point_only"] = np.full(mesh.n_points, 7.0)
    mesh.cell_data["T"] = [300.0, 310.0]
    surface = left.extract_surface()
    master, slave = surface.copy(), surface.copy()
    master.cell_data["T"] = np.full(master.n_cells, 300.0)
    slave.cell_data["T"] = np.full(slave.n_cells, 310.0)
    data = pv.MultiBlock({"internalMesh": mesh, "boundary": pv.MultiBlock({"master": master, "slave": slave})})
    pp._normalize_decomposed_points(data)
    assert data["internalMesh"].n_points == 12
    np.testing.assert_allclose(data["internalMesh"].point_data["point_only"], 7)
    np.testing.assert_allclose(data["boundary"]["master"].point_data["T"], 300)
    np.testing.assert_allclose(data["boundary"]["slave"].point_data["T"], 310)


def test_boundary_processing_uses_indices_and_keeps_patch_names(monkeypatch):
    """Name-based MultiBlock lookup is linear and becomes quadratic on Base604."""
    pv = pytest.importorskip("pyvista")
    internal = pv.ImageData(dimensions=(2, 2, 2)).cast_to_unstructured_grid()
    internal.cell_data["T"] = [300.0]
    triangle = pv.PolyData([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], [3, 0, 1, 2])
    triangle.cell_data["T"] = [300.0]
    names = [f"physical_patch_{index}" for index in range(16)]
    boundary = pv.MultiBlock({name: triangle.copy() for name in names})
    data = pv.MultiBlock({"internalMesh": internal, "boundary": boundary})
    original_lookup = pv.MultiBlock.get_index_by_name

    def checked_lookup(self, name):
        if self is boundary:
            pytest.fail("physical boundary loops must use constant-time integer indices")
        return original_lookup(self, name)

    monkeypatch.setattr(pv.MultiBlock, "get_index_by_name", checked_lookup)
    pp._normalize_decomposed_points(data)
    assert boundary.keys() == names
    cache = pp.CachedPostProcessor()
    cache._boundary = boundary
    assert [patch["name"] for patch in cache.get_mesh()[1]] == names


def test_cached_api_supports_surface_slice_and_volume_without_export(partitioned_case, monkeypatch):
    pytest.importorskip("pyvista")

    def forbidden_export(*args, **kwargs):
        pytest.fail("decomposed postprocessing must not run foamToVTK")

    monkeypatch.setattr(pp.PostProcessor, "convert_to_vtk", forbidden_export)
    cache = pp.CachedPostProcessor()
    info = cache.init(partitioned_case)
    assert info["success"] and info["decomposed"]
    assert info["n_cells"] == 2
    assert info["timing"]["foamToVTK"] == 0
    assert cache._vtm_path is None
    mesh, patches = cache.get_mesh()
    field, field_info = cache.get_field("T")
    assert mesh and field
    assert field_info["nPoints"] == sum(p["nPoints"] for p in patches)
    assert field_info["min"] == pytest.approx(300)
    assert field_info["max"] == pytest.approx(310)
    velocity, velocity_info = cache.get_field("Umag")
    assert velocity and velocity_info["max"] == pytest.approx(1)
    sliced, slice_info = cache.get_slice((0, 0, 1), (1, 0.5, 0.5), "T")
    assert sliced and slice_info["nTriangles"] > 0
    volume, volume_info = cache.get_volume("T", resolution=8)
    assert volume and "error" not in volume_info
    cached = cache.init(partitioned_case)
    assert cached["cached"]
    for name in ("sourceFormat", "decomposed", "timeValue", "n_cells"):
        assert cached[name] == info[name]
    cache.invalidate()
    assert not cache._source_info
    legacy_wrapper = pp.PostProcessor(partitioned_case)
    assert legacy_wrapper.load_vtk()["decomposed"]
    assert legacy_wrapper._vtm_path is None


def test_prefetched_init_gives_the_same_mesh_fields_and_volume(partitioned_case, monkeypatch):
    pytest.importorskip("pyvista")
    # Two cells: let them take the cell-centre volume path the prefetch indexes.
    monkeypatch.setattr(pp.CachedPostProcessor, "_VOLUME_PROBE_MAX_CELLS", 0)
    fresh = pp.CachedPostProcessor()
    assert "prefetched" not in fresh.init(partitioned_case)
    assert getattr(fresh, "_volume_index", None) is None  # built on the first volume, not at init
    expected = (fresh.get_mesh(), fresh.get_field("T"), fresh.get_field("Umag"))
    centres, _, spacing = fresh._volume_centres()
    fresh.invalidate()
    assert fresh._volume_index is None
    monkeypatch.setattr(pp, "RESULT_PREFETCH", pp._ResultPrefetch())
    assert pp.RESULT_PREFETCH.start(partitioned_case, 100.0)
    cache = pp.CachedPostProcessor()
    assert cache.init(partitioned_case)["prefetched"]
    assert cache._tri_cache  # triangulated by the prefetch, before any get_mesh
    assert cache._volume_index is not None  # and the volume's KD-tree
    assert cache._volume_index[0] == (id(cache._internal), cache._internal.n_cells)
    assert (cache.get_mesh(), cache.get_field("T"), cache.get_field("Umag")) == expected
    got_centres, _, got_spacing = cache._volume_centres()
    np.testing.assert_array_equal(got_centres, centres)
    np.testing.assert_array_equal(got_spacing, spacing)


def test_cached_init_switches_explicit_openfoam_time_without_reusing_previous_fields(partitioned_case):
    pytest.importorskip("pyvista")
    rank1 = partitioned_case / "processor1"
    shutil.copytree(rank1 / "100", rank1 / "200")
    for rank in range(2):
        step = partitioned_case / f"processor{rank}" / "200"
        old_t = 300 + 10 * rank
        t_path = step / "T"
        t_path.write_text(t_path.read_text().replace(
            f"internalField uniform {old_t};", f"internalField uniform {320 + 10 * rank};"))
        u_path = step / "U"
        u_path.write_text(u_path.read_text().replace(
            "internalField uniform (1 0 0);", f"internalField uniform ({2 + rank} 0 0);"))

    cache = pp.CachedPostProcessor()
    first = cache.init(partitioned_case, time_value=100)
    assert first["timeValue"] == 100
    np.testing.assert_allclose(cache._internal.cell_data["T"], [300, 310])
    np.testing.assert_allclose(cache._internal.cell_data["U"][:, 0], [1, 1])

    second = cache.init(partitioned_case, time_value=200)
    assert second["timeValue"] == 200
    assert not second["cached"]
    np.testing.assert_allclose(cache._internal.cell_data["T"], [320, 330])
    np.testing.assert_allclose(cache._internal.cell_data["U"][:, 0], [2, 3])
    assert cache.init(partitioned_case, time_value=200)["cached"]

    with pytest.raises(ValueError, match="Time 999 is not available"):
        cache.init(partitioned_case, time_value=999)
    assert cache.multi_block is not None
    assert cache.init(partitioned_case, time_value=200)["cached"]
    np.testing.assert_allclose(cache._internal.cell_data["T"], [320, 330])


def test_explicit_vtm_file_import_remains_supported(tmp_path, monkeypatch):
    pv = pytest.importorskip("pyvista")
    mesh = pv.ImageData(dimensions=(2, 2, 2)).cast_to_unstructured_grid()
    mesh.point_data["T"] = np.full(mesh.n_points, 301.0)
    data = pv.MultiBlock({"internal": mesh, "boundary": pv.MultiBlock({"walls": mesh.extract_surface()})})
    vtk_dir = tmp_path / "VTK"
    vtk_dir.mkdir()
    vtm = vtk_dir / "case_100.vtm"
    data.save(vtm)

    def forbidden_export(*args, **kwargs):
        pytest.fail("explicit VTM import must not run foamToVTK")

    monkeypatch.setattr(pp.PostProcessor, "convert_to_vtk", forbidden_export)
    cache = pp.CachedPostProcessor()
    info = cache.init(vtm)
    assert info["success"] and info["sourceFormat"] == "vtm"
    assert not info["decomposed"]
    assert cache.get_field("T")[1]["max"] == pytest.approx(301)
    with pytest.raises(FileNotFoundError, match="No complete OpenFOAM mesh"):
        pp.CachedPostProcessor().init(tmp_path)


def _copy_serial_mesh_and_time(partitioned_case, destination, time_name):
    rank = partitioned_case / "processor0"
    shutil.copytree(rank / "constant", destination / "constant", dirs_exist_ok=True)
    shutil.copytree(rank / "100", destination / time_name)
    boundary = destination / "constant/polyMesh/boundary"
    boundary.write_text(boundary.read_text().replace("type processor;", "type wall;"))
    for field in (destination / time_name).iterdir():
        field.write_text(re.sub(r"type processor; value uniform [^;]*;", "type zeroGradient;", field.read_text()))
    if destination != partitioned_case:
        shutil.copytree(partitioned_case / "system", destination / "system")


def test_serial_gui_reads_native_fields_and_ignores_stale_vtk(partitioned_case, monkeypatch):
    pytest.importorskip("pyvista")
    serial = partitioned_case / "serial-native"
    _copy_serial_mesh_and_time(partitioned_case, serial, "100")
    (serial / "VTK").mkdir()
    stale = serial / "VTK/stale_999.vtm"
    stale.write_text("An obsolete export must never be consulted")

    def forbidden_export(*args, **kwargs):
        pytest.fail("serial postprocessing must not run foamToVTK")

    monkeypatch.setattr(pp.PostProcessor, "convert_to_vtk", forbidden_export)
    cache = pp.CachedPostProcessor()
    info = cache.init(serial)
    assert info["sourceFormat"] == "openfoam" and not info["decomposed"]
    assert info["timeValue"] == 100
    assert info["timing"]["foamToVTK"] == 0
    assert cache._vtm_path is None
    assert cache.get_field("T")[1]["min"] == pytest.approx(300)
    wrapper = pp.PostProcessor(serial)
    assert wrapper.load_vtk()["sourceFormat"] == "openfoam"
    assert wrapper._vtm_path is None
    assert stale.read_text() == "An obsolete export must never be consulted"


@pytest.mark.parametrize("root_time, expected_partitioned, expected_time", [
    ("50", True, 100), ("100", False, 100), ("300", False, 300),
])
def test_latest_complete_native_results_choose_layout(
    partitioned_case, root_time, expected_partitioned, expected_time,
):
    pytest.importorskip("pyvista")
    _copy_serial_mesh_and_time(partitioned_case, partitioned_case, root_time)
    # Rank 0 has time 200; an empty rank 1 directory is still an incomplete write.
    (partitioned_case / "processor1/200").mkdir()
    data, info = pp.read_openfoam_case(partitioned_case)
    assert info["decomposed"] == expected_partitioned
    assert info["timeValue"] == expected_time
    assert data["internalMesh"].n_cells == (2 if expected_partitioned else 1)


def test_native_read_failure_does_not_fall_back_to_vtk(partitioned_case, monkeypatch):
    (partitioned_case / "VTK").mkdir()
    (partitioned_case / "VTK/old_100.vtm").write_text("stale")

    def failed_read(*args, **kwargs):
        raise RuntimeError("native data unreadable")

    def forbidden_export(*args, **kwargs):
        pytest.fail("native read failure must not trigger foamToVTK")

    monkeypatch.setattr(pp, "read_openfoam_case", failed_read)
    monkeypatch.setattr(pp.PostProcessor, "convert_to_vtk", forbidden_export)
    cache = pp.CachedPostProcessor()
    with pytest.raises(RuntimeError, match="native data unreadable"):
        cache.init(partitioned_case)
    assert cache.multi_block is None
    assert cache._vtm_path is None


@pytest.mark.parametrize("reverse", [False, True])
def test_concave_cut_face_triangles_stay_inside_polygon_and_keep_point_fields(reverse):
    pv = pytest.importorskip("pyvista")
    points = np.array([[3, 0, 0], [3, 1, 0], [1, 1, 0], [1, 3, 0], [0, 3, 0], [0, 0, 0]], dtype=float)
    vertices = list(range(6))
    if reverse:
        vertices.reverse()
    patch = pv.PolyData(points, [6, *vertices])
    values = 300 + points[:, 0] + 2 * points[:, 1]
    patch.point_data["T"] = values
    cache = pp.CachedPostProcessor()
    cache._boundary = pv.MultiBlock({"cut_face": patch})
    cache.get_mesh()
    display = cache._tri_cache["cut_face"]
    triangles = display["points"][display["indices"]]
    areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1) / 2
    assert len(areas) == 4 and np.all(areas > 0)
    assert areas.sum() == pytest.approx(5.0)
    for triangle in triangles:
        for i in range(11):
            for j in range(11 - i):
                sample = (i * triangle[0] + j * triangle[1] + (10 - i - j) * triangle[2]) / 10
                assert sample[0] <= 1.00001 or sample[1] <= 1.00001
    field, info = cache.get_field("T")
    # Header holds min/max float32 and component/point counts as uint32.
    scalars = np.frombuffer(field, dtype=np.float32, offset=16)
    expected = 300 + display["points"][:, 0] + 2 * display["points"][:, 1]
    np.testing.assert_allclose(scalars, expected)
    assert info["nPoints"] == len(display["points"])
    np.testing.assert_array_equal(patch.points, points)
    np.testing.assert_array_equal(patch.point_data["T"], values)


def test_sharp_surface_edges_split_display_vertices_without_changing_fields():
    pv = pytest.importorskip("pyvista")
    # Two perpendicular rectangles with different triangle areas sharing an edge.
    points = np.array([[0, 0, 0], [2, 0, 0], [2, 1, 0], [0, 1, 0],
                       [0, 0, 3], [2, 0, 3]], dtype=float)
    patch = pv.PolyData(points, [3, 0, 1, 2, 3, 0, 2, 3, 3, 0, 4, 5, 3, 0, 5, 1])
    values = 300 + points @ [1, 2, 3]
    patch.point_data["T"] = values
    patch.point_data["U"] = points + [1, 2, 3]
    display = pp._surface_triangle_cache(patch)
    tri = display["tri"]
    assert tri.n_points == 8  # Only the two sharp-edge vertices are duplicated.
    triangles = display["points"][display["indices"]]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    # Match the browser's area-weighted indexed computeVertexNormals operation.
    accumulated = np.zeros_like(display["points"])
    for indices, normal in zip(display["indices"], normals):
        accumulated[indices] += normal
    accumulated /= np.linalg.norm(accumulated, axis=1)[:, None]
    for indices, normal in zip(display["indices"], normals):
        np.testing.assert_allclose(accumulated[indices], np.tile(normal / np.linalg.norm(normal), (3, 1)))
    np.testing.assert_allclose(tri.point_data["T"], 300 + tri.points @ [1, 2, 3])
    np.testing.assert_allclose(tri.point_data["U"], tri.points + [1, 2, 3])
    assert patch.n_points == 6
    np.testing.assert_array_equal(patch.point_data["T"], values)


def test_cell_field_interpolation_precedes_display_splitting():
    pv = pytest.importorskip("pyvista")
    patch = pv.PolyData([[0., 0., 0.], [2., 0., 0.], [2., 1., 0.], [0., 1., 0.],
                         [0., 0., 3.], [2., 0., 3.]], [4, 0, 1, 2, 3, 4, 0, 4, 5, 1])
    patch.cell_data["T"] = [300., 320.]
    display = pp._surface_triangle_cache(patch)["tri"]
    shared = (display.points[:, 1] == 0) & (display.points[:, 2] == 0)
    np.testing.assert_allclose(display.point_data["T"][shared], 310)
    np.testing.assert_array_equal(patch.cell_data["T"], [300., 320.])


def test_display_preparation_preserves_existing_point_fields_when_another_field_is_cell_only():
    pv = pytest.importorskip("pyvista")
    patch = pv.PolyData([[0., 0., 0.], [2., 0., 0.], [2., 1., 0.], [0., 1., 0.]], [4, 0, 1, 2, 3])
    patch.point_data["T"] = [300., 310., 320., 330.]
    patch.cell_data["T"] = [315.]
    patch.cell_data["p"] = [2.]
    display = pp._surface_triangle_cache(patch)["tri"]
    np.testing.assert_array_equal(display.point_data["T"], patch.point_data["T"])
    np.testing.assert_allclose(display.point_data["p"], 2.)
    assert "p" not in patch.point_data
