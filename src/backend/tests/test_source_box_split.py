"""Exact source-box/seam certificates keep non-axis refusals separate from closure."""
import importlib.util
from pathlib import Path
import numpy as np
from fractions import Fraction as F
ROOT=Path(__file__).resolve().parents[3]
S=importlib.util.spec_from_file_location("sourcebox",ROOT/"tools/audit_air_source_box_split.py")
M=importlib.util.module_from_spec(S);S.loader.exec_module(M)

def box(seam=True):
    p=[[0,0,0],[.125,0,0],[.125,1,0],[0,1,0],
       [0,0,.25],[.125,0,.25],[.125,1,.25],[0,1,.25]]
    f=[[3,2,1,0],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]
    if seam:
        p.extend([[0,.375,0],[.125,.375,0],[0,.375,.25],[.125,.375,.25]])
        f=[[3,2,9,1,0,8],[4,5,11,6,7,10],[0,1,5,4],[1,9,11,5],[9,2,6,11],[3,7,6,2],[0,4,10,8],[8,10,7,3]]
    return p,f

def test_original_collinear_seam_nodes_certify_box_and_exact_partition_moments():
    p,f=box();b=M.source_box(p,f);s=M.seam_plan(p,f,b)
    assert b["exactSourceBoxQualified"]
    assert b["aspectRatio"]==8
    assert s["usesOriginalSeamCoordinate"] and s["splitAxis"]==1
    assert F(s["planeExactFraction"])==F(3,8)
    assert s["subBoxVolumeSumExact"] and s["subBoxFirstMomentSumExact"]
    assert s["subBoxVolumeEqualsOriginalSourceExact"]
    assert s["minSharePass"] and s["cleanPlaneNecessaryPass"]
    assert s["newIntersectionPointsRelativeToOriginalParent"]==[]

def test_midpoint_without_original_seam_remains_explicitly_new_geometry():
    p,f=box(False);b=M.source_box(p,f);s=M.seam_plan(p,f,b)
    assert b["exactSourceBoxQualified"]
    assert not s["usesOriginalSeamCoordinate"]
    assert F(s["planeExactFraction"])==F(1,2)
    assert len(s["newIntersectionPointsRelativeToOriginalParent"])==4

def test_tiny_nonaxis_tilt_does_not_corrupt_directed_closure_or_get_exact_certificate():
    p,f=box();p[1][0]+=1e-14
    b=M.source_box(p,f)
    assert b["directedEdgesClosedExactly"]
    assert not b["exactSourceBoxQualified"]
    assert not b["exactAxisBoundingFacePlanes"]
    assert "unsupported" in M.seam_plan(p,f,b)
    diag=M.native_tolerance_box_diagnostics(p,f,b)
    assert diag["matchesReportedNativeFloatingBoxNecessaryTolerances"]
    assert not diag["exactSourceBoxProof"]

def test_reversed_or_open_source_shell_is_not_a_box_certificate():
    p,f=box();f[0]=f[0][::-1]
    b=M.source_box(p,f)
    assert not b["directedEdgesClosedExactly"]
    assert not b["exactSourceBoxQualified"]
    p,f=box();b=M.source_box(p,f[:-1])
    assert not b["directedEdgesClosedExactly"]
    assert not b["exactSourceBoxQualified"]

def test_rotated_closed_box_cannot_enter_axis_split_gate():
    p,f=box();p=np.array(p);p[:,[0,1]]=p[:,[0,1]]@np.array([[.6,-.8],[.8,.6]])
    b=M.source_box(p,f)
    assert b["directedEdgesClosedExactly"]
    assert not b["exactSourceBoxQualified"]
    assert not M.native_tolerance_box_diagnostics(p,f,b)["matchesReportedNativeFloatingBoxNecessaryTolerances"]
