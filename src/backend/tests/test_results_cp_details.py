import numpy as np
import json
from pathlib import Path
from backend.liquidcooling.postprocess.results import LiquidResult, CpDetail


def test_cp_details_save_load(tmp_path):
    """cp_details round-trips through JSON."""
    cp = CpDetail(
        edge_id="Rack_1_ls1_cp0",
        chip_temp_K=np.array([310.0, 311.0]),
        water_inlet_temp_K=np.array([293.0, 293.1]),
        water_outlet_temp_K=np.array([296.0, 296.2]),
        inlet_pressure_Pa=np.array([150000.0, 150100.0]),
        outlet_pressure_Pa=np.array([140000.0, 140100.0]),
    )
    result = LiquidResult(
        converged=True,
        final_loss=0.001,
        epochs=100,
        node_pressure_Pa=np.zeros((2, 2)),
        node_ids=["n0", "n1"],
        edge_flow_kg_s=np.zeros((1, 2)),
        edge_ids=["e0"],
        cell_temp_K=np.array([]),
        cell_positions=np.array([]),
        chip_temp_K=np.array([[310.0, 311.0]]),
        water_outlet_temp_K=np.array([[296.0, 296.2]]),
        cp_details=[cp],
    )
    path = tmp_path / "result.json"
    result.save_json(path)

    loaded = LiquidResult.load_json(path)
    assert len(loaded.cp_details) == 1
    d = loaded.cp_details[0]
    assert d.edge_id == "Rack_1_ls1_cp0"
    np.testing.assert_allclose(d.chip_temp_K, [310.0, 311.0])
    np.testing.assert_allclose(d.water_inlet_temp_K, [293.0, 293.1])
    np.testing.assert_allclose(d.water_outlet_temp_K, [296.0, 296.2])
    np.testing.assert_allclose(d.inlet_pressure_Pa, [150000.0, 150100.0])
    np.testing.assert_allclose(d.outlet_pressure_Pa, [140000.0, 140100.0])


def test_cp_details_absent_backward_compat(tmp_path):
    """Old result JSON without cp_details loads with empty list."""
    data = {
        "converged": True, "final_loss": 0.001, "epochs": 50,
        "node_pressure_Pa": [[0]], "node_ids": ["n0"],
        "edge_flow_kg_s": [[0]], "edge_ids": ["e0"],
        "chip_temp_K": [], "water_outlet_temp_K": [], "time_s": [],
    }
    path = tmp_path / "old_result.json"
    path.write_text(json.dumps(data))
    loaded = LiquidResult.load_json(path)
    assert loaded.cp_details == []
