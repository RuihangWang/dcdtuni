"""An accepted liquid trajectory becomes traceable air heat on actual STL faces."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.cfdrun_schema import CfdrunV2
from backend.aircooling.runner.cfdrun_reader import resolve_run
from backend.tests.test_cfdrun_reader import _minimal_manifest, _write_ascii_stl_rect
from backend.tests.test_liquid.test_hybrid_heat_audit import coupled_case
from backend.liquidcooling.dae_runtime import DAESimulation
from backend.liquidcooling.liquidrun_dae_schema import LiquidrunDAEV3
from backend.liquidcooling.runtime import _canonical_hash


def _project(total_it_W=900.0, front_fraction=.25):
    return {
        'schemaVersion': 4, 'uid': 'project-stable-uid', 'name': 'hybrid-test',
        'constructions': {'racks': {'rack-uid': {
            'uid': 'rack-uid', 'constructions': {'servers': {'server-uid': {
                'uid': 'server-uid', 'type': 'liquidServer',
            }}},
        }}},
        'runDefaults': {'hybridHeatBudget': {
            'version': 1, 'source': 'mock', 'source_reference': 'synthetic-test-v1',
            'load_mapping': 'shared_heat_multiplier',
            'air_surface_allocation': {'front_fraction': front_fraction,
                                       'rear_fraction': 1 - front_fraction},
            'servers': {'rack-uid/server-uid': {'total_it_power_W': total_it_W}},
        }},
    }


def _v5(project):
    # A saved cfdproj v5: the same static project plus a transient block. This
    # synthetic project owns no track-registered field, so it has no tracks.
    return {**copy.deepcopy(project), 'schemaVersion': 5, 'transient': {
        'mode': 'steady', 'durationS': 600, 'gridS': 1,
        'start': {'kind': 'uniform', 'uniform': {'airT_C': 22, 'waterT_C': 30, 'solidT_C': 40}},
        'tracks': [],
    }}


def _air(project):
    from backend.aircooling.runner.hybrid_air_heat_handoff import cfdproj_content_hash

    raw = _minimal_manifest()
    raw['sourceProject'].update(uid=project['uid'], projectName=project['name'],
                                schemaVersion=project['schemaVersion'],
                                contentHash=cfdproj_content_hash(project))
    path = 'constructions.racks.rack-uid.servers.server-uid'
    for role, name in [('front', 'server-uid_front'),
                       ('rear', 'server-uid_outlet_p0')]:
        raw['geometryExport']['solids'][f'sol_{role}'] = {
            'solidName': name, 'patchName': name, 'stlFileId': None,
            'objectPath': path, 'surfaceUid': role,
            'role': 'nonfan_fixed_temp', 'areaM2': None,
        }
        raw['boundaryBindings'][f'bb_{role}'] = {
            'solidName': name, 'patchName': name, 'objectPath': path,
            'surfaceUid': role, 'role': 'nonfan_fixed_temp',
            'bcType': 'nonfan_fixed_temp', 'parameters': {'temperatureC': 35},
        }
    return raw


def _stls(tmp_path):
    tri = tmp_path / 'triSurface'
    _write_ascii_stl_rect(tri / 'front.stl', 'server-uid_front', .4, .2)
    _write_ascii_stl_rect(tri / 'rear.stl', 'server-uid_outlet_p0', .2, .2)
    return tri


def _completed_run(project):
    original = coupled_case()
    from backend.aircooling.runner.hybrid_air_heat_handoff import cfdproj_content_hash
    original['source_project']['sha256'] = cfdproj_content_hash(project)
    original['source_project']['schema_version'] = project['schemaVersion']
    simulation = DAESimulation(original)
    for multiplier in (1.2, .8, 1.0):
        simulation.set_heat_multiplier(multiplier)
        simulation.step()
    executed, result = simulation.executed_manifest(), simulation.result()
    assert result['completed'] is True
    canonical = LiquidrunDAEV3.model_validate(original).model_dump(
        by_alias=True, mode='json', exclude_none=True)
    receipt = {
        'format': 'liquid-accepted-inputs/v1', 'run_id': result['run_id'],
        'original_config_hash': _canonical_hash(canonical, []),
        'executed_config_hash': _canonical_hash(executed, []),
        'accepted_sample_count': executed['time']['N_t'],
        'original_sample_count': original['time']['N_t'],
        'accepted_time_s': result['time_s'][-1],
        'complete_original_horizon': True, 'input_file': 'executed.liquidrun',
    }
    return original, executed, result, receipt


@pytest.fixture(scope='module')
def full_liquid_run():
    return _completed_run(_project())


def _compile(project, air, tri, run, sample_index=1, *, bind_stls=True):
    from backend.aircooling.runner.hybrid_air_heat_handoff import compile_hybrid_air_heat

    air = copy.deepcopy(air)
    if bind_stls:
        air['geometryExport']['stlFiles'] = {
            f'stl_{index:03d}': {'path': path.name, 'objectPath': None,
                                 'kind': 'grouped_ascii',
                                 'contentHash': hashlib.sha256(path.read_bytes()).hexdigest()}
            for index, path in enumerate(sorted(tri.glob('*.stl')))
        }
    original, executed, result, receipt = run
    return compile_hybrid_air_heat(
        project, air, tri, original_liquidrun=original,
        executed_liquidrun=executed, liquid_result=result,
        accepted_provenance=receipt, sample_index=sample_index,
    )


def test_python_project_hash_matches_react_exporter_for_saved_shinaide_case():
    from backend.aircooling.runner.hybrid_air_heat_handoff import cfdproj_content_hash

    source = Path(__file__).parent / 'fixtures/shinaide_demo2_original_project.json'
    raw = json.loads(source.read_text())
    # Pinned to fixture_revision shinaide-demo-2 (stage4 demo polish, 2026-09-28:
    # dt 10 s + loadStep defaults + meta.provenance block). The value is the
    # React exporter's project_content_sha256 for the saved case; this assert
    # proves the Python hasher agrees bit-for-bit. Receipt:
    # docs/benchmarks/shinaide-demo-provenance-2026-09-28/receipt.json
    assert cfdproj_content_hash(raw) == '87114bef05225e05c6971f8a435fd361ff7377182a45110060582bf887409873'


def test_python_project_hash_matches_actual_react_exporter_for_current_newton_entry():
    from backend.aircooling.runner.hybrid_air_heat_handoff import cfdproj_content_hash

    source = Path(__file__).resolve().parents[3] / 'case/verification/shinaide-legacy-projects/Shinaide_dae_cpu.json'
    # Independently produced by the actual shared React exporter after the
    # canonical 0.25 m presets and default Newton entry revisions. The earlier
    # demo-2 contract above uses its immutable original project fixture.
    assert hashlib.sha256(source.read_bytes()).hexdigest() == '29e208ba34f937ceaa0136458c859b1312d3328f9d156e720a2640a684715a3b'
    assert cfdproj_content_hash(json.loads(source.read_text())) == 'b3684cfa8078b7f6fe2df43ee2525cc35e3870c11292417383003419a84ea144'


def test_completed_liquid_run_becomes_conserved_front_rear_flux(full_liquid_run, tmp_path):
    project = _project()
    air = _air(project)
    before = copy.deepcopy((project, air))
    tri = _stls(tmp_path)
    compiled, ledger = _compile(project, air, tri, full_liquid_run)
    assert (project, air) == before
    CfdrunV2.model_validate(compiled)
    assert ledger['format'] == 'hybrid-air-heat-handoff/v1'
    assert ledger['project_source'] == 'mock'
    assert ledger['liquid_run_id'] == full_liquid_run[2]['run_id']
    assert ledger['sample_index'] == 1 and ledger['time_s'] == 1.0
    assert ledger['heat_multiplier'] == 1.2
    assert ledger['heat_flux_sign'] == 'positive_q_into_air_OpenFOAM_v2412_verified_small_box'
    row = ledger['servers'][0]
    assert row['total_it_W'] == pytest.approx(1080.)
    assert row['liquid_removed_W'] + row['solid_storage_rate_W'] + row['air_residual_W'] == pytest.approx(1080., abs=1e-6)
    assert row['air_residual_W'] == pytest.approx(480., abs=1e-5)
    patches = {p['face']: p for p in row['faces']}
    assert patches['front']['area_m2'] == pytest.approx(.08)
    assert patches['rear']['area_m2'] == pytest.approx(.04)
    assert patches['front']['heat_W'] == pytest.approx(120.)
    assert patches['rear']['heat_W'] == pytest.approx(360.)
    assert patches['front']['heat_flux_W_m2'] == pytest.approx(1500.)
    assert patches['rear']['heat_flux_W_m2'] == pytest.approx(9000.)
    assert compiled['boundaryBindings']['bb_front']['bcType'] == 'nonfan_heat_flux'
    assert compiled['boundaryBindings']['bb_rear']['parameters']['heatFluxWm2'] == pytest.approx(9000.)
    assert compiled['geometryExport']['solids']['sol_front']['role'] == 'nonfan_heat_flux'
    assert compiled['geometryExport']['solids']['sol_rear']['role'] == 'nonfan_heat_flux'
    assert all(v['areaM2'] is not None for v in compiled['geometryExport']['solids'].values())

    model_dir = tmp_path / 'model'
    model_dir.mkdir()
    (model_dir / 'derived.cfdrun').write_text(json.dumps(compiled))
    resolved = resolve_run(tmp_path, tri_surface_dir=tri)
    assert resolved.bridge_registry.get('server-uid_front').heat_flux == pytest.approx(1500.)
    assert resolved.bridge_registry.get('server-uid_outlet_p0').heat_flux == pytest.approx(9000.)


def test_cfdproj_v5_project_hands_off_like_v4(tmp_path):
    """A v5 save hashes as saved; the air/liquid lineage must name the same revision."""
    project = _v5(_project())
    run = _completed_run(project)
    tri = _stls(tmp_path)
    compiled, ledger = _compile(project, _air(project), tri, run)
    CfdrunV2.model_validate(compiled)
    assert ledger['servers'][0]['air_residual_W'] == pytest.approx(480., abs=1e-5)

    stale = _air(project)
    stale['sourceProject']['schemaVersion'] = 4
    with pytest.raises(ValueError, match='contentHash|revision'):
        _compile(project, stale, tri, run)

    future = {**project, 'schemaVersion': 6}
    with pytest.raises(ValueError, match='V4|V5|schemaVersion'):
        _compile(future, _air(future), tri, run)


def test_refuses_partial_run_mismatched_project_and_negative_air_budget(full_liquid_run, tmp_path):
    tri = _stls(tmp_path)
    project = _project()
    air = _air(project)
    original, executed, result, receipt = full_liquid_run
    partial = copy.deepcopy((original, executed, result, receipt))
    partial[2]['completed'] = False
    with pytest.raises(ValueError, match='complete|identity|handoff'):
        _compile(project, air, tri, partial)

    changed = copy.deepcopy(project)
    changed['runDefaults']['hybridHeatBudget']['source_reference'] = 'edited-after-air-export'
    with pytest.raises(ValueError, match='contentHash|revision'):
        _compile(changed, air, tri, full_liquid_run)

    too_small = _project(total_it_W=400.)
    with pytest.raises(ValueError, match='negative|smaller|insufficient'):
        _compile(too_small, _air(too_small), tri, _completed_run(too_small))


def test_rejects_unmapped_or_missing_thermal_faces(full_liquid_run, tmp_path):
    tri = _stls(tmp_path)
    project = _project()
    air = _air(project)
    air['boundaryBindings']['bb_rear']['solidName'] = 'unknown_solid'
    with pytest.raises(ValueError, match='solid|rear|STL'):
        _compile(project, air, tri, full_liquid_run)

    air = _air(project)
    air['boundaryBindings'].pop('bb_rear')
    with pytest.raises(ValueError, match='front|rear|thermal'):
        _compile(project, air, tri, full_liquid_run)


def test_liquid_input_must_be_from_exact_same_project_revision(full_liquid_run, tmp_path):
    tri = _stls(tmp_path)
    project = _project()
    air = _air(project)
    legacy = copy.deepcopy(full_liquid_run)
    legacy[0]['source_project'].pop('sha256')
    with pytest.raises(ValueError, match='liquid.*sha256|revision|contentHash'):
        _compile(project, air, tri, legacy)

    changed = _project(total_it_W=950.)
    with pytest.raises(ValueError, match='liquid.*sha256|revision|contentHash'):
        _compile(changed, _air(changed), tri, full_liquid_run)


def test_air_stls_need_export_time_expected_digests(full_liquid_run, tmp_path):
    tri = _stls(tmp_path)
    project = _project()
    with pytest.raises(ValueError, match='STL.*hash|STL.*identity|stlFiles'):
        _compile(project, _air(project), tri, full_liquid_run, bind_stls=False)


def test_rejects_preexisting_server_heat_flux_and_boolean_budget_version(full_liquid_run, tmp_path):
    tri = _stls(tmp_path)
    project = _project()
    air = _air(project)
    air['boundaryBindings']['bb_extra_heat'] = {
        'patchName': 'server-uid_extra', 'solidName': 'server-uid_extra',
        'objectPath': 'constructions.racks.rack-uid.servers.server-uid',
        'role': 'nonfan_heat_flux', 'bcType': 'nonfan_heat_flux',
        'parameters': {'heatFluxWm2': 100.},
    }
    with pytest.raises(ValueError, match='double|existing|heat'):
        _compile(project, air, tri, full_liquid_run)

    project = _project()
    project['runDefaults']['hybridHeatBudget']['version'] = True
    with pytest.raises(ValueError, match='version'):
        _compile(project, _air(project), tri, full_liquid_run)

    huge = _project(total_it_W=10 ** 1000)
    with pytest.raises(ValueError, match='finite|power|safe'):
        _compile(huge, _air(_project()), tri, full_liquid_run)


def test_post_mesh_retarget_uses_actual_patch_area_and_preserves_each_server_watts(full_liquid_run, tmp_path):
    from backend.aircooling.runner.hybrid_air_heat_handoff import retarget_hybrid_air_heat_to_mesh

    project = _project()
    compiled, ledger = _compile(project, _air(project), _stls(tmp_path), full_liquid_run)
    old_flux = compiled['boundaryBindings']['bb_rear']['parameters']['heatFluxWm2']
    mesh_areas = {'server-uid_front': .079, 'server-uid_outlet_p0': .035}
    adjusted, checked = retarget_hybrid_air_heat_to_mesh(
        compiled, ledger, mesh_areas, mesh_sha256='a' * 64)
    assert compiled['boundaryBindings']['bb_rear']['parameters']['heatFluxWm2'] == old_flux
    assert checked['mesh_area_verified'] is True
    assert checked['mesh_sha256'] == 'a' * 64
    faces = {p['face']: p for p in checked['servers'][0]['faces']}
    assert faces['rear']['area_m2'] == pytest.approx(.04)  # source STL identity remains
    assert faces['rear']['mesh_area_m2'] == pytest.approx(.035)
    assert faces['rear']['mesh_heat_flux_W_m2'] == pytest.approx(360. / .035)
    assert adjusted['boundaryBindings']['bb_rear']['parameters']['heatFluxWm2'] == pytest.approx(360. / .035)
    assert checked['totals']['mesh_face_heat_W'] == pytest.approx(checked['totals']['air_residual_W'])

    with pytest.raises(ValueError, match='missing|patch'):
        retarget_hybrid_air_heat_to_mesh(compiled, ledger, {'server-uid_front': .079}, mesh_sha256='a' * 64)
    with pytest.raises(ValueError, match='area|positive'):
        retarget_hybrid_air_heat_to_mesh(compiled, ledger,
                                         {'server-uid_front': .079, 'server-uid_outlet_p0': 0.},
                                         mesh_sha256='a' * 64)


def test_post_mesh_retarget_rechecks_original_energy_ledger_not_only_local_flux(full_liquid_run, tmp_path):
    from backend.aircooling.runner.hybrid_air_heat_handoff import retarget_hybrid_air_heat_to_mesh

    project = _project()
    compiled, ledger = _compile(project, _air(project), _stls(tmp_path), full_liquid_run)
    changed_manifest, changed_ledger = copy.deepcopy((compiled, ledger))
    changed_ledger['servers'][0]['faces'][1]['heat_W'] += 10.
    changed_ledger['servers'][0]['air_residual_W'] += 10.
    changed_ledger['totals']['air_residual_W'] += 10.
    changed_manifest['boundaryBindings']['bb_rear']['parameters']['heatFluxWm2'] += 10. / .04
    with pytest.raises(ValueError, match='energy|IT|ledger|face_heat'):
        retarget_hybrid_air_heat_to_mesh(
            changed_manifest, changed_ledger,
            {'server-uid_front': .08, 'server-uid_outlet_p0': .04},
            mesh_sha256='a' * 64)


def test_file_based_handoff_writes_one_isolated_air_case(full_liquid_run, tmp_path):
    from backend.aircooling.runner.hybrid_air_case_cli import write_hybrid_air_case

    tri = _stls(tmp_path)
    project = _project()
    air = _air(project)
    air['geometryExport']['stlFiles'] = {
        f'stl_{index:03d}': {'path': path.name, 'objectPath': None,
                             'kind': 'grouped_ascii',
                             'contentHash': hashlib.sha256(path.read_bytes()).hexdigest()}
        for index, path in enumerate(sorted(tri.glob('*.stl')))
    }
    project_path = tmp_path / 'model.cfdproj'
    air_path = tmp_path / 'source.cfdrun'
    run_dir = tmp_path / 'liquid-run'
    run_dir.mkdir()
    project_path.write_text(json.dumps(project))
    air_path.write_text(json.dumps(air))
    for name, value in zip(('input.liquidrun', 'executed.liquidrun', 'result.json', 'executed-inputs.json'),
                           full_liquid_run):
        (run_dir / name).write_text(json.dumps(value))
    output = tmp_path / 'derived-air'
    report = write_hybrid_air_case(project_path, air_path, tri, run_dir, 1, output)
    assert report['air_residual_W'] == pytest.approx(480.)
    assert (output / 'model/source.cfdrun').is_file()
    assert (output / 'heat-ledger.json').is_file()
    assert {p.name for p in (output / 'Mesh/constant/triSurface').glob('*.stl')} == {'front.stl', 'rear.stl'}
    with pytest.raises(ValueError, match='exists|output'):
        write_hybrid_air_case(project_path, air_path, tri, run_dir, 1, output)
