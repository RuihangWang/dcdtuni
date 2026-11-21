# Target Shinaide Liquid air audit, 2026-10-05

The React-exported target achieved genuine strict SIMPLE residual convergence
at iteration 600 through the authenticated product API (job efff2dfe). Its first
pressure initial residual was 0.000998421 and all other configured controls were
below 0.001. The real log includes the convergence marker, final explicit
boundary write, End, MPI finalization; API done/returncode 0. All 112 native
fixed-temperature bindings are present and final T is 308.15 K exactly within
written precision. Final fields exist across all 8 MPI ranks.

This is solver-convergence evidence only. The **identical mesh failed 4 full
topology/geometry checks** in subsequent product check-only job 79b0f710:
35 poor face tets, 125598 concave cells (face-plane criterion), 54 small
interpolation weights and 46 small face volume ratios. Mesh SHA c279d3a... and
input SHA 7c54ce95... stayed unchanged; the bound quality receipt is rejected.
The mesh is not an accepted transient seed or complete physical-verification
result. No outer threshold or geometry was changed to obtain either outcome.

The final room field spans 292.15–298.525 K; the closed plenum remains 295.15 K
and has zero physical boundary flux. These fixed-temperature inputs do not
prescribe total heat power. Written phi is six-digit data and its boundary net
or cell imbalance is distinct from the solver's in-memory continuity.
Boundary phi*T is an advective moment, not a complete conductive energy balance.

Read gauged-steady-final-receipt.json first. The raw final solver stdout is
gauged-steady-final-stdout.txt; exact input is gauged-steady-input.cfdrun.
Final field hashes/ranges and actual boundary checks are in
gauged-written-field600-audit.json and gauged-final-thermal-binding-audit.json.
Full checker raw/binding are gauged-full-quality-stdout.txt and
gauged-full-quality-bound-receipt.json; SSE and final API status remain alongside.

Earlier misdirected and aborted exploratory jobs are preserved as failure
history, including source restoration and exact original input bytes. Their
logs do not establish a passed solve or mesh. The pre-gauge b53 baseline was
aborted above the strict threshold; it is separately indexed by old-b53-*.

No new files were created outside the repository. Case copies, builds, caches
and logs stayed inside its worktrees. Old active binaries and run inputs stayed
frozen. The new check-only API includes full flags, actual MPI layout, case lock,
nonempty check, input/mesh identity checks and a 300 s whole-job ceiling; even a
checkMesh exit 0 with Failed N becomes rejected.

Readonly cleanup comparison and deterministic face partition: [derivation and limits](target-tet-cleanup-partition.md), [raw comparison](target-tet-cleanup-comparison.json), [partition result](target-concave-face-partition.json), [receipt](target-tet-cleanup-partition-receipt.json). Official 35→24 tet failure events correspond to 32→21 unique face labels; this corrects the previous coupled-accounting interpretation. Neither polygon partition nor optional point cleanup establishes full geometry acceptance.

Rectangular54-cell roof diagnostic: [affine proof and scope](rectangular-roof-independent-audit.md), [raw matrix](rectangular-roof-independent-audit.json), [receipt](rectangular-roof-independent-receipt.json), and tracked roof-rectangular-fixture inputs. One actual2MPI fullchecker reports Mesh OK; this is not a production-case acceptance.

Compatible roof component counterexamples: [protected-border proof](roof-component-counterexamples.md), [raw trials](roof-component-counterexamples.json), [receipt](roof-component-counterexamples-receipt.json). Shared midpoint agreement does not establish union-star planarity; production components are not inferred from these mathematical fixtures.

Actual native Tri roof witness audit: [scope and concrete veto points](native-roof-tri-independent-audit.md), [independent result](native-roof-tri-independent-audit.json), [receipt](native-roof-tri-independent-receipt.json), and original roof-native-components-tri JSONL. Complete/partial proposal and star evidence is kept distinct; no mesh quality acceptance is claimed.


The independent serial roof witness audit is in [native-roof-serial-independent-audit.md](native-roof-serial-independent-audit.md). Its complete six-packet displacement proves a local planarity failure on the partial 32-cell export, while incomplete displacement fields are explicitly refused. This diagnostic does not authorize any user-case mesh or solver seed.


The independent cone/fan proof and native raw hash check are in [conforming-cone-independent-audit.md](conforming-cone-independent-audit.md). They confirm unchanged original coordinates, strict fixture kernel, positive nonoverlapping convex base fans and exact original boundary-polygon coverage. Concrete processor ordinal and rotational-cyclic counterexamples delimit the distributed implementation; no production mesh acceptance is claimed.

## Exact rejected native cone-base identities

conforming-cone-exact-base-witness-audit.md and its bound JSON/receipt preserve the independent read-only mapping of all 18 native Tri rejected base polygons to written faces by exact float64 cyclic coordinate bytes. There are 20 cell-base incidences: 13 unsplit warped faces and five split-required warped faces (seven incidences). All independent split flags match; the four focused regressions pass. Original native conditions, matrix, SSE and eight rank coordinate witnesses are included. This explains the failed optional preflight and does not establish product mesh acceptance or extend the planar cone/fan proof to warped source bases.

## Original STL and first observed carving stage

conforming-cone-source-stl-audit.md binds the original STL triangles and retained 17-digit native stage dumps to the independent source audit. One Rack_2_frame pentagon is already present with the final five-point coordinate multiset at 01carving and persists through 16skewrepair; the other two upper-y pentagons fall outside the stage box. It separates raw source-facet proximity from cap planarity and explains the whole-shell ON/cap tolerance mechanism without claiming a particular unrecorded branch. The separate three-internal-base fan audit rejects every original-vertex fan under the unchanged area/moment guard; air is correcting seam plane quantization at its source instead. These are read-only diagnostics and do not establish mesh acceptance.
