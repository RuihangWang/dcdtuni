import importlib.util,sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).parent))
spec=importlib.util.spec_from_file_location('axis_closure',Path(__file__).with_name('audit_air_axis_box_plane_closure.py'))
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def test_ulps_near_outer_boundary_are_excluded_not_merged():
    points=np.array([[0.,0.,0.],[1.,1.,1.],[.5,.5,.5],[np.nextafter(0.,1.),.5,1.]])
    original=points.copy();planes,excluded=M.interior_planes(points,np.zeros(3),np.ones(3))
    assert planes==[(0,.5),(1,.5),(2,.5)]
    assert len(excluded)==1 and excluded[0]['coordinate']!=0.
    assert np.array_equal(points,original)

def test_regular_box_selected_sf_known_values():
    assert M.selected_box_d([1,1,1])==pytest.approx(1.)
    assert M.selected_box_d([1,1,1],(0,))==pytest.approx(.5)

def test_rectangular_box_d_independent_gram():
    a,b,c=2.,3.,5.
    expected=(a*b*c)**4/(((a*b+a*c+b*c)/3)**6)
    assert M.selected_box_d([a,b,c])==pytest.approx(expected)

def test_box_widths_must_be_positive_finite():
    for widths in ([0,1,1],[1,float('nan'),1],[1,1,-1]):
        with pytest.raises(ValueError):M.selected_box_d(widths)

def test_closure_deduplicates_shared_neighbor_discovery():
    graph={0:[(0,1),(1,1)],1:[(2,0),(3,2)],2:[(4,1)]}
    row=M.bounded_walk(0,lambda c:graph[c],lambda c:None,3)
    assert row['uniqueDiscoveredCells']==3 and row['closedWithoutBlockersOrTruncation']
    assert row['visitedCellIds']==[0,1,2]

def test_limit_is_unique_cells_and_not_an_acceptance():
    row=M.bounded_walk(0,lambda c:[(c,c+1)],lambda c:None,4)
    assert row['uniqueDiscoveredCells']==4 and row['limitReached']
    assert not row['closedWithoutBlockersOrTruncation']
    assert row['omittedDiscoveryWitnesses'][0]['toCell']==4

def test_protected_star_blocks_propagation_with_witness():
    row=M.bounded_walk(0,lambda c:[(10,1)],lambda c:{'reason':'protected','point':23} if c==1 else None,10)
    assert row['blockerCounts']=={'protected':1}
    assert row['blockerWitnesses'][0]['point']==23
    assert not row['closedWithoutBlockersOrTruncation']
