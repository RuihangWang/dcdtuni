# MeshGenerator normal repository vendor

This directory contains the reviewed MeshGenerator CLI/core source subset and cached nlohmann JSON needed by the ordinary dcdtuni `meshEngine: meshgen` runner. It is a real repository directory, independent of an external Ref_code symlink and scratch worktrees. source/* and deps/* are the 87 exact reviewed files. Original source HEAD 71a7d134b57074fa930a25821f3e536367ac214b, current source identities and the three schema-layout changes are recorded in UPSTREAM_PROVENANCE.json and SOURCE_MANIFEST.json. Preserve [source/LICENSE](source/LICENSE) (Apache2) and [deps/nlohmann_json/LICENSE.MIT](deps/nlohmann_json/LICENSE.MIT) (MIT); source/README.md is the original text.

The current read-only shared CfdrunGeometryView selects modern schema3/4 meshGeometry for the four ingestion consumers and CLI room reporting. It preserves the schema1/2 control behavior and refuses malformed current typed layouts. The owned source/parser review and 49 focused parser cases do not prove actual whole-mesh quality, a 5-minute mesh budget, native MPI or solver convergence.

## Offline ordinary build

On Linux x86_64 with already installed /usr/bin cmake, gcc and g++, run `./deploy.sh`. This script reproduces the reviewed Release configure and meshgen-cli target build with GUI/tests/MPI OFF, fully disconnected FetchContent and the local cached JSON path. No packages are installed or downloaded. GUI/tests source folders are not included in this CLI/core subset; do not enable those CMake targets without a separately scoped source import.

Source files stay in source/ and deps/. CMake objects/cache, compile_commands, temporary/cache files and the installed executable stay under platforms/linux-x86_64-gcc-release/. The script uses two build workers, CUDA empty and OMP_NUM_THREADS=1; caller affinity remains inherited. It builds and atomically installs the executable into platforms/linux-x86_64-gcc-release/bin/meshgen-cli, prints its SHA, and never calls tests, the CLI, meshing, checkMesh, solving or services. A later rebuild receives its own SHA receipt; bit identity to an earlier ELF is not assumed. The current supplied ELF is a byte-preserved copy of an already completed reviewed build, not a new build performed at publication.

platforms/ is ignored. The local binary is not a Git source candidate. A fresh clone can reproduce it from the 87 files and deploy.sh, or receive an explicitly verified copy of an accepted ELF. A source merge alone does not transfer ignored local binaries into MAIN; copy/build and hash verification must be done explicitly before deleting scratch worktrees.

## Runner discovery and startup

The existing STLGenRunner first tries an executable DCDTUNI_MESHGEN_BIN candidate, then this directory's platforms/*/bin/meshgen-cli, then its development Ref_code fallback. No PATH mesher substitution is used. start_server.sh requires no new MeshGenerator flag and inherits exported DCDTUNI_MESHGEN_* variables. It supplies the current checkout's backend via PYTHONPATH. With this local vendor binary populated and no stale scratch env pin, normal discovery needs no Ref_code checkout.

For an explicit startup pin, compute the bin path under the checkout being served and verify its SHA and executable mode. Do not leave an old AIR/IT absolute pin active before worktree cleanup. DCDTUNI_MESHGEN_TASKSET=0 removes the runner's extra 0..N-1 taskset override and inherits service affinity; it does not grant unrestricted CPUs or enable MPI. This imported build has MPI OFF. Case input, sealed RAM carrier, source/STL/quality and cancellation guards are unchanged.


Local producer update 2026-10-06: boundary wall-fragment half ownership uses the existing leaf bounds and clipping tolerance consistently across direct classification, wrap inheritance, emission and edge fallback. Original upstream/parser provenance is retained; current source and normal-build identity are bound in UPSTREAM_PROVENANCE.json. Strict actual case/checkMesh acceptance remains separate.
