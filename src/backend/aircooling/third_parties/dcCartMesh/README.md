# dcCartMesh — the repository's cut-cell Cartesian mesher

This directory builds **`dcCartMesh`**, the cut-cell Cartesian mesh generator
that is the runner's default mesh engine (`meshConfig.meshEngine: dcCart`). It
also carries the editable OpenFOAM v2412 `snappyHexMesh` source it grew out of,
which builds as the optional A/B reference `snappyHexMeshDC`.

What the mesher does, how it runs in parallel, its dictionary, memory law,
performance and the full table of environment switches are in
**[DCCARTMESH.md](DCCARTMESH.md)**. Short version: an axis-aligned background
grid, octree refinement around the geometry, cells cut by the STL itself (no
snapping, no stair steps), sliver merging in rank-independent rounds, one
algorithm for any MPI rank count. As of 2026-10-21 the ten release cases pass
strict `checkMesh` with it.

## Build

```bash
cd src/backend/aircooling/third_parties/dcCartMesh
./deploy.sh              # check env -> build -> smoke test (serial and 2-rank)
./deploy.sh --cart-only  # skip the slow snappyHexMeshDC rebuild
./deploy.sh --no-test    # build only
./deploy.sh --clean      # wipe platforms/ and rebuild from scratch
```

`python3 tools/bootstrap_dcdtuni.py --install` runs the same build for a fresh
clone (`deploy.sh --clean --no-test`). Output is **non-destructive**, in
`platforms/<WM_OPTIONS>/` (git-ignored):

```
platforms/linux64GccDPInt32Opt/bin/dcCartMesh        lib/libdcCartMesh.so
platforms/linux64GccDPInt32Opt/bin/snappyHexMeshDC   lib/libsnappyHexMeshDC.so   (unless --cart-only)
```

The runner finds the binary under `platforms/` by path and puts every
`platforms/*/lib` on `LD_LIBRARY_PATH`, so nothing has to be on `PATH`. The
binaries carry an absolute rpath: after moving or renaming this directory, run
`./deploy.sh` again. The stock `snappyHexMesh` in the conda prefix is never
touched.

## Run

The normal way is the product pipeline: pick the dcCart engine in the mesh
panel (it is the default) and mesh from the editor, or use
`python -m agent.scripted_api air …`. The runner writes `system/dcCartMeshDict` next to
the `snappyHexMeshDict` it already writes and starts
`mpirun -np N dcCartMesh -overwrite -parallel` (one rank runs without mpirun).
By hand, in a prepared case:

```bash
source env.sh
dcCartMesh -overwrite                          # one rank
mpirun -np 8 dcCartMesh -overwrite -parallel   # needs system/decomposeParDict and empty processor0..7/
```

## Layout

```
dcCartMesh/
├── env.sh           source this first — wires wmake to the conda OpenFOAM v2412
├── Allwmake         ./Allwmake [-j] [--cart-only]
├── Allwclean
├── deploy.sh        fresh-clone entry: env check -> build -> smoke test
├── DCCARTMESH.md    the mesher's design, status and switches
├── src/mesh/dcCartMesh/                                   the mesher library
│   ├── dcCartGrid/  dcCartOctree/  dcCartMeshBuilder/
│   ├── dcCartSurfaces/        STL geometry queries, weld and coordinate snap
│   └── dcCartCutCells/        the cut-cell kernel
├── applications/utilities/mesh/generation/dcCartMesh/    the driver (dcCartMesh.C)
├── src/mesh/snappyHexMesh/                                editable snappyHexMesh library
├── applications/utilities/mesh/generation/snappyHexMesh/ its thin driver
└── platforms/<WM_OPTIONS>/{lib,bin}                       build output (git-ignored)
```

## Toolchain notes

`env.sh` activates the **`dcdtuni`** conda env (OpenFOAM v2412: `WM_OPTIONS`,
`wmake`, libs, headers), creates a one-time header-bridge symlink
`$CONDA_PREFIX/src -> include/OpenFOAM-2412/src` (wmake hardcodes
`LIB_SRC = $(WM_PROJECT_DIR)/src`, conda relocates the headers; remove with
`rm "$CONDA_PREFIX/src"`), and points `FOAM_USER_LIBBIN` / `FOAM_USER_APPBIN`
into `platforms/`.

- **Compiler:** the conda compiler (`gxx_linux-64`) is not installed, so the
  build uses the system `g++`; `env.sh` exports `GXX=g++`. Linking against
  conda's newer `libstdc++` (runtime) is the supported "build-old / run-new"
  direction. For an exact-match toolchain, `conda install -n dcdtuni
  gxx_linux-64` and re-`source env.sh`.
- **MPI:** the conda OpenFOAM is built `WM_MPLIB=MPICH` (`FOAM_MPI=sys-mpich`);
  both meshers link the same MPI, so `mpirun` runs match the rest of the
  pipeline.

## Appendix — the editable snappyHexMesh (`snappyHexMeshDC`)

The `snappyHexMesh` library and application here are **pristine v2412** source
from OpenFOAM.com (ESI), `https://dl.openfoam.com/source/v2412/OpenFOAM-v2412.tgz`
(the conda package ships the compiled libraries and headers but not the `.C`
files). Only the build config differs, to produce renamed, project-local
artefacts; the pristine originals sit alongside as `*.upstream`:

| File | Change |
|---|---|
| `src/mesh/snappyHexMesh/Make/files` | `LIB` → `$(FOAM_USER_LIBBIN)/libsnappyHexMeshDC` |
| `applications/.../snappyHexMesh/Make/files` | `EXE` → `$(FOAM_USER_APPBIN)/snappyHexMeshDC` |
| `applications/.../snappyHexMesh/Make/options` | snappy include → `$(SNAPPY_DC_SRC)` (our headers); link → `-lsnappyHexMeshDC` with rpath |

```bash
source env.sh
snappyHexMeshDC -overwrite                     # like stock snappyHexMesh
mpirun -np N snappyHexMeshDC -parallel -overwrite
```

It is kept for A/B comparison against the stock mesher. The product's `snappy`
engine runs the stock `snappyHexMesh` from the conda prefix.
