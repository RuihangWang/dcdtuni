# Shared-point mathematical diagnostics on the actual 7785 mesh

The production mesh remains rejected: the unchanged official full checker
reports 909 low-determinant cells, 100 low-interpolation-weight faces and two
low-volume-ratio faces. No C++ source, native binary, mesh coordinates or
quality thresholds were changed by these diagnostics.

The archive preserves 116 manifest-selected mathematical/writer files plus
two read-only native design files and their four manifests, all 122 members
verified again after decompression (89,544,339 raw bytes). It includes original
failed attempts and explicit conditional scope. The large actual native case
was previously preserved separately under the main repository's owned
`.codex-tmp/goal-20261005/preserve/air-mesh-7785-final/`; it is not duplicated
in this mathematical archive. This is not the whole worktree cleanup archive.

A ten-point simultaneous proposal covers 277 core cells and 188 outside
neighbors. The local stock-formula proxy changes D33 to10 and W14 to1, with
no R or skew failure and no formerly passing hard role flipped. Core volume
and first moments use exact fractions; outside CC/V bytes are unchanged.
Nonorthogonal warning labels can change; the maximum remains81.60636 degrees
below the original whole-case88.2022 degrees. The only moved physical point
is tangential to its same-BC, zone-free flat patch: the original outer ring,
plane and exact footprint area/first moments are retained. The existing
1.6203125mm normal offset from the exported raw STL is retained, not corrected
or represented as newly calibrated geometry. Individual physical triangular
coordinates change; an all-triangle-bitwise claim would be false.

The binary64 writer-format round trip retains all candidate point bits; the
minimum formerly passing determinant is0.00100085976875 (threshold0.001).
This format proof does not establish installed native CC/V/Sf/FC parity.
The helper's triangle arithmetic has an ULP-level distinction from installed
OpenFOAM; final native geometry and the unchanged full checker remain required.
Later eleven-point/ratio proposals are separate, not silently appended here.

[Native placement and cache review](native-design/README.md) explains why
plain polyMesh movePoints can retain old geometry caches. A general bounded
implementation belongs after zones and before the final coupled certificate,
with complete point-star/outside guards, batch real geometry updates, remaining
time caps and the ordinary global checker. Hardcoded case coordinates are not
a production repair. No large-case air solver or transient seed is authorized
by the local improvements.
