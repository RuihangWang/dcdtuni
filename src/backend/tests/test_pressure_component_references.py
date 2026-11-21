"""MPI pressure-kernel regression tests (opt-in: DCDTUNI_PRESSURE_KERNEL).

These numerical tests run only through pytest, decomposed on >=2 MPI ranks.
OpenFOAM v2412 rejects one-rank parallel mode (raw trial preserved).
The old implementation can be built with DCDTUNI_LEGACY_PRESSURE_REFERENCE
to retain the failing baseline receipt.
"""
import os
from pathlib import Path
import re
import subprocess

import pytest

KERNEL = os.environ.get("DCDTUNI_PRESSURE_KERNEL")
pytestmark = pytest.mark.skipif(not KERNEL, reason="isolated OpenFOAM kernel not configured")


def _header(name, kind="dictionary"):
    return f"FoamFile {{ version 2.0; format ascii; class {kind}; object {name}; }}\n"


def _run(case, args):
    completed = subprocess.run(args, cwd=case, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=90)
    (case / ("log." + Path(args[0]).name)).write_text(completed.stdout)
    assert completed.returncode == 0, completed.stdout
    return completed.stdout


def _case(path, *, layout="disconnected", fraction=None, gravity=0, initial=37,
          ranks=2, cyclic=False, local_nx=1, ami=False, toggle=None, refpoint=False):
    (path / "system").mkdir(parents=True)
    (path / "constant").mkdir()
    (path / "0").mkdir()
    if layout == "connected":
        blocks = [(0, 2, 2)]
    else:
        blocks = [(0, 1, local_nx), (2, 3, local_nx)]
    vertices, hexes, all_faces, lefts, rights = [], [], [], [], []
    for lo, hi, nx in blocks:
        n = len(vertices)
        vertices += [(lo,0,0),(hi,0,0),(hi,1,0),(lo,1,0),
                     (lo,0,1),(hi,0,1),(hi,1,1),(lo,1,1)]
        hexes.append("hex (" + " ".join(str(n+i) for i in range(8))
                     + f") ({nx} 1 1) simpleGrading (1 1 1)")
        lefts.append([n+i for i in (0,4,7,3)])
        rights.append([n+i for i in (1,2,6,5)])
        all_faces += [[n+i for i in row] for row in
                      ((0,1,5,4),(3,7,6,2),(0,3,2,1),(4,5,6,7))]
    patch_specs = []
    def patch(name, kind, faces, extra=""):
        fs = " ".join("("+" ".join(map(str,f))+")" for f in faces)
        patch_specs.append(f"{name} {{ type {kind}; {extra} faces ({fs}); }}")
    if cyclic:
        # Connect block 0's right to block 1's left (a translated ordinary cyclic).
        patch("cycA", "cyclicAMI" if ami else "cyclic", [rights[0]], "neighbourPatch cycB; transform translational; separationVector (1 0 0);")
        patch("cycB", "cyclicAMI" if ami else "cyclic", [lefts[1]], "neighbourPatch cycA; transform translational; separationVector (-1 0 0);")
        patch("anchor", "patch", [lefts[0]])
        patch("far", "patch", [rights[1]])
    elif layout == "connected":
        patch("walls", "wall", all_faces + lefts + rights)
        all_faces = []
    else:
        patch("anchor", "patch", [lefts[0]])
        patch("openWalls", "wall", [rights[0]])
        patch("closedWalls", "wall", [lefts[1],rights[1]])
    if all_faces:
        patch("walls", "wall", all_faces)
    vertices_text = " ".join(f"({x} {y} {z})" for x,y,z in vertices)
    (path/"system/blockMeshDict").write_text(_header("blockMeshDict")
        + f"convertToMeters 1; vertices ({vertices_text}); blocks ({' '.join(hexes)});"
        + f"edges (); boundary ({' '.join(patch_specs)}); mergePatchPairs ();")
    (path/"system/controlDict").write_text(_header("controlDict") +
        "application pressureComponentKernel; startFrom startTime; startTime 0; "
        "stopAt endTime; endTime 1; deltaT 1; writeControl timeStep; "
        "writeInterval 1; runTimeModifiable false;")
    (path/"system/fvSchemes").write_text(_header("fvSchemes") +
        "ddtSchemes { default steadyState; } gradSchemes { default Gauss linear; } "
        "divSchemes { default none; } laplacianSchemes { default Gauss linear corrected; } "
        "interpolationSchemes { default linear; } snGradSchemes { default corrected; }")
    (path/"system/fvSolution").write_text(_header("fvSolution") +
        "solvers { p_rgh { solver PCG; preconditioner DIC; tolerance 1e-12; "
        "relTol 0; maxIter 100; } }")
    (path/"system/decomposeParDict").write_text(_header("decomposeParDict") +
        f"numberOfSubdomains {ranks}; method simple; "
        f"simpleCoeffs {{ n ({ranks} 1 1); delta 0.001; }}")
    (path/"system/gaugeTestDict").write_text(_header("gaugeTestDict") +
        ( "pRefPoint (2.5 0.5 0.5);" if refpoint else "pRefCell 0;" )
        + f"pRefValue 5; gravity {gravity};"
        + (f"toggleFraction {toggle};" if toggle is not None else ""))
    boundary = []
    for spec in patch_specs:
        name = spec.split()[0]
        if name.startswith("cyc"):
            bc = "type cyclicAMI;" if ami else "type cyclic;"
        elif name == "anchor":
            bc = "type fixedValue; value uniform 0;"
        elif name == "closedWalls" and fraction is not None:
            bc = (f"type mixed; refValue uniform 11; refGradient uniform 0; "
                  f"valueFraction uniform {fraction}; value uniform {initial};")
        else:
            bc = f"type zeroGradient; value uniform {initial};"
        boundary.append(f"{name} {{ {bc} }}")
    (path/"0/p_rgh").write_text(_header("p_rgh", "volScalarField") +
        f"dimensions [0 2 -2 0 0 0 0]; internalField uniform {initial}; "
        + "boundaryField {" + " ".join(boundary) + "}")
    _run(path, ["blockMesh"])
    _run(path, ["decomposePar", "-force"])
    return path


def _solve(path, ranks):
    # Native OF v2412/MPICH env is inherited from the isolated pytest invocation.
    return _run(path, ["mpirun", "-np", str(ranks), KERNEL, "-parallel"])


def _rows(case):
    rows = []
    for path in case.glob("processor*/gauge-kernel-result.csv"):
        rows.extend(tuple(map(float, line.split())) for line in path.read_text().splitlines())
    return sorted(rows)


def _cells(case):
    return [(tuple(row[:3]),row[3],row[4]) for row in _rows(case)]


@pytest.mark.parametrize("ranks", [2])
@pytest.mark.parametrize("initial", [37, 73])
def test_closed_component_anchored_without_moving_dirichlet(tmp_path, ranks, initial):
    case = _case(tmp_path/"case", ranks=ranks, initial=initial)
    _solve(case, ranks)
    cells = _cells(case)
    assert len(cells) == 2
    assert cells[0][1:] == pytest.approx((0, 0), abs=1e-9)
    assert cells[1][1:] == pytest.approx((5, 5), abs=1e-9)


def test_two_cells_split_across_two_ranks_has_real_constraint(tmp_path):
    case = _case(tmp_path/"case", layout="connected", ranks=2)
    _solve(case, 2)
    rows = _rows(case)
    assert len(rows) == 2
    assert all(row[5] == 0 and abs(row[6]) > 0 for row in rows)
    assert sum(abs(row[7]) > 0 for row in rows) == 1
    assert all(row[3] == pytest.approx(5, abs=1e-9) for row in rows)


def test_multiple_local_cells_per_component(tmp_path):
    case = _case(tmp_path/"case", ranks=2, local_nx=2)
    _solve(case, 2)
    rows = _rows(case)
    assert len(rows) == 4
    assert all(abs(row[5]) > 0 for row in rows)
    assert all(row[3] == pytest.approx(0 if row[0] < 1 else 5, abs=1e-9)
               for row in rows)


@pytest.mark.parametrize("ranks", [2])
def test_ordinary_cyclic_connects_pressure_graph_and_retains_anchor(tmp_path, ranks):
    case = _case(tmp_path/"case", cyclic=True, ranks=ranks)
    _solve(case, ranks)
    assert all(prgh == pytest.approx(0, abs=1e-9) for _,prgh,_ in _cells(case))
    assert all(row[7] == 0 for row in _rows(case))


@pytest.mark.parametrize("fraction,expected", [(0,5), (1,11), (0.25,11)])
def test_mixed_boundary_uses_actual_matrix_anchor(tmp_path, fraction, expected):
    case = _case(tmp_path/"case", fraction=fraction)
    _solve(case, 2)
    cells = _cells(case)
    assert cells[0][1] == pytest.approx(0, abs=1e-9)
    assert cells[1][1] == pytest.approx(expected, abs=1e-9)


def test_physical_pressure_reference_preserves_hydrostatic_relation(tmp_path):
    gravity = -9.81
    case = _case(tmp_path/"case", gravity=gravity)
    _solve(case, 2)
    cells = _cells(case)
    assert cells[0][1] == pytest.approx(0, abs=1e-9)
    assert cells[0][2] == pytest.approx(-4.905, abs=1e-9)
    assert cells[1][2] == pytest.approx(5, abs=1e-9)
    assert cells[1][1] == pytest.approx(9.905, abs=1e-9)


@pytest.mark.parametrize("initial_fraction,new_fraction,expected", [(1,0,5), (0,1,11)])
def test_dynamic_mixed_boundary_rechecks_matrix_every_assembly(
        tmp_path, initial_fraction, new_fraction, expected):
    case = _case(tmp_path/"case", fraction=initial_fraction, toggle=new_fraction)
    _solve(case, 2)
    assert _cells(case)[1][1] == pytest.approx(expected, abs=1e-9)


def test_user_reference_point_can_belong_to_nonmaster_rank(tmp_path):
    case = _case(tmp_path/"case", refpoint=True)
    _solve(case, 2)
    assert _cells(case)[0][1] == pytest.approx(0, abs=1e-9)
    assert _cells(case)[1][1] == pytest.approx(5, abs=1e-9)


def test_pressure_coupled_ami_fails_closed_with_clear_scope(tmp_path):
    case = _case(tmp_path/"case", cyclic=True, ami=True)
    completed = subprocess.run(["mpirun", "-np", "2", KERNEL, "-parallel"],
        cwd=case, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
    (case/"log.mpirun").write_text(completed.stdout)
    assert completed.returncode != 0
    assert "Pressure component references support ordinary cyclic" in completed.stdout
    assert "AMI/ACMI" in completed.stdout


@pytest.mark.parametrize("cyclic", [False, True])
@pytest.mark.parametrize("mode", ["steady", "transient"])
def test_actual_new_solver_uses_component_gauges(tmp_path, cyclic, mode):
    """A tiny unit/regression case, not a production air workflow invocation."""
    solver = os.environ.get("DCDTUNI_PIMPLE_SOLVER" if mode=="transient" else "DCDTUNI_PRESSURE_SOLVER")
    if not solver:
        pytest.skip("isolated _new executable not configured")
    case = _case(tmp_path/"case", cyclic=cyclic, gravity=-9.81, initial=0)
    initial_prgh = (case/"0/p_rgh").read_text().replace("type zeroGradient;", "type fixedFluxPressure;")
    (case/"0/p_rgh").write_text(initial_prgh)
    names = re.findall(r"(\w+)\s*\{\s*type", initial_prgh)
    for name, kind, dimensions, value in [
        ("U","volVectorField","[0 1 -1 0 0 0 0]","(0 0 0)"),
        ("T","volScalarField","[0 0 0 1 0 0 0]","293.15"),
        ("alphat","volScalarField","[0 2 -1 0 0 0 0]","0")]:
        bcs=[]
        for patch in names:
            bc = "type cyclic;" if patch.startswith("cyc") else (
                "type noSlip;" if name == "U" else "type fixedValue; value uniform 293.15;" if name=="T"
                else "type calculated; value uniform 0;")
            bcs.append(f"{patch} {{ {bc} }}")
        (case/"0"/name).write_text(_header(name,kind)
            + f"dimensions {dimensions}; internalField uniform {value}; boundaryField {{ "
            + " ".join(bcs) + "}")
    (case/"constant/turbulenceProperties").write_text(_header("turbulenceProperties")
        + "simulationType laminar;")
    (case/"constant/transportProperties").write_text(_header("transportProperties")
        + "transportModel Newtonian; nu [0 2 -1 0 0 0 0] 1e-5;"
        + "beta [0 0 0 -1 0 0 0] 0.003; TRef [0 0 0 1 0 0 0] 293.15;"
        + "Pr [0 0 0 0 0 0 0] 0.71; Prt [0 0 0 0 0 0 0] 0.85;")
    (case/"constant/g").write_text(_header("g","uniformDimensionedVectorField")
        + "dimensions [0 1 -2 0 0 0 0]; value (0 -9.81 0);")
    (case/"constant/radiationProperties").write_text(_header("radiationProperties")
        + "radiationModel none;")
    (case/"system/controlDict").write_text(_header("controlDict")
        + "application buoyantBoussinesqSimpleFoam_new; startFrom startTime; startTime 0;"
        + "stopAt endTime; endTime 4; deltaT 1; writeControl timeStep; writeInterval 1;"
        + "writePrecision 15; runTimeModifiable false;"
        + "explicitBoundaryValues { mode preserveType; }")
    (case/"system/fvSchemes").write_text(_header("fvSchemes")
        + "ddtSchemes { default steadyState; } gradSchemes { default Gauss linear; }"
        + "divSchemes { default none; div(phi,U) Gauss upwind; div(phi,T) Gauss upwind;"
        + "div((nuEff*dev2(T(grad(U))))) Gauss linear; }"
        + "laplacianSchemes { default Gauss linear corrected; }"
        + "interpolationSchemes { default linear; } snGradSchemes { default corrected; }")
    (case/"system/fvSolution").write_text(_header("fvSolution")
        + 'solvers { p_rgh { solver PCG; preconditioner DIC; tolerance 1e-12; relTol 0; }'
        + ' "(U|T)" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-12; relTol 0; } }'
        + "SIMPLE { nNonOrthogonalCorrectors 0; pRefCell 0; pRefValue 5;"
        + "residualControl { p_rgh 1e-9; U 1e-9; T 1e-9; } }")
    if mode == "transient":
        control=(case/"system/controlDict").read_text()
        control=control.replace("buoyantBoussinesqSimpleFoam_new","buoyantBoussinesqPimpleFoam_new")
        control=control.replace("endTime 4;", "endTime 0.02;").replace("deltaT 1;", "deltaT 0.01;")
        (case/"system/controlDict").write_text(control)
        (case/"system/fvSchemes").write_text((case/"system/fvSchemes").read_text()
                                            .replace("steadyState","Euler"))
        solution=(case/"system/fvSolution").read_text().replace(
            "SIMPLE { nNonOrthogonalCorrectors 0;",
            "PIMPLE { nOuterCorrectors 1; nCorrectors 2; nNonOrthogonalCorrectors 0;")
        solution=solution.replace(
            ' "(U|T)" {', ' p_rghFinal { $p_rgh; relTol 0; } "(U|T|UFinal|TFinal)" {')
        solution=solution.replace(
            "residualControl { p_rgh 1e-9; U 1e-9; T 1e-9; }",
            "residualControl { p_rgh { tolerance 1e-9; relTol 0; } "
            "U { tolerance 1e-9; relTol 0; } T { tolerance 1e-9; relTol 0; } }")
        (case/"system/fvSolution").write_text(solution)
    # Re-decompose after all physical fields/settings are written.
    _run(case, ["decomposePar","-force"])
    stdout = _run(case, ["mpirun","-np","2",solver,"-parallel"])
    # This four-step operator regression does not claim a physical CFD convergence pass.
    # Near-zero equilibrium fluxes can retain finite normalized residuals at roundoff.
    assert re.search(r"^End$", stdout, re.M)
    if mode == "steady":
        assert re.search(r"^SIMPLE solution converged in \d+ iterations$", stdout, re.M)
    assert "FOAM FATAL" not in stdout
    if Path(solver).name.endswith("_new"):
        assert f"Pressure components: {1 if cyclic else 2}" in stdout
        assert f"gauge-referenced: {0 if cyclic else 1}" in stdout
    for proc in sorted(case.glob("processor*")):
        times=sorted((q for q in proc.iterdir() if q.is_dir() and
                      re.fullmatch(r"\d+(?:\.\d+)?",q.name) and q.name!="0"),key=lambda q:float(q.name))
        assert times
        text=(times[-1]/"p_rgh").read_text()
        pressure=float(re.search(r"internalField\s+uniform\s+([-+\deE.]+)",text)[1])
        expected=0 if proc.name=="processor0" or cyclic else 9.905
        assert pressure == pytest.approx(expected,abs=1e-9)
        physical=(times[-1]/"p").read_text()
        physical_value=float(re.search(r"internalField\s+uniform\s+([-+\deE.]+)",physical)[1])
        assert physical_value == pytest.approx(-4.905 if expected==0 else 5,abs=1e-9)
        if Path(solver).name.endswith("_new"):
            velocity=(times[-1]/"U").read_text()
            assert re.search(r"type\s+noSlip;\s+value\s+nonuniform",velocity)
            temperature=(times[-1]/"T").read_text()
            assert re.search(r"type\s+fixedValue;\s+value\s+nonuniform",temperature)


def test_transient_dynamic_mesh_declaration_is_explicitly_rejected(tmp_path):
    solver=os.environ.get("DCDTUNI_PIMPLE_SOLVER")
    if not solver or not Path(solver).name.endswith("_new"):
        pytest.skip("repo-owned static PIMPLE build not configured")
    case=_case(tmp_path/"case")
    declaration=_header("dynamicMeshDict")+"dynamicFvMesh dynamicMotionSolverFvMesh;"
    (case/"constant/dynamicMeshDict").write_text(declaration)
    for processor in case.glob("processor*"):
        (processor/"constant/dynamicMeshDict").write_text(declaration)
    completed=subprocess.run(["mpirun","-np","2",solver,"-parallel"],cwd=case,
        text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
    (case/"log.mpirun").write_text(completed.stdout)
    assert completed.returncode != 0
    assert "supports static meshes only" in completed.stdout
