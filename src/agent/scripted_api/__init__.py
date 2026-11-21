"""Scripted, headless API for external Python callers (for example the ISPO repository).

Air (through a private server, the product path)::

    from agent.scripted_api import run_air_case
    result = run_air_case("case/SingleRack/SingleRack.cfdproj",
                          run_settings={"simulationConfig": {"parallelSolve": True, "solveProcess": 8}},
                          max_processes=8)
    result["status"], result["metrics"]["fields"]["rackInlet"]["summary"]

Liquid (in-process DAE: steady state with implicit-adjoint gradients, or the product transient march)::

    from agent.scripted_api import run_liquid_case, solve_liquid_steady
    result = run_liquid_case("case/Shinaide_Liquid/Shinaide_liquid.cfdproj", fd_check=3)
    result["metrics"]["objectives"], result["metrics"]["gradients"]["max_chip_temperature_K"]["heat_W"]
    result["metrics"]["metrics"]["edges"]          # every edge: signed flow, direction, velocity, outlet T
    run_liquid_case("case/X/X.cfdproj", mode="transient", duration_s=20,
                    inputs={"pump_rpm_ratios": {"m_1_pump": 0.8}})   # queued through DAESimulation.set_inputs

Hybrid air/liquid (liquid session, mesh, solve with its heat -- one private server)::

    from agent.scripted_api import run_hybrid_case
    result = run_hybrid_case("case/Shinaide_Liquid/Shinaide_liquid.cfdproj", max_processes=8,
                             run_settings={"meshConfig": {"meshProcess": 4},
                                           "simulationConfig": {"parallelSolve": True, "solveProcess": 4}})
    result["metrics"]["heatLedger"], result["metrics"]["liquid"]["edges"]

Headless modelling (the automation contract's document verbs, in JSDOM)::

    from agent.scripted_api import build_project
    build_project([{"verb": "createDefaultRackModel"},
                   {"verb": "placeModel", "args": ["$0", {"location": {"x": 1, "y": 0, "z": 1}}]}],
                  out="_tmp/Room/Room.cfdproj")

Air stages and batches::

    run_air_case("case/X/X.cfdproj", stages=("mesh",))            # mesh only
    run_air_case("case/X/X.cfdproj", stages=("solve",),            # reuse the accepted mesh
                 run_settings={"simulationConfig": {"parallelSolve": True, "solveProcess": 4, "endTime": 200}})
    run_batch([{"cfdproj": "case/A/A.cfdproj"}, {"cfdproj": "case/B/B.cfdproj"}],
              out_root="_tmp/batches", max_parallel=2, max_processes=8)

Every ``run_*`` call writes a new dated directory and returns the structured
``result.json`` (format ``dcdtuni.api.result/v1``). The CLI is
``python -m agent.scripted_api {air,hybrid,liquid,build,export-air,export-liquid,batch,identity}``.
Reference: ``docs/reference/scripted-api.md``.
"""
from agent.scripted_api.air import export_air_case, run_air_case, run_hybrid_case
from agent.scripted_api.batch import run_batch
from agent.scripted_api.build import build_project
from agent.scripted_api.liquid import (DEFAULT_GRADIENT_PARAMETERS, OBJECTIVES, export_liquidrun,
                                run_liquid_case, solve_liquid_steady, solve_liquid_transient)

__all__ = ["run_air_case", "run_hybrid_case", "export_air_case", "run_batch", "build_project", "run_liquid_case",
           "solve_liquid_steady", "solve_liquid_transient", "export_liquidrun",
           "OBJECTIVES", "DEFAULT_GRADIENT_PARAMETERS"]
