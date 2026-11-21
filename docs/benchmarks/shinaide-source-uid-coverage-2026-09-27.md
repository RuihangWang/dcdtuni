# Shinaide dcCart source UID coverage (CPU-only, no mesh solve)

- Native probe: `/mnt/lv/home/litianyu/benchmarks/dccart_source_coverage_20260927/native-coverage.log`; source dictionary and prepared STL: `/mnt/lv/home/litianyu/benchmarks/dcdtuni_shinaide_source_dict_354_full_20260927/Mesh`.
- Native parser accepted 354 source identities; geometry signature unchanged.
- Native `dcCartSurfaces::surfaceInfo` reports 5 internalBaffle surfaces / 5 faceZones / 74 local STL regions.
- All 74/74 internal regions have unique `sourceFaceUid` and `representation=zeroThicknessSheet`; missing, wrong representation, duplicate UID: 0/0/0.

| faceZone | prepared STL | internal regions | missing UID | wrong representation |
| --- | --- | ---: | ---: | ---: |
| Raised_Floor | wallBaffle_floor_Raised_Floor.stl | 1 | 0 | 0 |
| merged_perforatedBlankingPanel_3U | merged_perforatedBlankingPanel_3U.stl | 14 | 0 | 0 |
| merged_perforatedBlankingPanel_4U | merged_perforatedBlankingPanel_4U.stl | 42 | 0 | 0 |
| merged_perforatedBlankingPanel_5U | merged_perforatedBlankingPanel_5U.stl | 14 | 0 | 0 |
| merged_wall_baffle | merged_wall_baffle.stl | 3 | 0 | 0 |

- Broad-phase screen across all 354 UID-bearing source regions: same axis-aligned plane, plane distance ≤ 0.0001 m, and positive projected AABB overlap. Distinct-UID candidates: **0**.
- This screen is not a pairing proof: it does not check triangle coverage, normals, two physical wall sides, holes, or intent. Zero candidates means only this particular broad-phase did not find a close overlapping distinct-UID pair.
- B06/B07 counterexample: corresponding server front faces lie 28 µm apart along z but are 0.72 m apart in x, so projected AABB overlap is zero. An offset-only plane bucket would wrongly group them.

| B06/B07 front | z-plane distance (µm) | x-gap (m) | projected overlap (m²) |
| --- | ---: | ---: | ---: |
| server_1 | 28.0 | 0.720 | 0.0 |
| server_2 | 28.0 | 0.720 | 0.0 |
| server_3 | 28.0 | 0.720 | 0.0 |
| server_4 | 28.0 | 0.720 | 0.0 |

**Migration conclusion:** This Shinaide 354-record input meets an opt-in all-internal-baffle UID/representation coverage gate. Legacy inputs still need their own path. UID coverage and this AABB screen do not authorize treating near sheets as physical pairs or claiming the Shinaide mesh passes `checkMesh`.

Machine-readable inputs, all candidate records, and SHA-256 hashes: [JSON record](shinaide-source-uid-coverage-2026-09-27.json). The audit script is retained with the native log in the isolated 40901 evidence directory as `analyze_coverage.py`.
