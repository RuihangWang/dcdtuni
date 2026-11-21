"""agent.dcdesign.hf: the CFD metrics scored with L0's plant and constraints.

The consistency test feeds the HF scorer air metrics built from L0's own solution (unit returns, coil loads,
server inlets); it must then reproduce L0's PUE and every constraint, which ties the two paths to one plant
and one set of constraint definitions.
"""
import pytest

torch = pytest.importorskip("torch")

from agent.dcdesign import compiler as C  # noqa: E402
from agent.dcdesign import hf  # noqa: E402
from agent.dcdesign import l0  # noqa: E402
from agent.dcdesign import spec as S  # noqa: E402

LIQUID = {"metrics": {"primaryReturnK": {"m_30_hx": 298.17}, "maxChipTemperatureK": 338.89, "heatInputW": 21000.0},
          "objectives": {"pump_hydraulic_power_W": 150.0}}


def _metrics_from_l0(project, r):
    hn = l0.build_network(project)
    uid = {a["meta"]["name"]: a["uid"] for a in project["constructions"]["acus"].values()}
    acus = [{"objectPath": f"constructions.acus.{uid[n]}", "return": {"fluxMeanK": float(r["unit_return_c"][i]) + 273.15},
             "heatRemovedW": float(r["unit_coil_kw"][i]) * 1000.0} for i, n in enumerate(hn.unit_names)]
    racks = {}
    for i, sid in enumerate(hn.server_ids):
        rack, server = sid.split("/")
        racks.setdefault(rack, []).append({"serverUid": server, "areaMeanK": float(r["server_inlet_c"][i]) + 273.15})
    return {"acus": acus, "rackInlet": {"racks": [{"rackUid": k, "servers": v} for k, v in racks.items()]}}


@pytest.mark.parametrize("family", list(S.FAMILIES))
@pytest.mark.parametrize("closed", [(), (1,)])
def test_hf_scorer_reproduces_l0_on_l0_air_temperatures(family, closed):
    design = S.Design(**{**C.reference_design(family).to_json(), "closed_units": closed})
    p = C.compile_design(S.Task(), design)
    r = l0.evaluate(p, LIQUID)
    h = hf.evaluate_hf(p, LIQUID, _metrics_from_l0(p, r))
    assert float(h["pue"]) == pytest.approx(float(r["pue"]), rel=1e-12, abs=1e-12)
    for k, v in r["constraints"].items():
        assert float(h["constraints"][k]) == pytest.approx(float(v), rel=1e-10, abs=1e-10), k
    assert float(h["air_heat_closure_kw"]) == pytest.approx(float(r["air_heat_closure_kw"]), abs=1e-9)


def test_missing_unit_row_is_an_error():
    p = C.compile_design(S.Task(), C.reference_design("INROW_CAC"))
    m = _metrics_from_l0(p, l0.evaluate(p, LIQUID))
    m["acus"] = m["acus"][1:]
    with pytest.raises(ValueError, match="no unit row"):
        hf.evaluate_hf(p, LIQUID, m)
