# Read-only P20 normal-build preflight

All 33 current AIR production source controls are byte exact against the OBS before snapshot. Existing conda prefix src is already a symlink to OpenFOAM-2412 dev headers, and IT platforms already resolves the AIR platforms directory. No new symlink outside B is needed. env.sh's conditional bridge-creation branch must remain unentered; recheck that link before normal activation.

Current APP and LIB hashes, RPATH and explicitly resolved owned library are in receipt.json and raw readelf/ldd outputs. No env.sh sourcing was done during this preflight.

Allwmake strips --cart-only and forwards -j2 unchanged to library then APP wmake. Installed wmake accepts the numeric worker argument; the preserved prior actual build observed make -j 2. This is source/historical proof, not a new build execution.

ROOT should activate the existing dcdtuni conda environment using its existing conda.sh, use CPU8-15/CUDA empty and repo-owned TMP/cache, and run the normal Allwmake --cart-only -j2 paired library/APP build. New SourceSidecar TLS requires the normal paired rebuild. No normal source/deploy/build/service/API/physics/Git change occurred here.
