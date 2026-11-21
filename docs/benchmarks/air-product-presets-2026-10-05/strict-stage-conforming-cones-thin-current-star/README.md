# Current full-star thin-box diagnostic

This remains an opt-in diagnostic and is not an accepted mesh or solver seed. The actual React load/export, owned software-browser close and mesh API completed in 221.568149 s. The full mesh API took 163.573110 s, including native generation (143.939 s) and the mandatory stock `checkMesh -allTopology -allGeometry`. The checker rejected three checks: 1147 small determinants, 102 low weights and 2 low volume ratios; skew failures were zero. Non-orthogonality warnings affected 817 faces. All thresholds and source geometry remain unchanged.

`matrix.json` contains monotonic request/response events, the input and executable hashes and the actual rejected job `41c554a7`. The native implementation snapshot is saved as three gzip sources plus a patch bound to air HEAD c34c1c61. These source snapshots are evidence; no prototype was promoted by this commit.

The independent reproduced conservation audit binds the read-only tool SHA. All 12 original STL files and every original source point-array prefix are byte/value exact. The signed exterior-outline volume is 308.2018924596476 m3 before and after, and the first-moment change is [2.2737367544323206e-13, 0, 1.1368683772161603e-13] m4. Prefix equality certifies stored original coordinates, not all possible late-writer aliases. `written-coupled-pairs.json` separately verifies 24 coincident cyclic and 7 processor patch pairs with exact relative-face, point0 and reverse mapping. Actual processorCyclic faces were absent and are not claimed as tested.

The 58 candidate vetoes comprise 51 predicted writer/fan rejections and 7 remote global-ID vetoes. Counts of thin candidates and splits are visits across rounds, not unique source cells. The current-star guard no longer permits the prior 165 invalid-fan refusal, but the final quality failure remains authoritative.

`test-audit-output.txt` records five independent tests of signed boundary integrals, including a concave outline, translated coordinates, subdivision invariance and non-finite/degenerate rejection. The original and independently reproduced reports are both preserved. Raw SSE, quality sets, source/written mappings, candidate logs and partition witnesses remain bound by the manifests.

Further investigation must match actual source polygon/patch/zone geometry and cone base pieces across versions. Written cell IDs cannot be compared directly after topology changes. Healthy source bodies can still generate bad fan children; increasing splitting rounds without tracing that cause is not supported by this result.
