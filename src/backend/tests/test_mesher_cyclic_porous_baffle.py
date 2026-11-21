"""A perforated baffle the mesher wrote as a cyclic pair keeps its pressure
drop. dcCart writes flow-through baffle pairs itself (no createBaffles) as
cyclic patches; p_rgh took every cyclic patch as pure cyclic, so perforated
floors, doors and blanking panels were fully open from 2026-09-14
(4a740aa64) to 2026-10-20 (Base2-9: sigma 0.64, 3 mm holes)."""
from backend.aircooling.runner.bridge_bc import BCSpec, BridgeBCRegistry, PerforatedSpec
from backend.aircooling.runner.fields import FieldGenerator


def _p_rgh(tmp_path, specs, patches):
    reg = BridgeBCRegistry()
    for name, spec in specs.items():
        reg.register(name, spec)
    gen = FieldGenerator(case_dir=tmp_path, bridge_registry=reg)
    gen._collect_data()
    out = tmp_path / "p_rgh"
    gen._write_p_rgh_field(out, patches)
    return out.read_text()


def _block(text, name):
    i = text.index(f"    {name}\n")
    return text[i:text.index("    }", i)]


def test_perforated_cyclic_pair_gets_porous_baffle_pressure(tmp_path):
    perf = PerforatedSpec(shape="circle", holeDiameterMm=3.0, openAreaPct=64.0)
    spec = BCSpec(bc_type="perforated_baffle", sigma=0.64, perforated=perf)
    text = _p_rgh(tmp_path, {"floor_master": spec, "floor_slave": spec},
                  [("floor_master", "cyclic"), ("floor_slave", "cyclic")])
    for side in ("floor_master", "floor_slave"):
        block = _block(text, side)
        assert "porousBafflePressure" in block and "patchType       cyclic;" in block
        assert "relax           0.05;" in block


def test_fully_open_and_unbound_cyclic_patches_stay_cyclic(tmp_path):
    text = _p_rgh(tmp_path, {"open_master": BCSpec(bc_type="perforated_baffle", sigma=1.0)},
                  [("open_master", "cyclic"), ("other_master", "cyclic"), ("proc0to1", "processor")])
    assert "type            cyclic;" in _block(text, "open_master")
    assert "type            cyclic;" in _block(text, "other_master")
    assert "type            processor;" in _block(text, "proc0to1")
