# dcCart source-UID baffle mode — isolated CPU trial, 2026-09-27 SGT

**Status: isolated and not mergeable as a general baffle fix.** Narrow
migration candidate in
`codex/dccart-uid-sheet-mode-20260927@2981d17f`, based on `8333f4e8`. The candidate
reduces the B07 false 28-µm layer in Shinaide, but its strict wind mesh still
fails five checks. It is not an accepted CFD mesh and no flow/temperature
solve was started. Two further native REDs show that the candidate still
accepts a narrow tilted double wall and a double wall hidden inside one UID.
Raw logs and case inputs are in
`/mnt/lv/home/litianyu/benchmarks/shinaide_uid_sheet_mode_trial_20260927`;
compact machine-readable evidence is in the adjacent JSON report.

## Contract implemented

The new branch consumes the already parsed `dcdtuniSourceSurfaces` records.
The opt-in boundary is **all internal-baffle regions in the project**. If
none is labelled, dcCart uses the old sheet grouping and coverage path. If
any is labelled, every region across all faceZones must have a unique
`sourceFaceUid` and `representation=zeroThicknessSheet`; missing or wrong
records cause a fatal error before cell cutting. Fully labelled regions stay
separate when the old normal/offset bucket would merge them. Expected baffle
area also collapses near sheets only within one source region, never across
different UIDs. There is no inferred pairing.

Different UID sheets inside a fixed near-parallel cone (|normal dot| ≥ 0.995)
are rejected if their vertex distance range reaches the ±5-cm band and their
**projected tangent-plane AABBs** overlap in positive area. This is a
conservative *fail-closed screen within that cone*, not a proof that their
triangles truly overlap or that they are two sides of one wall. It can reject
complementary disjoint triangles whose boxes overlap, and it misses a narrow
double-wall wedge just outside the cone. The check is
O(S²T) worst case for S UID sheets and T triangles, performed once at mesh
setup; the Shinaide input has 74 internal-baffle regions. Larger graphs need
a cached broad-phase spatial index before this becomes a general path.

Within a single source region, the historical 0.1-mm plane bucket and 5-cm
coverage folding still apply. The previous ordering and missing-support RED
cases in that scope remain unresolved. A region that secretly encodes two
physical wall faces still needs an explicit pair/source-support contract.

## Native regression and unchanged legacy path

Before the code change, the new native cut probe produced **4 expected
failures / 2 passes**: full UID B06/B07 still merged, and mixed, mistyped or
overlapping UID cases were accepted. After the change **9 native tests pass**
(including microtilt, cross-zone UID/legacy rejection, and one-shot metadata
loading), while **2 strict xfail REDs** remain deliberately unresolved:

- A 2-mm-wide sheet tilting 0.24 mm has |normal dot| ≈ 0.9929. It sits just
  outside the fixed cone, so two different UIDs are accepted and the local
  native probe writes **5 cells / 8 cut faces / 2 sheets**.
- Two parallel physical sides 1 mm apart in one STL region and one UID are
  accepted; the native probe writes **4 cells / 2 cut faces / 2 sheets** and
  coverage reports `0.17088 / 0.17088 = 1`, silently passing that gate.

These are concrete reasons not to cherry-pick this branch as a generally
safe repair. Moving a numeric angle threshold cannot establish physical
pairing or source purity.

The source-identity parser fixture was changed to a boundary-wall fixture so
its 13 parser-only cases remain valid under the stricter baffle contract;
**13/13 pass**. The dcCart driver still rejects an unknown source STL before
meshing. Both old no-metadata local fixtures were compared with the baseline
library and produced byte-identical `sheet-probe.json`; the two-sided wall
signature is SHA-256 `6950d4a15540f314500fadc4c2e99a0c4aa8f0df68ed69031c8c57c9a146950c`.

An adjacent Python selection on this **old isolated base `8333f4e8`** gave
**75 passed / 2 failed**. Both failures are in `test_dccart_memory_guard.py`:
its `STLGenRunner.__new__` fixture lacks `cfdrun_path` when the Python
`_verify_hybrid_source_stls` path runs. The primary development branch has
since fixed this fixture at `1a170fa6` and passed its separate 235+3 tests;
these old-base failures are unrelated to the C++ candidate.

## Same-source Shinaide A/B

The 354-record dictionary has SHA-256
`765adcf82500b8cf475264f84363c64f74241119f8da788cb61335323f0d3a1e`.
The previous native preflight of this exact dictionary confirmed all 74/74
`internalBaffle` local regions have unique zero-thickness-sheet UIDs. The
final-binary trial copied its 13 prepared STLs and added the frozen dcCart
grid settings; all 13 STL bytes remained identical after both runs. The
legacy comparison
differs only by deleting the `dcdtuniSourceSurfaces` block. Both used 8 MPI
ranks pinned to CPU 0–3, nice 10, one OpenMP/BLAS thread per rank, hidden
CUDA, and a 300-second mesher budget.

- **No UID metadata:** 371,843 cells; minimum V/A thickness 27.895 µm and
  73 cells below 1 mm, concentrated at B07 server fronts. Strict
  `checkMesh -allTopology -allGeometry -parallel` reports **6 failures**:
  294 low-quality face tets, 76 low-determinant cells, 34,727 concave cells,
  295 low interpolation-weight faces, and 609 low volume-ratio faces.
- **Full UID metadata:** native driver reads all 354 records; 371,742 cells;
  minimum V/A thickness 2.39037 mm and no cell below 1 mm. The same strict
  check still reports **5 failures**: 277 low-quality face tets,
  3 low-determinant cells, 34,562 concave cells, 90 low interpolation-weight
  faces, and 188 low volume-ratio faces.

`checkMesh` returned process code 0 in both runs; the explicit “Failed N mesh
checks” text is the acceptance result. `Raised_Floor` coverage remains about
2× (`113.386 / 56.7402`) in the UID run, so the source-support and area
semantics at that junction still need investigation. Neither run generated a
wind prediction. Local legacy probe JSON was byte-identical to the base
library; the whole-case legacy cell and checkMesh statistics were reproduced,
but full `polyMesh` bytes were not compared because the archived comparison
was written in a different OpenFOAM output format. The original `.cfdproj`
and the self-owned main development branch were not modified by this trial.

The next safe step is to compile explicit physical sheet/component topology
from `.cfdproj`, define any permitted two-sided pairing, and validate actual
source triangle support and allowed junctions. The current UID plus broad
geometry thresholds alone cannot prove those relationships.
