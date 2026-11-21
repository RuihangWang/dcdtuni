"""Necessary local geometry guards, not a native removal acceptance test."""
import importlib.util
from pathlib import Path
import sys
p=Path(__file__).resolve().parents[3]/"tools/audit_air_collinear_points.py"
spec=importlib.util.spec_from_file_location("collinear_audit",p)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
def test_between_point_is_collinear_but_corner_is_not():
    points=[(0,0,0),(.5,0,0),(1,0,0),(1,1,0),(0,1,0)]
    assert module.incident_check(points,1)["geometricallyRedundantHere"]
    assert not module.incident_check(points,0)["geometricallyRedundantHere"]
def test_triangle_cannot_lose_a_vertex_even_if_collinear():
    row=module.incident_check([(0,0,0),(.5,0,0),(1,0,0)],1)
    assert not row["geometricallyRedundantHere"]
def test_processor_or_cyclic_incidence_is_vetoed():
    points=[(0,0,0),(.5,0,0),(1,0,0),(1,1,0),(0,1,0)]
    for kind in ["processor","processorCyclic","cyclic","cyclicAMI","mappedWall"]:
        row=module.incident_check(points,1,{"type":kind})
        assert row["geometricallyRedundantHere"] and row["coupledVeto"]
def test_point_collinear_in_one_polygon_can_be_corner_in_another():
    a=[(0,0,0),(.5,0,0),(1,0,0),(1,1,0),(0,1,0)]
    b=[(.5,0,0),(.5,1,0),(1,1,0),(1,0,0)]
    rows=[module.incident_check(a,1),module.incident_check(b,0)]
    assert not all(x["geometricallyRedundantHere"] for x in rows)
