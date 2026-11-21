# macOS (Apple Silicon) Build & Smoke-Test Notes

> **Status (as of 2026-10-21).** These notes record one successful macOS bring-up and are kept as a guide; Linux remains the supported production target ([Installation](../user-guide/installation.md)). Two things have changed since they were written:
>
> - The §5b blocker is gone on every platform: `writeBCFields.H` (`third_parties/foam/src/lib/explicitBoundaryWrite/`) has been tracked since `b90282826` (2026-09-10), so `buoyantBoussinesqSimpleFoam_new`, `buoyantBoussinesqPimpleFoam_new` and `writeExplicitBC` build from a fresh clone. The macOS build of them has not been re-run since.
> - The two helper scripts the notes use, `tools/foam_env.sh` and `tools/build_third_parties.sh` (§3, §5, §9), were written on that Mac and never committed to this repository. Recreate them from the descriptions below, or use the equivalent manual commands.
>
> The default mesh engine is now dcCart (§5a builds it), and the default solvers are the repository `*_new` builds. Next step: repeat the bring-up on a Mac with the current tree, build the `*_new` solvers there, and commit the two helper scripts if they are still needed.

This document records how to stand up **dcdtuni** on macOS arm64 using the
native Homebrew OpenFOAM v2412 instead of the conda-forge flow that
`setup_env.sh` and the `third_parties/*/deploy.sh` scripts assume.

It was produced and verified end-to-end on:

| Component | Value |
| --- | --- |
| Host | macOS arm64 (Darwin 25.5.0, Apple Silicon) |
| OpenFOAM | v2412 via `brew install --cask openfoam2412` (gerlero/openfoam-app) |
| MPI | Open MPI **5.0.8** bundled inside the OpenFOAM app (see note below) |
| Python | 3.10.21 in repo-local `.venv` |
| Compiler | Apple clang 16.0.0 (`xcrun c++ -std=c++17`) |

> **Linux is still the recommended production target.** These notes exist so a
> developer on a Mac can build, mesh and solve without a Linux box. Everything
> below was actually run; commands and results are quoted verbatim.

---

## 1. Why macOS differs from the documented flow

`setup_env.sh` creates a **conda** env named `dcdtuni` and installs OpenFOAM +
MPICH from conda-forge into it. The runner detects OpenFOAM through
`STLGenRunner._conda_prefix()`, which checks whether `blockMesh` lives under
`sys.prefix`; if so, `_foam_env_prefix()` exports `WM_PROJECT_DIR`,
`FOAM_LIBBIN`, `FOAM_APPBIN`, `FOAM_ETC`, `FOAM_MPI=sys-mpich` and scrubs other
OpenFOAM/MPI paths.

On macOS we use the **gerlero/openfoam-app** cask instead. That app:

- keeps the whole OpenFOAM tree inside a **mounted read-only volume**
  (`/Volumes/OpenFOAM-v2412`), and
- puts only a launcher (`/opt/homebrew/bin/openfoam2412`) on `PATH`.

The compiled utilities live in

```
/Volumes/OpenFOAM-v2412/platforms/darwin64ClangDPInt32Opt/bin
```

and are **not** on `PATH` until the volume's `etc/bashrc` is sourced. Because
`blockMesh` is therefore *not* under the `.venv` prefix, `_conda_prefix()`
returns `None` and `_foam_env_prefix()` collapses to just
`export FOAM_SIGFPE=false &&`. The runner then executes every OpenFOAM command
via

```python
subprocess.Popen(<cmd>, shell=True, executable="/bin/bash", ...)
```

which **inherits the environment of the Python process**. So the entire macOS
integration reduces to one rule:

> **Source the OpenFOAM environment in the shell that launches the server /
> `run_cfd`.** The utilities then resolve on `PATH` for every child process.

That is exactly what `tools/foam_env.sh` (added for macOS) does.

### macOS SIP and `DYLD_LIBRARY_PATH` (important, but harmless here)

When any process `exec`s a SIP-protected system binary such as `/bin/bash`, the
kernel **strips all `DYLD_*` variables** from the child environment. The
OpenFOAM `etc/bashrc` does export `DYLD_LIBRARY_PATH`, so it is stripped from
the `bash` that the runner spawns.

This does **not** break anything, because the gerlero build resolves every
library through `@rpath` baked into the binaries:

```
$ otool -l .../bin/blockMesh | grep -A2 LC_RPATH | grep path
  path @executable_path/../lib/openmpi
  path @executable_path/../lib
  path @executable_path/../lib/dummy
  path @executable_path/../lib/../../../env/lib   # the bundled Open MPI
```

`@executable_path` is relative to the binary, so `blockMesh`, `snappyHexMesh`
and even `mpirun`-launched solvers find their dylibs without
`DYLD_LIBRARY_PATH`. `PATH`, `WM_PROJECT_DIR` and `FOAM_*` are **not** stripped
(only `DYLD_*` are), so the utilities resolve normally.

Verified through the exact `python → /bin/bash → OpenFOAM` chain the runner
uses: a real (non-`-help`) `blockMesh` ran to completion and `mpirun -np 2`
launched both ranks, with `DYLD_LIBRARY_PATH` confirmed empty inside the child.

### Which MPI?

`setup_env.sh` installs **MPICH** and the runner sets `FOAM_MPI=sys-mpich`.
The gerlero app instead bundles **Open MPI 5.0.8** under
`/Volumes/OpenFOAM-v2412/env/`, and its OpenFOAM is compiled with
`FOAM_MPI=openmpi` against that bundled MPI. Sourcing the volume `etc/bashrc`
puts the bundled `mpirun` first on `PATH`, which is the **correct, ABI-matched**
launcher for this build.

The machine also has a system **Open MPI 5.0.10** at `/opt/homebrew/bin/mpirun`.
Do **not** force it for OpenFOAM runs: the bundled 5.0.8 is what the
`libPstream.dylib` in `platforms/.../lib/openmpi/` was built against. (Both are
Open MPI 5.x, but mixing launchers with a differently-built Pstream is the kind
of thing that fails obscurely.) All parallel steps below used the bundled
`mpirun` and worked.

---

## 2. Prerequisites

```bash
# OpenFOAM v2412 (native macOS app + CLI launcher)
brew install --cask openfoam2412

# A C++ toolchain (Apple clang ships with Xcode Command Line Tools)
xcode-select --install      # if not already present

# The repo's Python venv (python3.10). Deps are installed with pip, NOT poetry:
# poetry.toml sets virtualenvs.create=false, and the project is a poetry-core
# build, so `pip install -e ".[dev]"` installs the same declared deps.
cd /path/to/dcdtuni
python3.10 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
# liquid cooling / AI postprocess extra (torch + networkx):
.venv/bin/python -m pip install -e ".[liquid]"
```

> **torch on Apple Silicon:** `pyproject.toml` pins torch to the `pytorch-cu128`
> (CUDA) source. That index has no arm64 macOS wheels, so on a Mac install torch
> from the default index instead — it provides CPU/MPS builds:
> `.venv/bin/python -m pip install torch networkx`. The verified install pulled
> `torch 2.13.0` (arm64) and `networkx 3.4.2`. torch/networkx are only used by
> `liquidcooling/` and the AI-postprocess worker; the air-cooling CFD pipeline
> does not need them.

### `.venv` dependency check (verified)

`pip list` against `pyproject.toml` showed every declared dependency present and
`backend` importable from the repo:

```
dcdtuni 4.0.1 (editable, /…/dcdtuni)   Jinja2 3.1.2   MarkupSafe 2.0.1
loguru 0.7.3   pydantic 2.13.4   numpy 1.26.4 (<2 ✓)   vtk 9.5.1
pyvista 0.47.3   websockets 16.1.1   matplotlib 3.10.9   playwright 1.62.0
openpyxl 3.1.5   networkx 3.4.2   torch 2.13.0   pytest 9.1.1   pytest-timeout 2.4.0
```

```bash
$ .venv/bin/python -c "import backend; print('backend OK:', backend.__file__)"
backend OK: /…/dcdtuni/src/backend/__init__.py
```

No `pip install` was required — the existing `.venv` was already complete.

---

## 3. The OpenFOAM environment hook — `tools/foam_env.sh`

`tools/foam_env.sh` is a small, **sourceable** adapter (added for macOS; it does
not modify any tracked source, it only exports environment variables). It:

1. resolves the gerlero app bundle from the `openfoam2412` brew symlink
   (falling back to `/Applications/OpenFOAM-v2412.app`),
2. mounts the read-only OpenFOAM volume (idempotent), and
3. sources `/Volumes/OpenFOAM-v2412/etc/bashrc`.

```bash
$ source tools/foam_env.sh
dcdtuni macOS OpenFOAM env ready:
  WM_PROJECT_DIR : /Volumes/OpenFOAM-v2412
  WM_OPTIONS     : darwin64ClangDPInt32Opt
  FOAM_APPBIN    : /Volumes/OpenFOAM-v2412/platforms/darwin64ClangDPInt32Opt/bin
  FOAM_MPI       : openmpi  (bundled mpirun: /Volumes/OpenFOAM-v2412/env/bin/mpirun)
  blockMesh      : /Volumes/OpenFOAM-v2412/platforms/darwin64ClangDPInt32Opt/bin/blockMesh
```

**Use it every time you launch dcdtuni against OpenFOAM on macOS:**

```bash
source tools/foam_env.sh
./src/start_server.sh                       # server + browser CFD pipeline
# or one-shot CLI runs:
bash -c 'source tools/foam_env.sh && .venv/bin/python -m backend.aircooling.runner.run_cfd ...'
```

---

## 4. OpenFOAM utilities — `-help` smoke test (all stock binaries)

With `tools/foam_env.sh` sourced, every utility the pipeline uses resolves and
runs `-help` cleanly (exit 0):

```
blockMesh                       exit=0      decomposePar                  exit=0
snappyHexMesh                   exit=0      reconstructParMesh            exit=0
surfaceFeatureExtract           exit=0      redistributePar               exit=0
createBaffles                   exit=0      topoSet                       exit=0
checkMesh                       exit=0      setFields                     exit=0
buoyantBoussinesqSimpleFoam     exit=0      buoyantBoussinesqPimpleFoam   exit=0
```

No compilation is needed for these — they ship prebuilt in the gerlero volume.

---

## 5. Custom C++ (`third_parties/`) — build with `tools/build_third_parties.sh`

dcdtuni ships custom OpenFOAM C++ under
`src/backend/aircooling/third_parties/`:

| Dir | Targets | Used by the production runner? |
| --- | --- | --- |
| `snappyhex/` | `dcCartMesh`, `snappyHexMeshDC` (+ libs) | **No** — A/B research meshers. The runner calls *stock* `snappyHexMesh`. |
| `foam/` | `buoyantBoussinesqSimpleFoam_new`, `writeExplicitBC` | **Yes** — steady solve + explicit-BC rewrite. Falls back to stock if unbuilt. |

The liquid-cooling ODIL solver (`liquidcooling/solver/*.py`) is **pure Python
(torch)** — there is no C++ to compile there.

The stock `deploy.sh` / `env.sh` in those directories are **conda-only** (they
require a `dcdtuni` conda env and conda-forge's relocated
`include/OpenFOAM-<api>/src` header layout, plus a `$CONDA_PREFIX/src` bridge
symlink). On macOS none of that applies: the gerlero volume natively provides
`$WM_PROJECT_DIR/src` with `lnInclude/` dirs, so wmake's hardcoded
`LIB_SRC = $(WM_PROJECT_DIR)/src` resolves directly and **no bridge symlink is
needed** (the volume is read-only anyway).

`tools/build_third_parties.sh` is the macOS adapter. It sources
`tools/foam_env.sh`, points wmake's user output at each third party's own
`platforms/$WM_OPTIONS/{bin,lib}` (non-destructive; all gitignored), and builds
with the native wmake `darwin64Clang` rule (`CC := xcrun c++ -std=c++17` — the
same Apple clang the volume's OpenFOAM was built with, so ABI-matched).

```bash
tools/build_third_parties.sh cart      # dcCartMesh (lib + app)   [default]
tools/build_third_parties.sh snappydc  # full snappyHexMeshDC lib + app (slow)
tools/build_third_parties.sh solver    # buoyantBoussinesqSimpleFoam_new + writeExplicitBC
tools/build_third_parties.sh all
```

### 5a. `dcCartMesh` — BUILDS AND RUNS ✓

```bash
$ tools/build_third_parties.sh cart
==> [cart] dcCartMesh against /Volumes/OpenFOAM-v2412 (darwin64ClangDPInt32Opt)
    ln: src/mesh/dcCartMesh/lnInclude
Compiling enabled on 14 cores
wmake libso (dcCartMesh)
…clang++ -std=c++17 … -c dcCartGrid/dcCartGrid.C …
…clang++ … -dynamiclib -install_name @rpath/libdcCartMesh.dylib … -o …/lib/libdcCartMesh.dylib
…clang++ … -o …/bin/dcCartMesh
-rwxr-xr-x  1 … 166K …/platforms/darwin64ClangDPInt32Opt/lib/libdcCartMesh.dylib
-rwxr-xr-x  1 … 119K …/platforms/darwin64ClangDPInt32Opt/bin/dcCartMesh
    ✓ dcCartMesh built
==> done (rc=0)
```

One harmless warning (`unused-private-field 'tree_'`). The binary runs:

```bash
$ dcCartMesh -help        # exit 0
Usage: dcCartMesh [OPTIONS]
Options:
  -case <dir>       Case directory (instead of current directory)
  -overwrite        Write the mesh into constant/ rather than a new time directory
  -timeLimit <seconds>  Wall-clock budget for the whole run (default 900; 0 …
```

This proves the custom-C++ toolchain (wmake + Apple clang against the gerlero
headers/libs) works on macOS.

### 5b. `buoyantBoussinesqSimpleFoam_new` + `writeExplicitBC` — was BLOCKED (repo gap, fixed 2026-09-10)

Both sources `#include "writeBCFields.H"`, expected at

```
third_parties/foam/src/lib/explicitBoundaryWrite/writeBCFields.H
```

That file is **documented in `third_parties/foam/README.md`** but is **not
tracked by git and is absent from a fresh clone** (it is *not* matched by any
`.gitignore` — `git ls-files third_parties/foam/src/lib` is empty, so it was
simply never committed). `tools/build_third_parties.sh solver` detects this and
refuses to start; running wmake directly shows the exact failure:

```
wmkdepend: could not open 'writeBCFields.H' for source file 'buoyantBoussinesqSimpleFoam_new.C': No such file or directory
buoyantBoussinesqSimpleFoam_new.C:49:10: fatal error: 'writeBCFields.H' file not found
   49 | #include "writeBCFields.H"
make: *** [Make/darwin64ClangDPInt32Opt/buoyantBoussinesqSimpleFoam_new.o] Error 1
```

**This is not a macOS issue** — it would fail identically on Linux until
`writeBCFields.H` (and the rest of `src/lib/explicitBoundaryWrite/`) is restored
to the repo. The wmake toolchain itself reaches the compiler fine (as `dcCartMesh`
demonstrates).

**Impact + workaround.** The steady solve defaults to
`buoyantBoussinesqSimpleFoam_new`. When it is unbuilt,
`_resolve_solver_binary()` returns the bare `_new` name (it will *not* silently
substitute stock), so the solve would fail with "command not found". Set the
override to use the **stock** solver, which is physically identical (the `_new`
build only differs in how it serialises explicit boundary values):

```bash
export DCDTUNI_SOLVER_OVERRIDE=buoyantBoussinesqSimpleFoam
```

`writeExplicitBC` is best-effort: the runner logs
*"writeExplicitBC is not built; reconstructed fields keep OpenFOAM's default
boundary serialisation"* and continues. The only consequence is that
reconstructed boundary patches carry `type` without per-face `value`s — solve
validity is unaffected.

---

## 6. Smoke test — mesh generation (blockMesh + snappyHexMesh) ✓

The mesh pipeline is driven by a `.cfdrun` manifest plus browser-exported STL in
`Mesh/constant/triSurface/`. Only **`case/Slab`** ships a ready
`model/Slab.cfdrun` + STL on disk (the other 20 demos ship only `.cfdproj`,
which is converted to `.cfdrun`+STL by the **headless browser** via
`/api/browser/export-case` → `window.__dcdtuni.buildCfdrunJsonV2()`; that needs
the server + playwright). Slab is therefore the natural CLI smoke case.

To avoid touching the repo, the case was copied to `/tmp` and run there. Slab's
schema-default mesh config is **parallel** (`parallelMesh=True`); the runner
re-derives the process count from the machine (it used **8** here, host has 14
CPUs). Hand-editing `meshConfig.parallelMesh`/`meshProcess` in the `.cfdrun`
does **not** stick — `STLGenRunner._load()` revalidates through the pydantic
schema and `model_dump`s it, normalising those fields back. So the run below
exercised the **full parallel mesh chain**.

```bash
cp -r case/Slab /tmp/dcdtuni_smoke/Slab
bash -c 'source tools/foam_env.sh && \
  .venv/bin/python -m backend.aircooling.runner.run_cfd \
    --from-cfdrun /tmp/dcdtuni_smoke/Slab/model/Slab.cfdrun --mesh-only'
```

Result — every step ran, exit 0, **~10 s wall clock**:

```
Step 1/8: blockMesh
Step 2/8: surfaceFeatureExtract
Step 3/8: decomposePar -force -no-fields (8 processors)
Step 4/8: mpirun -np 8 snappyHexMesh -overwrite -parallel
Step 5/8: mpirun -np 8 redistributePar -parallel -overwrite
          mpirun -np 8 createBaffles -overwrite -parallel
Step 6/8: reconstructParMesh -constant -mergeTol 1E-08 -noZero
Step 7/8: wall-baffle conversion
Step 8/8: checkMesh (final quality check)
…
    cells:            204840
    faces:            685574   (internal faces: 598510)
    Number of regions: 1 (OK).
    Total volume = 230.491.  Cell volumes OK.
    Mesh non-orthogonality Max: 48.563 average: 8.72   → Non-orthogonality check OK.
    Max skewness = 1 OK.
    Mesh OK.
Mesh complete.
```

`constant/polyMesh/{points,faces,owner,neighbour,boundary,cellZones,faceZones,pointZones}`
were all written. **Mesh generation: PASS**, including 8-way MPI decomposition,
parallel snappy + baffles, and reconstruction.

---

## 7. Smoke test — solve (≤ a few hundred iterations) ✓

The solve was run through the runner with the **stock** solver override (the
custom `_new` solver is blocked, §5b). The mesh-stage `fvSolution` is a stub
(`solvers {}`), so the solve **must** go through the runner — its
`_setup_complete_case_in_mesh()` regenerates `controlDict`/`fvSchemes`/
`fvSolution`/`fvOptions` for the steady solver.

```bash
DCDTUNI_SOLVER_OVERRIDE=buoyantBoussinesqSimpleFoam \
bash -c 'source tools/foam_env.sh && \
  env DCDTUNI_SOLVER_OVERRIDE=buoyantBoussinesqSimpleFoam \
  .venv/bin/python -m backend.aircooling.runner.run_cfd \
    --from-cfdrun /tmp/dcdtuni_smoke/Slab/model/Slab.cfdrun --solve-only'
```

(`simulationConfig.endTime`/`solveProcess` edits in the `.cfdrun` are likewise
normalised by the schema — the run used `endTime=5000`, `solveProcess=8`. It
nonetheless finished fast because SIMPLE **converged early** via
`residualControl`.)

Result — exit 0, solve clock ~17 s:

```
Generated controlDict (steady): endTime=5000, writeInterval=5000, probes=5
Running decomposePar with 8 processes            → 8 processor directories created
Running: mpirun -np 8 buoyantBoussinesqSimpleFoam -parallel
  Time = 1 … DILUPBiCGStab: Solving for Ux/Uy/Uz, T … GAMGPCG: p_rgh …
  (residuals fall monotonically)
SIMPLE solution converged in 247 iterations
End
Running: reconstructPar -latestTime
writeExplicitBC is not built; … default boundary serialisation   (expected warning, §5b)
Solve complete
Sensor harvest wrote 5 sensors → …/sensorResults/Slab_sensor_*.json
```

The reconstructed `247/` directory contains `T, U, p, p_rgh, phi, k, epsilon,
omega, nut, alphat, uniform/`. **Solve: PASS** (stock solver, 8-way parallel,
converged in 247 iterations, reconstructed + sensors harvested).

---

## 8. Status summary

| Step | Status | Notes |
| --- | --- | --- |
| `.venv` deps | ✓ | Already complete; `backend` imports. torch from default index (arm64), not cu128. |
| OpenFOAM hook | ✓ | `tools/foam_env.sh` sources the gerlero volume `etc/bashrc`; runner inherits PATH. |
| Stock utilities `-help` | ✓ | All 12 pipeline binaries exit 0. |
| `dcCartMesh` (custom C++) | ✓ | Builds + runs against gerlero OpenFOAM (Apple clang, wmake). |
| `snappyHexMeshDC` (custom C++) | not built | `tools/build_third_parties.sh snappydc`; A/B reference only, runner uses stock snappy. |
| `buoyantBoussinesqSimpleFoam_new` / `writeExplicitBC` | ✗ at the time; source fixed since | `writeBCFields.H` was missing then; tracked since `b90282826` (2026-09-10). Not yet rebuilt on macOS. |
| Mesh generation (blockMesh + snappyHexMesh, parallel) | ✓ | Slab, 204 840 cells, `checkMesh: Mesh OK`, ~10 s. |
| Solve (parallel, ≤ a few hundred iters) | ✓ | Stock solver, converged in 247 iters, reconstructed, ~17 s. |
| ODIL liquid solver | n/a | Pure Python/torch — nothing to compile. |

### Known limitations / gotchas

- *(Fixed 2026-09-10)* The custom steady solver could not be built from a fresh
  clone because `writeBCFields.H` was missing; it is tracked now.
- **`.cfdrun` hardware fields are schema-normalised.** Editing
  `meshConfig.parallelMesh/meshProcess` or `simulationConfig.endTime/solveProcess`
  in the JSON does not survive `_load()`'s pydantic validation + `model_dump`.
  The runner picks parallel process counts from the host. To change them, adjust
  the schema defaults or the export, not the manifest.
- **`.cfdproj → .cfdrun` needs the browser.** Only `case/Slab` ships a ready
  `.cfdrun`+STL for CLI runs; converting other demos requires the server +
  playwright headless browser (`/api/browser/export-case`).
- **Use the bundled Open MPI 5.0.8**, not the system 5.0.10, for OpenFOAM runs.
- **`DYLD_LIBRARY_PATH` is stripped** by SIP across the `python → /bin/bash`
  hop; harmless because the gerlero build is `@rpath`-self-contained.

---

## 9. Files added for macOS (not part of the conda flow)

```
tools/foam_env.sh              # sourceable: mounts the gerlero volume + sources etc/bashrc   (not committed)
tools/build_third_parties.sh   # wmake adapter: builds custom C++ against gerlero OpenFOAM   (not committed)
docs/developer/macos_build_notes.md   # this file
```

The two scripts existed only on the Mac where these notes were written; see the status note at the top.

None of these modify tracked source; `tools/` is new and the build artefacts they
produce (`platforms/`, `**/lnInclude/`, `**/Make/darwin*/`) are already covered
by the existing `third_parties/*/.gitignore`. To surface this page in the
mkdocs site, add `- developer/macos_build_notes.md` under the `developer` nav
section in `mkdocs.yml`.
