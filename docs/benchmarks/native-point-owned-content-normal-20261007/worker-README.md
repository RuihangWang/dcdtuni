# V3 normal rebuild and tiny validation

Normal source cohort 39 was exactly bound to Root's accepted source apply. `Allwmake --cart-only -j2` completed (rc 0, 143.82 s); compiler warnings remain in full raw logs. Existing conda OpenFOAM 2412 header bridge was checked before construction and no outside-repository bridge was created.

The normal tiny driver links the rebuilt normal library directly, without a private `dcCartCutCells.C` object. Original driver CPP and all copied tiny case bytes remain exact. Reference OFF / new normal OFF / explicit direct API ON each passed 7 checks. OFF numeric output is 6594 bytes, equal to the prior reference and to the current reference byte for byte (SHA256 `46e3d9735734e6ede20e10582e18f37fef017bf0d91c1cea1c30a7c4950458e3`). ON reports captured/staged/committed 1/1/1, D 4→2, and installed prefix/final failure 0.

Readelf and ldd raw records bind the normal APP and normal driver to the AIR library and conda OpenFOAM 2412. APP uses sys-mpich Pstream under its built RPATH; the bounded single-rank tiny driver uses dummy Pstream. No MPI behavior is thereby proven.

Default earliestPhysicalPointRepair remains false and APP source entry is unchanged. APP was not run. This package proves no large-case quality, checkMesh, solver convergence, positive coupled-face behavior, hard wall/RSS bound, or five-minute end-to-end completion. Main 29 tracked edits, IT 977 source guards, eight PID/start/cwd/UID roles, header bridge, and IT platforms link held throughout. All old binary backups and original red logs remain outside this frozen packet in their existing repository locations.

`NORMAL-worker-receipt.json` is the full machine-readable receipt; all commands, stdout/stderr and input/new-pair/source39 bindings are included. The separate APP opt-in design is not part of this validation packet.
