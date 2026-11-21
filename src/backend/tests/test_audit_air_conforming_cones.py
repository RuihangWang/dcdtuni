"""Canonical geometry equivalence and concrete unsupported pairing counterexamples."""
import importlib.util, sys
from pathlib import Path
import numpy as np

root=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(root/"tools"))
spec=importlib.util.spec_from_file_location("cone_audit",root/"tools/audit_air_conforming_cones.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
# Actual source face4 of the54-cell buffer.
points=np.array([[0,-1,0],[0,0,0],[1,0,0],[1,-.5,0],[1,-1,0]],float)

def test_canonical_cycle_survives_local_labels_rotations_and_reversal():
    expected=points[m.canonical_cycle(list(range(5)),points)]
    for order in ([0,1,2,3,4],[4,2,0,3,1],[3,0,4,1,2]):
        local=points[order]
        inverse={original:i for i,original in enumerate(order)}
        sequence=[inverse[i] for i in range(5)]
        for cycle in (sequence,list(reversed(sequence))):
            for j in range(5):
                answer=m.canonical_cycle(cycle[j:]+cycle[:j],local)
                assert local[answer].tobytes()==expected.tobytes()

def test_opposite_outward_normals_need_common_triangle_key_not_local_ordinal():
    cycle=m.canonical_cycle(list(range(5)),points)
    reverse=list(reversed(cycle))
    assert m.edge_keys(cycle)!=m.edge_keys(reverse)
    assert sorted(m.edge_keys(cycle))==sorted(m.edge_keys(reverse))

def test_rotational_cyclic_cannot_assume_local_canonical_start_agrees():
    rotation=np.array([[0,-1,0],[1,0,0],[0,0,1]])
    assert m.canonical_cycle(list(range(5)),points)!=m.canonical_cycle(list(range(5)),points@rotation.T)
    restored=(points@rotation.T)@rotation
    assert m.canonical_cycle(list(range(5)),restored)==m.canonical_cycle(list(range(5)),points)

def test_proper_crossing_only_misses_positive_fan_self_touch_and_overlap():
    proof=m.self_touching_fan_counterexample()
    assert proof["properCrossings"]==[]
    assert min(proof["anchorZeroTriangleAreasM2"])>0
    assert proof["fanAreaM2"]==proof["OFareaM2"]
    assert proof["maximumCentreDifferenceM"]<1e-15
    assert proof["selfTouch"]["exactCollinearityCross"]==[0,0,0]
    assert proof["selfTouch"]["strictlyBetweenEndpointDot"]<0
