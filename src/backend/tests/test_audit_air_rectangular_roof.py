"""Rectangular affine roof mathematics and optional two-rank checker diagnostic."""
import importlib.util,os,subprocess,sys
from pathlib import Path
import numpy as np
import pytest
path=Path(__file__).resolve().parents[3]/"tools/audit_air_rectangular_roof.py"
sys.path.insert(0,str(path.parent))
spec=importlib.util.spec_from_file_location("rect_roof",path)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
@pytest.fixture(scope="module")
def base():
    air=m.roof.load_air(path.with_name("air_transition_roof_probe.py"))
    return m.roof.evaluate_buffer(air,path.with_name("audit_air_mesh_geometry.py"),5e-4)
def test_affine_metric_identity_for_positive_anisotropic_dimensions(base):
    g,unit=base;widths=[.25,.255555556,.246875]
    for axis in range(3):
        for sign in [-1,1]:
            transformed,row=m.audit(g,widths,axis,sign)
            assert row["fullPureMetricGatePassed"]
            assert row["relativeTotalVolumeError"]<1e-13
            assert abs(row["minimumInternalWeight"]-unit["minimumInternalWeight"])<1e-12
            assert abs(row["minimumInternalVolumeRatio"]-unit["minimumInternalVolumeRatio"])<1e-12
def test_affine_internal_area_determinant_bound(base):
    g,unit=base
    for widths in [[1.1,1,1],[1,1.1,1],[1,1,1.1]]:
        _,row=m.audit(g,widths)
        assert row["fullPureMetricGatePassed"]
        bound=unit["minimumInternalAreaTensorDeterminant"]/1.1**6
        assert row["minimumInternalAreaTensorDeterminant"]>=bound
@pytest.mark.parametrize("widths",[(0,1,1),(float("nan"),1,1),(1e-8,1,1),(-1,1,1)])
def test_refuses_invalid_affine_dimensions(base,widths):
    with pytest.raises(ValueError):m.affine(base[0],widths)
@pytest.mark.skipif(os.environ.get("DCDTUNI_RECT_ROOF_CHECK")!="1",reason="isolated two-rank diagnostic not enabled")
def test_actual_liquid_rectangular_fixture_parallel_full_checker():
    case=Path(os.environ["DCDTUNI_RECT_ROOF_CASE"]).resolve()
    assert "/dcdtuni/" in str(case) and ".codex-tmp" in str(case)
    hashes={p.name:m.sha(p) for p in (case/"constant/polyMesh").iterdir() if p.is_file()}
    env=dict(os.environ,CUDA_VISIBLE_DEVICES="",OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1")
    result=subprocess.run(["decomposePar","-force","-no-fields","-case",str(case)],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
    (case/"log.decomposePar").write_text(result.stdout)
    assert result.returncode==0,result.stdout
    result=subprocess.run(["mpirun","-np","2","taskset","-c","24-27","checkMesh","-constant","-allTopology","-allGeometry","-parallel","-case",str(case)],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
    (case/"log.checkMesh").write_text(result.stdout)
    assert result.returncode==0,result.stdout
    assert "Mesh OK." in result.stdout and "Failed " not in result.stdout,result.stdout
    assert hashes=={p.name:m.sha(p) for p in (case/"constant/polyMesh").iterdir() if p.is_file()}
