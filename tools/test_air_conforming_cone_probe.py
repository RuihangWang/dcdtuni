"""Mathematical guards for the isolated diagnostic; no native acceptance claim."""
import importlib.util
import copy
from pathlib import Path
import numpy as np
import pytest
import air_conforming_cone_probe as probe

ROOT=Path(__file__).resolve().parents[1]
HELPER=ROOT/'tools/audit_air_transition_roof.py'

@pytest.fixture(scope='module')
def source():
    if not HELPER.exists():pytest.skip('Independent geometry helper unavailable')
    h=probe.load(HELPER,'cone_test_helper');a=probe.load(ROOT/'tools/air_transition_roof_probe.py','cone_test_roof')
    g,_=h.evaluate_buffer(a,HELPER.with_name('audit_air_mesh_geometry.py'),0.)
    return h,g

def test_coordinate_canonical_cycle_is_orientation_rotation_and_label_independent():
    points=np.array([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
    expected=[tuple(points[i]) for i in probe.canonical_cycle([0,1,2,3],points)]
    for ids in ([2,3,0,1],[3,2,1,0],[0,3,2,1]):
        assert [tuple(points[i]) for i in probe.canonical_cycle(ids,points)]==expected
    permutation=[2,0,3,1];p2=points[permutation];ids=[permutation.index(i) for i in [0,1,2,3]]
    assert [tuple(p2[i]) for i in probe.canonical_cycle(ids,p2)]==expected

def test_fan_shared_face_closure_preserves_geometry_and_every_parent_volume(source):
    helper,g=source
    out,proof=probe.mixed_partition(g,helper,{20})
    assert len(proof['markedCells'])==9
    assert proof['closureWaves']>0
    assert out.n_cells==270
    assert proof['allOriginalCoordinatesUnchanged']
    assert max(x['relativeError'] for x in proof['parentVolumeConservation'])<1e-12
    assert min(x['minimumBaseInnerMarginM'] for x in proof['apices'])>.49
    report=helper.audit_assembled(out,0.)
    assert report['allOppositeEdgeCyclesClosed']
    assert report['allFacesConvex'] and report['allFacesPlanar']
    assert report['allCellsStrictlyInsideFaceCentreCriterion']
    assert report['minimumTetFanQuality']>0
    assert report['minimumInternalWeight']>.05
    assert report['minimumInternalVolumeRatio']>.01

def test_nonplanar_base_is_rejected_before_partition(source):
    helper,g=source
    g=copy.deepcopy(g)
    g.points=g.points.copy();g.points[0,0]+=.001;g.faces={};g.cells={}
    with pytest.raises(ValueError,match='Unsupported nonplanar/nonconvex base'):
        probe.mixed_partition(g,helper,{0})


def test_original_vertex_fan_reduces_children_without_changing_conservation(source):
    helper,g=source
    out,proof=probe.mixed_partition(g,helper,{20},prefer_anchor=True)
    assert out.n_cells==198
    assert proof['allOriginalCoordinatesUnchanged']
    assert all(x['centrePoint'] is None for x in proof['splitSourceFaces'])
    assert max(x['relativeError'] for x in proof['parentVolumeConservation'])<1e-12
    report=helper.audit_assembled(out,0.)
    assert report['allOppositeEdgeCyclesClosed'] and report['allCellsStrictlyInsideFaceCentreCriterion']
    assert report['minimumTetFanQuality']>0
