"""Independent exact partition and orientation fixtures, without a native run."""
import itertools
from fractions import Fraction
import numpy as np
import pytest
from audit_air_bistellar_cavity import preflight_23,tet_volume,exact_xyz,dot,cross,sub
from audit_air_bistellar_quality import outward_tet_faces,canonical_owner_cycle

def test_exact_symmetric_bipyramid_volume_and_moment():
    tri=[[0,0,0],[2,0,0],[0,2,0]];op=[[.5,.5,-1],[.5,.5,1]]
    r=preflight_23(tri,op)
    assert r['strictInterior'] and r['totalVolumeExactlyPreserved'] and r['firstMomentExactlyPreserved']
    assert Fraction(r['oldTotalVolumeExact'])==Fraction(4,3)
    assert sorted(map(Fraction,r['newVolumeExact']))==[Fraction(1,3),Fraction(1,3),Fraction(2,3)]

@pytest.mark.parametrize('op', [[[0,0,-1],[0,0,1]], [[1,0,-1],[1,0,1]], [[2,2,-1],[2,2,1]]])
def test_edge_vertex_or_outside_intersections_are_rejected(op):
    r=preflight_23([[0,0,0],[2,0,0],[0,2,0]],op)
    assert not r['strictInterior']
    assert r['reason']=='newEdgeIntersectionNotStrictInterior'

def test_parallel_segment_is_exact_singular():
    r=preflight_23([[0,0,0],[2,0,0],[0,2,0]],[[.5,.5,1],[1,.5,1]])
    assert r['reason']=='exactSingular'

def test_exact_translation_and_every_original_triangle_permutation():
    tri=np.asarray([[0,0,0],[2,0,0],[0,2,0]],float);op=np.asarray([[.5,.5,-1],[.5,.5,1]],float)
    for perm in itertools.permutations(range(3)):
        for offset in [np.zeros(3),np.asarray([2**20,-2**18,2**16],float)]:
            r=preflight_23(tri[list(perm)]+offset,op+offset)
            assert r['strictInterior'] and r['totalVolumeExactlyPreserved'] and r['firstMomentExactlyPreserved']
            assert Fraction(r['oldTotalVolumeExact'])==Fraction(4,3)

def test_outward_faces_exactly_close_and_point0_reverse_is_preserved():
    pts={9:np.array([0.,0.,0.]),2:np.array([2.,0.,0.]),7:np.array([0.,2.,0.]),4:np.array([0.,0.,2.])}
    fs=outward_tet_faces([9,2,7,4],pts)
    edges={}
    for face in fs:
        other=next(p for p in pts if p not in face)
        a,b,c=map(exact_xyz,[pts[p] for p in face])
        assert dot(cross(sub(b,a),sub(c,a)),sub(exact_xyz(pts[other]),a))<0
        for u,v in zip(face,face[1:]+face[:1]):edges[(u,v)]=edges.get((u,v),0)+1
        owner,nei,flipped=canonical_owner_cycle(31,12,face)
        assert owner==12 and nei==31 and flipped[0]==face[0]
        x,y,z=map(exact_xyz,[pts[p] for p in flipped])
        assert cross(sub(y,x),sub(z,x))==tuple(-q for q in cross(sub(b,a),sub(c,a)))
    assert all(n==1 and edges.get((v,u))==1 for (u,v),n in edges.items())

def test_zero_volume_tet_cannot_create_closed_candidate():
    with pytest.raises(ValueError,match='Degenerate'):
        outward_tet_faces([0,1,2,3],{0:[0,0,0],1:[1,0,0],2:[0,1,0],3:[.25,.25,0]})
