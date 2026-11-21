# Dimension title unit correction — IT only

The generic Inspector and polygon floor dimension headings now use the existing project input unit. Chinese and English translations interpolate {unit}. The field conversion code, canonical metre values, schema, project files and material implementation are untouched: reversing the four precise text replacements exactly reconstructs every before-source byte.

The actual Inspector DOM regression covers Chinese/English × m/cm/mm. For a controlled Thermal_block size of 0.125 canonical metres, displayed inputs remain 0.1250 / 12.5000 / 125.0000; rendering does not mutate the project. The corrected pre-change run had 2 metre passes and 4 cm/mm heading failures. The post-change run has 6 passes. The floor heading shares the same locale key and passes its already-existing unit; this packet does not claim a separate floor DOM test.

Existing tests: lengthUnits 7, InspectorDelete 6, i18n 9 — all 22 pass. The i18n scan verifies referenced keys remain present. Raw stdout/stderr and exact commands, affinity and owned temporary/cache environment are retained.

The first draft incorrectly accessed findConstruction(...).geometry instead of the returned wrapper's .model.geometry. Its 6 failures are retained and explicitly excluded from product RED evidence; see FIRST_DRAFT_CLARIFICATION.md. No baseline logs were replaced.

Production changes are limited to these four files:

- src/frontend/react/src/components/Inspector.tsx
- src/frontend/react/src/components/FloorPolygonSection.tsx
- src/frontend/shared/i18n/zh-CN.json
- src/frontend/shared/i18n/en-US.json

Proof-only tests/config live in this isolated evidence folder. No production build, typecheck, Git operation, service action, browser operation, project/case edit, mesh/checkMesh/solver run was performed. ROOT's real UI acceptance is pending. Generated npm/node/temporary caches are excluded from the manifest.

source-identity.json binds original and final four sources; consumed-source-identity.json binds the inspected actual store/i18n, existing tests and Vite configuration. All declared files were hashed after the final source-hold assertion.
