"""Proposal constraints are independent of geometry-trial admission."""
import importlib.util,sys
from pathlib import Path
import pytest
path=Path(__file__).resolve().parents[3]/"tools/audit_air_native_roof_components.py"
sys.path.insert(0,str(path.parent))
spec=importlib.util.spec_from_file_location("native_roof",path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def row(a,b,axis_a=0,axis_b=0,mask=0):
    def pack(delta,axis,cell):
        return {"cell":cell,"axis":axis,"sign":1,"plane":1,
          "pointProposals":[{"point":i,"original":[i,0,0],"delta":delta if i==0 else [0,0,0]} for i in range(9)]}
    return {"totalPackets":2,"packets":[pack(a,axis_a,0),pack(b,axis_b,1)],
      "points":[{"point":i,"original":[i,0,0],"protectedMask":mask if i==0 else 0} for i in range(9)]}
def test_fixed_zero_corner_rejects_neighbor_nonzero_proposal():
    result=m.proposals(row([0,0,0],[.1,0,0]))
    assert result["visibleExactConflictPointIds"]==[0]
def test_zero_corners_allow_different_axes_but_nonzero_metadata_do_not():
    assert m.proposals(row([0,0,0],[0,0,0],0,1))["visibleExactConflictPointIds"]==[]
    assert m.proposals(row([.1,0,0],[.1,0,0],0,1))["visibleExactConflictPointIds"]==[0]
def test_moving_protected_point_is_not_hidden_by_same_delta():
    assert m.proposals(row([.1,0,0],[.1,0,0],mask=2))["visibleMovingProtectedPointIds"]==[0]
def test_packet_coordinate_must_match_raw_point_record():
    data=row([0,0,0],[0,0,0]);data["packets"][1]["pointProposals"][0]["original"]=[99,0,0]
    with pytest.raises(AssertionError):m.proposals(data)
