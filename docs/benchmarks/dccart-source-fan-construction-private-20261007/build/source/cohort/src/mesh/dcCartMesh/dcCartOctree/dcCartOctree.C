#include "dcCartOctree.H"
#include "OStringStream.H"
#include <algorithm>

// * * * * * * * * * * * * * * * Constructors  * * * * * * * * * * * * * * * //

Foam::dcCartOctree::dcCartOctree(const dcCartGrid& grid, label maxLevel)
:
    grid_(grid),
    maxLevel_(min(maxLevel, grid.maxRepresentableLevel())),
    leaves_()
{
    if (maxLevel > maxLevel_)
    {
        WarningInFunction
            << "Requested maxLevel " << maxLevel << " exceeds what the packed "
            << "cell key can address for this background grid; clamped to "
            << maxLevel_ << endl;
    }
    ensureLevel(maxLevel_);
}


// * * * * * * * * * * * * * * Private Members * * * * * * * * * * * * * * * //

void Foam::dcCartOctree::ensureLevel(label level)
{
    const label have = label(leaves_.size());
    if (have <= level)
    {
        leaves_.resize(level + 1);
        internal_.resize(level + 1);
        for (label L = have; L <= level; ++L)
        {
            const label nx = grid_.nAtLevel(0, L), ny = grid_.nAtLevel(1, L), nz = grid_.nAtLevel(2, L);
            leaves_[L].setShape(nx, ny, nz);
            internal_[L].setShape(nx, ny, nz);
        }
    }
}


// * * * * * * * * * * * * * * Member Functions  * * * * * * * * * * * * * * //

Foam::label Foam::dcCartOctree::deepestLevel() const
{
    for (label L = label(leaves_.size()) - 1; L >= 0; --L)
    {
        if (!leaves_[L].empty()) { return L; }
    }
    return 0;
}


Foam::label Foam::dcCartOctree::nLeaves() const
{
    label n = 0;
    for (const auto& s : leaves_) { n += label(s.size()); }
    return n;
}


Foam::label Foam::dcCartOctree::nLeavesAt(label level) const
{
    if (level < 0 || level >= label(leaves_.size())) { return 0; }
    return label(leaves_[level].size());
}


const Foam::dcCartLevelSet& Foam::dcCartOctree::leavesAt(label level) const
{
    static const dcCartLevelSet empty;
    if (level < 0 || level >= label(leaves_.size())) { return empty; }
    return leaves_[level];
}


bool Foam::dcCartOctree::isLeaf(label level, const labelVector& idx) const
{
    if (level < 0 || level >= label(leaves_.size())) { return false; }
    if (!inRange(level, idx)) { return false; }
    return leaves_[level].count(dcCartPack(idx.x(), idx.y(), idx.z())) != 0;
}


void Foam::dcCartOctree::seed(label level)
{
    for (auto& s : leaves_) { s.clear(); }
    for (auto& s : internal_) { s.clear(); }
    ensureLevel(level);

    const label nx = grid_.nAtLevel(0, level);
    const label ny = grid_.nAtLevel(1, level);
    const label nz = grid_.nAtLevel(2, level);

    auto& set = leaves_[level];
    set.reserve(size_t(nx) * size_t(ny) * size_t(nz));
    for (label i = 0; i < nx; ++i)
    {
        for (label j = 0; j < ny; ++j)
        {
            for (label k = 0; k < nz; ++k)
            {
                set.insert(dcCartPack(i, j, k));
            }
        }
    }
}


Foam::label Foam::dcCartOctree::refine
(
    label level,
    const List<labelVector>& idx
)
{
    if (level < 0 || level >= maxLevel_) { return 0; }
    ensureLevel(level + 1);

    label nRefined = 0;
    for (const labelVector& c : idx)
    {
        const uint64_t key = dcCartPack(c.x(), c.y(), c.z());
        if (!leaves_[level].erase(key)) { continue; }
        internal_[level].insert(key);

        const label i0 = c.x() * 2;
        const label j0 = c.y() * 2;
        const label k0 = c.z() * 2;
        for (label di = 0; di < 2; ++di)
        {
            for (label dj = 0; dj < 2; ++dj)
            {
                for (label dk = 0; dk < 2; ++dk)
                {
                    leaves_[level + 1].insert
                    (
                        dcCartPack(i0 + di, j0 + dj, k0 + dk)
                    );
                }
            }
        }
        ++nRefined;
    }
    return nRefined;
}


Foam::label Foam::dcCartOctree::refineKeys
(
    label level,
    const List<uint64_t>& keys
)
{
    List<labelVector> idx(keys.size());
    forAll(keys, i) { idx[i] = dcCartUnpack(keys[i]); }
    return refine(level, idx);
}


Foam::label Foam::dcCartOctree::containingLevel
(
    label level,
    const labelVector& idx
) const
{
    if (!inRange(level, idx)) { return -1; }

    labelVector cur(idx);
    for (label L = level; L >= 0; --L)
    {
        if (L < label(leaves_.size()))
        {
            const uint64_t key = dcCartPack(cur.x(), cur.y(), cur.z());
            if (leaves_[L].count(key)) { return L; }
            // refined here: every cell above it was refined too, so no leaf
            // at this level or coarser holds idx (the walk would end at -1)
            if (internal_[L].count(key)) { return -1; }
        }
        cur = labelVector(cur.x() / 2, cur.y() / 2, cur.z() / 2);
    }
    return -1;
}


Foam::dcCartOctree::neighbourType Foam::dcCartOctree::neighbour
(
    label level,
    const labelVector& idx,
    direction cmpt,
    bool positive,
    label& nbrLevel,
    labelVector& nbrIdx
) const
{
    labelVector n(idx);
    n[cmpt] += (positive ? 1 : -1);

    if (!inRange(level, n))
    {
        nbrLevel = -1;
        return OUTSIDE;
    }

    // A leaf at this level or coarser covering the neighbour index?
    const label lvl = containingLevel(level, n);
    if (lvl == level)
    {
        nbrLevel = level;
        nbrIdx = n;
        return SAME;
    }
    if (lvl >= 0 && lvl < level)
    {
        nbrLevel = lvl;
        nbrIdx = labelVector(n);
        for (label L = level; L > lvl; --L)
        {
            nbrIdx = labelVector(nbrIdx.x() / 2, nbrIdx.y() / 2, nbrIdx.z() / 2);
        }
        return COARSER;
    }

    // Nothing at this level or coarser → the region is subdivided. With a
    // balanced tree that means exactly one level finer.
    nbrLevel = level + 1;
    nbrIdx = n;
    return FINER;
}


void Foam::dcCartOctree::finerFaceNeighbours
(
    label level,
    const labelVector& idx,
    direction cmpt,
    bool positive,
    FixedList<labelVector, 4>& out
) const
{
    // The four level-(level+1) cells that touch this face from the other side.
    labelVector base(idx);
    base[cmpt] += (positive ? 1 : -1);
    base = labelVector(base.x() * 2, base.y() * 2, base.z() * 2);

    // On the far side of the face, the four children hug the shared plane.
    if (positive)
    {
        // neighbour block's low face is the shared plane -> children with
        // sub-index 0 along cmpt
    }
    else
    {
        base[cmpt] += 1; // children with sub-index 1 along cmpt
    }

    const direction a = (cmpt + 1) % 3;
    const direction b = (cmpt + 2) % 3;

    label n = 0;
    for (label da = 0; da < 2; ++da)
    {
        for (label db = 0; db < 2; ++db)
        {
            labelVector c(base);
            c[a] += da;
            c[b] += db;
            out[n++] = c;
        }
    }
}


Foam::label Foam::dcCartOctree::balance(label nBuffer)
{
    // Ripple: a leaf may only face a leaf at most one level coarser. Refining
    // a coarse leaf can in turn violate balance with ITS neighbours, so repeat
    // until a whole sweep changes nothing. Converges in a handful of sweeps for
    // the depths this mesher uses (<= 6).
    //
    // nBuffer widens the transition: with nBuffer = 1 (snappy's
    // nCellsBetweenLevels) a level jump is pushed one extra cell away, so the
    // finer level always has a cell of its own between it and the jump.
    label totalRefined = 0;

    for (label sweep = 0; sweep < 64; ++sweep)
    {
        label refinedThisSweep = 0;

        for (label L = deepestLevel(); L >= 1; --L)
        {
            // Every leaf strictly coarser than L-1 that lies within nBuffer+1
            // cells of a level-L leaf must be refined.
            std::unordered_set<uint64_t> toRefineByLevel[32];

            for (uint64_t key : leaves_[L])
            {
                const labelVector c = dcCartUnpack(key);

                for (direction cmpt = 0; cmpt < 3; ++cmpt)
                {
                    for (label s = -1; s <= 1; s += 2)
                    {
                        for (label step = 1; step <= nBuffer + 1; ++step)
                        {
                            labelVector n(c);
                            n[cmpt] += s * step;
                            if (!inRange(L, n)) { break; }

                            const label lvl = containingLevel(L, n);
                            if (lvl < 0 || lvl >= L - 1) { break; }

                            // too coarse: refine it (one step; the sweep repeats)
                            labelVector coarse(n);
                            for (label k = L; k > lvl; --k)
                            {
                                coarse = labelVector
                                (
                                    coarse.x() / 2, coarse.y() / 2, coarse.z() / 2
                                );
                            }
                            toRefineByLevel[lvl].insert
                            (
                                dcCartPack(coarse.x(), coarse.y(), coarse.z())
                            );
                            break;
                        }
                    }
                }
            }

            for (label lvl = 0; lvl < 32; ++lvl)
            {
                if (toRefineByLevel[lvl].empty()) { continue; }
                List<uint64_t> keys(label(toRefineByLevel[lvl].size()));
                label n = 0;
                for (uint64_t k : toRefineByLevel[lvl]) { keys[n++] = k; }
                refinedThisSweep += refineKeys(lvl, keys);
            }
        }

        totalRefined += refinedThisSweep;
        if (refinedThisSweep == 0) { break; }
    }

    return totalRefined;
}


void Foam::dcCartOctree::flatten
(
    labelList& levels,
    List<labelVector>& indices
) const
{
    const label n = nLeaves();
    levels.setSize(n);
    indices.setSize(n);

    label c = 0;
    for (label L = 0; L < label(leaves_.size()); ++L)
    {
        // Sort keys so the cell order (and therefore the whole mesh) is
        // reproducible run to run despite the hash set.
        std::vector<uint64_t> keys(leaves_[L].begin(), leaves_[L].end());
        std::sort(keys.begin(), keys.end());
        for (uint64_t key : keys)
        {
            levels[c] = L;
            indices[c] = dcCartUnpack(key);
            ++c;
        }
    }
}


Foam::string Foam::dcCartOctree::info() const
{
    OStringStream os;
    os << "leaves " << nLeaves() << " (";
    bool first = true;
    for (label L = 0; L < label(leaves_.size()); ++L)
    {
        if (leaves_[L].empty()) { continue; }
        if (!first) { os << ", "; }
        os << "L" << L << ':' << label(leaves_[L].size());
        first = false;
    }
    os << ')';
    return os.str();
}
