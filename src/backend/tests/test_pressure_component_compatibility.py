"""MPI-only compatibility checks before adding a pressure gauge."""
import os
from pathlib import Path
import subprocess
import re

import numpy as np
import pytest
from backend.tests.test_pressure_component_references import _case, _run, _cells, _header

KERNEL=os.environ.get("DCDTUNI_PRESSURE_KERNEL")
pytestmark=pytest.mark.skipif(not KERNEL,reason="isolated compatibility kernel not configured")

def configured(tmp_path,mode,amplitude=1,gradient=None,layout="connected"):
    case=_case(tmp_path/"case",layout=layout,ranks=2,local_nx=2)
    with (case/"system/gaugeTestDict").open("a") as out:
        out.write(f"sourceMode {mode}; sourceAmplitude {amplitude:.17g};")
    if gradient is not None:
        p=case/"0/p_rgh"
        p.write_text(p.read_text().replace("type zeroGradient;",
            f"type fixedGradient; gradient uniform {gradient};"))
    _run(case,["decomposePar","-force"])
    return case

def invoke(case):
    reply=subprocess.run(["mpirun","-np","2",KERNEL,"-parallel"],cwd=case,
        text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
    (case/"log.mpirun").write_text(reply.stdout)
    return reply

def test_balanced_two_cell_source_across_mpi_passes(tmp_path):
    case=configured(tmp_path,"balancedConnected")
    reply=invoke(case)
    assert reply.returncode==0,reply.stdout
    assert [x[1] for x in _cells(case)]==pytest.approx([5,6],abs=1e-9)

def test_unbalanced_two_cell_source_across_mpi_fails_before_gauge(tmp_path):
    case=configured(tmp_path,"uniform")
    reply=invoke(case)
    assert reply.returncode!=0
    assert "Incompatible assembled RHS in free pressure component" in reply.stdout
    assert not (case/"processor0/gauge-kernel-result.csv").exists()

@pytest.mark.parametrize("amplitude",[5,5+1e-14])
def test_deferred_neumann_boundary_source_balances_raw_source(tmp_path,amplitude):
    case=configured(tmp_path,"uniform",amplitude,gradient=1)
    reply=invoke(case)
    assert reply.returncode==0,reply.stdout
    assert [x[1] for x in _cells(case)]==pytest.approx([5,5],abs=1e-9)

def test_unbalanced_neumann_boundary_source_fails(tmp_path):
    case=configured(tmp_path,"none",gradient=1)
    reply=invoke(case)
    assert reply.returncode!=0
    assert "Incompatible assembled RHS in free pressure component" in reply.stdout

def test_anchored_component_source_does_not_trigger_free_component_gate(tmp_path):
    case=configured(tmp_path,"anchoredOnly",layout="disconnected")
    reply=invoke(case)
    assert reply.returncode==0,reply.stdout
    assert len(_cells(case))==4
    assert all(np.isfinite(x[1]) for x in _cells(case))
    assert [x[1] for x in _cells(case) if x[0][0]>2]==pytest.approx([5,5],abs=1e-9)

def test_jump_pressure_field_is_rejected_before_affine_compatibility_claim(tmp_path):
    case=_case(tmp_path/"case",cyclic=True,ranks=2)
    p=case/"0/p_rgh"
    p.write_text(p.read_text().replace("type cyclic;",
        "type fixedJump; patchType cyclic; jump uniform 2; value uniform 37;"))
    _run(case,["decomposePar","-force"])
    reply=invoke(case)
    assert reply.returncode!=0
    assert "jump/custom pressure fields" in reply.stdout


def periodic_flow_case(tmp_path,nx=3,ny=4,nz=5,shear=0,nonconstant=False):
    case=_case(tmp_path/"case",layout="connected",ranks=2)
    control=case/"system/controlDict"
    control.write_text(control.read_text()+"writePrecision 17;")
    faces=[("xlo","xhi","(0 4 7 3)","(1 2 6 5)",f"2 {2*shear} 0"),
           ("ylo","yhi","(0 1 5 4)","(3 7 6 2)","0 1 0"),
           ("zlo","zhi","(0 3 2 1)","(4 5 6 7)","0 0 1")]
    patches=[]
    bcs=[]
    for lo,hi,lf,hf,vector in faces:
        reverse=" ".join(str(-float(x)) for x in vector.split())
        patches += [f"{lo} {{ type cyclic; neighbourPatch {hi}; transform translational; separationVector ({vector}); faces ({lf}); }}",
                    f"{hi} {{ type cyclic; neighbourPatch {lo}; transform translational; separationVector ({reverse}); faces ({hf}); }}"]
        bcs += [f"{lo} {{ type cyclic; }}",f"{hi} {{ type cyclic; }}"]
    (case/"system/blockMeshDict").write_text(_header("blockMeshDict")
        + f"convertToMeters 1; vertices ((0 0 0)(2 {2*shear} 0)(2 {1+2*shear} 0)(0 1 0)"
        + f"(0 0 1)(2 {2*shear} 1)(2 {1+2*shear} 1)(0 1 1)); "
        + f"blocks (hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1));"
        + "edges (); boundary ("+" ".join(patches)+"); mergePatchPairs ();")
    initial="uniform 37"
    if nonconstant:
        pressure=[37+np.sin(np.pi*2*(i+.5)/nx)*np.cos(2*np.pi*(j+.5)/ny)
                  for k in range(nz) for j in range(ny) for i in range(nx)]
        initial=f"nonuniform List<scalar> {len(pressure)} ("+" ".join(f"{x:.17g}" for x in pressure)+")"
    (case/"0/p_rgh").write_text(_header("p_rgh","volScalarField")
        + "dimensions [0 2 -2 0 0 0 0]; internalField "+initial+"; boundaryField {"
        + " ".join(bcs)+"}")
    with (case/"system/gaugeTestDict").open("a") as out:
        out.write("sourceMode conservativeFlow; transport (0.123456789 0.3 0.8);")
    _run(case,["blockMesh"])
    _run(case,["decomposePar","-force"])
    return case

def test_periodic_uniform_flow_accepts_conservative_assembly_roundoff(tmp_path):
    case=periodic_flow_case(tmp_path)
    reply=invoke(case)
    assert reply.returncode==0,reply.stdout
    assert "CONSERVATIVE_FLOW_RHS" in reply.stdout
    assert all(np.isfinite(x[1]) for x in _cells(case))
    assert max(abs(x[1]-5) for x in _cells(case))<1e-9

def test_periodic_flow_still_rejects_genuine_unbalanced_forcing(tmp_path):
    case=periodic_flow_case(tmp_path)
    with (case/"system/gaugeTestDict").open("a") as out:
        out.write("netSource 1e-6;")
    reply=invoke(case)
    assert reply.returncode!=0
    assert "Incompatible assembled RHS in free pressure component" in reply.stdout


def test_periodic_sheared_mesh_includes_actual_nonorth_correction(tmp_path):
    case=periodic_flow_case(tmp_path,shear=.2,nonconstant=True)
    reply=invoke(case)
    assert reply.returncode==0,reply.stdout
    scales=[float(x) for x in re.findall(r"NONORTH_CORRECTION_SCALE ([-+\deE.]+)",reply.stdout)]
    assert max(scales)>1e-3
    assert all(np.isfinite(x[1]) for x in _cells(case))
    assert _cells(case)[0][1]==pytest.approx(5,abs=1e-9)
