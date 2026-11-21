#include "dcCartGrid.H"

#include <vector>
#include "ListOps.H"
#include "OStringStream.H"
#include <algorithm>

// * * * * * * * * * * * * * * Private Members * * * * * * * * * * * * * * * //

Foam::scalarField Foam::dcCartGrid::cleaned
(
    const scalarField& raw,
    const word& axis
)
{
    if (raw.size() < 2)
    {
        FatalErrorInFunction
            << "Axis " << axis << " needs at least 2 breakpoints, got "
            << raw.size() << exit(FatalError);
    }

    List<scalar> sorted(raw);
    std::sort(sorted.begin(), sorted.end());

    // Collapse breakpoints that a face-aligned grid generator emitted twice
    // (two objects sharing a face plane). The tolerance is relative to the
    // overall extent so it scales with the model.
    const scalar span = sorted.last() - sorted.first();
    if (span <= 0)
    {
        FatalErrorInFunction
            << "Axis " << axis << " has zero extent (" << sorted.first()
            << " .. " << sorted.last() << ')' << exit(FatalError);
    }
    const scalar tol = 1e-9 * span;

    DynamicList<scalar> out(sorted.size());
    out.append(sorted[0]);
    for (label i = 1; i < sorted.size(); ++i)
    {
        if (sorted[i] - out.last() > tol)
        {
            out.append(sorted[i]);
        }
    }

    if (out.size() < 2)
    {
        FatalErrorInFunction
            << "Axis " << axis << " collapsed to a single plane" << exit(FatalError);
    }

    return scalarField(std::move(out));
}


// * * * * * * * * * * * * * * * Constructors  * * * * * * * * * * * * * * * //

Foam::dcCartGrid::dcCartGrid
(
    const scalarField& x,
    const scalarField& y,
    const scalarField& z
)
{
    coords_[0] = cleaned(x, "x");
    coords_[1] = cleaned(y, "y");
    coords_[2] = cleaned(z, "z");
}


Foam::dcCartGrid::dcCartGrid(const dictionary& dict)
{
    coords_[0] = cleaned(dict.get<scalarField>("x"), "x");
    coords_[1] = cleaned(dict.get<scalarField>("y"), "y");
    coords_[2] = cleaned(dict.get<scalarField>("z"), "z");
}


Foam::dcCartGrid::dcCartGrid(const boundBox& bb, const labelVector& nCells)
{
    for (direction cmpt = 0; cmpt < 3; ++cmpt)
    {
        const label n = max(label(1), nCells[cmpt]);
        scalarField pts(n + 1);
        const scalar lo = bb.min()[cmpt];
        const scalar hi = bb.max()[cmpt];
        for (label i = 0; i <= n; ++i)
        {
            pts[i] = lo + (hi - lo) * scalar(i) / scalar(n);
        }
        coords_[cmpt] = pts;
    }
}


// * * * * * * * * * * * * * * Member Functions  * * * * * * * * * * * * * * //

Foam::scalar Foam::dcCartGrid::planeCoord
(
    direction cmpt,
    label level,
    label i
) const
{
    const scalarField& c = coords_[cmpt];
    const label sub = label(1) << level;
    const label bg = i >> level;         // background interval
    const label rem = i - (bg << level); // position inside it

    if (bg >= c.size() - 1)
    {
        // The top plane of the domain: i == nAtLevel, rem == 0.
        return c.last();
    }
    if (rem == 0)
    {
        return c[bg];
    }
    return c[bg] + (c[bg + 1] - c[bg]) * scalar(rem) / scalar(sub);
}


Foam::boundBox Foam::dcCartGrid::cellBox
(
    label level,
    const labelVector& idx
) const
{
    return boundBox
    (
        point
        (
            planeCoord(0, level, idx.x()),
            planeCoord(1, level, idx.y()),
            planeCoord(2, level, idx.z())
        ),
        point
        (
            planeCoord(0, level, idx.x() + 1),
            planeCoord(1, level, idx.y() + 1),
            planeCoord(2, level, idx.z() + 1)
        )
    );
}


Foam::point Foam::dcCartGrid::cellCentre
(
    label level,
    const labelVector& idx
) const
{
    return 0.5 * (cellBox(level, idx).min() + cellBox(level, idx).max());
}


Foam::boundBox Foam::dcCartGrid::bounds() const
{
    return boundBox
    (
        point(coords_[0].first(), coords_[1].first(), coords_[2].first()),
        point(coords_[0].last(), coords_[1].last(), coords_[2].last())
    );
}


bool Foam::dcCartGrid::findCell
(
    label level,
    const point& pt,
    labelVector& idx
) const
{
    const label sub = label(1) << level;

    for (direction cmpt = 0; cmpt < 3; ++cmpt)
    {
        const scalarField& c = coords_[cmpt];
        if (pt[cmpt] < c.first() || pt[cmpt] > c.last())
        {
            return false;
        }

        // Background interval by binary search, then the sub-interval inside it.
        label lo = 0;
        label hi = c.size() - 1;
        while (hi - lo > 1)
        {
            const label mid = (lo + hi) / 2;
            if (pt[cmpt] < c[mid]) { hi = mid; } else { lo = mid; }
        }

        const scalar width = c[lo + 1] - c[lo];
        label rem = 0;
        if (width > 0)
        {
            rem = label((pt[cmpt] - c[lo]) / width * scalar(sub));
            rem = min(max(rem, label(0)), sub - 1);
        }
        idx[cmpt] = (lo << level) + rem;
    }

    return true;
}


Foam::label Foam::dcCartGrid::maxRepresentableLevel() const
{
    label level = 0;
    while (level < 24)
    {
        bool ok = true;
        for (direction cmpt = 0; cmpt < 3; ++cmpt)
        {
            // need nAtLevel + 1 addressable (the top plane index)
            const uint64_t needed = uint64_t(n(cmpt)) << (level + 1);
            if (needed > dcCartKeyMask)
            {
                ok = false;
                break;
            }
        }
        if (!ok) { break; }
        ++level;
    }
    return max(label(0), level - 1);
}


Foam::scalar Foam::dcCartGrid::minCellSize(label level) const
{
    scalar smallest = GREAT;
    for (direction cmpt = 0; cmpt < 3; ++cmpt)
    {
        const scalarField& c = coords_[cmpt];
        for (label i = 0; i < c.size() - 1; ++i)
        {
            smallest = min(smallest, c[i + 1] - c[i]);
        }
    }
    return smallest / scalar(label(1) << level);
}


Foam::scalar Foam::dcCartGrid::maxCellSize(label level) const
{
    scalar largest = 0;
    for (direction cmpt = 0; cmpt < 3; ++cmpt)
    {
        const scalarField& c = coords_[cmpt];
        for (label i = 0; i < c.size() - 1; ++i)
        {
            largest = max(largest, c[i + 1] - c[i]);
        }
    }
    return largest / scalar(label(1) << level);
}


Foam::scalar Foam::dcCartGrid::medianSpacing() const
{
    std::vector<scalar> spacings;
    for (direction cmpt = 0; cmpt < 3; ++cmpt)
    {
        const scalarField& c = coords_[cmpt];
        for (label i = 1; i < c.size(); ++i)
        {
            spacings.push_back(c[i] - c[i - 1]);
        }
    }
    if (spacings.empty()) { return 1.0; }
    std::sort(spacings.begin(), spacings.end());
    return spacings[spacings.size() / 2];
}


Foam::string Foam::dcCartGrid::info() const
{
    OStringStream os;
    const boundBox bb = bounds();
    os  << "background " << nx() << 'x' << ny() << 'x' << nz()
        << " = " << nBackgroundCells() << " cells"
        << ", bounds " << bb.min() << " .. " << bb.max()
        << ", cell size " << minCellSize(0) << " .. " << maxCellSize(0);
    return os.str();
}
