"""The gpuNewton solver engine (runner/gpu_newton.py): opt-in, offered by a --gpu launch, OpenFOAM by default."""
import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from backend.aircooling.runner import convergence, gpu_newton


REPO = Path(__file__).resolve().parents[3]


def _solver_checkout(root, *, python=True):
    """Another solver checkout (DCDTUNI_GPU_SOLVER_ROOT), as used for solver development."""
    entry = root / gpu_newton.CHECKOUT_ENTRY
    entry.parent.mkdir(parents=True)
    entry.write_text("print('solver')\n")
    return {"DCDTUNI_GPU_SOLVER_ROOT": str(root)}


def _manifest(**simulation):
    return {
        "schemaVersion": 4,
        "simulationConfig": {"solverType": "steady", "endTime": 800, **simulation},
        "geometryExport": {"solids": {}},
        "boundaryBindings": {
            "a": {"bcType": "acu_supply", "patchName": "acu_1_supply", "parameters": {}},
            "b": {"bcType": "acu_return", "patchName": "acu_1_return", "parameters": {}},
            "c": {"bcType": "fan_outlet", "patchName": "srv_1_out", "parameters": {"powerW": 4000.0}},
            "d": {"bcType": "fan_inlet", "patchName": "srv_1_in", "parameters": {}},
            "e": {"bcType": "wall", "patchName": "room", "parameters": {}},
        },
    }


def test_openfoam_is_the_default_engine():
    assert gpu_newton.resolve_engine(None) == "openfoam"
    assert gpu_newton.resolve_engine("") == "openfoam"
    assert gpu_newton.resolve_engine("gpuNewton") == "gpuNewton"
    with pytest.raises(ValueError, match="solver engine must be one of"):
        gpu_newton.resolve_engine("simpleFoam")


def test_the_vendored_solver_and_this_interpreter_are_the_default():
    config = gpu_newton.configuration({})
    assert config["vendored"] is True
    assert config["entry"] == REPO / "src" / "backend" / "aircooling" / "gpu_solver" / "entries" / "dc_foam_solve.py"
    assert config["entry"].is_file()
    assert config["python"] == Path(sys.executable)
    assert config["devices"] == [] and config["threads"] == 4 and config["standby"] == 600.0
    # nothing is written into the source tree: sockets and the service log go to the runtime area
    assert config["service_dir"] == REPO / ".codex-tmp" / "gpu-solver"


def test_the_vendored_copy_is_what_its_manifest_records():
    spec = importlib.util.spec_from_file_location("vendor_gpu_solver", REPO / "tools" / "vendor_gpu_solver.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.check() == 0                    # a hand edit of a vendored file fails here
    manifest = json.loads((gpu_newton.VENDORED / "VENDORED.json").read_text())
    assert manifest["source"]["modified"] is False and len(manifest["source"]["commit"]) == 40
    assert not any(name.startswith("NNmodels/") for name in manifest["files"])
    found = gpu_newton.identity({})
    assert found["vendored"] is True and found["commit"] == manifest["source"]["commit"]


def test_another_checkout_says_what_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="has no src/entries/dc_foam_solve.py"):
        gpu_newton.configuration({"DCDTUNI_GPU_SOLVER_ROOT": str(tmp_path)})
    env = _solver_checkout(tmp_path / "solver", python=False)
    # one environment per machine: another checkout runs with this interpreter too, even with a .venv of its own
    own = tmp_path / "solver" / ".venv" / "bin" / "python"
    own.parent.mkdir(parents=True)
    own.write_text("#!/bin/sh\n")
    own.chmod(0o755)
    assert gpu_newton.configuration(env)["python"] == Path(sys.executable)
    assert gpu_newton.configuration({**env, "DCDTUNI_GPU_SOLVER_PYTHON": str(own)})["python"] == Path(sys.executable)


def test_an_l0_mesh_is_solved_by_the_zonal_network(tmp_path):
    root = tmp_path / "solver"
    env = _solver_checkout(root)
    (root / "src" / gpu_newton.ZONAL_ENTRY).write_text("print('zonal')\n")
    run = tmp_path / "run"
    run.mkdir()
    l0 = {**_manifest(), "meshConfig": {"coarsenLevel": "L0"}}
    command = gpu_newton.prepare(run, l0, stop_tolerance_k=0.5, env=env)
    assert command.endswith(f"{sys.executable} -u {root / 'src' / gpu_newton.ZONAL_ENTRY} --run .")
    l1 = {**_manifest(), "meshConfig": {"coarsenLevel": "L1"}}
    assert gpu_newton.prepare(run, l1, stop_tolerance_k=0.5, env=env).endswith(
        f"{root / gpu_newton.CHECKOUT_ENTRY} --run .")
    assert gpu_newton.zonal_verdict(run) is None
    (run / "gpuNewton" / "zonal").mkdir(parents=True)
    (run / "gpuNewton" / "zonal" / "result.json").write_text(json.dumps(
        {"ok": True, "rule": "Newton", "rtol": 1e-10, "certificate": {"iterations": 34, "residual_rel_max": 0.3}}))
    v = gpu_newton.zonal_verdict(run)
    assert v["verdict"] == "CONVERGED" and v["certificate"]["iterations"] == 34


def test_configuration_reads_the_environment(tmp_path):
    env = {**_solver_checkout(tmp_path / "solver"), "DCDTUNI_GPU_SOLVER_GPU": "GPU-abc, 2",
           "DCDTUNI_GPU_SOLVER_THREADS": "6", "DCDTUNI_GPU_SOLVER_STANDBY": "0"}
    config = gpu_newton.configuration(env)
    assert config["vendored"] is False and config["service_dir"] is None
    assert config["entry"] == tmp_path / "solver" / gpu_newton.CHECKOUT_ENTRY
    assert config["devices"] == ["GPU-abc", "2"] and config["threads"] == 6 and config["standby"] == 0
    with pytest.raises(ValueError, match="DCDTUNI_GPU_SOLVER_THREADS"):
        gpu_newton.configuration({**env, "DCDTUNI_GPU_SOLVER_THREADS": "many"})
    with pytest.raises(ValueError, match="DCDTUNI_GPU_SOLVER_STANDBY"):
        gpu_newton.configuration({**env, "DCDTUNI_GPU_SOLVER_STANDBY": "-1"})


def test_only_a_gpu_launch_offers_the_engine():
    assert gpu_newton.launch_status({}) == {"enabled": False, "devices": None}
    # the device list alone does not enable it: the launcher's --gpu sets the flag
    assert gpu_newton.launch_status({"DCDTUNI_GPU_SOLVER_GPU": "GPU-a"}) == {"enabled": False, "devices": None}
    with pytest.raises(ValueError, match="start_server.sh --gpu"):
        gpu_newton.require_enabled({})
    assert gpu_newton.launch_status({"DCDTUNI_GPU_SOLVER": "1"}) == {"enabled": True, "devices": None}
    assert gpu_newton.launch_status({"DCDTUNI_GPU_SOLVER": "1", "DCDTUNI_GPU_SOLVER_GPU": "GPU-a,GPU-b"}) == {
        "enabled": True, "devices": ["GPU-a", "GPU-b"]}
    gpu_newton.require_enabled({"DCDTUNI_GPU_SOLVER": "1"})


def test_the_launcher_checks_the_packages_this_module_names():
    launcher = (REPO / "src" / "start_server.sh").read_text(encoding="utf-8")
    listed = ", ".join(f'"{name}"' for name in gpu_newton.REQUIRED_MODULES)
    assert f"for m in ({listed}) if u.find_spec(m) is None" in launcher
    assert "export DCDTUNI_GPU_SOLVER=1" in launcher and "unset DCDTUNI_GPU_SOLVER\n" in launcher


def test_the_request_carries_this_projects_stop_rule():
    request = gpu_newton.build_request(_manifest(), stop_tolerance_k=0.5)
    assert request["stopToleranceK"] == 0.5
    assert request["energyTolerance"] == convergence.DEFAULT_ENERGY_TOLERANCE
    assert request["window"] == convergence.MIN_WINDOW_SAMPLES
    assert request["declaredHeatW"] == 4000.0
    assert request["returnPatches"] == ["acu_1_return"] and request["supplyPatches"] == ["acu_1_supply"]
    assert request["maxIterations"] == 800
    tighter = gpu_newton.build_request(_manifest(energyBalanceTolerance=0.005), stop_tolerance_k=0.2)
    assert tighter["energyTolerance"] == 0.005 and tighter["stopToleranceK"] == 0.2


def test_an_incomplete_declared_heat_is_handed_over_as_none():
    manifest = _manifest()
    manifest["boundaryBindings"]["f"] = {"bcType": "fixed_temperature", "patchName": "hot", "parameters": {}}
    assert convergence.declared_air_heat(manifest)[1]
    assert gpu_newton.build_request(manifest, stop_tolerance_k=0.5)["declaredHeatW"] is None


def test_transient_and_returnless_cases_are_refused():
    with pytest.raises(ValueError, match="steady"):
        gpu_newton.build_request(_manifest(solverType="transient"), stop_tolerance_k=0.5)
    manifest = _manifest()
    del manifest["boundaryBindings"]["b"]
    with pytest.raises(ValueError, match="ACU return"):
        gpu_newton.build_request(manifest, stop_tolerance_k=0.5)


def test_prepare_writes_the_request_and_isolates_the_solver_environment(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    env = {**_solver_checkout(tmp_path / "solver"), "DCDTUNI_GPU_SOLVER_GPU": "GPU-abc"}
    command = gpu_newton.prepare(run, _manifest(), stop_tolerance_k=0.5, env=env)
    request = json.loads((run / gpu_newton.WORK_DIR / "request.json").read_text())
    assert request["format"] == "dcdtuni.gpu-newton-request/v1"
    assert command.startswith("env -u LD_LIBRARY_PATH -u PYTHONPATH -u PYTHONHOME -u LPCNS_DC_DEVICES "
                              "CUDA_VISIBLE_DEVICES=GPU-abc OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 "
                              "PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 ")
    assert command.endswith(f"{tmp_path / 'solver' / gpu_newton.CHECKOUT_ENTRY} --run .")
    assert "mpirun" not in command      # the runner relays its output through the pipe, not the MPI log


def test_several_devices_are_handed_over_as_the_solvers_choice(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    env = {**_solver_checkout(tmp_path / "solver"), "DCDTUNI_GPU_SOLVER_GPU": "GPU-a,GPU-b"}
    command = gpu_newton.prepare(run, _manifest(), stop_tolerance_k=0.5, env=env)
    assert " -u CUDA_VISIBLE_DEVICES LPCNS_DC_DEVICES=GPU-a,GPU-b " in command
    assert "CUDA_VISIBLE_DEVICES=" not in command


def test_the_vendored_solver_keeps_its_sockets_out_of_the_source_tree(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    command = gpu_newton.prepare(run, _manifest(), stop_tolerance_k=0.5, env={})
    assert f" LPCNS_DC_SERVICE_DIR={REPO / '.codex-tmp' / 'gpu-solver'} " in command
    assert command.endswith(f"{sys.executable} -u {gpu_newton.VENDORED / gpu_newton.ENTRY} --run .")


def test_without_a_configured_device_the_solver_chooses(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    command = gpu_newton.prepare(run, _manifest(), stop_tolerance_k=0.5, env=_solver_checkout(tmp_path / "solver"))
    assert "CUDA_VISIBLE_DEVICES=" not in command and " -u CUDA_VISIBLE_DEVICES " in command
    assert "LPCNS_DC_DEVICES=" not in command and " -u LPCNS_DC_DEVICES " in command


def test_the_request_names_the_mesh_identity_when_given(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    env = _solver_checkout(tmp_path / "solver")
    gpu_newton.prepare(run, _manifest(), stop_tolerance_k=0.5, mesh_sha256="ab" * 32, env=env)
    assert json.loads((run / gpu_newton.WORK_DIR / "request.json").read_text())["meshSha256"] == "ab" * 32
    assert "meshSha256" not in gpu_newton.build_request(_manifest(), stop_tolerance_k=0.5)


def test_prewarm_announces_the_mesh_without_waiting(tmp_path, monkeypatch):
    root = tmp_path / "solver"
    env = _solver_checkout(root, python=False)
    seen = tmp_path / "seen.txt"
    exe = root / "python"
    # stands in for the solver's interpreter (this process's): records its arguments
    exe.write_text(f"#!/bin/sh\necho \"$@\" > {seen}\n")
    exe.chmod(0o755)
    monkeypatch.setattr(gpu_newton.sys, "executable", str(exe))
    mesh = tmp_path / "Mesh"
    mesh.mkdir()
    assert gpu_newton.prewarm(mesh, "cd" * 32, env=env) is True
    for _ in range(100):
        if seen.exists() and seen.read_text().strip():
            break
        import time
        time.sleep(0.05)
    # no service standing by: one is started and leaves after the stand-by time
    assert seen.read_text().split() == ["-u", str(root / gpu_newton.CHECKOUT_ENTRY), "--prepare", str(mesh),
                                        "--key", "cd" * 32, "--start-service", "600"]
    assert gpu_newton.prewarm(mesh, "", env=env) is False          # no identity, nothing to announce
    seen.unlink()
    assert gpu_newton.prewarm(mesh, "cd" * 32, env={**env, "DCDTUNI_GPU_SOLVER_STANDBY": "0"}) is True
    for _ in range(100):
        if seen.exists() and seen.read_text().strip():
            break
        import time
        time.sleep(0.05)
    assert "--start-service" not in seen.read_text()
    with pytest.raises(FileNotFoundError):
        gpu_newton.prewarm(mesh, "cd" * 32, env={"DCDTUNI_GPU_SOLVER_ROOT": str(tmp_path / "absent")})


def _mesh(case, cells_per_rank):
    """A partitioned mesh with the cell note OpenFOAM writes in each ``owner`` header."""
    for rank, cells in enumerate(cells_per_rank):
        poly = case / f"processor{rank}" / "constant" / "polyMesh"
        poly.mkdir(parents=True)
        for name in ("points", "faces", "neighbour", "boundary"):
            (poly / name).write_text("x\n")
        (poly / "owner").write_text(f'FoamFile\n{{\n    note  "nPoints:9 nCells:{cells} nFaces:30 nInternalFaces:9";\n}}\n')
    return case


_SMI = ("0, GPU-a, NVIDIA GeForce RTX 4090, 24564, 20000, 97\n"
        "1, GPU-b, NVIDIA GeForce RTX 4090, 24564, 500, 3\n"
        "2, GPU-c, NVIDIA GeForce RTX 4090, 24564, 2, 0\n")


def _cards(monkeypatch, tmp_path, devices="GPU-a,1", text=_SMI):
    import subprocess

    def fake(cmd, **kwargs):
        assert cmd[0] == "nvidia-smi"
        return subprocess.CompletedProcess(cmd, 0, stdout=text, stderr="")
    monkeypatch.setattr(gpu_newton.subprocess, "run", fake)
    return {"DCDTUNI_GPU_SOLVER_GPU": devices} if devices else {}


def test_the_mesh_size_is_read_from_the_partition_headers(tmp_path):
    assert gpu_newton.mesh_cells(_mesh(tmp_path / "Mesh", [400_000, 350_000])) == 750_000
    assert gpu_newton.mesh_cells(tmp_path / "absent") is None
    (tmp_path / "Mesh" / "processor1" / "constant" / "polyMesh" / "owner").write_text("FoamFile\n{\n}\n")
    assert gpu_newton.mesh_cells(tmp_path / "Mesh") is None


def test_the_memory_estimate_is_the_solvers_model():
    # one source: the literal in the vendored entry (gpu_solver/entries/dc_foam_solve.py MEMORY_MODEL)
    source = (gpu_newton.VENDORED / gpu_newton.ENTRY).read_text(encoding="utf-8")
    model = gpu_newton.memory_model()
    assert set(model) == {"baseMiB", "perMillionMiB", "smallPerMillionMiB", "smallUpToCells"}
    assert f'OUT_OF_MEMORY = "{gpu_newton.OUT_OF_MEMORY}"' in source
    # the solver's own function gives the same numbers, on both sides of the size where the per-cell part changes
    namespace: dict = {"os": os, "MEMORY_MODEL": model}
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "memory_estimate_mib")
    exec(compile(ast.Module(body=[function], type_ignores=[]), "memory_estimate_mib", "exec"), namespace)
    for cells in (100_000, int(model["smallUpToCells"]), int(model["smallUpToCells"]) + 1, 1_000_000, 1_600_000):
        assert gpu_newton.estimate_memory_mib(cells) == namespace["memory_estimate_mib"](cells)
    assert gpu_newton.estimate_memory_mib(1_000_000) == 18_800
    assert gpu_newton.estimate_memory_mib(300_000) > 18_800 * 0.3


def test_the_cards_of_the_launch_are_read_with_what_the_solver_does_on_them(monkeypatch, tmp_path):
    env = _cards(monkeypatch, tmp_path)
    cards = gpu_newton.device_readings(env)
    assert [(c["index"], c["uuid"], c["totalMiB"], c["usedMiB"], c["freeMiB"], c["utilization"], c["solver"])
            for c in cards] == [("0", "GPU-a", 24564, 20000, 4564, 97, None), ("1", "GPU-b", 24564, 500, 24064, 3, None)]
    assert [c["index"] for c in gpu_newton.device_readings(_cards(monkeypatch, tmp_path, devices=""))] == ["0", "1", "2"]
    # a listening solver process stands by; one that holds its lock is in a solve
    import fcntl
    service = gpu_newton.configuration(env)["service_dir"]
    service.mkdir(parents=True, exist_ok=True)
    try:
        (service / "GPU-b.sock").write_text("")
        (service / "GPU-b.lock").write_text("")
        assert [c["solver"] for c in gpu_newton.device_readings(env)] == [None, "standby"]
        with open(service / "GPU-b.lock") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            assert [c["solver"] for c in gpu_newton.device_readings(env)] == [None, "solving"]
    finally:
        for name in ("GPU-b.sock", "GPU-b.lock"):
            (service / name).unlink(missing_ok=True)


def test_without_nvidia_smi_there_are_no_readings(monkeypatch):
    def absent(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(gpu_newton.subprocess, "run", absent)
    assert gpu_newton.device_readings({}) == []
    assert gpu_newton.memory_status(1_000_000, {})["fits"] is None


def test_the_memory_status_compares_the_mesh_with_the_freest_card(monkeypatch, tmp_path):
    env = _cards(monkeypatch, tmp_path)
    assert gpu_newton.memory_status(1_000_000, env) == {
        "cells": 1_000_000, "estimateMiB": 18_800, "bestFreeMiB": 24_064, "fits": True}
    short = gpu_newton.memory_status(1_000_000, _cards(monkeypatch, tmp_path, devices="GPU-a"))
    assert short["fits"] is False and short["bestFreeMiB"] == 4564
    text = gpu_newton.memory_message(short)
    assert "start_server.sh" in text and "--gpu" in text and "18.4 GiB" in text and "4.5 GiB" in text
    assert gpu_newton.memory_status(None, env)["fits"] is None


def test_an_out_of_memory_result_becomes_the_reason_the_user_reads(tmp_path):
    work = tmp_path / gpu_newton.WORK_DIR
    work.mkdir()
    assert gpu_newton.failure_reason(tmp_path) is None
    (work / "result.json").write_text(json.dumps({"ok": False, "error": "RuntimeError: x"}))
    assert gpu_newton.failure_reason(tmp_path) is None
    (work / "result.json").write_text(json.dumps({
        "ok": False, "errorCode": "gpu_out_of_memory", "cells": 1_100_000,
        "gpu": {"name": "RTX 4090", "totalMiB": 24081, "freeMiB": 9480, "estimateMiB": 20600}}))
    reason = gpu_newton.failure_reason(tmp_path)
    assert "显存不足" in reason and "start_server.sh" in reason and "--gpu" in reason
    assert "20.1 GiB" in reason and "9.3 GiB" in reason and "1.10 million cells" in reason


def test_the_runner_reports_the_out_of_memory_reason_instead_of_the_exit_status():
    from backend.aircooling.runner import stlgen_runner
    source = open(stlgen_runner.__file__, encoding="utf-8").read()
    assert "gpu_newton.failure_reason(run_dir)" in source and "raise gpu_newton.GpuMemoryError(reason) from exc" in source


def test_run_cfd_takes_the_engine_flag():
    from backend.aircooling.runner import run_cfd
    source = open(run_cfd.__file__, encoding="utf-8").read()
    assert '"--solver-engine"' in source and 'runner.solver_engine' in source
    assert os.path.basename(str(gpu_newton.ENTRY)) == "dc_foam_solve.py"
