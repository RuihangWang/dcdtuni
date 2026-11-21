# Corrections — claims made in commits and docs that the evidence does not support

An audit on 2026-08-10 re-derived every number in the ROI work from the committed
artifacts. Most reproduced. These did not. They are recorded because a wrong claim in a
commit message outlives the session that wrote it, and because two of them describe a
**live defect**, not just prose.

Ordered by how much it costs to rediscover.

---

## 1. 🔴 The shipped sim-ROI path sent the pivot box in the wrong frame — FIXED

**Where.** `6a6509c` wired the "Also simulate on the ROI" checkbox. `ai_worker.py` built
`SCANO_PIVOT_BOX` from `roi["min"]/roi["max"]` with no conversion, while the *export* path
sends the same object through `export_roi.resolve_box()`, which converts demo frame →
solver frame. `SCANO_PIVOT_BOX` is compared against `cpd|centroid`, which is solver frame.

`normalise_roi`'s own docstring explains why the conversion belongs in one place. The pivot
path then bypassed it.

**Proof, on the real mesh** (`scripts/test_pivot_box_frame.py`, all counts re-derived from
`case0.h5`):

| box as interpreted | cpd ids selected |
|---|---|
| demo `[8,0.5,2]-[16,3,7]` read as solver — what shipped | **9,360** |
| converted `[8,12,0.5]-[16,17,3]` — what the exporter used | **14,648** |

Live receipts, before and after the fix, same drag:

```
06:25  PIVOT-EXP mode=box+anchor box=[8,0.5,2,16,3,7] -> pool 53389 ids     <- buggy
06:35  pivot box demo[8,0.5,2,16,3,7] -> solver [8,12,0.5]-[16,17,3] (depth=19.000)
06:35  box+anchor pool = 58178 (box + 45000 room-wide anchors)              <- fixed
```

53,389 ≈ 9,360 ∪ 45,000 anchors; 58,178 ≈ 14,648 ∪ 45,000. The pivot cloud was over a
different region of the room than the region being exported.

**What it did to the field.** This is the part worth remembering. The two predictions,
same drag, same 41×14×26 export grid:

| run | non-empty voxels | T mean | T min | T max |
|---|---|---|---|---|
| 06:25, buggy frame | 14,467 | **207.98 K** | **36.87 K** | 318.55 K |
| 06:35, fixed | 14,924 | 300.90 K | 294.34 K | 320.96 K |
| ground truth in the same box (`temp_cpd_cell`, n=14,648) | — | 299.32 K | 292.21 K | 360.21 K |

37 K air is not a field. The exported cells were largely not pivots, so they carried
whatever the uncovered path produces.

**And it passed a browser verification.** `6a6509c` claims "browser-verified end to end",
and it was — the screenshot showed a plausible orange plume over the rack row. Plausible is
exactly what a broken field looks like when a colormap is pinned to a sane domain. The
verification that would have caught it is the one run afterwards: compare the field's
statistics against ground truth. **A screenshot is evidence that something rendered, not
that the numbers are right.**

**Fix.** `SCANO_PIVOT_BOX` now accepts the same frame-stamped JSON object the exporter
gets. The bare 6-number CSV still means solver coordinates, because 39 recorded arm-log
lines used it that way. Conversion delegates to `export_roi.demo_box_to_solver` — one
implementation, so a sign error cannot be introduced on one side alone.

### 1.2 🔴 The layout query was computed, printed, and thrown away — FIXED

`c4d3cbd` shipped `CpdLayout.query` and the receipt
`extractRoi: layout query N cells in T ms (no result read)`. That number was printed and
never used: `scene.py` recomputed the identical selection by brute force on the next line.
Quoting the receipt as evidence that the extraction avoided reading the result was quoting
a value nobody consumed.

Fixed: the extraction now uses the layout's answer when there is one. Guarded by a new test
over 200 random boxes (`tests/test_cpd_cache.py`, 10/10), because an advisory index only
has to be *approximately* right and a load-bearing one does not.

Verified live: `layout query 852 cells in 69.3 ms` → `src=resident 852 of 1353400 cells
(0.1%) ... 0.15s` against 14.57 s for the first (cache-miss) extraction, and an independent
VTK count of the same box also gives 852.

### 1.3 🔴 A stale AI worker on port 8302 silently serves predictions from old code

`start_demo.sh` starts the Kit streamer (8301) **and** the SCANO worker (8302). Freeing only
8301 leaves the old worker alive; the new one dies with `OSError: Address already in use`
and **the old process keeps answering predictions**. A full head-browser run then completes,
screenshots, and prints `DONE` — against code that is not the code under test. This happened
during the verification of the fix in §1 and cost a full run.

Free both ports, and check `grep 'READY http' /tmp/dc_cfd_ai_worker.log` after restarting.

---

## 2. 🔴 "Stratified anchors are worse than random at every K" — false at K=4000

`04f2f57`, finding #2. Four stratified arms exist; the commit quoted three and generalised.
Re-scored independently (14 arms, the study's own definition — in-box interior cells,
`meta.json` std):

| K | random | stratified | stratified worse? |
|---|---|---|---|
| 4000 | +0.137332 | **+0.136828** | **NO** (by 5.0e-4) |
| 8944 | +0.061089 | +0.110181 | yes |
| 16000 | +0.037832 | +0.080331 | yes |
| 30000 | +0.005361 | +0.038738 | yes |

The margin at K=4000 is within noise, so the *mechanism* claim survives — matching the
training sampling distribution is what heals, and stratified spread does not. The wording
does not. Say "worse at every K where the difference exceeds noise", or quote all four.

## 3. 🔴 K=45000's PASS is a case0, study-box artifact — REOPENED, worse than first written

Originally: every scored arm used the study box `13.489,7.514,0-17.511,11.537,5` (4×4×5 m,
9,636 interior cells), so Δ = −0.0029 was not established for an arbitrary ROI.

Measured on 2026-08-10 across four operating points and both boxes, each against **its own**
whole-domain reference (all coverage 100.0%, zero laundered cells, re-scored independently):

**Study box**

| point | reference | K45000 Δ | band |
|---|---|---|---|
| case0 | 0.2125 | −0.0029 | PASS |
| case100 | 0.1730 | +0.0285 | GREY |
| case500 | 0.3284 | +0.0667 | GREY |
| case700 | 0.0568 | +0.0334 | GREY |

**Demo box** (`8,12,0.5-16,17,3` solver = `8,0.5,2-16,3,7` demo, 11,920 interior cells) —
the box a user actually drags, never scored before:

| point | reference | K45000 Δ | band | \|T bias\| K | relative degradation |
|---|---|---|---|---|---|
| case0 | 0.3758 | +0.0155 | PASS | 0.294 | +4.1% |
| case500 | 0.4988 | **+0.0936** | **FAIL** | 0.155 | +18.8% |
| case700 *(the demo's own 20→14 °C move)* | 0.0530 | +0.0439 | GREY | 0.232 | **+82.7%** |

So the correction is not "the number does not transfer" — it is **the band does not
transfer either**. The shipping mitigation PASSes at exactly one of six (box × point) cells,
and crosses the study's own FAIL threshold on the demo box at case500.

Two things this section must not overstate:

- At case700 the *absolute* error stays small (0.097 against a 0.053 reference); the +82.7%
  is a relative degradation of an already-accurate field. At case500 the whole-domain
  reference is itself 0.499 — the surrogate is weak there regardless of any ROI mitigation.
- The **T-bias numbers are per-point**, and the demo renders a *difference* between two
  operating points. Biases of +0.294 K (case0) and +0.222 K (case700) in the same direction
  would partly cancel in that difference. **Nobody has measured the differenced quantity the
  demo actually shows.** That measurement, not the per-point bias, is what would decide
  whether the demo's −0.94 K headline survives the mitigation.

## 4. 🟠 The entropy instrument's docstring contradicted its own result — FIXED

`slice_entropy_and_cost.py` predicted that occupancy entropy *cannot* move under injection
because injection never writes `slice_weights`. Measured: 2.2854 → 2.4585. The trunk is 7
sequential layers, so replacing layer *L*'s token changes layer *L+1*'s input and therefore
its pooling; only layer 0 is invariant. `9cb9376`'s conclusion depends on occupancy moving,
so the commit and the docstring could not both be right. The commit was right; the docstring
was a prediction never updated after the measurement. Now corrected in place.

Related: "injection buys back only about half" is **42.3%** —
(2.4585 − 2.2854)/(2.6944 − 2.2854).

## 5. 🟠 "The boundary K/V site is sampling-independent to the byte" — overstated

`2026-08-10-roi-inference-verdict.md` and `9cb9376`. The measured `encoder.patch` occupancy
is 2.269763116653149 (whole-domain) vs 2.269772171974182 (ROI-only) — equal to 5 significant
figures, Δ = 9.06e-6, **not** to the byte. Byte identity *is* supported, for a different
quantity: `kv_sum=62.739301` appears 54 times with zero variation. The conclusion stands;
the word "byte" belongs to the checksum.

## 6. 🟡 "Layer 0 is identical in every arm" — identical to 4 decimals

Values 3.465245 (S), 3.465452 (C2 and others), 3.4653 (K45000); spread 2.1e-4. The spec doc
prints both numbers honestly; the commit message rounds the distinction away.

## 7. 🟡 The proposed live health check is unavailable for the modes that ship

`8da9599` offered `dspread` as a ground-truth-free confirmation "usable as a live health
check". `dspread_overall` is **`nan`** for `arm_C2` and for `arm_K45000` — a single draw has
no second draw to disagree with — against 0.077320 for the 51-draw baseline. Both one-draw
modes are exactly what the work ships. The ratios quoted for the multi-draw arms do check
out.

## 8. 🟡 "+70 lines across 4 files" describes only part of the instrumentation

True of the edits to pre-existing model files (`runner.py` +14, `GraphTransolver.py` +19,
`PatchCrossAttentionEncoder.py` +27, `importer.py` +10). The same commit also adds
`slice_basis.py` at **729 lines** and four scripts; **3,171 insertions across 14 files**
total. The small number is the one that matters for "is the production path perturbed", but
it should not be read as the size of the change.

## 9. 🟠 Arm X's Δ is on a different cell set — mine, in this folder's first draft

`02-inference-findings.md` originally listed arm X's +0.0176 in the same table as C1 and P,
which reads as "same box, same cells, much smaller penalty". It is not. X's pool is a
*scattered* set the size of the box, so it covers **2,356 of the 9,636 in-box cells
(24.4%)**, and its Δ is computed on those.

Worse, scoring X over all 9,636 gives **0.114** — better than the whole-domain baseline —
because the 7,280 uncovered cells carry `prediction = target = 0` and contribute exactly
zero error. A masked-out cell is not a correct prediction. The study itself scored covered
cells only, so `verdict.json` is right; the presentation in this folder was not.

Corrected in place, with what arm X actually establishes: a **1,748-pivot scattered draw
produces PASS-quality predictions where it predicts** (0.2310 vs the baseline's 0.2125 on
the same 2,356 cells). That is a statement about the basis, not about coverage.

## 10. 🟡 Numbers that drift

| claim | where | actual |
|---|---|---|
| depth bug at `vtu_to_demo.py:126` | `dc5ea1a`, `31d61f0` | the buggy statement is line **128** at `31d61f0^` |
| "the whole 122 MB .vtu" | `c4d3cbd` | the demo's `data/internal.vtu` is **178.9 MB**; the design spec says 128.3 MB for a *different* mesh |
| "609,990 cells" | `dc5ea1a` | that is Base_leakage, **not** the A3 mesh (344,629 interior / 447,205 cpd) |
| "7.18% of its node-forwards" vs `cost_summary` 7.34% | `04f2f57` / `6a6509c` | both correct — **different denominators** (interior cells vs all cpd rows). Same for "2.7%" (pool) vs "3.1%" (coverage). Always qualify. |
| "C1 matches the baseline's 9.5 draws/cell" | `8da9599` | 9.5 is C1's number; the baseline is 9.368 |
| "43.92 MB → 1.55 MB VTU" | `31d61f0` | the surviving export is 1.35 MB; the full-domain file was overwritten — **UNVERIFIED** |
