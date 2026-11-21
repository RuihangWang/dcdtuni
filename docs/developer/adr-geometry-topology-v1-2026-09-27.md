# ADR (proposed): explicit geometry topology for air-side source sheets

**Status:** proposed; no mesher behavior or Shinaide case has been changed by this ADR.
**Date:** 2026-09-27.
**Scope:** `.cfdproj` V4 → grouped STL / `.cfdrun` V2 → dcCart; the same project identity is carried by `.liquidrun` V3. This first revision covers **air internal sheets**. It does not claim a complete CAD or liquid-network topology format.

## Problem and observed constraints

V4 project state, persisted as `.cfdproj`, is the editable engineering source; an export freezes that current state even if the user has not yet saved the latest UI edits. The current React export already gives 354 Shinaide source regions stable `sourceObjectUid` / `sourceFaceUid` entries in `.cfdrun.geometryExport.sourceSurfaces`; 74/74 prepared internal-baffle STL regions have a unique source face. dcCart can read these IDs, but it does not yet use them to decide cuts or area. It still groups triangles of a file by a rounded plane offset (~0.1 mm), and its area gate skips another sheet in the same faceZone if its plane is within 5 cm. Neither operation establishes physical identity.

The recorded RED fixtures show the resulting ambiguity: B06/B07 independent panels can be 28 µm apart along one axis yet 0.72 m apart in projection; two source skins of *one* thin wall can also be 28 µm apart; same-AABB complementary triangles are not duplicate area; a slightly tilted near pair can form a wedge; a mixed annotated/anonymous faceZone loses the assurance an ID provides. At the con_f/Raised_Floor seam, first-layer welding changes source vertices before `cutBaffles`; the first unsupported tagged faces appear in `02baffles`. Four of six sampled final faces in the local RED are outside their declared original source triangles. Sampling at face centres is only a necessary diagnostic, not a coverage proof.

One zero-thickness source sheet may yield `_master` and `_slave` through OpenFOAM `createBaffles`; these are the two **mesh sides of one source**, not two source skins to pair. `faceZone`, STL file, patch name, near-plane distance, AABB, and visual adjacency are not physical wall identities. The Shinaide `con_f` (`m_22`) and `Raised_Floor` (`m_52`) are separate zero-thickness source sheets. Their observed ~1 µm lateral offset and ~1 mm edge offset do not authorize declaring them joined; no field data or as-built P&ID is inferred here.

## Decision

Add an **optional, strict, versioned** root `geometryTopology` section to `.cfdproj` V4. Its project-side payload declares only semantic relations that geometry cannot safely infer: a pair of source faces representing one intentionally collapsed thin physical wall; a close, overlapping pair that intentionally bounds a resolvable fluid gap; and an edge-to-edge or edge-to-face contact. Stable face and contour IDs are **compiled from the saved construction geometry**, rather than copied as a second editable STL inventory. An imported/custom mesh must supply stable labelled faces/contours in its owning construction before it can participate in this contract. The proposed structural schema and a synthetic example are alongside this ADR: [`geometry-topology-v1.schema.json`](geometry-topology-v1.schema.json) and [`geometry-topology-v1.example.json`](geometry-topology-v1.example.json).

The project-side format is deliberately small:

```json
{
  "geometryTopology": {
    "version": 1,
    "mode": "strict",
    "coverage": "airInternalSheets",
    "wallPairs": {
      "logical_wall_uid": {
        "sides": ["project/object_a/side_a", "project/object_b/side_b"],
        "thickness_m": 0.02,
        "meshReferenceFaceUid": "project/object_a/side_a",
        "meshTreatment": "collapsedAirBarrier"
      }
    },
    "independentPairs": {},
    "edgeContacts": {}
  }
}
```

The key of `wallPairs` is a stable *logical physical-wall UID*. `sides` must contain exactly two distinct source-face IDs; a source face may belong to at most one pair. Both source faces must resolve to separate, singly owned, valid STL regions (possibly in different grouped files), and be `zeroThicknessSheet` sources. `thickness_m` is a positive physical input, not inferred from the two exported positions. `meshReferenceFaceUid` explicitly selects the supported source plane on which the **one** air barrier is cut; the other skin is validated then suppressed as a second air cut. No nearest-neighbour chain, first-triangle ordering, or `faceZone` matching selects a side. A collapsed barrier is an air-side thin-wall approximation: it does not perform solid conduction or imply an experimentally known material property. If side-specific boundary conditions cannot be mapped deterministically to the resulting master/slave patches, compilation fails.

Unpaired source faces are independent by default and their areas are counted independently. A pair of close, overlapping faces that might be mistaken for two skins instead needs one of: a valid `wallPairs` declaration; an `independentPairs` declaration with `relation: "fluidGap"` and a positive `minGap_m`; or a pre-mesh **ambiguity error**. The broad phase checks positive projected triangle-footprint overlap and a minimum separation within one selected local cell width; it does not use faceZone or AABB overlap as the final predicate. An independent gap is admitted only if the measured separation is at least the declared minimum and the selected local grid has at least two cells across its minimum width. A declared gap cannot legitimize coincident or crossing faces. Disjoint projected footprints, including B06/B07, are independent without a declaration. This broad-phase search is an error detector, never an auto-pairing algorithm.

`edgeContacts` may declare `edgeEdge` or `edgeFace`. It references stable **source contour IDs**, not triangle indices or generated patch names. Every point of the source edge must coincide with the other source edge, or lie inside the target face's original triangle support, under the versioned geometric tolerance. A contact records adjacency only. It never creates bridge area, changes the original STL, merges source-face identities, or grants a mesher permission to extend one face into another's support. A seam with a genuine gap must be corrected in `.cfdproj` geometry or represented by an explicitly modelled connecting source face. An undeclared close contour is never welded merely because it is near a grid plane; if leaving it separate cannot preserve source support, the mesher rejects it. If an imported STL has no stable contour identity, contact compilation fails rather than choosing an edge from triangle order.

## Source identity and compiled graph

Compilation emits a deterministic, sorted **source topology graph** before meshing:

- Face nodes: `sourceFaceUid`, `sourceObjectUid`, construction path, representation, original `(stlFileId, solidName)`, byte-checked STL SHA-256, original triangle-set hash, source area, and connected planar component/contour IDs. A face ID may own multiple disjoint *coplanar* components, but each component retains its area and support; no plane/AABB union turns them into one cut. Distinct non-coplanar parts need distinct source-face IDs in strict V1.
- Relation edges: declared physical-wall pair, intentional fluid gap, or contour contact. Each relation references only graph nodes; `faceZone` and boundary conditions remain separate attributes, not identity edges.
- Generated master/slave patches: one source sheet or one validated wall pair → one meshed barrier with two patch sides. The compiled graph records which source face and fluid side produced each patch. This is the mapping a side-specific BC needs; two patch names do not establish a wall pair in reverse.

The derived contour index uses `sourceEdgeUid`, `sourceFaceUid`, component UID, a canonical source-contour vertex/segment hash and the owning STL triangle-set hash. Geometry-generated rectangle edges can use stable names such as `/edge/bottom`; an arbitrary imported mesh must label contours in its owning construction. A triangle ordinal or an AABB is not a stable edge ID. The project declaration contains only references; the run-time index supplies the actual geometry and is checked against the exported bytes.

The *liquid* non-causal network remains compiled from saved pipe/device ports into its existing `.liquidrun` node/edge incidence graph and PyTorch DAE. Geometry contacts here do **not** create liquid hydraulic edges. A liquid port connection and its physical pipe length must still come from `.cfdproj` construction connectivity. The air source graph and liquid graph share project UID/hash and object UIDs, allowing heat bindings to be checked without making STL proximity a fluid-connection rule.

For strict V1, the compiler must find a unique source ID for **every** prepared dcCart `internalBaffle` region in every faceZone in scope; a faceZone with one anonymous or wrong-representation region fails as a whole. The existing 354-row Shinaide provenance is enough to attempt this admission check for its 74 internal regions, but its seam/source-support RED and mesh quality failures remain. This ADR does not put `geometryTopology` into that case or claim it passes.

## Geometric admission and meshing invariants

All geometry below is in metres and Y-up. Tolerances are a **versioned compiler/mesher validation profile**, recorded numerically in the run and receipt; they are not user-adjustable magic distances in the project that can be raised to make a failed case pass. The profile records separate limits for source planarity, normal parallelism, pair-thickness error, projected symmetric-difference area, contour distance, meshed-face normal displacement and source-support deficit. The first implementation must freeze and test actual values against export precision and positive fixtures before accepting cases. There are four separate predicates, never one combined “near” predicate:

1. **Ownership / shape.** Verify `.cfdproj` construction/face IDs; exported STL bytes and region uniqueness; finite, nondegenerate triangles; each strict source face's planar connected-component partition independent of triangle order; exact region/faceZone coverage. Duplicate face IDs or an unlabelled part of a strict zone fail.
2. **Wall-pair geometry.** Require two distinct planar source skins. Using one canonical unit normal from the selected reference face, test normals parallel up to sign, both planes internally planar, and `abs((p_b - p_a) · n)` versus `thickness_m` for *all* triangles/vertices. This is translation invariant and detects a tilted wedge rather than pairing only a midpoint. Project both triangle unions onto the reference plane and compare true footprint coverage, including holes, by symmetric difference and contour boundary deviation. AABB is a fast reject only. Partial overlap or same AABB with complementary triangles fails and must be split/re-authored.
3. **Independent/contact geometry.** For an intentional fluid gap, verify the minimum separation over the *overlapping supported footprints*, no face crossing, and local mesh resolution. For an edge contact, verify full source contour coverage by the other original contour/face, including endpoints; a partial edge requires a separately labelled contour segment. Contact does not authorize surface movement or area transfer.
4. **Source-support at every mesh stage.** Keep immutable original source triangles and track source face/component IDs through STL rebucketing, first-layer welding, cut, merge, MPI partition, `createBaffles`, and final patches. For every tagged mesh-face polygon, its full orthogonal projection (not just its centre) must lie in the union of its own original source component triangles, with bounded **normal** displacement checked separately. A declared wall pair may use only the selected reference face's supported footprint. An edge contact never relaxes the face-support rule. On first violation, stop with source UID, STL hash, stage, offending face/polygon, in-plane excess and normal offset. Then run existing coverage and strict `checkMesh -allTopology -allGeometry`; a valid topology graph alone never permits a CFD solve.

The mesher's expected source area is the union of validated source components, counted once per zero-thickness source sheet or per declared wall pair, not once per `faceZone` or once per near plane. The final tagged area must neither miss source-supported fluid interface nor spill outside it. Parallel ranks aggregate ownership exactly once; compare deterministic per-source measures before zone totals.

Component admission also needs a robust numerical predicate contract. Parse
and analyse the **same** STL byte snapshot whose SHA-256 was checked; a second
file read after verification can silently switch the geometry. In-plane
projection alone cannot establish 3D edge connectivity: two edges separated
by a sub-tolerance normal or tangent gap must be rejected as ambiguous, not
welded into one component. Positive triangle overlap must not be hidden by an
absolute area threshold larger than admissible triangles, while a genuinely
shared edge must not be misreported as overlap because floating-point polygon
clipping leaves a tiny sliver. Area totals and hashes must be invariant to STL
triangle order. These are admission tests, not settings to tune for Shinaide.
The current STL writer rounds coordinates to six decimal places. Exact
coplanarity of those *rounded* decimals alone rejects a valid rotated plane,
while a tolerance large enough to admit it could hide a distinct sub-micron
layer. Strict admission must therefore retain the unquantized source plane,
component and transform from `.cfdproj`, record the export precision, and
prove each STL triangle lies within that source's bounded quantization error.
It must not use export rounding to weld two separately identified source
components or claim that their fluid gap is resolved.
The shared STL writer's pre-ASCII callback sees world-transformed
`BufferGeometry` coordinates, which may already be Float32. For sub-micron
seams, retain the construction's double-precision planar source before
`ShapeGeometry`, then trace it through triangulation, world transform,
orientation overrides, and final STL file regrouping. Door, through-zone and
custom-region exporters also have hand-written triangle paths, so a writer
callback alone is incomplete. The trace, exported UID map, STL SHA-256 and
`.cfdproj` content hash must come from the **same final export pass**; a
preliminary STL generated while building a manifest is not authoritative.
The legacy HTML exporter shares the geometry writer but does not currently
bind the React source UID/hash inventory, so strict admission must verify its
own final package rather than assuming the two paths are equivalent.

## Derived files and receipts

The **editable** declaration lives only in `.cfdproj.geometryTopology`. The React compiler derives and freezes these additions; their exact field spelling is a proposed interface, not currently implemented:

- `.cfdrun.geometryExport.sourceSurfaces` remains the export-time face binding. New `.cfdrun.geometryExport.topology` holds `version`, `coverage`, the resolved relation maps, the per-source component/contour index (or its content-addressed artifact), the canonical graph SHA-256, and the validation profile. `sourceProject.contentHash` continues to bind the same project snapshot. The source graph includes STL byte hashes; after rebucketing the backend checks each source maps to **exactly one** actual `(STL, local region)` and emits native dictionary entries for both identities and validated relations. No native parser inference fills a missing relation.
- `.liquidrun.source_project.sha256` remains the same project snapshot; add a `geometry_topology_sha256` provenance field, and keep the existing physical `nodes`, `edges`, `object_bindings` self-contained. Liquid execution need not read STL, but a hybrid run requires the topology/project digest to match the `.cfdrun` used for air heat transfer. No geometry adjacency becomes a DAE hydraulic edge.
- A mesh-source receipt records source project SHA-256, graph SHA-256, original and prepared STL path/hash mappings, versioned tolerance values, compiled pair/gap/contact counts, rejected/accepted source-component counts, mesher binary/version, five `polyMesh` file hashes, per-source support/coverage results and final `checkMesh` status. `solve-only` rejects a missing/mismatched receipt or any failed gate before OpenFOAM starts. The current hybrid and ordinary source-surface receipts provide a base but do not yet attest these geometry invariants.

The digest is over canonical serialization with sorted IDs, numeric SI values, source triangle hashes and *all* relation fields. Both run inputs derive it from the same in-memory project revision. Never invent a pair in a post-hoc `.cfdrun` or by patching only an OpenFOAM dictionary. Save/reopen/export tests compare project hash and topology digest across React and backend.

## Compatibility and failure policy

Absent `geometryTopology`: preserve legacy parsing and behavior, including current `sourceSurfaces` provenance when present. Label receipts `legacyGeometryTopology` and do not claim an explicit-pair or source-support guarantee; current strict `checkMesh` and solve gates still apply. Existing files are **not** silently migrated or treated as if their near planes were physical pairs. A saved `geometryTopology.version` other than 1, unknown fields, dangling references, half-labelled strict faceZone, mixed geometry regimes, unresolved near-overlap, invalid pair/contact, unsupported custom contour, changed STL hash, or unsupported source polygon must fail **before** a solver. Opt-in strict V1 cannot fall back to legacy on error. Re-exporting a project without the required geometry relation is not a repair.

This V1 purposely does not model a finite solid volume, arbitrary curved paired skins, a CAD-wide Boolean adjacency solver, unknown NV PEK02 field topology, or a liquid pipe graph as geometry contacts. Those cases need an explicit later contract, not a geometric-nearness exception.

## Alternatives considered

1. **Keep the 0.1 mm/5 cm heuristics and tune them.** Rejected: independent B06/B07, genuine double skins, complementary triangles, and translated opposite normals require different answers from the same distance/AABB test.
2. **Use only source UIDs, with all different UIDs independent.** Insufficient: a genuine two-sided wall can create a sub-cell fluid wedge, and UID inequality does not prove fluid occupies the gap.
3. **Use explicit relations atop derived source identity (chosen).** Small project-side change, deterministic failure on ambiguous geometry, and receipts that can be reviewed. It requires authoring relations for real double skins and a robust polygon-support validator; it cannot fix the current seam or other mesh defects merely by adding metadata.

## Implementation slices and acceptance tests

1. Add V4 project validation and React save→reopen→export identity tests for the **optional** strict section. Extend strict V2/V3 schemas and canonical digest; fixture with unknown field, dangling UID, duplicate pair side or bad thickness must fail. No Shinaide edit.
2. Compile source face/component/contour registry from the existing exported STL and construction UIDs; reject mixed annotation within one faceZone, repeated face UID on distinct regions, nonplanar same-UID strict source and order-sensitive component grouping. Test the 74/74 Shinaide audit as a *coverage* positive control only.
3. Native relation admission tests: disjoint B06/B07 28 µm planes remain two supported sheets; synthetic two-skin 28 µm wall becomes one cut **only with** explicit pair; same footprint 20 mm independent gap remains two barriers when grid-resolved; ambiguous undeclared pair, triple/chained pair, translated opposite normals, tilted wedge, same-AABB complementary half-triangles, partial footprint and cross-zone half-annotation fail deterministically. Permute triangle/file order and MPI count where applicable.
4. Stage support tests: retain current two-orthogonal-sheet near-grid RED and far-grid positive control; once fixed, `02baffles` and every later stage must pass full-polygon source support. The con_f/Raised_Floor final-face fixture must reject the four unsourced faces. Valid declared edge contacts do not make out-of-source faces legal.
5. Receipt/solve-only tests: mutate only project relation, original STL, rebucket mapping or polyMesh; each must reject before CFD. Then require per-source coverage, full `checkMesh`, positive wall heat-flux sign and a real wind-side solve before claiming Shinaide hybrid prediction. No GPU/OV task is implied.

Likely edit points for those slices are `src/frontend/shared/js/cfdproj_schema.js`, React's typed project and `cfdrunBuild.ts`, `src/frontend/shared/js/liquid_graphgen.js`, strict `cfdrun_schema.py` / `liquidrun_dae_schema.py`, `source_surface_dict.py`, `stlgen_runner.py`, and native `dcCartSurfaces` / `dcCartCutCells`. The current schema example is a proposed **section value**, not an edit to Shinaide or a second `.cfdproj` file.

### Relevant current evidence

- [Source identity implementation and limits](physical-source-surfaces-v1-2026-09-27.md)
- [Pairing counterexamples](../archive/dccart-source-validation-2026-09-27/dccart-pairing-contract-counterexamples-2026-09-27.md)
- [B07 source-region candidate review](../archive/dccart-source-validation-2026-09-27/dccart-source-region-review-red-2026-09-27.md)
- [First unsupported cut stage](../archive/dccart-source-validation-2026-09-27/dccart-stage-support-trace-2026-09-27.md)
- [Local source-support RED](../archive/dccart-source-validation-2026-09-27/dccart-source-support-red-2026-09-27.md)
- [Strict schema reference](../reference/cfdrun-v2-schema.md)

## Incremental implementation status

The pure backend `source_relation_graph.py` validates the declared relation
structure against exported source-face UIDs and, when supplied, an explicit
contour-to-face registry. Its sorted `declaration_sha256` is only the identity
of those declarations and UID references: it returns
`status=unverified_geometry` and never authorizes meshing. No wall-pair
footprint, fluid-gap resolution or source-support proof is implemented by
that module. The final topology graph digest described above must also bind
the verified source triangles/components and geometric validation profile.
