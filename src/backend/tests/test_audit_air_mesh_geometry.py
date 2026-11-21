"""Independent geometric examples for diagnostic convexity claims."""
import importlib.util
from pathlib import Path
import sys
import numpy as np
import pytest

path=Path(__file__).resolve().parents[3]/"tools/audit_air_mesh_geometry.py"
spec=importlib.util.spec_from_file_location("geometry_audit",path)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)

def geometry(points,faces):
    mesh=module.Geometry.__new__(module.Geometry)
    mesh.points=np.asarray(points,dtype=float)
    mesh.offsets=np.r_[0,np.cumsum([len(f) for f in faces])]
    mesh.vertices=np.asarray([v for f in faces for v in f])
    mesh.owner=np.zeros(len(faces),dtype=int)
    mesh.neighbour=np.empty(0,dtype=int)
    mesh.cell_offsets=np.array([0,len(faces)])
    mesh.cell_faces=np.arange(len(faces))
    mesh.faces={};mesh.cells={};mesh.patches=[]
    return mesh

def split_cube():
    points=[(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1),(0,.5,0),(0,.5,1)]
    faces=[(0,4,9,8),(8,9,7,3),(1,2,6,5),(0,1,5,4),(3,7,6,2),(0,8,3,2,1),(4,5,6,7,9)]
    return points,faces

def test_coplanar_subdivided_cube_is_convex_despite_checker_trigger():
    mesh=geometry(*split_cube())
    row=mesh.classify(0,True)
    assert row["classification"]=="convex-with-coplanar-facets"
    assert row["checkerTriggerCounts"]["coplanar"]==2
    assert row["allVertexMaximumOutsideMetres"]<1e-14
    assert row["volumeM3"]==pytest.approx(1)
    assert row["pyramidCandidateEligible"]

def test_l_prism_has_true_halfspace_and_polygon_violation():
    base=[(0,0),(2,0),(2,1),(1,1),(1,2),(0,2)]
    points=[(x,y,z) for z in [0,1] for x,y in base]
    faces=[tuple(range(5,-1,-1)),tuple(range(6,12))]
    faces += [(i,(i+1)%6,(i+1)%6+6,i+6) for i in range(6)]
    row=geometry(points,faces).classify(0)
    assert row["classification"]=="vertex-halfspace-violation"
    assert row["allVertexMaximumOutsideMetres"]==pytest.approx(1)
    assert not row["allFacePolygonsConvex"]
    assert not row["pyramidCandidateEligible"]
    assert row["volumeM3"]==pytest.approx(3)

def test_tetrahedron_has_positive_pyramids_without_checker_trigger():
    points=[(0,0,0),(1,0,0),(0,1,0),(0,0,1)]
    faces=[(0,2,1),(0,1,3),(0,3,2),(1,2,3)]
    row=geometry(points,faces).classify(0)
    assert row["classification"]=="convex-no-face-plane-trigger"
    assert row["checkerTriggerCounts"]=={"coplanar":0,"positiveOutside":0,"nearPlanarMargin":0}
    assert row["volumeM3"]==pytest.approx(1/6)
    assert row["pyramidCandidateEligible"]

def test_warped_polyhedron_is_not_claimed_proven_convex():
    points,faces=split_cube()
    points[5]=(1,0,1.1)
    row=geometry(points,faces).classify(0)
    assert row["classification"]=="nonplanar-face-unproved"
    assert not row["pyramidCandidateEligible"]

def test_ascii_hashset_under_binary_header_is_read_as_written(tmp_path):
    path=tmp_path/"concaveCells"
    path.write_text("FoamFile { format binary; class cellSet; }\n3\n(\n1\n2\n8\n)\n")
    assert module.cell_set(path).tolist()==[1,2,8]


@pytest.mark.parametrize("payload,expected",[("3(1 2 8)",[1,2,8]),("0()",[])])
def test_compact_ascii_diagnostic_set(tmp_path,payload,expected):
    path=tmp_path/"faceSet"
    path.write_text("FoamFile { format binary; class faceSet; }\n"+payload+"\n")
    assert module.cell_set(path).tolist()==expected


def test_preserved_base_cone_retains_coplanar_side_counterexample():
    mesh=geometry(*split_cube())
    probe=module.cone_probe(mesh,0)
    assert probe["childVolumeSumM3"]==pytest.approx(1)
    assert probe["childrenFailingCheckerPlaneCriterion"]==2
    assert len(probe["children"])==7

def test_global_polygon_edge_support_rejects_self_crossing_star():
    angles=np.arange(5)*2*np.pi/5
    points=np.c_[np.cos(angles),np.sin(angles),np.zeros(5)]
    mesh=module.Geometry.from_arrays(points,[(0,2,4,1,3)])
    # Same-sign local turns do not establish a simple convex polygon.
    assert mesh.face(0)[3]>0
    assert mesh.face(0)[5]<0
