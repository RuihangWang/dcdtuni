# Pose/ports independent preparation plan v2

2026-10-06. This document expands the prior read-only baseline preparation, not a review of the author's changing implementation and not an executed parity suite. The 13 baseline source identities remain in preparation-identities-v1.json. No source, test, build, business, Git, service or browser action was performed. CPU28–31/CUDA empty; new evidence only this assigned IT folder. AIR source-review closure takes priority when its new freeze arrives.

## Minimal contract

A shared stateless numeric kernel should consume the geometry record's location/quaternion/scale, return owned plain numeric tuples/frames and retain current coercion and arithmetic. GeometryEntity methods delegate to that kernel. Legacy wrappers convert plain tuples to Three.Vector3 only at the carrier boundary; renderer types do not become domain input. Model.transform mirror cannot override geometry.transform.

Keep project coordinate storage in metres, with display-unit conversion at existing UI boundaries. liquid_graphgen.js:8–38 already states storage metres, inputUnit is display-only and inputScale=1; do not multiply graph world coordinates again. DSX/SimReady's published Z-up/metres and field-specific flow/temperature/pressure metadata describe future external adapters. They do not authorize changing the current project's Y-up coordinate convention, all port-field units, gauge/absolute pressure or solver physical parameters in this narrow slice. No certification claim follows.

## Actual call-chain boundaries from the read baseline

| Consumer | Required unchanged boundary |
| --- | --- |
| sceneBuilder/applyModelTransform and Viewport move/rotate | location and quaternion/scale source truth is geometry; Three is a derived carrier |
| cfdrunBuild modelWorldTransform | same numerical transform result; RAM export remains a later architecture slice |
| LiquidEquipmentVisuals local frame then group transform | retain local frame result and separate visual radius clamping; no duplicate world transform |
| liquidGlobals manifoldEndpoint and snapping | retain outer rack/liquid-mode guards and exact slot threshold semantics |
| liquid_pipe_renderer getRackPort/getCduPort, couplers and autorouting | obtain same world positions/directions; existing missing-pure legacy fallback remains bounded loader compatibility, not source truth |
| liquid_graphgen header frame and server slot | retain _worldToMeters=identity scale; server slot formula uses physical .04445 independently of custom visual slotHeight |
| STL_generate.html legacy loading | graphgen/pipe_renderer currently load before pure but call helpers lazily; new numeric kernel must load before geometry_pure, without new eager consumer dependence |
| React geometryBuilder imports | numeric kernel side effect is imported before pure; no duplicate mutable domain state or need for installed renderer during domain-only import |

Legacy sequence captured: graphgen at HTML1229, pipe_renderer1231; rack_state1244, geometry_pure1248 and geometry_scene1249. Existing HTML clone wrapper2922 appears to call `(rawTransform={})`, discarding the supplied argument; this is an old source observation, not a requested automatic unrelated fix.

## Independent parity cases to verify against the original producer

The following is a bounded case plan. Expected Float64 bits must be recorded from the old producer **before edits**, bound to that source SHA and exact actual-input SHA. New numeric functions cannot generate their own expected results. Preserve original input hash after every call. All case outputs below are proposed checks, not executed observations.

| Case family | Cases and precise expected legacy semantics |
| --- | --- |
| Source precedence and defaults | missing geometry/transform/location; geometry.transform versus conflicting record-level transform; quaternion absent/partial, scale absent/partial; no shared mutable default objects |
| Component coercion | transform components numeric strings, null, NaN/Infinity, finite zero and negative; q/scale `Number.isFinite` fallback must stay distinct from point/anchor/size `Number(v)||0` coercion; inspect signed zero where arithmetic preserves it |
| Quaternion math | identity, nonunit ordinary values, exact all-zero, extremely small finite/underflow and huge finite/overflow length; original sqrt(sum of squares), reciprocal multiplication and zero=>identity branch must remain exact; Math.hypot or reordered matrix expansion is not bit-parity |
| Point transform order | finite anchor with negative/nonuniform/zero scale, rotation and point; scale first, Quaternion rotation with exact Three cross-expanded order, anchor last |
| Direction transform | arbitrary vector with rotation and negative/nonuniform/zero scale; scale/rotate/normalize via sqrt and length||1, preserve zero result; do not replace with inverse-transpose normal matrix |
| Rack layouts | both-left, both-right, opposite-sides, missing/falsy, unknown truthy; unknown truthy currently follows opposite branch, default is both-left; exact outlet mountFace versus other/missing values |
| Rack dimensions | finite zero/negative versus string/NaN/Infinity diameters and spacing; safeBottom coercion; slot and slotHeight string/null/falsy; top/bottom position and height fallback |
| Exact rack threshold | slotHeight product below, equal and above 1e-6; pure local-point uses >1e-6, liquidGlobals outer endpoint uses >=1e-6, so these must not be collapsed into one supposedly corrected condition |
| Port role/type/end | lowercase substring supply/inlet; Rack exact manifold_in; CDU manifold_in currently falls return because it lacks Rack alias; unknown role defaults return; end exact bottom only for Rack, CDU ignores end; case sensitivity must follow current normalization boundary |
| Unsupported entity | no geometry, wrong kind/unsupported entity, plain DomainEntity without pose capability: preserve current null/empty dispatch behavior and forbid fallback guesses |
| Actual models | LiquidSingle and Shinaide rack/CDU/ACU/server local/world transform + declared ports where available; enumerate identities and source paths, not only hand-made fixtures; descriptor methods must use current domain record |
| Carrier/loader parity | original helper versus numeric/domain method versus thin Three wrapper tuples; CJS require without THREE/window/scene; legacy script order and React import order; plain output owned and independent of scene globals |

Three0.184.0 original math identities are recorded in v1. Quaternion.js623–659 computes sqrt(x*x+y*y+z*z+w*w), zero resets identity, otherwise reciprocal multiplication. Vector3.js471–490 uses cross-expanded applyQuaternion; length769 and normalize792 use sqrt and length||1. These math details explain the specific extremes and arithmetic-order cases above; this slice should not silently repair old nonfinite propagation/coercion behavior.

Rack baseline geometry_pure.js310–345 and CDU353–378 are different API semantics. Rack world+local support does not imply CDU ends or role aliases become identical. The pure helper defaults differ from product UI physical clamps: keep geometry-pure numeric outputs and visual/physical validation separate.

## Frozen-source review acceptance

1. Bind all final changed source paths and exact patch to before source identities; bind original-producer fixture to pre-edit producer SHA, actual input SHA and original Float64 outputs. Read recorded author evidence, then compare source flow without claiming tests ran independently.
2. Verify numeric kernel contains no renderer/window/scene dependency, stable UMD/CJS loading and no top-level mutable project state. Domain adapter is a narrow numeric import bridge; carrier wrappers only allocate Three vectors/objects.
3. Trace geometry source truth, domain entity current record identity, default/coercion and arithmetic through all call sites above. No extra unit factor or model.transform fallback; one shared implementation for local/world pose and supported Rack/CDU ports.
4. For loader failures, exact unsupported type/null outputs and declared port role/end semantics, require honest dispatch. Do not broaden to new physical-port types, auto-geometry repair, graph compiler migration, CFD RAM architecture or DSX certification.
5. The future independent bounded replay, if separately authorized by ROOT, should reuse frozen original fixture/actual inputs and cases, not launch broad sweeps. Until then this is source/parity planning only.
