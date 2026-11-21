# ROI as the unit of simulation and transport ("drag & sim")

Design produced 2026-08-09 from a four-way parallel investigation of SCANO's sampling,
the mesh cell identity, the AI worker's I/O, and kit-cae's ingestion. Every claim below
carries a file:line or a measurement; where two investigations disagreed the
disagreement is recorded rather than averaged (see F.2).

Requirement, from the repo owner:
  1. The repo's essence is DRAG & SIM - simulate ONLY the ROI, load/visualise ONLY the
     ROI. Decided at the algorithm level, not as a render-time filter.
  2. Today the whole VTU is read globally and AI inference ships the whole field. That
     contradicts the intent.
  3.1 A cache keyed on the mesh's invariant cell array (cpd_cell) emits an ROI-only VTU.
  3.2 That cache extends incrementally as the ROI moves, with no duplicate computation,
      until the whole domain is filled. (This is the AI path; it mirrors SCANO's
      sampling + boundary-recovery, with a contiguous block instead of a scattered one.)
  3.3 The non-AI path (reading an OpenFOAM result) keeps today's clip/render semantics.

# ROI-as-Simulation-Unit — Design

Roots (absolute, used as prefixes below):
- `OV = /mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/cfd_demo`
- `AI = $OV/SCANO/GTC_A3_Numerical`
- `KC = /mnt/lv/home/litianyu/mycode/dcdtuni/src/frontend/omniverse/Ref_code/kit-cae`

**Path correction to the reports before anything else:** `HomoGraph_dataset.py` is at `$AI/src/FVdomain/HomoGraph_dataset.py`, **not** `src/Main/Data_driven/`. All three reports cite it without a directory; two imply the wrong one. Line numbers in the reports are correct (verified `:462-466` budget, `:516` `full_pool`, `:542-548` rank split, `:1283` pool draw, `:1290-1293` k-hop, `:1318-1323` gather, `:1429` `original_node_indices`, `:1432` `sampled_pivot_nodes`).

---

## A. Feasibility verdict

### 3.1 — cache emits a VTU containing only the ROI: **BUILDABLE ON WHAT EXISTS**

Three independent facts close it.

1. **The identity key is closed-form arithmetic, no lookup table.** `$AI/src/Parse_mesh/parse_boundary.py:246` writes `start_idx_cpd_cell = start_face - num_internal_faces + num_cells`, so cell `i` → cpd `i`, boundary face `f` → cpd `nCells + (f - nInternalFaces)`. Built at `$AI/src/Parse_mesh/parse_openfoam_refactored.py:704-811`. Measured invariant: A3 case0 `n_cpd = 447,205` for all 753 dataset cases.
2. **A `CaeDataSet` carries no completeness contract.** `$KC/source/schemas/shared/omniCae/schema.usda:32-42` — a dataset is any set of field arrays. `:51-56` documents multiple `fileNames` as *spatial partitions of the same dataset*. Nothing validates watertightness or coverage anywhere in `$KC/.../omni.cae.dav/python/commands.py`.
3. **Emitting is cheap and measured.** ROI extract+write+re-read for a 3.4 % ROI = 61 + 60 + 70 ms; a cell-centre cache slices in 20 ms and is 17.1 MB resident for Base_leakage's 609,990 cells vs the 128.3 MB `.vtu` it replaces.

Two bounded constraints, not blockers: authoring *real polyhedral cells* from our own arrays requires SIDS, which rejects `ET_MIXED` (`omni.cae.dav/python/commands.py:584-590`) and needs `NFACE_n` for polyhedra (`:592-593`); and IndeX's willingness to voxelise an unstructured ROI is contested (see F.2). Both are avoided by emitting a resampled `.vti`, which is what the demo's proven path already uses (`$OV/web/src/geometry.ts:97-101`).

### 3.2 — incremental extension, no duplicate computation: **BUILDABLE WITH WORK**, with one genuinely blocked sub-path and one scientific unknown

Buildable:
- The scatter-accumulate merge already exists and is the right arithmetic. `$AI/src/Main/Data_driven/runner.py:1449` reads `original_node_indices`, `:1472-1479` does `index_add_` into `predictions_sum` / `predictions_sq_sum` / `counts`, `:1533` divides. `_all_reduce_accumulator` (`:1587-1623`) is the same operation across ranks — it is a working partial-result merge today.
- Every stage downstream of the pivot list is index-driven, so an arbitrary index set slots in with no model change: `_sample_from_pool` (`HomoGraph_dataset.py:785-844`) → `k_hop_subgraph` (`:1290-1293`) → gather (`:1318-1323`) → `original_node_indices` (`:1429`).
- The boundary/condition stream is **already whole-domain by construction** and must stay that way: `:986-987` snapshots `cpd_centroid_full` before sampling, `:1067-1127` walks all 856 patches every draw, intent stated verbatim at `:1010-1011`. This is precisely why an ROI-only query is physically well-posed at all.

Requires real work:
- **Nothing survives a request.** Every `/predict` forks a fresh process (`$AI/scripts/ai_worker.py:109-127`, `subprocess.run` at `:127`); the accumulator is created per process (`inferencer.py:409`) and dropped at exit. "No duplicate computation" is meaningless until state persists — hence §B's on-disk cache.
- **Partial coverage is currently treated as a fault.** `runner.py:1522-1530` warns "nodes were not sampled", forces `counts=1`, and exports 0.0 K / 0 m/s. This must become a `valid` mask.
- **The emitter is whole-mesh.** `inferencer.py:98-184`, `export_full_mesh_vtu` at `:167-173` with the whole `pv_cells_node`.

**Blocked sub-path (say it plainly):** incremental extension *at the `CaeDataSet` level* is blocked. `$KC/.../omni.cae.data/python/cache.py` is invalidation-only — `put_ex`/`get`/`remove`/`clear`, no grow/patch API — and touching `fileNames` is a property update that invalidates and re-converts the whole dataset (`cache.py:396-419`). `dav.operators.voxelization.compute` allocates a new volume every call and accepts no existing grid (`omni.cae.dav_libs/dav/operators/voxelization.py:139-142`). The only incremental primitive is at the **NanoVDB grid** level (pre-size once, refill tiles, toggle `nvindex:composite` — `omni.cae.viz/python/index_volume.py:78-96`), and that is source-read, not runtime-verified. Design accordingly: the incremental structure lives in *our* cache, and Kit gets a fresh small `.vti` per emit.

**Scientific unknown, not an engineering one:** a contiguous block is out-of-distribution. Every draw this model has ever seen — training and inference — is a permutation slice (`HomoGraph_dataset.py:526-527`, `:547-548`); grepping `bbox|roi|region|spatial_sample` across `$AI/src`, `cases`, `scripts` returns nothing in any sampling path. Not blocked; unvalidated. §D turns this into a measurement.

### 3.3 — non-AI path keeps today's clip semantics: **BUILDABLE, NO CONFLICT**

The existing `CaeVtkUnstructuredGridAPI` + `dav_voxelization` + `roi` rel path (`handlers.py:463-513` `handle_set_roi_box`, `:514+` `handle_set_roi_clip`, `scene.py:1498` `_do_rebuild_volume`) stays untouched as the fallback. The extraction path is an *additional* dataset source selected per mode.

---

## B. The cache data structure — `CpdCache`

### Identity

**Key = the global `cpd_cell` id, `int32`, dense over `[0, n_cpd)`.** Not a centroid, not a spatial hash. It is simultaneously the VTU cell index (verified: `internal.vtu` cellData matches OpenFOAM `964/T` with max abs diff 0.0, exact float32 equality fraction 1.0, `NumberOfCells == nCells == 609990`) and SCANO's native sampling index (`baseline_sampling.py:6-28` takes exactly `sampled_nodes: LongTensor [S] global cpd_cell ids`).

**Validity scope = one mesh.** `mesh_id = sha1(nCells, nFaces, nInternalFaces, ordered patch names + startFace, hash of polyMesh/owner header)`. Cell order is invariant per mesh but **not** across a re-mesh or a different `nProcs`, because `reconstructParMesh` merges in rank order (`$REPO/src/backend/aircooling/runner/stlgen_runner.py:5648`). The layout must be snapshotted from the **post-`createBaffles`** mesh — `createBaffles` runs *after* reconstruct (`stlgen_runner.py:5576`) and changes `nInternalFaces` and adds patches.

**Physical scope = one operating point.** `gen_id = sha256(canonical bc dict posted by the UI)` over the four keys at `$OV/web/src/components/AiPrediction.tsx:96-101`, plus the checkpoint hash. Two operating points are two different fields; merging them is nonsense, and the directory key makes that structural rather than a convention.

### On-disk layout

Root: `$AI/logger/cpd_cache/<mesh_id>/`

**`layout.npz` — written once per mesh, shared by all generations:**

| array | dtype | shape | bytes (A3 / Base_leakage) |
|---|---|---|---|
| `n_cells`, `n_internal_faces`, `n_cpd` | int64 | scalar | — |
| `centroid` | float32 | `[n_cpd,3]` | 5.4 MB / 9.2 MB |
| `cell_type` | uint8 | `[n_cpd]` | 0.45 MB / 0.77 MB |
| `bin_offsets` | int32 | `[nb+1]` (64³ bins) | 1.0 MB |
| `bin_items` | int32 | `[n_cpd]` | 1.8 MB / 3.1 MB |
| `grid_origin`, `grid_inv_step` | float32 | `[3]` | — |
| `domain_bounds` | float64 | `[6]` | — (solver frame; **`depth = domain_bounds[3]`**, see C.9) |

`centroid` is a straight copy of `cpd|centroid` (`$AI/src/FVdomain/HomoGraph_dataset.py:908`; persisted at `parse_base.py:879`) — it already contains boundary-face centres in the tail block, which is why it is the right array to index and not the cell centroids alone.

**`gen_<gen_id>/` — one per operating point:**

| file | dtype | shape | bytes (A3) |
|---|---|---|---|
| `sum.f32.npy` (memmap) | float32 | `[n_cpd,5]` = u,v,w,p,T | 8.94 MB |
| `counts.u16.npy` (memmap) | uint16 | `[n_cpd]` | 0.89 MB |
| `sqsum.f64.npy` (optional) | float64 | `[n_cpd,5]` | 17.9 MB |
| `filled.bits` | packed uint8 | `ceil(n_cpd/8)` | **56 KB** (96 KB for Base_leakage) |
| `meta.json` | — | mesh_id, bc payload, ckpt hash, `n_filled`, committed-ROI ledger | — |

**Store the SUM, not the mean.** This is load-bearing: sum + count is associative and restart-safe, so a later ROI that overlaps an earlier one merges by `sum += new; counts += 1` with no knowledge of history. It is also bit-for-bit the same arithmetic the existing TTA accumulator uses (`runner.py:1472-1479` then `:1533`), so the incremental cache and the multi-rank all-reduce (`:1587-1623`) are the same operation at two timescales. The mean is derived at emit: `field = sum / max(counts,1)`.

Total resident, all generations aside: **~19 MB** — smaller than the single 128 MB VTU read it replaces.

### ROI query

```python
lo, hi = roi_aabb_solver_frame                       # see C.9 for the frame conversion
b0 = np.floor((lo - origin) * inv_step).astype(int)
b1 = np.floor((hi - origin) * inv_step).astype(int)
cand = concat(bin_items[bin_offsets[b]:bin_offsets[b+1]] for b in block(b0,b1))
ids  = cand[((centroid[cand] >= lo) & (centroid[cand] <= hi)).all(1)]
```

Measured on the real 609,990 centroids: build 140.8 ms once (7.0 MB int64, **3.5 MB int32**); query **3.88 ms** for a 1 m slab (37,168 cells) and **0.23 ms** for a 0.5 m cube (126 cells), against 22.32 / 19.82 ms brute force, results **bit-identical**. A kd-tree is the wrong structure: an AABB has no cheap ball query and `cKDTree` overselected 603,260 of 609,990 cells for a 6 % slab.

### Exactly-once bookkeeping

```python
todo     = ids[~get_bits(filled, ids)]        # only these are ever computed
complete = popcount(filled) == n_cpd
coverage = popcount(filled) / n_cpd           # the UI's "domain filled: 12 %"
```

`filled` is kept **separate from `counts > 0`** deliberately. `counts > 0` means *touched by some subgraph* (including as a k-hop halo node that must not be trusted); `filled` means *committed as a pivot*. §D is entirely about that distinction.

### Persistence across a Kit restart

The cache is plain `.npy` memmaps under the AI bundle's `logger/`. Kit never owns it, never caches it, and holds only `(mesh_id, gen_id, path)`. On restart the worker re-mmaps in ~35 ms (measured `savez`/`load` 35 / 33 ms for the equivalent point-cloud pack). `layout.npz` is rebuilt only on a `mesh_id` miss. Nothing in `omni.cae.data/python/cache.py` is involved, which sidesteps the invalidation-only wall entirely.

---

## C. The two modes

### AI mode: ROI → subgraph → inference → fill cache → emit ROI VTU

| # | Change | File:line |
|---|---|---|
| 1 | ROI state lifted so both tabs share one source. It is already threaded as a prop — `roi?: {min,max}` at `$OV/web/src/components/PostProcessing.tsx:34-38` and `CfdControls.tsx:88-92`, owned at `CfdControls.tsx:125-126`. Move ownership to `LeftPanel.tsx`. | `CfdControls.tsx:125-126`, `LeftPanel.tsx` |
| 2 | POST body gains `roi:{min,max}` (demo frame) and `mode:"roi"` | `$OV/web/src/components/AiPrediction.tsx:92-104` |
| 3 | Proxy: **no change** — relays the body verbatim, no timeout | `$OV/web/vite.config.ts:245-263` |
| 4 | `handle_predict` reads `roi`, converts to solver frame, computes `gen_id`, opens the cache, computes `todo`. **If `todo` is empty, skip inference entirely and go straight to emit** — this is the visible payoff of 3.2. Otherwise write `roi_pivots.npy` and pass `--roi-ids` | `$AI/scripts/ai_worker.py:208-250`, cmd built at `:109-124` |
| 5 | Pivot pool becomes the ROI: `full_pool = roi_ids` (still shuffled by the same seeded generator), `budget = max(1,int(len(roi_ids)*ratio))`. **This is the only sampling change.** | `$AI/src/FVdomain/HomoGraph_dataset.py:466` (budget), `:516` (`full_pool`), `:542-548` (rank split — now splits the ROI pool, so multi-GPU still works unchanged) |
| 6 | Boundary/condition stream: **touch nothing.** All 856 patches, all 102,576 boundary rows, every draw | `HomoGraph_dataset.py:986-987`, `:1067-1127`, intent at `:1010-1011` |
| 7 | Accumulator stays full-size (8.9 MB) so `index_add_` remains global-id addressed with zero change | `$AI/src/Main/Data_driven/runner.py:1389-1401`, `:1472-1479` |
| 8 | `_finalize_predictions` returns a `valid` mask instead of forcing `counts[zero]=1` and emitting 0.0 | `runner.py:1522-1533` |
| 9 | **New**: commit step — `sum[pivots] += pred[pivots]`, `counts[pivots] += 1`, `set_bits(filled, pivots)`. Pivots only, never `subgraph_nodes` (§D) | new `$AI/src/FVdomain/cpd_cache.py`, called from `runner.py` after `:1585` |
| 10 | Emitter reads **the cache**, not the accumulator, so ROI #2's output includes ROI #1's cells wherever the render box overlaps them | replaces `$AI/src/Main/Data_driven/inferencer.py:98-184` (`export_full_mesh_vtu` at `:167-173`) for ROI mode |
| 11 | **Frame + `depth` fix.** `to_demo_frame` is `(x,y,z)→(x, z, depth−y)` with `depth = float(mesh.bounds[3])` computed *from the mesh being adapted*. For an ROI subset that is the ROI's own `y_max`, so the slab lands at the wrong height with a receipt that says OK. `depth` must come from `layout.npz.domain_bounds[3]` | `$AI/scripts/vtu_to_demo.py:42-53`, **bug at `:126`** |
| 12 | `resample_to_image` grid from ROI bounds is correct and desirable (small grid); the outside-voxel fill at `:105-113` now paints everything outside the ROI at the field minimum, which is the wanted "empty room" look | `vtu_to_demo.py:64-120` |

Inverse frame conversion (ROI AABB → ROI AABB **exactly**, since it is an axis permutation plus a reflection):
```
solver_x ∈ [Xmin, Xmax]
solver_y ∈ [depth − Zmax, depth − Zmin]
solver_z ∈ [Ymin, Ymax]
```

### OpenFOAM mode: ROI → subset the existing result → emit ROI VTU

Pure extraction. **Writes nothing to the cache's `filled`/`sum`** — the field is already complete everywhere, there is nothing to fill; the cache here is a read-through index, not an accumulator. That separation is what keeps 3.3 from contaminating 3.2.

| # | Change | File:line |
|---|---|---|
| 1 | New command `extractRoi` alongside `loadDataset` | `$OV/web/src/streamMessages.ts:87-88` (sender), `$OV/kit/extensions/dc.cfd_bridge/dc/cfd_bridge/handlers.py:419` (`handle_load_dataset` — add `handle_extract_roi` beside it) |
| 2 | Handler: read `scene.vtu_path` once, mask cell centres to the box, `extract_cells`, write `roi_<hash>.vtu`/`.vti`, call the existing `load_dataset` | `$OV/kit/.../scene.py:1221-1288` |
| 3 | Button next to the ROI controls | `$OV/web/src/components/CfdControls.tsx:524` (ROI section) |
| 4 | Today's clip path stays exactly as-is as the fallback | `handlers.py:463-513`, `:514-558`, `scene.py:1498` |

Measured per-drag floor for the naive disk version: extract 61 ms + write 60 ms + Kit re-read ~70 ms + DAV convert ~30 ms + voxelise ~100 ms + ≥3 app ticks ≈ **0.5 s**, degrading linearly (a 31 % ROI is ~1.3 s). **Debounce on release, not live drag.** The Phase-3 upgrade is a resident point-cloud cache behind a `DataDelegateBase` (`omni.cae.data/python/delegates.py:36-80`, registration ~12 lines as in `omni.cae.delegate.npz/python/extension.py:14-27`), which removes the disk round trip: 20 ms gather, 17.1 MB resident.

---

## D. The incremental merge — what happens at the seam

This is the crux, and a naive design silently produces a discontinuous field. Three parts.

### D.1 Which cells does ROI #2 actually recompute?

`k_hop_subgraph(..., khop=2)` (`HomoGraph_dataset.py:1290-1293`) pulls each pivot's 2-hop *upstream* ancestors into the subgraph. Verified property of this edge list: it is a strict DAG (1,085,175/1,085,175 edges satisfy `src < dst`, zero self-edges), and **no boundary node is ever a source** — all 102,576 boundary nodes appear only as targets. So the halo is directed and one-sided: it reaches spatially adjacent cells with lower index, asymmetrically, and it will **never** pull in a boundary face the pivot set did not already contain.

That halo reaches into ROI #1's territory and those cells are predicted again. **Policy: halo cells participate but are never committed.** The machinery to make the distinction already exists and is already carried separately: `homograph.original_node_indices = subgraph_nodes` at `:1429` versus `homograph.sampled_pivot_nodes = sampled_cpd_cell_ids` at `:1432`. Commit `field[pivots]`, not `field[subgraph_nodes]`. That gives exactly-once semantics with **first-writer-wins** at the seam.

### D.2 Is first-writer-wins physically defensible? — precisely what SCANO guarantees, and what it does not

SCANO's boundary recovery guarantees that the **domain boundary condition is complete for every draw**: `cpd_centroid_full` and `unv_cpd_cell_full` are snapshotted before sampling (`:986-987`), all 856 patches are walked and concatenated every draw (`:1067-1127`), `patch_numerical_values` is a per-patch mean over the 5-field t=1 state (`:1111`) giving `[856,5]`, and the sampling at `:1318-1323` deliberately indexes only the prediction-stream tensors. The bridge is complete-boundary slice cross-attention: 32 slice tokens per head over the whole boundary (`$AI/src/NNmodels/PatchEncoder/PatchCrossAttentionEncoder.py:344-372`), `Q(internal) · K(boundary)` (`:421-437`), desliced back onto the sampled nodes (`:441-453`). That is what makes "simulate only part of the domain" well-posed at all, and it is instrumented — `$AI/src/Main/Data_driven/bc_intervention.py:143-182` (`droppatch`) exists solely to test completeness, with `assert_fired()` at `:92-107` refusing to let a mis-wired intervention read as a discovery.

**It guarantees nothing about the artificial interior boundary an ROI creates.** The cut face of an ROI is not a patch, carries no BC, and appears nowhere in `patch_order`. `k_hop_subgraph` returns the *induced* edge set, so edges leaving the ROI are simply dropped and the outermost shell sees a truncated neighbourhood. There is no mechanism by which ROI #1's value at the seam and ROI #2's value at the seam are made to agree.

**So: the seam is real, and it will be visible if ignored.** Expected artefact thickness = `khop` = 2 cells. In the existing whole-domain run it is invisible only because ~51 overlapping scattered draws average over it — `dspread_node_frac = 0.7706` in `$AI/logger/inference_results.json`, i.e. 77.06 % of nodes are hit ≥2× and get averaged. A single contiguous draw has no such averaging.

### D.3 The design that survives it

**(a) Halo-dilate, commit-core.** Before predicting, dilate the ROI pivot set by `khop` hops (or by one CSR bin, which is cheaper and close enough) and use the dilated set as pivots; commit only the *undilated core minus already-filled*. This moves the truncated-neighbourhood shell outside the committed region at a few percent extra compute. **This is the single most important line of the design.** Boundary faces inside the ROI must be added to the pivot set explicitly, because — per D.1 — k-hop can never reach one; every boundary face is only ever hit as its own pivot.

**(b) Accumulate, never overwrite, in the overlap band.** Where an already-filled id legitimately reappears as a core pivot, do the running mean: `sum += new; counts += 1`, mean derived at emit. This is byte-for-byte the existing TTA merge (`runner.py:1472-1479` + `:1533`) and the existing multi-rank merge (`:1587-1623`), so the incremental cache is not a new numerical scheme — it is the same one at a longer timescale. Persisting the sum rather than the mean is what makes it associative and restart-safe.

**(c) Never blend across `gen_id`.** Enforced by the directory layout, not by discipline.

### D.4 The measurement that decides whether (a) is enough

Do not ship this on argument. Run: ROI #1 ∪ ROI #2 as two sequential calls, then the identical union as one call; compare per-cell. Report `max|ΔT|` and p99 **inside the 2-cell seam band** versus the ROI interiors. The comparison instrument already exists — `draw_spread` in `$AI/src/Main/Data_driven/metrics_utils.py:54-97`, computed in float64 at `runner.py:1541-1549` over nodes with `counts >= 2`.

- If the seam band's discrepancy sits inside the model's own whole-domain draw spread, first-writer-wins is inside the noise and (a) suffices.
- If it does not, either increase the dilation, or force-recompute the band and average it via (b).

This same experiment is the **first ever measurement of the contiguous-vs-scattered OOD risk** (F.1). It must run before the feature is demonstrated.

---

## E. Phased plan

### Phase 0 — remove the fixed frame waits (free, independent, do first)

`wait_for_update()` defaults to **10 app cycles**, not one (`$KC/.../omni.cae.testing/python/__init__.py:80-99`). `_do_rebuild_volume` calls it 30× / 15× / 20× / 6× / 4× at `$OV/kit/.../scene.py:1601, 1610, 1631, 1653, 1660` — up to 750 cycles at a measured ~66 ms/cycle. Real compute in that window is 83-114 ms (full-domain vs ROI-clipped voxelise) and the DAV dataset was a cache **HIT**. Replace with polling on operator completion.

**Browser check** (head browser only — `scripts/head_verify_colormap.py`, Playwright `headless=False` on Xvfb `:99`, never `browser-use`): open the Operational Log, toggle Temperature on. The `volume rebuilt` line must land **< 3 s** after the command line, against the currently measured **35.1 s**. Screenshot the viewport and read it back to confirm the volume is actually rendered — a fast receipt with a blank frame is the exact failure mode `vtu_to_demo.py:64-75` documents.

### Phase 1 — OpenFOAM-mode ROI extraction (no AI, no cache)

Changes: `handle_extract_roi` in `handlers.py` (beside `:419`), `extractRoi` in `streamMessages.ts:87`, button in `CfdControls.tsx:524`. Emit **both** `roi.vti` and `roi.vtu` and load each in turn — this is also the experiment that settles F.2.

**Browser check** (extend `scripts/head_verify_roi_flow.py`): set the ROI to a 1 m slab, click *Extract ROI*. Log must show `n_cells` dropping 609,990 → ~37,168. Screenshot: the field exists **only** inside the box and the rest of the room is empty. Then hide the ROI box — the field stays confined. That last step is the proof it is extraction and not the clip, because the clip cannot do it.

### Phase 2 — `cpd_cache` + layout (still no AI change)

New `$AI/src/FVdomain/cpd_cache.py`: `build_layout()`, `query_roi()`, `todo()`, `commit()`, `emit_vti()`. Build `layout.npz` from the H5 (`cpd|centroid`) or polyMesh. Wire Phase 1's extraction to read through it.

**Browser check**: extract the *same* ROI twice. The second log line must read `cache hit, 0 cells computed`, the second click must be visibly faster (< 0.3 s vs ~0.5 s), and the two screenshots must be pixel-identical.

### Phase 3 — AI mode, one ROI, one shot (no incremental claim yet)

`roi` in the request (`AiPrediction.tsx:92-104` → `ai_worker.py:208`), `roi_ids` into the pool (`HomoGraph_dataset.py:466, 516`), `valid` mask out of finalize (`runner.py:1522-1533`), cache commit, **`depth` fix at `vtu_to_demo.py:126`**.

**Browser check**: draw a 1 m slab, click *simulate*. Log must show pivots dropping from 8,944 to ~1,200 and `nn_forward` shrinking from ~70.5 ms/draw × 50. Screenshot: a coloured slab **in the right place**. This is exactly where the `depth` bug manifests — a slab floating at the wrong height with a perfectly healthy receipt — so read the image, do not trust the log.

### Phase 4 — incremental fill (the actual 3.2)

Halo-dilate/commit-core, sum+counts persistence, `todo` skip, `coverage` readout in the UI.

**Browser check**: three abutting ROIs in sequence. After the third, the log reports committed = union of the three with **zero recomputed pivots**, and the viewport shows one continuous field across both seams. Screenshot centred on a seam at high zoom. Then run D.4's union-vs-split comparison and print `max|ΔT|` in the seam band to the log — that number is the acceptance criterion, not the screenshot.

### Phase 5 — resident worker + in-memory delegate

Hoist Runner/Inferencer out of `main()` (already flagged at `ai_worker.py:14-16`); replace the per-draw full-H5 re-read (`HomoGraph_dataset.py:905-919`, which re-reads `cpd|centroid`, `node|pos`, `cpd|neighbor_cell`, and an 11,028,128-element `pv_cells_node` on every one of 51 draws — **8.52 s of the 13.45 s inference loop**) with a per-case in-memory cache; register a `DataDelegateBase` so ROI data never hits disk. **On today's numbers this is worth more than the ROI itself**: the ROI can shrink ~8.5 s of data-load and ~3.7 s of forward, but ~15 s of 1-step solve (`$AI/cases/GTC/A3/inference.py:403-657`) and ~10 s of torch import survive any ROI.

---

## F. Risks and unknowns, ranked

**1. Contiguous-block sampling is out-of-distribution.** No engineering fixes this. Training and inference draws are permutation slices only (`HomoGraph_dataset.py:526-527`, `:547-548`); Transolver-style physics attention builds its 32 slice tokens by `scatter_add` over exactly the nodes passed in (`PatchCrossAttentionEncoder.py:247-256`), so a contiguous ROI produces slice tokens describing one block where training always saw a room-spanning cloud. Measurable today by D.4. Mitigating fact: the trunk consumes **absolute** positions (`$AI/src/NNmodels/TransFVGN/TextCondTransFVGN_v1.py:488`), and normalisation is dataset-wide constants from `meta.json` / `patch_statistics.json` (`inference.py:285-301`, `importer.py:263-273`, `:756-760`) — so nothing rescales under an ROI. The AeroGTO SCA baseline *would* shift (min-max over the sampled subgraph, `AeroGTOSCA_v1.py:182`); A3 does not use it.

**2. DISAGREEMENT — does IndeX voxelise an unstructured ROI `.vtu` at all?** Do not average these:
- kitcae-ingestion measured `internal.vtu` (Base_leakage, 609,990 cells, VTK_POLYHEDRON present) converting via `CaeVtkUnstructuredGridConvertToDAVDataSet` in 1.44 s and voxelising in 83 ms, from the Kit log.
- `$OV/web/src/geometry.ts:97-99` states the 1.35 M-cell GTC `.vtu` "produces an empty grid", and `$AI/scripts/vtu_to_demo.py:64-75` says the same of a raw 344 k-cell prediction: "the backend still logs volume rebuilt … yet the receipt carries `extent=None` and the viewport shows nothing".

Both cannot be the general rule. Resolve empirically in Phase 1 (same ROI, both formats, read the viewport back). **Until resolved, ship `.vti` for anything the volume renders** — that is the demo's proven default (`geometry.ts:100`).

**3. Seam discontinuity.** §D. Mitigated by halo-dilate/commit-core, quantified by D.4, and *not* covered by SCANO's boundary guarantee.

**4. Frame conversion and `depth`.** `vtu_to_demo.py:126` computes `depth` from the adapted mesh's own bounds. Silent wrong placement for any subset. Must come from `layout.npz.domain_bounds[3]`. Ranked this high because the failure is invisible in logs.

**5. Layout invalidation.** cpd order is invariant per mesh, not across a re-mesh or a different `nProcs` (`stlgen_runner.py:5648`), and `createBaffles` runs *after* `reconstructParMesh` (`:5576`), changing `nInternalFaces` and adding patches. Snapshot post-`createBaffles`; fingerprint; refuse on mismatch. Also: read only reconstructed global output (`964/`, `VTK/`), never `processorN/` — `cellProcAddressing` was verified an exact permutation over all 8 ranks, but only reconstructed output has global order.

**6. ROI-invariant cost floor.** ~15 s 1-step solve + ~10 s torch import + export/adapt survive any ROI. Total today is 19.57 s inference (`inference_results.json`) inside a 45-100 s button (`AiPrediction.tsx:216-220`). A user who draws a tiny box and still waits 30 s will read the feature as broken. Either do Phase 5 first, or put an honest breakdown in the UI.

**7. Generation explosion.** Every slider move mints a new `gen_id` and a 9-27 MB directory. Needs an LRU cap and a size budget in `meta.json`.

**8. Boundary faces inside an ROI.** They live in the same `cpd|centroid` array and come back from the same query, but k-hop can never reach one (D.1). If they are not added to the pivot set explicitly, an ROI that clips a wall silently under-covers its own boundary — and `filled` will honestly report the gap, which is the good news.

**9. Kit-side incremental NanoVDB is source-read, not runtime-verified.** The mechanism (`index_volume.py:78-96`: swap under `[viz:index_volume]::<path>`, toggle `nvindex:composite`) is documented in the source, including the accumulate-across-batches loop in `voxelization_gaussian.py:68-73, 214-229`, but the ingestion report states plainly it was not executed. Phase 4 must not depend on it; use per-ROI `.vti` rebuilds and adopt grid-accumulate only after measuring. Related known hazard: removing a live IndeX volume crashes it, which is why `scene.reset_scene` (`scene.py:1290`) does composite-off → settle → `cache.remove` → `RemovePrim`.

**10. Conservativeness.** Centroid-in-box includes or excludes a straddling cell by its centroid. Fine for rendering; dilate the query by one bin if conservative coverage is required.

**11. `counts` dtype.** uint16 caps at 65,535 draws. Assert rather than saturate.

**12. Operational rules unchanged.** One WebRTC client at a time (`NVST_R_BUSY` flood = the slot is held); stop only via `fuser -k 8301/tcp`; backend `.py` changes require a Kit restart; verification is head-browser only, never `browser-use`.
