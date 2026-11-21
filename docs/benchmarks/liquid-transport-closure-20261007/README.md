# Liquid transport literal carrier closure — 2026-10-07

The saved Shinaide project exported an underground transport carrier with 87 unpaired directed exterior edges. Transport-only CSG classification now uses 1e-9, with that precision carried by split/clone/affine support planes. Default CSG epsilon remains 1e-5. Export quantization, closure checks, geometry, UID and the fixture are unchanged. No triangle is filled, removed or silently repaired.

The frozen literal ASCII STL has 130106 facets and two source regions (m_31/m_32). Literal finite coordinates, positive areas, oriented edge closure and positive volume passed an independent reader. Individual source regions need not close: dcCart consumes their combined carrier. Backend wall staging and strict native mesh are separate acceptance steps.

Root verified all 113 frozen payloads and streamed all 87 regular lossless members against SHA/size records. frozen-classification preserves original 87-edge failures, the unsuccessful plane-only candidate, historical raw controls, final inputs, unchanged authored case and accepted literal carrier.

Current normal source: 11 selected tests PASS (48.199 s). First build exposed three TypeScript unknown-entry errors in the new test helper; raw RED retained. Root added explicit runtime shape assertions and a narrow helper type, with no production change. Two affected project tests then PASS (40.845 s), and TypeScript plus production build PASS (37.844 s). The six precision and three independent closure controls are unchanged. These are selected scopes, not an unfiltered suite.

From src/frontend/react, use the supported repository Node/npm environment:
npm test -- test/shinaideHybridCompiler.test.ts test/transportCsgPrecision.test.ts test/literalCarrierOracle.test.ts
npm run build
Historical absolute paths identify the actual 40901 experiment; they are not portable bootstrap configuration.

Remaining: fresh unfiltered frontend suite, new native mesh/checkMesh with wall staging, <=300 s acceptance, wind steady/transient solving, final main convergence/cleanup. Native signed-crossing candidates and OV pixel verification are not accepted by this receipt.
