from pathlib import Path
import os,subprocess,json,time
import pytest
P=Path(__file__).resolve().parent
D=P.parents[1]/"src/backend/aircooling/third_parties/dcCartMesh/src/mesh/dcCartMesh/dcCartCutCells"
CASES="""five_origins same_origin_not_duplicate_input duplicate_origin missing_facet facet_order unknown_patch wrong_zone wrong_rank wrong_leaf wrong_piece wrong_level stale_raw stale_roles stale_material stale_producer stale_boundary unknown_material incomplete alternate_path nonlocal_valid bad_shape nonfinite budget budget_during_origins origin_cap lineage_full_union lineage_deep_copy lineage_unknown lineage_repeat lineage_cell_cap lineage_origin_cap lineage_sparse_cap lineage_budget lineage_mapping lineage_cavity_cap lineage_disabled""".split()
@pytest.fixture(scope="session")
def exe():
    env=dict(os.environ,CUDA_VISIBLE_DEVICES="",OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",TMPDIR=str(P/"tmp"))
    (P/"tmp").mkdir(exist_ok=True)
    cmd=["taskset","-c","8-15","g++","-std=c++17","-O2","-Wall","-Wextra","-Werror","-I"+str(D),"-I"+str(P),str(P/"core_harness.cpp"),"-o",str(P/"core_harness")]
    start=time.monotonic();r=subprocess.run(cmd,capture_output=True,text=True,env=env)
    (P/"core-compile.log").write_text(r.stdout+r.stderr)
    (P/"core-compile.json").write_text(json.dumps(dict(command=cmd,exitCode=r.returncode,elapsed=time.monotonic()-start),indent=2)+"\n")
    assert r.returncode==0,r.stderr
    return P/"core_harness",env
@pytest.mark.parametrize("case",CASES)
def test_real_standard_core(exe,case):
    path,env=exe;r=subprocess.run(["taskset","-c","8-15",str(path),case],capture_output=True,text=True,env=env,timeout=10)
    assert r.returncode==0,r.stdout+r.stderr
