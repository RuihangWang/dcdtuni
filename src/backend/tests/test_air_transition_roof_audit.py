"""Same-coordinate analytic regression plus opt-in >=2 MPI checker diagnostic."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import pytest
import numpy as np
path=Path(__file__).resolve().parents[3]/"tools/audit_air_transition_roof.py"
sys.path.insert(0,str(path.parent))
spec=importlib.util.spec_from_file_location("independent_roof",path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
LOCAL_AIR_TOOL=path.with_name("air_transition_roof_probe.py")
DEFAULT_AIR_TOOL=LOCAL_AIR_TOOL if LOCAL_AIR_TOOL.exists() else path.parents[1].parent/"air/tools/air_transition_roof_probe.py"
AIR_TOOL=Path(os.environ.get("DCDTUNI_ROOF_AIR_TOOL",DEFAULT_AIR_TOOL))
AUDIT=path.with_name("audit_air_mesh_geometry.py")
@pytest.fixture(scope="module")
def original_air():
    if not AIR_TOOL.exists():pytest.skip("Original exact air analytic fixture unavailable")
    return module.load_air(AIR_TOOL)
def test_exact_packet_global_assembly_and_strict_metrics(original_air):
    g,row=module.evaluate(original_air,AUDIT,5e-4)
    assert (len(g.points),g.n_cells,g.n_faces,len(g.neighbour))==(22,5,25,8)
    assert row["allOppositeEdgeCyclesClosed"] and row["allFacesPlanar"] and row["allFacesConvex"]
    assert row["allCellsStrictlyInsideFaceCentreCriterion"] and row["allNonincidentVerticesStrictInside"]
    assert abs(row["totalVolumeM3"]-1.5)<1e-12
    assert row["minimumTetFanQuality"]>0
    assert row["minimumInternalWeight"]>.05 and row["minimumInternalVolumeRatio"]>.01
def test_epsilon_needs_native_plane_margin_not_merely_convexity(original_air):
    _,flat=module.evaluate(original_air,AUDIT,0)
    _,small=module.evaluate(original_air,AUDIT,1e-6)
    _,enough=module.evaluate(original_air,AUDIT,1.001e-6)
    assert not flat["allCellsStrictlyInsideFaceCentreCriterion"]
    assert small["allNonincidentVerticesStrictInside"]
    assert not small["allCellsStrictlyInsideFaceCentreCriterion"]
    assert enough["allCellsStrictlyInsideFaceCentreCriterion"]
def test_upper_width_bound_prevents_topological_collapse(original_air):
    _,limit=module.evaluate(original_air,AUDIT,.5)
    _,beyond=module.evaluate(original_air,AUDIT,.51)
    assert not limit["allOppositeEdgeCyclesClosed"]
    assert not beyond["allNonincidentVerticesStrictInside"] and beyond["minimumTetFanQuality"]<0
@pytest.mark.skipif(os.environ.get("DCDTUNI_ROOF_CHECKER_DIAGNOSTIC")!="1",reason="isolated checker diagnostic not enabled")
def test_exact_fixture_two_rank_full_checker_diagnostic(original_air):
    case=Path(os.environ["DCDTUNI_ROOF_DIAG_CASE"]).resolve()
    assert "/dcdtuni/" in str(case) and ".codex-tmp" in str(case)
    before={p.name:module.sha(p) for p in (case/"constant/polyMesh").iterdir() if p.is_file()}
    env=dict(os.environ,OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",CUDA_VISIBLE_DEVICES="")
    completed=subprocess.run(["decomposePar","-force","-no-fields","-case",str(case)],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
    (case/"log.decomposePar").write_text(completed.stdout)
    assert completed.returncode==0,completed.stdout
    completed=subprocess.run(["mpirun","-np","2","taskset","-c","24-27","checkMesh","-constant","-allTopology","-allGeometry","-parallel","-case",str(case)],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
    (case/"log.checkMesh").write_text(completed.stdout)
    assert completed.returncode==0,completed.stdout
    assert "Mesh OK." in completed.stdout and "Failed " not in completed.stdout,completed.stdout
    after={p.name:module.sha(p) for p in (case/"constant/polyMesh").iterdir() if p.is_file()}
    assert after==before
    # This exact tiny fixture is diagnostic evidence, not case acceptance.

def test_buffer_preserves_central_packet_and_internal_directions(original_air):
    g,row=module.evaluate_buffer(original_air,AUDIT,5e-4)
    assert (len(g.points),g.n_cells,g.n_faces,len(g.neighbour))==(130,54,222,129)
    assert row["allOppositeEdgeCyclesClosed"] and row["allFacesPlanar"] and row["allFacesConvex"]
    assert row["allCellsStrictlyInsideFaceCentreCriterion"] and row["allNonincidentVerticesStrictInside"]
    assert abs(row["totalVolumeM3"]-22.5)<1e-12
    assert row["minimumInternalAreaTensorDeterminant"]>.001
    _,single=module.evaluate(original_air,AUDIT,5e-4)
    central=[row["cells"][20+i] for i in range(5)]
    assert [sorted(c["vertices"]) for c in central]==[sorted(c["vertices"]) for c in single["cells"]]
def test_single_packet_internal_area_tensor_has_small_determinant(original_air):
    epsilon=5e-4
    g,_=module.evaluate(original_air,AUDIT,epsilon)
    areas=np.array([g.face(int(f))[0] for f in g.cell_faces[g.cell_offsets[0]:g.cell_offsets[1]] if f<len(g.neighbour)])
    avg=np.mean(np.linalg.norm(areas,axis=1))
    tensor=sum(np.outer(a/avg,a/avg) for a in areas)
    determinant=abs(np.linalg.det(tensor))/8
    exact=8*epsilon**4/(1+2*epsilon**2)**3
    assert abs(determinant-exact)<1e-23
    assert determinant<.001
