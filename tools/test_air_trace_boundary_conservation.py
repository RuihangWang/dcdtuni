import importlib.util
from pathlib import Path
import numpy as np
import pytest
spec=importlib.util.spec_from_file_location('boundary_audit',Path(__file__).with_name('audit_air_trace_boundary_conservation.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def evaluate(points,faces):
    offsets=np.r_[0,np.cumsum(list(map(len,faces)))].astype(np.uint64)
    labels=np.array([v for f in faces for v in f],dtype=np.int64)
    points=np.asarray(points,dtype=float);reference=(points.min(axis=0)+points.max(axis=0))*.5
    return m.boundary_moments(points,offsets,labels,np.arange(len(faces)),reference)

CUBE=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]],float)
FACES=[[3,2,1,0],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]

def test_unit_cube_and_translation():
    for shift in [np.zeros(3),np.array([1e6,-2e6,3e6])]:
        volume,moment=evaluate(CUBE+shift,FACES)
        assert volume==1.;np.testing.assert_allclose(moment,shift+.5,rtol=0,atol=1e-10)

def test_concave_original_outline_signed_moment():
    xy=np.array([[0,0],[2,0],[2,1],[1,1],[1,2],[0,2]],float)
    points=np.r_[np.c_[xy,np.zeros(6)],np.c_[xy,np.ones(6)]]
    faces=[list(range(5,-1,-1)),list(range(6,12))]
    faces.extend([[i,(i+1)%6,(i+1)%6+6,i+6] for i in range(6)])
    volume,moment=evaluate(points,faces)
    assert volume==3.;np.testing.assert_allclose(moment,[2.5,2.5,1.5],rtol=0,atol=1e-14)

def test_boundary_subdivision_retains_all_signed_integrals():
    first=evaluate(CUBE,FACES);second=evaluate(CUBE,[[3,2,1],[3,1,0]]+FACES[1:])
    assert first[0]==second[0];np.testing.assert_array_equal(first[1],second[1])

def test_nonfinite_boundary_is_rejected():
    with pytest.raises(ValueError,match='non-finite'):
        evaluate([[0,0,0],[1,0,0],[np.nan,1,0]],[[0,1,2]])

def test_boundary_with_two_vertices_is_rejected():
    with pytest.raises(ValueError,match='fewer than three'):
        evaluate([[0,0,0],[1,0,0]],[[0,1]])
