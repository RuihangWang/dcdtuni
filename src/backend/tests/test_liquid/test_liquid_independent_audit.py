"""Product-path liquid results pass an independent conservation audit.

Inputs are saved Shinaide_Liquid and NV_PEK02_Mock runs (CPU). Criteria were
registered before the first run (campaign A, 2026-10-21) and are not tuned:
mass <= 1e-12 kg/s; momentum <= 1e-6 Pa + 1e-9 |term| (scaled form below);
node mixing, plate heat and HX law <= 1e-5 W; plates vs HX secondary vs HX
primary <= 1e-5 W + 1e-9 Q; active adiabatic pipe cells within 1e-6 K of
upstream; no passive edge against its driving pressure; pumps forward with
positive rise; second law per plate and HX; loop elevations close to 1e-9 m;
backward-Euler energy per step <= 1e-4 W + 1e-9 (|Q| + |storage| + |rejection|).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from backend.liquidcooling.dae_runtime import DAESimulation
from backend.tests.test_liquid.independent_audit import steady_audit, transient_energy_audit

# Liquid networks exported from the current case projects (python -m agent.scripted_api export-liquid).
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
SHINAIDE = FIXTURES / 'shinaide_liquid.liquidrun'
NV = FIXTURES / 'nv_pek02_mock.liquidrun'


def cpu(path, *, hx_model=None):
    raw = json.loads(path.read_text())
    raw['execution'].update(device='cpu', max_threads=2)
    if hx_model is not None:
        for edge in raw['edges']:
            if edge['type'] == 'heat_exchanger':
                edge['params']['hxModel'] = hx_model
    return raw


def check_steady(raw):
    sim = DAESimulation(copy.deepcopy(raw))
    report = steady_audit(raw, sim, sim.latest_frame)
    q = report['heat_total_W']
    assert q > 1e3
    assert report['mass_kg_s'] <= 1e-12, report
    assert report['momentum_scaled'] <= 1e-6, report
    assert report['node_mixing_W'] <= 1e-5, report
    assert report['plate_heat_W'] <= 1e-5, report
    assert report['hx_model_W'] <= 1e-5, report
    assert report['energy_plates_vs_hx_secondary_W'] <= 1e-5 + 1e-9 * q, report
    assert report['energy_hx_secondary_vs_primary_W'] <= 1e-5 + 1e-9 * q, report
    assert report['pipe_adiabatic_K'] <= 1e-6, report
    assert report['edge_outlet_vs_solver_K'] <= 1e-6, report
    assert report['flow_against_drive'] == [], report
    assert report['pump_reverse_or_no_rise'] == [], report
    assert report['second_law_violations'] == [], report
    assert report['elevation_closure_m'] <= 1e-9, report
    return report


@pytest.mark.parametrize('path', [SHINAIDE, NV], ids=['shinaide', 'nv-mock'])
def test_saved_runs_pass_the_independent_steady_audit(path):
    check_steady(cpu(path))


@pytest.mark.parametrize('path', [SHINAIDE, NV], ids=['shinaide', 'nv-mock'])
def test_counterflow_hx_model_passes_the_independent_steady_audit(path):
    check_steady(cpu(path, hx_model='counterflow-ntu'))


@pytest.mark.parametrize('path', [SHINAIDE, NV], ids=['shinaide', 'nv-mock'])
def test_backward_euler_steps_conserve_energy_under_load_speed_and_supply_changes(path):
    raw = cpu(path)
    sim = DAESimulation(copy.deepcopy(raw))
    pumps = sim.experiment.pump_rpm_control_edge_ids
    sim.set_inputs(heat_multiplier=1.5, pump_rpm_ratios={p: .8 for p in pumps},
                   primary_inlet_K={h: 298.15 for h in sim.metadata['hx_edge_ids']})
    for _ in range(4):
        sim.step()
    rows = transient_energy_audit(raw, sim, sim.frames)
    assert len(rows) == 4
    assert max(abs(r['storage_W']) for r in rows) > 1.  # the inputs really changed the state
    for row in rows:
        gross = abs(row['heat_W']) + abs(row['storage_W']) + abs(row['rejection_W'])
        assert abs(row['balance_W']) <= 1e-4 + 1e-9 * gross, row
    # The final transient state still satisfies every algebraic law.
    final = sim.latest_frame
    report = steady_audit(raw, sim, final)
    assert report['mass_kg_s'] <= 1e-12 and report['momentum_scaled'] <= 1e-6, report
    assert report['node_mixing_W'] <= 1e-5 and report['flow_against_drive'] == [], report
