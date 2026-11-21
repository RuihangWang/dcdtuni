"""Opt-in MPI2 operator regression: real production startup + Euler correction.

No product solver or server workflow is invoked. Build and cases remain under
the pytest basetemp, which must be inside this repository.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import numpy as np
import pytest

REPO=Path(__file__).resolve().parents[3]
SOURCE=REPO/"src/backend/aircooling/third_parties/foam/src/applications/solvers/buoyantBoussinesqPimpleFoam_new"
pytestmark=pytest.mark.skipif(os.environ.get("DCDTUNI_STARTUP_KERNEL_TEST")!="1",
                            reason="isolated OpenFOAM startup kernel not configured")


def _command(path,args):
    cp=subprocess.run(args,cwd=path,text=True,stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT,timeout=90)
    (path/("log."+Path(args[0]).name)).write_text(cp.stdout)
    assert cp.returncode==0,cp.stdout
    return cp.stdout


@pytest.fixture(scope="session")
def executable(tmp_path_factory):
    path=tmp_path_factory.mktemp("startup-kernel-build")
    assert path.resolve().is_relative_to(REPO.resolve())
    (path/"Make").mkdir()
    shutil.copy2(Path(__file__).with_name("pimple_startup_kernel")/"pimpleStartupKernel.C",path)
    source=(SOURCE/"createFields.H").read_text()
    start=source.index('Info<< "Reading field U')
    end=source.index('#include "createPhi.H"',start)
    (path/"startupVelocityRead.H").write_text(source[start:end])
    (path/"startup-source-identity.json").write_text(json.dumps({
        "source":str(SOURCE/"createFields.H"),
        "sha256":hashlib.sha256((SOURCE/"createFields.H").read_bytes()).hexdigest(),
        "actualStartupBlock":source[start:end]},indent=2)+"\n")
    (path/"Make/files").write_text("pimpleStartupKernel.C\nEXE = $(FOAM_USER_APPBIN)/pimpleStartupKernel\n")
    (path/"Make/options").write_text(
        "EXE_INC = -I$(LIB_SRC)/finiteVolume/lnInclude -I$(LIB_SRC)/meshTools/lnInclude\n"
        "EXE_LIBS = -lfiniteVolume -lmeshTools\n")
    env=os.environ.copy()
    env["FOAM_USER_APPBIN"]=str(path/"bin")
    (path/"bin").mkdir()
    cp=subprocess.run(["wmake"],cwd=path,env=env,text=True,stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT,timeout=90)
    (path/"log.wmake").write_text(cp.stdout)
    assert cp.returncode==0,cp.stdout
    return path/"bin/pimpleStartupKernel"


def _header(name,kind="dictionary"):
    return f"FoamFile {{ version 2.0; format ascii; class {kind}; object {name}; }}\n"


def _case(path,cache):
    (path/"system").mkdir(parents=True);(path/"constant").mkdir();(path/"0").mkdir()
    (path/"system/blockMeshDict").write_text(_header("blockMeshDict")+
        "convertToMeters 1; vertices ((0 0 0)(2 0 0)(2 1 0)(0 1 0)"
        "(0 0 1)(2 0 1)(2 1 1)(0 1 1));"
        "blocks (hex (0 1 2 3 4 5 6 7) (2 1 1) simpleGrading (1 1 1)); edges ();"
        "boundary ( inlet { type patch; faces ((0 4 7 3)); }"
        "outlet { type patch; faces ((1 2 6 5)); }"
        "walls { type wall; faces ((0 1 5 4)(3 7 6 2)(0 3 2 1)(4 5 6 7)); });"
        "mergePatchPairs ();")
    (path/"system/controlDict").write_text(_header("controlDict")+
        "application pimpleStartupKernel; startFrom startTime; startTime 0;"
        "stopAt endTime; endTime .01; deltaT .01; writeControl timeStep; writeInterval 1;"
        "writePrecision 17; runTimeModifiable false;")
    (path/"system/fvSchemes").write_text(_header("fvSchemes")+
        "ddtSchemes { default Euler; } gradSchemes { default Gauss linear; }"
        "divSchemes { default none; div(phi) Gauss linear; }"
        "laplacianSchemes { default Gauss linear corrected; }"
        "interpolationSchemes { default linear; } snGradSchemes { default corrected; }")
    (path/"system/fvSolution").write_text(_header("fvSolution")+
        "solvers { p { solver PCG; preconditioner DIC; tolerance 1e-12; relTol 0; maxIter 100; } }")
    (path/"system/decomposeParDict").write_text(_header("decomposeParDict")+
        "numberOfSubdomains 2; method simple; simpleCoeffs { n (2 1 1); delta .001; }")
    (path/"0/U").write_text(_header("U","volVectorField")+
        "dimensions [0 1 -1 0 0 0 0]; internalField nonuniform List<vector> 2 ((2 0 0)(.5 0 0));"
        "boundaryField { inlet { type fixedValue; value uniform (1 0 0); }"
        "outlet { type fixedValue; value uniform (1 0 0); }"
        "walls { type fixedValue; value uniform (0 0 0); } }")
    (path/"0/p").write_text(_header("p","volScalarField")+
        "dimensions [0 2 -2 0 0 0 0]; internalField uniform 0;"
        "boundaryField { inlet { type zeroGradient; }"
        "outlet { type fixedValue; value uniform 0; } walls { type zeroGradient; } }")
    _command(path,["blockMesh"]);_command(path,["decomposePar","-force"])
    for proc in path.glob("processor*"):
        p=proc/"0/U"
        text=p.read_text()
        # Values produced by decomposePar need not represent a genuine saved
        # processor neighbour cache. Exercise missing and deliberately stale
        # input exactly as the product's internal-field-only seed can produce.
        pattern=r"(\bprocBoundary\w+\s*\{)[^{}]*\}"
        replacement=(r"\1 type processor; }" if cache=="missing" else
                     r"\1 type processor; value uniform (0 0 0); }")
        text,n=re.subn(pattern,replacement,text)
        assert n==1
        p.write_text(text)
    return path


def _rows(path):
    rows=[]
    for proc in sorted(path.glob("processor*")):
        for line in (proc/"startup-kernel-faces.csv").read_text().splitlines():
            p=line.split()
            rows.append({"rank":int(proc.name.removeprefix("processor")),"patch":p[0],
                         "coupled":bool(int(p[1])),"face":int(p[2]),
                         "cache":list(map(float,p[3:6])),"own":list(map(float,p[6:9])),
                         "sf":list(map(float,p[9:12])),"initial":float(p[12]),
                         "ddt":float(p[13]),"predictor":float(p[14]),"corrected":float(p[15])})
    return rows


@pytest.mark.parametrize("cache",["missing","stale"])
def test_production_startup_initializes_remote_old_velocity_before_euler(tmp_path,executable,cache):
    case=_case(tmp_path/cache,cache)
    stdout=_command(case,["mpirun","-np","2",str(executable),"-parallel"])
    assert re.search(r"^End$",stdout,re.M)
    rows=_rows(case);coupled=[row for row in rows if row["coupled"]]
    assert len(coupled)==2
    physical=[row for row in rows if not row["coupled"]]
    cells=[float(line.split()[1]) for p in case.glob("processor*/startup-kernel-cells.csv")
           for line in p.read_text().splitlines()]
    audit={"format":"pimple-startup-coupled-kernel/v1","cache":cache,"ranks":2,
           "rows":rows,"cellNetVolumeFluxM3s":cells,
           "coupledDdtSum":sum(row["ddt"] for row in coupled),
           "coupledPredictorSum":sum(row["predictor"] for row in coupled),
           "coupledCorrectedSum":sum(row["corrected"] for row in coupled),
           "physicalBoundaryNetM3s":sum(row["corrected"] for row in physical)}
    (case/"audit.json").write_text(json.dumps(audit,indent=2)+"\n")
    # A local pressure solve can force both cell balances to zero even with
    # globally nonconservative processor input; check BOTH levels explicitly.
    assert max(map(abs,cells))<1e-11
    assert coupled[0]["cache"]==pytest.approx(coupled[1]["own"],abs=1e-14)
    assert coupled[1]["cache"]==pytest.approx(coupled[0]["own"],abs=1e-14)
    assert audit["coupledDdtSum"]==pytest.approx(0,abs=1e-12)
    assert audit["coupledPredictorSum"]==pytest.approx(0,abs=1e-13)
    assert audit["coupledCorrectedSum"]==pytest.approx(0,abs=1e-12)
    assert audit["physicalBoundaryNetM3s"]==pytest.approx(0,abs=1e-12)
    for row in physical:
        expected=[1,0,0] if row["patch"] in ("inlet","outlet") else [0,0,0]
        assert row["cache"]==expected
