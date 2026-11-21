# GPU air solver (vendored)

The GPU Newton-Krylov solver of the steady buoyant air case: a collocated finite-volume discretisation (k-epsilon,
Boussinesq buoyancy, wall functions, rack and ACU boundary models) whose residual and Jacobian-vector products run
as fused Warp kernels, solved by pseudo-transient Newton-Krylov with a block algebraic multigrid preconditioner.
It is differentiable: the residual is an explicit function of the state and of the boundary data.

`backend.aircooling.runner.gpu_newton` runs it as the solver engine `gpuNewton` (see that module for the
environment variables and `docs/reference/scripted-api.md` for the request). A server offers it only when started
with `start_server.sh --gpu`.

- `entries/dc_foam_solve.py` is the entry: it reads a prepared OpenFOAM-layout run directory (partitioned mesh,
  boundary conditions, `gpuNewton/request.json` with this project's stop rule), solves, and writes the monitors and
  the final time directory that the OpenFOAM solver would write. Its module text documents the solver service.
- `entries/solve_with_coupled_newton.py` is the solver's general driver; the recipe the entry passes to it is
  `RECIPE_ENV` / `RECIPE_ARGS` in `dc_foam_solve.py`.
- The packages keep their own top-level names (`FVdomain`, `FVsolver`, `Pipeline`, `SparseSolver`, ...): the
  solver runs in a process of its own with this directory first on `sys.path`, never imported into `backend`.

**Do not edit these files here.** They are copied verbatim from the solver's development repository
(`VENDORED.json`: source commit and the SHA-256 of every file). Change the solver there and refresh the copy:

    python tools/vendor_gpu_solver.py --from <solver checkout>
    python tools/vendor_gpu_solver.py --check          # the copy still equals VENDORED.json

Python packages beyond the core install: the `gpu-solver` extra of `pyproject.toml` (`setup_env.sh` installs it).
