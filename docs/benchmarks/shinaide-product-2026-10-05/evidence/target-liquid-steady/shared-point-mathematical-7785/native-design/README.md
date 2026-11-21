# Shared-point native insertion and bounded performance design

Read-only implementation proposal, not a source patch or native acceptance. Source freeze: AIR C ead4a057, H 4a5ef961, APP babd07c8. Full identifiers, installed-source excerpts and actual binary headers are in source-evidence.json. No GPU probes, native run, services, browser, builds or index mutation were performed.

## Recommended insertion

Use the REAL compact installed polyMesh, inside toPolyMesh after mesh.addZones (C16253) and before its final coupled certificate (C16254–16295). Make the feature separately default OFF and skip baselineOnly. Candidate trials stay in compact local scratch. Accept a bounded combined proposal, update installed coordinates once on all ranks, force installed Sf/FC/CC/V, then validate the changed complete stencil and the existing final certificate. APP obtains a mesh only after these gates and still invokes its normal write at APP2969–2974; the backend full checkMesh remains the product acceptance gate.

Moving after toPolyMesh returns would bypass the current certificate: C16295 already resets the earliest baseline. Capturing a new baseline after movement would also lose the preoperator promise. When smoothing runs without literal unions, the earliest immutable installed oracle must be captured before the FIRST authorized mutation; the current oracle is only created inside literalUnions (C14148–14196). When both operators run, reuse that earliest baseline rather than refreshing it.

Prewriter cutMesh proposals are possible but weaker as a first implementation. C15689 merges points, C15697–15779 cleans/rewrites faces and owner/neighbour ordering, and C15848–15920 compacts addresses. Their local mathematical checks do not certify the final aliases, ring order or installed geometry. Normal writerBindings (H55–59; C15915–15920) are production addressing; optional TRACE maps must not become an authorization dependency.

## Installed geometry trap and safe update

This is a plain polyMesh (C16123), not fvMesh. In this installed v2412 build, polyMesh::movePoints calls primitiveMesh::movePoints, which is a no-op (primitiveMesh.C318–329). The inherited updateGeom only recalculates missing geometry (343–355). Thus bare movePoints on an object with already-materialized CC/V/Sf/FC can leave stale arrays and falsely certify a candidate.

Prefer the actual installed polyMesh::updateGeomPoints API (polyMeshClear.C72–147): an unregistered, NO_WRITE pointIOField at the existing instance is transferred; primitive and boundary geometry caches are cleared and patch geometry is updated. Reacquire all four geometry arrays afterwards. The new point field is temporary; the registered mesh and APP setInstance/write path remain the actual output owner. An existing tet-base cache is NOT automatically cleared by an empty optional replacement: explicitly clearTetBasePtIs and require stock-valid face decomposition to be recomputed.

If movePoints is used instead, explicitly clear geometry first and reacquire it. It additionally invokes Time.functionObjects().movePoints (polyMesh.C1292); with debug invalid motion it may call write() at1299. That path is less suitable for a no-write-until-final-certificate operator. Never retain geometry references across a clearing/replacement call; copy only the compact stencil values needed for comparisons.

All ranks, including no-candidate/empty ranks, must enter any actual patch-geometry update in the same phase. Reduce global-any once; if it is true, every rank invokes the same update using changed or unchanged local points. No per-point or per-candidate MPI collective is needed. After local installed verification, one unconditional failure reduction must Fatal-before-write and preserve the immutable checkpoint.

## Complete graph and exact arithmetic order

Acquire mesh.pointFaces() and mesh.cells() once. pointFaces inverts the face graph once and caches it (primitiveMeshPointFaces.C33–47); cells similarly caches owner-to-face addressing. Do not additionally build pointCells for every mesh point: derive the small candidate core directly from incident faces.

For proposed point set P:
1. Incident faces = union pointFaces[p] for every p in P.
2. Core cells = both owners and internal neighbours of every incident face.
3. Affected interfaces = ALL faces of ALL core cells, not just the moved-point faces.
4. Outside cells = the other cells across those interfaces; retain ALL their faces, coordinates, roles, CC/V and the second-ring fixed-cell values needed to prove their unchanged interfaces.
5. Coalesce overlapping expanded stencils before joint optimization, or reevaluate overlapping later trials against the current accepted coordinates. An outside cell of one component can be core in another; it cannot remain assumed fixed in a combined proposal.

Use the cells list in its installed owner-faces-then-neighbour-faces order (primitiveMeshCells.C82–95), identical to primitiveMeshTools.C311–362 accumulation. Mixing all face IDs into a single ascending list changes rounding. Face normal/centre operation order is primitiveMeshTools.C204–268. The existing C7132–7182 helper is related context, not a proven bit-identical substitute: its triangle branch C7152 divides the vector sum by3.0, whereas installed triangleI.H157 multiplies by the rounded scalar(1.0/3.0); VectorSpaceI.H781–789 performs component division. New trials should call installed triPointRef::centre/areaNormal directly and confirm final real geometry. No compiled equivalence experiment was run; the current writer certificate already uses real installed geometry.

The old thin pointFaces and unionCF/geometry caches are function-local before final writer compaction. They cannot be reused as compact addressing without real bindings and a demonstrated lifetime. Reusing the installed graph is the smaller, safer first implementation. Topology remains fixed, so these two graphs do not need rebuilding after point moves.

## Authorization and final guards

For internal motion: all moved points must have a complete incident star, no physical/coupled point incident, and no protected face/point/cell zone or coupled interface in the authorized core/outside stencil. Read actual installed patch.coupled() plus protected type prefixes, complete patch dictionaries, zone identities/flip flags and source roles. Do not infer eligibility from point ID suffixes, nGridPoints, TRACE parent maps or case IDs. Fixed outside geometry is verified from its complete unchanged face graph and again from freshly installed CC/V bytes.

Flat physical motion is a distinct authorization class. Every physical incident must lie on one exact existing axis plane with identical complete physical patch dictionary and unzoned support; internal edge cancellation must yield one fixed simple outer ring excluding the moved point. Its outer-ring coordinates and roles remain exact, all replacement pieces are positive/convex, and signed area/first moment equal the fixed footprint. Existing plane offset from raw STL remains unchanged. This does NOT promise raw STL exactness or unchanged coordinates on every physical triangle.

The new ten-point Air audit explicitly records unchangedShellOrderedWorldCoordinatesAndRoles=false. Therefore it cannot be presented as a global physical-face DP identity certificate. Its flat footprint certificate must remain separate. This report reads that candidate but does not independently recertify its entire math stencil.

For each trial and final installed stencil preserve:
- finite point/area/centre/volume, positive signed volumes and face pyramids;
- directed edge closure, legal convex/planar faces, stock face-centre cell-concavity predicate and stock tet-base validity;
- actual selected-face determinant mask (physical uncoupled faces omitted; internal/coupled selected), original D/W/R/S thresholds and no new failing original role or worsened already-bad severity;
- warning-N policy may exchange labels but may not exceed the authorized original ceiling; do not recast that policy as zero warnings;
- fixed outer geometry and mathematical V/M1. Closed unchanged shell plus opposite shared-face cancellation proves regional moments algebraically; a native floating-point small difference is not an exact Fraction assertion. Independent actual DP/Fraction audit of final written coordinates is still required.
- coupled coordinates, native aliases, ordered rings/point0, patch/zone roles, installed Sf/FC and owner CC/V remain bit-exact via the EARLIEST writer certificate. No tolerance substitution.

Stock primitives: primitiveMeshTools.C829–931 determinant, 380–451 skew and486–521 orthogonality; polyMeshTools.C175–228 weights and232–278 ratios. Those full-field helpers perform boundary communication and allocate full arrays; use their exact local formulas for trials, reserving collective/full-field work for one final phase. Existing literal-union C14385–14418 is useful formula context but its strict-positive-tet guard and tolerances are not automatically the stock specification.

## Bounded work and honest 300-second budget

The frozen actual manifests contain5,906,779 owned cells,16,309,666 rank-local face entries and4,543,830 rank-local point entries (boundary duplicates included in the last two). One final full pointField copy has109,051,920 bytes of DP payload across ranks; the four geometry fields total971,880,896 bytes. These are payload estimates, not RSS or an allocated-memory promise. Installed geometry already exists for the certificate; avoid duplicating every global geometry field for trial storage.

Cheap census: one selected-Sf D pass over current cells, then seed point sets from the actual bad cells/quality interfaces only. Deduplicate those points and components. Reuse installed Sf/FC/CC/V as baseline; trial updates touch only incident faces and core CC/V, then all affected metrics. Outside arrays are fixed copied stencil values until a component overlaps another accepted proposal. No wholemesh scan per candidate, no rebuild polyMesh per optimizer iteration, no repeated whole-field determinant allocation.

Suggested first OFF prototype bounds are explicit implementation limits, not established acceptance settings: at most16 moved points/component,512 core and512 outside cells,4096 stored stencil faces; at most256 complete profiles/rank per run and no more than2 local passes. Reject an oversized component transparently instead of truncating its star. Candidate generation remains generic and geometry-derived, with bounded backtracking. The particular ten-point candidate is not a source constant or complete repair.

Pass the remaining absolute mesh deadline into this phase. Check time/caps before and during each bounded trial; on exhaustion, stop local proposal work and join the common phase safely. Admission must reserve the observed cost of installed update, writer, I/O and the existing final checker; a reservation is not a proof that an arbitrary case will succeed. Log graph construction, census, trial and final materialization clocks/caps and peak memory separately. Current APP618–629 checks at stage boundaries only; it does not supply a per-trial deadline. The external300-second watchdog still terminates overruns and does not certify successful meshing.

Current actual oracle log example reports local copy/construct/snapshot .377717/1.44182/.151713 seconds and union max wall .249388 seconds. These are this frozen run's measurements, not a cost bound for new smoothing. A final installed update requires one O(faces+cells) pass after all accepted local trials. It cannot honestly be called localized cache reuse if the implementation invokes that full update after every trial.

## Validation before any promotion

First an isolated CPU/MPI kernel must demonstrate old-cache-before-fail and fresh-cache-after-update, no writes from the OFF baseline path, all-rank no-candidate participation, unchanged protected/coupled aliases and bytes, exact flat footprint, rejection of incomplete star/overlapping stale caches, and binary roundtrip using actual writer. Then one ordinary authenticated React-to-API diagnostic can test the whole pipeline under the existing300-second limit and full checker. No part of this read-only review performs those tests.

The currently compiled C/H/APP are frozen and unchanged. Existing shared-point results improve only local bad counts; the full actual7785 mesh remains Failed3 (D909/W100/R2). New combined ten-point data are still mathematical diagnostics, not native or global PASS.
