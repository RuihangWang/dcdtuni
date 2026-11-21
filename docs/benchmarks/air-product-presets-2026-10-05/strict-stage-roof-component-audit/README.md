# Read-only compatible roof component diagnostic

This optional audit does not write coordinates or authorize a mesh. `DCCART_TRANSITION_ROOF_AUDIT`, `DCCART_TRANSITION_ROOF_RECTANGULAR` and `DCCART_TRANSITION_ROOF_COMPONENTS` must be explicitly enabled; all are absent from product defaults. Proposals are connected through their entire incident cell stars. Conflicting displacements and fixed corner constraints are rejected exactly. Physical, coupled and zone point protections remain in force. The temporary joint geometry is restored on every path.

The local audit checks planarity, polygon support halfspaces, directed edge closure, cell moments and volume, vertex and face-centre halfspaces, area-tensor determinant, owner/neighbour face and shared vertex tet fans, weights, volume ratios, skewness and conservation. This list does not reproduce every checkMesh check (for example, non-orthogonality); even a local survivor would not constitute full-mesh acceptance.

The actual eight-rank Tri API run completed in 9.111 seconds and rejected all 19 components: three displacement conflicts and sixteen coupled-star vetoes. The six core files in each of eight processor meshes are identical to the audit-off baseline. Actual allTopology/allGeometry still reports five failed checks. JSONL samples are intentionally bounded, report exported/total counts and cannot establish whole-component quality when truncated.

An independently exported serial copy changes only meshProcess=1 and parallelMesh=false. Its 1447 unprotected packets form three components: one with 51 multi-axis point conflicts, one rejected for planarity, and one for cell halfspace/face-centre geometry. No component survives. Same serial audit on/off files are byte-identical; both jobs complete around 14.07 seconds and reject five full-geometry checks. The serial diagnostic is not default eight-rank acceptance.

Original raw SSE, native source/ELF identity, flags, exports and limited proposal witnesses are retained in this directory and its sibling serial directories. Protected input coordinates and patch/zone identities have not been changed.
