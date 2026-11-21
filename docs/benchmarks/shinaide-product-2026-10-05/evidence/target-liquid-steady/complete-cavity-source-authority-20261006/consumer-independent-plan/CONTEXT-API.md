# Proposed context API for a future consumer (not implemented / not a source acceptance)

Split three responsibilities: normal-context production, bounded proposal/admission, atomic publication. Names below are proposed contracts, not existing C++ symbols.

```cpp
struct NormalContextStamp {
    // Owned current m revision + digest; fresh normal snapshot identities.
    GeometryEpoch epoch;
    SnapshotBindings source; // raw/material/producer/boundary membership digests
    WriterNormalizationVersion normalization;
};
struct SourceFaceRole {
    NativeFace native;
    NativeCell owner, neighbour;
    PatchZone role;            // original physical/internal/coupled, not tiny patch
    bool determinantSelected;
    bool artificialCut;       // geometric closure label only
};
struct FaceOrder {
    NativeFace native;
    GlobalNativeCellOrdinal ownerOrdinal, neighbourOrdinal;
    WriterFaceOrderKey key;
    OwnedOrderedRing rawRing, normalizedRing; // point0/orientation distinct
};
struct CellGeometryRecipe {
    NativeCell native;
    OwnedOrderedFaceSequence actualAccumulationOrder;
    OwnedCompleteFaces faces; // no truncated cell for CC/V or determinant
};
struct AliasBinding {
    NativePoint raw, representative;
    BitExactPoint world;
    bool representativeIsGrid;
    OwnedBoundedAliasClass affectedClass;
};
struct ProtectedDependencies {
    OwnedCellFacePointFootprints committedWorD;
    LegacyMutationJournal legacy;
    bool legacyComplete; // if absent, require normal legacyAccepted == 0
};
struct ClosedLocalView {
    // <=128 core, all explicit halo/endpoint caps checked before expensive copy.
    OwnedCellGeometryRecipes cells;
    OwnedSourceFaceRoles roles;
    OwnedWriterOrder faceOrder;
    OwnedAliasBindings alias;
    OwnedMetricEndpoints actualCurrentEndpoints;
    GeometryEpoch epoch;
};
struct CompleteCavityContext {
    const NormalContextStamp stamp;
    const OwnedOrLifetimePinnedNormalIndex adjacencyAndAliases;
    const ProtectedDependencies protectedDependencies;
    const EarliestCoupledBaseline* earliest; // immutable separate certificate
    const CompleteOriginAuthority* origin;  // normal snapshot only
    std::function<scalar()> remainingJobSeconds;
    // The binder may refuse. It cannot fabricate current installed producer.
    BindResult bindCurrentClosedView(const Seed&, const Caps&) const;
};
```

A normal global-native ordinal is needed only to reproduce owner orientation/order, and must be produced from the actual current alive-slot ordering, not compact local IDs. For immutable old native slots it can be represented by an order-preserving rank key; appended/retired slots require explicit predicted full-writer ordering. Unknown key/tie refuses. The normal index is lifetime-pinned and const; a global lazy build is explicitly charged, never hidden in a const query. Source binding is checked again before admission and commit; a mere stored hash string cannot establish freshness.

`bindCurrentClosedView` returns **two distinct views**: a closed geometric recipe and original quality roles. Local CC/V/SF/FC may be calculated with the real installed primitiveMesh implementation only if it has complete cell faces and the writer-preserved actual accumulation sequence. A tiny view's artificial faces can help form a closed geometry, but its local patch/internal classification is never used to select original D faces or replace genuine neighbour data. Querying global roles, alias representative or metric endpoint may not invent default values. Cross-rank coupled/unknown closure refuses in first grammar.

The binder owns copies or pins lifetime explicitly. A candidate does not retain references into polyMesh demand-driven geometry across refresh; it materializes owned numeric fields and IDs. Its `before` is current accepted-prefix, while earliest original certificate remains unchanged. To reproduce normalization without full per-trial writer calls, the binder must obtain normal alias/index/order data or conservatively prove locality. It does not call MPI or `toPolyMesh`.

```cpp
struct PreparedReplacement {
    NormalContextStamp expected;
    OwnedOldNativeSlotsAndKeys old;
    OwnedNewGeometryPatch geometry;
    OwnedNewNativeSlotLayout newSlots;
    PreparedOriginReplacement lineage;
    PreparedAliasReplacement aliases;
    OwnedActualBeforeRoleCertificate before;
    OwnedActualProposedRoleCertificate proposed;
    CapacityReadiness reserved; // all geometry + key + alias storage admitted
};
PrepareResult prepareReplacement(const CutMesh&, const ClosedLocalView&,
                                 const GeometryProposal&, const Caps&,
                                 RemainingClock);
CommitStatus commitPrepared(CutMesh&, PreparedReplacement&&) noexcept;
```

Preparation must not call mutating `join`, change alive bits or install new composite entries/keys. Composite key/catalog admission is prepared as an independent owned value and checked against live catalog identity before commit. A whole-cutMesh deep copy per candidate is excluded from the bounded journal contract. Reserve may change capacity without changing logical data; allocation/copy time still consumes the stage budget and must finish before final clock/epoch checks. If the implementation cannot ensure no throwing work after its first logical mutation, it needs a fully staged shadow publication strategy instead of claiming atomicity.

The final check validates identical stamp, old native slots/keys, all raw dependencies, origin authority freshness, storage limits, remaining real clock and proposed certificates. No callback/allocator/MPI is permitted during commit. Geometry arrays, all cell metadata, alias state, fresh journal and lineage must publish coherently; explicit invalidation/versioning prevents stale adjacency or installed views from authorizing another proposal. The old shared earliest baseline and W/D state stay identical and are never rebaselined. Any logical mismatch produces Refused without geometry/lineage changes.

`Result` carries `Disabled`, `UnknownContext`, `DependencyUnknown`, `Capacity`, `Expired`, `AmbiguousGeometry`, `GuardRefused`, `Prepared`, `Accepted`, `FinalFailed` with owned bounded diagnostics. The local kernel never performs collective operations. APP/C owns global enable and fixed summary/prefix/final reductions. A refusal is a no-op local result, not a branch that skips existing collective guards. Default-OFF stays explicit; no consumer enabled until ROOT separately authorizes the normal hook and its writer certificates.

The first executable discovery policy can use bounded cut metrics to nominate bad cells; label these proposal-only. It must not claim these are current actual installed D values. Enumerate bounded point stars from real native adjacency, then collect complete origin sets via normal authority and reject insufficient source/role/shape bindings. All candidate geometry belongs to this bounded snapshot. Existing finite-point kernel can optimize one 3-D lobe only after exact/direct failed proposal, charging its actual requests to the same clock; it does not expand permitted topology or authorize old pair unions.

For final installed verification, retain affected native IDs, role/shape and predicted normalization plus complete endpoints; actual `newCell/newFace/point/alias` output maps are required. Missing/unknown mapping cannot be accepted merely because `compactKeys` succeeded. Recompute owned fields after prefix and finite repair, preserve all previously passing hard roles; original physical facets and earliest coupled baseline retain their separate exact requirements. Global N allocation requires an explicit ROOT policy; until then choose local count/max nonincrease or refuse.
