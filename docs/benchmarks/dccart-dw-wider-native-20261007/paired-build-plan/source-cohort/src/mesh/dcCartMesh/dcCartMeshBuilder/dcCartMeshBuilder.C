#include "dcCartMeshBuilder.H"
#include "polyMesh.H"
#include "Time.H"
#include "OStringStream.H"
#include <unordered_map>
#include <vector>
#include "polyPatch.H"
#include "faceZone.H"
#include "cellZone.H"
#include "pointZone.H"
#include <algorithm>
#include <functional>

namespace
{

//- One emitted face before ordering: the two cells it separates plus the
//  rectangle it spans, all in FINEST-level integer coordinates.
struct rawFace
{
    Foam::label own;        // cell id on the -normal side (may be -1 for none)
    Foam::label nei;        // cell id on the +normal side (-1 when boundary)
    Foam::direction cmpt;   // face normal axis
    Foam::label plane;      // finest-level index of the face plane
    Foam::label a0, a1;     // extent along (cmpt+1)%3, finest-level indices
    Foam::label b0, b1;     // extent along (cmpt+2)%3
    Foam::label patch;      // patch id for boundary faces, -1 internal
    Foam::label zone;       // faceZone index for internal baffle faces, else -1
};

//- Packed key -> label in one flat array (open addressing, linear probing).
//  Keys are dcCartPack values (63 bits), so ~0 marks an empty slot.
class keyLabelMap
{
    static constexpr uint64_t empty_ = ~uint64_t(0);
    std::vector<uint64_t> keys_;
    std::vector<Foam::label> vals_;
    size_t size_ = 0;

    static size_t home(uint64_t k, size_t mask)
    {
        k ^= k >> 33;
        k *= 0xff51afd7ed558ccdULL;
        k ^= k >> 33;
        return size_t(k) & mask;
    }

    void rehash(size_t cap)
    {
        std::vector<uint64_t> oldK(cap, empty_);
        std::vector<Foam::label> oldV(cap, -1);
        oldK.swap(keys_);
        oldV.swap(vals_);
        size_ = 0;
        for (size_t i = 0; i < oldK.size(); ++i)
        {
            if (oldK[i] != empty_) { set(oldK[i], oldV[i]); }
        }
    }

public:

    void reserve(size_t n)
    {
        size_t cap = 16;
        while (cap < 2*n) { cap <<= 1; }
        if (cap > keys_.size()) { rehash(cap); }
    }

    Foam::label find(const uint64_t k) const
    {
        if (keys_.empty()) { return -1; }
        const size_t mask = keys_.size() - 1;
        for (size_t i = home(k, mask); ; i = (i + 1) & mask)
        {
            if (keys_[i] == k) { return vals_[i]; }
            if (keys_[i] == empty_) { return -1; }
        }
    }

    //- insert, or overwrite the value of a present key
    void set(const uint64_t k, const Foam::label v)
    {
        if (2*(size_ + 1) > keys_.size()) { rehash(keys_.empty() ? size_t(16) : 2*keys_.size()); }
        const size_t mask = keys_.size() - 1;
        for (size_t i = home(k, mask); ; i = (i + 1) & mask)
        {
            if (keys_[i] == k) { vals_[i] = v; return; }
            if (keys_[i] == empty_) { keys_[i] = k; vals_[i] = v; ++size_; return; }
        }
    }
};


//- Every registered point strictly between p0 and p1 (exclusive), in order,
//  by recursive midpoint subdivision along the axis on which they differ
struct spliceWalker
{
    const keyLabelMap& pointId;

    void operator()
    (
        const Foam::labelVector& p0,
        const Foam::labelVector& p1,
        Foam::DynamicList<Foam::label>& out
    ) const
    {
        using namespace Foam;
        labelVector d = p1 - p0;
        const label span = mag(d.x()) + mag(d.y()) + mag(d.z());
        if (span <= 1) { return; }
        const labelVector mid
        (
            (p0.x() + p1.x()) / 2, (p0.y() + p1.y()) / 2, (p0.z() + p1.z()) / 2
        );
        const label id = pointId.find(dcCartPack(mid.x(), mid.y(), mid.z()));
        if (id < 0) { return; }
        (*this)(p0, mid, out);
        out.append(id);
        (*this)(mid, p1, out);
    }
};

//- Steps 1-4 of the build: the live cells, every face emitted once, the
//  registered points and each face's point list (hanging points spliced
//  in). `patchOf` / `zoneOf` name the emitted faces, `ready` sees the points
//  and faces once registered, `faceOut` receives every face's point list in
//  emission order. The mesh constructor and the corner-topology pass share
//  it, so both see the same points under the same ids.
template<class PatchFn, class ZoneFn, class ReadyFn, class FaceFn>
void assembleFaces
(
    const Foam::dcCartOctree& tree,
    const Foam::boolList& alive,
    PatchFn&& patchOf,
    ZoneFn&& zoneOf,
    Foam::labelList& cellLevel,
    Foam::List<Foam::labelVector>& cellIndex,
    Foam::DynamicList<Foam::point>& pts,
    std::vector<rawFace>& raw,
    ReadyFn&& ready,
    FaceFn&& faceOut
)
{
    using namespace Foam;
    const dcCartGrid& grid = tree.grid();
    const label fineLevel = max(tree.deepestLevel(), label(1));

    // ---------------------------------------------------------------------
    // 1. Flatten the octree and keep only live cells, renumbering as we go.
    // ---------------------------------------------------------------------
    labelList allLevels;
    List<labelVector> allIndices;
    tree.flatten(allLevels, allIndices);

    if (alive.size() != allLevels.size())
    {
        FatalErrorInFunction
            << "alive mask has " << alive.size() << " entries but the octree "
            << "has " << allLevels.size() << " leaves" << exit(FatalError);
    }

    label nLive = 0;
    std::vector<size_t> liveCount(fineLevel + 2, 0);
    forAll(alive, i)
    {
        if (alive[i]) { ++nLive; ++liveCount[allLevels[i]]; }
    }

    cellLevel.setSize(nLive);
    cellIndex.setSize(nLive);

    // per-level lookup from packed key to LIVE cell id (-1 = dead or absent)
    std::vector<keyLabelMap> liveByLevel(fineLevel + 2);
    for (size_t l = 0; l < liveByLevel.size(); ++l)
    {
        liveByLevel[l].reserve(liveCount[l]);
    }

    {
        label c = 0;
        forAll(alive, i)
        {
            if (!alive[i]) { continue; }
            cellLevel[c] = allLevels[i];
            cellIndex[c] = allIndices[i];
            const labelVector& v = allIndices[i];
            liveByLevel[allLevels[i]].set(dcCartPack(v.x(), v.y(), v.z()), c);
            ++c;
        }
    }

    // Scale a level-L index up to the finest level, where all points live.
    auto toFine = [&](label level, label v) -> label
    {
        return v << (fineLevel - level);
    };

    auto liveIdOf = [&](label level, const labelVector& v) -> label
    {
        return liveByLevel[level].find(dcCartPack(v.x(), v.y(), v.z()));
    };

    // ---------------------------------------------------------------------
    // 2. Emit every face exactly once (see the header for the rule).
    // ---------------------------------------------------------------------
    raw.clear();
    raw.reserve(size_t(nLive) * 3 + 1024);

    auto emit = [&]
    (
        label own, label nei, direction cmpt,
        label planeFine, label a0, label a1, label b0, label b1,
        label patch, label zone = -1
    )
    {
        raw.push_back(rawFace{own, nei, cmpt, planeFine, a0, a1, b0, b1, patch, zone});
    };

    for (label c = 0; c < nLive; ++c)
    {
        const label L = cellLevel[c];
        const labelVector& v = cellIndex[c];

        for (direction cmpt = 0; cmpt < 3; ++cmpt)
        {
            const direction a = (cmpt + 1) % 3;
            const direction b = (cmpt + 2) % 3;

            for (label s = 0; s < 2; ++s)
            {
                const bool positive = (s == 1);

                label nbrLevel = -1;
                labelVector nbrIdx(labelVector::zero);
                const dcCartOctree::neighbourType nt =
                    tree.neighbour(L, v, cmpt, positive, nbrLevel, nbrIdx);

                // Face plane in finest-level indices
                const label planeFine =
                    toFine(L, v[cmpt] + (positive ? 1 : 0));
                const label a0 = toFine(L, v[a]);
                const label a1 = toFine(L, v[a] + 1);
                const label b0 = toFine(L, v[b]);
                const label b1 = toFine(L, v[b] + 1);

                auto emitBoundary = [&](bool domainBoundary)
                {
                    point centre = grid.cellCentre(L, v);
                    centre[cmpt] = grid.planeCoord
                    (
                        cmpt, L, v[cmpt] + (positive ? 1 : 0)
                    );
                    const label p = patchOf
                    (
                        L, v, cmpt, positive, centre, domainBoundary
                    );
                    // Boundary faces are always stored with the live cell as
                    // owner and the normal pointing OUT of the domain.
                    if (positive)
                    {
                        emit(c, -1, cmpt, planeFine, a0, a1, b0, b1, p);
                    }
                    else
                    {
                        emit(-1, c, cmpt, planeFine, a0, a1, b0, b1, p);
                    }
                };

                if (nt == dcCartOctree::OUTSIDE)
                {
                    emitBoundary(true);
                    continue;
                }

                if (nt == dcCartOctree::FINER)
                {
                    // The four finer neighbours each emit their own face when
                    // alive. Any that is dead leaves a boundary sub-face that
                    // only this cell can emit, at the finer resolution.
                    FixedList<labelVector, 4> quad;
                    tree.finerFaceNeighbours(L, v, cmpt, positive, quad);
                    const label fl = L + 1;
                    forAll(quad, q)
                    {
                        if (liveIdOf(fl, quad[q]) >= 0) { continue; }

                        const labelVector& n = quad[q];
                        const label sa0 = toFine(fl, n[a]);
                        const label sa1 = toFine(fl, n[a] + 1);
                        const label sb0 = toFine(fl, n[b]);
                        const label sb1 = toFine(fl, n[b] + 1);

                        point centre = grid.cellCentre(fl, n);
                        centre[cmpt] = grid.planeCoord
                        (
                            cmpt, L, v[cmpt] + (positive ? 1 : 0)
                        );
                        const label p = patchOf
                        (
                            L, v, cmpt, positive, centre, false
                        );
                        if (positive)
                        {
                            emit(c, -1, cmpt, planeFine, sa0, sa1, sb0, sb1, p);
                        }
                        else
                        {
                            emit(-1, c, cmpt, planeFine, sa0, sa1, sb0, sb1, p);
                        }
                    }
                    continue;
                }

                // SAME or COARSER: one neighbour cell (or a dead one)
                const label nbr = liveIdOf(nbrLevel, nbrIdx);

                if (nbr < 0)
                {
                    emitBoundary(false);
                    continue;
                }

                if (nt == dcCartOctree::SAME && !positive)
                {
                    continue;   // the +side cell emits it
                }

                const label zone = zoneOf(L, v, cmpt, positive);
                if (positive)
                {
                    emit(c, nbr, cmpt, planeFine, a0, a1, b0, b1, -1, zone);
                }
                else
                {
                    emit(nbr, c, cmpt, planeFine, a0, a1, b0, b1, -1, zone);
                }
            }
        }
    }

    // ---------------------------------------------------------------------
    // 3. Register the corner points of every face.
    // ---------------------------------------------------------------------
    keyLabelMap pointId;
    // An interior hexahedron contributes about one unique point and three
    // faces. Reserving twice the face count, then doubling again for the
    // hash load factor, initialized four times the required table on large
    // rooms, independently on every MPI rank. The cell estimate keeps the
    // same insertion order and grows normally for sparse/transition meshes.
    const size_t expectedPoints = size_t(nLive) * 2 + 1024;
    pointId.reserve(expectedPoints);
    pts.clear();
    pts.reserve(label(expectedPoints));

    auto registerPoint = [&](label i, label j, label k) -> label
    {
        const uint64_t key = dcCartPack(i, j, k);
        const label have = pointId.find(key);
        if (have >= 0) { return have; }
        const label id = pts.size();
        pts.append
        (
            point
            (
                grid.planeCoord(0, fineLevel, i),
                grid.planeCoord(1, fineLevel, j),
                grid.planeCoord(2, fineLevel, k)
            )
        );
        pointId.set(key, id);
        return id;
    };

    auto cornerIdx = [&](const rawFace& f, label ai, label bi) -> labelVector
    {
        const direction a = (f.cmpt + 1) % 3;
        const direction b = (f.cmpt + 2) % 3;
        labelVector v(labelVector::zero);
        v[f.cmpt] = f.plane;
        v[a] = ai;
        v[b] = bi;
        return v;
    };

    for (const rawFace& f : raw)
    {
        for (label ai : {f.a0, f.a1})
        {
            for (label bi : {f.b0, f.b1})
            {
                const labelVector v = cornerIdx(f, ai, bi);
                registerPoint(v.x(), v.y(), v.z());
            }
        }
    }

    // ---------------------------------------------------------------------
    // 4. Build the face point lists, splicing in hanging points.
    // ---------------------------------------------------------------------
    auto existingPoint = [&](const labelVector& v) -> label
    {
        return pointId.find(dcCartPack(v.x(), v.y(), v.z()));
    };

    // Append every registered point strictly between p0 and p1 (exclusive),
    // in order, by recursive midpoint subdivision along the single axis on
    // which they differ.
    const spliceWalker splice{pointId};

    ready(pts, raw);

    DynamicList<label> vlist(16);
    for (label fi = 0; fi < label(raw.size()); ++fi)
    {
        const rawFace& f = raw[fi];

        // Counter-clockwise about +cmpt using the cyclic (a, b) convention:
        // (a0,b0) -> (a1,b0) -> (a1,b1) -> (a0,b1) has normal +cmpt for all
        // three axes because a = (cmpt+1)%3, b = (cmpt+2)%3.
        const labelVector c00 = cornerIdx(f, f.a0, f.b0);
        const labelVector c10 = cornerIdx(f, f.a1, f.b0);
        const labelVector c11 = cornerIdx(f, f.a1, f.b1);
        const labelVector c01 = cornerIdx(f, f.a0, f.b1);

        vlist.clear();
        vlist.append(existingPoint(c00)); splice(c00, c10, vlist);
        vlist.append(existingPoint(c10)); splice(c10, c11, vlist);
        vlist.append(existingPoint(c11)); splice(c11, c01, vlist);
        vlist.append(existingPoint(c01)); splice(c01, c00, vlist);

        faceOut(fi, f, vlist);
    }

}

} // End anonymous namespace


// * * * * * * * * * * * * * * * Constructors  * * * * * * * * * * * * * * * //

Foam::dcCartMeshBuilder::dcCartMeshBuilder
(
    const dcCartOctree& tree,
    const boolList& alive,
    const wordList& patchNames,
    const wordList& patchTypes,
    const patchResolver& resolver,
    const wordList& faceZoneNames,
    const faceZoneResolver* zoneResolver
)
:
    tree_(tree),
    patchNames_(patchNames),
    patchTypes_(patchTypes),
    nInternalFaces_(0),
    faceZoneNames_(faceZoneNames)
{
    if (patchTypes_.size() != patchNames_.size())
    {
        patchTypes_.setSize(patchNames_.size(), word("patch"));
    }
    std::vector<rawFace> raw;
    DynamicList<point> pts;
    faceList allFaces;
    labelList allOwn;
    labelList allNei;
    labelList allPatch;
    assembleFaces
    (
        tree, alive,
        [&](label L, const labelVector& v, direction cmpt, bool positive, const point& centre, bool domainBoundary) -> label
        {
            return resolver.patchId(L, v, cmpt, positive, centre, domainBoundary);
        },
        [&](label L, const labelVector& v, direction cmpt, bool positive) -> label
        {
            return zoneResolver ? zoneResolver->faceZoneId(L, v, cmpt, positive) : -1;
        },
        cellLevel_, cellIndex_, pts, raw,
        [&](const DynamicList<point>&, const std::vector<rawFace>& r)
        {
            allFaces.setSize(label(r.size()));
            allOwn.setSize(label(r.size()));
            allNei.setSize(label(r.size()));
            allPatch.setSize(label(r.size()));
        },
        [&](label fi, const rawFace& f, const DynamicList<label>& vlist)
        {
            face fc(vlist);

            // polyMesh wants owner < neighbour with the normal pointing owner ->
            // neighbour. The emit() calls above already put the -normal side in
            // `own`; only a cell-id inversion on an internal face needs a flip.
            if (f.nei >= 0 && f.own >= 0 && f.own > f.nei)
            {
                if (fc.size() > 1) { std::reverse(fc.begin() + 1, fc.end()); }   // reverseFace
                allOwn[fi] = f.nei;
                allNei[fi] = f.own;
            }
            else if (f.own < 0)
            {
                // boundary face on the -side: flip so the normal points out
                if (fc.size() > 1) { std::reverse(fc.begin() + 1, fc.end()); }   // reverseFace
                allOwn[fi] = f.nei;
                allNei[fi] = -1;
            }
            else
            {
                allOwn[fi] = f.own;
                allNei[fi] = f.nei;
            }
            allPatch[fi] = f.patch;
            allFaces[fi].transfer(fc);
        }
    );

    points_ = pointField(pts);

    // ---------------------------------------------------------------------
    // 5. Order: internal faces upper-triangular, then boundary faces by patch.
    // ---------------------------------------------------------------------
    labelList internalIds(allFaces.size());
    label nInt = 0;
    labelList boundaryIds(allFaces.size());
    label nBnd = 0;
    forAll(allFaces, fi)
    {
        if (allNei[fi] >= 0) { internalIds[nInt++] = fi; }
        else { boundaryIds[nBnd++] = fi; }
    }
    internalIds.setSize(nInt);
    boundaryIds.setSize(nBnd);

    std::sort
    (
        internalIds.begin(), internalIds.end(),
        [&](label p, label q)
        {
            if (allOwn[p] != allOwn[q]) { return allOwn[p] < allOwn[q]; }
            return allNei[p] < allNei[q];
        }
    );

    std::stable_sort
    (
        boundaryIds.begin(), boundaryIds.end(),
        [&](label p, label q) { return allPatch[p] < allPatch[q]; }
    );

    nInternalFaces_ = nInt;
    faces_.setSize(allFaces.size());
    owner_.setSize(allFaces.size());
    neighbour_.setSize(nInt);

    List<DynamicList<label>> zoneFaces(faceZoneNames_.size());
    forAll(internalIds, i)
    {
        const label fi = internalIds[i];
        faces_[i].transfer(allFaces[fi]);
        owner_[i] = allOwn[fi];
        neighbour_[i] = allNei[fi];
        const label z = raw[fi].zone;
        if (z >= 0 && z < zoneFaces.size())
        {
            zoneFaces[z].append(i);
        }
    }
    faceZoneFaces_.setSize(zoneFaces.size());
    forAll(zoneFaces, z) { faceZoneFaces_[z] = labelList(std::move(zoneFaces[z])); }

    patchSizes_.setSize(patchNames_.size(), 0);
    patchStarts_.setSize(patchNames_.size(), 0);

    forAll(boundaryIds, i)
    {
        const label fi = boundaryIds[i];
        const label dst = nInt + i;
        faces_[dst].transfer(allFaces[fi]);
        owner_[dst] = allOwn[fi];
        const label p = max(label(0), allPatch[fi]);
        if (patchSizes_[p] == 0) { patchStarts_[p] = dst; }
        ++patchSizes_[p];
    }

    // Empty patches still need a sane start offset
    label running = nInt;
    forAll(patchSizes_, p)
    {
        if (patchSizes_[p] == 0) { patchStarts_[p] = running; }
        else { running = patchStarts_[p] + patchSizes_[p]; }
    }
}


// * * * * * * * * * * * * * * Member Functions  * * * * * * * * * * * * * * //

void Foam::dcCartMeshBuilder::cornerCells
(
    const dcCartOctree& tree,
    const boolList& alive,
    const std::function<bool(const point&)>& wanted,
    const std::function<bool(label)>& collect,
    pointField& points,
    labelList& cellLevel,
    List<labelVector>& cellIndex,
    std::vector<bool>& wantedPoint,
    std::vector<std::vector<label>>& pointCells
)
{
    std::vector<rawFace> raw;
    DynamicList<point> pts;
    std::vector<bool> keep;
    assembleFaces
    (
        tree, alive,
        [](label, const labelVector&, direction, bool, const point&, bool) -> label { return 0; },
        [](label, const labelVector&, direction, bool) -> label { return -1; },
        cellLevel, cellIndex, pts, raw,
        [&](const DynamicList<point>& p, const std::vector<rawFace>&)
        {
            wantedPoint.assign(p.size(), false);
            keep.assign(p.size(), false);
            pointCells.assign(p.size(), std::vector<label>());
            forAll(p, i)
            {
                if (wanted(p[i]))
                {
                    wantedPoint[i] = true;
                    keep[i] = collect(i);
                }
            }
        },
        [&](label, const rawFace& f, const DynamicList<label>& vlist)
        {
            // the cells on either side of the face list its points
            for (const label v : vlist)
            {
                if (!keep[v]) { continue; }
                std::vector<label>& lst = pointCells[v];
                if (f.own >= 0 && (lst.empty() || lst.back() != f.own)) { lst.push_back(f.own); }
                if (f.nei >= 0 && (lst.empty() || lst.back() != f.nei)) { lst.push_back(f.nei); }
            }
        }
    );
    points = pointField(pts);
}


void Foam::dcCartMeshBuilder::requestedCornerCells
(
    const dcCartOctree& tree,
    const boolList& alive,
    const std::vector<uint64_t>& requestedKeys,
    const std::function<bool(label)>& collect,
    pointField& points,
    labelList& cellLevel,
    List<labelVector>& cellIndex,
    std::vector<bool>& wantedPoint,
    std::vector<std::vector<label>>& pointCells
)
{
    const dcCartGrid& grid = tree.grid();
    const label fineLevel = max(tree.deepestLevel(), label(1));
    labelList levels;
    List<labelVector> indices;
    tree.flatten(levels, indices);
    if (alive.size() != levels.size())
    {
        FatalErrorInFunction << "alive mask does not match the octree" << exit(FatalError);
    }
    label nLive = 0;
    std::vector<size_t> count(fineLevel + 1, 0);
    forAll(alive, c) { if (alive[c]) { ++nLive; ++count[levels[c]]; } }
    cellLevel.setSize(nLive);
    cellIndex.setSize(nLive);
    std::vector<keyLabelMap> liveByLevel(fineLevel + 1);
    for (label l = 0; l <= fineLevel; ++l) { liveByLevel[l].reserve(count[l]); }
    label next = 0;
    forAll(alive, c)
    {
        if (!alive[c]) { continue; }
        cellLevel[next] = levels[c];
        cellIndex[next] = indices[c];
        const labelVector& idx = indices[c];
        liveByLevel[levels[c]].set(dcCartPack(idx.x(), idx.y(), idx.z()), next++);
    }

    DynamicList<point> selected;
    selected.reserve(label(requestedKeys.size()));
    pointCells.clear();
    pointCells.reserve(requestedKeys.size());
    for (const uint64_t key : requestedKeys)
    {
        const labelVector q = dcCartUnpack(key);
        std::vector<label> incident;
        incident.reserve(8);
        // A point belongs to every leaf whose closed box touches it, not
        // just leaves that list it among their own eight corners. Probe the
        // open finest-grid octants, using integer indices rather than a
        // physical epsilon that would fail on a nonuniform background.
        for (label k = 0; k < 8; ++k)
        {
            labelVector probe(q.x() - ((k & 1) ? 0 : 1),
                q.y() - ((k & 2) ? 0 : 1), q.z() - ((k & 4) ? 0 : 1));
            bool in = true;
            for (direction d = 0; d < 3; ++d)
            {
                if (probe[d] < 0 || probe[d] >= grid.nAtLevel(d, fineLevel)) { in = false; break; }
            }
            if (!in) { continue; }
            for (label l = fineLevel; l >= 0; --l)
            {
                const label sh = fineLevel - l;
                const label c = liveByLevel[l].find(dcCartPack(probe.x() >> sh, probe.y() >> sh, probe.z() >> sh));
                if (c >= 0) { incident.push_back(c); break; }
            }
        }
        if (incident.empty()) { continue; }
        std::sort(incident.begin(), incident.end());
        incident.erase(std::unique(incident.begin(), incident.end()), incident.end());

        // An arbitrary requested point inside a coarse boundary face need
        // not be a registered stair-step vertex. Raw face corners consist
        // of leaf corners and the quadrants of a FINER face neighbour (even
        // when that neighbour is dead). Once registered, hanging points on
        // other incident leaf edges are spliced into their face lists too.
        bool registered = false;
        for (label c : incident)
        {
            const label l = cellLevel[c], sh = fineLevel - l, step = 1 << sh;
            const labelVector& idx = cellIndex[c];
            const labelVector lo(idx.x() << sh, idx.y() << sh, idx.z() << sh);
            bool edge[3];
            for (direction d = 0; d < 3; ++d) { edge[d] = q[d] == lo[d] || q[d] == lo[d] + step; }
            if (edge[0] && edge[1] && edge[2]) { registered = true; break; }
            if (!sh) { continue; }
            for (direction d = 0; d < 3 && !registered; ++d)
            {
                if (!edge[d]) { continue; }
                label nl = -1;
                labelVector ni(labelVector::zero);
                const auto kind = tree.neighbour(l, idx, d, q[d] == lo[d] + step, nl, ni);
                if (kind != dcCartOctree::FINER) { continue; }
                const direction u = (d + 1) % 3, v = (d + 2) % 3;
                const auto quadrantCorner = [&](direction a) -> bool
                {
                    return q[a] == lo[a] || q[a] == lo[a] + step/2 || q[a] == lo[a] + step;
                };
                if (quadrantCorner(u) && quadrantCorner(v)) { registered = true; }
            }
            if (registered) { break; }
        }
        if (!registered) { continue; }
        const label p = selected.size();
        selected.append(point(grid.planeCoord(0, fineLevel, q.x()),
            grid.planeCoord(1, fineLevel, q.y()), grid.planeCoord(2, fineLevel, q.z())));
        pointCells.push_back(collect(p) ? std::move(incident) : std::vector<label>());
    }
    points = pointField(selected);
    wantedPoint.assign(points.size(), true);
}


Foam::string Foam::dcCartMeshBuilder::info() const
{
    OStringStream os;
    os  << "cells " << nCells()
        << ", points " << points_.size()
        << ", faces " << faces_.size()
        << " (internal " << nInternalFaces_
        << ", boundary " << (faces_.size() - nInternalFaces_) << ')';
    return os.str();
}


Foam::autoPtr<Foam::polyMesh> Foam::dcCartMeshBuilder::mesh
(
    const Time& runTime,
    const word& regionName
) const
{
    // polyMesh takes ownership of the primitives, so hand it copies: the
    // builder stays queryable afterwards (the app reports on it, and the zone
    // pass needs cellLevel()/cellIndex()).
    pointField pts(points_);
    faceList fcs(faces_);
    labelList own(owner_);
    labelList nei(neighbour_);

    autoPtr<polyMesh> meshPtr
    (
        new polyMesh
        (
            IOobject
            (
                regionName,
                runTime.constant(),
                runTime,
                IOobject::NO_READ,
                IOobject::AUTO_WRITE
            ),
            std::move(pts),
            std::move(fcs),
            std::move(own),
            std::move(nei)
        )
    );

    List<polyPatch*> patches(patchNames_.size());
    forAll(patchNames_, p)
    {
        patches[p] = polyPatch::New
        (
            patchTypes_[p],
            patchNames_[p],
            patchSizes_[p],
            patchStarts_[p],
            p,
            meshPtr->boundaryMesh()
        ).ptr();
    }
    meshPtr->addPatches(patches);

    if (faceZoneNames_.size())
    {
        PtrList<pointZone> pz(0);
        PtrList<cellZone> cz(0);
        PtrList<faceZone> fz(faceZoneNames_.size());
        forAll(faceZoneNames_, z)
        {
            // flipMap all false: the face is already oriented owner -> neighbour
            fz.set
            (
                z,
                new faceZone
                (
                    faceZoneNames_[z],
                    faceZoneFaces_[z],
                    false,
                    z,
                    meshPtr->faceZones()
                )
            );
        }
        meshPtr->addZones(std::move(pz), std::move(fz), std::move(cz));
    }

    return meshPtr;
}
