"""Independent safeguards for read-only exact cone diagnostics."""
from pathlib import Path
import importlib.util
import json
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[3]
def module(name):
    path=ROOT/"tools"/(name+".py")
    spec=importlib.util.spec_from_file_location(name,path)
    out=importlib.util.module_from_spec(spec);spec.loader.exec_module(out);return out

trace=module("air_cone_trace_probe")
union=module("air_cone_child_union_probe")
fan=module("air_face_fan_quality_probe")

def write_empty_trace(path,rank=0):
    source={"format":"conforming-input-stage/v1","rank":rank,"littleEndian":True,
        "labelBytes":4,"scalarBytes":8,"pointStrideBytes":24,"points":1,"faces":1,
        "cells":1,"facePointLabels":3}
    written={"format":"conforming-written-stage/v1","rank":rank,
        "labelBytes":4,"cells":1,"faces":1,"points":1}
    prefixes=[path/f"conforming-source-rank{rank}-",path/f"conforming-written-rank{rank}-"]
    Path(str(prefixes[0])+"manifest.json").write_text(json.dumps(source))
    Path(str(prefixes[1])+"manifest.json").write_text(json.dumps(written))
    arrays={"points.f64":(np.array([[0.,0.,0.]]),"<f8"),"face-offsets.u64":(np.array([0,3]),"<u8"),
        "face-points.i32":(np.array([0,0,0]),"<i4"),"apex.f64":(np.array([[0.,0.,0.]]),"<f8"),
        "parent-volume.f64":(np.array([1.]),"<f8")}
    for name in ["owner","neighbour","patch","zone"]:arrays[name+".i32"]=(np.array([0]),"<i4")
    for name in ["face-alive","cell-alive"]:arrays[name+".u8"]=(np.array([1]),"u1")
    for name,(a,dtype) in arrays.items():a.astype(dtype).tofile(str(prefixes[0])+name)
    for name in ["cell-parent","cell-native","face-base","face-native","point-native"]:
        np.array([0],dtype="<i4").tofile(str(prefixes[1])+name+".i32")
    return prefixes

def test_declared_trace_layout_is_checked_before_geometry(tmp_path):
    source,_=write_empty_trace(tmp_path)
    p=Path(str(source)+"manifest.json");meta=json.loads(p.read_text())
    meta["pointStrideBytes"]=32;p.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match="storage layout"):trace.read_trace(tmp_path,0)

def test_trace_rejects_rank_alias_and_truncated_payload(tmp_path):
    source,written=write_empty_trace(tmp_path)
    p=Path(str(written)+"manifest.json");meta=json.loads(p.read_text())
    meta["rank"]=1;p.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match="identity"):trace.read_trace(tmp_path,0)
    meta["rank"]=0;p.write_text(json.dumps(meta))
    Path(str(source)+"points.f64").write_bytes(b"\0"*16)
    with pytest.raises(ValueError,match="size mismatch"):trace.read_trace(tmp_path,0)

def test_two_triangles_merge_to_same_directed_outline():
    # Common diagonal has opposite directions. Outer boundary points remain.
    result=union.loop_from_edges([(0,1),(1,2),(2,0),(0,2),(2,3),(3,0)])
    assert result==[0,1,2,3]

@pytest.mark.parametrize("edges",[
    [(0,1),(1,2),(2,0),(3,4),(4,5),(5,3)], # disconnected outlines
    [(0,1),(1,2),(2,0),(0,1),(1,3),(3,0)], # repeated boundary edge
    [(0,1),(1,2),(2,0),(0,3),(3,4),(4,0)], # shared vertex, two loops
])
def test_non_simple_union_outlines_refused(edges):
    assert union.loop_from_edges(edges) is None

def test_exact_collinear_anchor_fans_cannot_be_writer_faces():
    # A rectangle has a required collinear midpoint on one edge.
    p=np.array([[0.,0.,0.],[.5,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.]])
    families=fan.candidate_fans(p,fan.newell(p))
    assert families
    assert {f["anchor"] for f in families}=={1,3,4}
    for family in families:
        assert all(np.linalg.norm(fan.newell(t))>1e-12 for t in family["pieces"])

def test_roundoff_positive_triangle_below_writer_absolute_floor_is_refused():
    p=np.array([[0.,0.,0.],[1.,0.,0.],[.5,1e-17,0.]])
    assert fan.candidate_fans(p,fan.newell(p))==[]


def axis_face(axis,sign,plane):
    p=np.zeros((4,3));other=[j for j in range(3) if j!=axis];p[:,axis]=plane
    p[:,other]=[[0,0],[1,0],[1,1],[0,1]]
    a=np.zeros(3);a[axis]=sign;c=p.mean(axis=0)
    return {'coordinates':p.tolist(),'outwardArea':a.tolist(),'OFcentre':c.tolist()}

def test_axis_kernel_uses_inward_constraints_not_outer_bbox():
    # A re-entrant face further restricts a kernel while outer extent stays 2.
    faces=[axis_face(j,s,v) for j in range(3) for s,v in [(-1,0),(1,2)]]
    faces.append(axis_face(0,1,.5))
    result=trace.axis_kernel_centre(faces)
    assert result['point']==[.25,1.,1.]
    assert result['hi']==[.5,2.,2.]

def test_axis_kernel_refuses_missing_direction_and_thin_or_warped_input():
    faces=[axis_face(j,s,v) for j in range(3) for s,v in [(-1,0),(1,2)]]
    assert trace.axis_kernel_centre(faces[:-1]) is None
    faces[1]=axis_face(0,1,5e-6)
    assert trace.axis_kernel_centre(faces) is None
    faces[1]=axis_face(0,1,2)
    faces[1]['coordinates'][0][0]+=1e-5
    assert trace.axis_kernel_centre(faces) is None
