"""Read real OpenFOAM mesh patch areas before applying hybrid heat flux."""
from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace

import pytest


def _write_mesh(root: Path, label: str) -> None:
    root.mkdir(parents=True)
    for name in ('points', 'faces', 'owner', 'neighbour', 'boundary'):
        (root / name).write_text(f'{label}:{name}')
    (root / 'owner').write_text(f'FoamFile {{ note "nCells:1"; object owner; }}\n// {label}\n')


def _partitioned_case(case: Path) -> None:
    for rank in (0, 1):
        _write_mesh(case / f'processor{rank}/constant/polyMesh', f'rank{rank}')
    system = case / 'system'
    system.mkdir()
    (system / 'decomposeParDict').write_text('numberOfSubdomains 2;\n')


def _write_area_output(case: Path, control_dict: Path, *, faces: int, area: str) -> None:
    function = re.search(r'\b(hybridArea[0-9a-f]{12}_001)\b', control_dict.read_text())
    assert function is not None
    output = case / 'postProcessing' / function.group(1) / '0' / 'surfaceFieldValue.dat'
    output.parent.mkdir(parents=True)
    output.write_text(
        '# Region type : patch floor\n'
        f'# Faces       : {faces}\n'
        f'# Area        : {area}\n'
        '# Scale factor: 1.000000e+00\n'
        '# Time        \tArea\n'
        f'0             \t{area}\n'
    )


def test_surface_field_value_area_parser_checks_patch_faces_and_precision(tmp_path):
    from backend.aircooling.runner.openfoam_patch_area import parse_surface_field_value_area

    output = tmp_path / 'surfaceFieldValue.dat'
    output.write_text(
        '# Region type : patch Rack_A_01_Rack_A_01_server_1_front\n'
        '# Faces       : 48\n'
        '# Area        : 8.543267e-02\n'
        '# Scale factor: 1.000000e+00\n'
        '# Time        \tArea\n'
        '0             \t8.543267e-02\n'
    )
    result = parse_surface_field_value_area(output, 'Rack_A_01_Rack_A_01_server_1_front')
    assert result == {'area_m2': pytest.approx(.08543267), 'face_count': 48}

    with pytest.raises(ValueError, match='patch|Region'):
        parse_surface_field_value_area(output, 'different_patch')
    output.write_text(output.read_text().replace('# Faces       : 48', '# Faces       : 0'))
    with pytest.raises(ValueError, match='Faces|positive'):
        parse_surface_field_value_area(output, 'Rack_A_01_Rack_A_01_server_1_front')
    output.write_text(output.read_text().replace('# Faces       : 0', '# Faces       : 48').replace('8.543267e-02\n', '1.000000e-01\n', 1))
    with pytest.raises(ValueError, match='Area|disagree'):
        parse_surface_field_value_area(output, 'Rack_A_01_Rack_A_01_server_1_front')


def test_checkmesh_text_gate_rejects_failed_checks_even_when_process_exit_is_zero():
    from backend.aircooling.runner.openfoam_patch_area import checkmesh_quality_status

    assert checkmesh_quality_status('Checking geometry...\nMesh OK.\nEnd\n') == 'ok'
    with pytest.raises(ValueError, match='4 mesh checks'):
        checkmesh_quality_status('Checking geometry...\nFailed 4 mesh checks.\nEnd\n')
    with pytest.raises(ValueError, match='unrecognized|missing'):
        checkmesh_quality_status('Checking geometry...\nEnd\n')


def test_mesh_identity_includes_cell_topology_not_only_patch_geometry(tmp_path):
    from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256

    mesh = tmp_path / 'constant/polyMesh'
    mesh.mkdir(parents=True)
    for name in ('points', 'faces', 'owner', 'neighbour', 'boundary'):
        (mesh / name).write_text('initial')
    before = mesh_identity_sha256(tmp_path)
    (mesh / 'owner').write_text('changed owner topology')
    assert mesh_identity_sha256(tmp_path) != before
    after_owner = mesh_identity_sha256(tmp_path)
    (mesh / 'neighbour').write_text('changed neighbour topology')
    assert mesh_identity_sha256(tmp_path) != after_owner


def test_mesh_identity_rejects_partition_gaps_or_incomplete_ranks(tmp_path):
    from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256

    for rank in (0, 2):
        poly = tmp_path / f'processor{rank}/constant/polyMesh'
        poly.mkdir(parents=True)
        for name in ('points', 'faces', 'owner', 'neighbour', 'boundary'):
            (poly / name).write_bytes(f'rank{rank}:{name}'.encode())
    with pytest.raises(ValueError, match='contiguous|missing.*processor1'):
        mesh_identity_sha256(tmp_path)

    (tmp_path / 'processor2').rename(tmp_path / 'processor1')
    (tmp_path / 'processor1/constant/polyMesh/owner').unlink()
    with pytest.raises(ValueError, match='missing.*owner'):
        mesh_identity_sha256(tmp_path)


def test_mesh_identity_rejects_aliased_partition_number(tmp_path):
    from backend.aircooling.runner.openfoam_patch_area import mesh_identity_sha256

    for directory in ('processor0', 'processor00'):
        poly = tmp_path / directory / 'constant/polyMesh'
        poly.mkdir(parents=True)
        for name in ('points', 'faces', 'owner', 'neighbour', 'boundary'):
            (poly / name).write_bytes(f'{directory}:{name}'.encode())
    with pytest.raises(ValueError, match='noncanonical|ambiguous'):
        mesh_identity_sha256(tmp_path)


def test_partitioned_quality_checks_all_ranks_and_uses_global_verdict(tmp_path, monkeypatch):
    from backend.aircooling.runner.openfoam_patch_area import verify_openfoam_mesh_quality

    _partitioned_case(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='cells: 500\nMesh OK.\n', stderr='')

    monkeypatch.setattr('backend.aircooling.runner.openfoam_patch_area.subprocess.run', run)
    result = verify_openfoam_mesh_quality(tmp_path, checkmesh_binary='checkMesh')

    assert result['status'] == 'ok'
    assert calls == [[
        'mpirun', '-np', '2', 'checkMesh', '-case', str(tmp_path), '-parallel',
        '-constant', '-allTopology', '-allGeometry',
    ]]


def test_partitioned_quality_rejects_failed_checks_even_if_mpi_returns_zero(tmp_path, monkeypatch):
    from backend.aircooling.runner.openfoam_patch_area import verify_openfoam_mesh_quality

    _partitioned_case(tmp_path)

    def run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout='Failed 3 mesh checks.\n', stderr='')

    monkeypatch.setattr('backend.aircooling.runner.openfoam_patch_area.subprocess.run', run)
    with pytest.raises(ValueError, match='3 mesh checks'):
        verify_openfoam_mesh_quality(tmp_path)


def test_partitioned_area_reads_one_global_root_output(tmp_path, monkeypatch):
    from backend.aircooling.runner.openfoam_patch_area import measure_openfoam_patch_areas

    _partitioned_case(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        _write_area_output(tmp_path, Path(command[command.index('-dict') + 1]),
                           faces=100, area='1.000000e+00')
        return SimpleNamespace(returncode=0, stdout='total faces = 100\ntotal area = 1\n', stderr='')

    monkeypatch.setattr('backend.aircooling.runner.openfoam_patch_area.subprocess.run', run)
    result = measure_openfoam_patch_areas(tmp_path, {'floor'})

    assert result['patches']['floor'] == {'area_m2': 1.0, 'face_count': 100}
    assert calls[0][:7] == [
        'mpirun', '-np', '2', 'postProcess', '-case', str(tmp_path), '-parallel',
    ]


def test_root_mesh_remains_authoritative_with_stale_processor_dirs(tmp_path, monkeypatch):
    from backend.aircooling.runner.openfoam_patch_area import (
        measure_openfoam_patch_areas, verify_openfoam_mesh_quality,
    )

    _partitioned_case(tmp_path)
    _write_mesh(tmp_path / 'constant/polyMesh', 'serial')
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if '-dict' in command:
            _write_area_output(tmp_path, Path(command[command.index('-dict') + 1]),
                               faces=100, area='1.000000e+00')
        return SimpleNamespace(returncode=0, stdout='Mesh OK.\n', stderr='')

    monkeypatch.setattr('backend.aircooling.runner.openfoam_patch_area.subprocess.run', run)
    verify_openfoam_mesh_quality(tmp_path)
    measure_openfoam_patch_areas(tmp_path, {'floor'})

    assert calls[0][:3] == ['checkMesh', '-case', str(tmp_path)]
    assert calls[1][:3] == ['postProcess', '-case', str(tmp_path)]
    assert all('-parallel' not in command for command in calls)


def test_checkmesh_quality_status_reports_concave_cells_without_refusing():
    from backend.aircooling.runner.openfoam_patch_area import checkmesh_quality_status
    concave = (' ***Concave cells (using face planes) found, number of cells: 83293\n'
               'Failed 1 mesh checks.\nEnd\n')
    assert checkmesh_quality_status(concave) == 'ok'
    with pytest.raises(ValueError, match='failed 1 mesh checks'):
        checkmesh_quality_status(' ***Error in face tets: 4 faces\n' + concave.replace('Failed 1', 'Failed 2'))
