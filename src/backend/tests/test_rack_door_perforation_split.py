"""Rack doors keep their own perforation: identical doors share the merged
pair (with the doors' perforation, not the editor's fixed 3 mm round),
different doors are split per door (DAML-Testbed: R1 5 mm round, R2 rear
10 mm diamond, all solved as 3 mm round before)."""
from backend.aircooling.runner.bridge_bc import BCSpec, BridgeBCRegistry, PerforatedSpec
from backend.aircooling.runner.stlgen_runner import STLGenRunner


def _runner(doors):
    r = STLGenRunner.__new__(STLGenRunner)
    racks = {}
    for rack, side, perf in doors:
        racks.setdefault(rack, {})[f"{side}Door"] = {
            "enabled": True, "solidName": f"{rack}_{side}_door", "perforated": perf}
    r._data = {"racks": racks}
    return r


def test_door_perforations_are_read_per_door():
    r = _runner([("R1", "inlet", {"shape": "circle", "holeDiameterMm": 5, "openAreaPct": 94.7}),
                 ("R2", "outlet", {"shape": "diamond", "holeDiameterMm": 10, "openAreaPct": 94.7})])
    perf = r._rack_door_perforations()
    assert perf["R1_inlet_door"]["holeDiameterMm"] == 5
    assert perf["R2_outlet_door"]["shape"] == "diamond"


def _registry():
    reg = BridgeBCRegistry()
    editor = BCSpec(bc_type="perforated_baffle", sigma=0.947,
                    perforated=PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=94.7))
    reg.register("merged_rack_door_master", editor)
    reg.register("merged_rack_door_slave", editor)
    return reg


def test_identical_doors_give_the_merged_pair_their_perforation():
    door = {"shape": "diamond", "holeDiameterMm": 5, "openAreaPct": 80}
    r = _runner([("R1", "inlet", door), ("R1", "outlet", door), ("R2", "outlet", door)])
    r._bridge_registry = _registry()
    r._align_merged_door_perforation()
    for side in ("master", "slave"):
        spec = r._bridge_registry.get(f"merged_rack_door_{side}")
        assert spec.perforated.shape == "diamond" and spec.perforated.holeDiameterMm == 5
        assert abs(spec.sigma - 0.80) < 1e-12


def test_different_doors_leave_the_merged_key_alone():
    r = _runner([("R1", "inlet", {"shape": "circle", "holeDiameterMm": 5, "openAreaPct": 94.7}),
                 ("R2", "outlet", {"shape": "diamond", "holeDiameterMm": 10, "openAreaPct": 94.7})])
    r._bridge_registry = _registry()
    r._align_merged_door_perforation()
    assert r._bridge_registry.get("merged_rack_door_master").perforated.holeDiameterMm == 3.0
