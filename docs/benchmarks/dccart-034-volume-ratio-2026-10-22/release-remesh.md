# The ten release cases with `baffleCrumb` (2026-10-22)

Re-meshed with the rebuilt dcCartMesh, mesh only, each through the scripted
API (`backend.api air --mesh-only`) in its own scratch case directory and at the
rank count of its 2026-10-21 receipt (`release-remesh.sh`; summarised by
`release-remesh-summarize.py` into `release-remesh.json`).

| Case | Ranks | `baffleCrumb` candidates / handed | Strict gate | Mesh SHA-256 equal to 2026-10-21 |
|---|---:|---|---|---|
| Base_leakage | 8 | 0 / 0 | accepted | yes |
| Tri_angle | 8 | 0 / 0 | accepted | yes |
| Slab | 8 | 0 / 0 | accepted | yes |
| STDCT | 8 | 0 / 0 | accepted | yes |
| CMI-China | 8 | 0 / 0 | accepted | yes |
| Base604 | 16 | 0 / 0 | accepted | yes |
| Base2-9 | 16 | 0 / 0 | accepted | yes |
| DAML-Testbed | 8 | 0 / 0 | accepted | yes |
| Shinaide | 8 | 0 / 0 | accepted | yes |
| Shinaide_Liquid | 8 | 0 / 0 | accepted | yes |

Every release mesh is byte for byte the one its release receipt
(`docs/benchmarks/air-release-cases-2026-10-21/summary.json`, `meshSha256`)
records: the round admits nothing in a mesh that already passes strict
checkMesh, and admitting nothing changes nothing.
