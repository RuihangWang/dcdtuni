"""Counterexamples distinguishing compatible roof proposals from whole-star quality."""
import importlib.util,sys
from pathlib import Path
import pytest
path=Path(__file__).resolve().parents[3]/"tools/audit_air_roof_components.py"
sys.path.insert(0,str(path.parent))
spec=importlib.util.spec_from_file_location("roof_component",path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def test_same_plane_edge_midpoint_displacement_agrees():
    rows=m.shared(m.packet([0,0,0],[1,1,1]),m.packet([0,1,0],[1,1,1]))
    assert len(rows)==1 and rows[0]["exactDeltaEqual"]
    assert rows[0]["firstDelta"]==[.00025,0,0]
def test_orthogonal_normals_on_shared_midpoint_conflict():
    rows=m.shared(m.packet([0,1,0],[1,1,1],0),m.packet([1,0,0],[1,1,1],1))
    assert len(rows)==1 and not rows[0]["exactDeltaEqual"]
@pytest.mark.parametrize("n",[2,4,6])
def test_frozen_rectangular_border_has_only_zero_planar_displacement(n):
    assert m.fixed_border_rank(n)["nullity"]==0
def test_individually_compatible_island_warps_whole_star():
    row=m.evaluate()
    assert row["allNineIdealCompatiblePackets"]["nonplanarFaceCountAt1e-12m"]==0
    assert row["allNineIdealCompatiblePackets"]["cellsFailingStrictFaceCentreCriterion"]==0
    assert row["onlyCentralPacketEntireStar"]["nonplanarFaceCountAt1e-12m"]>0
    assert row["allNinePacketsWithOuterBorderFixed"]["nonplanarFaceCountAt1e-12m"]>0
    assert row["clampedMaximumMixedDifferenceM"]>1e-5
