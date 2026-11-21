# Independent read-only audit of actual formal MeshGenerator job 420920b2

This actual normal React job reached native export, then failed the unchanged strict OpenFOAM checkMesh with three checks. The leaf-boundary half-ownership change solved the prior four S10 excess wall-half failures. The remaining mesh has a separate post-validation topology mutation and incomplete convexity validation in the producer. This packet is an audit and a proof-only private algorithm preview; it does not contain an accepted production repair.

## Actual identity and preserved evidence

Authoritative IT: /mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration. New packet: .codex-tmp/meshgen-leaf-ownership-actual-independent-audit-20261006. Python analysis ran only on CPU24-27, CUDA empty, BLAS/OMP threads one. No native mesher, checkMesh, utility, API, browser, service, build, or Git operation was executed. The attempted independent child source review was refused by the four-agent concurrency limit.

ROOT's actual native observation captured PID877504, startTicks129894917, cwd the actual case Mesh/.meshgen_scratch, and the positional cfdrun argv. Actual CLI SHA256 cf22d153cfad5979bc5fb274fd0e5d0b0258dac8c2366804ecbdff402d54c029 is independently still verified. Cartesian.cpp is SHA256 4cd6aab4259fdc15339e35bf27f28de52b394d9949319f0b20cce3a5c74c0eed. The six-hunk approved ownership preview therefore remains the source being audited, with its helper separately bound in source-references.json.

The physical input SHA256218096e0b21848204fcb5271375427eda11bc60f207190e21494ba413876679e independently differs from the preceding b616237a8786416b543158cbdd4885e16dbcd593ff24fcb12c1593babfe0a460 only in /meta/casePath. All four actual input STL files exactly equal the preceding case files. independent-input-STL-binding.json records each hash. The browser helper with BrowserProof_Only in its filename remains a reference to ROOT's evidence, not physical input.

The five ASCII polyMesh files were copied once (about 280KiB total), plus the five small actual checkMesh sets, four small STL files, two scratch reports, physical input, and runtime/preflight receipts. Scratch and promoted polyMesh files are byte-identical in all five files. Original full browser proof/DOM/JPG and helper are hash references, not redundant copies. The DOM contains the actual full native/checkMesh output; logs[] in ROOT's browser JSON is empty. actual-dom-extracted-log.txt losslessly decodes the 5519 generic log rows bearing the actual timestamp/severity prefix; the original DOM remains referenced for non-log UI lines and ordering.

Do not confuse ingest-report.json (solid-role interpretation) with native-S7-report.json (postprocessing report whose stage field is S7 and which contains S10 census). The latter has kind1=kind2=kind3=0, cells1344. Its patch-shell nonManifoldEdges=8 is separate evidence and is not asserted to be the cause of any individual exported cell failure.

## The original S10 ownership failure has actually disappeared

Native actual rows show S10 orient+closure completed and its census has no failures. The final exported 1344 cells all have exactly zero directed area sum under rational arithmetic. V=511/512=0.998046875; min1/32768, max27/4096, matching checkMesh. Global first moments are (4091,4087,4091)/8192, also exactly the unit room minus the literal thermal block. Every cell's exact volume and first moment agree between tetrahedral reference origins zero and (3/7,5/11,13/17). All 1344 individual values are retained.

The old compact cells457/463/469/475 are not relabelled as current IDs. Comparing their geometric boxes with current cells yields current pairs1328+1329,1332+1333,1336+1337,1340+1341. Each pair has volume15/32768 and first moment identical, component by component as Fractions, to the old shell with its single excess +Y cap omitted. These are partition comparisons, not native identity joins.

The formerly excess cap supports still have lawful lower-neighbour wall halves: current faces4093/4095/4094/4092 belong to lower cells161/168/175/182, each with maxY=.5, Thermal_block_Bottom/BARRIER_SHEET, owner Sf=(0,1/256,0). None belongs to the compared upper partition. Thus the ownership repair removed the wrong upper half while retaining the valid lower wall. old-s10-to-actual-geometric-partition.json supplies both exact moment comparison and lower owner/role/area binding.

## Actual strict failure and independent reproduction

Raw actual checkMesh reports:
- 74 open cells, writing zipUpCells with the generic wording over used edges.
- 8 low/negative face-tet error events, writing four distinct faces to lowQualityTetFaces.
- 74 concave cells using face planes.
It also reports four concave faces as an informational result. Face pyramids, volumes, nonorthogonality, skewness, face flatness, determinant, interpolation weight and volume ratio passed. The actual terminal is Failed3; solver remains disabled. No passing quality state is inferred from exported geometry or S10 passage.

The sets zipUpCells and concaveCells are exactly equal, and lowQualityTetFaces and concaveFaces are exactly {3747,3760,3770,3779}. The OpenFOAM-installed v2412 sources are hashed and relevant formulas preserved as numbered excerpts. Python uses those face centres, area vectors, cell-centre pyramid formulas, face-plane predicate, signed tet volumes and circumradius quality formula. The calculation is independently implemented; it is not another invocation of native checkMesh and not a claim of bitwise equivalence to compiled C++.

### Topology: exact surviving conformity vertices were stripped

Direct point-ID edge counting independently returns exactly the 74 actual zipUpCells and no others. All1068 abnormal cell-edge incidences have count1, rather than count>2. The raw text's over used label therefore must not be treated as a measured degree. A typical failure is cell152: edge43--109 is present as one long edge, but neighbouring faces contain43--107--109 as two short edges, all three points lying exactly on the same line.

Cartesian.cpp34118-34280 validates edge count2 before export, returning false on any failure. Then dcMode W-SB3 at34283-34329 removes exact collinear points independently from each face, without updating all incident face topology or repeating that validation. Actual W-SB3 report records181 faces/260 deleted references after that gate. S11c34330 onward only compacts unused points.

A proof-only reconstruction uses existing final points, not invented geometry: inserting all exact collinear points within each existing edge touches161 faces and adds236 references. Every one of the1344 cells then has exactly two oppositely directed uses of each point-ID edge. Every face area vector is exactly unchanged. Reapplying the literal existing W-SB3 lowest-index exact cross-product rule to that closed representation reproduces every one of the4672 original actual face-ID rings exactly. This establishes that the source mutation can destroy the exact conformity invariant in precisely the manner observed. It is not a claim to reconstruct the uncaptured native pre-prune ring byte sequence: its161/236 counts differ from actual181/260 because24 deleted references can include vertices no longer present in final points.

A single pass limited to owner/neighbour existing point sets is insufficient: after resolving the original74 it leaves24 different cells with broken edges due to missing propagation. That failed first proof is preserved under one-pass-*; the complete global-coordinate proof is the authoritative geometry-summary.json/existing-vertex-subdivision-proof.json. Moving the existing insertion once after prune, or merely retaining a few actual IDs, would not establish a general solution.

The generic private safe_prune helper in proof_preview.py permits removal only when the vertex is exact-collinear in every incident current face and the complete incident cell edge counts remain2 after the joint transaction. On the closed reconstruction it correctly retains all236 required references, performs no removals, and retains valid directed edge pairing. This is only a topology-safe algorithm proof; it has no native source diff/application and no quality acceptance.

### Face-plane gate: 66 planar-partition cells and eight truly nonconvex cells

Installed primitiveMeshCheck.C808-910 compares each other face centre with each oriented face plane and rejects cosine>-1e-6. It explicitly includes planar face pairs. The independent implementation reproduces exactly the full74-cell concaveCells set. Sixty-six cells have no positive plane violation above1e-6; their trigger is distinct coplanar face centres, with cosine near0. Their multiple coplanar tiles remain a valid reason for rejection by the unchanged strict gate, even when the spatial cell is geometrically convex.

Eight cells do have substantial positive plane violations:1330/1331,1334/1335,1338/1339,1342/1343. These share the four internal six-point diagonal faces3747/3760/3770/3779. Each cell has exactV23/65536. They occupy the four upper corner boxes with Y=.59375--.6875, above the thermal block topY=.625. Their actual external boundary faces bind Thermal_block_Top plus the corresponding Left/Right/Front/Back wall to BARRIER_SHEET. Their complete owner/neighbour references, coordinates, directed areas, exactV/M1, boundary names and source-role records are in bad-cells-actual-face-role-binding.json. Source and actual raw split-parent IDs are not joined merely by spatial similarity.

### Tet failure: face-centre fan is invalid despite positive pyramids and a vertex fan

Each six-point diagonal face has area-vector magnitude components7/1024, with x/z signs following its orientation. Its two incident cells each have a positive face-pyramid volume approximately7.533566387379e-05. But the face-centre fan has one reversed triangle at the thermal notch: owner tet volume+1.861649702509e-06 and quality+0.0267800113 (owner expected negative), and neighbour volume/quality the corresponding negative values (neighbour expected positive). The four owner and four neighbour failures give exactly the raw count8, with four unique face IDs.

Each of these faces has two available positive common vertex-base decompositions. Thus noBase is not the root of this four-face failure. Choosing that base, or merely triangulating the face, does not cure the eight nonconvex volume cells or the66 coplanar-partition cell gate. Detailed every-edge tet volumes/qualities, all six base candidates, face/cell centres and pyramids are retained in bad-face-tets.json.

## Producer source blind spot and reuse candidates

The actual raw rows5253-5280 show eight S9b parent splits. Four upper diagonal choices at raw parents459/466/473/480 are marked allGreen=1 with bestFoam=-.00362319. These numbers are centroid halfspace criteria, not a proof of convex pieces. Current internal diagonal faces have compatible geometry, but the packet does not invent an uncaptured parent-to-compact-ID map.

Current reusable transaction:
- trySplit17279 clips both sides and reconstructs connected piece shells.
- Piece closure/positive volume checks at17635-17668.
- Piece foam-centre evaluation17669-17686 and centroid quality checks.
- NbrPatch17753 onward splits the matching neighbour face; finalNbr17932 onward stages neighbour topology.
- Crossface pairing/partner checks and neighbour final face records precede the dry run and commit.
- The dryRun/commit tail18187-18248 reuses the existing transaction.
- splitOneCell18253 rejects population when the centroid is inside at18255; candidate scoring at18347 treats centroidInside as allGreen.

Those guards explain why a positive-pyramid, centroid-inside nonconvex cell can be accepted and cease being considered for further decomposition. They are not equivalent to the installed face-plane/tet quality gates. Existing source has no complete V/M1 conservation assertion in this transaction; the next preview must add/verify that evidence instead of assuming positive child volumes imply conservation.

## Proposed next step and acceptance boundary

MINIMAL_PRIVATE_FIX_DESIGN.md separates a small topology safeguard from the larger lawful convex/conformal producer repair. No quality thresholds are loosened; no cells or wall halves are discarded; no flags are disabled. Only the safe topology algorithm was previewed in this packet. The eight nonconvex cells,66 coplanar-partition checks and four failing face-centre fans remain unresolved in the actual case.

Any further preview must prove owner/neighbour/shared-face updates, closed directed edges, literal patch/role preservation, conserved cavity volume and first moments, positive pyramid/tet conditions and the exact strict face-plane predicate for every changed cell and neighbour. Native full-case acceptance must subsequently come through ROOT's normal React job and unchanged strict checkMesh. The current Failed3 artifact remains frozen.

Controlled proof completed rc0 in11.2365300199s, CPU24-27/CUDA empty,20 assertions allPASS; stdout, empty stderr and exact script hashes are retained. The exact subdivision also preserves every cell V and M1 under both reference origins. Actual raw rows5360-5362 show S11b edgeConsistency2 START/END immediately followed by W-SB3 pruning. Source33990-34117 binds the original monotone64-round insertion cap and the out.mesh lifecycle: CellRec.faces can already be released, so topology validation belongs on out.mesh while lawful convex splitting must occur before that release. The next preview must preserve the existing convergence cap and fail closed.
