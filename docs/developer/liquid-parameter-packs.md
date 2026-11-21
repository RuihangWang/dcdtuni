# Equipment and material parameter packs

Parameter packs provide versioned, replaceable values for liquid simulation. They contain no solver code. `src/backend/liquidcooling/data/mock-demo-v1.json` is synthetic demo data and must not be described as NVIDIA/Vertiv specifications or measured data.

On an unresolved `.liquidrun` input, set `parameter_pack` to the JSON pack and bind an edge using `parameter_ref`. For example, `parameter_ref: "pipe-25mm"` fills that edge's physical `params`; its topology and explicit geometry remain in the project. `fluid_ref: "water"` similarly fills coolant properties. Explicit `edge.params` or `fluid` values override profile values; their values are recorded in provenance. Remove an override if a replacement pack should control that property.

`LiquidrunConfig` resolves profiles before compilation. The input object is not mutated. Profiles are type checked, every profile parameter declares a unit, supported compiler units are enforced, nonfinite values and unknown profile parameters are rejected. Kv uses m³/h at 1 bar; most other fields use SI. No files, URLs, or executable expressions are loaded by the resolver.

The result contains materialized physical values and `parameter_provenance`, including pack ID/revision, source (`mock`, `vendor`, or `calibrated`), pack SHA-256, effective-parameter SHA-256, and reference/override bindings. Resolved snapshots remove active pack references so loading them again cannot unexpectedly reapply a different library. To replace equipment data, replace the pack on the original project/manifest input and compile a new run. Save the original input and resolved snapshot with each run.

The source label records a declaration; it does not independently certify a vendor measurement. Limits, working ranges, uncertainty and temperature-dependent material correlations require their own validated models. Existing legacy manifests without packs remain supported and receive no invented provenance claim.

Bindings have separate `bindings.fluid` and `bindings.edges[edgeId]` namespaces. The effective-parameter hash covers coolant values and per-edge ID/type/materialized parameters in canonical ID order; visual metadata and connectivity are excluded. Run services must also hash the complete run input so a topology or scenario change invalidates results even when the material-parameter hash remains the same.

The UI/run service must use these identities to mark results stale after a parameter change; this module supplies the identities but does not itself manage frontend state.

## Verification

New tests exercise actual graph compilation, pack replacement without topology changes, explicit overrides, fluid binding, self-contained snapshot reload, invalid units/types/references, unknown fields, finite values and the shipped mock pack. Legacy schema/compiler tests remain in the regression set.

TDD evidence from 2026-09-22: before implementation, the bound pipe diameter remained absent and graph compilation failed (2 expected failing tests). After the first implementation, an added typo test still demonstrated silent acceptance (1 expected failure), then the supported-profile field check closed that gap. The final focused parameter/schema/compiler run passed 77 tests in 12.32 s. The shell's later packaging-file lookup failed because there is no `src/pyproject.toml`; that lookup did not affect the pytest result.

Independent review of `fa5e83ce` found two important input-validation gaps: numeric strings/fractional cell counts/unknown characteristics and unvalidated overrides. Eight new tests first failed, then the implementation applied one typed-field validator to profiles and overrides; strings are not coerced to numbers. It also fixed two smaller provenance issues after two failing tests: an edge called `fluid` now has its own namespace, and visual metadata does not change the physical-parameter hash. The resulting parameter/schema/compiler suite passed **87 tests in 3.25 s**. The corrected code is undergoing scoped re-review.
