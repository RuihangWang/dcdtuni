# Independent frozen pose/ports source review — v3

2026-10-06. ROOT-authorized read-only review of the eleven frozen source paths, original producers, actual input bindings, existing test scripts/logs and loader/math call sites. No test/probe, compiler, build, browser, service, business/solver/mesh, source or Git action was performed by this reviewer. CPU28–31/CUDA empty; all new evidence only this assigned IT directory. Earlier preparation checkpoints are preserved.

## Result and exact acceptance boundary

No concrete P1/P2 blocker was found within the reviewed pose/local-point/local-direction/Rack-CDU port numerical ownership slice. The shared kernel has one implementation for these numerical functions, domains delegate to it using registry kind and plain source records, and the legacy point/direction/port helpers create Three vector carriers from the same numeric results. No numerical experiment or browser acceptance is inferred from this source review.

This does not mean the full scene/geometry/physics/export architecture is now renderer-independent. Existing `geometry_pure.js:359–373` applyModelTransformToObject still normalizes the carrier Quaternion with Three; that function is byte exact versus the prior source, and its input clone/world transform now delegates to the kernel. Other renderer-specific Euler/object/geometry math and scene-derived STL remain outside this slice. The reviewed single owner is the declared domain/local/port kernel, not every display operation. RAM cfdrun/graph compiler/material ownership, complete ports of all equipment kinds, OV/USD adapters and browser operation remain separate later work.

## Frozen identities and scope integrity

Author manifest SHA **b7cb85d6d821365a412c3c9af833cb355d6786b45292f8b5161f55479371b013**: all 36 payload SHA/byte counts independently match, total 4,186,505 bytes. All eleven `after/` bodies match `source-final.json` and current frozen IT source bytes. All six `before/` bodies match declared baseline hashes. Reconstructing the eleven unified diffs directly from before/after bytes yields exactly `pose-ports-scoped.diff`, SHA `2d57544726f80de682adc8ba4efbae874c81ad09eddce75cd2e637f02644fb65`.

Core SHA **8f0c9b99f0a5079ad2bc25a0ea0dad287417345c7dd3d9981313352831c22847**, 7,772 bytes. Domain BaseEntity SHA `70ac8028d3599ba0b8a9ca1b9297efb3eefbd53fe50dbe3e9a52a6aca17fba6d`; typed bridge `d330e3c33d0001666c04956600fc8c088a956fe45c0789e748c4bb89156e8d40`. Exact eleven body and six baseline identities are in source-and-golden-identities.json.

Only the authorized eleven files are represented by the scoped patch: shared kernel+declaration; rack/default and pure wrappers; one HTML script entry; typed bridge/domain methods; geometryBuilder and independent cfdprojModules load order; targeted test and fixture. The cfdprojModules change is solely a kernel import immediately before rack_state. No serialized cfdproj record, graph arithmetic, inputUnit conversion, exporter geometry or UI clone parameter semantics are silently revised.

## Numeric ownership and parity by source inspection

`model_pose_ports.js:17–46` allocates a new default/clone, with per-component Number.isFinite on quaternion/scale: strings/null/nonfinite components fall back, finite negative/zero including -0 are retained. Model transform always comes from geometry, never the stale record.transform mirror. Coordinates/dimensions use Number(value)||0 (`61`, `83–85`, rack/CDU positions), retaining old string/null/NaN coercion and Infinity propagation.

Quaternion normalization `49–53` is the same sqrt(sum of component squares), zero=>identity and reciprocal multiplication as installed Three0.184 Quaternion.js623–659. Infinite/NaN raw quaternion components are filtered by clone before this math. Large finite components can overflow the squared norm to Infinity, yielding zero components after reciprocal multiplication; tiny finite components can underflow the norm to zero and use identity. These existing mathematical branches are preserved by exact arithmetic inspection, not claimed newly tested across every extreme.

Rotation `68–78` matches Three0.184 Vector3.applyQuaternion's exact cross expansion/order. Point `89–94` scales, rotates then adds numeric anchor. Direction `96–102` scales, rotates then divides by sqrt(length-squared)||1 with reciprocal multiplication, matching Vector3 normalize/divideScalar. Zero direction stays zero. Negative/nonuniform scale direction behavior remains the old scaled-vector semantics; no inverse-transpose or supposedly corrected normal matrix is introduced.

The ordinary fixture test `domainPosePorts.test.ts:51–79` keeps 28-value Float64LE comparisons per case. Its additional Infinity fixture assertion `36–44`, `122–136` only uses NaN classification when the original recorded expected value is NaN; every other value, including signed zeros and Infinity, still compares exact bits. The retained first related log records positive/negative NaN sign difference; final code does not change ordinary fixture expected values or weaken ordinary finite assertions. The original nonfinite bits are preserved. JSON project inputs do not represent numeric NaN/Infinity; classification is the explicitly limited standalone arithmetic edge-case contract.

Defaults, clones, poses and frames are owned new objects. API export is frozen, but caller outputs remain usable new values. Functions read fields/coerce scalars and assign only local result objects; no model/project write or full-project deep clone is added. Domain methods use the current referenced record. The preexisting domain record itself is not newly frozen or copied; store ownership constraints remain the foundation's prior boundary.

## Ports, definitions and physical units

Core `105–139` transplants Rack manifold formula and branches without reinterpretation: both-right, falsy/default both-left, other truthy layout opposite branch; diameter/spacing only finite raw number else defaults; exact outlet and bottom; slot product **>1e-6** and original height fallback. Existing outer liquidGlobals endpoint's >=1e-6 and graph thresholds are not changed. Negative/zero diameters/spacing continue to pass through the numeric geometry function, independent of physical/visual validation and clamps.

Core `142–167` supports declared rack/cdu only, refuses unsupported kind/missing geometry, lowercases role and preserves Rack exact manifold_in supply versus CDU's old return branch. CDU ends remain ignored; Rack falsy end defaults top and only exact bottom selects lower. These are compatibility boundaries, not a new formal physical port schema for all equipment.

BaseEntity.ts `74–76`, `80–82`, `116–118` delegates directly. Rack/CDU select `this.definition.kind`, allowing normal v5 records with no record.kind; the legacy adapter intentionally continues to dispatch using its enriched model.kind. Test cases verify missing record.kind through DomainProject registry admission rather than writing it into source. Stable uid/path/physical records and cfdproj codec are untouched by these methods.

Storage coordinate semantics and graph _worldToMeters remain unchanged. This kernel performs no unit conversion or external Z-up reorientation. The previously read current project UI/graph chain stores metres and handles inputUnit at display boundaries; DSX/SimReady field-specific flow/temp/pressure units are future adapter concerns, not conversion factors added here. The neutral project-coordinate descriptor does not claim every physical declaration is SI.

## Loading and standalone behavior

UMD shell `6–14` installs a frozen API through CommonJS export or compatibility globals; the numeric factory `15–179` reads no renderer, window, scene or project/store global. Multiple runtime shells still share the same source implementation, not mutable project state.

Typed bridge modelPoseKernel.ts `1–14` uses namespace import (valid for browser UMD side effects and CJS interop), then CJS default or registered globals. Missing kernel throws instead of reimplementing math. It does not need THREE at runtime. Rack/default and pure wrappers lazily resolve the same global or CommonJS module, never retain duplicate numerical fallback code. Missing THREE still causes the old point/direction/port carrier helpers to return null before numeric calls; direct numeric/domain functions remain available.

Classic HTML `1244–1249` loads the kernel before rack_state and geometry_pure. React geometryBuilder `125–137` and cfdprojModules `30–31` likewise import it before dependent defaults. Earlier lazy graph/pipe scripts preserve their existing late-use contract. The author's CJS child-process test requires core/rack/pure without global window/THREE, checks owned defaults/frozen API and carrier-null boundary. This reviewer read its exact script and the recorded passing output, but did not execute it. These are correct source load paths; a production Vite build/browser run has not occurred in this slice and cannot be reported as passed.

HTML `2921–2925` still invokes `cloneModelTransform(rawTransform = {})`. Its exact function bytes match before/after; this preexisting parameter-clearing behavior is explicitly kept. It is not an accidental new kernel regression or quietly repaired adjacent bug.

## Original golden binding and exact recorded coverage

The original producer SHA values (`rack_state fc37b647...`, `geometry_pure 0444bcc3...`) agree across pre-edit copies, independent preparation identities, fixture producerSources and golden-producer-binding.json. Installed Three Quaternion/Vector3/package hashes still match the independently read preparation baseline.

LiquidSingle SHA **6df7ba7846b6b86c2a6c910d89a386a663c2a2ba553375c29c03641b2d764883** and Shinaide SHA **594558e17f9618bf216fefd1f8788c31f7332b1bca00b6a7cadc25247316e626** independently match actual source files. The four first Rack/CDU model field projections selected by capture script exactly match the fixture. This is four actual models plus six synthetic edge variants, not all entities or a full-case physical solve. The per-record JSON.stringify hashes are declared capture metadata; they were not independently re-executed.

Main capture script `8`, `11–18`, `38–51` reads the then-live original producers and actual files, uses actual installed Three, records producer hashes and values/bits, removes kind for domain fixtures, and writes ten cases. Its hash assertions and copied originals are consistent with before-edit capture; this reviewer did not independently witness/replay that past execution. Extra nonfinite capture explicitly reads **before/** originals (`4–6`), appends only nonfiniteOriginal, and preserves the ordinary ten-case block. Removing that extra final block from final fixture gives 28,794 bytes, matching original capture log; the reconstructed ordinary JSON is retained with independent SHA. No expected data re-recording from the new kernel is present in either capture script.

Baseline red log preserves ten missing-method failures and one old-control pass. First kernel log records eleven passes. Final author log records 125 passes/ten files, including thirteen targeted pose/port cases; tsc log is empty with recorded return 0. The standalone CJS test and nonfinite classification test are the two added targeted cases. The meaningful ordinary corpus is retained; finite bit comparisons were not swapped for tolerances. Existing logs/scripts are evidence leads, not this reviewer's test execution or product acceptance.

Next accepted engineering step may use this numeric core for further domain/adapter ownership. A separate authorized build/browser check, further physical ports/graph compilation, owned cfdrun RAM snapshot and OV carrier integration are still required by the larger goal.
