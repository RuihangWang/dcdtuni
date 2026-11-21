"""The boundary-condition audit catches each silent mismatch it was built
from, and stays quiet on a correct case."""
from backend.aircooling.runner.bc_audit import audit
from backend.aircooling.runner.bridge_bc import BCSpec, BridgeBCRegistry, PerforatedSpec, perforated_to_porous_baffle


def _field(path, blocks):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"    {n}\n    {{\n{b}\n    }}\n" for n, b in blocks.items())
    path.write_text("FoamFile {}\nboundaryField\n{\n" + body + "}\n")


PERF = PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=64.0)
D, I, _ = perforated_to_porous_baffle(PERF)


def _case(tmp_path, *, inlet_u="flowRateOutletVelocity", floor_p="porousBafflePressure",
          supply_q=1.0, outlets=(0.3, 0.2), drop_supply=False,
          supply_u=None, supply_p=None):
    inlet = f"        type {inlet_u};" + ("\n        volumetricFlowRate 0.5;" if inlet_u.startswith("flowRate") else "")
    U = {"S_inlet_patch": inlet,
         "S_outlet_p0": f"        type flowRateInletVelocity;\n        volumetricFlowRate {outlets[0]};",
         "S_outlet_p1": f"        type flowRateInletVelocity;\n        volumetricFlowRate {outlets[1]};",
         "F_master": "        type cyclic;", "F_slave": "        type cyclic;",
         "AC_ret": "        type pressureInletOutletVelocity;"}
    if not drop_supply:
        U["AC_sup"] = (f"        type {supply_u};" if supply_u else
                       f"        type flowRateInletVelocity;\n        volumetricFlowRate {supply_q};")
    T = {n: "        type zeroGradient;" for n in U}
    T.update({"S_inlet_patch": "        type inletOutlet;", "S_outlet_p0": "        type uniformFixedValue;",
              "S_outlet_p1": "        type uniformFixedValue;", "F_master": "        type cyclic;",
              "F_slave": "        type cyclic;", "AC_sup": "        type fixedValue;\n        value uniform 293.15;"})
    porous = f"        type {floor_p};\n        D {D};\n        I {I};"
    P = {n: "        type fixedFluxPressure;" for n in U}
    P.update({"F_master": porous, "F_slave": porous, "AC_ret": "        type fixedValue;"})
    if supply_p:
        P["AC_sup"] = f"        type {supply_p};"
    for name, blocks in (("U", U), ("T", T), ("p_rgh", P)):
        _field(tmp_path / "processor0" / "0" / name, blocks)
    manifest = {"boundaryBindings": {
        "a": {"patchName": "AC_sup", "bcType": "acu_supply", "parameters": {"flowRateM3s": 1.0, "temperatureC": 20}},
        "b": {"patchName": "AC_ret", "bcType": "acu_return", "parameters": {}},
        "c": {"patchName": "S_inlet_patch", "bcType": "fan_inlet", "parameters": {"flowRateM3s": 0.5}},
        "d": {"patchName": "S_outlet_p0", "bcType": "fan_outlet", "parameters": {"flowRateM3s": 0.25}},
        "e": {"patchName": "S_outlet_p1", "bcType": "fan_outlet", "parameters": {"flowRateM3s": 0.25}}},
        "porousBindings": {n: {"patchName": n, "parameters": {"sigma": 0.64}} for n in ("F_master", "F_slave")}}
    reg = BridgeBCRegistry()
    for n in ("F_master", "F_slave"):
        reg.register(n, BCSpec(bc_type="perforated_baffle", sigma=0.64, perforated=PERF))
    return audit(tmp_path, manifest, reg, perforated_to_porous_baffle)["issues"]


def _kinds(issues):
    return sorted({(i["kind"], i["patch"]) for i in issues})


def test_a_correct_case_has_no_issues(tmp_path):
    assert _case(tmp_path) == []


def test_perforated_baffle_written_as_pure_cyclic(tmp_path):
    assert ("type", "F_master") in _kinds(_case(tmp_path, floor_p="cyclic"))


def test_fan_inlet_turned_into_a_wall_and_its_imbalance(tmp_path):
    kinds = _kinds(_case(tmp_path, inlet_u="noSlip"))
    assert ("type", "S_inlet_patch") in kinds and ("balance", "S") in kinds


def test_outlets_that_do_not_add_up_to_the_inlet(tmp_path):
    assert ("balance", "S") in _kinds(_case(tmp_path, outlets=(0.3, 0.1)))


def test_supply_flow_and_a_missing_supply_patch(tmp_path):
    assert ("value", "AC_sup") in _kinds(_case(tmp_path, supply_q=0.8))
    assert ("missing_patch", "AC_sup") in _kinds(_case(tmp_path / "x", drop_supply=True))


def test_liquid_cooled_server_inlet_named_front_balances(tmp_path):
    # the air inlet of a liquid-cooled fan server is <stem>_front
    U = {"L_front": "        type flowRateOutletVelocity;\n        volumetricFlowRate 0.151;",
         "L_outlet_patch": "        type flowRateInletVelocity;\n        volumetricFlowRate 0.151;"}
    T = {"L_front": "        type inletOutlet;", "L_outlet_patch": "        type uniformFixedValue;"}
    P = {n: "        type fixedFluxPressure;" for n in U}
    for name, blocks in (("U", U), ("T", T), ("p_rgh", P)):
        _field(tmp_path / "processor0" / "0" / name, blocks)
    manifest = {"boundaryBindings": {
        "a": {"patchName": "L_front", "bcType": "fan_inlet", "parameters": {"flowRateM3s": 0.151}},
        "b": {"patchName": "L_outlet_patch", "bcType": "fan_outlet", "parameters": {"flowRateM3s": 0.151}}}}
    assert audit(tmp_path, manifest, None, perforated_to_porous_baffle)["issues"] == []


def test_sealed_region_supply_as_a_pressure_inlet(tmp_path):
    # The supply of a sealed region with no return follows pressure.
    assert _case(tmp_path / "pair", supply_u="pressureInletOutletVelocity",
                 supply_p="totalPressure") == []
    # Half of the pair is still a mismatch.
    assert ("type", "AC_sup") in _kinds(_case(tmp_path / "u", supply_u="pressureInletOutletVelocity"))
    assert ("type", "AC_sup") in _kinds(_case(tmp_path / "p", supply_p="totalPressure"))
