# Minimum private-preview design — leaf-boundary wall-half ownership

This is a proposed producer correction, not an applied source change. The first implementation should touch only the existing wall-fragment ownership path in held Cartesian.cpp SHA6200ab2a4c86aa8e7b69f28d630a97d79a680360e0a688c2e58ee6edd0c6e43b. ROOT decides whether to apply after a separately frozen private preview and controlled tests.

1. Identify only a nondegenerate clipped wall fragment whose complete vertices lie on an existing leaf-box boundary plane using current clipTol. Require a real boundary plane; an internal or oblique fragment preserves its exact previous arithmetic and classification. Do not key on case/cell/triangle/patch IDs, names, wall orientation or classifier fam.
2. Preserve the current centroid, normal, clipTol and p±=tc±10*clipTol*n arithmetic. For a boundary tile, compute whether each probe belongs to the owning leaf. A clearly outside half is not eligible to own that tile; an inside half continues through the existing classification/kept component logic. Define boundary-tie handling explicitly using current tolerance; do not enlarge tolerances or snap any geometry.
3. Carry this half eligibility through direct12551–12552, wrap12603–12643 and edge-fallback12696–12716 paths. Setting an outside half to−1 alone is insufficient: wrap inheritance or fallback can otherwise resurrect it. The edge fallback direction pr.second must bind to the corresponding p+/p− half without changing its existing winding arithmetic. If a direction cannot be proved to correspond, refuse that emission in the preview rather than guess.
4. Keep existing patch/source identity, vertex order/reversal rule, role and flood/seed meanings unchanged. For wallPair/BARRIER_SHEET, adjacent leaves each retain their own inside-fluid half when it is actually kept; legitimate two-sided sheets remain two-sided. The false upper corner face disappears only through ownership, not by a post-S10 bad-face filter.
5. Preserve strict-interior rescue12737–12754, all existing thresholds, S10 kind1 refusal32679–32693, emitted face checks, volume/centroid computations, and final export/checker behaviour.

## Controlled preview specification

Use a single pure helper wired in a private Cartesian.cpp copy and an independent geometry driver; no live source, CLI business mesh, package installation or binary replacement.

- Three axes, both normal orientations, minimum and maximum leaf boundaries: precisely one owning half; exterior half remains forbidden through direct, successful wrap and edge fallback.
- Two neighbouring leaves on one plane with two kept fluid regions and BARRIER_SHEET/wallPair: one lawful half per leaf, original patch identity and opposite winding retained. If one fluid side is dropped, it emits none.
- Four actual copied cap rings/upper bbox values: reject the out-of-leaf half and preserve complete existing12-face fluid shell. Below counterpart context must preserve lawful wall coverage; no unconditional patch deletion.
- Strictly internal planar and noncoplanar/oblique fragments: old classification and emission decisions unchanged, even when an unrelated out-of-box probe exists. Degenerate tile stays degenerate and is not promoted.
- Current tolerance boundaries: just-inside, exact, within-band and just-outside cases with the original10*clipTol probe; near-coplanar genuine interior triangles must not be silently assigned to a boundary.
- Multiple kept components, wrap aliases and signed edge fallback: the guard cannot redirect a half or choose another component. Existing failed classification stays failed unless existing allowed fallback can prove an inside half.
- Exact shell closure, physical face area/orientation and first moment for the controlled neighbouring cavity; counterexample reversing/deleting an arbitrary physical face still fails. No threshold relaxation.

The present evidence does not establish global ownership after reconstruction or whole-case success. A source-only preview must bind original source/ELF and actual audit manifest and retain first failure logs. Only subsequent ROOT normal build and React job can establish runtime improvement.
