"""A v2 .cfdrun must not silently run a different mesh/solve budget.

The STLGen mesh path still consumes meshGeometry's compatibility config while
the public v2 manifest also carries root meshConfig/simulationConfig. A manual
or automated override to just one copy previously launched an unexpected MPI
rank count. Catch the disagreement before any OpenFOAM process starts.
"""
import copy
import json
from pathlib import Path

import pytest

from backend.aircooling.runner.stlgen_runner import STLGenRunner


FIXTURE = Path(__file__).resolve().parent / 'fixtures/cfdrun_v2_full/Base_leakage.cfdrun'
EXECUTION_FIELDS = (
    ('meshConfig', 'parallelMesh'),
    ('meshConfig', 'meshProcess'),
    ('meshConfig', 'maxGlobalCells'),
    ('simulationConfig', 'parallelSolve'),
    ('simulationConfig', 'solveProcess'),
    ('simulationConfig', 'endTime'),
    ('simulationConfig', 'writeInterval'),
    ('simulationConfig', 'startFrom'),
)


def make_runner(tmp_path, raw):
    case = tmp_path / 'case'
    model = case / 'model'
    model.mkdir(parents=True)
    manifest = model / 'Base_leakage.cfdrun'
    manifest.write_text(json.dumps(raw))
    return STLGenRunner(case, manifest)


def different(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return 'startTime' if value != 'startTime' else 'latestTime'
    raise AssertionError(f'Uncovered execution-control type: {type(value)}')


@pytest.mark.parametrize('loader_name', ('_load_cfd_json_only', '_load'))
@pytest.mark.parametrize('group,key', EXECUTION_FIELDS)
def test_root_only_execution_override_is_rejected_before_mesh_or_solver(tmp_path, loader_name, group, key):
    raw = json.loads(FIXTURE.read_text())
    assert key in raw[group] and key in raw['meshGeometry'][group]
    assert raw[group][key] == raw['meshGeometry'][group][key]
    raw[group][key] = different(raw[group][key])
    runner = make_runner(tmp_path, raw)
    with pytest.raises(ValueError, match=rf'{group}\.{key}.*meshGeometry\.{group}\.{key}'):
        getattr(runner,loader_name)()
    assert runner._data is None


def test_matching_execution_controls_keep_existing_v2_runner_behavior(tmp_path):
    raw = copy.deepcopy(json.loads(FIXTURE.read_text()))
    runner = make_runner(tmp_path, raw)
    runner._load_cfd_json_only()
    assert runner._data['meshConfig']['meshProcess'] == raw['meshConfig']['meshProcess']
    assert runner._data['simulationConfig']['solveProcess'] == raw['simulationConfig']['solveProcess']


def test_explicit_four_rank_override_is_accepted_when_both_v2_blocks_agree(tmp_path):
    raw = json.loads(FIXTURE.read_text())
    raw['meshConfig']['meshProcess'] = raw['meshGeometry']['meshConfig']['meshProcess'] = 4
    raw['simulationConfig']['solveProcess'] = raw['meshGeometry']['simulationConfig']['solveProcess'] = 4
    runner = make_runner(tmp_path, raw)
    runner._load_cfd_json_only()
    assert runner._data['meshConfig']['meshProcess'] == 4
    assert runner._data['simulationConfig']['solveProcess'] == 4
