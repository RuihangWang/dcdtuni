# dcCartMesh memory profile — why it eats RAM, is it reasonable, does it leak?

**Date:** 2026-09-21
**Trigger:** on 2026-09-18 a dcCartMesh run exhausted the host's 251 GB, SSH
timed out and the machine wedged. This note measures where the memory goes,
compares against snappyHexMesh, and answers the leak question. All numbers
are RSS sampled every 2–20 s over the live `mpirun` ranks
(`/tmp/mem_*.csv` method: `ps -C <binary> -o rss=` summed).

## 1. The shape of a run (Base604, np16, cap 24M, ~19.7M global leaves)

| phase | t (s) | sum RSS | per rank | behaviour |
|---|---|---|---|---|
| refine + carve | 0–50 | **peak 145.6 GB** | 9.44 GB | transient, released after |
| assemble | ~120 | 85 GB | 5.3 GB | plateau |
| clip | 300–1000+ | 99.4 GB | 6.2–6.4 GB | **flat for 12+ min** (±0.1 GB) |

The carve peak is released (145 → 85 GB), and the clip plateau does not grow:
**no classical leak**. The danger is the peak and the plateau size, not
unbounded growth.

## 2. Where the bytes are: ~8 KB per leaf per rank

Same-case, same-binary comparisons (Tri_angle):

| run | leaves/rank | peak per rank | per leaf |
|---|---|---|---|
| dcCart np4 | ~90 k | 0.36 GB | ~9.5 KB |
| dcCart np16 | ~22 k | 0.21 GB | ~8 KB |
| Base604 np16 | 1.2 M | 9.44 GB | ~7.9 KB |

> **Erratum (same day, later).** The "independent of np" conclusion below is
> wrong: every rank keeps the whole octree, carve and pass-1 corner topology,
> so the machine-wide peak RISES with the rank count. Slab (329 k octree
> leaves) peaks at 0.80 / 1.60 / 4.56 GB on 1 / 4 / 16 ranks (RSS summed,
> 1 s samples). The fitted law used by the guards is
> `leaves × 1.6 KB + ranks × (100 MB + leaves × 0.35 KB)` (× 1.15), which
> reproduces this table's Base604 point (165 GB predicted vs 145.6 GB measured)
> and the Slab points; see DCCARTMESH.md "Memory". Every run now prints its
> own `Memory: peak RSS sum …` line for re-fitting.

Per-rank memory is proportional to **that rank's** leaf count (np-inverse →
no global octree duplication; the column split works). But the **machine-wide**
peak is `global leaves × ~8 KB` and is therefore **independent of np**: adding
ranks cuts wall time, not RAM. 19.7M leaves ⇒ ~150 GB no matter how many
ranks you throw at it. That is the mechanism behind the 09-18 wedge: a
config whose leaf count times ~8 KB exceeds MemAvailable.

What carries the ~8 KB/leaf: per-leaf carve auxiliaries (intersection loops
and point clouds for the shells a leaf touches — the room shell touches every
leaf), per-leaf side/anchor classification arrays, the clip pass's per-cut-face
loop storage, then the resident cutMesh (faces as `vector<vector<label>>`,
points, owner/neighbour/patch/zone, cellRank/cellLeaf/cellPiece, alive flags)
plus ~35 % halo duplication (Base604 np16: 918 k live + 254 k halo leaves).

## 3. Is that reasonable? snappyHexMesh comparison

Same case (Tri_angle), same np4, comparable final cell count:

| engine | peak per rank |
|---|---|
| snappyHexMesh | **0.19 GB** |
| dcCartMesh | 0.36 GB |

dcCart runs ~1.9× snappy at small scale and ~8 KB/cell vs snappy's ~1.9 KB/cell
at Base604 scale. Part of the gap is inherent to the method — true cut cells
keep exact cut polygons and per-leaf shell intersection data, where snappy
keeps a snapped cell per background cell — and part is optimisable (below).
snappy also decomposes *before* castellation, so its total RAM falls with np;
dcCart's total does not (Section 2). Both facts should be stated plainly:
the method is heavier per cell by design, and its RAM does not parallelise
away.

## 4. Leak verdict

No. Plateaus are flat over 12+ min of clip work; carve/assemble transients are
released; repeated rounds of merge/rekey do not accumulate (round-k structures
are function-local). The 09-18 event was a **capacity** failure (peak > RAM),
not a leak.

## 5. Recommendations (in priority order)

1. **Preflight memory guard.** Before carve, estimate global leaves (the
   refine pass already logs `maxGlobalCells … reached at N leaves`; make a
   dry count cheap or reuse the previous run's) and require
   `leaves × 9 KB × 1.25 < MemAvailable`, else refuse with an actionable
   message (lower `maxGlobalCells`/levels or raise np for time, not RAM).
   Env override `DCCART_MEM_GUARD=off`.
2. **Host protection independent of the mesher.** Launch mesh jobs under a
   cgroup scope (`systemd-run --scope -p MemoryMax=…`) so the OOM killer
   selects inside the job and sshd/the desktop survive a mis-estimate.
3. **Cut the per-leaf constant.** Free each shell's carve auxiliaries
   immediately after that shell's pass (the room shell is the biggest single
   allocation); flatten cutMesh faces into an arena (removes per-face heap
   headers); stream clip loops per face batch instead of resident per-cut-face
   storage. Target ≤ 3 KB/leaf.
4. **Document the RAM law** in DCCARTMESH.md: total peak ≈ global leaves ×
   ~8 KB, np-independent; size `maxGlobalCells` from host RAM, not from time.

## 6. Reproduction

Samplers and curves: `/tmp/mem_b604.csv`, `/tmp/mem_tri_np4.csv`,
`/tmp/mem_tri_np16.csv`, `/tmp/mem_snappy3.csv` (ephemeral); method one-liner
in each header. Jobs were triggered through the server REST API
(`/api/run-mesh` with `meshOnly`/`solveOnly`) per house rules.
