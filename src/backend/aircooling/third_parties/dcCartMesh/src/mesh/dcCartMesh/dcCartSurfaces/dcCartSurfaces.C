#include <chrono>
#include "dcCartSurfaces.H"
#include "dcCartAsciiSTLPrecision.H"
#include "STLReader.H"
#include "IFstream.H"
#include "mergePoints.H"
#include <cstdint>
#include <memory>
#include <stdexcept>
#include "dcCartServerComponentCoverage.H"
#include "dcCartGrid.H"
#include "dcCartPlanarSheetPerimeterWeld.H"
#include "treeBoundBox.H"
#include "HashTableCore.H"
#include "OStringStream.H"
#include "volumeType.H"
#include "Map.H"
#include "mathematicalConstants.H"
#include "Pstream.H"
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <functional>
#include <unordered_map>
#include <unordered_set>
#include <map>
#include <set>
#include <tuple>

namespace
{

// ASCII keeps authoritative decimal coordinates; official reader owns metadata.
Foam::autoPtr<Foam::triSurfaceMesh> readSurfacePreservingAscii(const Foam::IOobject& io)
{
    using namespace Foam;
    const fileName logical(io.name());
    const word ext=logical.has_ext("gz")?logical.stem().ext():logical.ext();
    if(ext!="stl")return autoPtr<triSurfaceMesh>(new triSurfaceMesh(io));
    const fileName actual=triSurface::checkFile(io,true);
    fileFormats::STLReader reader(actual);
    if(reader.stlFormat()!=fileFormats::STLCore::ASCII)
        return autoPtr<triSurfaceMesh>(new triSurfaceMesh(io));
    auto failure=[&](const char* reason)->void
    {throw std::runtime_error(std::string("ASCII STL source ")+actual.c_str()+": "+reason);};
    const auto& original=reader.points();const auto& zones=reader.zoneIds();
    const auto& names=reader.names();const auto& sizes=reader.sizes();
    if(original.size()%3 || original.size()/3!=zones.size() || names.size()!=sizes.size())
        failure("ASCII STL official inventory sizes");
    std::int64_t total=0;
    for(const label count:sizes){if(count<0)failure("ASCII STL negative region size");total+=count;}
    if(total!=zones.size())failure("ASCII STL region facet total");
    for(const label zone:zones)if(zone<0 || zone>=names.size())
        failure("ASCII STL region address");
    IFstream input(actual);std::vector<std::array<double,3>> decimal;std::string why;
    if(!input.good() || !dcCartAsciiSTLPrecision::readPoints(input.stdStream(),decimal,&why))
        throw std::runtime_error(std::string("ASCII STL source ")+actual.c_str()+": DP replay: "+why);
    if(decimal.size()!=std::size_t(original.size()))
        failure("ASCII STL DP vertex count");
    pointField precise(original.size());
    label changedRawVertices=0;scalar maxPromotedFloatDelta=0;
    forAll(original,i)for(direction d=0;d<3;++d)
    {
        const double value=decimal[std::size_t(i)][d];const float replay=static_cast<float>(value);
        if(!std::isfinite(value) || !std::isfinite(replay) || replay!=original[i][d]
            || std::signbit(replay)!=std::signbit(original[i][d]))
            failure("ASCII STL DP order/float-roundtrip");
        precise[i][d]=value;
    }
    forAll(precise,i)
    {
        const point promoted(original[i][0],original[i][1],original[i][2]);
        const scalar delta=mag(precise[i]-promoted);
        if(delta>0)++changedRawVertices;
        maxPromotedFloatDelta=max(maxPromotedFloatDelta,delta);
    }
    labelList pointMap;
    const label unique=Foam::mergePoints(precise,100*doubleScalarSMALL,false,pointMap);
    pointField points(unique);
    forAll(precise,i)points[pointMap[i]]=precise[i]; // Official last-write semantics.
    List<labelledTri> faces(zones.size());
    forAll(faces,i)
    {faces[i]=labelledTri(pointMap[3*i],pointMap[3*i+1],pointMap[3*i+2],zones[i]);}
    geometricSurfacePatchList patches(names.size());
    forAll(names,i)patches[i]=geometricSurfacePatch(names[i],i);
    const triSurface surface(faces,patches,points);
    const auto previousPrecision=Pout.precision();Pout.precision(17);
    Pout<< "ASCII STL DP fidelity source=" << logical << " format=ASCII scalarBits="
        << (8*sizeof(scalar)) << " rawVertices=" << original.size()
        << " uniquePoints=" << unique << " triangles=" << faces.size()
        << " regions=" << names.size() << " names=" << names
        << " changedRawVertices=" << changedRawVertices
        << " maxPromotedFloatDelta=" << maxPromotedFloatDelta << nl;
    Pout.precision(previousPrecision);
    return autoPtr<triSurfaceMesh>(new triSurfaceMesh(io,surface));
}



//- Every triangle indexedOctree::findBox would report for `searchBox`, in the
//  same node order, handed to `visit` (return true to stop). A triangle
//  stored in several content nodes comes more than once, where findBox
//  folds the repeats in a HashSet; building that set for a yes/no question
//  was most of the refinement time.
template<class Visit>
bool visitBox
(
    const Foam::indexedOctree<Foam::treeDataTriSurface>& tree,
    const Foam::label nodeI,
    const Foam::treeBoundBox& searchBox,
    Visit& visit
)
{
    // indexedOctree::findBox's walk: every shape of every content octant it
    // would look at goes to `visit`, which applies the shape test itself
    // (return true to stop). A triangle stored in several octants comes
    // more than once.
    using octree = Foam::indexedOctree<Foam::treeDataTriSurface>;
    const octree::node& nod = tree.nodes()[nodeI];
    const Foam::treeBoundBox& nb = nod.bb_;
    const Foam::point mid = nb.centre();
    const Foam::point& smin = searchBox.min();
    const Foam::point& smax = searchBox.max();
    for (Foam::direction octant = 0; octant < 8; ++octant)
    {
        const Foam::labelBits index = nod.subNodes_[octant];
        if (octree::isNode(index))
        {
            const Foam::label sub = octree::getNode(index);
            if (tree.nodes()[sub].bb_.overlaps(searchBox))
            {
                if (visitBox(tree, sub, searchBox, visit)) { return true; }
            }
        }
        else if (octree::isContent(index))
        {
            // treeBoundBox::subOverlaps, inline
            Foam::point subMin = mid;
            Foam::point subMax = mid;
            if (octant & 1) { subMax.x() = nb.max().x(); } else { subMin.x() = nb.min().x(); }
            if (octant & 2) { subMax.y() = nb.max().y(); } else { subMin.y() = nb.min().y(); }
            if (octant & 4) { subMax.z() = nb.max().z(); } else { subMin.z() = nb.min().z(); }
            if
            (
                subMin.x() <= smax.x() && smin.x() <= subMax.x()
             && subMin.y() <= smax.y() && smin.y() <= subMax.y()
             && subMin.z() <= smax.z() && smin.z() <= subMax.z()
            )
            {
                for (const Foam::label t : tree.contents()[octree::getContent(index)])
                {
                    if (visit(t)) { return true; }
                }
            }
        }
    }
    return false;
}


//- boundBox::intersects(const triPointRef&) from a triangle's precomputed
//  axes. A box's extent along an axis is the axis dotted with the corner
//  taking, per component, the lower or upper bound by the component's sign:
//  the dot product is monotone in every coordinate, so that corner gives the
//  value the eight-corner scan settles on (min) and its mirror the max. The
//  triangle's extent along the box normals is its bounding box.
inline bool boxTriSat
(
    const Foam::boundBox& bb,
    const Foam::boundBox& triBb,
    const Foam::dcCartTriSat& t
)
{
    using Foam::scalar;
    using Foam::vector;
    const Foam::point& mn = bb.min();
    const Foam::point& mx = bb.max();
    for (int d = 0; d < 3; ++d)
    {
        const scalar dist = (mx[d] - mn[d]);
        if (!(dist > 0)) { return false; }
    }
    auto inBox = [&](const Foam::point& p) -> bool
    {
        return
        (
            mn.x() <= p.x() && p.x() <= mx.x()
         && mn.y() <= p.y() && p.y() <= mx.y()
         && mn.z() <= p.z() && p.z() <= mx.z()
        );
    };
    if (inBox(t.a) || inBox(t.b) || inBox(t.c)) { return true; }
    for (int d = 0; d < 3; ++d)
    {
        if (!(triBb.min()[d] <= mx[d] && mn[d] <= triBb.max()[d])) { return false; }
    }
    auto boxRange = [&](const vector& u, scalar& lo, scalar& hi)
    {
        lo =
        (
            u.x()*(u.x() > 0 ? mn.x() : mx.x())
          + u.y()*(u.y() > 0 ? mn.y() : mx.y())
          + u.z()*(u.z() > 0 ? mn.z() : mx.z())
        );
        hi =
        (
            u.x()*(u.x() > 0 ? mx.x() : mn.x())
          + u.y()*(u.y() > 0 ? mx.y() : mn.y())
          + u.z()*(u.z() > 0 ? mx.z() : mn.z())
        );
    };
    {
        scalar blo, bhi;
        boxRange(t.n, blo, bhi);
        if (!(t.tn <= bhi && blo <= t.tn)) { return false; }
    }
    for (int k = 0; k < 9; ++k)
    {
        scalar blo, bhi;
        boxRange(t.axis[k], blo, bhi);
        if (!(t.lo[k] <= bhi && blo <= t.hi[k])) { return false; }
    }
    return true;
}


//- treeDataPrimitivePatch<triSurface>::overlaps on a cached triangle box
inline bool triBoxOverlap
(
    const Foam::treeBoundBox& searchBox,
    const Foam::boundBox& triBb,
    const Foam::dcCartTriSat& sat
)
{
    if (!searchBox.overlaps(triBb)) { return false; }
    return boxTriSat(searchBox, triBb, sat);
}


//- snappy's resolveFeatureAngle test, fed one triangle at a time: two of the
//  triangles seen with normals more than 30 degrees apart. The answer of the
//  all-pairs loop, from the distinct normals only.
struct featureScan
{
    const Foam::vectorField& fn;
    std::vector<Foam::vector> normals;     // distinct unit normals so far
    std::vector<Foam::label> degenerate;   // triangles with no normal
    Foam::label nDistinct = 0;

    explicit featureScan(const Foam::vectorField& n) : fn(n) {}

    bool add(const Foam::label t)
    {
        const Foam::vector& n = fn[t];
        if ((n & n) < 0.866)
        {
            // a zero normal pairs below the threshold with ANY other triangle
            if (std::find(degenerate.begin(), degenerate.end(), t) != degenerate.end()) { return false; }
            if (nDistinct > 0) { return true; }
            degenerate.push_back(t);
            ++nDistinct;
            return false;
        }
        if (!degenerate.empty()) { return true; }
        for (const Foam::vector& m : normals)
        {
            if (m == n) { ++nDistinct; return false; }
        }
        for (const Foam::vector& m : normals)
        {
            if ((m & n) < 0.866) { return true; }
        }
        normals.push_back(n);
        ++nDistinct;
        return false;
    }
};


static constexpr uint32_t allTriangles = 0xFFFFFFFFu;


//- treeDataTriSurface::findIntersectOp (or findAllIntersectOp with `mask`),
//  asked only of triangles whose grown box the segment can reach
struct boxedIntersectOp
{
    const Foam::indexedOctree<Foam::treeDataTriSurface>& tree;
    const Foam::List<Foam::boundBox>& grown;
    const Foam::DynamicList<Foam::label>* mask;

    bool operator()
    (
        const Foam::label index,
        const Foam::point& start,
        const Foam::point& end,
        Foam::point& intersectionPoint
    ) const
    {
        const Foam::boundBox& bb = grown[index];
        for (Foam::direction d = 0; d < 3; ++d)
        {
            if (start[d] < bb.min()[d] && end[d] < bb.min()[d]) { return false; }
            if (start[d] > bb.max()[d] && end[d] > bb.max()[d]) { return false; }
        }
        if (mask && mask->found(index)) { return false; }
        return Foam::treeDataTriSurface::findIntersection(tree, index, start, end, intersectionPoint);
    }
};


//- triSurfaceSearch::checkUniqueHit, line for line
bool uniqueHit
(
    const Foam::triSurface& surf,
    const Foam::pointIndexHit& currHit,
    const Foam::UList<Foam::pointIndexHit>& hits,
    const Foam::vector& lineVec
)
{
    using namespace Foam;
    label nearType = -1;
    label nearLabel = -1;
    const labelledTri& f = surf[currHit.index()];
    f.nearestPointClassify(currHit.hitPoint(), surf.points(), nearType, nearLabel);
    if (nearType == triPointRef::POINT)
    {
        const label nearPointi = f[nearLabel];
        const labelList& pointFaces = surf.pointFaces()[surf.meshPointMap()[nearPointi]];
        forAll(pointFaces, pI)
        {
            const label pointFacei = pointFaces[pI];
            if (pointFacei != currHit.index())
            {
                forAll(hits, hI)
                {
                    if (hits[hI].index() == pointFacei) { return false; }
                }
            }
        }
    }
    else if (nearType == triPointRef::EDGE)
    {
        const labelList& fEdges = surf.faceEdges()[currHit.index()];
        const label edgeI = fEdges[nearLabel];
        const labelList& edgeFaces = surf.edgeFaces()[edgeI];
        forAll(edgeFaces, fI)
        {
            const label edgeFacei = edgeFaces[fI];
            if (edgeFacei != currHit.index())
            {
                forAll(hits, hI)
                {
                    if (hits[hI].index() == edgeFacei)
                    {
                        const vector currHitNormal = surf.faceNormals()[currHit.index()];
                        const vector existingHitNormal = surf.faceNormals()[edgeFacei];
                        const label signCurrHit = pos0(currHitNormal & lineVec);
                        const label signExistingHit = pos0(existingHitNormal & lineVec);
                        if (signCurrHit == signExistingHit) { return false; }
                    }
                }
            }
        }
    }
    return true;
}

} // End anonymous namespace

// * * * * * * * * * * * * * * * Constructors  * * * * * * * * * * * * * * * //

void Foam::dcCartSurfaces::retirePreparedShellCycles()
{
    ++shellGeometryEpoch_;
    if (!shellGeometryEpoch_) { ++shellGeometryEpoch_; }
    shellMembershipEpoch_ = 0;
    shellCycleCache_.clear();
}


void Foam::dcCartSurfaces::snapSurfaceCoordinates(scalar tol)
{
    retirePreparedShellCycles();
    coordSnapTol_ = 0;
    nCoordSnapped_ = 0;
    for (direction ax = 0; ax < 3; ++ax) { snapCanon_[ax].clear(); }
    if (tol <= 0) { return; }

    // One cluster per run of DISTINCT coordinates that all lie within `tol` of
    // the run's first value: no chaining, so a cluster spans at most `tol` and
    // two genuinely different planes are never merged into one. The canonical
    // value is the run's mean, which is a pure function of the sorted run --
    // the same on every rank, so the mesh does not depend on the rank count.
    std::vector<scalar> vals[3];
    forAll(surfaces_, i)
    {
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
        for (const point& pt : ts.points())
        {
            for (direction ax = 0; ax < 3; ++ax)
            {
                vals[ax].push_back(pt.component(ax));
            }
        }
    }
    for (direction ax = 0; ax < 3; ++ax)
    {
        std::vector<scalar>& v = vals[ax];
        if (v.empty()) { continue; }
        std::sort(v.begin(), v.end());
        v.erase(std::unique(v.begin(), v.end()), v.end());
        for (size_t start = 0; start < v.size(); )
        {
            size_t end = start + 1;
            while (end < v.size() && (v[end] - v[start]) <= tol) { ++end; }
            if (end - start > 1)
            {
                scalar mean = 0;
                for (size_t k = start; k < end; ++k) { mean += v[k]; }
                snapCanon_[ax].push_back(mean / scalar(end - start));
                nCoordSnapped_ += label(end - start);
            }
            start = end;
        }
        std::sort(snapCanon_[ax].begin(), snapCanon_[ax].end());
    }
    if (!nCoordSnapped_) { return; }
    coordSnapTol_ = tol;

    label nMovedPoints = 0, nDropped = 0;
    forAll(surfaces_, i)
    {
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
        pointField pts(ts.points());
        bool changed = false;
        for (point& pt : pts)
        {
            const point q = snapPoint(pt);
            if (q != pt) { pt = q; changed = true; }
        }
        if (!changed) { continue; }
        nMovedPoints += pts.size();
        // The snap makes two copies of one coordinate bit-equal, so their
        // points must become ONE label too: CMI-Guichou's rack J7 sits at
        // z=11.6079995, its server fronts arrive as 11.728 on some facets and
        // 11.727999 on others, and with the corners left on separate labels
        // every server box of the rack split into open pieces, was cut as
        // thin sheets, and stair-stepped open between the hot and cold aisle.
        // (Re-doing the geometry also clears every search tree and cache: nothing
        // downstream can see the pre-snap coordinates.)
        nDropped += setMovedPoints(i, pts);
    }
    if (nMovedPoints)
    {
        Info<< "Surfaces: snapped " << nCoordSnapped_
            << " near-duplicate coordinates (" << nMovedPoints
            << " vertices moved) onto canonical values within " << tol
            << " m, " << nDropped << " collapsed facets dropped" << nl;
    }
}


Foam::label Foam::dcCartSurfaces::setMovedPoints(label i, const pointField& pts)
{
    const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
    labelList pointMap;
    const label nUnique =
        Foam::mergePoints(pts, 100*doubleScalarSMALL, false, pointMap);
    pointField merged(nUnique);
    forAll(pts, pI) { merged[pointMap[pI]] = pts[pI]; }
    DynamicList<labelledTri> kept(ts.size());
    label dropped = 0;
    forAll(ts, tI)
    {
        const labelledTri& f = ts[tI];
        const label a = pointMap[f[0]], b = pointMap[f[1]], c = pointMap[f[2]];
        if
        (
            a == b || b == c || a == c
         || mag((merged[b] - merged[a]) ^ (merged[c] - merged[a])) == 0
        )
        {
            ++weldDroppedFacets_[labelPair(i, f.region())];
            ++dropped;
            continue;
        }
        kept.append(labelledTri(a, b, c, f.region()));
    }
    if (nUnique == pts.size() && kept.size() == ts.size())
    {
        surfaces_[i].movePoints(pts);
        return 0;
    }
    const triSurface rebuilt(List<labelledTri>(std::move(kept)), ts.patches(), merged);
    const IOobject io(static_cast<const searchableSurface&>(surfaces_[i]));
    surfaces_.release(i);
    surfaces_.set(i, new triSurfaceMesh(io, rebuilt));
    return dropped;
}


Foam::point Foam::dcCartSurfaces::snapPoint(const point& p) const
{
    if (coordSnapTol_ <= 0) { return p; }
    point q(p);
    for (direction ax = 0; ax < 3; ++ax)
    {
        const std::vector<scalar>& c = snapCanon_[ax];
        if (c.empty()) { continue; }
        const scalar v = p.component(ax);
        // canonical values of different clusters can be closer than tol, so
        // take the NEAREST of the two candidates bracketing v
        const auto it = std::lower_bound(c.begin(), c.end(), v);
        scalar best = 0;
        bool have = false;
        for (label k = -1; k <= 0; ++k)
        {
            const auto jt = it + k;
            if (jt < c.begin() || jt >= c.end()) { continue; }
            if (!have || std::abs(*jt - v) < std::abs(best - v))
            {
                best = *jt;
                have = true;
            }
        }
        if (have && std::abs(best - v) <= coordSnapTol_) { q.component(ax) = best; }
    }
    return q;
}


void Foam::dcCartSurfaces::snapToGridPlanes
(
    const dcCartGrid& grid,
    const scalar tol
)
{
    retirePreparedShellCycles();
    if (tol <= 0) { return; }
    // Dyadic subdivision: every level-L plane is also a level-(L+1) plane, so
    // enumerating the deepest level alone yields every plane the octree can
    // put a cell face on.
    const label lev = max(maxLevel_, label(0));
    const label subdiv = label(1) << lev;
    std::vector<scalar> planes[3];
    for (direction ax = 0; ax < 3; ++ax)
    {
        const scalarField& bp = grid.breakpoints(ax);
        for (label i = 1; i < bp.size(); ++i)
        {
            const scalar lo = bp[i - 1], h = (bp[i] - bp[i - 1]) / scalar(subdiv);
            if (h <= 0) { continue; }
            for (label k = 0; k <= subdiv; ++k) { planes[ax].push_back(lo + k * h); }
        }
        std::sort(planes[ax].begin(), planes[ax].end());
        planes[ax].erase(std::unique(planes[ax].begin(), planes[ax].end()), planes[ax].end());
    }
    label nMoved = 0, nCoords = 0, nDropped = 0;
    forAll(surfaces_, i)
    {
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
        pointField pts(ts.points());
        bool changed = false;
        for (point& pt : pts)
        {
            for (direction ax = 0; ax < 3; ++ax)
            {
                const std::vector<scalar>& g = planes[ax];
                if (g.empty()) { continue; }
                const scalar v = pt.component(ax);
                const auto it = std::lower_bound(g.begin(), g.end(), v);
                scalar best = 0;
                bool have = false;
                for (label k = -1; k <= 0; ++k)
                {
                    const auto jt = it + k;
                    if (jt < g.begin() || jt >= g.end()) { continue; }
                    if (!have || std::abs(*jt - v) < std::abs(best - v)) { best = *jt; have = true; }
                }
                if (!have) { continue; }
                const scalar d = best - v;
                if (d == 0 || std::abs(d) > tol) { continue; }
                pt.component(ax) = best;
                ++nCoords;
                changed = true;
            }
        }
        if (!changed) { continue; }
        nMoved += pts.size();
        // (one label per bit-equal point, as after the coordinate snap)
        nDropped += setMovedPoints(i, pts);
    }
    if (nCoords)
    {
        Info<< "Surfaces: snapped " << nCoords << " coordinates (" << nMoved
            << " vertices moved) onto a level-" << lev << " grid plane within "
            << tol << " m, " << nDropped << " collapsed facets dropped" << nl;
    }
}


void Foam::dcCartSurfaces::weldNearCoincident
(
    const dcCartGrid& grid,
    const scalar weldTol,
    const label rounds,
    const scalar firstLayerMinFraction,
    const scalar firstLayerMax,
    const scalar firstLayerSealGap
)
{
    retirePreparedShellCycles();
    // Exact-position ordering for the vertex move map: a weld is decided by
    // BIT-EQUAL coordinates (the canonicalisation passes above made junction
    // partners bit-equal), never by a distance tolerance, so the map and every
    // rank agree on what "the same vertex" is.
    struct posKey
    {
        scalar x, y, z;
        bool operator<(const posKey& o) const
        {
            if (x != o.x) { return x < o.x; }
            if (y != o.y) { return y < o.y; }
            return z < o.z;
        }
    };
    auto keyOf = [](const point& p) -> posKey
    {
        return posKey{p.x(), p.y(), p.z()};
    };

    const bool dbg = (getenv("DCCART_WELD_DEBUG") != nullptr);
    // G2 (tilted planes) is OFF by default (-1): measured on DAML-Testbed,
    // snapping even the big duct sheets tears their stitched seams -- the
    // 0.18 mm translation of duct_expand_zmin twisted its throat/collar
    // partners and left 1e-12 m2 seam faces and 1e-18 m3 cells, a floor of
    // 4.2e-7, WORSE than the graze it cured. A tilted snap is only safe for
    // an assembly whose every sheet moves rigidly together, which no local
    // weld can promise. DCCART_WELD_MIN_PLANE_AREA=<m2> re-enables G2 for
    // patches at least that big (0.05 separates the DAML duct sheets,
    // 0.14-0.66 m2, from the spiral-riser facet strips, <= 0.02 m2, whose
    // per-strip snapping corrugates the surface: 9865 snaps, min face 0).
    const char* mpaEnv = getenv("DCCART_WELD_MIN_PLANE_AREA");
    const scalar minPlaneArea = mpaEnv ? scalar(atof(mpaEnv)) : scalar(-1);

    // Dyadic plane enumeration, identical to snapToGridPlanes': the deepest
    // level's planes contain every plane the octree can realise.
    const label lev = max(maxLevel_, label(0));
    const label subdiv = label(1) << lev;
    std::vector<scalar> planes[3];
    for (direction ax = 0; ax < 3; ++ax)
    {
        const scalarField& bp = grid.breakpoints(ax);
        for (label i = 1; i < bp.size(); ++i)
        {
            const scalar lo = bp[i - 1], h = (bp[i] - bp[i - 1]) / scalar(subdiv);
            if (h <= 0) { continue; }
            for (label k = 0; k <= subdiv; ++k) { planes[ax].push_back(lo + k * h); }
        }
        std::sort(planes[ax].begin(), planes[ax].end());
        planes[ax].erase
        (
            std::unique(planes[ax].begin(), planes[ax].end()),
            planes[ax].end()
        );
    }
    // Nearest plane to `c` on `ax` (lower of the two on an exact tie).
    auto nearestPlane = [&](direction ax, scalar c, scalar& g) -> scalar
    {
        const std::vector<scalar>& ps = planes[ax];
        if (ps.empty()) { return GREAT; }
        const auto it = std::lower_bound(ps.begin(), ps.end(), c);
        scalar best = 0; bool have = false;
        for (label k = -1; k <= 0; ++k)
        {
            const auto jt = it + k;
            if (jt < ps.begin() || jt >= ps.end()) { continue; }
            if (!have || std::abs(*jt - c) < std::abs(best - c))
            {
                best = *jt; have = true;
            }
        }
        if (!have) { return GREAT; }
        g = best;
        return best - c;
    };
    // Nearest LEVEL-L plane to `c` on `ax`, and the level-L spacing of the
    // background interval holding `c`. The plane value is the one
    // dcCartGrid::planeCoord returns for it, so a coordinate moved onto it is
    // bit-equal to the cell face the octree puts there. Lower plane on a tie.
    auto nearestPlaneAt = [&]
    (
        direction ax, label L, scalar c, scalar& g, scalar& h
    ) -> scalar
    {
        const scalarField& bp = grid.breakpoints(ax);
        if (bp.size() < 2 || L < 0) { return GREAT; }
        label bg = 0;
        {
            const scalar* b = bp.cdata();
            const scalar* e = b + bp.size();
            bg = label(std::upper_bound(b, e, c) - b) - 1;
            bg = max(label(0), min(bg, bp.size() - 2));
        }
        const scalar D = bp[bg + 1] - bp[bg];
        const label sub = label(1) << L;
        h = D / scalar(sub);
        if (h <= 0) { return GREAT; }
        label k = label(std::floor((c - bp[bg]) / h));
        k = max(label(0), min(k, sub - 1));
        scalar best = 0; bool have = false;
        for (label kk = k; kk <= k + 1; ++kk)
        {
            const scalar p = grid.planeCoord(ax, L, bg * sub + kk);
            if (!have || std::abs(p - c) < std::abs(best - c))
            {
                best = p; have = true;
            }
        }
        if (!have) { return GREAT; }
        g = best;
        return best - c;
    };

    // First-layer floor: a fraction of the class's own level spacing,
    // capped, never below the classic band. 0.5 snaps every axis-aligned
    // surface onto its level grid.
    const scalar fFrac = min(max(firstLayerMinFraction, scalar(0)), scalar(0.5));
    const scalar sealGap = (firstLayerSealGap < 0 ? weldTol : firstLayerSealGap);
    auto floorFor = [&](scalar h) -> scalar
    {
        scalar t = fFrac * h;
        if (firstLayerMax > 0) { t = min(t, firstLayerMax); }
        return max(t, weldTol);
    };
    {
        // the floor in force at the finest level: what the census checks
        scalar hFinest = GREAT;
        for (direction ax = 0; ax < 3; ++ax)
        {
            const scalarField& bp = grid.breakpoints(ax);
            for (label i = 1; i < bp.size(); ++i)
            {
                const scalar D = bp[i] - bp[i - 1];
                if (D > 0) { hFinest = min(hFinest, D / scalar(subdiv)); }
            }
        }
        firstLayerFloor_ =
            (fFrac > 0 && hFinest < GREAT) ? floorFor(hFinest) : weldTol;
    }

    const scalar onTol = 1e-6;   // below this the carver already sees ON
    label totG1 = 0, totG2 = 0, totVerts = 0, totGuard = 0, roundsRun = 0;
    label totFloor = 0, totGap = 0, totSealed = 0;
    scalar maxShift = 0;

    // ---- G0: surface-to-surface axis coordinate weld -----------------------
    // G1/G2 weld a surface to the GRID; two surfaces a fraction of a
    // millimetre apart from each other, neither near a plane, were left
    // alone. Hand-placed objects carry exactly that (Base_leakage: three
    // wall baffles closing a box end at x = 2.801 / 2.8019 / 2.802, and two
    // of its four racks stand 2.9579 tall, two 2.958), and two exact-outline
    // cuts that close inside one cell carve a 0.1-1 mm slab no convex merge
    // can absorb: 54 zero-determinant cells, 285 tiny volume ratios. Per
    // axis, the coordinates within weldTol of the lowest one form a class;
    // each value moves as a whole (every vertex at it, so junctions stay
    // bit-equal) onto one target. Only values carried solely by vertices of
    // axis-aligned triangles may move -- a tilted triangle's vertex would
    // bend it -- and a value never joins a class in which the same surface
    // has a face normal to the axis looking the opposite way: those are the
    // two sides of a solid thinner than weldTol (or of a slot narrower than
    // it), which welding would flatten. A step between faces looking the
    // same way (racks side by side, 2.9579 and 2.958 tall) welds, and a
    // sliver strip inside a sheet's plane (a 1 mm gap between two hand-laid
    // floor tiles) collapses and is dropped below.
    // DCCART_WELD_PEER=0 turns it off.
    label totG0 = 0, totG0Dropped = 0;
    {
        const char* peerEnv = getenv("DCCART_WELD_PEER");
        const bool peerOn = !peerEnv || atoi(peerEnv) != 0;
        struct coordClass
        {
            bool frozen = false;   // a tilted triangle uses the value
            label n = 0;           // vertex uses of the value
        };
        std::map<scalar, coordClass> classes[3];
        // value -> (surface, +1/-1) of the faces normal to the axis there
        std::map<scalar, std::set<std::pair<label, int>>> facing[3];
        forAll(surfaces_, i)
        {
            if (!peerOn) { break; }
            const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
            const pointField& pts = ts.points();
            forAll(ts, t)
            {
                const labelledTri& f = ts[t];
                bool aligned = false;
                const vector cr = (pts[f[1]] - pts[f[0]]) ^ (pts[f[2]] - pts[f[0]]);
                for (direction ax = 0; ax < 3; ++ax)
                {
                    const scalar c = pts[f[0]].component(ax);
                    if (pts[f[1]].component(ax) == c && pts[f[2]].component(ax) == c)
                    {
                        aligned = true;
                        if (cr.component(ax) != 0)
                        {
                            facing[ax][c].insert({label(i), cr.component(ax) > 0 ? 1 : -1});
                        }
                    }
                }
                for (direction ax = 0; ax < 3; ++ax)
                {
                    for (label k = 0; k < 3; ++k)
                    {
                        coordClass& c = classes[ax][pts[f[k]].component(ax)];
                        c.frozen = c.frozen || !aligned;
                        ++c.n;
                    }
                }
            }
        }
        std::map<posKey, point> newpos;
        for (direction ax = 0; ax < 3 && peerOn; ++ax)
        {
            // Greedy partition of the sorted values: a value joins the open
            // class when it is within weldTol of the class's lowest value and
            // no surface faces the opposite way at it and at a member;
            // otherwise it opens the next class.
            std::vector<std::vector<std::pair<scalar, const coordClass*>>> groups;
            std::set<std::pair<label, int>> groupFacing;
            for (const auto& kv : classes[ax])
            {
                bool join = !groups.empty() && kv.first - groups.back().front().first <= weldTol;
                const auto fit = facing[ax].find(kv.first);
                if (join && fit != facing[ax].end())
                {
                    for (const auto& sf : fit->second)
                    {
                        if (groupFacing.count({sf.first, -sf.second}))
                        {
                            join = false;
                            ++totGuard;
                            if (dbg)
                            {
                                Info<< "  weld G0 guard: axis " << label(ax) << " coord "
                                    << kv.first << " faces " << groups.back().front().first
                                    << " on " << info_[sf.first].name.c_str() << nl;
                            }
                            break;
                        }
                    }
                }
                if (!join) { groups.emplace_back(); groupFacing.clear(); }
                groups.back().emplace_back(kv.first, &kv.second);
                if (fit != facing[ax].end()) { groupFacing.insert(fit->second.begin(), fit->second.end()); }
            }
            for (const auto& grp : groups)
            {
                if (grp.size() < 2) { continue; }
                label nFrozen = 0;
                scalar target = grp.front().first;
                bool haveTarget = false;
                for (const auto& m : grp)
                {
                    if (m.second->frozen) { ++nFrozen; target = m.first; haveTarget = true; }
                }
                if (nFrozen > 1)
                {
                    ++totGuard;
                    if (dbg)
                    {
                        Info<< "  weld G0 guard: axis " << label(ax) << " coords "
                            << grp.front().first << " .. " << grp.back().first
                            << " (two tilted values)" << nl;
                    }
                    continue;
                }
                if (!haveTarget)
                {
                    // a value already on a grid plane, else the most-used one
                    // (the lower on a tie: ascending order)
                    label best = -1;
                    for (const auto& m : grp)
                    {
                        scalar g = 0;
                        if (nearestPlane(ax, m.first, g) == 0) { target = m.first; haveTarget = true; break; }
                        if (m.second->n > best) { best = m.second->n; target = m.first; }
                    }
                }
                for (const auto& m : grp)
                {
                    if (m.first == target) { continue; }
                    ++totG0;
                    if (dbg)
                    {
                        Info<< "  weld G0: axis " << label(ax) << " coord " << m.first
                            << " -> " << target << " (" << m.second->n << " uses)" << nl;
                    }
                    forAll(surfaces_, i)
                    {
                        const pointField& pts =
                            static_cast<const triSurface&>(surfaces_[i]).points();
                        for (const point& p : pts)
                        {
                            if (p.component(ax) != m.first) { continue; }
                            auto ins = newpos.emplace(keyOf(p), p);
                            ins.first->second.component(ax) = target;
                        }
                    }
                }
            }
        }
        if (!newpos.empty())
        {
            forAll(surfaces_, i)
            {
                const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
                pointField pts(ts.points());
                bool changed = false;
                for (point& p : pts)
                {
                    const auto f = newpos.find(keyOf(p));
                    if (f == newpos.end()) { continue; }
                    maxShift = max(maxShift, mag(f->second - p));
                    p = f->second;
                    changed = true;
                    ++totVerts;
                }
                if (!changed) { continue; }
                // A welded sliver (a 1 mm strip between two hand-laid floor
                // tiles, a 0.05 mm step on a server box) collapses to zero
                // area, and its corners now sit bit-equal on separate point
                // labels. Drop the zero-area triangles -- a zero normal
                // reaches the carve's server-box frame (SIGFPE in
                // carveShellPass) -- and merge the labels the way the STL
                // reader does, so the shell stays closed. The source-region
                // facet inventory still counts them (weldDroppedFacets_).
                totG0Dropped += setMovedPoints(i, pts);
            }
        }
    }

    for (label round = 0; round < max(rounds, label(1)); ++round)
    {
        roundsRun = round + 1;

        // ---- exact coordinate classes: axis coordinate -> vertices -------
        std::map<scalar, std::vector<std::pair<label, label>>> axisCoords[3];
        forAll(surfaces_, i)
        {
            const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
            const pointField& pts = ts.points();
            forAll(pts, pI)
            {
                for (direction ax = 0; ax < 3; ++ax)
                {
                    axisCoords[ax][pts[pI].component(ax)]
                        .emplace_back(label(i), label(pI));
                }
            }
        }

        // ---- G1 marks: exactly-axis-aligned triangle planes in the band ---
        // Per axis: coordinate -> (target plane, does some surface show a
        // +normal there, a -normal there). A coordinate is marked when ANY
        // axis-aligned triangle plane at it grazes a grid plane; the class
        // rewrite then moves every vertex at that coordinate, aligned or not,
        // which is what keeps junctions watertight.
        struct g1info
        {
            scalar g = 0;          // target plane
            scalar d = 0;          // signed move (g - c)
            bool pos = false, neg = false;
            label surfPos = -1, surfNeg = -1;
            label level = -1;      // finest level a surface in the class refines to
            bool byFloor = false;  // beyond the classic band: the floor's doing
        };
        std::map<scalar, g1info> g1[3];
        forAll(surfaces_, i)
        {
            const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
            const pointField& pts = ts.points();
            forAll(ts, t)
            {
                const labelledTri& f = ts[t];
                const point& a = pts[f[0]];
                // exactly axis-aligned: all three vertices share one exact
                // coordinate (float32 exports are canonical per plane, and the
                // coordinate snap above made junction partners bit-equal)
                for (direction ax = 0; ax < 3; ++ax)
                {
                    const scalar c = a.component(ax);
                    if (pts[f[1]].component(ax) != c || pts[f[2]].component(ax) != c)
                    {
                        continue;
                    }
                    vector cr = (pts[f[1]] - a) ^ (pts[f[2]] - a);
                    const scalar m = mag(cr);
                    if (m < VSMALL) { continue; }
                    cr /= m;
                    const scalar s = cr.component(ax);
                    if (std::abs(std::abs(s) - 1) > 1e-9) { continue; }
                    auto& e = g1[ax][c];
                    if (s > 0) { e.pos = true; e.surfPos = i; }
                    else { e.neg = true; e.surfNeg = i; }
                    e.level = max(e.level, info_[i].maxLevel);
                }
            }
        }
        std::map<scalar, scalar> g1mark[3];   // coordinate -> grid plane
        for (direction ax = 0; ax < 3; ++ax)
        {
            for (auto& kv : g1[ax])
            {
                scalar g = 0;
                scalar d = GREAT;
                bool marked = false, byFloor = false;
                if (fFrac > 0 && kv.second.level >= 0)
                {
                    // the class's own level: its planes are the faces the
                    // cells it cuts can have, and its spacing sets the floor
                    scalar h = 0;
                    const scalar dL =
                        nearestPlaneAt(ax, kv.second.level, kv.first, g, h);
                    if
                    (
                        dL != GREAT
                     && std::abs(dL) > onTol
                     && std::abs(dL) <= floorFor(h)
                    )
                    {
                        d = dL;
                        marked = true;
                        byFloor = (std::abs(dL) > weldTol);
                    }
                }
                if (!marked)
                {
                    // classic band against the finest planes: a cell some
                    // OTHER surface or a shell refined past the class level
                    d = nearestPlane(ax, kv.first, g);
                    if (d == GREAT) { continue; }
                    if (std::abs(d) <= onTol || std::abs(d) > weldTol) { continue; }
                }
                kv.second.g = g;
                kv.second.d = d;
                kv.second.byFloor = byFloor;
                g1mark[ax].insert({kv.first, g});
            }
            // collapse guard: two opposing faces of ONE solid both marked onto
            // a plane BETWEEN them would flatten the solid to zero thickness.
            // Collect first, erase after: no iterator invalidation.
            std::vector<scalar> drop;
            for (auto itA = g1mark[ax].begin(); itA != g1mark[ax].end(); ++itA)
            {
                for (auto itB = g1mark[ax].begin(); itB != g1mark[ax].end(); ++itB)
                {
                    if (itA->first >= itB->first) { continue; }
                    if (itA->second != itB->second) { continue; }
                    const scalar c1 = itA->first, c2 = itB->first, g = itA->second;
                    if (!(c1 < g && g < c2)) { continue; }
                    const auto& lo = g1[ax][c1];
                    const auto& hi = g1[ax][c2];
                    // facing each other across g: +normal at the low coordinate
                    // and -normal at the high one, on one surface
                    if (lo.pos && hi.neg && lo.surfPos == hi.surfNeg)
                    {
                        ++totGuard;
                        drop.push_back(c1);
                        drop.push_back(c2);
                        if (dbg)
                        {
                            Info<< "  weld G1 guard: axis " << label(ax)
                                << " coords " << c1 << " / " << c2
                                << " kept (same-solid opposing faces)" << nl;
                        }
                    }
                }
            }
            for (const scalar c : drop) { g1mark[ax].erase(c); }

            // gap guard (first-layer floor): two DIFFERENT solids facing
            // each other across a gap, both marked onto one plane between
            // them, would have the gap sealed. Inside the classic band that
            // is the weld's job (a sub-weldTol gap is an export artefact);
            // a wider one is sealed only up to `sealGap`, the configured
            // gap-closing bound, else both classes stay put
            // and the carver's own machinery takes the slabs as before.
            if (fFrac > 0)
            {
                std::vector<scalar> drop2;
                for (auto itA = g1mark[ax].begin(); itA != g1mark[ax].end(); ++itA)
                {
                    for (auto itB = std::next(itA); itB != g1mark[ax].end(); ++itB)
                    {
                        if (itA->second != itB->second) { continue; }
                        const scalar c1 = itA->first, c2 = itB->first, g = itA->second;
                        if (!(c1 < g && g < c2)) { continue; }
                        const auto& lo = g1[ax][c1];
                        const auto& hi = g1[ax][c2];
                        if (!(lo.pos && hi.neg)) { continue; }
                        const bool classic =
                            std::abs(lo.d) <= weldTol && std::abs(hi.d) <= weldTol;
                        if (classic || (c2 - c1) <= sealGap)
                        {
                            ++totSealed;
                            continue;
                        }
                        ++totGap;
                        drop2.push_back(c1);
                        drop2.push_back(c2);
                        if (dbg)
                        {
                            Info<< "  weld floor gap guard: axis " << label(ax)
                                << " coords " << c1 << " / " << c2 << " gap "
                                << (c2 - c1) << " m kept apart" << nl;
                        }
                    }
                }
                for (const scalar c : drop2) { g1mark[ax].erase(c); }
            }
        }

        // ---- G2: tilted planar patches grazing a grid point ---------------
        struct g2move
        {
            label surf;
            label firstTri;      // deterministic order key
            vector disp;
            std::vector<point> verts;
        };
        std::vector<g2move> g2moves;
        forAll(surfaces_, i)
        {
            const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
            const pointField& pts = ts.points();
            const label nT = ts.size();
            if (!nT) { continue; }
            // per-triangle planes
            std::vector<vector> tn;
            tn.resize(size_t(nT));
            std::vector<scalar> to(size_t(nT), 0);
            std::vector<char> tv(size_t(nT), 0);
            forAll(ts, t)
            {
                const labelledTri& f = ts[t];
                vector cr = (pts[f[1]] - pts[f[0]]) ^ (pts[f[2]] - pts[f[0]]);
                const scalar m = mag(cr);
                if (m < VSMALL) { continue; }
                tn[size_t(t)] = cr / m;
                to[size_t(t)] = tn[size_t(t)] & pts[f[0]];
                tv[size_t(t)] = 1;
            }
            // union-find: triangles sharing an exact position AND coplanar
            std::vector<label> par;
            par.resize(size_t(nT));
            for (label t = 0; t < nT; ++t) { par[size_t(t)] = t; }
            std::function<label(label)> find = [&](label a) -> label
            {
                while (par[size_t(a)] != a)
                {
                    const label p = par[size_t(a)];
                    par[size_t(a)] = par[size_t(p)];
                    a = p;
                }
                return a;
            };
            {
                std::map<posKey, std::vector<label>> posTris;
                forAll(ts, t)
                {
                    if (!tv[size_t(t)]) { continue; }
                    const labelledTri& f = ts[t];
                    for (label k = 0; k < 3; ++k)
                    {
                        posTris[keyOf(pts[f[k]])].push_back(t);
                    }
                }
                for (auto& kv : posTris)
                {
                    std::vector<label>& ts2 = kv.second;
                    std::sort(ts2.begin(), ts2.end());
                    ts2.erase(std::unique(ts2.begin(), ts2.end()), ts2.end());
                    for (size_t a = 0; a + 1 < ts2.size(); ++a)
                    {
                        const label t1 = ts2[a], t2 = ts2[a + 1];
                        // chain consecutive only: bounded drift, no O(k^2)
                        if (std::abs((tn[size_t(t1)] & tn[size_t(t2)]) - 1) < 1e-8
                         && std::abs(to[size_t(t1)] - to[size_t(t2)]) < 2e-6)
                        {
                            const label r1 = find(t1), r2 = find(t2);
                            if (r1 != r2) { par[size_t(r1)] = r2; }
                        }
                    }
                }
            }
            // group into patches
            std::map<label, std::vector<label>> groups;
            for (label t = 0; t < nT; ++t)
            {
                if (!tv[size_t(t)]) { continue; }
                groups[find(t)].push_back(t);
            }
            for (auto& kv : groups)
            {
                const std::vector<label>& tris = kv.second;
                const label t0 = tris.front();   // ascending: deterministic
                const vector& n = tn[size_t(t0)];
                // axis-aligned patches are G1's job (class rewrite); a patch
                // is tilted when no component dominates to ~1
                direction big = 0;
                for (direction d = 1; d < 3; ++d)
                {
                    if (std::abs(n.component(d)) > std::abs(n.component(big))) { big = d; }
                }
                if (std::abs(std::abs(n.component(big)) - 1) <= 1e-9) { continue; }

                const scalar o = to[size_t(t0)];
                // patch bounds and area
                scalar bmin[3], bmax[3];
                for (direction ax = 0; ax < 3; ++ax)
                {
                    bmin[ax] = GREAT; bmax[ax] = -GREAT;
                }
                scalar patchArea = 0;
                for (const label t : tris)
                {
                    const labelledTri& f = ts[t];
                    patchArea += 0.5 * mag
                    (
                        (pts[f[1]] - pts[f[0]]) ^ (pts[f[2]] - pts[f[0]])
                    );
                    for (label k = 0; k < 3; ++k)
                    {
                        const point& p = pts[f[k]];
                        for (direction ax = 0; ax < 3; ++ax)
                        {
                            bmin[ax] = min(bmin[ax], p.component(ax));
                            bmax[ax] = max(bmax[ax], p.component(ax));
                        }
                    }
                }
                // A tilted snap is only COHERENT for a large engineering
                // plane (a duct sheet, a diagonal wall): translating it is a
                // rigid sub-weldTol shift of one flat face. On a finely
                // faceted curved surface (the DAML spiral duct riser carries
                // ~10k triangles in <=0.02 m2 planar strips) every strip
                // grazes a different grid point and snapping them
                // individually corrugates the surface at the weldTol scale
                // -- measured: 9865 strip snaps drove min face area to 0 and
                // the quality floor to 4.2e-7, worse than the defect. Those
                // stay for the carver's own machinery; the threshold is
                // DCCART_WELD_MIN_PLANE_AREA (default 0.05 m2: the DAML duct
                // sheets are 0.14-0.66 m2, its facet strips <= 0.02 m2).
                if (minPlaneArea < 0 || patchArea < minPlaneArea) { continue; }
                // nearest grid POINT to the plane inside the padded bounds:
                // x outer, y, z inner, strict improvement -> on a tie the
                // lexicographically smallest point wins (deterministic)
                std::vector<label> rng[3];
                bool empty = false;
                for (direction ax = 0; ax < 3; ++ax)
                {
                    const std::vector<scalar>& ps = planes[ax];
                    auto a = std::lower_bound(ps.begin(), ps.end(), bmin[ax] - weldTol);
                    auto b = std::upper_bound(ps.begin(), ps.end(), bmax[ax] + weldTol);
                    if (a >= b) { empty = true; break; }
                    for (auto it = a; it != b; ++it) { rng[ax].push_back(label(it - ps.begin())); }
                }
                if (empty) { continue; }
                scalar bestD = GREAT;
                point bestP = point::zero;
                for (const label ix : rng[0])
                {
                    const scalar px = planes[0][size_t(ix)];
                    for (const label iy : rng[1])
                    {
                        const scalar py = planes[1][size_t(iy)];
                        const scalar base = n.x() * px + n.y() * py - o;
                        for (const label iz : rng[2])
                        {
                            const scalar d = base + n.z() * planes[2][size_t(iz)];
                            const scalar ad = std::abs(d);
                            if (ad < bestD)
                            {
                                bestD = ad;
                                bestP = point(px, py, planes[2][size_t(iz)]);
                            }
                        }
                    }
                }
                if (bestD == GREAT || bestD <= onTol || bestD > weldTol) { continue; }
                // signed distance of that point from the plane; translating
                // every vertex by d*n puts the plane exactly through it
                const scalar dSigned = (n & bestP) - o;
                g2move mv;
                mv.surf = i;
                mv.firstTri = t0;
                mv.disp = dSigned * n;
                {
                    std::set<posKey> seen;
                    for (const label t : tris)
                    {
                        const labelledTri& f = ts[t];
                        for (label k = 0; k < 3; ++k)
                        {
                            if (seen.insert(keyOf(pts[f[k]])).second)
                            {
                                mv.verts.push_back(pts[f[k]]);
                            }
                        }
                    }
                }
                if (dbg)
                {
                    const wordList regs = surfaces_[i].regions();
                    const label reg = ts[size_t(t0)].region();
                    Info<< "  weld G2: " << info_[i].name.c_str() << "/"
                        << (reg >= 0 && reg < regs.size() ? regs[reg] : word("?")).c_str()
                        << " " << tris.size() << " tris, n=" << n
                        << " o=" << o << " -> point " << bestP
                        << " shift " << dSigned << nl;
                }
                g2moves.push_back(std::move(mv));
            }
        }
        // deterministic application order: surface, then first triangle
        std::stable_sort
        (
            g2moves.begin(), g2moves.end(),
            [](const g2move& a, const g2move& b)
            {
                if (a.surf != b.surf) { return a.surf < b.surf; }
                return a.firstTri < b.firstTri;
            }
        );

        // ---- build the move map: G1 classes first, then G2 patches -------
        std::map<posKey, point> newpos;
        label nG1 = 0, nFloor = 0;
        for (direction ax = 0; ax < 3; ++ax)
        {
            for (auto& kv : g1mark[ax])
            {
                const auto vit = axisCoords[ax].find(kv.first);
                if (vit == axisCoords[ax].end()) { continue; }
                ++nG1;
                const bool byFloor = g1[ax][kv.first].byFloor;
                if (byFloor) { ++nFloor; }
                if (dbg)
                {
                    Info<< "  weld G1: axis " << label(ax) << " coord "
                        << kv.first << " -> " << kv.second << " ("
                        << vit->second.size() << " verts"
                        << (byFloor ? ", floor" : "") << ")" << nl;
                }
                for (const auto& sp : vit->second)
                {
                    const triSurface& ts2 =
                        static_cast<const triSurface&>(surfaces_[sp.first]);
                    const point& p = ts2.points()[sp.second];
                    // A vertex whose class is marked on TWO axes (a box edge
                    // grazing two planes at once) composes both moves: each
                    // axis writes its own component. The first-claim rule
                    // that used to skip the second axis left every such
                    // edge on a ~1 mm ramp (Base604 B12/B14 rack frames),
                    // and a ramp's triangles are no longer axis-aligned, so
                    // no later round could mark them again -- the ramps
                    // carved the 52 um crumbs that sank the quality floor.
                    auto ins = newpos.emplace(keyOf(p), p);
                    ins.first->second.component(ax) = kv.second;
                }
            }
        }
        label nG2 = 0;
        for (const g2move& mv : g2moves)
        {
            bool claimed = false;
            for (const point& p : mv.verts)
            {
                const posKey kk = keyOf(p);
                if (newpos.count(kk)) { continue; }
                newpos.insert({kk, p + mv.disp});
                claimed = true;
            }
            if (claimed) { ++nG2; }
        }
        // Exact-axis sheet perimeter classes: classic band only. Keep the
        // original normal-face G1/floor/gap decisions and G2 ordering intact.
        // No outline proposal is mixed with an active tilted-patch transaction.
        if (g2moves.empty())
        {
            namespace Perimeter = Foam::dcCartPlanarSheetPerimeterWeld;
            std::vector<Perimeter::SurfaceView> views;
            views.reserve(surfaces_.size());
            // A region of a solid's surface that is one axis-aligned plane (a
            // patch on a box face) is a sheet too: its seam with the rest of
            // the face is an outline the cut has to follow. Held as a copy of
            // the region's triangles over the surface's own points; the whole
            // surface stays in as a non-eligible view (its barriers).
            std::vector<std::unique_ptr<triSurface>> regionSheets;
            forAll(surfaces_, i)
            {
                const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
                Perimeter::SurfaceView view;
                view.surface = i;
                view.eligible = info_[i].internalBaffle && !info_[i].carves;
                view.points = &ts.points(); view.triangles = &ts;
                std::map<label, label> nByRegion;
                forAll(ts, t) { ++nByRegion[ts[t].region()]; }
                // A baffle surface holding several sheets in different
                // planes (merged_wall_baffle: every wall baffle of the case)
                // is no planar sheet as a whole -- the proposal skipped it
                // entirely, and a 0.6 mm misfit of an opening's edge off a
                // grid plane carved 1 mm strips (DAML's enclosure door).
                // Each region is a sheet of its own, as for a solid's face.
                const bool splitBaffle = view.eligible && nByRegion.size() >= 2;
                if (splitBaffle) { view.eligible = false; }
                views.push_back(view);
                if (view.eligible) { continue; }
                std::map<label, List<labelledTri>> byRegion;
                forAll(ts, t)
                {
                    auto& lst = byRegion[ts[t].region()];
                    if (lst.empty()) { lst.setSize(nByRegion[ts[t].region()]); nByRegion[ts[t].region()] = 0; }
                    lst[nByRegion[ts[t].region()]++] = ts[t];
                }
                if (byRegion.size() < 2) { continue; }
                for (auto& kv : byRegion)
                {
                    const pointField& ps = ts.points();
                    bool flat = true;
                    label normal = -1;
                    for (direction ax = 0; ax < 3; ++ax)
                    {
                        bool same = true;
                        const scalar c0 = ps[kv.second[0][0]].component(ax);
                        for (const labelledTri& f : kv.second)
                        {
                            for (label k = 0; k < 3; ++k) { if (ps[f[k]].component(ax) != c0) { same = false; } }
                        }
                        if (same) { if (normal >= 0) { flat = false; } normal = ax; }
                    }
                    if (!flat || normal < 0) { continue; }
                    regionSheets.emplace_back(new triSurface(kv.second, ts.patches(), ps));
                    Perimeter::SurfaceView rv;
                    rv.surface = i;
                    rv.eligible = true;
                    rv.points = &regionSheets.back()->points();
                    rv.triangles = regionSheets.back().get();
                    views.push_back(rv);
                }
            }
            Perimeter::Marks legacy;
            Perimeter::Classes originalNormalClasses;
            for (direction ax=0; ax<3; ++ax)
            {
                legacy[ax] = g1mark[ax];
                for (const auto& item : g1[ax]) originalNormalClasses[ax].insert(item.first);
            }
            Perimeter::Result perimeter;
            std::string perimeterWhy;
            const auto legacyAt = [&](const point& p)
            {
                const auto it = newpos.find(keyOf(p));
                return it == newpos.end() ? p : it->second;
            };
            if (Perimeter::prepare
                (views, legacy, originalNormalClasses, weldTol, onTol,
                 nearestPlane, legacyAt, [](){return true;}, perimeter, &perimeterWhy,
                 max(weldTol, firstLayerFloor_)))
            {
                for (direction ax=0; ax<3; ++ax)
                    for (const auto& item : perimeter.marks[ax])
                    {
                        const auto vit=axisCoords[ax].find(item.first);
                        if (vit==axisCoords[ax].end()) continue;
                        for (const auto& sp : vit->second)
                        {
                            const triSurface& ts=static_cast<const triSurface&>(surfaces_[sp.first]);
                            const point& p=ts.points()[sp.second];
                            auto inserted=newpos.emplace(keyOf(p),p);
                            inserted.first->second.component(ax)=item.second;
                        }
                    }
                if (dbg && Pstream::master())
                for (direction ax=0; ax<3; ++ax)
                    for (const auto& item : perimeter.marks[ax])
                        Info<< "  perimeter mark axis " << label(ax) << " " << item.first
                            << " -> " << item.second << nl;
                if (Pstream::master())
                Info<< "Surfaces: planar sheet perimeter classic-band proposal: "
                    << perimeter.proposed << " classes, " << perimeter.dropped
                    << " guard-dropped, " << Perimeter::count(perimeter.marks)
                    << " applied, " << perimeter.sheetCount << " sheets" << nl;
                if (Pstream::master())
                for (const auto& change : perimeter.areaChanges)
                    Info<< "  perimeter surface " << info_[change.surface].stlFile
                        << " combined legacy+perimeter triangle-area " << change.before << " -> " << change.after
                        << " delta " << (change.after-change.before) << nl;
            }
            else if (dbg && Pstream::master())
                Info<< "  perimeter proposal refused: " << perimeterWhy.c_str() << nl;
        }

        if (newpos.empty())
        {
            if (round == 0 && dbg) { Info<< "  weld: nothing to do" << nl; }
            roundsRun = round;
            break;
        }

        // ---- apply --------------------------------------------------------
        label nMoved = 0;
        forAll(surfaces_, i)
        {
            const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
            pointField pts(ts.points());
            bool changed = false;
            for (point& p : pts)
            {
                const auto it = newpos.find(keyOf(p));
                if (it == newpos.end()) { continue; }
                const scalar shift = mag(it->second - p);
                if (shift > maxShift) { maxShift = shift; }
                p = it->second;
                changed = true;
                ++nMoved;
            }
            if (changed) { surfaces_[i].movePoints(pts); }
        }
        totG1 += nG1;
        totFloor += nFloor;
        totG2 += nG2;
        totVerts += nMoved;
    }

    nWeldRounds_ = roundsRun;
    nWeldCoords_ = totG1;
    nWeldPatches_ = totG2;
    nWeldVerts_ = totVerts;
    nWeldGuard_ = totGuard;
    weldMaxShift_ = maxShift;
    nWeldFloor_ = totFloor;
    nWeldGap_ = totGap;
    nWeldSealed_ = totSealed;

    if (!Pstream::parRun() || Pstream::master())
    {
        Info<< "Surfaces: near-coincidence weld (tol " << weldTol
            << " m, tilted-plane min area " << minPlaneArea << " m2, level-"
            << lev << " grid): " << totG0 << " surface-to-surface coordinates ("
            << totG0Dropped << " collapsed facets dropped), "
            << totG1 << " axis planes, " << totG2
            << " tilted patches, " << totVerts << " vertices moved, max shift "
            << maxShift << " m, " << roundsRun << " round(s)";
        if (totGuard)
        {
            Info<< ", guard kept " << totGuard << " pair(s)";
        }
        Info<< nl;
        if (fFrac > 0)
        {
            Info<< "Surfaces: first-layer floor " << fFrac
                << " x level spacing (max " << firstLayerMax << " m, seal gap "
                << sealGap << " m): " << totFloor
                << " axis planes moved beyond the weld band, " << totSealed
                << " facing pair(s) sealed, " << totGap
                << " kept apart; floor at the finest level "
                << firstLayerFloor_ << " m" << nl;
        }
    }
}


Foam::dcCartSurfaces::dcCartSurfaces
(
    const Time& runTime,
    const dictionary& geometryDict,
    const dictionary& refinementSurfacesDict,
    const dictionary& refinementRegionsDict,
    const scalar backgroundSpacing,
    const scalar coordSnapFraction,
    const dcCartGrid* grid,
    const scalar gridSnapFraction,
    const scalar weldTolerance,
    const label weldRounds,
    const scalar firstLayerMinFraction,
    const scalar firstLayerMax,
    const scalar firstLayerSealGap
)
:
    maxLevel_(0)
{
    DynamicList<word> patchNames;
    DynamicList<word> patchTypes;
    DynamicList<label> regionSurface;
    DynamicList<surfaceInfo> infos;

    for (const entry& e : geometryDict)
    {
        if (!e.isDict())
        {
            continue;
        }
        const dictionary& gd = e.dict();
        const word type = gd.getOrDefault<word>("type", "triSurfaceMesh");
        if (type != "triSurfaceMesh" && type != "closedTriSurfaceMesh")
        {
            // searchableBox / searchableRotatedBox shells are refinement
            // volumes, handled by the caller as boxes — not STL geometry.
            continue;
        }

        const fileName stlFile(e.keyword());
        const word surfName = gd.getOrDefault<word>("name", stlFile.lessExt());

        // A surface only carves and claims patches when refinementSurfaces
        // declares it. Everything else in `geometry` is a refinement volume.
        const bool declared = refinementSurfacesDict.found(surfName);

        autoPtr<triSurfaceMesh> surf
        (
            readSurfacePreservingAscii
            (
                IOobject
                (
                    stlFile,
                    runTime.constant(),
                    "triSurface",
                    runTime,
                    IOobject::MUST_READ,
                    IOobject::NO_WRITE
                )
            )
        );

        surfaceInfo si;
        si.name = surfName;
        si.stlFile = stlFile;
        si.carves = declared;
        si.regionOffset = patchNames.size();
        if (declared)
        {
            const dictionary& rs0 = refinementSurfacesDict.subDict(surfName);
            si.internalBaffle =
                (rs0.getOrDefault<word>("faceType", "boundary") == "internal");
            rs0.readIfPresent("faceZone", si.faceZone);
            si.wallBaffle = rs0.getOrDefault<bool>("wallBaffle", false);
            if (si.internalBaffle)
            {
                // zero thickness: it marks faces, it does not remove cells
                si.carves = false;
            }
        }

        // Region names: refinementSurfaces.<surface>.regions when present,
        // else the STL's own solid names. Same rule as snappyHexMesh, so the
        // patch names match what the pipeline already binds BCs to.
        wordList regionNames(surf->regions());
        wordList regionTypes(regionNames.size(), word("wall"));

        if (declared)
        {
            const dictionary& rs = refinementSurfacesDict.subDict(surfName);
            const labelPair lvl
            (
                rs.getOrDefault<labelPair>("level", labelPair(0, 0))
            );
            si.minLevel = lvl.first();
            si.maxLevel = lvl.second();

            if (rs.found("regions"))
            {
                const dictionary& rd = rs.subDict("regions");
                DynamicList<word> names(regionNames.size());
                DynamicList<word> types(regionNames.size());
                for (const entry& re : rd)
                {
                    if (!re.isDict()) { continue; }
                    names.append(re.keyword());
                    word ptype("wall");
                    const dictionary* pi = re.dict().findDict("patchInfo");
                    if (pi) { pi->readIfPresent("type", ptype); }
                    types.append(ptype);
                    const dictionary& rdd = re.dict();
                    if (rdd.found("level"))
                    {
                        const labelPair rl(rdd.get<labelPair>("level"));
                        si.maxLevel = max(si.maxLevel, rl.second());
                    }
                }
                if (names.size())
                {
                    regionNames = wordList(std::move(names));
                    regionTypes = wordList(std::move(types));
                }
            }
        }

        // Only a DECLARED surface claims patches. A surface that appears in
        // `geometry` alone is a refinement volume — a rack through-zone, say —
        // and inventing a boundary patch for it would put a wall across the
        // opening it exists to keep open.
        si.nRegions = declared ? regionNames.size() : 0;
        for (label r = 0; r < si.nRegions; ++r)
        {
            patchNames.append(regionNames[r]);
            patchTypes.append(regionTypes[r]);
            regionSurface.append(infos.size());
        }

        maxLevel_ = max(maxLevel_, si.maxLevel);
        infos.append(si);
        surfaces_.append(surf.ptr());
    }

    // Undeclared surfaces (geometry-only refinement volumes — a rack
    // through-zone, say) carry nRegions == 0 but were handed a regionOffset
    // inside the declared id space above. Every dereference of the form
    // regionOffset + tri.region() then ALIASES the first region of whichever
    // declared surface comes next in this loop: regionArea() and the cut-face
    // tagging sum the undeclared surface's triangles into that patch's area
    // (Tri_angle: three through-zone STLs inflated the diagonal wall's `want`
    // to 22.6375 m2 so the coverage gate could never pass; Base604: a
    // coplanar neighbour stole 0.18 m2 of a perforated patch's `have`).
    // Park each undeclared surface in a reserved id space past the declared
    // total — injective across undeclared surfaces (prefix over their solid
    // counts) and disjoint from every declared region id — so no dereference
    // site can ever confuse them with a patch that claims BCs.
    {
        const label declaredTotal = patchNames.size();
        label cum = 0;
        forAll(infos, i)
        {
            if (infos[i].nRegions == 0)
            {
                infos[i].regionOffset = declaredTotal + cum;
                cum += max(label(1), label(surfaces_[i].regions().size()));
            }
        }
    }

    // ------------------------------------------------------------------
    // Refinement shells. Each refinementRegions entry names a geometry entry
    // that is a closed searchable volume; searchableSurface::New builds
    // whichever type it is, so box / rotatedBox / cylinder all work without a
    // special case here.
    // ------------------------------------------------------------------
    DynamicList<label> shellLevels;
    for (const entry& re : refinementRegionsDict)
    {
        if (!re.isDict()) { continue; }
        const word& shellName = re.keyword();
        if (!geometryDict.found(shellName)) { continue; }

        const dictionary& gd = geometryDict.subDict(shellName);
        const word type = gd.getOrDefault<word>("type", "searchableBox");
        if (type == "triSurfaceMesh" || type == "closedTriSurfaceMesh")
        {
            // an STL used as a shell: already loaded above as a surface
            continue;
        }

        // levels ((distance level) ...) — `mode inside` ignores the distance,
        // so the deepest level in the list is what applies inside the volume.
        label level = 0;
        const List<Tuple2<scalar, label>> levels
        (
            re.dict().getOrDefault<List<Tuple2<scalar, label>>>
            (
                "levels", List<Tuple2<scalar, label>>()
            )
        );
        forAll(levels, i) { level = max(level, levels[i].second()); }
        if (level <= 0) { continue; }

        autoPtr<searchableSurface> shell
        (
            searchableSurface::New
            (
                type,
                IOobject
                (
                    shellName,
                    runTime.constant(),
                    "triSurface",
                    runTime,
                    IOobject::NO_READ,
                    IOobject::NO_WRITE
                ),
                gd
            )
        );
        shells_.append(shell.ptr());
        shellLevels.append(level);
        maxLevel_ = max(maxLevel_, level);
    }
    shellLevel_ = labelList(std::move(shellLevels));
    // Every searchableSurface can cover itself with spheres; their box is a
    // bound in world coordinates for any shell type (bounds() of a rotated box
    // is not)
    refineShellBounds_.setSize(shells_.size());
    forAll(shells_, i)
    {
        pointField centres;
        scalarField radiusSqr;
        shells_[i].boundingSpheres(centres, radiusSqr);
        boundBox bb(boundBox::invertedBox);
        forAll(centres, k)
        {
            const scalar r = std::sqrt(max(radiusSqr[k], scalar(0)));
            bb.add(centres[k] - vector(r, r, r));
            bb.add(centres[k] + vector(r, r, r));
        }
        refineShellBounds_[i] = bb;
    }

    // ---- canonicalise near-duplicate surface coordinates -----------------
    // An STL written from float32 geometry carries ONE physical plane as
    // several coordinates: Shinaide's rack side arrives as both 1.490737 and
    // 1.490738, and its containment baffle -- coplanar with that rack side --
    // as 1.490738. Cutting against the pair leaves a 1 um sliver between two
    // copies of the same wall: 8 open cells, aspect ratio 3.6e4 and 22 skewed
    // faces on that case alone, all of them clustered on those planes.
    // Snapping the vertices makes the plane single-valued, so the two cuts
    // coincide exactly and no sliver is ever created.
    //
    // FDS does this by default (SNAP_GEOM_NODES, geom.f90:15662) and rewrites
    // surface nodes onto the cutting plane's analytic value at
    // SNAP_DIST_FACTOR = 1e-4*dx (geom.f90:5348); the same fraction of the
    // finest cell is used here. Deterministic and rank-independent: the
    // clusters come from the surface coordinates alone.
    if (backgroundSpacing > 0 && maxLevel_ >= 0)
    {
        const scalar finest =
            backgroundSpacing / scalar(label(1) << maxLevel_);
        // Grid planes first, then the export-quantisation clusters: a value
        // already on a plane is at least `finest` from every other plane, so
        // the (100x tighter) cluster pass can never pull it back off.
        if (grid && gridSnapFraction > 0)
        {
            snapToGridPlanes(*grid, gridSnapFraction * finest);
        }
        if (coordSnapFraction > 0)
        {
            snapSurfaceCoordinates(coordSnapFraction * finest);
        }
    }

    DynamicList<word> zoneNames;
    forAll(infos, i)
    {
        if (!infos[i].internalBaffle || infos[i].faceZone.empty()) { continue; }
        if (zoneNames.found(infos[i].faceZone)) { continue; }
        zoneNames.append(infos[i].faceZone);
    }
    faceZoneNames_ = wordList(std::move(zoneNames));

    info_ = List<surfaceInfo>(std::move(infos));
    patchNames_ = wordList(std::move(patchNames));
    patchTypes_ = wordList(std::move(patchTypes));
    regionSurface_ = labelList(std::move(regionSurface));

    // ---- near-coincidence weld (DAML thin-wall wedge WP, route 1) --------
    // Runs AFTER both snaps and BEFORE any point-derived data (shells, baffle
    // sheets, cached trees): coordinates are canonical by now, so the weld's
    // exact-position vertex classes are the same on every rank, and a value
    // the 0.01-scale grid snap already put on a plane is exactly 0 from it
    // and outside the weld's (1e-6, weldTol] band. See weldNearCoincident.
    if (grid && weldTolerance > 0)
    {
        weldNearCoincident
        (
            *grid, weldTolerance, weldRounds,
            firstLayerMinFraction, firstLayerMax, firstLayerSealGap
        );
    }

    // ---- solid shells: connected components of each carving surface ----
    {
        DynamicList<label> shellSurf;
        DynamicList<boundBox> shellBB;
        DynamicList<bool> shellClosed;
        triShell_.setSize(surfaces_.size());
        forAll(surfaces_, i)
        {
            const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
            const label nT = ts.size();
            triShell_[i].setSize(nT, -1);
            if (!info_[i].carves || !nT) { continue; }
            // union-find over point ids
            const label nP = ts.points().size();
            labelList parent(nP);
            forAll(parent, p) { parent[p] = p; }
            std::function<label(label)> find = [&](label a) -> label
            {
                while (parent[a] != a) { parent[a] = parent[parent[a]]; a = parent[a]; }
                return a;
            };
            auto unite = [&](label a, label b)
            {
                a = find(a); b = find(b);
                if (a != b) { parent[a] = b; }
            };
            forAll(ts, t)
            {
                const labelledTri& f = ts[t];
                unite(f[0], f[1]);
                unite(f[1], f[2]);
            }
            Map<label> compShell;
            forAll(ts, t)
            {
                const label root = find(ts[t][0]);
                label sh;
                const auto it = compShell.find(root);
                if (it == compShell.end())
                {
                    sh = shellSurf.size();
                    compShell.insert(root, sh);
                    shellSurf.append(i);
                    shellBB.append(boundBox(boundBox::invertedBox));
                    shellClosed.append(true);
                }
                else
                {
                    sh = *it;
                }
                triShell_[i][t] = sh;
                const labelledTri& f = ts[t];
                shellBB[sh].add(ts.points()[f[0]]);
                shellBB[sh].add(ts.points()[f[1]]);
                shellBB[sh].add(ts.points()[f[2]]);
            }
            // closedness: every edge used by exactly two triangles
            std::unordered_map<uint64_t, label> edgeUse;
            forAll(ts, t)
            {
                const labelledTri& f = ts[t];
                for (label k = 0; k < 3; ++k)
                {
                    label a = f[k], b = f[(k + 1) % 3];
                    if (a > b) { std::swap(a, b); }
                    ++edgeUse[(uint64_t(uint32_t(a)) << 32) | uint64_t(uint32_t(b))];
                }
            }
            forAll(ts, t)
            {
                const labelledTri& f = ts[t];
                for (label k = 0; k < 3; ++k)
                {
                    label a = f[k], b = f[(k + 1) % 3];
                    if (a > b) { std::swap(a, b); }
                    if (edgeUse[(uint64_t(uint32_t(a)) << 32) | uint64_t(uint32_t(b))] != 2)
                    {
                        shellClosed[triShell_[i][t]] = false;
                    }
                }
            }
        }
        shellSurface_ = labelList(std::move(shellSurf));
        shellBounds_ = List<boundBox>(std::move(shellBB));
        shellClosed_ = boolList(std::move(shellClosed));
        // ---- baffle sheets: connected AND coplanar parts of each baffle ----
        {
            DynamicList<baffleSheet> sheets;
            DynamicList<DynamicList<labelPair>> sheetTris;
            forAll(surfaces_, i)
            {
                const surfaceInfo& si = info_[i];
                if (!si.internalBaffle || si.faceZone.empty()) { continue; }
                label zone = -1;
                forAll(faceZoneNames_, z) { if (faceZoneNames_[z] == si.faceZone) { zone = z; break; } }
                if (zone < 0) { continue; }
                const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
                const vectorField& fn = ts.faceNormals();
                const label nT = ts.size();
                if (!nT) { continue; }
                // a sheet is one PLANE of the file: every triangle lying in
                // the same plane (normal up to sign, offset within 0.1 mm)
                // joins it, connected or not — a rack door is dozens of quads
                // that share no vertex, and an edge crossing one quad must
                // count as crossing the door
                labelList parent(nT);
                forAll(parent, t) { parent[t] = t; }
                std::function<label(label)> find = [&](label a) -> label
                {
                    while (parent[a] != a) { parent[a] = parent[parent[a]]; a = parent[a]; }
                    return a;
                };
                std::map<std::tuple<long, long, long, long>, label> planeRep;
                forAll(ts, t)
                {
                    vector n = fn[t];
                    // sign-normalise: largest component positive
                    label big = 0;
                    for (direction d = 1; d < 3; ++d) { if (std::abs(n[d]) > std::abs(n[big])) { big = d; } }
                    if (n[big] < 0) { n = -n; }
                    const scalar off = n & ts.points()[ts[t][0]];
                    const auto key = std::make_tuple
                    (
                        (long)std::lround(n.x() * 500), (long)std::lround(n.y() * 500),
                        (long)std::lround(n.z() * 500), (long)std::lround(off * 1e4)
                    );
                    auto it = planeRep.find(key);
                    if (it == planeRep.end()) { planeRep[key] = t; }
                    else
                    {
                        const label ra = find(it->second), rb = find(t);
                        if (ra != rb) { parent[ra] = rb; }
                    }
                }
                Map<label> rootSheet;
                forAll(ts, t)
                {
                    const label root = find(t);
                    label sh;
                    const auto it = rootSheet.find(root);
                    if (it == rootSheet.end())
                    {
                        sh = sheets.size();
                        rootSheet.insert(root, sh);
                        baffleSheet bs;
                        bs.zone = zone;
                        bs.normal = fn[t];
                        bs.origin = ts.points()[ts[t][0]];
                        bs.bounds = boundBox(boundBox::invertedBox);
                        sheets.append(bs);
                        sheetTris.append(DynamicList<labelPair>());
                    }
                    else { sh = *it; }
                    sheetTris[sh].append(labelPair(i, t));
                    for (label k = 0; k < 3; ++k) { sheets[sh].bounds.add(ts.points()[ts[t][k]]); }
                }
            }
            sheets_ = List<baffleSheet>(std::move(sheets));
            sheetTris_.setSize(sheetTris.size());
            forAll(sheetTris, k) { sheetTris_[k] = List<labelPair>(std::move(sheetTris[k])); }
        }

        shellTris_.setSize(shellSurface_.size());
        {
            List<DynamicList<labelPair>> tmp(shellSurface_.size());
            forAll(triShell_, i)
            {
                forAll(triShell_[i], t)
                {
                    if (triShell_[i][t] >= 0) { tmp[triShell_[i][t]].append(labelPair(i, t)); }
                }
            }
            forAll(tmp, sh) { shellTris_[sh] = List<labelPair>(std::move(tmp[sh])); }
        }
    }
    // Preserve the exact filtering order of the reference queries, including
    // undeclared reserved ids and any repeated id across surfaces. No values
    // derived from points are stored here. Triangle indices and region offsets
    // are fixed after construction; later source identities do not alter them.
    forAll(surfaces_, i)
    {
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
        forAll(ts, t)
        {
            regionTris_[info_[i].regionOffset + ts[t].region()].push_back(labelPair(i, t));
        }
    }
    // No public API edits surface points or shell membership. Bind only now:
    // every coordinate snap/weld and final triangle/point membership is done.
    if (!shellGeometryEpoch_) { shellGeometryEpoch_ = 1; }
    shellMembershipEpoch_ = shellGeometryEpoch_;
    shellCycleCache_.resize(shellTris_.size());
}


void Foam::dcCartSurfaces::solidShellHits
(
    label sh,
    const point& p0,
    const point& p1,
    std::vector<point>& hits,
    std::vector<label>& regions,
    std::vector<vector>* normals
) const
{
    hits.clear();
    regions.clear();
    if (normals) { normals->clear(); }
    const label i = shellSurface_[sh];
    const triSurfaceMesh& sm = surfaces_[i];
    const triSurface& ts = static_cast<const triSurface&>(sm);
    // triSurfaceSearch::findLineAll for one segment: nearest hit, mask it,
    // again — with the boxed triangle test
    static thread_local DynamicList<pointIndexHit> all;
    static thread_local DynamicList<label> shapeMask;
    all.clear();
    shapeMask.clear();
    {
        const auto& tree = sm.tree();
        const scalar oldTol = indexedOctree<treeDataTriSurface>::perturbTol
        (
            static_cast<const triSurfaceSearch&>(sm).tolerance()
        );
        const boxedIntersectOp op{tree, grownBounds(i), &shapeMask};
        while (true)
        {
            const pointIndexHit inter = tree.findLine(p0, p1, op);
            if (!inter.hit()) { break; }
            const vector lineVec = normalised(p1 - p0);
            if (uniqueHit(ts, inter, all, lineVec)) { all.append(inter); }
            shapeMask.append(inter.index());
        }
        indexedOctree<treeDataTriSurface>::perturbTol(oldTol);
    }
    for (const pointIndexHit& h : all)
    {
        if (!h.hit()) { continue; }
        const label t = h.index();
        if (t < 0 || t >= ts.size() || triShell_[i][t] != sh) { continue; }
        hits.push_back(h.point());
        regions.push_back(info_[i].regionOffset + ts[t].region());
        if (normals) { normals->push_back(ts.faceNormals()[t]); }
    }
}


void Foam::dcCartSurfaces::solidShellTrianglesInBox
(
    label sh,const boundBox& bb,std::vector<triInfo>& out
) const
{
    solidShellTrianglesInBox(sh,bb,out,nullptr);
}

void Foam::dcCartSurfaces::solidShellTrianglesInBox
(
    label sh,const boundBox& bb,std::vector<triInfo>& out,
    preparedFacetObservation* observation
) const
{
    out.clear();
    const auto started=observation?std::chrono::steady_clock::now():std::chrono::steady_clock::time_point();
    if(observation)*observation=preparedFacetObservation();
    const std::uint64_t entryGeometry=shellGeometryEpoch_,entryMembership=shellMembershipEpoch_;
    const label i=shellSurface_[sh];
    const triSurfaceMesh& sm=surfaces_[i];
    const triSurface& ts=static_cast<const triSurface&>(sm);
    static thread_local std::vector<label> found;
    findBoxToc(i,treeBoundBox(bb),found);
    const pointField& pts=ts.points();const vectorField& fn=ts.faceNormals();
    bool diagnosticValid=observation && entryGeometry && entryMembership==entryGeometry
      &&i>=0&&i<triShell_.size()&&i<info_.size()&&triShell_[i].size()==ts.size()
      &&fn.size()==ts.size();
    if(observation)
    {
        observation->indexCandidates=found.size();
        for(direction d=0;d<3;++d)if(!std::isfinite(bb.min()[d])||!std::isfinite(bb.max()[d])
          ||bb.min()[d]>bb.max()[d]||!std::isfinite(bb.max()[d]-bb.min()[d]))diagnosticValid=false;
    }
    for(const label t:found)
    {
        if(triShell_[i][t]!=sh)continue;
        const labelledTri& f=ts[t];triInfo ti;
        ti.p0=pts[f[0]];ti.p1=pts[f[1]];ti.p2=pts[f[2]];
        ti.normal=fn[t];ti.region=info_[i].regionOffset+f.region();
        out.push_back(ti);
        if(observation)
        {
          ++observation->shellCandidates;
          if(observation->failed)continue;
          try
          {
            if(observation->rows.size()>=2048){observation->truncated=true;continue;}
            preparedFacetAddress row;row.surface=i;row.triangle=t;row.pointIds={{f[0],f[1],f[2]}};
            row.geometryEpoch=entryGeometry;row.membershipEpoch=entryMembership;
            row.valid=diagnosticValid&&t>=0&&t<ts.size();
            for(label p:row.pointIds)if(p<0||p>=pts.size())row.valid=false;
            const label local=f.region();const std::int64_t global=std::int64_t(info_[i].regionOffset)+local;
            if(local<0||local>=info_[i].nRegions||local>=surfaces_[i].regions().size()
              ||global<0||global>=patchNames_.size()||global>=patchTypes_.size()
              ||global>=regionSurface_.size()||regionSurface_[global]!=i)row.valid=false;
            for(direction d=0;d<3;++d)if(!std::isfinite(ti.p0[d])||!std::isfinite(ti.p1[d])
              ||!std::isfinite(ti.p2[d])||!std::isfinite(ti.normal[d]))row.valid=false;
            // API validates surface/local-region indices; never call it on invalid metadata.
            if(!sourceIdentities_.empty()&&(i>=sourceIdentities_.size()||local<0||local>=sourceIdentities_[i].size()))row.valid=false;
            if(row.valid)
            {
                const sourceSurfaceIdentity* identity=sourceIdentity(i,local);
                if(identity){row.identityBound=true;row.sourceObjectUid=identity->sourceObjectUid;
                  row.sourceFaceUid=identity->sourceFaceUid;row.representation=identity->representation;}
            }
            observation->rows.push_back(std::move(row));
          }
          catch(...){observation->failed=true;diagnosticValid=false;}
        }
    }
    if(observation)
    {
        observation->complete=diagnosticValid&&!observation->truncated&&!observation->failed
          &&entryGeometry==shellGeometryEpoch_&&entryMembership==shellMembershipEpoch_
          &&observation->rows.size()==out.size();
        for(const auto& row:observation->rows)if(!row.valid)observation->complete=false;
        observation->querySeconds=std::chrono::duration<scalar>(std::chrono::steady_clock::now()-started).count();
    }
}


bool Foam::dcCartSurfaces::preparedFacetWindowHeld
(
    const dcCartPreparedFacetPartition::Window& window
) const
{
    const auto sh=window.shell,i=window.surface;
    return window.complete && sh>=0 && sh<shellSurface_.size()
      &&sh<shellClosed_.size()&&shellClosed_[label(sh)]&&sh<shellTris_.size()
      &&i>=0&&i<surfaces_.size()&&i<info_.size()&&i<triShell_.size()
      &&shellSurface_[label(sh)]==i&&info_[label(i)].carves
      &&window.geometryEpoch!=0&&window.geometryEpoch==shellGeometryEpoch_
      &&window.membershipEpoch==shellMembershipEpoch_
      &&shellMembershipEpoch_==shellGeometryEpoch_;
}

bool Foam::dcCartSurfaces::preparedFacetWindow
(
    label sh,const boundBox& bb,std::size_t maxFacets,
    const std::function<bool()>& available,
    dcCartPreparedFacetPartition::Window& out,std::string* refusal
) const
{
    namespace G=dcCartPreparedFacetPartition;
    out=G::Window();if(refusal)refusal->clear();
    auto reject=[&](const char* reason)->bool
    {if(refusal){try{*refusal=reason;}catch(...){}}return false;};
    try
    {
        auto ready=[&]()->bool
        {return available&&available()&&dcCartSignedCrossing::supportedFloatingEnvironment();};
        if(!ready())return reject("window-clock-or-fp");
        if(sh<0||sh>=shellSurface_.size()||sh>=shellClosed_.size()||!shellClosed_[sh]
          ||sh>=shellTris_.size()||sh>=shellBounds_.size()
          ||!shellGeometryEpoch_||shellMembershipEpoch_!=shellGeometryEpoch_
          ||maxFacets==0||maxFacets>8192
          ||sizeof(scalar)!=sizeof(double)||std::numeric_limits<scalar>::digits!=53)
            return reject("window-shell-or-cap");
        const label i=shellSurface_[sh];
        if(i<0||i>=surfaces_.size()||i>=info_.size()||i>=triShell_.size()
          ||!info_[i].carves)return reject("window-surface");
        for(direction d=0;d<3;++d)
            if(!std::isfinite(bb.min()[d])||!std::isfinite(bb.max()[d])
              ||bb.min()[d]>bb.max()[d]||!std::isfinite(bb.max()[d]-bb.min()[d]))
                return reject("window-bounds");
        const triSurface& ts=static_cast<const triSurface&>(surfaces_[i]);
        const pointField& pts=ts.points();const vectorField& normals=ts.faceNormals();
        if(triShell_[i].size()!=ts.size()||normals.size()!=ts.size())
            return reject("window-address-sizes");
        // The trigger follows the ordinary cap query, which warms these exact
        // native caches. Refuse a different/cold path instead of unbounded setup.
        if(i>=triBb_.size()||triBb_[i].size()!=ts.size()
          ||i>=triSat_.size()||triSat_[i].size()!=std::size_t(ts.size()))
            return reject("window-query-cache-not-prepared");
        const auto& tree=surfaces_[i].tree();
        const auto& boxes=triBb_[i];const auto& sats=triSat_[i];
        G::Window window;window.surface=i;window.shell=sh;
        window.geometryEpoch=shellGeometryEpoch_;window.membershipEpoch=shellMembershipEpoch_;
        for(direction d=0;d<3;++d)
        {window.requestedMin[d]=double(bb.min()[d]);window.requestedMax[d]=double(bb.max()[d]);}
        // Exactly the reference findBoxToc insertion/rehash/toc order. The
        // visitor is bounded and stops atomically on time/cap/invalid addresses.
        label cap=HashTableCore::canonicalSize(2*max(label(128),label(ts.size())/100));
        std::vector<label> heads(std::size_t(cap),-1),next,keys;
        std::size_t visits=0;bool aborted=false;const char* cause="window-query";
        const treeBoundBox query(bb);
        auto visit=[&](label t)->bool
        {
            if(++visits>1048576||!ready())
            {aborted=true;cause="window-query-budget";return true;}
            if(t<0||t>=ts.size())
            {aborted=true;cause="window-index-address";return true;}
            if(!triBoxOverlap(query,boxes[t],sats[t]))return false;
            const label index=label(unsigned(t)&unsigned(cap-1));
            for(label n=heads[index];n>=0;n=next[n])if(keys[n]==t)return false;
            if(keys.size()>=8*maxFacets)
            {aborted=true;cause="window-index-cap";return true;}
            keys.push_back(t);next.push_back(heads[index]);heads[index]=label(keys.size())-1;
            if(0.8*cap<keys.size())
            {
                const label grown=HashTableCore::canonicalSize(2*cap);
                std::vector<label> replacement(std::size_t(grown),-1);
                for(label b=0;b<cap;++b)for(label n=heads[b];n>=0;)
                {const label old=next[n],target=label(unsigned(keys[n])&unsigned(grown-1));
                 next[n]=replacement[target];replacement[target]=n;n=old;}
                heads.swap(replacement);cap=grown;
            }
            return false;
        };
        if(!tree.nodes().empty())visitBox(tree,0,query,visit);
        if(aborted)return reject(cause);
        for(label b=0;b<cap;++b)for(label n=heads[b];n>=0;n=next[n])
        {
            if(!ready())return reject("window-copy-budget");
            const label t=keys[n];
            if(triShell_[i][t]!=sh)continue;
            if(window.facets.size()>=maxFacets)return reject("window-facet-cap");
            const labelledTri& f=ts[t];const label local=f.region();
            const std::int64_t global=std::int64_t(info_[i].regionOffset)+local;
            if(local<0||local>=info_[i].nRegions||local>=surfaces_[i].regions().size()
              ||global<0||global>=patchNames_.size()||global>=patchTypes_.size()
              ||global>=regionSurface_.size()||regionSurface_[label(global)]!=i)
                return reject("window-region-address");
            G::NativeFacet facet;facet.surface=i;facet.shell=sh;facet.triangle=t;
            facet.localRegion=local;facet.globalRegion=global;
            facet.geometryEpoch=window.geometryEpoch;facet.membershipEpoch=window.membershipEpoch;
            for(label k=0;k<3;++k)
            {
                const label p=f[k];if(p<0||p>=pts.size())return reject("window-point-address");
                facet.pointIds[k]=p;
                for(direction d=0;d<3;++d)
                {if(!std::isfinite(pts[p][d]))return reject("window-point-nonfinite");
                 facet.p[k][d]=double(pts[p][d]);}
            }
            for(direction d=0;d<3;++d)
            {if(!std::isfinite(normals[t][d]))return reject("window-normal-nonfinite");
             facet.normal[d]=double(normals[t][d]);}
            if(!sourceIdentities_.empty())
            {
                if(i>=sourceIdentities_.size()||local>=sourceIdentities_[i].size())
                    return reject("window-identity-address");
                // Copy actual nullable fields; empty face UID stays empty.
                const auto& identity=sourceIdentities_[i][local];
                facet.sourceObjectUid=identity.sourceObjectUid;
                facet.sourceFaceUid=identity.sourceFaceUid;
                facet.representation=identity.representation;
            }
            window.facets.push_back(std::move(facet));
        }
        window.complete=true;
        if(!ready()||!preparedFacetWindowHeld(window))return reject("window-stale");
        out=std::move(window);return true;
    }
    catch(const std::exception& e)
    {if(refusal){try{*refusal=std::string("window-exception:")+e.what();}catch(...){}}return false;}
    catch(...){return reject("window-exception-unknown");}
}

namespace
{
// closest point on triangle abc to p (Ericson, Real-Time Collision Detection)
Foam::point closestOnTri
(
    const Foam::point& p, const Foam::point& a, const Foam::point& b, const Foam::point& c
)
{
    using Foam::vector; using Foam::scalar;
    const vector ab = b - a, ac = c - a, ap = p - a;
    const scalar d1 = ab & ap, d2 = ac & ap;
    if (d1 <= 0 && d2 <= 0) { return a; }
    const vector bp = p - b;
    const scalar d3 = ab & bp, d4 = ac & bp;
    if (d3 >= 0 && d4 <= d3) { return b; }
    const scalar vc = d1 * d4 - d3 * d2;
    if (vc <= 0 && d1 >= 0 && d3 <= 0)
    {
        const scalar v = d1 / (d1 - d3);
        return a + v * ab;
    }
    const vector cp = p - c;
    const scalar d5 = ab & cp, d6 = ac & cp;
    if (d6 >= 0 && d5 <= d6) { return c; }
    const scalar vb = d5 * d2 - d1 * d6;
    if (vb <= 0 && d2 >= 0 && d6 <= 0)
    {
        const scalar w = d2 / (d2 - d6);
        return a + w * ac;
    }
    const scalar va = d3 * d6 - d5 * d4;
    if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0)
    {
        const scalar w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
        return b + w * (c - b);
    }
    const scalar denom = 1.0 / (va + vb + vc);
    const scalar v = vb * denom, w = vc * denom;
    return a + ab * v + ac * w;
}
inline Foam::scalar pointTriDist2
(
    const Foam::point& p, const Foam::point& a, const Foam::point& b, const Foam::point& c
)
{
    return Foam::magSqr(p - closestOnTri(p, a, b, c));
}
}


Foam::label Foam::dcCartSurfaces::solidShellSide
(
    label sh,
    const point& pt,
    scalar tol,
    label& region
) const
{
    region = -1;
    scalar best = GREAT;
    std::vector<std::pair<scalar, label>> near;   // dist2, index into shellTris_
    const List<labelPair>& tris = shellTris_[sh];
    std::vector<point> cps(tris.size());
    forAll(tris, k)
    {
        const label i = tris[k].first();
        const label t = tris[k].second();
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
        const labelledTri& f = ts[t];
        const pointField& pts = ts.points();
        cps[k] = closestOnTri(pt, pts[f[0]], pts[f[1]], pts[f[2]]);
        const scalar d2 = magSqr(pt - cps[k]);
        if (d2 < best)
        {
            best = d2;
            region = info_[i].regionOffset + f.region();
        }
    }
    if (best >= GREAT) { return 1; }
    if (best <= tol * tol) { return 0; }
    // every triangle tied for nearest: behind any one of them is inside
    const scalar tie = best + 1e-12 * (1 + best);
    bool behind = false;
    forAll(tris, k)
    {
        const label i = tris[k].first();
        const label t = tris[k].second();
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
        if (magSqr(pt - cps[k]) > tie) { continue; }
        if (((pt - cps[k]) & ts.faceNormals()[t]) < 0) { behind = true; break; }
    }
    return behind ? -1 : 1;
}


bool Foam::dcCartSurfaces::solidShellInside(label sh, const point& pt) const
{
    if (!shellClosed_[sh]) { return false; }
    const label i = shellSurface_[sh];
    const triSurfaceMesh& sm = surfaces_[i];
    List<volumeType> vt;
    sm.getVolumeType(pointField(1, pt), vt);
    if (vt[0] != volumeType::INSIDE) { return false; }
    List<pointIndexHit> hits;
    sm.findNearest(pointField(1, pt), scalarField(1, GREAT), hits);
    if (!hits[0].hit()) { return false; }
    const label t = hits[0].index();
    return (t >= 0 && t < triShell_[i].size() && triShell_[i][t] == sh);
}


bool Foam::dcCartSurfaces::solidShellPreparedSnapshot
(
    label sh,
    preparedShellSnapshot& out,
    const std::function<bool()>& available
) const
{
    out = preparedShellSnapshot();
    if (sh < 0 || sh >= shellTris_.size() || sh >= shellSurface_.size()
     || sh >= shellBounds_.size() || !shellGeometryEpoch_
     || shellMembershipEpoch_ != shellGeometryEpoch_
     || sizeof(scalar) != sizeof(double) || std::numeric_limits<scalar>::digits != 53)
    {
        return false;
    }
    const label surface = shellSurface_[sh];
    if (surface < 0 || surface >= surfaces_.size() || surface >= info_.size()) { return false; }
    const triSurface& ts = static_cast<const triSurface&>(surfaces_[surface]);
    const pointField& pts = ts.points();
    const List<labelPair>& addresses = shellTris_[sh];
    if (addresses.empty() || addresses.size() > 1048576) { return false; }
    try
    {
        auto ready = [&]() -> bool
        {
            try { return available && available(); }
            catch (...) { return false; }
        };
        if (!ready()) { return false; }
        preparedShellSnapshot snapshot;
        snapshot.surface = surface;
        snapshot.geometryEpoch = shellGeometryEpoch_;
        snapshot.membershipEpoch = shellMembershipEpoch_;
        std::unordered_map<label, std::uint64_t> compact;
        compact.reserve(3 * std::size_t(addresses.size()));
        snapshot.triangles.reserve(addresses.size());
        snapshot.localRegions.reserve(addresses.size());
        for (const labelPair& address : addresses)
        {
            if (!ready() || address.first() != surface || address.second() < 0
             || address.second() >= ts.size()) { return false; }
            const labelledTri& triangle = ts[address.second()];
            dcCartSignedCrossing::Triangle member;
            member.surface = std::uint64_t(surface);
            member.originalTriangle = std::uint64_t(address.second());
            for (label k = 0; k < 3; ++k)
            {
                const label original = triangle[k];
                if (original < 0 || original >= pts.size()) { return false; }
                const auto found = compact.find(original);
                if (found != compact.end()) { member.vertices[k] = found->second; continue; }
                if (snapshot.points.size() >= 2097152) { return false; }
                const point& p = pts[original];
                for (direction d = 0; d < 3; ++d) { if (!std::isfinite(p[d])) { return false; } }
                const std::uint64_t index = snapshot.points.size();
                compact.emplace(original, index);
                snapshot.points.push_back(dcCartSignedCrossing::Point{{double(p.x()), double(p.y()), double(p.z())}});
                snapshot.originalPointIds.push_back(original);
                member.vertices[k] = index;
            }
            snapshot.triangles.push_back(member);
            snapshot.localRegions.push_back(triangle.region());
        }
        if (!ready() || snapshot.geometryEpoch != shellGeometryEpoch_
         || snapshot.membershipEpoch != shellMembershipEpoch_) { return false; }
        out = std::move(snapshot);
        return true;
    }
    catch (...) { return false; }
}


bool Foam::dcCartSurfaces::solidShellWindingDegree
(
    label sh,
    const point& pt,
    scalar& degree,
    const std::function<bool()>& available
) const
{
    degree = 0;
    if (!dcCartSignedCrossing::supportedFloatingEnvironment()
     || sh < 0 || sh >= shellClosed_.size() || !shellClosed_[sh]
     || sh >= shellCycleCache_.size() || !shellGeometryEpoch_
     || shellMembershipEpoch_ != shellGeometryEpoch_) { return false; }
    shellCycleCacheEntry& cached = shellCycleCache_[sh];
    if (cached.geometryEpoch != shellGeometryEpoch_
     || cached.membershipEpoch != shellMembershipEpoch_)
    {
        cached = shellCycleCacheEntry();
        cached.geometryEpoch = shellGeometryEpoch_;
        cached.membershipEpoch = shellMembershipEpoch_;
    }
    if (!cached.attempted)
    {
        // A structural/capacity/clock refusal is cached for this immutable
        // preparation epoch; do not repeatedly scan a failing large carrier.
        cached.attempted = true;
        preparedShellSnapshot snapshot;
        if (!solidShellPreparedSnapshot(sh, snapshot, available)) { return false; }
        if (!cached.cycle.prepare(snapshot.points, snapshot.triangles, available)) { return false; }
    }
    const auto result = cached.cycle.classify
    (
        dcCartSignedCrossing::Point{{double(pt.x()), double(pt.y()), double(pt.z())}}, available
    );
    if (!result.known) { return false; }
    degree = scalar(result.degree); // bounded integer exactly representable in DP
    return true;
}


Foam::scalar Foam::dcCartSurfaces::solidShellWinding(label sh, const point& pt) const
{
    // Van Oosterom & Strackee (1983) solid angle per triangle
    scalar sum = 0;
    const List<labelPair>& tris = shellTris_[sh];
    forAll(tris, k)
    {
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[tris[k].first()]);
        const labelledTri& f = ts[tris[k].second()];
        const pointField& pts = ts.points();
        const vector a = pts[f[0]] - pt, b = pts[f[1]] - pt, c = pts[f[2]] - pt;
        const scalar la = mag(a), lb = mag(b), lc = mag(c);
        const scalar num = a & (b ^ c);
        const scalar den = la * lb * lc + (a & b) * lc + (a & c) * lb + (b & c) * la;
        sum += 2.0 * std::atan2(num, den);
    }
    return sum / (4.0 * Foam::constant::mathematical::pi);
}


bool Foam::dcCartSurfaces::baffleSheetHit
(
    label sheet,
    const point& p0,
    const point& p1,
    point& hit
) const
{
    // the sheet's triangles are all in one surface; ask that surface for
    // every hit and keep the nearest one belonging to the sheet
    const List<labelPair>& tris = sheetTris_[sheet];
    if (tris.empty()) { return false; }
    const label i = tris[0].first();
    const triSurfaceMesh& sm = surfaces_[i];
    List<List<pointIndexHit>> all;
    sm.findLineAll(pointField(1, p0), pointField(1, p1), all);
    std::unordered_set<label> mine;
    forAll(tris, k) { mine.insert(tris[k].second()); }
    for (const pointIndexHit& h : all[0])
    {
        if (h.hit() && mine.count(h.index())) { hit = h.point(); return true; }
    }
    return false;
}


bool Foam::dcCartSurfaces::baffleSheetOverlaps(label sheet, const boundBox& bb) const
{
    // the sheet's own triangles, not its bounding box: a slanted sheet's
    // box covers a whole wedge of cells the sheet never touches, and every
    // one of those would be split against the plane with no crossing to
    // find — a failed split and a stair-step cell for nothing
    if (!sheets_[sheet].bounds.overlaps(bb)) { return false; }
    const List<labelPair>& tris = sheetTris_[sheet];
    if (tris.empty()) { return false; }
    const label i = tris[0].first();
    const auto& tree = surfaces_[i].tree();
    if (tree.nodes().empty()) { return false; }
    // any triangle findBox reports that is one of the sheet's
    const std::vector<label>& members = sheetMembers(sheet);
    const treeBoundBox tbb(bb);
    const List<boundBox>& bbs = triBounds(i);
    const std::vector<dcCartTriSat>& sats = triSats(i);
    auto visit = [&](const label t) -> bool
    {
        return std::binary_search(members.begin(), members.end(), t)
            && triBoxOverlap(tbb, bbs[t], sats[t]);
    };
    return visitBox(tree, 0, tbb, visit);
}


Foam::scalar Foam::dcCartSurfaces::baffleSheetExposedArea
(
    label sheet,
    const boundBox& domain
) const
{
    scalar area = 0;
    const List<labelPair>& tris = sheetTris_[sheet];
    if (tris.empty()) { return 0; }
    const label i = tris[0].first();
    const triSurfaceMesh& sm = surfaces_[i];
    const triSurface& ts = static_cast<const triSurface&>(sm);
    const pointField& pts = ts.points();
    for (const labelPair& tp : tris)
    {
        if (tp.first() != i) { continue; }
        const labelledTri& tr = ts[tp.second()];
        const point c = (pts[tr[0]] + pts[tr[1]] + pts[tr[2]]) / 3;
        const scalar a =
            mag((pts[tr[1]] - pts[tr[0]]) ^ (pts[tr[2]] - pts[tr[0]])) / 2;
        // parity of +x ray crossings against every CLOSED shell: odd = buried
        bool buried = false;
        for (label sh = 0; sh < shellClosed_.size() && !buried; ++sh)
        {
            if (!shellClosed_[sh]) { continue; }
            boundBox sb(shellBounds_[sh]);
            sb.inflate(1e-9);
            if (!sb.contains(c)) { continue; }
            const scalar cy = c.component(vector::Y);
            const scalar cz = c.component(vector::Z);
            label crossings = 0;
            for (const labelPair& st : shellTris_[sh])
            {
                const triSurfaceMesh& ssm = surfaces_[st.first()];
                const triSurface& sts = static_cast<const triSurface&>(ssm);
                const pointField& sp = sts.points();
                const labelledTri& str = sts[st.second()];
                const point& q0 = sp[str[0]];
                const point& q1 = sp[str[1]];
                const point& q2 = sp[str[2]];
                const scalar d0 =
                    (q0.component(vector::Y) - cy) * (q1.component(vector::Z) - cz) - (q0.component(vector::Z) - cz) * (q1.component(vector::Y) - cy);
                const scalar d1 =
                    (q1.component(vector::Y) - cy) * (q2.component(vector::Z) - cz) - (q1.component(vector::Z) - cz) * (q2.component(vector::Y) - cy);
                const scalar d2 =
                    (q2.component(vector::Y) - cy) * (q0.component(vector::Z) - cz) - (q2.component(vector::Z) - cz) * (q0.component(vector::Y) - cy);
                const bool pos = (d0 > 0) || (d1 > 0) || (d2 > 0);
                const bool neg = (d0 < 0) || (d1 < 0) || (d2 < 0);
                if (pos && neg) { continue; }
                const scalar sum = d0 + d1 + d2;
                if (mag(sum) < VSMALL) { continue; }
                const scalar x = (d0 * q0.component(vector::X) + d1 * q1.component(vector::X) + d2 * q2.component(vector::X)) / sum;
                if (x > c.component(vector::X)) { ++crossings; }
            }
            if (crossings % 2 == 1) { buried = true; }
        }
        boundBox db(domain);
        db.inflate(1e-6);
        if (!buried && db.contains(c)) { area += a; }
    }
    return area;
}


Foam::scalar Foam::dcCartSurfaces::baffleSheetOpenArea
(
    label sheet,
    const boundBox& domain,
    const scalar probeDist,
    const scalar minTriArea,
    const scalar onTol
) const
{
    const List<labelPair>& tris = sheetTris_[sheet];
    if (tris.empty()) { return 0; }
    const label i = tris[0].first();
    const triSurfaceMesh& sm = surfaces_[i];
    const triSurface& ts = static_cast<const triSurface&>(sm);
    const pointField& pts = ts.points();
    const vector n = sheets_[sheet].normal / (mag(sheets_[sheet].normal) + VSMALL);

    // ---- the bodies that can seal part of the sheet -----------------------
    // Closed shells at least 50 mm across: a rack, a server, an ACU. Thin
    // shells are left out on purpose -- a sheet seals nothing, and a
    // zero-thickness baffle's own solid must never hide the hole being looked
    // for. Bucketed by bounding box because the probe below asks thousands of
    // times.
    boundBox queryBb(sheets_[sheet].bounds);
    queryBb.inflate(probeDist + 1e-9);
    // ---- the carving surfaces that can close the plane themselves ---------
    std::vector<label> carves;
    forAll(surfaces_, k)
    {
        if (!info_[k].carves) { continue; }
        boundBox cb(surfaces_[k].bounds());
        cb.inflate(onTol + 1e-9);
        if (!cb.overlaps(queryBb)) { continue; }
        carves.push_back(k);
    }
    std::vector<label> bodies;
    for (label sh = 0; sh < nSolidShells(); ++sh)
    {
        if (!solidShellClosed(sh)) { continue; }
        const boundBox& sb = solidShellBounds(sh);
        const vector span = sb.span();
        if (min(span.x(), min(span.y(), span.z())) < 0.05) { continue; }
        if (!sb.overlaps(queryBb)) { continue; }
        bodies.push_back(sh);
    }
    std::vector<std::vector<label>> buckets(8 * 8 * 8);
    const vector qspan = queryBb.span();
    const vector qh
    (
        max(qspan.x(), VSMALL) / 8,
        max(qspan.y(), VSMALL) / 8,
        max(qspan.z(), VSMALL) / 8
    );
    auto bucketOf = [&](const point& p) -> size_t
    {
        label ix = label(std::floor((p.x() - queryBb.min().x()) / qh.x()));
        label iy = label(std::floor((p.y() - queryBb.min().y()) / qh.y()));
        label iz = label(std::floor((p.z() - queryBb.min().z()) / qh.z()));
        ix = min(max(ix, label(0)), label(7));
        iy = min(max(iy, label(0)), label(7));
        iz = min(max(iz, label(0)), label(7));
        return size_t((ix * 8 + iy) * 8 + iz);
    };
    for (const label sh : bodies)
    {
        const boundBox sb(solidShellBounds(sh));
        const label x0 = min(max(label(std::floor((sb.min().x() - queryBb.min().x()) / qh.x())), label(0)), label(7));
        const label x1 = min(max(label(std::floor((sb.max().x() - queryBb.min().x()) / qh.x())), label(0)), label(7));
        const label y0 = min(max(label(std::floor((sb.min().y() - queryBb.min().y()) / qh.y())), label(0)), label(7));
        const label y1 = min(max(label(std::floor((sb.max().y() - queryBb.min().y()) / qh.y())), label(0)), label(7));
        const label z0 = min(max(label(std::floor((sb.min().z() - queryBb.min().z()) / qh.z())), label(0)), label(7));
        const label z1 = min(max(label(std::floor((sb.max().z() - queryBb.min().z()) / qh.z())), label(0)), label(7));
        for (label a = x0; a <= x1; ++a)
        for (label b = y0; b <= y1; ++b)
        for (label c = z0; c <= z1; ++c)
        {
            buckets[size_t((a * 8 + b) * 8 + c)].push_back(sh);
        }
    }

    // ---- the probe, memoised ----------------------------------------------
    // Recursive subdivision revisits every shared edge midpoint and corner,
    // so the answer is cached on the point's exact bits: sibling triangles
    // compute a shared midpoint bit-identically, no quantisation needed.
    struct key3
    {
        uint64_t a, b, c;
        bool operator==(const key3& o) const { return a == o.a && b == o.b && c == o.c; }
    };
    struct key3Hash
    {
        size_t operator()(const key3& k) const
        {
            uint64_t h = k.a * 0x9E3779B97F4A7C15ULL;
            h ^= k.b + 0x632BE59BD9B4E019ULL + (h << 6) + (h >> 2);
            h ^= k.c + 0x8CB92BA72F3D8DD7ULL + (h << 6) + (h >> 2);
            h ^= h >> 31;
            return size_t(h);
        }
    };
    std::unordered_map<key3, signed char, key3Hash> memo;
    auto bits = [](scalar v) -> uint64_t
    {
        uint64_t u = 0;
        std::memcpy(&u, &v, sizeof(v));
        return u;
    };
    auto inBody = [&](const point& p) -> bool
    {
        for (const label sh : buckets[bucketOf(p)])
        {
            boundBox sb(solidShellBounds(sh));
            sb.inflate(1e-12);
            if (!sb.contains(p)) { continue; }
            if (solidShellInside(sh, p)) { return true; }
        }
        return false;
    };
    // A carving surface within onTol of the point: the mesh already carries
    // that surface's wall face in the sheet plane, so no baffle face is
    // missing there.
    auto onCarvingSurface = [&](const point& p) -> bool
    {
        for (const label k : carves)
        {
            boundBox cb(surfaces_[k].bounds());
            cb.inflate(onTol + 1e-9);
            if (!cb.contains(p)) { continue; }
            List<pointIndexHit> hit;
            surfaces_[k].findNearest
            (
                pointField(1, p),
                scalarField(1, onTol),
                hit
            );
            if (hit[0].hit()) { return true; }
        }
        return false;
    };

    // OPEN = the mesh must tile here: fluid on both sides of the sheet plane
    // with nothing between them
    auto openAt = [&](const point& p) -> signed char
    {
        const key3 k{bits(p.x()), bits(p.y()), bits(p.z())};
        const auto it = memo.find(k);
        if (it != memo.end()) { return it->second; }
        signed char v = 1;
        if (inBody(p + probeDist * n) || inBody(p - probeDist * n)) { v = 0; }
        else if (onCarvingSurface(p)) { v = 0; }
        memo.emplace(k, v);
        return v;
    };

    boundBox db(domain);
    db.inflate(1e-6);

    // ---- adaptive subdivision of one triangle ------------------------------
    // All five probes agreeing settles the whole triangle; only the
    // sealed/open boundary is refined. A leaf is therefore at most
    // sqrt(minTriArea) across, and the area it can misattribute is bounded by
    // half that times the boundary length.
    scalar area = 0;
    label leaves = 0;
    const label leafCap = 4000000;
    std::function<void(const point&, const point&, const point&, label)> rec =
        [&](const point& a, const point& b, const point& c, label depth)
    {
        const scalar ar = mag((b - a) ^ (c - a)) / 2;
        if (ar <= 0) { return; }
        const point cen = (a + b + c) / 3;
        if (!db.contains(cen)) { return; }
        if (++leaves > leafCap) { area += ar; return; }   // conservative
        const signed char sa = openAt(a), sb = openAt(b), sc = openAt(c);
        const signed char sm = openAt(cen);
        if (sa == sb && sb == sc && sc == sm)
        {
            if (sa) { area += ar; }
            return;
        }
        if (ar <= minTriArea || depth >= 26)
        {
            if (sm) { area += ar; }
            return;
        }
        const point ab = 0.5 * (a + b);
        const point bc = 0.5 * (b + c);
        const point ca = 0.5 * (c + a);
        rec(a, ab, ca, depth + 1);
        rec(ab, b, bc, depth + 1);
        rec(ca, bc, c, depth + 1);
        rec(ab, bc, ca, depth + 1);
    };

    for (const labelPair& tp : tris)
    {
        if (tp.first() != i) { continue; }
        const labelledTri& tr = ts[tp.second()];
        rec(pts[tr[0]], pts[tr[1]], pts[tr[2]], 0);
    }
    return area;
}


Foam::scalar Foam::dcCartSurfaces::regionArea(label globalRegion) const
{
    const bool compare = (getenv("DCCART_COMPARE_REGION_QUERIES") != nullptr);
    if (!compare && getenv("DCCART_INDEX_REGIONS") == nullptr) { return regionAreaReference(globalRegion); }
    scalar area = 0;
    const auto found = regionTris_.find(globalRegion);
    if (found != regionTris_.end())
    {
        for (const labelPair& tp : found->second)
        {
            const triSurface& ts = static_cast<const triSurface&>(surfaces_[tp.first()]);
            const pointField& pts = ts.points();
            const labelledTri& tr = ts[tp.second()];
            area += mag((pts[tr[1]] - pts[tr[0]]) ^ (pts[tr[2]] - pts[tr[0]])) / 2;
        }
    }
    if (compare)
    {
        const scalar reference = regionAreaReference(globalRegion);
        if (area != reference) { FatalErrorInFunction << "indexed region area differs for " << globalRegion << exit(FatalError); }
        ++nRegionAreaAudit_;
    }
    return area;
}


bool Foam::dcCartSurfaces::regionPlane(label globalRegion, vector& n, boundBox& bb) const
{
    const bool compare = (getenv("DCCART_COMPARE_REGION_QUERIES") != nullptr);
    if (!compare && getenv("DCCART_INDEX_REGIONS") == nullptr) { return regionPlaneReference(globalRegion, n, bb); }
    n = vector::zero;
    bb = boundBox(boundBox::invertedBox);
    bool any = false;
    const auto found = regionTris_.find(globalRegion);
    if (found != regionTris_.end())
    {
        for (const labelPair& tp : found->second)
        {
            const triSurface& ts = static_cast<const triSurface&>(surfaces_[tp.first()]);
            const pointField& pts = ts.points();
            const labelledTri& tr = ts[tp.second()];
            const vector tn = (pts[tr[1]] - pts[tr[0]]) ^ (pts[tr[2]] - pts[tr[0]]);
            const scalar a = mag(tn);
            if (a < VSMALL) { continue; }
            n += tn;
            for (const label v : tr) { bb.add(pts[v]); }
            any = true;
        }
    }
    const scalar a = mag(n);
    const bool valid = any && a >= VSMALL;
    if (valid) { n /= a; }
    if (compare)
    {
        vector referenceNormal; boundBox referenceBounds;
        const bool reference = regionPlaneReference(globalRegion, referenceNormal, referenceBounds);
        if (valid != reference || n != referenceNormal || bb.min() != referenceBounds.min() || bb.max() != referenceBounds.max())
        { FatalErrorInFunction << "indexed region plane differs for " << globalRegion << exit(FatalError); }
        ++nRegionPlaneAudit_;
    }
    return valid;
}


bool Foam::dcCartSurfaces::regionIsThinSheet(label globalRegion) const
{
    const bool compare = (getenv("DCCART_COMPARE_REGION_QUERIES") != nullptr);
    if (!compare && getenv("DCCART_INDEX_REGIONS") == nullptr) { return regionIsThinSheetReference(globalRegion); }
    bool thin = false;
    const auto found = regionTris_.find(globalRegion);
    if (found != regionTris_.end() && !found->second.empty())
    {
        const labelPair& tp = found->second.front();
        const label sh = triShell_[tp.first()][tp.second()];
        const vector span = shellBounds_[sh].span();
        thin = min(span.x(), min(span.y(), span.z())) < 0.05;
    }
    if (compare)
    {
        if (thin != regionIsThinSheetReference(globalRegion))
        { FatalErrorInFunction << "indexed thin-sheet predicate differs for " << globalRegion << exit(FatalError); }
        ++nRegionThinAudit_;
    }
    return thin;
}


void Foam::dcCartSurfaces::reportRegionQueryAudit() const
{
    if (getenv("DCCART_COMPARE_REGION_QUERIES") != nullptr)
    {
        // Sweep every post-weld registry key, including undeclared triangle
        // ids and ids shared across surfaces, plus declared holes and a miss.
        // Non-carving triangles have no shell: the reference thin predicate
        // is undefined for those ids, so do not manufacture an oracle there.
        std::set<label> regions;
        for (const auto& entry : regionTris_) { regions.insert(entry.first); }
        forAll(patchNames_, rg) { regions.insert(rg); }
        regions.insert(-1);
        regions.insert(patchNames_.size());
        label nUndefinedThin = 0;
        for (const label rg : regions)
        {
            regionArea(rg);
            vector normal; boundBox bounds;
            regionPlane(rg, normal, bounds);
            const auto found = regionTris_.find(rg);
            bool thinDefined = found == regionTris_.end() || found->second.empty();
            if (!thinDefined)
            {
                const labelPair& tp = found->second.front();
                const label sh = triShell_[tp.first()][tp.second()];
                thinDefined = sh >= 0 && sh < shellBounds_.size();
            }
            if (thinDefined) { regionIsThinSheet(rg); }
            else { ++nUndefinedThin; }
        }
        Pout<< "Region registry sweep: " << label(regions.size())
            << " ids, undefined non-carving thin ids skipped " << nUndefinedThin << nl;
        Pout<< "Region query audit: exact reference area/plane/thin results match; area "
            << nRegionAreaAudit_ << ", plane " << nRegionPlaneAudit_
            << ", thin " << nRegionThinAudit_ << nl;
    }
}


bool Foam::dcCartSurfaces::regionPlaneReference
(
    label globalRegion,
    vector& n,
    boundBox& bb
) const
{
    n = vector::zero;
    bb = boundBox(boundBox::invertedBox);
    bool any = false;
    forAll(surfaces_, i)
    {
        const label off = info_[i].regionOffset;
        const triSurfaceMesh& sm = surfaces_[i];
        const triSurface& ts = static_cast<const triSurface&>(sm);
        const pointField& pts = ts.points();
        forAll(ts, t)
        {
            if (off + ts[t].region() != globalRegion) { continue; }
            const labelledTri& tr = ts[t];
            const vector tn = (pts[tr[1]] - pts[tr[0]]) ^ (pts[tr[2]] - pts[tr[0]]);
            const scalar a = mag(tn);
            if (a < VSMALL) { continue; }
            n += tn;
            for (const label v : tr) { bb.add(pts[v]); }
            any = true;
        }
    }
    if (!any) { return false; }
    const scalar a = mag(n);
    if (a < VSMALL) { return false; }
    n /= a;
    return true;
}


Foam::scalar Foam::dcCartSurfaces::regionAreaReference(label globalRegion) const
{
    scalar area = 0;
    forAll(surfaces_, i)
    {
        const label off = info_[i].regionOffset;
        const triSurfaceMesh& sm = surfaces_[i];
        const triSurface& ts = static_cast<const triSurface&>(sm);
        const pointField& pts = ts.points();
        forAll(ts, t)
        {
            if (off + ts[t].region() != globalRegion) { continue; }
            const labelledTri& tr = ts[t];
            area += mag((pts[tr[1]] - pts[tr[0]]) ^ (pts[tr[2]] - pts[tr[0]])) / 2;
        }
    }
    return area;
}


Foam::scalar Foam::dcCartSurfaces::regionContactArea
(
    label globalRegion,
    const scalar contactTol
) const
{
    // ---- the region's plane, basis and 2D triangles ------------------------
    vector nR;
    boundBox bbR;
    if (!regionPlane(globalRegion, nR, bbR)) { return 0; }
    const scalar offR = bbR.midpoint() & nR;

    // Orthonormal (u, v) in the plane: drop the normal's smallest component
    // to build the first axis, so the basis is a deterministic function of
    // the normal (identical on every rank, at any decomposition).
    label kAx = 0;
    if (mag(nR.y()) < mag(nR.component(kAx))) { kAx = 1; }
    if (mag(nR.z()) < mag(nR.component(kAx))) { kAx = 2; }
    vector eAx(0, 0, 0);
    eAx.component(kAx) = 1;
    vector u = nR ^ eAx;
    u /= mag(u) + VSMALL;
    const vector v = nR ^ u;

    struct tri2
    {
        Vector2D<scalar> a, b, c;
    };
    auto proj = [&](const point& p) -> Vector2D<scalar>
    {
        return Vector2D<scalar>(p & u, p & v);
    };

    std::vector<tri2> mine;
    scalar mineArea = 0;
    label ownShell = -1;
    forAll(surfaces_, i)
    {
        const label off = info_[i].regionOffset;
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
        const pointField& pts = ts.points();
        forAll(ts, t)
        {
            if (off + ts[t].region() != globalRegion) { continue; }
            const labelledTri& tr = ts[t];
            if (ownShell < 0) { ownShell = triShell_[i][t]; }
            tri2 q;
            q.a = proj(pts[tr[0]]);
            q.b = proj(pts[tr[1]]);
            q.c = proj(pts[tr[2]]);
            mine.push_back(q);
            mineArea += 0.5 * mag
            (
                (q.b.x() - q.a.x()) * (q.c.y() - q.a.y())
              - (q.b.y() - q.a.y()) * (q.c.x() - q.a.x())
            );
        }
    }
    if (mine.empty() || mineArea <= 0) { return 0; }

    // ---- exact 2D triangle-triangle overlap (Sutherland-Hodgman + shoelace)
    auto cross2 = [](const Vector2D<scalar>& p, const Vector2D<scalar>& q)
    {
        return p.x() * q.y() - p.y() * q.x();
    };
    auto toCCW = [&](tri2& t)
    {
        if (cross2(t.b - t.a, t.c - t.a) < 0) { std::swap(t.b, t.c); }
    };
    const scalar eps = 1e-12;
    auto clipArea = [&](tri2 subj, const tri2& clip) -> scalar
    {
        toCCW(subj);
        tri2 cl(clip);
        toCCW(cl);
        std::vector<Vector2D<scalar>> poly{subj.a, subj.b, subj.c};
        const Vector2D<scalar> cv[3] = {cl.a, cl.b, cl.c};
        for (label e = 0; e < 3 && !poly.empty(); ++e)
        {
            const Vector2D<scalar>& A = cv[e];
            const Vector2D<scalar> ed = cv[(e + 1) % 3] - A;
            std::vector<Vector2D<scalar>> out;
            for (label j = 0; j < label(poly.size()); ++j)
            {
                const Vector2D<scalar>& P = poly[j];
                const Vector2D<scalar>& Q = poly[(j + 1) % label(poly.size())];
                const scalar sp = cross2(ed, P - A);
                const scalar sq = cross2(ed, Q - A);
                if (sp >= -eps) { out.push_back(P); }
                if ((sp > eps && sq < -eps) || (sp < -eps && sq > eps))
                {
                    const scalar w = sp / (sp - sq);
                    out.push_back(P + w * (Q - P));
                }
            }
            poly.swap(out);
        }
        if (poly.size() < 3) { return 0; }
        scalar a = 0;
        for (label j = 0; j < label(poly.size()); ++j)
        {
            a += cross2(poly[j], poly[(j + 1) % label(poly.size())]);
        }
        return 0.5 * mag(a);
    };

    // ---- partner triangles: coplanar, other thick shell --------------------
    // A partner is any carving triangle (never an internalBaffle: a
    // zero-thickness zone sheet seals nothing) that lies in the region's
    // plane within `contactTol` — either winding, exports mix them — and
    // belongs to a DIFFERENT shell with real thickness (bbox min span
    // >= 20 mm). Two thick bodies sharing a plane overlap only where they
    // touch: solid on both sides, no mesh face can exist, none is missing.
    // The same shell is excluded: its coplanar overlaps are the duplicate
    // interface rects of welded stacked boxes, which the coverage gate's
    // unmapped-pairing band already waives, and CSG hole/partner pairs of
    // one room shell must never cancel each other here.
    boundBox qbb(bbR);
    qbb.inflate(contactTol + 1e-9);
    std::map<label, bool> thickShell;
    scalar buried = 0;
    forAll(surfaces_, k)
    {
        if (!info_[k].carves || info_[k].internalBaffle) { continue; }
        boundBox sb(surfaces_[k].bounds());
        if (!sb.overlaps(qbb)) { continue; }
        const label off = info_[k].regionOffset;
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[k]);
        const pointField& pts = ts.points();
        forAll(ts, t)
        {
            const labelledTri& tr = ts[t];
            if (off + tr.region() == globalRegion) { continue; }
            const label sh = triShell_[k][t];
            if (sh == ownShell) { continue; }
            const auto it = thickShell.find(sh);
            if (it != thickShell.end())
            {
                if (!it->second) { continue; }
            }
            else
            {
                const vector span = shellBounds_[sh].span();
                const bool thick =
                    min(span.x(), min(span.y(), span.z())) >= 0.02;
                thickShell.emplace(sh, thick);
                if (!thick) { continue; }
            }
            const point& p0 = pts[tr[0]];
            const point& p1 = pts[tr[1]];
            const point& p2 = pts[tr[2]];
            if (mag((p0 & nR) - offR) > contactTol) { continue; }
            if (mag((p1 & nR) - offR) > contactTol) { continue; }
            if (mag((p2 & nR) - offR) > contactTol) { continue; }
            const vector tn = (p1 - p0) ^ (p2 - p0);
            const scalar ta = mag(tn);
            if (ta < VSMALL) { continue; }
            if (mag((tn / ta) & nR) < 0.99) { continue; }
            tri2 q;
            q.a = proj(p0);
            q.b = proj(p1);
            q.c = proj(p2);
            for (const tri2& mt : mine)
            {
                buried += clipArea(mt, q);
            }
        }
    }
    return min(buried, mineArea);
}


bool Foam::dcCartSurfaces::pointInThinSheetShell(const point& p) const
{
    for (label sh = 0; sh < shellClosed_.size(); ++sh)
    {
        if (!shellClosed_[sh]) { continue; }
        const vector span = shellBounds_[sh].span();
        if (min(span.x(), min(span.y(), span.z())) >= 0.05) { continue; }
        boundBox bb(shellBounds_[sh]);
        bb.inflate(1e-9);
        if (bb.contains(p)) { return true; }
    }
    return false;
}


bool Foam::dcCartSurfaces::regionIsThinSheetReference(label globalRegion) const
{
    forAll(surfaces_, i)
    {
        const label off = info_[i].regionOffset;
        const triSurfaceMesh& sm = surfaces_[i];
        const triSurface& ts = static_cast<const triSurface&>(sm);
        forAll(ts, t)
        {
            if (off + ts[t].region() != globalRegion) { continue; }
            // the region's shell: any shell holding one of its triangles
            const label sh = triShell_[i][t];
            const boundBox& bb = shellBounds_[sh];
            const vector span = bb.span();
            return min(span.x(), min(span.y(), span.z())) < 0.05;
        }
    }
    return false;
}


Foam::scalar Foam::dcCartSurfaces::baffleSheetArea(label sheet) const
{
    scalar area = 0;
    const List<labelPair>& tris = sheetTris_[sheet];
    if (tris.empty()) { return 0; }
    const label i = tris[0].first();
    const triSurfaceMesh& sm = surfaces_[i];
    const triSurface& ts = static_cast<const triSurface&>(sm);
    const pointField& pts = ts.points();
    for (const labelPair& tp : tris)
    {
        if (tp.first() != i) { continue; }
        const labelledTri& tr = ts[tp.second()];
        area += mag((pts[tr[1]] - pts[tr[0]]) ^ (pts[tr[2]] - pts[tr[0]])) / 2;
    }
    return area;
}


void Foam::dcCartSurfaces::baffleSheetTrianglesInBox
(
    label sheet,
    const boundBox& bb,
    std::vector<triInfo>& out
) const
{
    out.clear();
    const List<labelPair>& tris = sheetTris_[sheet];
    if (tris.empty()) { return; }
    const label i = tris[0].first();
    const triSurfaceMesh& sm = surfaces_[i];
    const triSurface& ts = static_cast<const triSurface&>(sm);
    const auto& tree = sm.tree();
    if (tree.nodes().empty()) { return; }
    const treeBoundBox tbb(bb);
    const List<boundBox>& bbs = triBounds(i);
    const std::vector<dcCartTriSat>& sats = triSats(i);
    const pointField& pts = ts.points();
    // what findBox reports, as a sorted list (the output follows the sheet's
    // own triangle order, not the set's)
    static thread_local std::vector<label> inBox;
    inBox.clear();
    auto visit = [&](const label t) -> bool
    {
        if (triBoxOverlap(tbb, bbs[t], sats[t])) { inBox.push_back(t); }
        return false;
    };
    visitBox(tree, 0, tbb, visit);
    if (inBox.empty()) { return; }
    std::sort(inBox.begin(), inBox.end());
    inBox.erase(std::unique(inBox.begin(), inBox.end()), inBox.end());
    const vectorField& fn = ts.faceNormals();
    forAll(tris, k)
    {
        const label t = tris[k].second();
        if (!std::binary_search(inBox.begin(), inBox.end(), t)) { continue; }
        const labelledTri& f = ts[t];
        triInfo ti;
        ti.p0 = pts[f[0]]; ti.p1 = pts[f[1]]; ti.p2 = pts[f[2]];
        ti.normal = fn[t];
        ti.region = info_[i].regionOffset + f.region();
        out.push_back(ti);
    }
}


Foam::scalar Foam::dcCartSurfaces::solidShellDistance
(
    label sh,
    const point& pt,
    scalar maxDist,
    label& region
) const
{
    region = -1;
    boundBox bb(pt - maxDist * vector::one, pt + maxDist * vector::one);
    // The nearest of the shell's triangles in the box. The loop over
    // solidShellTrianglesInBox keeps the FIRST nearest in findBox's order,
    // an order that only matters for a tie: it is built only then.
    {
        const label i = shellSurface_[sh];
        const triSurfaceMesh& sm = surfaces_[i];
        const auto& tree = sm.tree();
        if (tree.nodes().empty()) { return GREAT; }
        const triSurface& ts = static_cast<const triSurface&>(sm);
        const pointField& pts = ts.points();
        const List<boundBox>& bbs = triBounds(i);
        const std::vector<dcCartTriSat>& sats = triSats(i);
        const treeBoundBox tbb(bb);
        scalar best = GREAT;
        label bestT = -1;
        bool tie = false;
        auto visit = [&](const label t) -> bool
        {
            if (triShell_[i][t] != sh) { return false; }
            if (!triBoxOverlap(tbb, bbs[t], sats[t])) { return false; }
            const labelledTri& f = ts[t];
            const scalar d2 = pointTriDist2(pt, pts[f[0]], pts[f[1]], pts[f[2]]);
            if (d2 < best) { best = d2; bestT = t; tie = false; }
            else if (d2 == best && t != bestT) { tie = true; }
            return false;
        };
        visitBox(tree, 0, tbb, visit);
        if (bestT < 0) { return GREAT; }
        if (!tie)
        {
            if (best < GREAT) { region = info_[i].regionOffset + ts[bestT].region(); }
            return (best < GREAT) ? Foam::sqrt(best) : GREAT;
        }
    }
    std::vector<triInfo> tris;
    solidShellTrianglesInBox(sh, bb, tris);
    scalar best = GREAT;
    for (const auto& t : tris)
    {
        const scalar d2 = pointTriDist2(pt, t.p0, t.p1, t.p2);
        if (d2 < best) { best = d2; region = t.region; }
    }
    return (best < GREAT) ? Foam::sqrt(best) : GREAT;
}


// * * * * * * * * * * * * * * Member Functions  * * * * * * * * * * * * * * //

Foam::boundBox Foam::dcCartSurfaces::bounds() const
{
    boundBox bb(boundBox::invertedBox);
    forAll(surfaces_, i)
    {
        if (!info_[i].carves) { continue; }
        bb.add(surfaces_[i].bounds());
    }
    return bb;
}


bool Foam::dcCartSurfaces::segmentMayHit(label i, const point& p0, const point& p1) const
{
    // indexedOctree::findLine returns no hit when both ends lie beyond one
    // face of the tree's box (posBits share a bit): skip the call then
    const auto& tree = surfaces_[i].tree();
    if (tree.nodes().empty()) { return false; }
    const treeBoundBox& tb = tree.nodes()[0].bb_;
    for (direction d = 0; d < 3; ++d)
    {
        if (p0[d] < tb.min()[d] && p1[d] < tb.min()[d]) { return false; }
        if (p0[d] > tb.max()[d] && p1[d] > tb.max()[d]) { return false; }
    }
    return true;
}


const Foam::List<Foam::boundBox>& Foam::dcCartSurfaces::grownBounds(label i) const
{
    if (triBbGrown_.size() != surfaces_.size()) { triBbGrown_.setSize(surfaces_.size()); }
    List<boundBox>& g = triBbGrown_[i];
    const List<boundBox>& bbs = triBounds(i);
    if (g.size() != bbs.size())
    {
        g.setSize(bbs.size());
        forAll(bbs, t)
        {
            // the Moller-Trumbore test accepts barycentrics within 10*SMALL
            // (relative) of the triangle; a millionth of its size is plenty
            const vector grow = vector::uniform(1e-6 * mag(bbs[t].span()) + 1e-9);
            g[t] = boundBox(bbs[t].min() - grow, bbs[t].max() + grow);
        }
    }
    return g;
}


Foam::pointIndexHit Foam::dcCartSurfaces::findLineFast(label i, const point& p0, const point& p1) const
{
    const triSurfaceMesh& sm = surfaces_[i];
    const auto& tree = sm.tree();
    const scalar oldTol = indexedOctree<treeDataTriSurface>::perturbTol
    (
        static_cast<const triSurfaceSearch&>(sm).tolerance()
    );
    const boxedIntersectOp op{tree, grownBounds(i), nullptr};
    const pointIndexHit h = tree.findLine(p0, p1, op);
    indexedOctree<treeDataTriSurface>::perturbTol(oldTol);
    return h;
}


const std::vector<Foam::dcCartTriSat>& Foam::dcCartSurfaces::triSats(label i) const
{
    if (triSat_.size() != surfaces_.size()) { triSat_.setSize(surfaces_.size()); }
    std::vector<dcCartTriSat>& v = triSat_[i];
    const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
    if (label(v.size()) != ts.size())
    {
        v.assign(ts.size(), dcCartTriSat());
        const pointField& pts = ts.points();
        forAll(ts, t)
        {
            dcCartTriSat& s = v[t];
            const labelledTri& f = ts[t];
            s.a = pts[f[0]]; s.b = pts[f[1]]; s.c = pts[f[2]];
            const point& a = s.a; const point& b = s.b; const point& c = s.c;
            // triangle::areaNormal, then axis & a (inner: x, y, z in order)
            const vector e1(b.x() - a.x(), b.y() - a.y(), b.z() - a.z());
            const vector e2(c.x() - a.x(), c.y() - a.y(), c.z() - a.z());
            const vector cr
            (
                (e1.y()*e2.z() - e1.z()*e2.y()),
                (e1.z()*e2.x() - e1.x()*e2.z()),
                (e1.x()*e2.y() - e1.y()*e2.x())
            );
            s.n = vector(0.5*cr.x(), 0.5*cr.y(), 0.5*cr.z());
            s.tn = (s.n.x()*a.x() + s.n.y()*a.y() + s.n.z()*a.z());
            const vector edges[3] =
            {
                vector(c.x() - b.x(), c.y() - b.y(), c.z() - b.z()),
                vector(a.x() - c.x(), a.y() - c.y(), a.z() - c.z()),
                vector(b.x() - a.x(), b.y() - a.y(), b.z() - a.z())
            };
            label k = 0;
            for (const vector& e : edges)
            {
                for (int faceDir = 0; faceDir < 3; ++faceDir, ++k)
                {
                    vector u(0, 0, 0);
                    u[faceDir] = 1;
                    const vector axis
                    (
                        (e.y()*u.z() - e.z()*u.y()),
                        (e.z()*u.x() - e.x()*u.z()),
                        (e.x()*u.y() - e.y()*u.x())
                    );
                    s.axis[k] = axis;
                    scalar lo = (axis.x()*a.x() + axis.y()*a.y() + axis.z()*a.z());
                    scalar hi = lo;
                    const scalar vb = (axis.x()*b.x() + axis.y()*b.y() + axis.z()*b.z());
                    lo = (lo < vb) ? lo : vb; hi = (vb < hi) ? hi : vb;
                    const scalar vc = (axis.x()*c.x() + axis.y()*c.y() + axis.z()*c.z());
                    lo = (lo < vc) ? lo : vc; hi = (vc < hi) ? hi : vc;
                    s.lo[k] = lo;
                    s.hi[k] = hi;
                }
            }
        }
    }
    return v;
}


const Foam::List<Foam::boundBox>& Foam::dcCartSurfaces::triBounds(label i) const
{
    if (triBb_.size() != surfaces_.size()) { triBb_.setSize(surfaces_.size()); }
    List<boundBox>& bbs = triBb_[i];
    const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
    if (bbs.size() != ts.size())
    {
        bbs.setSize(ts.size());
        const pointField& pts = ts.points();
        forAll(ts, t)
        {
            const labelledTri& f = ts[t];
            boundBox bb(pts[f[0]], pts[f[0]]);
            bb.add(pts[f[1]]);
            bb.add(pts[f[2]]);
            bbs[t] = bb;
        }
    }
    return bbs;
}


bool Foam::dcCartSurfaces::anyOverlapCached(label i, const treeBoundBox& bb) const
{
    const auto& tree = surfaces_[i].tree();
    if (tree.nodes().empty()) { return false; }
    const List<boundBox>& bbs = triBounds(i);
    const std::vector<dcCartTriSat>& sats = triSats(i);
    auto visit = [&](const label t) -> bool { return triBoxOverlap(bb, bbs[t], sats[t]); };
    return visitBox(tree, 0, bb, visit);
}


void Foam::dcCartSurfaces::collectOverlapCached
(
    label i,
    const treeBoundBox& bb,
    std::vector<uint64_t>& out
) const
{
    const auto& tree = surfaces_[i].tree();
    if (tree.nodes().empty()) { return; }
    const List<boundBox>& bbs = triBounds(i);
    const std::vector<dcCartTriSat>& sats = triSats(i);
    const size_t k0 = out.size();
    const uint64_t tag = uint64_t(i) << 32;
    auto visit = [&](const label t) -> bool
    {
        if (triBoxOverlap(bb, bbs[t], sats[t])) { out.push_back(tag | uint32_t(t)); }
        return false;
    };
    visitBox(tree, 0, bb, visit);
    std::sort(out.begin() + k0, out.end());
    out.erase(std::unique(out.begin() + k0, out.end()), out.end());
}


void Foam::dcCartSurfaces::filterRun
(
    label i,
    const treeBoundBox& bb,
    const uint64_t* p,
    const uint64_t* q,
    std::vector<uint64_t>& out
) const
{
    if (q - p == 1 && uint32_t(*p) == allTriangles)
    {
        collectOverlapCached(i, bb, out);
        return;
    }
    const List<boundBox>& bbs = triBounds(i);
    const std::vector<dcCartTriSat>& sats = triSats(i);
    for (const uint64_t* e = p; e != q; ++e)
    {
        const label t = label(uint32_t(*e));
        if (triBoxOverlap(bb, bbs[t], sats[t])) { out.push_back(*e); }
    }
}


std::vector<uint64_t> Foam::dcCartSurfaces::refinementCandidates() const
{
    std::vector<uint64_t> c;
    forAll(surfaces_, i) { c.push_back((uint64_t(i) << 32) | allTriangles); }
    return c;
}


std::vector<uint64_t> Foam::dcCartSurfaces::proximityCandidates() const
{
    std::vector<uint64_t> c;
    forAll(surfaces_, i)
    {
        const surfaceInfo& si = info_[i];
        if (si.carves || (si.internalBaffle && !si.faceZone.empty()))
        {
            c.push_back((uint64_t(i) << 32) | allTriangles);
        }
    }
    return c;
}


uint64_t Foam::dcCartSurfaces::carvingBits() const
{
    uint64_t b = 0;
    forAll(surfaces_, i)
    {
        if (info_[i].carves) { b |= (i < 64) ? (uint64_t(1) << i) : ~uint64_t(0); }
    }
    return b;
}


bool Foam::dcCartSurfaces::levelInBoxAboveCand
(
    const boundBox& bb,
    label above,
    const uint64_t* cb,
    const uint64_t* ce,
    std::vector<uint64_t>& keep
) const
{
    // Surface by surface, decided at the first answer above `above` (it
    // cannot come down again). A surface with no candidate run is one whose
    // octree query would have found nothing: no change of state.
    keep.clear();
    const treeBoundBox tbb(bb);
    label level = -1;
    bool decided = false;
    const uint64_t* p = cb;
    while (p != ce)
    {
        const label i = label(*p >> 32);
        const uint64_t* q = p;
        while (q != ce && label(*q >> 32) == i) { ++q; }
        const surfaceInfo& si = info_[i];
        if (decided || si.minLevel <= level)
        {
            keep.insert(keep.end(), p, q);
            p = q;
            continue;
        }
        const size_t k0 = keep.size();
        filterRun(i, tbb, p, q, keep);
        p = q;
        if (keep.size() == k0) { continue; }
        level = max(level, si.minLevel);
        if (level > above) { decided = true; continue; }
        // snappy's resolveFeatureAngle: a cell holding an EDGE of the
        // surface (two triangles whose normals differ by more than 30
        // degrees) is refined to the surface's max level. For the cut
        // cells this is what keeps the fold-over band at a box edge —
        // one cap face straddling two patches — one fine cell wide.
        if (si.maxLevel > level)
        {
            featureScan scan(static_cast<const triSurface&>(surfaces_[i]).faceNormals());
            bool feature = false;
            for (size_t k = k0; k < keep.size() && !feature; ++k)
            {
                feature = scan.add(label(uint32_t(keep[k])));
            }
            if (feature)
            {
                level = max(level, si.maxLevel);
                if (level > above) { decided = true; }
            }
        }
    }
    return decided || level > above;
}


std::vector<unsigned char> Foam::dcCartSurfaces::curvatureReliefSurfaces
(
    const std::function<void()>& checkpoint
) const
{
    std::vector<unsigned char> selected(info_.size(), 0), seen(info_.size(), 0);
    forAll(info_, i)
    {
        checkpoint();
        selected[i] = info_[i].carves && !info_[i].internalBaffle;
    }
    forAll(shellSurface_, sh)
    {
        checkpoint();
        const label i = shellSurface_[sh];
        if (i < 0 || i >= info_.size())
        {
            FatalErrorInFunction << "Invalid curvature refinement surface index"
                << exit(FatalError);
        }
        seen[i] = 1;
        if (!shellClosed_[sh]) { selected[i] = 0; }
    }
    forAll(info_, i) { selected[i] = selected[i] && seen[i]; }
    return selected;
}


bool Foam::dcCartSurfaces::curvatureReliefInBoxAboveCand
(
    const boundBox& bb,
    label above,
    label globalMaximum,
    const std::vector<unsigned char>& selected,
    const uint64_t* cb,
    const uint64_t* ce,
    std::vector<uint64_t>& keep,
    const std::function<void()>& checkpoint
) const
{
    if (above >= globalMaximum) { return false; }
    if (selected.size() != size_t(info_.size()))
    {
        FatalErrorInFunction << "Invalid curvature refinement policy size"
            << exit(FatalError);
    }
    const treeBoundBox tbb(bb);
    std::vector<uint64_t> narrowed;
    bool requested = false;
    const uint64_t* p = cb;
    while (p != ce)
    {
        checkpoint();
        const label i = label(*p >> 32);
        const uint64_t* q = p;
        while (q != ce && label(*q >> 32) == i) { ++q; }
        if (i < 0 || i >= info_.size())
        {
            FatalErrorInFunction << "Invalid curvature refinement candidate index"
                << exit(FatalError);
        }
        const surfaceInfo& si = info_[i];
        // Clamp before adding one: no overflow and no change to global maxLevel.
        const label declared = min(si.maxLevel, globalMaximum);
        const label target = declared < globalMaximum ? declared + 1 : declared;
        if (requested || !selected[i] || target <= above)
        {
            narrowed.insert(narrowed.end(), p, q);
            p = q;
            continue;
        }
        const size_t k0 = narrowed.size();
        // filterRun performs the existing finite triangle/box SAT test.
        // Query work must complete inside the same APP budget before publish.
        filterRun(i, tbb, p, q, narrowed);
        checkpoint();
        p = q;
        const vectorField& fn =
            static_cast<const triSurface&>(surfaces_[i]).faceNormals();
        featureScan scan(fn);
        for (size_t k = k0; k < narrowed.size() && !requested; ++k)
        {
            checkpoint();
            const label t = label(uint32_t(narrowed[k]));
            const vector& n = fn[t];
            if (!std::isfinite(n.x()) || !std::isfinite(n.y()) || !std::isfinite(n.z()))
            {
                FatalErrorInFunction << "Non-finite curvature refinement normal"
                    << exit(FatalError);
            }
            // Empty and finite degenerate normals retain featureScan semantics.
            requested = scan.add(t);
        }
    }
    checkpoint();
    if (requested) { keep.swap(narrowed); }
    return requested;
}


uint64_t Foam::dcCartSurfaces::overlapMaskCand
(
    const treeBoundBox& bb,
    const uint64_t* cb,
    const uint64_t* ce
) const
{
    uint64_t mask = 0;
    const uint64_t* p = cb;
    while (p != ce)
    {
        const label i = label(*p >> 32);
        const uint64_t* q = p;
        while (q != ce && label(*q >> 32) == i) { ++q; }
        bool hit = false;
        if (q - p == 1 && uint32_t(*p) == allTriangles)
        {
            hit = anyOverlapCached(i, bb);
        }
        else
        {
            const List<boundBox>& bbs = triBounds(i);
            const std::vector<dcCartTriSat>& sats = triSats(i);
            for (const uint64_t* e = p; e != q && !hit; ++e)
            {
                const label t = label(uint32_t(*e));
                hit = triBoxOverlap(bb, bbs[t], sats[t]);
            }
        }
        if (hit) { mask |= (i < 64) ? (uint64_t(1) << i) : ~uint64_t(0); }
        p = q;
    }
    return mask;
}


void Foam::dcCartSurfaces::filterCand
(
    const treeBoundBox& bb,
    const uint64_t* cb,
    const uint64_t* ce,
    std::vector<uint64_t>& out
) const
{
    const uint64_t* p = cb;
    while (p != ce)
    {
        const label i = label(*p >> 32);
        const uint64_t* q = p;
        while (q != ce && label(*q >> 32) == i) { ++q; }
        filterRun(i, bb, p, q, out);
        p = q;
    }
}


void Foam::dcCartSurfaces::findBoxToc
(
    label i,
    const treeBoundBox& bb,
    std::vector<label>& out
) const
{
    out.clear();
    const auto& tree = surfaces_[i].tree();
    if (tree.nodes().empty()) { return; }
    const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
    const List<boundBox>& bbs = triBounds(i);
    const std::vector<dcCartTriSat>& sats = triSats(i);

    // labelHashSet as findBox fills it: reserve(max(128, n/100)) doubles the
    // count into its capacity, keys hash to themselves, a new key goes to the
    // head of its bucket, and past 0.8 load the table doubles, relinking
    // bucket by bucket onto the new heads. toc() walks buckets, then chains.
    label cap = HashTableCore::canonicalSize(2*max(label(128), label(ts.size())/100));
    static thread_local std::vector<label> heads, next, keys;
    heads.assign(cap, -1);
    next.clear();
    keys.clear();
    label size = 0;
    auto visit = [&](const label t) -> bool
    {
        if (!triBoxOverlap(bb, bbs[t], sats[t])) { return false; }
        const label idx = label(unsigned(t) & unsigned(cap - 1));
        for (label n = heads[idx]; n >= 0; n = next[n])
        {
            if (keys[n] == t) { return false; }
        }
        keys.push_back(t);
        next.push_back(heads[idx]);
        heads[idx] = label(keys.size()) - 1;
        ++size;
        if (0.8*cap < size)
        {
            const label newCap = HashTableCore::canonicalSize(2*cap);
            std::vector<label> newHeads(newCap, -1);
            for (label b = 0; b < cap; ++b)
            {
                for (label n = heads[b]; n >= 0; )
                {
                    const label nx = next[n];
                    const label ni = label(unsigned(keys[n]) & unsigned(newCap - 1));
                    next[n] = newHeads[ni];
                    newHeads[ni] = n;
                    n = nx;
                }
            }
            heads.swap(newHeads);
            cap = newCap;
        }
        return false;
    };
    visitBox(tree, 0, bb, visit);
    out.reserve(size);
    for (label b = 0; b < cap; ++b)
    {
        for (label n = heads[b]; n >= 0; n = next[n]) { out.push_back(keys[n]); }
    }
}


const std::vector<Foam::label>& Foam::dcCartSurfaces::sheetMembers(label sheet) const
{
    if (sheetMembers_.size() != sheetTris_.size()) { sheetMembers_.setSize(sheetTris_.size()); }
    std::vector<label>& mem = sheetMembers_[sheet];
    const List<labelPair>& tris = sheetTris_[sheet];
    if (mem.empty() && !tris.empty())
    {
        forAll(tris, k) { mem.push_back(tris[k].second()); }
        std::sort(mem.begin(), mem.end());
        mem.erase(std::unique(mem.begin(), mem.end()), mem.end());
    }
    return mem;
}


void Foam::dcCartSurfaces::buildShellGrid() const
{
    shellGridBuilt_ = true;
    shellGridCells_.clear();
    shellGridBb_ = boundBox(boundBox::invertedBox);
    forAll(shells_, i) { if (refineShellBounds_[i].valid()) { shellGridBb_.add(refineShellBounds_[i]); } }
    if (!shellGridBb_.valid()) { return; }
    const vector span = shellGridBb_.span();
    scalar vol = 1;
    for (direction d = 0; d < 3; ++d) { vol *= max(span[d], scalar(1e-6)); }
    shellGridH_ = max(std::cbrt(vol / max(scalar(8*shells_.size()), scalar(1))), scalar(1e-6));
    label total = 1;
    for (direction d = 0; d < 3; ++d)
    {
        shellGridN_[d] = min(max(label(1), label(std::ceil(span[d] / shellGridH_))), label(256));
        total *= shellGridN_[d];
    }
    shellGridCells_.resize(total);
    auto bucket = [&](scalar x, direction d) -> label
    {
        const label b = label(std::floor((x - shellGridBb_.min()[d]) / shellGridH_));
        return min(max(b, label(0)), shellGridN_[d] - 1);
    };
    forAll(shells_, i)
    {
        const boundBox& sb = refineShellBounds_[i];
        if (!sb.valid()) { continue; }
        for (label k = bucket(sb.min().z(), 2); k <= bucket(sb.max().z(), 2); ++k)
        {
            for (label j = bucket(sb.min().y(), 1); j <= bucket(sb.max().y(), 1); ++j)
            {
                for (label ii = bucket(sb.min().x(), 0); ii <= bucket(sb.max().x(), 0); ++ii)
                {
                    shellGridCells_[ii + shellGridN_[0]*(j + shellGridN_[1]*k)].push_back(i);
                }
            }
        }
    }
}


bool Foam::dcCartSurfaces::shellLevelAbove(const point& pt, label above) const
{
    // Only the shells whose bounds' buckets hold the point can contain it
    // (an invalid bound contains nothing); any order answers "any inside".
    if (!shellGridBuilt_) { buildShellGrid(); }
    if (shellGridCells_.empty() || !shellGridBb_.contains(pt)) { return false; }
    label b[3];
    for (direction d = 0; d < 3; ++d)
    {
        b[d] = min(max(label(std::floor((pt[d] - shellGridBb_.min()[d]) / shellGridH_)), label(0)), shellGridN_[d] - 1);
    }
    const std::vector<label>& cand = shellGridCells_[b[0] + shellGridN_[0]*(b[1] + shellGridN_[1]*b[2])];
    for (const label i : cand)
    {
        if (shellLevel_[i] <= above || !refineShellBounds_[i].contains(pt)) { continue; }
        const pointField query(1, pt);
        List<volumeType> vt;
        shells_[i].getVolumeType(query, vt);
        if (!vt.empty() && vt[0] == volumeType::INSIDE) { return true; }
    }
    return false;
}


Foam::label Foam::dcCartSurfaces::blockingRegionMasked
(
    const point& p0,
    const point& p1,
    uint64_t mask
) const
{
    const bool useMask = (surfaces_.size() <= 64);
    // Nearest hit wins: with two surfaces between the cell centres the face
    // belongs to the one actually adjacent to p0's cell.
    scalar bestDistSqr = GREAT;
    label bestRegion = -1;

    forAll(surfaces_, i)
    {
        if (!info_[i].carves) { continue; }
        if (useMask && !((mask >> i) & 1u)) { continue; }
        if (!segmentMayHit(i, p0, p1)) { continue; }

        const pointIndexHit h = findLineFast(i, p0, p1);
        if (!h.hit()) { continue; }

        const scalar d2 = magSqr(h.point() - p0);
        if (d2 >= bestDistSqr) { continue; }

        const label local = static_cast<const triSurface&>(surfaces_[i])[h.index()].region();
        if (local < 0 || local >= info_[i].nRegions) { continue; }

        bestDistSqr = d2;
        bestRegion = info_[i].regionOffset + local;
    }

    return bestRegion;
}


Foam::label Foam::dcCartSurfaces::crossedFaceZoneMasked
(
    const point& p0,
    const point& p1,
    uint64_t mask
) const
{
    const bool useMask = (surfaces_.size() <= 64);
    forAll(surfaces_, i)
    {
        const surfaceInfo& si = info_[i];
        if (!si.internalBaffle || si.faceZone.empty()) { continue; }
        if (useMask && !((mask >> i) & 1u)) { continue; }
        if (!segmentMayHit(i, p0, p1)) { continue; }

        if (!findLineFast(i, p0, p1).hit()) { continue; }

        forAll(faceZoneNames_, z)
        {
            if (faceZoneNames_[z] == si.faceZone) { return z; }
        }
    }
    return -1;
}


bool Foam::dcCartSurfaces::isInside(label surfI, const point& pt) const
{
    if (surfI < 0 || surfI >= surfaces_.size()) { return false; }

    List<volumeType> vt;
    surfaces_[surfI].getVolumeType(pointField(1, pt), vt);
    return !vt.empty() && vt[0] == volumeType::INSIDE;
}


// * * * * * * * * * * * * * Cut-cell queries  * * * * * * * * * * * * * * * //

bool Foam::dcCartSurfaces::carvingHit
(
    const point& p0,
    const point& p1,
    point& hit,
    label& region
) const
{
    scalar bestDistSqr = GREAT;
    region = -1;
    forAll(surfaces_, i)
    {
        if (!info_[i].carves) { continue; }
        if (!segmentMayHit(i, p0, p1)) { continue; }
        const pointIndexHit h = findLineFast(i, p0, p1);
        if (!h.hit()) { continue; }
        const scalar d2 = magSqr(h.point() - p0);
        if (d2 >= bestDistSqr) { continue; }
        const label local = static_cast<const triSurface&>(surfaces_[i])[h.index()].region();
        if (local < 0 || local >= info_[i].nRegions) { continue; }
        bestDistSqr = d2;
        hit = h.point();
        region = info_[i].regionOffset + local;
    }
    return region >= 0;
}


Foam::scalar Foam::dcCartSurfaces::nearestCarvingDistance
(
    const point& pt,
    scalar maxDist
) const
{
    scalar best = GREAT;
    forAll(surfaces_, i)
    {
        if (!info_[i].carves) { continue; }
        List<pointIndexHit> hits;
        surfaces_[i].findNearest
        (
            pointField(1, pt), scalarField(1, maxDist * maxDist), hits
        );
        if (!hits[0].hit()) { continue; }
        best = min(best, mag(hits[0].point() - pt));
    }
    return best;
}


void Foam::dcCartSurfaces::carvingTrianglesInBox
(
    const boundBox& bb,
    std::vector<triInfo>& out
) const
{
    out.clear();
    forAll(surfaces_, i)
    {
        if (!info_[i].carves) { continue; }
        const triSurfaceMesh& sm = surfaces_[i];
        const triSurface& s = static_cast<const triSurface&>(sm);
        static thread_local std::vector<label> found;
        findBoxToc(i, treeBoundBox(bb), found);
        const pointField& pts = s.points();
        const vectorField& fn = s.faceNormals();
        for (const label t : found)
        {
            const labelledTri& f = s[t];
            const label local = f.region();
            if (local < 0 || local >= info_[i].nRegions) { continue; }
            triInfo ti;
            ti.p0 = pts[f[0]]; ti.p1 = pts[f[1]]; ti.p2 = pts[f[2]];
            ti.normal = fn[t];
            ti.region = info_[i].regionOffset + local;
            out.push_back(ti);
        }
    }
}


Foam::label Foam::dcCartSurfaces::nearestCarvingRegion(const point& pt) const
{
    scalar bestDistSqr = GREAT;
    label region = -1;
    forAll(surfaces_, i)
    {
        if (!info_[i].carves) { continue; }
        // A later surface only counts when nearer than the best so far, so its
        // search needs no wider sphere. The octree compares sqr(distance),
        // this loop magSqr: the sphere is a hair wider than the best so that
        // no triangle this loop would take is left out.
        const scalar searchSqr = (bestDistSqr < GREAT) ? bestDistSqr*(1 + 1e-9) : GREAT;
        List<pointIndexHit> hits;
        surfaces_[i].findNearest
        (
            pointField(1, pt), scalarField(1, searchSqr), hits
        );
        if (!hits[0].hit()) { continue; }
        const scalar d2 = magSqr(hits[0].point() - pt);
        if (d2 >= bestDistSqr) { continue; }
        labelList reg;
        surfaces_[i].getRegion(hits, reg);
        const label local = reg.empty() ? 0 : reg[0];
        if (local < 0 || local >= info_[i].nRegions) { continue; }
        bestDistSqr = d2;
        region = info_[i].regionOffset + local;
    }
    return region;
}


Foam::label Foam::dcCartSurfaces::nearestCarvingRegionProbe(const point& pt, scalar probe) const
{
    const scalar probeSqr = probe*probe;
    scalar bestDistSqr = GREAT;
    label region = -1;
    forAll(surfaces_, i)
    {
        if (!info_[i].carves) { continue; }
        // A later surface only counts when nearer than the best so far, so its
        // search needs no wider sphere. The octree compares sqr(distance),
        // this loop magSqr: the sphere is a hair wider than the best so that
        // no triangle this loop would take is left out.
        const scalar searchSqr = min(bestDistSqr, probeSqr)*(1 + 1e-9);
        List<pointIndexHit> hits;
        surfaces_[i].findNearest
        (
            pointField(1, pt), scalarField(1, searchSqr), hits
        );
        if (!hits[0].hit()) { continue; }
        const scalar d2 = magSqr(hits[0].point() - pt);
        if (d2 >= bestDistSqr) { continue; }
        labelList reg;
        surfaces_[i].getRegion(hits, reg);
        const label local = reg.empty() ? 0 : reg[0];
        if (local < 0 || local >= info_[i].nRegions) { continue; }
        bestDistSqr = d2;
        region = info_[i].regionOffset + local;
    }
    // found inside the probe: whatever lies outside it is farther than the
    // winner and cannot win or tie
    if (region >= 0 && bestDistSqr < probeSqr) { return region; }
    return nearestCarvingRegion(pt);
}


Foam::label Foam::dcCartSurfaces::coplanarCarvingRegion
(
    const point& pt, const vector& n, scalar radius, label current
) const
{
    const scalar nm = mag(n);
    if (nm < VSMALL) { return -1; }
    const vector nu = n / nm;
    // A face is named by the facet UNDER it: of the surfaces with a facet
    // parallel to the face (2.6 degrees) within `radius`, the nearest. The
    // current region keeps the face when its facet is as near as any, so a
    // cap named by its crossings stays as it is. A facet merely beside the
    // face in its plane does not keep it: a re-admitted leaf on the floor
    // beside a rotated rack's foot took the frame's name for its floor face
    // (the foot is at y = 0 too, 6 mm away), and "any frame facet within
    // `radius`" let the frame keep that name — Tri_angle 2026-09-23, 13
    // floor faces labelled Default_Rack_1_frame, 20 mm outside the frame.
    const scalar tieTol = 1e-6;
    scalar bestDist = GREAT, curDist = GREAT;
    label region = -1;
    forAll(surfaces_, i)
    {
        if (!info_[i].carves) { continue; }
        const triSurfaceMesh& sm = surfaces_[i];
        const triSurface& ts = static_cast<const triSurface&>(sm);
        List<pointIndexHit> hits;
        sm.findNearest(pointField(1, pt), scalarField(1, radius * radius), hits);
        if (!hits[0].hit()) { continue; }
        const label t = hits[0].index();
        if (t < 0 || t >= ts.size()) { continue; }
        if (std::abs(ts.faceNormals()[t] & nu) <= 0.999) { continue; }
        const label local = ts[t].region();
        if (local < 0 || local >= info_[i].nRegions) { continue; }
        const label reg = info_[i].regionOffset + local;
        const scalar d = mag(hits[0].point() - pt);
        if (reg == current) { curDist = min(curDist, d); }
        if (d < bestDist - tieTol) { bestDist = d; region = reg; }
    }
    if (current >= 0 && curDist <= bestDist + tieTol) { return current; }
    return region;
}


bool Foam::dcCartSurfaces::baffleHit
(
    label zone,
    const point& p0,
    const point& p1,
    point& hit
) const
{
    if (zone < 0 || zone >= faceZoneNames_.size()) { return false; }
    const word& zoneName = faceZoneNames_[zone];
    scalar bestDistSqr = GREAT;
    bool found = false;
    forAll(surfaces_, i)
    {
        const surfaceInfo& si = info_[i];
        if (!si.internalBaffle || si.faceZone != zoneName) { continue; }
        List<pointIndexHit> hits;
        surfaces_[i].findLine(pointField(1, p0), pointField(1, p1), hits);
        if (!hits[0].hit()) { continue; }
        const scalar d2 = magSqr(hits[0].point() - p0);
        if (d2 >= bestDistSqr) { continue; }
        bestDistSqr = d2;
        hit = hits[0].point();
        found = true;
    }
    return found;
}


bool Foam::dcCartSurfaces::baffleNearestPlane
(
    label zone,
    const point& pt,
    point& origin,
    vector& normal
) const
{
    if (zone < 0 || zone >= faceZoneNames_.size()) { return false; }
    const word& zoneName = faceZoneNames_[zone];
    scalar bestDistSqr = GREAT;
    bool found = false;
    forAll(surfaces_, i)
    {
        const surfaceInfo& si = info_[i];
        if (!si.internalBaffle || si.faceZone != zoneName) { continue; }
        List<pointIndexHit> hits;
        surfaces_[i].findNearest(pointField(1, pt), scalarField(1, GREAT), hits);
        if (!hits[0].hit()) { continue; }
        const scalar d2 = magSqr(hits[0].point() - pt);
        if (d2 >= bestDistSqr) { continue; }
        vectorField normals;
        surfaces_[i].getNormal(hits, normals);
        if (normals.empty()) { continue; }
        bestDistSqr = d2;
        origin = hits[0].point();
        normal = normals[0];
        found = true;
    }
    if (found)
    {
        const scalar m = mag(normal);
        if (m > VSMALL) { normal /= m; }
    }
    return found;
}


bool Foam::dcCartSurfaces::carvingNearestPlane
(
    const point& pt,
    point& origin,
    vector& normal
) const
{
    scalar bestDistSqr = GREAT;
    bool found = false;
    forAll(surfaces_, i)
    {
        if (!info_[i].carves) { continue; }
        List<pointIndexHit> hits;
        surfaces_[i].findNearest(pointField(1, pt), scalarField(1, GREAT), hits);
        if (!hits[0].hit()) { continue; }
        const scalar d2 = magSqr(hits[0].point() - pt);
        if (d2 >= bestDistSqr) { continue; }
        vectorField normals;
        surfaces_[i].getNormal(hits, normals);
        if (normals.empty()) { continue; }
        bestDistSqr = d2;
        origin = hits[0].point();
        normal = normals[0];
        found = true;
    }
    if (found)
    {
        const scalar m = mag(normal);
        if (m > VSMALL) { normal /= m; }
    }
    return found;
}


Foam::label Foam::dcCartSurfaces::baffleSide
(
    label zone,
    const point& pt,
    scalar tolOn
) const
{
    point origin;
    vector normal;
    if (!baffleNearestPlane(zone, pt, origin, normal)) { return -2; }
    const scalar d = (pt - origin) & normal;
    if (mag(d) <= tolOn) { return 0; }
    return d > 0 ? 1 : -1;
}


bool Foam::dcCartSurfaces::baffleOverlaps(label zone, const boundBox& bb) const
{
    if (zone < 0 || zone >= faceZoneNames_.size()) { return false; }
    const word& zoneName = faceZoneNames_[zone];
    forAll(surfaces_, i)
    {
        const surfaceInfo& si = info_[i];
        if (!si.internalBaffle || si.faceZone != zoneName) { continue; }
        if (anyOverlapCached(i, treeBoundBox(bb))) { return true; }
    }
    return false;
}


bool Foam::dcCartSurfaces::tiltedOverlaps(const boundBox& bb) const
{
    const treeBoundBox tb(bb);
    forAll(surfaces_, i)
    {
        if (!surfaces_[i].bounds().overlaps(bb)) { continue; }
        const auto& tree = surfaces_[i].tree();
        if (tree.nodes().empty()) { continue; }
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
        const pointField& pts = ts.points();
        const List<boundBox>& bbs = triBounds(i);
        const std::vector<dcCartTriSat>& sats = triSats(i);
        auto visit = [&](const label t) -> bool
        {
            if (!triBoxOverlap(tb, bbs[t], sats[t])) { return false; }
            const labelledTri& f = ts[t];
            const vector n = (pts[f[1]] - pts[f[0]]) ^ (pts[f[2]] - pts[f[0]]);
            const scalar nm = mag(n);
            return nm > VSMALL && cmptMax(cmptMag(n)) < (1 - 1e-9) * nm;
        };
        if (visitBox(tree, 0, tb, visit)) { return true; }
    }
    return false;
}


bool Foam::dcCartSurfaces::surfaceOverlaps(const boundBox& bb, bool bafflesOnly, bool axisAlignedOnly) const
{
    // axisAlignedOnly: true only when what overlaps bb is axis-aligned
    // facets alone; one tilted facet anywhere in the box (of any surface)
    // makes it false
    bool axisHit = false;
    forAll(surfaces_, i)
    {
        if (!surfaces_[i].bounds().overlaps(bb)) { continue; }
        if (!axisAlignedOnly)
        {
            if (bafflesOnly && !info_[i].internalBaffle) { continue; }
            if (anyOverlapCached(i, treeBoundBox(bb))) { return true; }
            continue;
        }
        const auto& tree = surfaces_[i].tree();
        if (tree.nodes().empty()) { continue; }
        const triSurface& ts = static_cast<const triSurface&>(surfaces_[i]);
        const pointField& pts = ts.points();
        const List<boundBox>& bbs = triBounds(i);
        const std::vector<dcCartTriSat>& sats = triSats(i);
        const treeBoundBox tb(bb);
        const bool counts = !bafflesOnly || info_[i].internalBaffle;
        bool tilted = false;
        auto visit = [&](const label t) -> bool
        {
            if (!triBoxOverlap(tb, bbs[t], sats[t])) { return false; }
            const labelledTri& f = ts[t];
            const vector n = (pts[f[1]] - pts[f[0]]) ^ (pts[f[2]] - pts[f[0]]);
            const scalar nm = mag(n);
            if (nm <= VSMALL) { return false; }
            if (cmptMax(cmptMag(n)) >= (1 - 1e-9) * nm) { axisHit = axisHit || counts; return false; }
            tilted = true;
            return true;
        };
        visitBox(tree, 0, tb, visit);
        if (tilted) { return false; }
    }
    return axisHit;
}


Foam::string Foam::dcCartSurfaces::summary() const
{
    OStringStream os;
    label carving = 0;
    forAll(info_, i) { if (info_[i].carves) { ++carving; } }
    label baffles = 0;
    forAll(info_, i) { if (info_[i].internalBaffle) { ++baffles; } }
    os  << surfaces_.size() << " surfaces (" << carving << " carving, "
        << baffles << " baffle), "
        << shells_.size() << " refinement shells, "
        << faceZoneNames_.size() << " faceZones, "
        << patchNames_.size() << " patch regions, maxLevel " << maxLevel_;
    return os.str();
}



Foam::label Foam::dcCartSurfaces::finalizePreparedServerComponents()
{
    // Explicit physical preparation, separate from passive export identity read.
    // Fixed caps/order only: no per-rank clock, GPU availability or new MPI.
    namespace S=dcCartServerComponentCoverage;
    if(serverComponentsFinalized_)return serverComponentGroups_;
    if(sourceIdentities_.empty())return 0;
    constexpr std::size_t maxScanned=65536,maxGroups=512,maxTotalOperations=4194304;
    if(shellTris_.size()!=shellSurface_.size()||shellBounds_.size()!=shellTris_.size()
     ||shellClosed_.size()!=shellTris_.size()||triShell_.size()!=surfaces_.size()||!shellGeometryEpoch_
     ||shellMembershipEpoch_!=shellGeometryEpoch_)return 0;
    try
    {
        using Key=std::pair<label,std::string>;
        std::map<Key,std::vector<label>> candidates;
        std::set<Key> blocked;
        std::size_t scanned=0,operations=0;
        forAll(shellTris_,sh)
        {
            const label surface=shellSurface_[sh];
            if(surface<0||surface>=surfaces_.size()||surface>=info_.size())return 0;
            const auto& si=info_[surface];
            // Reuse the existing C3625 server physical rule. Identity is not it.
            if(!si.carves||si.internalBaffle||si.wallBaffle||si.name.find("merged_server_")!=0)continue;
            const triSurface& ts=static_cast<const triSurface&>(surfaces_[surface]);
            if(triShell_[surface].size()!=ts.size())return 0;
            std::set<std::string> uids;bool valid=!shellTris_[sh].empty();
            for(const auto& address:shellTris_[sh])
            {
                if(++scanned>maxScanned)return 0;
                if(address.first()!=surface||address.second()<0||address.second()>=ts.size())return 0;
                const auto& triangle=ts[address.second()];
                if(triangle.region()<0||triangle.region()>=si.nRegions)return 0;
                const auto* id=sourceIdentity(surface,triangle.region());
                if(!id||id->sourceObjectUid.empty()||id->sourceFaceUid.empty()) {valid=false;continue;}
                uids.insert(std::string(id->sourceObjectUid.c_str()));
                if(id->representation!="surfaceFace"
                 ||S::faceRole(std::string(id->sourceFaceUid.c_str()))==S::unknown)valid=false;
            }
            if(uids.size()!=1||!valid||shellTris_[sh].size()>label(S::maxTriangles))
            {for(const auto& uid:uids)blocked.insert(Key(surface,uid));continue;}
            candidates[Key(surface,*uids.begin())].push_back(sh);
            if(candidates.size()>maxGroups)return 0;
        }
        // All allocations/validation are staged before any original array write.
        List<List<labelPair>> pendingTris(shellTris_);
        List<labelList> pendingTriShell(triShell_);
        List<boundBox> pendingBounds(shellBounds_);
        boolList pendingClosed(shellClosed_);
        List<labelList> pendingSeeds(shellTris_.size());
        forAll(pendingSeeds,sh)pendingSeeds[sh]=labelList(1,sh);
        label merged=0;
        for(const auto& group:candidates)
        {
            if(blocked.count(group.first)||group.second.size()<2)continue;
            const label surface=group.first.first;
            const auto& ts=static_cast<const triSurface&>(surfaces_[surface]);
            const auto& pts=ts.points();
            std::vector<S::Facet> facets;S::Members members;std::set<label> triangleIds;
            bool valid=true;boundBox bbox(boundBox::invertedBox);
            for(const label sh:group.second)
            {
                for(const auto& address:shellTris_[sh])
                {
                    const label t=address.second();
                    if(t<0||t>=ts.size()||triShell_[surface][t]!=sh||!triangleIds.insert(t).second)return 0;
                    const auto& triangle=ts[t];
                    if(facets.size()>=S::maxTriangles){valid=false;break;}
                    const auto* id=sourceIdentity(surface,triangle.region());
                    if(!id||id->representation!="surfaceFace"||std::string(id->sourceObjectUid.c_str())!=group.first.second)return 0;
                    S::Facet facet;facet.surface=surface;facet.triangle=t;facet.seed=sh;
                    facet.objectUid=group.first.second;facet.faceUid=std::string(id->sourceFaceUid.c_str());
                    facet.role=S::faceRole(facet.faceUid);
                    for(unsigned v=0;v<3;++v)
                    {
                        const label pi=triangle[v];if(pi<0||pi>=pts.size())return 0;
                        facet.originalPoints[v]=pi;facet.points[v]={{double(pts[pi].x()),double(pts[pi].y()),double(pts[pi].z())}};
                        bbox.add(pts[pi]);
                    }
                    members[sh].push_back(t);facets.push_back(std::move(facet));
                }
                if(!valid)break;
            }
            if(!valid)continue; // the WHOLE object declines; never accept a prefix.
            if(operations>=maxTotalOperations)return 0;
            const auto certificate=S::certify(facets,members,std::min(S::maxOperations,maxTotalOperations-operations));
            operations+=certificate.operations;
            if(operations>=maxTotalOperations)return 0;
            if(!certificate.complete)continue;
            // Query in the existing findBoxToc order, filtering the FULL group.
            std::vector<label> found;findBoxToc(surface,treeBoundBox(bbox),found);
            std::vector<triInfo> tt;tt.reserve(facets.size());const auto& normals=ts.faceNormals();
            for(const label t:found)if(triangleIds.count(t))
            {
                const auto& triangle=ts[t];triInfo ti;
                ti.p0=pts[triangle[0]];ti.p1=pts[triangle[1]];ti.p2=pts[triangle[2]];
                ti.normal=normals[t];ti.region=info_[surface].regionOffset+triangle.region();tt.push_back(ti);
            }
            if(tt.size()!=facets.size())continue;
            vector serverAxes[3];point serverOrigin(point::zero);
            vector serverLo(GREAT,GREAT,GREAT),serverHi(-GREAT,-GREAT,-GREAT);bool serverBox=false;
            // Literal unchanged original prism numeric body, with identical TT order.
            serverOrigin = tt[0].p0;
            serverAxes[0] = tt[0].normal;
            for (const auto& tri : tt)
            {
                if (std::abs(tri.normal & serverAxes[0]) < 1e-5)
                {
                    serverAxes[1] = tri.normal - (tri.normal & serverAxes[0]) * serverAxes[0];
                    serverAxes[1] /= mag(serverAxes[1]);
                    serverAxes[2] = serverAxes[0] ^ serverAxes[1];
                    serverBox = true;
                    break;
                }
            }
            if (serverBox)
            {
                for (const auto& tri : tt)
                {
                    for (const point& p : {tri.p0, tri.p1, tri.p2})
                    {
                        for (direction d = 0; d < 3; ++d)
                        {
                            const scalar q = (p - serverOrigin) & serverAxes[d];
                            serverLo[d] = min(serverLo[d], q); serverHi[d] = max(serverHi[d], q);
                        }
                    }
                }
                bool seen[6] = {false, false, false, false, false, false};
                for (const auto& tri : tt)
                {
                    bool onBoxFace = false;
                    for (direction d = 0; d < 3; ++d)
                    {
                        for (label sd = 0; sd < 2; ++sd)
                        {
                            const scalar limit = sd ? serverHi[d] : serverLo[d];
                            bool on = true;
                            for (const point& p : {tri.p0, tri.p1, tri.p2})
                            {
                                on = on && std::abs(((p - serverOrigin) & serverAxes[d]) - limit) < 2e-6;
                            }
                            if (on) { onBoxFace = true; seen[2*d + sd] = true; }
                        }
                    }
                    if (!onBoxFace) { serverBox = false; break; }
                }
                label nSides = 0;
                for (bool side : seen) { nSides += side; }
                serverBox = serverBox && nSides >= 4 && cmptMin(serverHi - serverLo) > 1e-6;
            }

            if(!serverBox)continue;
            const label representative=*std::min_element(group.second.begin(),group.second.end());
            DynamicList<labelPair> combined;bool originalClosed=true;
            for(const label sh:group.second)
            {
                originalClosed=originalClosed&&shellClosed_[sh];
                for(const auto& address:shellTris_[sh])combined.append(address);
            }
            std::sort(combined.begin(),combined.end(),[](const labelPair& a,const labelPair& b)
            {return a.first()<b.first()||(a.first()==b.first()&&a.second()<b.second());});
            pendingTris[representative]=List<labelPair>(std::move(combined));
            pendingBounds[representative]=bbox;
            pendingClosed[representative]=originalClosed; // never promote an open seed to closed.
            pendingSeeds[representative]=labelList(group.second.size());
            for(std::size_t j=0;j<group.second.size();++j)
            {
                const label sh=group.second[j];pendingSeeds[representative][j]=sh;
                for(const auto& address:shellTris_[sh])pendingTriShell[surface][address.second()]=representative;
                if(sh!=representative)
                {
                    pendingTris[sh].clear();pendingBounds[sh]=boundBox(boundBox::invertedBox);
                    pendingClosed[sh]=false;pendingSeeds[sh].clear();
                }
            }
            ++merged;
        }
        if(!merged)return 0;
        // Allocate the replacement signed-cycle cache before the no-allocation commit.
        std::vector<shellCycleCacheEntry> pendingCache(shellTris_.size());
        retirePreparedShellCycles();
        shellTris_.transfer(pendingTris);triShell_.transfer(pendingTriShell);
        shellBounds_.transfer(pendingBounds);shellClosed_.transfer(pendingClosed);
        serverComponentSeeds_.transfer(pendingSeeds);shellCycleCache_.swap(pendingCache);
        shellMembershipEpoch_=shellGeometryEpoch_;
        serverComponentGroups_=merged;serverComponentsFinalized_=true;
        // Surface point/tree caches, regionTris, refinement-shell grid and sheets
        // are unchanged: their source coordinates/triangle addresses never moved.
        return merged;
    }
    catch(const std::exception& error)
    {
        FatalErrorInFunction << "unexpected server finalization exception: " << error.what()
            << exit(FatalError);
        return 0; // syntactically required; FatalError terminates the full MPI job.
    }
}

Foam::label Foam::dcCartSurfaces::readSourceSurfaceIdentities
(
    const dictionary& sourceSurfaces
)
{
    if(serverComponentsFinalized_)
    {
        FatalErrorInFunction << "source identities cannot be replaced after explicit prepared server finalization"
            << exit(FatalError);
    }
    List<List<sourceSurfaceIdentity>> pending(surfaces_.size());
    forAll(surfaces_, i) { pending[i].setSize(surfaces_[i].regions().size()); }
    std::set<std::string> faceUids;
    label count = 0;
    for (const entry& fileEntry : sourceSurfaces)
    {
        if (!fileEntry.isDict() || fileEntry.keyword().isPattern())
        {
            FatalErrorInFunction << "dcdtuniSourceSurfaces: file entry must be a literal dictionary: "
                << fileEntry.keyword() << exit(FatalError);
        }
        label surfaceI = -1;
        forAll(info_, i)
        {
            if (info_[i].stlFile != fileEntry.keyword()) { continue; }
            if (surfaceI >= 0)
            {
                FatalErrorInFunction << "dcdtuniSourceSurfaces: ambiguous STL file "
                    << fileEntry.keyword() << " matches more than one surface entry"
                    << exit(FatalError);
            }
            surfaceI = i;
        }
        if (surfaceI < 0)
        {
            FatalErrorInFunction << "dcdtuniSourceSurfaces: unknown STL file "
                << fileEntry.keyword() << exit(FatalError);
        }
        const wordList regions(surfaces_[surfaceI].regions());
        for (const entry& regionEntry : fileEntry.dict())
        {
            if (!regionEntry.isDict() || regionEntry.keyword().isPattern())
            {
                FatalErrorInFunction << "dcdtuniSourceSurfaces: region entry must be a literal dictionary: "
                    << fileEntry.keyword() << '/' << regionEntry.keyword() << exit(FatalError);
            }
            label regionI = -1;
            forAll(regions, i)
            {
                if (regions[i] != regionEntry.keyword()) { continue; }
                if (regionI >= 0)
                {
                    FatalErrorInFunction << "dcdtuniSourceSurfaces: ambiguous STL region "
                        << fileEntry.keyword() << '/' << regionEntry.keyword() << exit(FatalError);
                }
                regionI = i;
            }
            if (regionI < 0)
            {
                FatalErrorInFunction << "dcdtuniSourceSurfaces: unknown STL region "
                    << fileEntry.keyword() << '/' << regionEntry.keyword() << exit(FatalError);
            }
            const dictionary& fields = regionEntry.dict();
            for (const entry& field : fields)
            {
                const word& key = field.keyword();
                if (field.isDict() || field.keyword().isPattern()
                 || (key != "sourceObjectUid" && key != "sourceFaceUid" && key != "representation"))
                {
                    FatalErrorInFunction << "dcdtuniSourceSurfaces: unknown or invalid field "
                        << key << " in " << fileEntry.keyword() << '/' << regionEntry.keyword()
                        << exit(FatalError);
                }
            }
            for (const word& key : {word("sourceObjectUid"), word("sourceFaceUid"), word("representation")})
            {
                if (!fields.found(key, keyType::LITERAL))
                {
                    FatalErrorInFunction << "dcdtuniSourceSurfaces: missing " << key
                        << " in " << fileEntry.keyword() << '/' << regionEntry.keyword()
                        << exit(FatalError);
                }
            }
            sourceSurfaceIdentity identity;
            identity.sourceObjectUid = fields.get<string>("sourceObjectUid", keyType::LITERAL);
            identity.sourceFaceUid = fields.get<string>("sourceFaceUid", keyType::LITERAL);
            identity.representation = fields.get<word>("representation", keyType::LITERAL);
            if (identity.sourceObjectUid.find_first_not_of(" \t\r\n") == string::npos)
            {
                FatalErrorInFunction << "dcdtuniSourceSurfaces: empty sourceObjectUid in "
                    << fileEntry.keyword() << '/' << regionEntry.keyword() << exit(FatalError);
            }
            if (identity.sourceFaceUid.find_first_not_of(" \t\r\n") == string::npos)
            {
                FatalErrorInFunction << "dcdtuniSourceSurfaces: empty sourceFaceUid in "
                    << fileEntry.keyword() << '/' << regionEntry.keyword() << exit(FatalError);
            }
            if (identity.representation != "zeroThicknessSheet" && identity.representation != "surfaceFace")
            {
                FatalErrorInFunction << "dcdtuniSourceSurfaces: unsupported representation "
                    << identity.representation << exit(FatalError);
            }
            if (!faceUids.insert(std::string(identity.sourceFaceUid.c_str())).second)
            {
                FatalErrorInFunction << "dcdtuniSourceSurfaces: duplicate sourceFaceUid "
                    << identity.sourceFaceUid << exit(FatalError);
            }
            pending[surfaceI][regionI] = identity;
            ++count;
        }
    }
    sourceIdentities_.transfer(pending);
    return count;
}


const Foam::dcCartSurfaces::sourceSurfaceIdentity*
Foam::dcCartSurfaces::sourceIdentity(label surfaceI, label regionI) const
{
    if (surfaceI < 0 || surfaceI >= surfaces_.size()
     || regionI < 0 || regionI >= surfaces_[surfaceI].regions().size())
    {
        FatalErrorInFunction << "dcdtuniSourceSurfaces: invalid surface/region lookup "
            << surfaceI << '/' << regionI << exit(FatalError);
    }
    if (sourceIdentities_.empty()) { return nullptr; }
    const sourceSurfaceIdentity& identity = sourceIdentities_[surfaceI][regionI];
    return identity.sourceFaceUid.empty() ? nullptr : &identity;
}
