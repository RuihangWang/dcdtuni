# Requirements — state of each, and what is only half-built

The original ask, restated:

1. Only **simulate** the ROI.
2. Only **read** the ROI — do not load the whole result to show a box.
3. Three sub-points: (3.1) a `cpd_cell` cache emitting an ROI-only vtu; (3.2) incremental
   extension with no duplicate computation; (3.3) the non-AI path keeps clip semantics.

Line numbers below were current at `9cb9376` plus the fixes in `CORRECTIONS.md` §1.
Re-grep before relying on them.

---

## Requirement 1 — only simulate the ROI

**Not reachable on this checkpoint.** Two inference-side routes were built and measured;
one fails, one is circular. Full evidence in `02-inference-findings.md`; the remaining route
is `03-retraining-route.md`.

What ships instead, behind an opt-in checkbox that is **off by default**:

| Control | Effect | Exact? |
|---|---|---|
| `Export the ROI only` | inference still covers the whole domain; only the written `.vti` is cut to the box | **yes** |
| `Also simulate on the ROI` | pivots become the box ∪ 45,000 room-wide anchors, one draw instead of 51 | no — trades exactness for compute |

The second is gated on the first (`AiPrediction.tsx`: `disabled={running || !roi || !roiOnly}`),
because restricting the pivots without restricting the export makes no sense.

Honest label, repeated in the UI log line and the code comment: it touches **54.7%** of the
domain. It is faster, not ROI-local.

## Requirement 2 — only read the ROI

Two halves, both wired, one of them only recently made real.

**Backend (Kit).** `CpdLayout` indexes cell centroids in a 64³ bin grid and answers
"which cells are in this box?" from an 11.3 MB `.npz` instead of the 178.9 MB result.
Lives at `kit/cache/cpd/<mesh_id>/layout.npz`; `mesh_id` fingerprints
`realpath|size|mtime_ns|n_cells` and refuses on mismatch, because cell order is invariant
per mesh but **not** across a re-mesh or a different `nProcs`.

Caveat that must not be lost: the result file is still read **once per session**, because
the extraction needs the values, not just the ids. The layout removes the read from the
*second and later* ROI — the drag case. Removing the last read means serving values from
the cache too, which is the AI path's job and is not built.

Until 2026-08-10 the layout's answer was printed and discarded while the code recomputed the
same selection by brute force. See `CORRECTIONS.md` §1.2.

**AI side.** `SCANO_EXPORT_ROI` restricts what the exporter writes. Measured on the demo
box: 11,920 of 344,629 interior cells (3.46%), `.vti` 383 KB against ~10.6 MB.

The `depth` used for the demo→solver conversion comes from the **full** mesh
(`inferencer.py`: `mesh_pos[:,1].max()`), never from the subset's own bounds — a subset's
`y_max` is smaller, and deriving the translation from it places the ROI at the wrong depth
with an identical cell count, i.e. invisibly.

## Requirement 3.1 — ROI-only emission

Done and browser-verified. Both formats work: `.vtu` (unstructured subset) and `.vti`
(resampled image). The demo defaults to `.vti` for anything the volume renders.

## Requirement 3.2 — incremental fill, no duplicate computation

**Machinery only. Called by no production path.**

`CpdCache` implements the contract properly:

| Method | Contract |
|---|---|
| `todo(gen, ids)` | the subset of `ids` not yet committed — the no-duplicate-computation guarantee |
| `commit(gen, ids, values)` | `sum += v; counts += 1; filled[ids] = True` — associative, restart-safe, the same arithmetic as the TTA accumulator |
| `read(gen, ids)` | mean **plus a validity mask**; count-0 cells are UNKNOWN, not 0.0 |
| `coverage(gen)` | filled / total |

Two design points that are easy to get wrong and are deliberately not:

- `filled` records cells committed **as pivots**, not cells that have a value. A halo
  neighbour has a value contribution computed from a truncated neighbourhood; marking it
  filled would freeze it and stop a later ROI from producing it properly.
- Generations are directories. Two operating points are two different fields, so merging
  them is structurally impossible rather than a convention someone has to remember.

10/10 tests in `tests/test_cpd_cache.py`. But `todo`/`commit`/`read`/`coverage` have **no
caller outside those tests**. The accuracy premise they would need — that a partially
filled cache can be completed one ROI at a time — is exactly what `02` shows is false on
this checkpoint. So the machinery is correct and parked, not wired and working.

**Do not describe 3.2 as done.**

## Requirement 3.3 — non-AI path keeps clip semantics

Done. The clip and the extraction are deliberately different operations, and the verify
driver's discriminator is the sequence only extraction can pass: set an ROI → extract →
**hide the ROI box** → the field stays confined. With a clip, the whole field is still
loaded and hiding the box brings it back.

---

## Known gaps

| Gap | Impact |
|---|---|
| `CpdCache` fill path unwired | requirement 3.2 is not delivered |
| Result file still read once per session | requirement 2 is delivered for the drag case only |
| K=45000 never scored at a user-drawn box | the −0.0029 headline does not transfer; see `CORRECTIONS.md` §3 |
| `dspread` health check is `nan` for one-draw modes | no ground-truth-free confidence signal for exactly the modes that ship |
| Retrained checkpoints have no version control | `logger/` is gitignored; provenance today is a hand-computed md5 |

## Verification drivers

Under `cfd_demo/scripts/`, run with the Playwright env on `DISPLAY=:99` — never headless
`browser-use`, which renders the WebRTC viewport black and grabs a GPU process.

| Driver | Proves |
|---|---|
| `head_verify_ai_roi.py` | ROI box → both checkboxes → predict → load → Temperature renders |
| `head_verify_roi_extract.py` | extraction is not clipping (the hide-the-box discriminator), and `.vtu` vs `.vti` both voxelise |
| `head_verify_openfoam.py` | a `.cfdproj` plus its own OpenFOAM result, end to end |
| `head_verify_cfdproj.py`, `head_verify_tree_controls.py`, `head_verify_rack_closeup.py`, `head_verify_grid.py` | geometry parity with the Three.js editor |

A screenshot proves something rendered. It does **not** prove the numbers are right — see
`CORRECTIONS.md` §1. For a field, check statistics against ground truth as well.
