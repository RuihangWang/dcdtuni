import copy
import importlib.util
import math
from pathlib import Path
import pytest

PATH=Path(__file__).resolve().parents[3]/'tools/audit_air_exact_physical_quad.py'
spec=importlib.util.spec_from_file_location('exact_physical_quad',PATH)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def fixture():
    points={'0':[0.,0.,0.],'1':[1.,0.,0.],'2':[1.,1.,0.],'3':[0.,1.,0.]}
    role={'name':'wall','type':'wall','nFaces':2,'startFace':7}
    rows=[{'owner':10,'neighbour':None,'patch':copy.deepcopy(role),'sourceBase':42,'sourceZone':-1,'pointIDs':[0,1,2]},
          {'owner':11,'neighbour':None,'patch':copy.deepcopy(role),'sourceBase':42,'sourceZone':-1,'pointIDs':[0,2,3]}]
    return points,rows

def test_exact_outline_area_and_first_moment():
    points,rows=fixture();r=m.audit(points,rows,[10,11])
    assert r['signedAreaTwice']=='2' and r['signedFirstMomentSixTimes']==['3','3']
    assert r['allOriginalNodesRetained'] and r['positiveDisjointTriangleCoverage']

@pytest.mark.parametrize('key,value',[('sourceBase',43),('sourceZone',3),('patch',{'name':'other','type':'wall'})])
def test_wrong_original_role_refused(key,value):
    points,rows=fixture();rows[1][key]=value
    with pytest.raises(ValueError,match='same patch zone'):m.audit(points,rows,[10,11])

@pytest.mark.parametrize('which,value',[('neighbour',12),('owner',99)])
def test_outside_identity_cannot_be_borrowed(which,value):
    points,rows=fixture();rows[1][which]=value
    with pytest.raises(ValueError,match='physical owner'):m.audit(points,rows,[10,11])

def test_one_ulp_warp_is_not_exact_plane():
    points,rows=fixture();points['2'][2]=math.nextafter(0.,1.)
    with pytest.raises(ValueError,match='exact common'):m.audit(points,rows,[10,11])

def test_rotated_planar_face_remains_unsupported():
    points,rows=fixture()
    for value in points.values():value[2]=value[0]+2*value[1]
    with pytest.raises(ValueError,match='exact common'):m.audit(points,rows,[10,11])

@pytest.mark.parametrize('point',[ [.2,.2,0.],[.5,0.,0.],[0.,0.,0.] ])
def test_concave_collinear_or_collapsed_outline_refused(point):
    points,rows=fixture();points['2']=point
    with pytest.raises(ValueError):m.audit(points,rows,[10,11])

def test_same_sense_shared_edge_refused():
    points,rows=fixture();rows[1]['pointIDs']=[0,3,2]
    with pytest.raises(ValueError,match='opposite common'):m.audit(points,rows,[10,11])

def test_nonfinite_original_point_refused():
    points,rows=fixture();points['2'][0]=float('inf')
    with pytest.raises(ValueError,match='finite'):m.audit(points,rows,[10,11])

def test_no_source_base_identity_refused():
    points,rows=fixture();rows[0]['sourceBase']=rows[1]['sourceBase']=-1
    with pytest.raises(ValueError,match='same patch zone'):m.audit(points,rows,[10,11])

def test_whole_cycle_rotation_and_reversal_preserve_exact_shape():
    points,rows=fixture();r=m.audit(points,rows,[10,11])
    for row in rows:row['pointIDs']=row['pointIDs'][1:]+row['pointIDs'][:1]
    q=m.audit(points,rows,[10,11]);assert q['signedAreaTwice']==r['signedAreaTwice']
    for row in rows:row['pointIDs']=list(reversed(row['pointIDs']))
    q=m.audit(points,rows,[10,11]);assert q['signedAreaTwice']=='-2'
    assert q['signedFirstMomentSixTimes']==['-3','-3']
