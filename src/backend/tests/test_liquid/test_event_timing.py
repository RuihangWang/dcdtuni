"""
Tests for correct event-time → step-index conversion in the liquid compiler.

Bug C2 fix: step_idx = min(int(time_s / dt_s), N_t - 1)
"""

import copy
import json
import pathlib

import pytest

from backend.liquidcooling.schema import LiquidrunConfig
from backend.liquidcooling.graphcompiling.compiler import compile_graph

FIXTURE_PATH = pathlib.Path(__file__).parent / "minimal.liquidrun"


def _base_data() -> dict:
    """Return the minimal.liquidrun fixture as a plain dict."""
    return json.loads(FIXTURE_PATH.read_text())


def _add_cold_plate_edge(data: dict, edge_id: str, Q_W: float = 350.0) -> None:
    """
    Add a cold_plate edge between supply and return nodes.

    The minimal fixture has two pressure_bc nodes ('supply' and 'return').
    We insert a cold_plate edge alongside the existing pipe edge so that the
    compiler can exercise _compile_cold_plates with events.
    """
    data["edges"].append({
        "id": edge_id,
        "type": "cold_plate",
        "from": "supply",
        "to": "return",
        "params": {
            "Q_W": Q_W,
            "dp_a": 1e8,
            "dp_b": 1e5,
            "UA_W_per_K": 150.0,
            "C_solid_J_per_K": 50.0,
            "V_fluid_m3": 5e-6,
        },
    })


def _add_event(data: dict, time_s: float, target: str, field: str, value: float) -> None:
    data["events"].append({
        "time_s": time_s,
        "target": target,
        "field": field,
        "value": value,
    })


# ---------------------------------------------------------------------------


class TestColdPlateEventTiming:
    """Cold-plate Q_W events must fire at the correct step index."""

    def test_event_fires_at_correct_step(self):
        """dt=0.5, N_t=20, event at time_s=5.0 → step 10.

        Steps 0-9: Q_W == 350. Steps 10-19: Q_W == 500.
        """
        data = _base_data()
        data["time"] = {"dt_s": 0.5, "N_t": 20, "bdf_order": 1}
        _add_cold_plate_edge(data, "cp1", Q_W=350.0)
        _add_event(data, time_s=5.0, target="cp1", field="Q_W", value=500.0)

        cfg = LiquidrunConfig(**data)
        graph = compile_graph(cfg)

        assert graph.cp_params is not None
        Q_W = graph.cp_params.Q_W  # [1, 20]
        assert Q_W.shape == (1, 20)

        for t in range(10):
            assert Q_W[0, t].item() == pytest.approx(350.0), (
                f"step {t}: expected 350, got {Q_W[0, t].item()}"
            )
        for t in range(10, 20):
            assert Q_W[0, t].item() == pytest.approx(500.0), (
                f"step {t}: expected 500, got {Q_W[0, t].item()}"
            )

    def test_event_at_dt_1(self):
        """dt=1.0, time_s=5.0 → step 5.

        Steps 0-4: Q_W == 350. Steps 5-9: Q_W == 999.
        """
        data = _base_data()
        # minimal.liquidrun already has dt_s=1.0, N_t=10
        _add_cold_plate_edge(data, "cp1", Q_W=350.0)
        _add_event(data, time_s=5.0, target="cp1", field="Q_W", value=999.0)

        cfg = LiquidrunConfig(**data)
        graph = compile_graph(cfg)

        assert graph.cp_params is not None
        Q_W = graph.cp_params.Q_W  # [1, 10]
        assert Q_W.shape == (1, 10)

        for t in range(5):
            assert Q_W[0, t].item() == pytest.approx(350.0), (
                f"step {t}: expected 350, got {Q_W[0, t].item()}"
            )
        for t in range(5, 10):
            assert Q_W[0, t].item() == pytest.approx(999.0), (
                f"step {t}: expected 999, got {Q_W[0, t].item()}"
            )

    def test_event_clamped_to_last_step(self):
        """dt=1.0, N_t=10, time_s=100.0 → clamps to step 9.

        Steps 0-8: Q_W == 350. Step 9: Q_W == 600.
        """
        data = _base_data()
        # minimal.liquidrun already has dt_s=1.0, N_t=10
        _add_cold_plate_edge(data, "cp1", Q_W=350.0)
        _add_event(data, time_s=100.0, target="cp1", field="Q_W", value=600.0)

        cfg = LiquidrunConfig(**data)
        graph = compile_graph(cfg)

        assert graph.cp_params is not None
        Q_W = graph.cp_params.Q_W  # [1, 10]
        assert Q_W.shape == (1, 10)

        for t in range(9):
            assert Q_W[0, t].item() == pytest.approx(350.0), (
                f"step {t}: expected 350, got {Q_W[0, t].item()}"
            )
        assert Q_W[0, 9].item() == pytest.approx(600.0), (
            f"step 9: expected 600, got {Q_W[0, 9].item()}"
        )
