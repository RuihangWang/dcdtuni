"""The SCANO AI solve's data path (backend.aircooling.scano) on a two-cell mesh written here.

Two unit cubes along x: cell 0 in [0, 1], cell 1 in [1, 2]. Patches: inlet (x = 0, patch), outlet (x = 2, patch),
walls (the eight side faces, wall). The decomposed copy puts one cell on each of two ranks with a processor patch
at x = 1. The end-to-end checks against the DCTS dataset (a real room, the paper's error) are in
docs/benchmarks/scano-ai-2026-10-27/.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

from backend.aircooling.scano import dcts_cache, pipeline
from backend.aircooling.scano.foam_fields import boundary_values, read_field
from backend.aircooling.scano.foam_mesh import face_geometry, read_graph
from backend.aircooling.scano.foam_writer import write_prediction

HEADER = 'FoamFile\n{{\n    version 2.0;\n    format ascii;\n    class {cls};\n    object {obj};\n}}\n' \
         '// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n'


def _write(path: Path, cls: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER.format(cls=cls, obj=path.name) + body)


def _labels(values) -> str:
    return f'{len(values)}\n(\n' + '\n'.join(str(v) for v in values) + '\n)\n'


def _mesh(poly: Path, points, faces, owner, neighbour, patches) -> None:
    _write(poly / 'points', 'vectorField',
           f'{len(points)}\n(\n' + '\n'.join(f'({x} {y} {z})' for x, y, z in points) + '\n)\n')
    _write(poly / 'faces', 'faceList',
           f'{len(faces)}\n(\n' + '\n'.join(f'{len(f)}(' + ' '.join(map(str, f)) + ')' for f in faces) + '\n)\n')
    _write(poly / 'owner', 'labelList', _labels(owner))
    _write(poly / 'neighbour', 'labelList', _labels(neighbour))
    body = f'{len(patches)}\n(\n'
    for name, d in patches:
        body += f'    {name}\n    {{\n' + ''.join(f'        {k} {v};\n' for k, v in d.items()) + '    }\n'
    _write(poly / 'boundary', 'polyBoundaryMesh', body + ')\n')


def _box_faces(x0):
    """Point indices of the six faces of the unit cube [x0, x0 + 1] on a 3 x 2 x 2 point lattice (outward)."""
    p = lambda i, j, k: i * 4 + j * 2 + k                     # noqa: E731
    a = x0
    return {'x-': [p(a, 0, 0), p(a, 0, 1), p(a, 1, 1), p(a, 1, 0)],
            'x+': [p(a + 1, 0, 0), p(a + 1, 1, 0), p(a + 1, 1, 1), p(a + 1, 0, 1)],
            'y-': [p(a, 0, 0), p(a + 1, 0, 0), p(a + 1, 0, 1), p(a, 0, 1)],
            'y+': [p(a, 1, 0), p(a, 1, 1), p(a + 1, 1, 1), p(a + 1, 1, 0)],
            'z-': [p(a, 0, 0), p(a, 1, 0), p(a + 1, 1, 0), p(a + 1, 0, 0)],
            'z+': [p(a, 0, 1), p(a + 1, 0, 1), p(a + 1, 1, 1), p(a, 1, 1)]}


LATTICE = [(i, j, k) for i in range(3) for j in range(2) for k in range(2)]


def reconstructed_case(root: Path) -> Path:
    c0, c1 = _box_faces(0), _box_faces(1)
    walls = [c0[s] for s in ('y-', 'y+', 'z-', 'z+')] + [c1[s] for s in ('y-', 'y+', 'z-', 'z+')]
    faces = [c0['x+']] + [c0['x-']] + [c1['x+']] + walls
    owner = [0, 0, 1] + [0, 0, 0, 0, 1, 1, 1, 1]
    _mesh(root / 'constant' / 'polyMesh', LATTICE, faces, owner, [1],
          [('inlet', {'type': 'patch', 'nFaces': 1, 'startFace': 1}),
           ('outlet', {'type': 'patch', 'nFaces': 1, 'startFace': 2}),
           ('walls', {'type': 'wall', 'nFaces': 8, 'startFace': 3})])
    return root


def decomposed_case(root: Path) -> Path:
    for rank, x0 in ((0, 0), (1, 1)):
        c = _box_faces(x0)
        own_patch = ('inlet', c['x-']) if rank == 0 else ('outlet', c['x+'])
        shared = c['x+'] if rank == 0 else c['x-']
        faces = [own_patch[1]] + [c[s] for s in ('y-', 'y+', 'z-', 'z+')] + [shared]
        _mesh(root / f'processor{rank}' / 'constant' / 'polyMesh', LATTICE, faces, [0] * 6, [],
              [(own_patch[0], {'type': 'patch', 'nFaces': 1, 'startFace': 0}),
               ('walls', {'type': 'wall', 'nFaces': 4, 'startFace': 1}),
               (f'procBoundary{rank}to{1 - rank}', {'type': 'processor', 'nFaces': 1, 'startFace': 5,
                                                   'myProcNo': rank, 'neighbProcNo': 1 - rank})])
    return root


def test_face_geometry_unit_cube():
    pts = np.array(LATTICE, float)
    faces = list(_box_faces(0).values())
    off = np.cumsum([0] + [len(f) for f in faces])
    n, a, c = face_geometry(pts, off, np.concatenate(faces))
    assert np.allclose(a, 1.0)
    assert np.allclose(np.abs(n).max(1), 1.0)
    assert np.allclose(c[0], [0, 0.5, 0.5]) and np.allclose(c[1], [1, 0.5, 0.5])


def test_reconstructed_graph(tmp_path):
    g = read_graph(reconstructed_case(tmp_path))
    assert g.n_cells == 2 and g.n_boundary == 10
    assert np.allclose(g.volume, 1.0)
    assert np.allclose(g.pos[:2], [[0.5, 0.5, 0.5], [1.5, 0.5, 0.5]])
    assert [p.name for p in g.patches] == ['inlet', 'outlet', 'walls']
    assert list(g.kind) == [0, 0, 2, 2] + [1] * 8
    # every boundary normal points away from its owner cell
    own = g.edges[0, -g.n_boundary:]
    assert (np.einsum('ij,ij->i', g.normal, g.pos[2:] - g.pos[own]) > 0).all()
    assert np.allclose(g.normal[0], [-1, 0, 0]) and np.allclose(g.normal[1], [1, 0, 0])
    assert np.array_equal(g.edges[:, 0], [0, 1])                 # the internal face first
    assert np.array_equal(g.edges[1, 1:], np.arange(2, 12))


def test_decomposed_graph_matches_reconstructed(tmp_path):
    rec = read_graph(reconstructed_case(tmp_path / 'r'))
    dec = read_graph(decomposed_case(tmp_path / 'd'), 'processors')
    assert dec.n_cells == rec.n_cells and dec.n_boundary == rec.n_boundary
    assert [p.name for p in dec.patches] == ['inlet', 'walls', 'outlet']      # rank 0's order, then new names
    assert dec.edges.shape == rec.edges.shape
    assert {tuple(sorted(e)) for e in dec.edges[:, :1].T} == {(0, 1)}          # the processor face is internal
    for name in ('inlet', 'outlet', 'walls'):
        i_r = [p.name for p in rec.patches].index(name)
        i_d = [p.name for p in dec.patches].index(name)
        a = np.sort(rec.pos[rec.n_cells:][rec.patch == i_r], axis=0)
        b = np.sort(dec.pos[dec.n_cells:][dec.patch == i_d], axis=0)
        assert np.allclose(a, b)
    assert dec.rank_proc_faces[0]['procBoundary0to1'][1].tolist() == [1]


def _field(path: Path, cls: str, internal: str, patches: dict) -> None:
    body = f'dimensions [0 0 0 0 0 0 0];\n\ninternalField {internal};\n\nboundaryField\n{{\n'
    for name, entry in patches.items():
        body += f'    {name}\n    {{\n{entry}\n    }}\n'
    _write(path, cls, body + '}\n')


def test_read_field_list_forms(tmp_path):
    long_list = 'nonuniform List<vector>\n12\n(\n' + '\n'.join(f'({i} 0 0)' for i in range(12)) + '\n)\n'
    _field(tmp_path / 'U', 'volVectorField', 'uniform (0 0 0)', {
        'a': '        type fixedValue;  // original: noSlip\n        value nonuniform List<vector> 2((1 2 3) (4 5 6));',
        'b': '        type fixedValue;\n        value ' + long_list + ';',
        'c': '        type fixedValue;\n        value uniform (7 8 9);',
        'd': '        type fixedValue;\n        value nonuniform List<vector>\n0\n(\n)\n;',
        'e': '        type cyclic;',
        'f': '        type uniformFixedValue;\n        uniformValue { type constant; value (9 9 9); }\n'
             '        value uniform (1 1 1);',
    })
    internal, out = read_field(tmp_path / 'U', 3, {'a': 2, 'b': 12, 'c': 3, 'd': 0, 'e': 1, 'f': 1})
    assert internal.shape == (1, 3)
    assert out['a'].tolist() == [[1, 2, 3], [4, 5, 6]]
    assert out['b'][:, 0].tolist() == list(range(12))
    assert out['c'].tolist() == [[7, 8, 9]] * 3
    assert out['d'].shape == (0, 3)
    assert out['e'] is None
    assert out['f'].tolist() == [[1, 1, 1]]                      # the top-level value, not uniformValue's


def _t1_fields(case: Path, graph) -> None:
    """Time-1 files of the reconstructed case: U on inlet/outlet, T everywhere, p_rgh on the outlet."""
    t = case / '1'
    _field(t / 'U', 'volVectorField', 'nonuniform List<vector> 2((0.1 0 0) (0.2 0 0))', {
        'inlet': '        type fixedValue;\n        value uniform (1 0 0);',
        'outlet': '        type fixedValue;\n        value uniform (2 0 0);',
        'walls': '        type fixedValue;\n        value uniform (0 0 0);'})
    _field(t / 'p_rgh', 'volScalarField', 'uniform 0', {
        'inlet': '        type fixedValue;\n        value uniform 0;',
        'outlet': '        type fixedValue;\n        value uniform -0.1;',
        'walls': '        type fixedValue;\n        value uniform 0;'})
    _field(t / 'T', 'volScalarField', 'nonuniform List<scalar> 2(295.15 295.15)', {
        'inlet': '        type fixedValue;\n        value uniform 295.15;',
        'outlet': '        type fixedValue;\n        value uniform 300.15;',
        'walls': '        type fixedValue;\n        value uniform 295.15;'})


def test_boundary_values_and_dataset_form(tmp_path):
    case = reconstructed_case(tmp_path)
    g = read_graph(case)
    _t1_fields(case, g)
    bc = boundary_values(g, '1')
    assert bc.shape == (10, 5)
    assert bc[0].tolist() == [1, 0, 0, 0, 295.15] and bc[1].tolist() == [2, 0, 0, -0.1, 300.15]
    # the dataset's outlet temperature: the paired inlet's T + P / (rho cp Q)
    g.patches[0].name, g.patches[1].name = 'srv_inlet_patch', 'srv_outlet_p0'
    bindings = {'srv_inlet_patch': {'bcType': 'fan_inlet', 'parameters': {'flowRateM3s': 0.1}},
                'srv_outlet_p0': {'bcType': 'fan_outlet', 'parameters': {'flowRateM3s': 0.1, 'powerW': 1196.0}}}
    roles = dcts_cache.patch_roles(g, bindings)
    assert roles == ['server_inlet', 'server_outlet', 'wall']
    ds, notes = dcts_cache.to_dataset_form(g, bc, roles, bindings)
    dT = 1196.0 / (1.19 * 1005.0 * 0.1)
    assert ds[1, 4] == pytest.approx(295.15 + dT) and notes['server_outlets'] == 1
    # the fast path: doubled load, halved airflow
    old = dcts_cache.binding_values(bindings)
    bindings['srv_outlet_p0']['parameters'].update(powerW=2392.0, flowRateM3s=0.05)
    bindings['srv_inlet_patch']['parameters'].update(flowRateM3s=0.05)
    new = dcts_cache.binding_values(bindings)
    out, changed = dcts_cache.boundary_from_bindings(g, ds, old, new)
    assert sorted(changed) == ['srv_inlet_patch', 'srv_outlet_p0']
    assert out[1, 4] == pytest.approx(295.15 + 4 * dT)               # P x2, Q / 2: rise x4
    assert out[1, 3] == pytest.approx(-0.4)                           # p_rgh follows the rise
    assert out[1, :3] == pytest.approx([-0.05, 0, 0])                 # -(Q/A) n: into the room, n = +x, A = 1
    assert out[0, :3] == pytest.approx([0.5, 0, 0])                   # the inlet profile scaled
    assert (out[2:] == ds[2:]).all()


def test_physics_key_ignores_tunable_values():
    run = {'boundaryBindings': {'a': {'patchName': 'a', 'bcType': 'fan_outlet',
                                      'parameters': {'powerW': 1.0, 'flowRateM3s': 0.1}}},
           'simulationConfig': {'roomTemperatureC': 22}}
    k = pipeline.physics_key(run, 'input-a')
    run['boundaryBindings']['a']['parameters']['powerW'] = 5.0
    assert pipeline.physics_key(run, 'input-a') == k
    assert pipeline.physics_key(run, 'input-b') != k                  # another model input: another work dir
    run['simulationConfig']['roomTemperatureC'] = 25
    assert pipeline.physics_key(run, 'input-a') != k


def test_writer_round_trip(tmp_path):
    case = decomposed_case(tmp_path / 'd')
    (case / 'system').mkdir()
    (case / 'system' / 'controlDict').write_text('application buoyantBoussinesqSimpleFoam;\n')
    g = read_graph(case, 'processors')
    pred = np.arange(g.pos.shape[0] * 5, dtype=float).reshape(-1, 5)
    out = write_prediction(g, pred, case, tmp_path / 'run', {'model': 'm'})
    raw = (out / 'processor1' / '1' / 'T').read_bytes()
    i = raw.index(b'internalField')
    j = raw.index(b'(', i) + 1
    assert struct.unpack('<d', raw[j:j + 8])[0] == pred[1, 4]          # rank 1's one cell is global cell 1
    assert b'procBoundary1to0' in raw and b'type            processor;' in raw
    assert (out / 'processor0' / 'constant' / 'polyMesh' / 'faces').is_file()
    assert json.loads((out / 'scano_prediction.json').read_text())['model'] == 'm'


DCTS_RECIPE = Path(__file__).resolve().parents[2] / 'backend' / 'aircooling' / 'scano' / 'checkpoints' \
    / 'dcts_normalizer' / 'first_iteration.json'


def test_first_iteration_dict_patch(tmp_path):
    """The DCTS recipe applied by the runner (the runner itself knows no dataset)."""
    from backend.aircooling.runner.stlgen_runner import _patch_first_iteration_dicts, load_first_iteration_recipe
    sysd = tmp_path / 'system'
    sysd.mkdir()
    (sysd / 'controlDict').write_text('startFrom latestTime;\nstartTime 0;\nendTime 1000;\nwriteControl timeStep;\n'
                                      'writeInterval 1000;\nwriteFormat ascii;\n'
                                      'explicitBoundaryValues\n{\n    mode preserveType;\n}\n')
    (sysd / 'fvSolution').write_text('solvers\n{\n    p_rgh { solver GAMG; tolerance 1e-07; relTol 0.01; }\n}\n'
                                     'SIMPLE\n{\n    nNonOrthogonalCorrectors 2;\n    consistent yes;\n}\n')
    _patch_first_iteration_dicts(tmp_path, load_first_iteration_recipe(DCTS_RECIPE))
    cd = (sysd / 'controlDict').read_text()
    for entry in ('startFrom startTime;', 'endTime 1;', 'writeInterval 1;', 'writeFormat binary;', 'mode fixedValue;'):
        assert entry in cd
    assert 'preserveType' not in cd
    fv = (sysd / 'fvSolution').read_text()
    for entry in ('tolerance 10;', 'relTol 0;', 'nNonOrthogonalCorrectors 0;', 'consistent no;'):
        assert entry in fv


def test_compatibility_warnings(tmp_path):
    g = read_graph(reconstructed_case(tmp_path))
    ranges = {'server_power_W': {'min': 400, 'max': 2200}, 'nodes_per_room': {'min': 1, 'max': 100}}
    run = {'boundaryBindings': {'s_outlet_p0': {'patchName': 's_outlet_p0', 'bcType': 'fan_outlet',
                                                'parameters': {'powerW': 5000.0, 'flowRateM3s': 0.1}}}}
    w = pipeline.compatibility(g, run, ranges)
    assert len(w) == 1 and w[0].startswith('server power 5000-5000 W')


def test_cell_average_operator_matches_vtk():
    """replace_fields' point data = vtkCellDataToPointData of the same cell values (the load-time path)."""
    pv = pytest.importorskip('pyvista')
    from backend.aircooling.runner.postprocess import CachedPostProcessor
    grid = pv.ImageData(dimensions=(4, 3, 3)).cast_to_unstructured_grid()
    surf = grid.extract_surface()
    rng = np.random.default_rng(0)
    for mesh in (grid, surf):
        values = rng.standard_normal(mesh.n_cells)
        mesh.cell_data['T'] = values
        ref = np.asarray(mesh.cell_data_to_point_data().point_data['T'])
        got = CachedPostProcessor._cell_average_operator(mesh) @ values
        np.testing.assert_allclose(got, ref, rtol=1e-6, atol=1e-9)


def test_first_iteration_recipe_checks(tmp_path):
    from backend.aircooling.runner.stlgen_runner import _patch_first_iteration_dicts, load_first_iteration_recipe
    base = {'format': 'dcdtuni.first-iteration/v1', 'controlDict': {'endTime': '3'}, 'time': '3', 'processes': 4}
    for bad in ({'format': 'x'}, {'time': '1'}, {'processes': 1}):
        (tmp_path / 'r.json').write_text(json.dumps({**base, **bad}))
        with pytest.raises(ValueError):
            load_first_iteration_recipe(tmp_path / 'r.json')
    # a recipe without explicitBoundaryValues leaves the controlDict's mode alone; SIMPLE entries are added
    (tmp_path / 'system').mkdir()
    (tmp_path / 'system' / 'controlDict').write_text('endTime 1000;\nwriteFormat ascii;\n'
                                                      'explicitBoundaryValues\n{\n    mode preserveType;\n}\n')
    (tmp_path / 'system' / 'fvSolution').write_text('SIMPLE\n{\n    nNonOrthogonalCorrectors 2;\n}\n')
    _patch_first_iteration_dicts(tmp_path, {**base, 'SIMPLE': {'momentumPredictor': 'no'}})
    assert 'endTime 3;' in (tmp_path / 'system' / 'controlDict').read_text()
    assert 'mode preserveType;' in (tmp_path / 'system' / 'controlDict').read_text()
    assert 'momentumPredictor no;' in (tmp_path / 'system' / 'fvSolution').read_text()


def test_model_cards_drive_the_pipeline(tmp_path):
    """A checkpoint is a directory with a card: references resolve relative to it, the input key follows the
    adapter and the recipe (not the checkpoint), an unknown adapter or a missing recipe is refused."""
    from backend.aircooling.scano import adapters, models
    root = tmp_path / 'checkpoints'
    (root / 'data').mkdir(parents=True)
    (root / 'data' / 'recipe.json').write_text(json.dumps({'format': 'dcdtuni.first-iteration/v1', 'time': '1'}))
    (root / 'data' / 'ranges.json').write_text(json.dumps({'server_power_W': {'min': 1, 'max': 2}}))
    for mid, card in (('a', {'adapter': 'scano-dcts-v1', 'first_iteration': '../data/recipe.json',
                             'training_ranges': '../data/ranges.json', 'recommended': True}),
                      ('b', {'adapter': 'scano-dcts-v1', 'first_iteration': '../data/recipe.json'}),
                      ('c', {'adapter': 'no-such-format', 'first_iteration': {'format': 'x', 'time': '2'}}),
                      ('d', {'adapter': 'scano-dcts-v1'})):
        (root / mid).mkdir()
        (root / mid / 'checkpoint.pt').write_bytes(b'')
        (root / mid / 'model_card.json').write_text(json.dumps(card))
    a, b, c = (models.card(m, root) for m in 'abc')
    assert a['first_iteration']['time'] == '1' and a['training_ranges']['server_power_W']['max'] == 2
    assert models.input_key(a) == models.input_key(b) != models.input_key(c)
    assert adapters.for_card(a).id == 'scano-dcts-v1'
    with pytest.raises(ValueError, match='no-such-format'):
        adapters.for_card(c)
    with pytest.raises(ValueError, match='first_iteration'):
        models.card('d', root)
    listed = {m['id']: m for m in models.list_models(root)}
    assert 'error' in listed['d'] and 'run_dir' not in listed['a']
    assert models.recommended(root) == 'a'


def _zero_field(path: Path, cls: str, internal: bytes, fmt: str = 'ascii', tail: bytes = b'') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (f'FoamFile\n{{\n    version 2.0;\n    format {fmt};\n    arch "LSB;label=32;scalar=64";\n'
            f'    class {cls};\n    object {path.name};\n}}\n').encode()
    path.write_bytes(head + b'dimensions [0 0 0 0 0 0 0];\n\n' + internal + b'\n\nboundaryField\n{\n' + tail + b'}\n')


def test_initial_fields_serial_and_partitions(tmp_path):
    """run_cfd --initial-fields: serial 0/ gets the arrays; partitions their cells by cellProcAddressing, ASCII or
    binary, with what follows the internalField (processor patch values) unchanged."""
    from backend.aircooling.runner.initial_fields import apply_initial_fields
    U = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    npz = tmp_path / 'init.npz'
    np.savez(npz, U=U, p_rgh=np.array([-1.0, -2.0]), T=np.array([300.0, 310.0]))
    serial = reconstructed_case(tmp_path / 's')
    for f, cls, v in (('U', 'volVectorField', b'uniform (0 0 0);'), ('p_rgh', 'volScalarField', b'uniform 0;'),
                      ('T', 'volScalarField', b'uniform 295.15;')):
        _zero_field(serial / '0' / f, cls, b'internalField   ' + v)
    assert apply_initial_fields(serial, npz) == {'ranks': 1, 'cells': 2, 'fields': ['U', 'p_rgh', 'T']}
    assert read_field(serial / '0' / 'T', 1, {})[0].ravel().tolist() == [300.0, 310.0]
    assert read_field(serial / '0' / 'U', 3, {})[0].tolist() == U.tolist()

    part = decomposed_case(tmp_path / 'p')
    tail = b'    procBoundary0to1 { type processor; value nonuniform List<scalar> 1(\x00\x00\x00\x00\x00\x00\xf0?); }\n'
    for rank, cell in ((0, 1), (1, 0)):                      # swapped: rank 0 holds serial cell 1
        poly = part / f'processor{rank}' / 'constant' / 'polyMesh'
        _write(poly / 'cellProcAddressing', 'labelList', _labels([cell]))
        zero = part / f'processor{rank}' / '0'
        _zero_field(zero / 'U', 'volVectorField', b'internalField   uniform (0 0 0);')
        _zero_field(zero / 'p_rgh', 'volScalarField', b'internalField   uniform 0;', 'binary', tail)
        _zero_field(zero / 'T', 'volScalarField', b'internalField   nonuniform List<scalar> 1(295.15);')
    assert apply_initial_fields(part, npz)['ranks'] == 2
    assert read_field(part / 'processor0' / '0' / 'T', 1, {})[0].ravel().tolist() == [310.0]
    assert read_field(part / 'processor1' / '0' / 'U', 3, {})[0].tolist() == [U[0].tolist()]
    raw = (part / 'processor0' / '0' / 'p_rgh').read_bytes()
    assert struct.pack('<d', -2.0) in raw and raw.endswith(tail + b'}\n')
    np.savez(npz, U=U[:1], p_rgh=np.array([-1.0]), T=np.array([300.0]))
    with pytest.raises(ValueError, match='beyond'):
        apply_initial_fields(part, npz)


def test_roi_result_is_local(tmp_path):
    """An ROI prediction keeps values only inside its box: written NaN outside, post-processing loads only the
    region (and maps a real-time replacement onto it), and it cannot initialise a CFD solve."""
    from backend.aircooling.scano import pipeline
    from backend.aircooling.runner.postprocess import CachedPostProcessor
    case = decomposed_case(tmp_path / 'd')
    (case / 'system').mkdir()
    (case / 'system' / 'controlDict').write_text('application buoyantBoussinesqSimpleFoam;\n')
    g = read_graph(case, 'processors')
    pred = np.arange(g.pos.shape[0] * 5, dtype=float).reshape(-1, 5) + 290.0
    cx = g.pos[:g.n_cells, 0]
    box = {'min': [cx.min() - 1e-6, -1e9, -1e9], 'max': [cx.min() + 1e-6, 1e9, 1e9]}     # the first cell only
    out_pred, roi = pipeline.restrict_to_roi(g, pred, box)
    assert roi['cells'] == 1 and np.isnan(out_pred[:g.n_cells]).sum() == (g.n_cells - 1) * 5
    with pytest.raises(ValueError):
        pipeline.restrict_to_roi(g, pred, {'min': [1e8] * 3, 'max': [1e9] * 3})
    with pytest.raises(ValueError):
        pipeline.parse_roi({'min': [0, 0, 0], 'max': [1, 0, 1]})
    with pytest.raises(ValueError):
        write_prediction(g, out_pred, case, tmp_path / 'bad', {'model': 'm'})       # NaN without an roi record
    run = write_prediction(g, out_pred, case, tmp_path / 'run', {'model': 'm', 'roi': roi})

    base = tmp_path / 'base'
    base.mkdir()
    import pickle
    (base / 'graph.pkl').write_bytes(pickle.dumps(g))
    np.save(base / 'p.npy', out_pred)
    with pytest.raises(ValueError, match='ROI'):
        pipeline.initial_fields(base, base / 'p.npy', base / 'init.npz')

    cpp = CachedPostProcessor()
    info = cpp.init(run, force=True)
    assert info['success'] and info['roi']['cells'] == 1 and cpp._internal.n_cells == 1
    keep = int(np.flatnonzero(np.isfinite(out_pred[:g.n_cells, 4]))[0])
    assert float(cpp._internal.cell_data['T'][0]) == pytest.approx(pred[keep, 4])
    assert np.isfinite(np.asarray(cpp._internal.point_data['T'])).all()
    full = pred.copy()
    full[:, 4] += 1.0
    cpp.replace_fields({'U': full[:g.n_cells, :3], 'p_rgh': full[:g.n_cells, 3], 'T': full[:g.n_cells, 4]}, {})
    assert float(cpp._internal.cell_data['T'][0]) == pytest.approx(pred[keep, 4] + 1.0)
