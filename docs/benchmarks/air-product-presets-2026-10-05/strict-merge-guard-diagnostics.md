# Strict merge guard diagnostics

These opt-in candidates were tested through real React exports and the native mesh API. All four runs were rejected by unchanged full topology and geometry checks. The source patch is preserved for diagnosis and removed from active C++.

| Case / factor | API seconds | Bad face tets | Small determinant | Concave or planar | Low weight | Low volume ratio |
|---|---:|---:|---:|---:|---:|---:|
| Tri baseline | 8.148 | 1914 | 107 | 16617 | 1062 | 1369 |
| Tri polygon join only | 9.141 | 1898 | 107 | 16617 | 1062 | 1369 |
| Tri all-vertex union only | 7.090 | 1943 | 113 | 17194 | 1561 | 1562 |
| Tri both | 8.114 | 1937 | 113 | 17194 | 1561 | 1562 |
| Target liquid baseline | existing identical mesh | 35 | 0 | 125598 | 54 | 46 |
| Target liquid both | 89.806 | 75 | 0 | 127444 | 94 | 104 |

The vertex-plane test prevents centroid-inside but nonconvex agglomeration; it leaves more thin cells requiring a different conservative repair. The polygon test reduces some face fan failures but leaves most unresolved. Neither is a production solution or an accepted seed. Each stage directory binds source patch/hash, environment, native artifact hashes, input/export hashes, raw SSE and complete API outcome.
