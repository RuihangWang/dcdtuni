"""Written coordinate and actual coupled-role audit, no native execution."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("coupled",ROOT/"tools/audit_air_written_coupled_pairs.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def patch(kind,**fields):return {"type":kind,"fields":fields}
def test_geometry_equal_whole_reverse_still_fails_point_zero():
    a=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]])
    bad=m.pair_check(a,a[::-1])
    assert bad["samePolygon"] and not bad["samePoint0"] and not bad["sameStartReverse"]
    good=m.pair_check(a,a[[0,2,1]])
    assert good["samePoint0"] and good["sameStartReverse"]
    assert good["maximumExpectedPointDistanceM"]==0.
def test_exact_cycle_disagrees_when_geometry_moves_by_one_ulp():
    a=np.array([[1.,0.,0.],[2.,0.,0.],[1.,1.,0.]])
    b=a[[0,2,1]].copy();b[1,0]=np.nextafter(b[1,0],np.inf)
    assert not m.pair_check(a,b)["samePolygon"]
def test_nonfinite_and_empty_polygons_are_not_paired():
    with pytest.raises(ValueError):m.pair_check([],[])
    with pytest.raises(ValueError):m.pair_check([[np.nan,0,0]],[[np.nan,0,0]])
def test_processor_roles_require_actual_rank_and_unique_reciprocal_peer():
    meshes={0:SimpleNamespace(metadata={"p":patch("processor",myProcNo="0",neighbProcNo="1")}),
            1:SimpleNamespace(metadata={"q":patch("processor",myProcNo="1",neighbProcNo="0")})}
    assert m.counterpart(0,"p",meshes)==(1,"q")
    meshes[1].metadata["duplicate"]=meshes[1].metadata["q"]
    with pytest.raises(ValueError,match="ambiguous"):m.counterpart(0,"p",meshes)
    meshes[0].metadata["p"]["fields"]["myProcNo"]="2"
    with pytest.raises(ValueError,match="actual directory"):m.counterpart(0,"p",meshes)
def test_processor_cyclic_uses_reciprocal_actual_references():
    meshes={
      0:SimpleNamespace(metadata={
        "A":patch("cyclic",neighbourPatch="B",transform="coincidentFullMatch"),
        "B":patch("cyclic",neighbourPatch="A",transform="coincidentFullMatch"),
        "p":patch("processorCyclic",myProcNo="0",neighbProcNo="1",referPatch="A")}),
      1:SimpleNamespace(metadata={
        "A":patch("cyclic",neighbourPatch="B",transform="coincidentFullMatch"),
        "B":patch("cyclic",neighbourPatch="A",transform="coincidentFullMatch"),
        "q":patch("processorCyclic",myProcNo="1",neighbProcNo="0",referPatch="B")})}
    assert m.counterpart(0,"p",meshes)==(1,"q")
    assert m.counterpart(1,"q",meshes)==(0,"p")
    meshes[1].metadata["B"]["fields"]["transform"]="rotational"
    with pytest.raises(ValueError,match="transformed"):m.counterpart(0,"p",meshes)
def test_local_cyclic_requires_reciprocal_coincident_transform():
    meshes={0:SimpleNamespace(metadata={
       "A":patch("cyclic",neighbourPatch="B",transform="coincidentFullMatch"),
       "B":patch("cyclic",neighbourPatch="A",transform="coincidentFullMatch")})}
    assert m.counterpart(0,"A",meshes)==(0,"B")
    meshes[0].metadata["B"]["fields"]["neighbourPatch"]="B"
    with pytest.raises(ValueError,match="not reciprocal"):m.counterpart(0,"A",meshes)
