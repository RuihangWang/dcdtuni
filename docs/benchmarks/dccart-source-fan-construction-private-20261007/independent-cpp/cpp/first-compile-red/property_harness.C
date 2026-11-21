#include "point.H"
#include "vector.H"
#include <cmath>
#include <cstring>
#include <cstdint>
#include <vector>
#include <iostream>
#include <iomanip>
#include <cfenv>
#include <sys/wait.h>
#include <sys/resource.h>
#include <unistd.h>
using namespace Foam;
static_assert(sizeof(scalar)==8 && sizeof(label)==4,"Require actual OpenFOAM DPInt32");
scalar D(std::uint64_t x){scalar s;std::memcpy(&s,&x,8);return s;}
point P(std::uint64_t x,std::uint64_t y,std::uint64_t z){return point(D(x),D(y),D(z));}
std::uint64_t bits(scalar x){std::uint64_t n;std::memcpy(&n,&x,8);return n;}
bool same(const point& a,const point& b){return bits(a[0])==bits(b[0]) && bits(a[1])==bits(b[1]) && bits(a[2])==bits(b[2]);}
void emit(const point& p){std::cout<<'[';for(int k=0;k<3;++k){if(k)std::cout<<',';std::cout<<'"'<<std::hex<<std::setw(16)<<std::setfill('0')<<bits(p[k])<<'"';}std::cout<<std::dec<<']';}
inline void faceAreaCentreOF
(
    const std::vector<Foam::point>& pts,
    const std::vector<Foam::label>& poly,
    Foam::vector& A,
    Foam::point& C
);
template<class GeoFn>
Foam::scalar boundarySkewT
(
    const Foam::dcCartCutCells::cutMesh& m,
    const std::vector<std::vector<Foam::label>>& cellFaces,
    Foam::label c,
    Foam::label o,
    GeoFn& geo
);
} // End anonymous namespace


void Foam::dcCartCutCells::carveShellPass
(
    cutMesh& m,
    const dcCartSurfaces& surfaces,
    const settings& s,
    report& r,
    label sh,
    const boundBox& sbb,
    std::vector<bool>& everSplit,
    std::vector<signed char>& gside,
    const std::vector<bool>& origSlot
)
{
    // One shell's pass, on the neighbourhood cutCarving extracted for it (the
    // whole mesh would do too: nothing here looks further than two rings).
    const scalar tol = s.onToleranceCarve;
    const scalar tol2 = tol * tol;
    auto nearestRegion = [&](const std::vector<label>& poly) -> label
    {
        vector nn; point cc;
        polygonNormalCentre(m.points, poly, nn, cc);
        const label reg = surfaces.nearestCarvingRegion(cc);
        return reg >= 0 ? reg : 0;
    };
    const bool closed = surfaces.solidShellClosed(sh);

    // Rack servers omit their side walls from the export: those walls belong
    // to the surrounding frame. Reachability through these artificial gaps
    // is not a reliable inside/outside test for the server body. Recognize
    // the rectangular server prism, without adding duplicate side patches.
    vector serverAxes[3];
    point serverOrigin(point::zero);
    vector serverLo(GREAT, GREAT, GREAT), serverHi(-GREAT, -GREAT, -GREAT);
    bool serverBox = false;
    bool frameSurface = false;
    {
        std::vector<dcCartSurfaces::triInfo> tt;
        surfaces.solidShellTrianglesInBox(sh, sbb, tt);
        bool serverSurface = false;
        if (!tt.empty())
        {
            for (const auto& si : surfaces.info())
            {
                if (tt[0].region >= si.regionOffset && tt[0].region < si.regionOffset + si.nRegions)
                {
                    serverSurface = si.name.find("merged_server_") == 0;
                    frameSurface = si.name == "merged_rack_frame";
                    break;
                }
            }
        }
        if (serverSurface && tt.size() >= 4)
        {
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
        }
    }


    // ---- feature pre-split ---------------------------------------------
    // A cell holding a convex edge of the solid (two facets > 20° apart)
    // would be cut into an L-shaped, concave cell whose centre sits
    // behind one facet (checkMesh's face-pyramid error), or — with one
    // tilted cap — into a chamfered one. Instead the cell is first split
    // by one facet's plane (a plain internal face where that plane runs
    // past the facet); each half is then cut by one facet only, so the
    // caps are planar, the edge is exact and the halves are convex.
    if (!getenv("DCCART_NOPRESPLIT"))
    {
        featurePreSplit(m, surfaces, sh, s, r, everSplit, serverBox || frameSurface);
        // One id per position before this shell looks at the points:
        // two ids on one spot (the pre-split's planes meeting on an
        // edge) are classified apart — one ON, one inside — and a cell
        // with an all-ON top and side then splits along an L-shaped
        // loop of their edges into one folded cap.
        mergeCoincidentPoints(m, &sbb);
        dedupFaceVertices(m);
    }

    // cells the shell may cross, and their points
    std::vector<std::vector<label>> cellFaces;
    buildCellFaces(m, cellFaces);
    std::vector<bool> eligible(m.nCells, false);
    std::vector<bool> nearPoint(m.points.size(), false);
    label nEligible = 0;
    for (label c = 0; c < m.nCells; ++c)
    {
        if (!m.cellAlive[c]) { continue; }
        boundBox bb(boundBox::invertedBox);
        for (label f : cellFaces[c])
        {
            for (label v : m.faces[f]) { bb.add(m.points[v]); }
        }
        if (!bb.valid() || !bb.overlaps(sbb)) { continue; }
        eligible[c] = true;
        ++nEligible;
        for (label f : cellFaces[c])
        {
            for (label v : m.faces[f]) { nearPoint[v] = true; }
        }
    }
    // (no live cell overlaps the shell's box: nothing to classify or cut)
    if (!nEligible)
    {
        // This return must NOT skip the collective below: syncSideOwners
        // exchanges one band with each slab neighbour per shell pass, so a
        // rank that bails out here while another rank still has eligible
        // cells leaves that neighbour blocked forever on this rank's band.
        // Participate with an empty dummy mesh — exactly the empty-reach
        // guard in cutCarving — so the exchange completes: the dummy sends
        // empty bands and applies nothing it receives. Unreachable under
        // the current caller invariant (cutCarving only calls this pass
        // with a non-empty reach), but a bare early return deadlocks the
        // moment that invariant changes.
        if (Pstream::parRun())
        {
            cutMesh dummy;
            std::vector<signed char> noSide;
            syncSideOwners(dummy, noSide);
        }
        return;
    }
    if (getenv("DCCART_DEBUG"))
    {
        std::vector<dcCartSurfaces::triInfo> tt;
        surfaces.solidShellTrianglesInBox(sh, sbb, tt);
        std::set<label> regs;
        for (const auto& t : tt) { regs.insert(t.region); }
        Foam::Info<< "  shell " << sh << (closed ? " closed" : " OPEN")
            << " tris " << label(tt.size()) << " regions " << label(regs.size())
            << " bb " << sbb.min() << " " << sbb.max()
            << " eligible " << nEligible;
        label shown = 0;
        for (label rg : regs) { if (shown++ < 6) { Foam::Info<< " " << surfaces.patchNames()[rg]; } }
        Foam::Info<< Foam::nl;
    }

    // fluid anchors: ORIGINAL live cells owning the point (their centres
    // are fluid with respect to every shell)
    std::vector<std::vector<label>> pointCells(m.points.size());
    std::vector<std::vector<label>> pointAnyCells(m.points.size());
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        for (label sd = 0; sd < 2; ++sd)
        {
            const label c = sd ? m.neighbour[f] : m.owner[f];
            if (c < 0 || !m.cellAlive[c]) { continue; }
            for (label v : m.faces[f])
            {
                if (!nearPoint[v]) { continue; }
                auto& any = pointAnyCells[v];
                if (any.empty() || any.back() != c) { any.push_back(c); }
                if (!m.cellOrigAlive[c]) { continue; }
                auto& lst = pointCells[v];
                if (lst.empty() || lst.back() != c) { lst.push_back(c); }
            }
        }
    }
    // A point owned only by re-admitted cells (inside a thin plate the
    // stair-step carve had dropped) borrows its anchors from the
    // original live cells next to those: two rings is enough.
    auto borrowAnchors = [&](label p, std::vector<label>& out)
    {
        out.clear();
        std::vector<label> ring(pointAnyCells[p]);
        std::unordered_set<label> seen(ring.begin(), ring.end());
        for (label depth = 0; depth < 2 && out.empty(); ++depth)
        {
            std::vector<label> next;
            for (label c : ring)
            {
                for (label f : cellFaces[c])
                {
                    const label o = (m.owner[f] == c) ? m.neighbour[f] : m.owner[f];
                    if (o < 0 || !m.cellAlive[o] || seen.count(o)) { continue; }
                    seen.insert(o);
                    if (m.cellOrigAlive[o]) { out.push_back(o); }
                    next.push_back(o);
                }
            }
            ring.swap(next);
        }
    };

    // Where a ray to a point starts from: the anchor cell's own vertex
    // average. A pre-split half inherits its parent's refCentre, which
    // may lie beyond the facet plane (below the unit's top while the
    // half is above it): rays from there enter the solid first and
    // every point on the top reads "blocked". A half whose centroid is
    // not on its parent's side of the shell is no anchor at all.
    std::vector<point> anchorPt(m.nCells, point::zero);
    std::vector<signed char> anchorOk(m.nCells, -1);   // -1 unknown, 0 no, 1 yes
    auto anchorPoint = [&](label c) -> const point&
    {
        if (anchorOk[c] < 0)
        {
            point sum = point::zero; label n = 0;
            for (label f : cellFaces[c]) { for (label v : m.faces[f]) { sum += m.points[v]; ++n; } }
            anchorPt[c] = n ? sum / n : m.cellRefCentre[c];
            anchorOk[c] = 1;
        }
        return anchorPt[c];
    };
    // side of every near point w.r.t. this shell: +1 out, -1 in, 0 on.
    // ON by distance; in/out by the PARITY of the shell's crossings
    // along a segment from a fluid anchor — independent of which way the
    // STL normals point (the room enclosure and the boxes disagree).
    // Coincident hits (a segment through an edge shared by two
    // triangles reports both) count once.
    std::vector<signed char> side(m.points.size(), 1);
    // on the shell by distance (whatever side the anchors decide)
    std::vector<signed char> onDist(m.points.size(), 0);
    std::vector<label> onDistReg(m.points.size(), -1);
    std::vector<point> hits; std::vector<label> regs;
    auto parityFrom = [&](const point& c0, const point& p) -> label
    {
        surfaces.solidShellHits(sh, c0, p, hits, regs);
        label k = 0;
        point last(GREAT, GREAT, GREAT);
        for (const point& h : hits)
        {
            if (magSqr(h - p) <= tol2) { continue; }       // the point itself
            if (magSqr(h - last) <= tol2) { continue; }    // same crossing twice
            last = h;
            ++k;
        }
        return k;
    };
    const point farPt = sbb.max() + 0.37 * vector(1, 1.1, 1.2) * mag(sbb.span());
    auto anchorUsable = [&](label c) -> bool
    {
        const point& a = anchorPoint(c);
        if (anchorOk[c] == 0) { return false; }
        if (c < label(origSlot.size()) && origSlot[c]) { return true; }
        // a half: same side of the shell as the parent's centre?
        const label kx = parityFrom(m.cellRefCentre[c], a);
        const bool ok = (kx == 0 || (closed && kx % 2 == 0));
        if (!ok) { anchorOk[c] = 0; }
        return ok;
    };
    // Is the winding number a usable oracle for this shell? Not for one
    // that ENCLOSES the fluid (the four room walls: every fluid anchor
    // reads |w| ~ 1), which is decided from the fluid anchors. For every
    // other shell — closed, or a box missing a face — |w| > 1/2 is
    // inside, and it does not care which way the STL normals point.
    bool useWinding = closed;
    if (!closed)
    {
        std::vector<scalar> ws;
        for (label c = 0; c < m.nCells && ws.size() < 300; ++c)
        {
            if (!eligible[c] || !m.cellOrigAlive[c]) { continue; }
            ws.push_back(std::abs(surfaces.solidShellWinding(sh, m.cellRefCentre[c])));
        }
        // usable when some anchor is clearly outside and none is clearly
        // inside (anchors between stacked servers read ~1/2 either way)
        label clearOut = 0, clearIn = 0;
        for (scalar w : ws) { if (w < 0.25) { ++clearOut; } else if (w > 0.75) { ++clearIn; } }
        if (!ws.empty()) { std::sort(ws.begin(), ws.end()); }
        // a box missing one face still reads ~0 outside; a server box
        // exported without its sides, stacked with others, does not —
        // its anchors sit in the gaps and read ~1/2, and the winding
        // would then turn the missing sides into stair-step walls
        // ~0 only: a box missing its wall-side face reads 1e-16 at every
        // anchor; a hollow rack frame with ring openings reads ~0.2 and
        // its cavity points spread from 0 to 1 — MeshGenerator's finding
        // that the winding number is not an oracle for open shells
        // Off for every open shell. Even a box that only lacks its
        // wall-side face reads |w| > 1/2 in the fluid just outside its
        // OTHER openings — the ACU's duct mouth was carved away as
        // "inside" — so an open shell is always answered by reachability.
        useWinding = false;
        (void)clearIn; (void)clearOut;
        if (getenv("DCCART_DEBUG"))
        {
            Foam::Info<< "    open shell: anchors median |w| "
                << (ws.empty() ? -1.0 : ws[ws.size() / 2])
                << (useWinding ? "  -> winding" : "  -> reachability") << Foam::nl;
        }
    }
    for (label p = 0; p < label(m.points.size()); ++p)
    {
        if (!nearPoint[p]) { continue; }
        label reg;
        const bool onByDist = (surfaces.solidShellDistance(sh, m.points[p], 4 * tol, reg) <= tol);
        if (onByDist) { onDist[p] = 1; onDistReg[p] = reg; }
        auto& lst = pointCells[p];
        std::sort(lst.begin(), lst.end());
        lst.erase(std::unique(lst.begin(), lst.end()), lst.end());
        if (onByDist)
        {
            side[p] = 0;    // decided again below, from its neighbours
            continue;
        }
        // Fluid when some anchor reaches the point: with no crossing at
        // all for an open shell (the four walls, a server box exported
        // without its sides — behind such a surface is solid), with an
        // even count for a closed one (in and out of a thin solid is
        // still outside). Orientation-free: STL producers disagree on
        // which way normals point.
        signed char best = -1;
        if (serverBox)
        {
            bool inside = true;
            for (direction d = 0; d < 3; ++d)
            {
                const scalar q = (m.points[p] - serverOrigin) & serverAxes[d];
                inside = inside && q >= serverLo[d] - 1e-9 && q <= serverHi[d] + 1e-9;
            }
            side[p] = inside ? -1 : 1;
            continue;
        }
        if (useWinding)
        {
            side[p] = (std::abs(surfaces.solidShellWinding(sh, m.points[p])) > 0.5) ? -1 : 1;
            continue;
        }
        std::vector<label> borrowed;
        if (lst.empty()) { borrowAnchors(p, borrowed); }
        const std::vector<label>& anchors = lst.empty() ? borrowed : lst;
        if (anchors.empty())
        {
            best = (closed && parityFrom(farPt, m.points[p]) % 2 == 0) ? 1 : -1;
        }
        else
        {
            for (label c : anchors)
            {
                const label kx = parityFrom(m.cellRefCentre[c], m.points[p]);
                if (kx == 0 || (closed && kx % 2 == 0)) { best = 1; break; }
            }
        }
        side[p] = best;
    }

    // ---- the owner of a point decides its side --------------------------------
    // The anchor set of a point is the cells around it in THIS rank's local
    // mesh, and near a slab boundary the halo ends before the two-face-ring
    // anchor reach: a halo rank can then classify a shared point differently
    // from the rank that owns it (DAML-Testbed: the corner behind the open
    // z-end of a GPU server, where one rank still holds the western cell
    // whose borrowed parity ray escapes through the missing tube face and
    // reads "fluid" while the other rank has lost that anchor and reads
    // "solid"). The diverging side then flips a promote chain, the interface
    // cells clip differently on the two ranks, and the processor patches no
    // longer mirror each other -- checkMesh dies exchanging face indices
    // with a neighbour whose list does not match. Overwrite the side of
    // every point this rank does NOT own with the value its owner computed
    // (owner = lowest cellRank among the live cells containing the point --
    // the same rank on every holder, because the cells and their cellRank
    // are global data). Band-limited exchange with the two slab neighbours
    // per shell pass; a pure function of the owner's view, so the halo is
    // cut exactly as its owner cuts it, which is the invariant the whole
    // parallel design rests on.
    syncSideOwners(m, side);

    // ---- DCCART_DEBUG_CELL: trace the classification of one cell (temp) --
    if (getenv("DCCART_DEBUG_CELL"))
    {
        double bc[6] = {0, 0, 0, 0, 0, 0};
        if (sscanf(getenv("DCCART_DEBUG_CELL"), "%lf,%lf,%lf,%lf,%lf,%lf",
                   &bc[0], &bc[1], &bc[2], &bc[3], &bc[4], &bc[5]) == 6)
        {
            const boundBox tbox(point(bc[0], bc[1], bc[2]), point(bc[3], bc[4], bc[5]));
            for (label c = 0; c < m.nCells; ++c)
            {
                if (!m.cellAlive[c]) { continue; }
                boundBox cb(boundBox::invertedBox);
                for (const label f : cellFaces[c])
                {
                    if (!m.faceAlive[f]) { continue; }
                    for (const label v : m.faces[f]) { cb.add(m.points[v]); }
                }
                if (!cb.valid() || !cb.overlaps(tbox)) { continue; }
                Pout<< "SIDE rank " << Pstream::myProcNo() << " shell " << sh
                    << " cell " << c << " cellRank " << m.cellRank[c]
                    << " orig " << (m.cellOrigAlive[c] ? 1 : 0)
                    << " eligible " << (c < label(eligible.size()) && eligible[c] ? 1 : 0)
                    << " refC " << m.cellRefCentre[c] << nl;
                std::set<label> seenPts;
                for (const label f : cellFaces[c])
                {
                    if (!m.faceAlive[f]) { continue; }
                    for (const label v : m.faces[f])
                    {
                        if (!seenPts.insert(v).second) { continue; }
                        Pout<< "  PT rank " << Pstream::myProcNo() << " shell " << sh
                            << " cell " << c << " v " << v << " " << m.points[v]
                            << " side " << int(side[v]) << " onD " << int(onDist[v]) << nl;
                        // anchor forensics: which cells anchored this point
                        if (!onDist[v] && !serverBox && !useWinding)
                        {
                            auto lst2 = pointCells[v];
                            std::sort(lst2.begin(), lst2.end());
                            lst2.erase(std::unique(lst2.begin(), lst2.end()), lst2.end());
                            std::vector<label> borr;
                            const bool usedBorrow = lst2.empty();
                            if (usedBorrow) { borrowAnchors(v, borr); }
                            const std::vector<label>& anch = usedBorrow ? borr : lst2;
                            for (const label ac : anch)
                            {
                                Pout<< "    ANCH rank " << Pstream::myProcNo()
                                    << " v " << v << " anchor " << ac
                                    << " refC " << m.cellRefCentre[ac]
                                    << " kx " << parityFrom(m.cellRefCentre[ac], m.points[v])
                                    << " orig " << (m.cellOrigAlive[ac] ? 1 : 0) << nl;
                            }
                            if (anch.empty())
                            {
                                Pout<< "    ANCH rank " << Pstream::myProcNo()
                                    << " v " << v << " NONE (farPt kx "
                                    << parityFrom(farPt, m.points[v]) << ")" << nl;
                            }
                        }
                    }
                }
            }
        }
    }

    // A point ON the shell by distance is ON only when the fluid
    // reaches it directly: a hex corner on a pipe's end disk (flush
    // with the ceiling) is ON the shell by distance, but the edge from
    // every fluid neighbour crosses the pipe wall before it — it is
    // inside, and the wall must cross the faces around it. Decided on
    // the mesh edges, the same segments the crossings are found on.
    {
        std::vector<std::vector<label>> nbr(m.points.size());
        for (label f = 0; f < label(m.faces.size()); ++f)
        {
            if (!m.faceAlive[f]) { continue; }
            const std::vector<label>& poly = m.faces[f];
            const label k = label(poly.size());
            bool any = false;
            for (label v : poly) { if (v < label(onDist.size()) && onDist[v]) { any = true; break; } }
            if (!any) { continue; }
            for (label i = 0; i < k; ++i)
            {
                const label a = poly[i], b = poly[(i + 1) % k];
                if (a == b) { continue; }
                if (a < label(onDist.size()) && onDist[a]) { nbr[a].push_back(b); }
                if (b < label(onDist.size()) && onDist[b]) { nbr[b].push_back(a); }
            }
        }
        // 0 undecided, 1 reached (stays ON), -1 not reached
        std::vector<signed char> state(onDist.size(), 0);
        label nInside = 0;
        for (label p = 0; p < label(onDist.size()); ++p)
        {
            if (!onDist[p]) { continue; }
            bool anyFluidNbr = false, reached = false;
            for (label q : nbr[p])
            {
                if (q >= label(side.size()) || !nearPoint[q] || side[q] <= 0) { continue; }
                anyFluidNbr = true;
                if (parityFrom(m.points[q], m.points[p]) == 0) { reached = true; break; }
            }
            if (anyFluidNbr) { state[p] = reached ? 1 : -1; }
        }
        // A point with no fluid neighbour (a concave corner where a
        // column's side meets the crossbar's underside: every neighbour
        // is on one surface or the other) is reached through a reached
        // ON neighbour along an edge the shell does not cross.
        for (int it = 0; it < 4; ++it)
        {
            label nNew = 0;
            for (label p = 0; p < label(onDist.size()); ++p)
            {
                if (!onDist[p] || state[p] != 0) { continue; }
                for (label q : nbr[p])
                {
                    if (q >= label(state.size()) || !onDist[q] || state[q] != 1) { continue; }
                    if (parityFrom(m.points[q], m.points[p]) == 0) { state[p] = 1; ++nNew; break; }
                }
            }
            if (!nNew) { break; }
        }
        for (label p = 0; p < label(onDist.size()); ++p)
        {
            if (!onDist[p]) { continue; }
            bool reached = (state[p] == 1);
            if (state[p] == 0)
            {
                // nothing around it is decided (a hex corner in the
                // middle of a pipe's end disk): ask the fluid anchors
                // around it, as for any other point
                auto& lst = pointCells[p];
                std::vector<label> borrowed;
                if (lst.empty()) { borrowAnchors(p, borrowed); }
                const std::vector<label>& anchors = lst.empty() ? borrowed : lst;
                reached = anchors.empty();
                for (label c : anchors)
                {
                    const label kx = parityFrom(m.cellRefCentre[c], m.points[p]);
                    if (kx == 0 || (closed && kx % 2 == 0)) { reached = true; break; }
                }
            }
            if (!reached) { side[p] = -1; ++nInside; }
        }
        if (nInside && getenv("DCCART_DEBUG"))
        {
            Foam::Info<< "    " << nInside << " on-by-distance points are inside (no fluid neighbour reaches them)" << Foam::nl;
        }
    }
    // ---- clear-edge promotion (shells answered by reachability) ---------
    // A point is put INSIDE when no anchor's straight ray reaches it. Two
    // ways that is wrong: it has no anchor within two rings (a corner of a
    // re-admitted cell buried in ANOTHER solid — the floor's pass asked about
    // a rack's base plate, the frame's pass about the inside of the switch
    // it holds), or every anchor's ray clips a convex edge of this shell on
    // its way (enters a box's top, leaves through its back, 0.5 mm from the
    // corner it went round). Yet a mesh edge joins it to a reached point
    // without coming within the ON tolerance of any facet of the shell — the
    // crossing search looks at exactly that edge, finds nothing, and the
    // faces around it cannot be split: the cell keeps its stair-step shape.
    // Such a point is reached along that edge. A few rounds, each from the
    // labels of the round before (independent of the order of the points),
    // never through an ON point and never along an edge near a facet.
    // (each round looks one mesh edge further than the anchors did; three
    // rounds stay inside the three-layer halo a rank cuts its cells with.
    // DCCART_PROMOTE_ROUNDS=0 switches the promotion off.)
    if (!useWinding && !serverBox)
    {
        static const label promoteRounds = []
        {
            const char* e = getenv("DCCART_PROMOTE_ROUNDS");
            return e ? label(atoi(e)) : label(3);
        }();
        static const Foam::boundBox dbgPromoteBox = []
        {
            double b[6];
            const char* e = getenv("DCCART_DEBUG_BOX");
            if (e && sscanf(e, "%lf %lf %lf %lf %lf %lf", b, b+1, b+2, b+3, b+4, b+5) == 6)
            {
                return Foam::boundBox(Foam::point(b[0], b[1], b[2]), Foam::point(b[3], b[4], b[5]));
            }
            return Foam::boundBox(Foam::boundBox::invertedBox);
        }();
        // an inside point that is not ON the shell by distance
        auto isCand = [&](label v) -> bool
        {
            return v < label(side.size()) && v < label(nearPoint.size())
                && nearPoint[v] && side[v] == -1 && !onDist[v];
        };
        std::vector<dcCartSurfaces::triInfo> segTris;
        // clear: no facet of the shell within the ON tolerance of the edge
        // (a pierced facet is at distance 0, so this is the crossing test
        // too, and a hit findLine might miss on a facet's rim still counts)
        auto edgeClear = [&](const point& a, const point& b) -> bool
        {
            boundBox bb(a, a);
            bb.add(b);
            bb.grow(2 * tol);
            surfaces.solidShellTrianglesInBox(sh, bb, segTris);
            for (const auto& t : segTris)
            {
                if (segTriDist2(a, b, t.p0, t.p1, t.p2) <= tol2) { return false; }
            }
            return true;
        };
        // the edges of the pass's faces from an inside point p to a point q
        // of the given kind, (p, q) sorted: round 0 asks the anchor-reached
        // points, later rounds the points promoted by the round before
        std::vector<bool> fromLast;
        auto edgesTo = [&](bool firstRound, std::vector<std::pair<label, label>>& out)
        {
            out.clear();
            for (label f = 0; f < label(m.faces.size()); ++f)
            {
                if (!m.faceAlive[f]) { continue; }
                const label o = m.owner[f], nb = m.neighbour[f];
                if (!((o >= 0 && eligible[o]) || (nb >= 0 && eligible[nb]))) { continue; }
                const std::vector<label>& poly = m.faces[f];
                const label k = label(poly.size());
                for (label i = 0; i < k; ++i)
                {
                    const label a = poly[i], b = poly[(i + 1) % k];
                    if (a == b) { continue; }
                    for (int dirn = 0; dirn < 2; ++dirn)
                    {
                        const label pp = dirn ? b : a, qq = dirn ? a : b;
                        if (!isCand(pp) || qq >= label(side.size())) { continue; }
                        const bool ok = firstRound
                            ? (qq < label(nearPoint.size()) && nearPoint[qq] && side[qq] == 1)
                            : (qq < label(fromLast.size()) && fromLast[qq]);
                        if (ok) { out.emplace_back(pp, qq); }
                    }
                }
            }
            std::sort(out.begin(), out.end());
            out.erase(std::unique(out.begin(), out.end()), out.end());
        };
        label nPromoted = 0, nRounds = 0;
        std::vector<std::pair<label, label>> pairs;
        for (label round = 0; round < promoteRounds; ++round)
        {
            edgesTo(round == 0, pairs);
            // ---- DCCART_DEBUG_CELL: seed-edge forensics (temporary) ------
            if (round == 0 && getenv("DCCART_DEBUG_CELL"))
            {
                double pb[6] = {0, 0, 0, 0, 0, 0};
                if (sscanf(getenv("DCCART_DEBUG_CELL"), "%lf,%lf,%lf,%lf,%lf,%lf",
                           &pb[0], &pb[1], &pb[2], &pb[3], &pb[4], &pb[5]) == 6)
                {
                    const Foam::boundBox pbox
                    (
                        Foam::point(pb[0], pb[1], pb[2]),
                        Foam::point(pb[3], pb[4], pb[5])
                    );
                    for (label f = 0; f < label(m.faces.size()); ++f)
                    {
                        if (!m.faceAlive[f]) { continue; }
                        const label o = m.owner[f], nb = m.neighbour[f];
                        const bool elig =
                            (o >= 0 && o < label(eligible.size()) && eligible[o])
                         || (nb >= 0 && nb < label(eligible.size()) && eligible[nb]);
                        const std::vector<label>& poly = m.faces[f];
                        const label k = label(poly.size());
                        for (label i = 0; i < k; ++i)
                        {
                            const label a = poly[i], b = poly[(i + 1) % k];
                            if (a == b || !isCand(a)) { continue; }
                            if (!pbox.contains(m.points[a])) { continue; }
                            Pout<< "PEDGE rank " << Pstream::myProcNo()
                                << " shell " << sh
                                << " p " << a << " " << m.points[a]
                                << " q " << b << " " << m.points[b]
                                << " sideQ " << int(side[b])
                                << " nearQ " << (b < label(nearPoint.size()) && nearPoint[b] ? 1 : 0)
                                << " faceElig " << (elig ? 1 : 0)
                                << " f " << f << nl;
                        }
                    }
                }
            }
            std::vector<label> up;
            for (size_t i = 0; i < pairs.size(); ++i)
            {
                const label p = pairs[i].first;
                if (!up.empty() && up.back() == p) { continue; }
                if (edgeClear(m.points[pairs[i].second], m.points[p])) { up.push_back(p); }
            }
            if (up.empty()) { break; }
            fromLast.assign(side.size(), false);
            for (label p : up)
            {
                side[p] = 1;
                fromLast[p] = true;
                if (dbgPromoteBox.valid() && dbgPromoteBox.contains(m.points[p]))
                {
                    const int oldPrec = Foam::Info().precision(9);
                    Foam::Info<< "    [promote] shell " << sh << " round " << round << " p " << p << " @" << m.points[p] << Foam::nl;
                    Foam::Info().precision(oldPrec);
                }
                if (getenv("DCCART_DEBUG_CELL"))
                {
                    double pb[6] = {0, 0, 0, 0, 0, 0};
                    if (sscanf(getenv("DCCART_DEBUG_CELL"), "%lf,%lf,%lf,%lf,%lf,%lf",
                               &pb[0], &pb[1], &pb[2], &pb[3], &pb[4], &pb[5]) == 6)
                    {
                        const Foam::boundBox pbox
                        (
                            Foam::point(pb[0], pb[1], pb[2]),
                            Foam::point(pb[3], pb[4], pb[5])
                        );
                        if (pbox.contains(m.points[p]))
                        {
                            Foam::Pout<< "PROMOTE rank " << Pstream::myProcNo()
                                << " shell " << sh << " round " << round
                                << " p " << p << " " << m.points[p] << Foam::nl;
                        }
                    }
                }
            }
            nPromoted += label(up.size());
            nRounds = round + 1;
        }
        if (nPromoted && getenv("DCCART_DEBUG"))
        {
            Foam::Info<< "    " << nPromoted << " inside points reached along clear edges ("
                << nRounds << " rounds)" << Foam::nl;
        }
    }
    if (getenv("DCCART_DEBUG"))
    {
        label nIn = 0, nOn = 0, nOut = 0;
        for (label p = 0; p < label(m.points.size()); ++p)
        {
            if (!nearPoint[p]) { continue; }
            if (side[p] < 0) { ++nIn; } else if (side[p] == 0) { ++nOn; } else { ++nOut; }
        }
        Foam::Info<< "    sides: in " << nIn << " on " << nOn << " out " << nOut << Foam::nl;
    }
    gside.resize(m.points.size(), 0);
    for (label p = 0; p < label(m.points.size()); ++p)
    {
        if (!nearPoint[p]) { continue; }
        if (side[p] < 0) { gside[p] = -1; }
        else if (side[p] == 0 && gside[p] > 0) { gside[p] = 0; }
    }
    std::unordered_map<uint64_t, label> crossings;
    std::unordered_map<label, label> pointRegion;
    std::unordered_map<label, vector> pointNormal;
    auto crossing = [&](label a, label b) -> label
    {
        const uint64_t key = edgeKey(a, b);
        auto it = crossings.find(key);
        if (it != crossings.end()) { return it->second; }
        const bool aFluid = (side[a] >= 0);
        const label pa = aFluid ? a : b;
        const label pb = aFluid ? b : a;
        const bool fromOn = (side[pa] == 0);
        const bool toOn = (side[pb] == 0);
        std::vector<vector> hitN;
        surfaces.solidShellHits(sh, m.points[pa], m.points[pb], hits, regs, &hitN);
        const vector edir = m.points[pb] - m.points[pa];
        const scalar elen = mag(edir);
        label id = -1;
        for (size_t i = 0; i < hits.size(); ++i)
        {
            // the ON vertex itself, at either end (an oblique facet
            // puts the hit farther along the edge than the vertex's
            // perpendicular distance to it)
            if (fromOn && magSqr(hits[i] - m.points[pa]) <= 16 * tol2) { continue; }
            if (toOn && magSqr(hits[i] - m.points[pb]) <= 16 * tol2) { continue; }
            // an edge between two ON vertices runs IN a facet: the
            // hits findLine reports on that facet are numerical noise,
            // not crossings — only a facet the edge goes through counts
            if ((fromOn && toOn) && elen > VSMALL && std::abs((hitN[i] & edir) / elen) < 0.05) { continue; }
            id = label(m.points.size());
            m.points.push_back(hits[i]);
            side.push_back(0);
            pointRegion[id] = regs[i];
            pointNormal[id] = hitN[i];
            break;
        }
        if (id < 0 && (fromOn || toOn)) { id = -2; }
        // a B end that is ON the shell by distance but was put inside
        // (the fluid does not reach it directly): the cut is at the
        // vertex itself, which findLineAll misses at the segment's end
        if (id == -1 && pb < label(onDist.size()) && onDist[pb])
        {
            id = label(m.points.size());
            m.points.push_back(m.points[pb]);
            side.push_back(0);
            pointRegion[id] = onDistReg[pb] >= 0 ? onDistReg[pb] : regs.empty() ? 0 : regs[0];
        }
        if (id == -1 && getenv("DCCART_DEBUG"))
        {
            static label nDbg = 0;
            static Foam::boundBox dbgBox(Foam::boundBox::invertedBox);
            static bool parsed = false;
            if (!parsed)
            {
                parsed = true; double b[6];
                if (getenv("DCCART_DEBUG_BOX") && sscanf(getenv("DCCART_DEBUG_BOX"), "%lf %lf %lf %lf %lf %lf", b, b+1, b+2, b+3, b+4, b+5) == 6)
                { dbgBox = Foam::boundBox(Foam::point(b[0], b[1], b[2]), Foam::point(b[3], b[4], b[5])); }
            }
            const bool inBox = dbgBox.valid() && (dbgBox.contains(m.points[pa]) || dbgBox.contains(m.points[pb]));
            if (nDbg++ < 6 || inBox)
            {
                point h1; label r1;
                const bool single = surfaces.carvingHit(m.points[pa], m.points[pb], h1, r1);
                Foam::Info<< "    crossing MISSED shell " << sh << " from " << m.points[pa]
                    << " (side " << label(side[pa]) << ") to " << m.points[pb]
                    << " (side " << label(side[pb]) << ") findLineAll hits " << label(hits.size());
                for (const point& h : hits) { Foam::Info<< " " << h; }
                Foam::Info<< " | findLine: " << (single ? "hit " : "none ");
                if (single) { Foam::Info<< h1 << " region " << surfaces.patchNames()[r1]; }
                Foam::Info<< Foam::nl;
            }
        }
        crossings.emplace(key, id);
        return id;
    };
    auto existing = [&](label a, label b) -> label
    {
        auto it = crossings.find(edgeKey(a, b));
        return (it == crossings.end()) ? -1 : it->second;
    };
    auto regionOfFace = [&](const std::vector<label>& poly) -> label
    {
        std::unordered_map<label, label> votes;
        label best = -1, bestN = 0;
        for (label v : poly)
        {
            auto it = pointRegion.find(v);
            if (it == pointRegion.end()) { continue; }
            const label n = ++votes[it->second];
            if (n > bestN) { bestN = n; best = it->second; }
        }
        if (best >= 0) { return best; }
        vector nn; point cc;
        polygonNormalCentre(m.points, poly, nn, cc);
        label reg;
        if (surfaces.solidShellDistance(sh, cc, mag(sbb.span()), reg) < GREAT && reg >= 0)
        {
            return reg;
        }
        return nearestRegion(poly);
    };
    auto trisInBox = [&](const boundBox& bb, std::vector<dcCartSurfaces::triInfo>& out)
    {
        surfaces.solidShellTrianglesInBox(sh, bb, out);
    };
    std::function<bool(label, const std::vector<label>&, const point&,
        std::vector<std::pair<std::vector<label>, label>>&, std::vector<chordInsert>&)> splitCap =
        [&](label, const std::vector<label>& loop, const point& fluidCentre, auto& pieces, auto& inserts) -> bool
    {
        boundBox bb(boundBox::invertedBox);
        for (label v : loop) { bb.add(m.points[v]); }
        if (!bb.valid()) { return false; }
        bb.inflate(0.5);
        return splitCapByTriangles(m, side, trisInBox, bb, loop, fluidCentre, pieces, inserts, tol);
    };

    // ---- feature pre-split ---------------------------------------------
    // A cell holding a convex edge of the solid (two facets > 20° apart)
    // would be cut into an L-shaped, concave cell whose centre sits
    // behind one facet (checkMesh's face-pyramid error), or — with one
    // tilted cap — into a chamfered one. Instead the cell is first split
    // by one facet's plane (a plain internal face where that plane runs
    // past the facet); each half is then cut by one facet only, so the
    // caps are planar, the edge is exact and the halves are convex.
    // Feature pierce points: the lines where two facet planes of this
    // shell (normals > 20° apart) meet, intersected with the axis
    // plane a chord's face lies in, kept when they fall between the
    // chord's ends and ON the surface.
    std::function<void(label, label, std::vector<point>&)> pierce =
        [&](label a, label b, std::vector<point>& out)
    {
        out.clear();
        const point& pa = m.points[a]; const point& pb = m.points[b];
        int ax = -1;
        for (int k = 0; k < 3; ++k) { if (std::abs(pa[k] - pb[k]) < 1e-9) { ax = k; break; } }
        if (ax < 0) { return; }
        const vector ab = pb - pa;
        const scalar l2 = magSqr(ab);
        if (l2 < 1e-20) { return; }
        boundBox bb(pa, pa); bb.add(pb);
        bb.inflate(0.5);
        std::vector<dcCartSurfaces::triInfo> tris;
        surfaces.solidShellTrianglesInBox(sh, bb, tris);
        std::vector<std::pair<vector, point>> planes;
        for (const auto& t : tris)
        {
            bool dup = false;
            for (const auto& pl : planes)
            {
                if ((pl.first & t.normal) > 0.94 && std::abs((t.p0 - pl.second) & pl.first) < 1e-6) { dup = true; break; }
            }
            if (!dup) { planes.emplace_back(t.normal, t.p0); }
        }
        std::vector<std::pair<scalar, point>> found;
        for (size_t i = 0; i < planes.size(); ++i)
        {
            for (size_t j = i + 1; j < planes.size(); ++j)
            {
                const vector& n1 = planes[i].first; const vector& n2 = planes[j].first;
                if (std::abs(n1 & n2) > 0.94) { continue; }
                const vector d = n1 ^ n2;
                const scalar dm = mag(d);
                if (dm < 1e-9) { continue; }
                const scalar h1 = (planes[i].second - pa) & n1;
                const scalar h2 = (planes[j].second - pa) & n2;
                const scalar n12 = n1 & n2;
                const scalar det = 1 - n12 * n12;
                const point p0 = pa + ((h1 - h2 * n12) / det) * n1 + ((h2 - h1 * n12) / det) * n2;
                const vector du = d / dm;
                if (std::abs(du[ax]) < 1e-9) { continue; }   // line in the face plane
                const scalar tt = (pa[ax] - p0[ax]) / du[ax];
                point x = p0 + tt * du;
                x[ax] = pa[ax];
                const scalar t = ((x - pa) & ab) / l2;
                if (t < 1e-4 || t > 1 - 1e-4) { continue; }
                // lateral offset from the chord: a real bend, not a rounding
                const scalar off2 = magSqr((x - pa) - t * ab);
                if (off2 < 1e-16 || off2 > l2) { continue; }
                // and on the surface (the two planes' line may leave the solid)
                label reg;
                if (surfaces.solidShellDistance(sh, x, 0.01, reg) > 1e-5) { continue; }
                bool dupx = false;
                for (const auto& fx : found) { if (magSqr(fx.second - x) < 1e-14) { dupx = true; break; } }
                if (!dupx) { found.emplace_back(t, x); }
            }
        }
        std::sort(found.begin(), found.end(), [](const auto& u, const auto& v) { return u.first < v.first; });
        for (const auto& fx : found) { out.push_back(fx.second); }
    };

    std::vector<bool> splitOut;
    passArgs args;
    args.capBoundary = true;
    args.eligible = &eligible;
    args.splitCap = &splitCap;
    args.splitOut = &splitOut;
    args.countRank = s.myRank;
    if (getenv("DCCART_PIERCE")) { args.pierce = &pierce; }
    args.onIsA = false;
    std::function<bool(label, vector&)> hitNormal = [&](label x, vector& n) -> bool
    {
        auto it = pointNormal.find(x);
        if (it == pointNormal.end()) { return false; }
        n = it->second;
        return true;
    };
    args.hitNormal = &hitNormal;
    args.planeTol = 4 * tol;
    std::vector<bool> wasInternal(m.faces.size(), false);
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        wasInternal[f] = m.faceAlive[f] && m.neighbour[f] >= 0;
    }
    clipPass(m, side, crossing, existing, regionOfFace, args, r.nCarveCut, r.nCarveFallback, r.nCapFaces);
    for (label c = 0; c < label(splitOut.size()) && c < label(everSplit.size()); ++c)
    {
        if (splitOut[c]) { everSplit[c] = true; }
    }

    // A face that became boundary in this pass without being cut (a grid
    // face lying on the surface, or the in-plane half of a cell the feature
    // pre-split cut along a server's face) is named whole by its vertices'
    // votes, so a region boundary running through it (a fan opening and the
    // wall round it) jumps to the face's edge. Tri_angle (2026-09-23):
    // Rack_2's outlet_wall took a 2.47 mm strip of outlet_p0 on every row,
    // up to the grid plane x = 3.49688. Such a face is split by the shell's
    // triangles exactly as a cap is; the pieces' corners on its edges are
    // hanging vertices, as a cap's are.
    //
    // On an axis-aligned face, unless a piece would be a face checkMesh
    // cannot pass: a strip at the side of a thin cut cell lies far to the
    // side of the cell centre's foot, and the merges do not remove it.
    // Base604 (945 axis-aligned servers, the p0 / wall line a few mm from a
    // grid line) came out with 29 boundary faces of skewness up to 9. There
    // the owner's worst boundary skewness (as checkMesh measures it, the way
    // the merges gate on it) may not rise above the merge limit; where it
    // would, the face stays whole and the boundary stays on the grid line
    // (944 of Base604's 4286 faces; the fan openings' area error per server
    // went from 5.1 % mean / 17.9 % max to 0.7 % / 8.0 %). A tilted face is
    // not held back: its owner is still a pre-merge sliver here, whose
    // skewness the merges settle — the guard refused all 12 of Tri_angle's,
    // and the mesh with them has a worst skewness of 3.98.
    std::vector<std::vector<label>> cellFacesIP;
    bool haveCellFacesIP = false;
    auto geoIP = [&](label g, vector& a, point& cc) { faceAreaCentreOF(m.points, m.faces[g], a, cc); };
    for (label f = 0; f < label(wasInternal.size()); ++f)
    {
        if (!wasInternal[f] || !m.faceAlive[f] || m.neighbour[f] >= 0 || m.patch[f] < 0) { continue; }
        bool allOn = true;
        for (label v : m.faces[f])
        {
            if (v >= label(side.size()) || side[v] != 0) { allOn = false; break; }
        }
        if (!allOn) { continue; }
        bool axisAligned = false;
        {
            vector fn; point fc;
            polygonNormalCentre(m.points, m.faces[f], fn, fc);
            const scalar fa = mag(fn);
            if (fa < VSMALL) { continue; }
            fn /= fa;
            const scalar big = std::max(std::abs(fn.x()), std::max(std::abs(fn.y()), std::abs(fn.z())));
            axisAligned = big > 0.9999;
        }
        std::vector<std::pair<std::vector<label>, label>> pieces;
        std::vector<chordInsert> inserts;
        const std::vector<label> loop = m.faces[f];
        const label c = m.owner[f];
        const label nP0 = label(m.points.size());
        if (!splitCap(c, loop, point::zero, pieces, inserts) || pieces.size() < 2) { continue; }
        if (!haveCellFacesIP)
        {
            buildCellFaces(m, cellFacesIP);
            haveCellFacesIP = true;
        }
        if (c >= label(cellFacesIP.size())) { cellFacesIP.resize(c + 1); }
        const scalar before = axisAligned ? boundarySkewT(m, cellFacesIP, c, label(-1), geoIP) : 0;
        const label patch0 = m.patch[f];
        const label nF0 = label(m.faces.size());
        m.faces[f] = pieces[0].first;
        m.patch[f] = pieces[0].second;
        for (size_t k = 1; k < pieces.size(); ++k)
        {
            m.faces.push_back(pieces[k].first);
            m.owner.push_back(c);
            m.neighbour.push_back(-1);
            m.patch.push_back(pieces[k].second);
            m.zone.push_back(-1);
            m.faceAlive.push_back(true);
            cellFacesIP[c].push_back(label(m.faces.size()) - 1);
        }
        const scalar after = axisAligned ? boundarySkewT(m, cellFacesIP, c, label(-1), geoIP) : 0;
        if (axisAligned && after > std::max(before, s.mergeMaxSkew))
        {
            // back to the whole face, named as before
            m.faces[f] = loop;
            m.patch[f] = patch0;
            m.faces.resize(nF0);
            m.owner.resize(nF0);
            m.neighbour.resize(nF0);
            m.patch.resize(nF0);
            m.zone.resize(nF0);
            m.faceAlive.resize(nF0);
            cellFacesIP[c].resize(cellFacesIP[c].size() - (pieces.size() - 1));
            m.points.resize(nP0);
            side.resize(nP0);
            continue;
        }
        if (c < label(m.cellRank.size()) && m.cellRank[c] == s.myRank) { r.nCapFaces += label(pieces.size()) - 1; }
    }
}


void Foam::dcCartCutCells::cutCarving
(
    cutMesh& m,
    const dcCartSurfaces& surfaces,
    const settings& s,
    report& r
)
{
    // One clip pass per SOLID SHELL (a connected carving surface: a box, a
    // pipe, the four room walls). Every point is asked which side of THAT
    // shell it lies on, so a corner lying on one surface while inside
    // another is inside — the global "on any surface" label used to make it
    // ON, the face in the wall plane never split, and the cut loop could
    // not close. A cell crossed by several shells is clipped several times,
    // and gets a cap per shell, each split by that shell's regions.
    const label nCells0 = m.nCells;
    std::vector<bool> everSplit(nCells0, false);
    // side w.r.t. EVERY shell seen so far: +1 fluid for all of them, 0 on
    // one of them, -1 inside one of them (points made later are on a cut)
    std::vector<signed char> gside(m.points.size(), 1);

    auto nearestRegion = [&](const std::vector<label>& poly) -> label
    {
        vector nn; point cc;
        polygonNormalCentre(m.points, poly, nn, cc);
        const label reg = surfaces.nearestCarvingRegion(cc);
        return reg >= 0 ? reg : 0;
    };

    // Each shell's pass runs on the cells within its reach
    // (cutNeighbourhood::reachOfBox), cut by topology, not by a distance:
    // the live cells overlapping the shell's box, every live cell overlapping
    // their joint box (which holds the cells sharing a vertex with them:
    // anchors, splices, ON neighbours) and two face rings beyond those
    // (borrowed anchors). Every point in the shell's box comes too, for the
    // coincident-point merge.
    cutNeighbourhood nbh(m);
    label nLocalCells = 0, nPassCells = 0;
    // DCCART_PROFILE: where a shell pass spends its time. The room shell's
    // reach is the whole mesh, so its extract / writeBack is a copy of
    // everything for nothing; the timers say whether that is what the stage
    // costs before anyone optimises the clipping itself.
    static const bool profCarve = (getenv("DCCART_PROFILE") != nullptr);
    clockTime profClock;
    scalar tReach = 0, tExtract = 0, tCarve = 0, tWrite = 0, tDedup = 0;
    scalar profStart = profClock.elapsedTime();
    // The whole-mesh pass deduplicated repeated vertex ids on EVERY face after
    // each shell's merge: a face a pass left with a repeat was cleaned at the
    // next shell, wherever that shell was. A face a pass writes back is dirty
    // until then; the next shell cleans the dirty faces outside its
    // neighbourhood itself (its own pass cleans those inside, at the moment
    // the whole-mesh pass did).
    std::vector<label> dirty;
    auto dedupFace = [&](label f)
    {
        if (!m.faceAlive[f]) { return; }
        std::vector<label>& poly = m.faces[f];
        std::vector<label> out;
        out.reserve(poly.size());
        for (label v : poly) { if (out.empty() || out.back() != v) { out.push_back(v); } }
        while (out.size() > 1 && out.front() == out.back()) { out.pop_back(); }
        if (out.size() != poly.size()) { poly.swap(out); }
        if (poly.size() < 3) { m.faceAlive[f] = false; }
    };

    // Progress: a large room carves thousands of shells for tens of minutes
    // with nothing else in the log, which is indistinguishable from a hang
    // (Base604 on 23.5 M leaves: 2555 s of silence). Every rank walks the same
    // shells, so the master's count is the job's.
    const label nShells = surfaces.nSolidShells();
    clockTime progressClock;
    scalar lastProgress = 0;
    for (label sh = 0; sh < nShells; ++sh)
    {
        if (progressClock.elapsedTime() - lastProgress >= 30)
        {
            lastProgress = progressClock.elapsedTime();
            Info<< "Cut cells: carving shell " << sh << " of " << nShells
                << ", " << label(lastProgress) << " s" << endl;
        }
        boundBox sbb(surfaces.solidShellBounds(sh));
        if (!sbb.valid()) { continue; }
        sbb.inflate(0.02);

        std::vector<label> reach;
        const scalar reachStart = profClock.elapsedTime();
        nbh.reachOfBox(sbb, reach);
        tReach += profClock.elapsedTime() - reachStart;
        // (no cell reaches the shell: the whole-mesh pass found nothing to do
        // either — its pre-split and point merge act inside the box — apart
        // from the dedup sweep)
        if (reach.empty())
        {
            for (label f : dirty) { dedupFace(f); }
            dirty.clear();
            // The owner-side exchange inside the pass is COLLECTIVE: another
            // rank may have reach for this shell and would wait forever for
            // this rank's (empty) band. Participate with an empty mesh.
            if (Pstream::parRun())
            {
                cutMesh dummy;
                std::vector<signed char> noSide;
                syncSideOwners(dummy, noSide);
            }
            continue;
        }
        cutNeighbourhood::part P;
        scalar t0 = profClock.elapsedTime();
        nbh.extractCells(reach, &sbb, P);
        tExtract += profClock.elapsedTime() - t0;
        t0 = profClock.elapsedTime();
        for (label f : dirty) { if (!nbh.faceIn(P, f)) { dedupFace(f); } }
        dirty.clear();
        tDedup += profClock.elapsedTime() - t0;
        nLocalCells += P.nC; ++nPassCells;
        if (profCarve)
        {
            Pout<< "  [carve] shell " << sh << " reach " << label(reach.size())
                << " cells, local " << P.nC << " cells " << P.nP << " points "
                << label(P.lm.faces.size()) << " faces" << nl;
        }

        std::vector<bool> everSplitL(P.nC, false), origSlotL(P.nC, false);
        for (label i = 0; i < P.nC; ++i)
        {
            const label c = P.cells[i];
            origSlotL[i] = (c < nCells0);
            everSplitL[i] = (c < nCells0) && everSplit[c];
        }
        std::vector<signed char> gsideL(P.nP, 0);
        for (label i = 0; i < P.nP; ++i) { gsideL[i] = (P.points[i] < label(gside.size())) ? gside[P.points[i]] : 0; }

        t0 = profClock.elapsedTime();
        carveShellPass(P.lm, surfaces, s, r, sh, sbb, everSplitL, gsideL, origSlotL);
        tCarve += profClock.elapsedTime() - t0;
        // One shell can hold the stage by itself (Base604: a single pass of
        // ~5 min between two progress lines), so say which one it was.
        if (profClock.elapsedTime() - t0 >= 30)
        {
            Info<< "Cut cells: shell " << sh << " of " << nShells << " took "
                << label(profClock.elapsedTime() - t0) << " s (" << label(reach.size())
                << " cells in reach on the master)" << endl;
        }

        // the carving's own arrays first (the points and cells the pass made
        // are appended by writeBack in the order the pass made them)
        const label newPointBase = label(m.points.size());
        gside.resize(newPointBase + (label(P.lm.points.size()) - P.nP), 0);
        for (label l = 0; l < label(P.lm.points.size()) && l < label(gsideL.size()); ++l)
        {
            gside[(l < P.nP) ? P.points[l] : newPointBase + (l - P.nP)] = gsideL[l];
        }
        for (label l = 0; l < P.nC; ++l)
        {
            if (P.cells[l] < nCells0) { everSplit[P.cells[l]] = everSplitL[l]; }
        }
        t0 = profClock.elapsedTime();
        nbh.writeBack(P, &dirty);
        tWrite += profClock.elapsedTime() - t0;

        // ---- rank-symmetry fingerprint (DCCART_DEBUG_BOX, temporary) ------
        // Per-leaf XOR of fragment geometry hashes for every live cell the
        // debug box touches, after each shell pass: diff the FPSH lines of
        // two ranks to find the first shell whose pass diverges.
        if (getenv("DCCART_DEBUG_BOX"))
        {
            double b[6];
            if (sscanf(getenv("DCCART_DEBUG_BOX"), "%lf,%lf,%lf,%lf,%lf,%lf",
                       &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) == 6)
            {
                const boundBox dbg(point(b[0], b[1], b[2]), point(b[3], b[4], b[5]));
                std::vector<std::vector<label>> cfDbg;
                buildCellFaces(m, cfDbg);
                std::map<std::pair<uint64_t, uint64_t>, uint64_t> byLeaf;
                for (label c = 0; c < m.nCells; ++c)
                {
                    if (!m.cellAlive[c]) { continue; }
                    boundBox cb(boundBox::invertedBox);
                    for (const label f : cfDbg[c])
                    {
                        if (!m.faceAlive[f]) { continue; }
                        for (const label v : m.faces[f]) { cb.add(m.points[v]); }
                    }
                    if (!cb.valid() || !cb.overlaps(dbg)) { continue; }
                    uint64_t fh = 0;
                    for (const label f : cfDbg[c])
                    {
                        if (!m.faceAlive[f]) { continue; }
                        std::vector<std::tuple<long long, long long, long long>> qs;
                        for (const label v : m.faces[f])
                        {
                            const point& x = m.points[v];
                            qs.emplace_back
                            (
                                (long long)std::llround(x.x() * 1e7),
                                (long long)std::llround(x.y() * 1e7),
                                (long long)std::llround(x.z() * 1e7)
                            );
                        }
                        std::sort(qs.begin(), qs.end());
                        uint64_t h = 0xcbf29ce484222325ULL;
                        for (const auto& q : qs)
                        {
                            h = (h ^ (uint64_t(std::get<0>(q)) + 0x9E3779B97F4A7C15ULL)) * 0x100000001b3ULL;
                            h = (h ^ (uint64_t(std::get<1>(q)) + 0x632BE59BD9B4E019ULL)) * 0x100000001b3ULL;
                            h = (h ^ (uint64_t(std::get<2>(q)) + 0x8CB92BA72F3D8DD7ULL)) * 0x100000001b3ULL;
                        }
                        fh += splitMix64(h ^ uint64_t(m.faces[f].size()));
                    }
                    const auto key = std::make_pair
                    (
                        uint64_t(m.cellLeaf[c]),
                        uint64_t(m.cellLevel[c])
                    );
                    byLeaf[key] ^= splitMix64(fh ^ 0xD6E8FEB86659FD93ULL);
                }
                if (Pstream::myProcNo() == 0 || getenv("DCCART_FPSH_ALL"))
                {
                    const boundBox shbb(surfaces.solidShellBounds(sh));
                    Pout<< "FPSHBOX shell " << sh
                        << " bb " << shbb.min() << " " << shbb.max()
                        << " closed " << (surfaces.solidShellClosed(sh) ? 1 : 0)
                        << " reach " << label(reach.size()) << nl;
                }
                for (const auto& kv : byLeaf)
                {
                    Pout<< "FPSH rank " << Pstream::myProcNo()
                        << " shell " << sh
                        << " leaf " << kv.first.first
                        << " lvl " << kv.first.second
                        << " h " << kv.second << nl;
                }
                double bc[6] = {0, 0, 0, 0, 0, 0};
                const bool traceOne =
                    getenv("DCCART_DEBUG_CELL")
                 && sscanf(getenv("DCCART_DEBUG_CELL"), "%lf,%lf,%lf,%lf,%lf,%lf",
                           &bc[0], &bc[1], &bc[2], &bc[3], &bc[4], &bc[5]) == 6;
                const boundBox dbgOne
                (
                    point(bc[0], bc[1], bc[2]),
                    point(bc[3], bc[4], bc[5])
                );
                for (label c = 0; c < m.nCells; ++c)
                {
                    boundBox cb(boundBox::invertedBox);
                    for (const label f : cfDbg[c])
                    {
                        if (!m.faceAlive[f]) { continue; }
                        for (const label v : m.faces[f]) { cb.add(m.points[v]); }
                    }
                    if (traceOne && cb.valid() && cb.overlaps(dbgOne))
                    {
                        label nAliveF = 0;
                        for (const label f : cfDbg[c]) { if (m.faceAlive[f]) { ++nAliveF; } }
                        Pout<< "FPT rank " << Pstream::myProcNo()
                            << " shell " << sh
                            << " cell " << c
                            << " leaf " << m.cellLeaf[c]
                            << " piece " << m.cellPiece[c]
                            << " alive " << (m.cellAlive[c] ? 1 : 0)
                            << " nfc " << cfDbg[c].size()
                            << " nfa " << nAliveF
                            << " bb " << cb.min() << " " << cb.max() << nl;
                        continue;
                    }
                    if (!m.cellAlive[c]) { continue; }
                    if (!cb.valid() || !cb.overlaps(dbg)) { continue; }
                    Pout<< "FPSC rank " << Pstream::myProcNo()
                        << " shell " << sh
                        << " leaf " << m.cellLeaf[c]
                        << " piece " << m.cellPiece[c]
                        << " lvl " << m.cellLevel[c]
                        << " bb " << cb.min() << " " << cb.max()
                        << " nfc " << cfDbg[c].size() << nl;
                }
            }
        }
    }
    if (profCarve)
    {
        Pout<< "  [carve] totals: reach " << tReach << " s, extract " << tExtract << " s, clip " << tCarve
            << " s, writeBack " << tWrite << " s, dedup " << tDedup
            << " s, wall " << (profClock.elapsedTime() - profStart) << " s" << nl;
    }
    if (getenv("DCCART_DEBUG"))
    {
        Info<< "  carve passes on " << nPassCells << " shells, " << nLocalCells << " neighbourhood cells in all" << nl;
    }

    // A cell the stair-step carve had dropped (its centre is in the solid)
    // was brought back because a corner of it is fluid. If no shell could
    // split it — the solid is thinner than the cell and holds no corner,
    // a rack's 2 cm base plate under a 2.5 cm cell — keeping the whole hex
    // would swallow the plate: its top face vanishes and its bottom face is
    // claimed by the floor. Drop it again: the plate becomes one cell thick,
    // which is what the stair-step mesh did, and the patch stays closed.
    // Likewise a pre-split half that lies wholly beyond the facet plane
    // (its vertices are ON the plane or in the solid, no shell had a
    // crossing left to find in it) is solid: keeping it plants a fluid hex
    // inside the column and hands its side faces to the column's patch.
    // Any cell that is not an untouched original fluid cell and has no
    // strictly fluid vertex goes.
    label nKilled = 0, nKilledOwned = 0;
    {
        std::vector<std::vector<label>> cf;
        buildCellFaces(m, cf);
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!m.cellAlive[c]) { continue; }
            const bool untouchedOrig = (c < nCells0 && m.cellOrigAlive[c] && !everSplit[c]);
            if (untouchedOrig) { continue; }
            // no strictly fluid vertex and at least one strictly solid
            // one (a pre-split plane point is ON the plane, not on the
            // surface: a quarter piece made of plane points and one corner
            // on the floor is fluid and stays)
            bool anyFluid = false, anySolid = false;
            for (label f : cf[c])
            {
                for (label v : m.faces[f])
                {
                    if (v >= label(gside.size())) { continue; }
                    if (gside[v] > 0) { anyFluid = true; break; }
                    if (gside[v] < 0) { anySolid = true; }
                }
                if (anyFluid) { break; }
            }
            if (!anyFluid && anySolid)
            {
                m.cellAlive[c] = false;
                ++nKilled;
                if (m.cellRank[c] == s.myRank) { ++nKilledOwned; }
                if (getenv("DCCART_DEBUG_BOX"))
                {
                    double b[6];
                    if (sscanf(getenv("DCCART_DEBUG_BOX"), "%lf %lf %lf %lf %lf %lf", b, b+1, b+2, b+3, b+4, b+5) == 6)
                    {
                        const boundBox bb(point(b[0], b[1], b[2]), point(b[3], b[4], b[5]));
                        point cen = point::zero; label nv = 0;
                        for (label f : cf[c]) { for (label v : m.faces[f]) { cen += m.points[v]; ++nv; } }
                        if (nv && bb.contains(cen / nv))
                        {
                            Info<< "  killed cell " << c << " at " << cen / nv << " (no fluid vertex):";
                            for (label f : cf[c]) { for (label v : m.faces[f]) { Info<< " " << label(v < label(gside.size()) ? gside[v] : 9); } Info<< " |"; }
                            Info<< nl;
                        }
                    }
                }
            }
        }
    }
    if (nKilled) { dropDeadFaces(m, nearestRegion); }
    r.nReadmitKilled += nKilledOwned;
}


// * * * * * * * * * * * * * relabelCoplanarPatches * * * * * * * * * * * * //

Foam::label Foam::dcCartCutCells::relabelCoplanarPatches
(
    cutMesh& m,
    const dcCartSurfaces& surfaces,
    const settings& s
)
{
    label n = 0;
    const scalar radius = 4 * std::max(s.onToleranceCarve, s.onTolerance);
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f] || m.neighbour[f] >= 0 || m.patch[f] < 0 || m.zone[f] >= 0) { continue; }
        vector nn; point cc;
        polygonNormalCentre(m.points, m.faces[f], nn, cc);
        // (the current patch wins when its own surface runs through the
        // face: a cap named by its crossings is right as it is)
        const label reg = surfaces.coplanarCarvingRegion(cc, nn, radius, m.patch[f]);
        if (getenv("DCCART_DEBUG_BOX"))
        {
            double b[6];
            if (sscanf(getenv("DCCART_DEBUG_BOX"), "%lf %lf %lf %lf %lf %lf", b, b+1, b+2, b+3, b+4, b+5) == 6
             && boundBox(point(b[0], b[1], b[2]), point(b[3], b[4], b[5])).contains(cc))
            {
                Info<< "  relabel face " << f << " at " << cc << " n " << nn / (mag(nn) + VSMALL)
                    << " patch " << surfaces.patchNames()[m.patch[f]] << " -> "
                    << (reg >= 0 ? surfaces.patchNames()[reg] : word("none")) << nl;
            }
        }
        if (reg < 0 || reg == m.patch[f]) { continue; }
        m.patch[f] = reg;
        if (m.cellRank[m.owner[f]] == s.myRank) { ++n; }
    }
    return n;
}


// * * * * * * * * * * * * * * * * cutBaffles  * * * * * * * * * * * * * * * //

// * * * * * * * * * * * * * * baffleEdgePreSplit * * * * * * * * * * * * * //

namespace
{

//- p inside triangle t grown by tol, in the plane of unit normal nu
bool inSheetTriangle
(
    const Foam::point& p,
    const Foam::dcCartSurfaces::triInfo& t,
    const Foam::vector& nu,
    const Foam::scalar tol
)
{
    using namespace Foam;
    const point* v[3] = {&t.p0, &t.p1, &t.p2};
    const scalar orient = ((((t.p1 - t.p0) ^ (t.p2 - t.p0)) & nu) >= 0) ? 1 : -1;
    for (int k = 0; k < 3; ++k)
    {
        const point& a = *v[k];
        const vector e = *v[(k + 1) % 3] - a;
        const scalar le = mag(e);
        if (le < VSMALL) { return false; }
        if (orient * ((e ^ (p - a)) & nu) / le < -tol) { return false; }
    }
    return true;
}

//- The ON band of an exact-outline sheet (partitions, blanking panels,
//  wall baffles, the rack door). It was 1e-6 m, so in-plane faces keep the
//  sheet's own outline and the coverage gate measures it exactly; but a
//  grid corner a few microns off the plane was then cut off as a sliver.
//  Tri_angle's merged_perforatedBlankingPanel_8U (2026-09-23) passes 5.9 um
//  from the grid corner (5.30188, y, 5.08508) of the rotated Rack_1: the cut
//  left 3.5e-11 m2 faces, the merges folded them into 13-face concave
//  cells, and checkMesh read 5 wrong-oriented faces and a skewness of
//  114951. A thousandth of the finest cell (a hundredth of onTolerance)
//  makes such a corner ON — the cut runs through it — and still keeps
//  every in-plane face within tens of microns of the sheet.
Foam::scalar exactOutlinePlaneTol(const Foam::dcCartCutCells::settings& s)
{
    return std::min(s.onTolerance, std::max(Foam::scalar(1e-6), Foam::scalar(1e-2) * s.onTolerance));
}

//- Segment a-b meets box bb grown by tol
bool segmentHitsBox
(
    const Foam::point& a, const Foam::point& b, const Foam::boundBox& bb, Foam::scalar tol
)
{
    using namespace Foam;
    scalar t0 = 0, t1 = 1;
    for (direction d = 0; d < 3; ++d)
    {
        const scalar lo = bb.min()[d] - tol, hi = bb.max()[d] + tol;
        const scalar dd = b[d] - a[d];
        if (std::abs(dd) < VSMALL)
        {
            if (a[d] < lo || a[d] > hi) { return false; }
            continue;
        }
        scalar u0 = (lo - a[d]) / dd, u1 = (hi - a[d]) / dd;
        if (u0 > u1) { std::swap(u0, u1); }
        t0 = std::max(t0, u0);
        t1 = std::min(t1, u1);
        if (t0 > t1) { return false; }
    }
    return true;
}

} // End anonymous namespace


void Foam::dcCartCutCells::baffleEdgePreSplit
(
    cutMesh& m,
    const dcCartSurfaces& surfaces,
    const settings& s,
    report& r,
    label sh,
    std::vector<bool>* edgeCells,
    bool exactOutline
)
{
    if (edgeCells) { edgeCells->assign(m.nCells, false); }
    // A sheet's pass cuts a cell by the sheet's whole PLANE and then trims
    // the cap to the triangles: where a FREE edge (no other triangle of the
    // sheet beyond it, no other sheet meeting it) runs through a cell, the
    // two halves share a covered piece and a phantom piece. Near a cell
    // face the thinner piece is a strip whose centre the centre line misses
    // (skewness 4-5 as an internal face) and, once createBaffles has made
    // the covered piece a wall, whose owner's centre lies far to its side.
    // Splitting the cell first by the plane through the edge normal to the
    // sheet puts the two pieces in different cells: every cap is covered or
    // phantom whole, and the strip cell merges like any sliver.
    const dcCartSurfaces::baffleSheet& bs = surfaces.baffleSheetInfo(sh);
    const scalar edgeTolerance = exactOutline ? min(s.onToleranceCarve, exactOutlinePlaneTol(s)) : s.onToleranceCarve;
    if (!bs.bounds.valid() || s.baffleEdgeSplitFraction <= 0) { return; }
    const vector nu = bs.normal / max(mag(bs.normal), VSMALL);
    boundBox sb(bs.bounds);
    sb.grow(1e-6);
    std::vector<dcCartSurfaces::triInfo> tris, nearTris, edgeTris;
    surfaces.baffleSheetTrianglesInBox(sh, sb, tris);
    if (tris.empty()) { return; }

    auto keyOf = [](const point& p)
    {
        return std::make_tuple
        (
            (long long)std::llround(p.x() * 1e8),
            (long long)std::llround(p.y() * 1e8),
            (long long)std::llround(p.z() * 1e8)
        );
    };
    auto covered = [&](const point& p) -> bool
    {
        boundBox pb(p, p);
        pb.grow(1e-6);
        surfaces.baffleSheetTrianglesInBox(sh, pb, nearTris);
        for (const auto& t : nearTris) { if (inSheetTriangle(p, t, nu, 1e-9)) { return true; } }
        return false;
    };
    // on another sheet: a junction, whose other sheet's own pass separates
    // the two sides of the edge
    const scalar tolJ = max(s.onTolerance, scalar(1e-6));
    auto onOtherSheet = [&](const point& p) -> bool
    {
        boundBox pb(p, p);
        pb.grow(tolJ);
        for (label s2 = 0; s2 < surfaces.nBaffleSheets(); ++s2)
        {
            if (s2 == sh || !surfaces.baffleSheetInfo(s2).bounds.overlaps(pb)) { continue; }
            surfaces.baffleSheetTrianglesInBox(s2, pb, nearTris);
            for (const auto& t : nearTris)
            {
                if (pointTriDist2(p, t.p0, t.p1, t.p2) <= sqr(tolJ)) { return true; }
            }
        }
        return false;
    };

    struct edgeSeg { point a, b; vector pn; scalar off; };
    std::vector<edgeSeg> segs;
    std::set<std::pair<std::tuple<long long, long long, long long>, std::tuple<long long, long long, long long>>> seen;
    for (const auto& t : tris)
    {
        const point* v[3] = {&t.p0, &t.p1, &t.p2};
        for (int k = 0; k < 3; ++k)
        {
            const point a = *v[k];
            const point b = *v[(k + 1) % 3];
            const vector ab = b - a;
            const scalar l2 = magSqr(ab);
            if (l2 < 1e-18) { continue; }
            // the sheet's vertices on this edge cut it where the coverage
            // beyond it can change (a T-junction with the next triangle)
            boundBox eb(a, a);
            eb.add(b);
            eb.grow(1e-6);
            surfaces.baffleSheetTrianglesInBox(sh, eb, edgeTris);
            std::vector<scalar> us{0, 1};
            for (const auto& t2 : edgeTris)
            {
                for (const point* q : {&t2.p0, &t2.p1, &t2.p2})
                {
                    const scalar u = ((*q - a) & ab) / l2;
                    if (u <= 1e-9 || u >= 1 - 1e-9) { continue; }
                    if (magSqr(*q - (a + u * ab)) > 1e-14) { continue; }
                    us.push_back(u);
                }
            }
            std::sort(us.begin(), us.end());
            us.erase(std::unique(us.begin(), us.end(), [](scalar x, scalar y) { return std::abs(x - y) < 1e-9; }), us.end());
            for (size_t i = 0; i + 1 < us.size(); ++i)
            {
                const point p = a + us[i] * ab;
                const point q = a + us[i + 1] * ab;
                auto kp = keyOf(p), kq = keyOf(q);
                if (kq < kp) { std::swap(kp, kq); }
                if (!seen.emplace(kp, kq).second) { continue; }
                const vector pq = q - p;
                const scalar lpq = mag(pq);
                if (lpq < 1e-6) { continue; }
                vector e = nu ^ pq;
                const scalar me = mag(e);
                if (me < VSMALL) { continue; }
                e /= me;
                const point mid = 0.5 * (p + q);
                const scalar delta = min(scalar(1e-5), 0.01 * lpq);
                if (covered(mid + delta * e) == covered(mid - delta * e)) { continue; }
                if (!exactOutline && onOtherSheet(mid))
                {
                    // A junction hands the split to the other sheet's own
                    // pass only while that sheet runs ACROSS the edge. Where
                    // two sheets merely abut -- Base604's raised floor has
                    // Room_Raising_Floor_part1 ending at x=1.200 exactly where
                    // Room_Raising_Floor_patch_3 begins -- each sheet finds the
                    // other on the edge and neither splits. The cell below the
                    // junction then keeps a 13 mm strip of one zone beside
                    // 72 mm of the other, createBaffles turns them into two
                    // coplanar wall faces of one cell, and checkMesh scores
                    // the strip 5.01 against its limit of 4. Probe both sides
                    // and split whenever the other sheet does not cross.
                    const scalar dProbe = max(delta, 2 * tolJ);
                    if (onOtherSheet(mid + dProbe * e) && onOtherSheet(mid - dProbe * e)) { continue; }
                }
                vector pn = e;
                label big = 0;
                for (direction d = 1; d < 3; ++d) { if (std::abs(pn[d]) > std::abs(pn[big])) { big = d; } }
                if (pn[big] < 0) { pn = -pn; }
                segs.push_back({p, q, pn, pn & p});
            }
        }
    }
    if (segs.empty()) { return; }

    // one pass per plane (collinear pieces of one edge share it)
    std::map<std::tuple<long, long, long, long>, std::vector<size_t>> byPlane;
    for (size_t k = 0; k < segs.size(); ++k)
    {
        const vector& n = segs[k].pn;
        byPlane
        [
            std::make_tuple
            (
                (long)std::lround(n.x() * 1e4), (long)std::lround(n.y() * 1e4),
                (long)std::lround(n.z() * 1e4), (long)std::lround(segs[k].off * 1e6)
            )
        ].push_back(k);
    }
    std::vector<boundBox> cellBB;
    auto boxes = [&]()
    {
        std::vector<std::vector<label>> cellFaces;
        buildCellFaces(m, cellFaces);
        cellBB.assign(m.nCells, boundBox(boundBox::invertedBox));
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!m.cellAlive[c]) { continue; }
            for (label f : cellFaces[c]) { for (label v : m.faces[f]) { cellBB[c].add(m.points[v]); } }
        }
    };
    boxes();
    label nSplit = 0;
    for (const auto& kv : byPlane)
    {
        const vector pn = segs[kv.second[0]].pn;
        const point po = segs[kv.second[0]].a;
        std::vector<bool> eligible(m.nCells, false);
        label nEligible = 0;
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!m.cellAlive[c] || !cellBB[c].valid()) { continue; }
            const boundBox& bb = cellBB[c];
            bool hit = false;
            for (size_t k : kv.second)
            {
                if (segmentHitsBox(segs[k].a, segs[k].b, bb, 1e-9)) { hit = true; break; }
            }
            if (!hit) { continue; }
            // where the plane runs through the cell, across it
            scalar lo = GREAT, hi = -GREAT;
            for (label i = 0; i < 8; ++i)
            {
                const point corner
                (
                    (i & 1) ? bb.max().x() : bb.min().x(),
                    (i & 2) ? bb.max().y() : bb.min().y(),
                    (i & 4) ? bb.max().z() : bb.min().z()
                );
                const scalar d = (corner - po) & pn;
                lo = min(lo, d);
                hi = max(hi, d);
            }
            if (-lo <= edgeTolerance || hi <= edgeTolerance) { continue; }
            const scalar fraction = exactOutline ? scalar(0.5) : s.baffleEdgeSplitFraction;
            if (min(-lo, hi) >= fraction * (hi - lo)) { continue; }
            eligible[c] = true;
            ++nEligible;
        }
        if (!nEligible) { continue; }
        std::vector<signed char> side(m.points.size(), 1);
        for (label p = 0; p < label(m.points.size()); ++p)
        {
            const scalar d = (m.points[p] - po) & pn;
            side[p] = (std::abs(d) <= edgeTolerance) ? 0 : (d < 0 ? -1 : 1);
        }
        std::unordered_map<uint64_t, label> crossings;
        auto crossing = [&](label a, label b) -> label
        {
            const uint64_t key = edgeKey(a, b);
            auto it = crossings.find(key);
            if (it != crossings.end()) { return it->second; }
            label id = -1;
            const bool aFluid = (side[a] >= 0);
            const label pa = aFluid ? a : b;
            const label pb = aFluid ? b : a;
            if (side[pa] == 0) { id = -2; }
            else
            {
                const scalar da = (m.points[pa] - po) & pn;
                const scalar db = (m.points[pb] - po) & pn;
                scalar t = (da - db != 0) ? da / (da - db) : 0.5;
                t = std::min(std::max(t, scalar(0)), scalar(1));
                id = label(m.points.size());
                m.points.push_back(m.points[pa] + t * (m.points[pb] - m.points[pa]));
                side.push_back(0);
            }
            crossings.emplace(key, id);
            return id;
        };
        auto existing = [&](label a, label b) -> label
        {
            auto it = crossings.find(edgeKey(a, b));
            return (it == crossings.end()) ? -1 : it->second;
        };
        auto regionOfFace = [&](const std::vector<label>&) -> label { return -1; };
        std::vector<bool> splitOut;
        passArgs args;
        args.capBoundary = false;
        args.capZone = -1;   // a plain internal face: nothing joins the two sides back
        args.eligible = &eligible;
        args.splitOut = &splitOut;
        args.onIsA = false;
        args.countRank = s.myRank;
        const label nCellsBefore = m.nCells;
        label nCut = 0, nFallback = 0, nCaps = 0;
        clipPass(m, side, crossing, existing, regionOfFace, args, nCut, nFallback, nCaps);
        nSplit += nCut;
        if (edgeCells)
        {
            edgeCells->resize(m.nCells, true);   // the halves made here
            for (label c = 0; c < label(splitOut.size()); ++c) { if (splitOut[c]) { (*edgeCells)[c] = true; } }
        }
        if (m.nCells != nCellsBefore) { boxes(); }
    }
    r.nBaffleEdgeSplit += nSplit;
    if (getenv("DCCART_DEBUG"))
    {
        Pout<< "  baffle sheet " << sh << " free edge pieces " << label(segs.size())
            << " planes " << label(byPlane.size()) << " cells split " << nSplit << endl;
    }
}


void Foam::dcCartCutCells::baffleSheetPass
(
    cutMesh& m,
    const dcCartSurfaces& surfaces,
    const settings& s,
    report& r,
    label sh,
    const boundBox& sbb
)
{
    // the cells the free-edge split made: the ones it left on the phantom
    // side of the edge are not halved again by the plane beyond the sheet
    std::vector<bool> edgeCells;
    const dcCartSurfaces::baffleSheet& bs = surfaces.baffleSheetInfo(sh);
    const word& zoneName = surfaces.faceZoneNames()[bs.zone];
    const bool exactOutline = zoneName.size() >= 10
        && zoneName.substr(zoneName.size() - 10) == "_partition"
        || zoneName.find("merged_perforatedBlankingPanel_") == 0
        || zoneName.find("merged_wallBaffle_") == 0
        || zoneName.find("merged_wall_baffle") == 0
        || zoneName == "merged_rack_door";
    const scalar planeTolerance = exactOutline ? exactOutlinePlaneTol(s) : s.onTolerance;
    if (s.baffleEdgeSplit)
    {
        baffleEdgePreSplit(m, surfaces, s, r, sh, &edgeCells, exactOutline);
    }
    std::vector<std::vector<label>> cellFaces;
    buildCellFaces(m, cellFaces);
    std::vector<bool> eligible(m.nCells, false);
    std::vector<bool> nearPoint(m.points.size(), false);
    label nEligible = 0;
    for (label c = 0; c < m.nCells; ++c)
    {
        if (!m.cellAlive[c]) { continue; }
        boundBox bb(boundBox::invertedBox);
        for (label f : cellFaces[c])
        {
            for (label v : m.faces[f]) { bb.add(m.points[v]); }
        }
        if (!bb.valid() || !bb.overlaps(sbb)) { continue; }
        bb.inflate(0.02);
        if (!surfaces.baffleSheetOverlaps(sh, bb)) { continue; }
        eligible[c] = true;
        ++nEligible;
        for (label f : cellFaces[c])
        {
            for (label v : m.faces[f]) { nearPoint[v] = true; }
        }
    }
    if (getenv("DCCART_DEBUG_BOX"))
    {
        static double bb6[6];
        static Foam::boundBox dbgB(Foam::boundBox::invertedBox);
        static bool parsed = false;
        if (!parsed)
        {
            parsed = true;
            if (sscanf(getenv("DCCART_DEBUG_BOX"), "%lf %lf %lf %lf %lf %lf",
                       bb6, bb6+1, bb6+2, bb6+3, bb6+4, bb6+5) == 6)
            {
                dbgB = Foam::boundBox(Foam::point(bb6[0], bb6[1], bb6[2]),
                                      Foam::point(bb6[3], bb6[4], bb6[5]));
            }
        }
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!dbgB.contains(m.cellRefCentre[c])) { continue; }
            boundBox cb(boundBox::invertedBox);
            for (label f : cellFaces[c])
            {
                for (label v : m.faces[f]) { cb.add(m.points[v]); }
            }
            Foam::Pout<< "  [sheet " << sh << "] cell " << c
                << " alive " << (m.cellAlive[c] ? 1 : 0)
                << " eligible " << (eligible[c] ? 1 : 0)
                << " ovSheet " << (surfaces.baffleSheetOverlaps(sh, cb) ? 1 : 0)
                << " ovBox " << (cb.valid() && cb.overlaps(sbb) ? 1 : 0)
                << " box " << cb << Foam::nl;
        }
    }
    if (!nEligible) { return; }

    std::vector<signed char> side(m.points.size(), 1);
    for (label p = 0; p < label(m.points.size()); ++p)
    {
        if (!nearPoint[p]) { continue; }
        const scalar d = (m.points[p] - bs.origin) & bs.normal;
        side[p] = (std::abs(d) <= planeTolerance) ? 0 : (d < 0 ? -1 : 1);
    }

    std::unordered_map<uint64_t, label> crossings;
    // An ON point lying ON an edge in a face beside it: a cap-piece corner
    // an earlier sheet cut on its own loop edge (where that sheet's diagonal,
    // run on past the sheet's corner, met the loop edge) is a hanging vertex
    // the faces holding the whole edge do not carry. Within this sheet's ON
    // tolerance it IS the cut in the faces that hold it, and a crossing made
    // on the whole edge 0.7 mm from it gives the cell two cut points on one
    // edge: the cut loop cannot close and the cell kept its stair-step. The
    // cut on such an edge is that vertex (the one nearest the plane, then the
    // lowest id), and the hanging-vertex splice carries it into every face
    // holding the edge.
    std::vector<std::vector<label>> pointFaces(m.points.size());
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        for (label v : m.faces[f])
        {
            if (v < label(nearPoint.size()) && nearPoint[v]) { pointFaces[v].push_back(f); }
        }
    }
    auto onVertexOnEdge = [&](label a, label b) -> label
    {
        const point& pa = m.points[a];
        const vector ab = m.points[b] - pa;
        const scalar l2 = magSqr(ab);
        if (l2 < VSMALL) { return -1; }
        label best = -1;
        scalar bestD = GREAT;
        for (const label end : {a, b})
        {
            if (end >= label(pointFaces.size())) { continue; }
            for (label f : pointFaces[end])
            {
                if (!m.faceAlive[f]) { continue; }
                for (label x : m.faces[f])
                {
                    if (x == a || x == b || x >= label(side.size()) || side[x] != 0) { continue; }
                    const vector dx = m.points[x] - pa;
                    const scalar t = (dx & ab) / l2;
                    if (t <= 1e-6 || t >= 1 - 1e-6 || magSqr(dx - t * ab) > 1e-14) { continue; }
                    const scalar d = std::abs((m.points[x] - bs.origin) & bs.normal);
                    if (d < bestD || (d == bestD && x < best)) { bestD = d; best = x; }
                }
            }
        }
        return best;
    };
    // The crossing is where the edge meets the sheet's PLANE, not a
    // triangle: at a free edge, or where two sheets meet inside a cell,
    // the sheet ends inside the cell and a triangle hit is not there
    // — the plane cut is, and the cap is trimmed to the sheet below.
    auto crossing = [&](label a, label b) -> label
    {
        const uint64_t key = edgeKey(a, b);
        auto it = crossings.find(key);
        if (it != crossings.end()) { return it->second; }
        label id = -1;
        const bool aFluid = (side[a] >= 0);
        const label pa = aFluid ? a : b;
        const label pb = aFluid ? b : a;
        if (side[pa] == 0)
        {
            id = -2;
        }
        else if ((id = onVertexOnEdge(pa, pb)) < 0)
        {
            const scalar da = (m.points[pa] - bs.origin) & bs.normal;
            const scalar db = (m.points[pb] - bs.origin) & bs.normal;
            scalar t = (da - db != 0) ? da / (da - db) : 0.5;
            t = std::min(std::max(t, scalar(0)), scalar(1));
            const point hit = m.points[pa] + t * (m.points[pb] - m.points[pa]);
            id = label(m.points.size());
            m.points.push_back(hit);
            side.push_back(0);
        }
        crossings.emplace(key, id);
        return id;
    };
    auto existing = [&](label a, label b) -> label
    {
        auto it = crossings.find(edgeKey(a, b));
        return (it == crossings.end()) ? -1 : it->second;
    };
    auto regionOfFace = [&](const std::vector<label>&) -> label { return -1; };
    auto trisInBox = [&](const boundBox& bb, std::vector<dcCartSurfaces::triInfo>& out)
    {
        surfaces.baffleSheetTrianglesInBox(sh, bb, out);
    };
    std::function<bool(label, const std::vector<label>&, const point&,
        std::vector<std::pair<std::vector<label>, label>>&, std::vector<chordInsert>&)> splitCap =
        [&](label, const std::vector<label>& loop, const point&, auto& pieces, auto&) -> bool
    {
        boundBox bb(boundBox::invertedBox);
        for (label v : loop) { bb.add(m.points[v]); }
        if (!bb.valid()) { return false; }
        bb.inflate(0.05);
        return splitCapBySheet(m, side, trisInBox, bb, loop, bs.zone, pieces, nullptr, exactOutline);
    };

    // (only there: a cell beyond a JUNCTION of two sheets is halved by
    // this plane on purpose, and keeping it whole left 1e-3 volume ratios
    // in the duct's corners)
    std::function<bool(label, const std::vector<label>&)> uncoveredCap = [&](label c, const std::vector<label>& loop) -> bool
    {
        if (c >= label(edgeCells.size()) || !edgeCells[c]) { return false; }
        boundBox bb(boundBox::invertedBox);
        for (label v : loop) { bb.add(m.points[v]); }
        if (!bb.valid()) { return false; }
        bb.inflate(0.05);
        std::vector<std::pair<std::vector<label>, label>> pieces;
        bool unc = false;
        if (!splitCapBySheet(m, side, trisInBox, bb, loop, bs.zone, pieces, &unc, exactOutline)) { return false; }
        return unc;
    };

    passArgs args;
    args.capBoundary = false;
    args.capZone = bs.zone;
    args.eligible = &eligible;
    args.splitCap = &splitCap;
    args.onIsA = false;
    args.countRank = s.myRank;
    args.havePlane = true;
    args.planeOrigin = bs.origin;
    args.planeNormal = bs.normal;
    if (s.baffleEdgeSplit) { args.uncoveredCap = &uncoveredCap; }
    clipPass(m, side, crossing, existing, regionOfFace, args, r.nBaffleCut, r.nBaffleFallback, r.nCapFaces);
}


void Foam::dcCartCutCells::splitSeamedThinCells(cutMesh& m, const settings& s, report& r)
{
    // A THIN cut slab whose face on one plane carries two patches has that
    // face split in two, and checkMesh normalises a boundary face's skewness
    // by max(0.4*|d|, the face's own reach). On a slab 5 mm thick |d| is 2 mm,
    // so the denominator collapses onto the narrow piece's own 3 mm reach and
    // a 15 mm offset scores 5.9 (Shinaide: ACU_02_Front_patch ends at
    // x=5.0407381 INSIDE the rack frame's bottom, both at z=4.0199 -- two
    // objects coplanar and overlapping, exactly the imprecision this mesher
    // has to absorb). No merge repairs it: the union with the full cell below
    // is not convex, and every sideways neighbour is another slab of the same
    // layer, so joining one only widens the strip. Cutting the slab along the
    // seam does -- both halves are boxes whose face on that plane is one patch
    // covering the half exactly, the skewness goes to ~0, and each half stays a
    // small, well-shaped cell (the documented outcome for a sliver that finds
    // no convex host). Same split primitive as baffleEdgePreSplit.
    if (getenv("DCCART_NOSEAMSPLIT")) { return; }
    // Split at EVERY seam of a thin cell's planar face group, not only where
    // the patch changes: two same-patch pieces that coalesceCoplanarFaces did
    // not join (they do not share the exact point ids, or an internal piece of
    // the same plane sits between them) leave the same narrow strip far from
    // the cell centre, and checkMesh scores it the same way.
    const bool allSeams = (getenv("DCCART_SEAMSPLIT_ALLPATCH") != nullptr);

    std::vector<std::vector<label>> cellFaces;
    buildCellFaces(m, cellFaces);

    const scalar tol = max(s.onTolerance, scalar(1e-9));
    // The seam itself is exact: both pieces carry the seam's own vertices, so
    // the plane through it is a vertex coordinate. It is located, and the
    // cell's points are put on either side of it, with the exact-outline
    // tolerance (a hundredth of onTolerance), NOT with onTolerance. That one
    // is 0.1 x the finest cell -- 3.1 mm at a 0.25 m base -- and it counted
    // every corner of a slab whose seam ran 1.8 mm from the slab's side as ON
    // the plane, so the clip found nothing to cut and the 1.8 mm strip stayed
    // (Shinaide, ACU_01_Front_patch beside ACU_01_Front_part1 at z=4.0199:
    // 53 faces at skewness 5.53, checkMesh failed).
    const scalar exactTol = exactOutlinePlaneTol(s);
    // Both halves must keep a real share of the cell: cutting 1 um off a face
    // would trade one degenerate face for another.
    const scalar minShare = 0.02;
    // ... and no point of the cell may sit just off the plane: with the exact
    // tolerance such a point is no longer ON it, and the clip would cut it
    // off as a sliver. A seam whose plane passes closer than minShare of the
    // cell to one of its points (but not through it) is left alone.
    auto cleanPlane = [&](label c, const std::vector<std::vector<label>>& cfs, label S, scalar x, scalar minGap) -> bool
    {
        for (label f : cfs[c])
        {
            if (!m.faceAlive[f]) { continue; }
            for (label v : m.faces[f])
            {
                const scalar dv = mag(m.points[v].component(S) - x);
                if (dv > exactTol && dv < minGap) { return false; }
            }
        }
        return true;
    };

    std::map<std::pair<label, long long>, std::vector<label>> groups;
    std::map<std::pair<label, long long>, std::pair<scalar, scalar>> coords;
    auto recordPlane = [&](const std::pair<label, long long>& key, label c, scalar x)
    {
        if (!std::isfinite(x)) { return; }
        groups[key].push_back(c);
        const auto found = coords.find(key);
        if (found == coords.end()) { coords.emplace(key, std::make_pair(x, x)); }
        else
        {
            found->second.first = min(found->second.first, x);
            found->second.second = max(found->second.second, x);
        }
    };

    for (label c = 0; c < m.nCells; ++c)
    {
        if (!m.cellAlive[c]) { continue; }
        // Cheap gate first: a seam needs at least two boundary faces of
        // DIFFERENT patches in one plane. Almost every cell fails here, so the
        // bounding box below is only built for cells on a patch boundary.
        label nBnd = 0;
        label firstPatch = -1;
        bool multiPatch = false;
        bool touchesZone = false;
        for (label f : cellFaces[c])
        {
            if (!m.faceAlive[f]) { continue; }
            if (m.zone[f] >= 0) { touchesZone = true; break; }
            if (m.neighbour[f] >= 0 || m.patch[f] < 0) { continue; }
            ++nBnd;
            if (firstPatch < 0) { firstPatch = m.patch[f]; }
            else if (m.patch[f] != firstPatch) { multiPatch = true; }
        }
        if (allSeams && nBnd >= 2) { multiPatch = true; }
        // A cell carrying a baffle face is left alone: the split could move a
        // face out of its zone, and a baffle that loses a face is a leak.
        // Two narrower variants were MEASURED on Base604 and both rejected:
        //  (a) skip only when a baffle lies in the face plane being grouped
        //      (havePlane set, so off-plane zone tags survive) -- seam-split
        //      still fired on 388 cells / 6 planes, 21 residual skew faces
        //      untouched: the blocking baffle is IN that same plane (Base604's
        //      raised floor at y=0.8 is a faceZone, and the thin slab's own
        //      face lies in it);
        //  (b) skip only when a baffle lies in the SPLIT plane -- seam-split
        //      collapsed from 388 cells / 6 planes to 17 / 3 (so most splits
        //      do cut across a baffle plane), Base604 went 22 -> 21 faces with
        //      max unchanged at 21.3791, and it puts Shinaide's repair at risk
        //      (its 19 skew faces were cleared by seam-split 351 cells / 15
        //      planes). Zero benefit, real regression risk.
        // So the guard stays whole-cell.
        if (touchesZone || nBnd < 2 || !multiPatch) { continue; }

        boundBox bb(boundBox::invertedBox);
        for (label f : cellFaces[c])
        {
            if (!m.faceAlive[f]) { continue; }
            for (label v : m.faces[f]) { bb.add(m.points[v]); }
        }
        if (!bb.valid()) { continue; }
        const vector span = bb.span();

        for (label A = 0; A < 3; ++A)
        {
            const label U = (A + 1) % 3, V = (A + 2) % 3;
            const scalar thick = span.component(A);
            // A narrow extent PARALLEL to the seam does not determine its
            // skewness: the offset is across the seam. Admit either in-plane
            // direction here, then test the actual split direction below.
            // Shinaide's front wall is 15.4 mm deep, 31.9 mm across the seam,
            // and only 1.8 mm wide along it; min(U,V) missed that repair.
            const scalar inPlane = max(span.component(U), span.component(V));
            if (inPlane <= 0 || thick > 0.5 * inPlane) { continue; }
            for (label sd = 0; sd < 2; ++sd)
            {
                const scalar pc = sd ? bb.max().component(A) : bb.min().component(A);
                std::vector<label> fs;
                for (label f : cellFaces[c])
                {
                    if (!m.faceAlive[f] || m.neighbour[f] >= 0 || m.patch[f] < 0) { continue; }
                    bool in = true;
                    for (label v : m.faces[f])
                    {
                        if (mag(m.points[v].component(A) - pc) > tol) { in = false; break; }
                    }
                    if (in) { fs.push_back(f); }
                }
                if (fs.size() < 2) { continue; }
                std::vector<boundBox> fbb(fs.size(), boundBox(boundBox::invertedBox));
                bool seamPatch = allSeams;
                for (size_t i = 0; i < fs.size(); ++i)
                {
                    for (label v : m.faces[fs[i]]) { fbb[i].add(m.points[v]); }
                    if (m.patch[fs[i]] != m.patch[fs[0]]) { seamPatch = true; }
                }
                if (!seamPatch) { continue; }
                for (size_t i = 0; i < fs.size(); ++i)
                {
                    for (size_t j = i + 1; j < fs.size(); ++j)
                    {
                        if (!allSeams && m.patch[fs[i]] == m.patch[fs[j]]) { continue; }
                        bool found = false;
                        for (label which = 0; which < 2 && !found; ++which)
                        {
                            const label S = which ? V : U;
                            const label T = which ? U : V;
                            if (thick > 0.5 * span.component(S)) { continue; }
                            for (label e = 0; e < 2 && !found; ++e)
                            {
                                const scalar x0 = e ? fbb[i].max().component(S) : fbb[i].min().component(S);
                                const scalar x1 = e ? fbb[j].min().component(S) : fbb[j].max().component(S);
                                if (mag(x0 - x1) > exactTol) { continue; }
                                // the two pieces must actually touch along T
                                if (fbb[i].max().component(T) <= fbb[j].min().component(T) + tol) { continue; }
                                if (fbb[j].max().component(T) <= fbb[i].min().component(T) + tol) { continue; }
                                const scalar lo = bb.min().component(S), hi = bb.max().component(S);
                                if (hi - lo <= 0) { continue; }
                                const scalar x = 0.5 * (x0 + x1);
                                if (x - lo < minShare * (hi - lo)) { continue; }
                                if (hi - x < minShare * (hi - lo)) { continue; }
                                if (!cleanPlane(c, cellFaces, S, x, minShare * (hi - lo))) { continue; }
                                if (!std::isfinite(x)) { continue; }
                                const std::pair<label, long long> key
                                (
                                    S, (long long)std::llround(x * 1e9)
                                );
                                recordPlane(key, c, x);
                                found = true;
                            }
                        }
                    }
                }
            }
        }

        // The same strip on a TILTED wall. A cut wedge against a sloped face
        // whose cap changes patch mid-face along a line of constant x, y or z
        // scores the same way, and the loop above never sees it: the face's
        // points share no coordinate, so it is in no axis plane. DAML-Testbed
        // at 0.25 m: AC3's sloped front, Front_patch beside Front_part1 at
        // x=3.60469198, an 11 mm strip on 3-5 mm wedges of 63 mm cells --
        // skewness 4.55 and 4.45, checkMesh failed. The faces are grouped by
        // plane instead, the slab test is made along the plane's normal (the
        // wedge's thickness against its two in-plane extents), and the cut is
        // the same axis plane through the seam: the two pieces lie whole on
        // either side of it and meet along it, so each half keeps one patch
        // on its wall. Axis-aligned faces are left to the loop above, whose
        // decisions this does not touch.
        {
            std::vector<label> tf;
            std::vector<vector> tn;
            std::vector<scalar> toff;
            for (label f : cellFaces[c])
            {
                if (!m.faceAlive[f] || m.neighbour[f] >= 0 || m.patch[f] < 0) { continue; }
                vector a; point fc;
                polygonNormalCentre(m.points, m.faces[f], a, fc);
                const scalar am = mag(a);
                if (am < VSMALL) { continue; }
                const vector n = a / am;
                if (cmptMax(cmptMag(n)) > 1 - 1e-9) { continue; }
                tf.push_back(f);
                tn.push_back(n);
                toff.push_back(n & fc);
            }
            std::vector<point> cellPts;
            if (tf.size() >= 2)
            {
                for (label f : cellFaces[c])
                {
                    if (!m.faceAlive[f]) { continue; }
                    for (label v : m.faces[f]) { cellPts.push_back(m.points[v]); }
                }
            }
            auto spanAlong = [&](const vector& d) -> scalar
            {
                scalar lo = GREAT, hi = -GREAT;
                for (const point& p : cellPts) { const scalar t = p & d; lo = min(lo, t); hi = max(hi, t); }
                return hi - lo;
            };
            bool tiltFound = false;
            for (size_t i = 0; i < tf.size() && !tiltFound; ++i)
            {
                for (size_t j = i + 1; j < tf.size() && !tiltFound; ++j)
                {
                    if (!allSeams && m.patch[tf[i]] == m.patch[tf[j]]) { continue; }
                    if ((tn[i] & tn[j]) < 1 - 1e-9) { continue; }
                    bool coplanar = true;
                    for (label v : m.faces[tf[j]])
                    {
                        if (mag((m.points[v] & tn[i]) - toff[i]) > exactTol) { coplanar = false; break; }
                    }
                    if (!coplanar) { continue; }
                    const vector& n = tn[i];
                    const scalar thick = spanAlong(n);
                    boundBox bi(boundBox::invertedBox), bj(boundBox::invertedBox);
                    for (label v : m.faces[tf[i]]) { bi.add(m.points[v]); }
                    for (label v : m.faces[tf[j]]) { bj.add(m.points[v]); }
                    for (label S = 0; S < 3 && !tiltFound; ++S)
                    {
                        // the seam line lies in the face plane and in S = x
                        vector u = vector::zero;
                        u[S] = 1;
                        u -= (u & n) * n;
                        if (mag(u) < 1e-6) { continue; }
                        u /= mag(u);
                        const vector t = n ^ u;
                        const scalar inPlane = min(spanAlong(u), spanAlong(t));
                        if (inPlane <= 0 || thick > 0.5 * inPlane) { continue; }
                        for (label e = 0; e < 2 && !tiltFound; ++e)
                        {
                            const scalar x0 = e ? bi.max().component(S) : bi.min().component(S);
                            const scalar x1 = e ? bj.min().component(S) : bj.max().component(S);
                            if (mag(x0 - x1) > exactTol) { continue; }
                            const scalar x = 0.5 * (x0 + x1);
                            // both pieces reach the plane along an edge, and the
                            // two edges overlap: the pieces meet there
                            scalar ti0 = GREAT, ti1 = -GREAT, tj0 = GREAT, tj1 = -GREAT;
                            label ni = 0, nj = 0;
                            for (label v : m.faces[tf[i]])
                            {
                                if (mag(m.points[v].component(S) - x) > exactTol) { continue; }
                                const scalar s = m.points[v] & t;
                                ti0 = min(ti0, s); ti1 = max(ti1, s); ++ni;
                            }
                            for (label v : m.faces[tf[j]])
                            {
                                if (mag(m.points[v].component(S) - x) > exactTol) { continue; }
                                const scalar s = m.points[v] & t;
                                tj0 = min(tj0, s); tj1 = max(tj1, s); ++nj;
                            }
                            if (ni < 2 || nj < 2) { continue; }
                            if (min(ti1, tj1) - max(ti0, tj0) <= exactTol) { continue; }
                            const scalar lo = bb.min().component(S), hi = bb.max().component(S);
                            if (hi - lo <= 0) { continue; }
                            if (x - lo < minShare * (hi - lo)) { continue; }
                            if (hi - x < minShare * (hi - lo)) { continue; }
                            if (!cleanPlane(c, cellFaces, S, x, minShare * (hi - lo))) { continue; }
                            if (!std::isfinite(x)) { continue; }
                            const std::pair<label, long long> key
                            (
                                S, (long long)std::llround(x * 1e9)
                            );
                            recordPlane(key, c, x);
                            tiltFound = true;
                        }
                    }
                }
            }
        }
    }
    // Every rank must split on the SAME set of planes. The seams are read off
    // this rank's own cells, and a rank holds only its slab of columns plus a
    // halo, so a seam visible to one rank and not to its neighbour would leave
    // a shared cell split on one side of a processor interface and whole on
    // the other -- redistributePar then dies with
    //   face 2211 area does not match neighbour by 113.043%
    //   patch:procBoundary2to1 my area:0.000613633 neighbour area:0.000170455
    // (measured on Base604 with a non-uniform background). baffleEdgePreSplit
    // cannot hit this because its planes come from the global surface
    // geometry; these come from the local mesh, so they have to be unioned
    // over the ranks and iterated in one global order. The plane coordinate is
    // retained as a grouping/order key only. Reconstructing it at 1 nm
    // rounded a true STL seam at .8999999761581421 to .899999976 and
    // generated nonplanar internal bases. Transport real candidate doubles
    // separately; all ranks agree on their bounds before any local skip.
    const uint64_t lowMask = (uint64_t(1) << 62) - 1;
    const long long cqBias = static_cast<long long>(1) << 40;
    List<List<uint64_t>> gatheredKeys(UPstream::nProcs());
    List<List<scalar>> gatheredCoords(UPstream::nProcs());
    auto& myKeys = gatheredKeys[UPstream::myProcNo()];
    auto& myCoords = gatheredCoords[UPstream::myProcNo()];
    myKeys.resize(groups.size()); myCoords.resize(2*groups.size());
    label slot = 0;
    for (const auto& kv : groups)
    {
        myKeys[slot] = (uint64_t(kv.first.first) << 62)
          | (static_cast<uint64_t>(kv.first.second + cqBias) & lowMask);
        const auto& bound = coords.at(kv.first);
        myCoords[2*slot] = bound.first; myCoords[2*slot+1] = bound.second;
        ++slot;
    }
    if (Pstream::parRun())
    {
        Pstream::allGatherList(gatheredKeys);
        Pstream::allGatherList(gatheredCoords);
    }
    std::vector<uint64_t> allKeys;
    std::map<uint64_t, std::pair<scalar, scalar>> exactCoords;
    forAll(gatheredKeys, rank)
    {
        const auto& keys = gatheredKeys[rank]; const auto& bounds = gatheredCoords[rank];
        if (bounds.size() != 2*keys.size())
        { FatalErrorInFunction << "seam coordinate transport shape mismatch" << exit(FatalError); }
        forAll(keys, i)
        {
            const scalar lo=bounds[2*i],hi=bounds[2*i+1];
            if (!std::isfinite(lo) || !std::isfinite(hi) || lo>hi)
            { FatalErrorInFunction << "invalid seam coordinate transport" << exit(FatalError); }
            allKeys.push_back(keys[i]);
            const auto found=exactCoords.find(keys[i]);
            if (found==exactCoords.end()) { exactCoords.emplace(keys[i],std::make_pair(lo,hi)); }
            else { found->second.first=min(found->second.first,lo); found->second.second=max(found->second.second,hi); }
        }
    }
    std::sort(allKeys.begin(), allKeys.end());
    allKeys.erase(std::unique(allKeys.begin(), allKeys.end()), allKeys.end());
    label ambiguousPlanes=0;
    for (const auto& kv:exactCoords) if (kv.second.first!=kv.second.second) { ++ambiguousPlanes; }
    Info << "Seam plane coordinate transport: " << allKeys.size() << " groups, "
         << ambiguousPlanes << " distinct-source groups refused; actual candidate coordinates retained" << endl;
    if (getenv("DCCART_SEAM_COORD_EVIDENCE") && Pstream::master())
    {
        std::ofstream out("seam-plane-coordinates.json");out<<std::setprecision(17)<<"[";
        bool first=true;
        for (uint64_t key:allKeys)
        {
            if (!first) { out<<","; } first=false;
            const label axis=label(key>>62);
            const long long quantized=static_cast<long long>(key&lowMask)-cqBias;
            const auto& bound=exactCoords.at(key);
            out<<"{\"axis\":"<<axis<<",\"quantized\":"<<quantized
               <<",\"minimum\":"<<bound.first<<",\"maximum\":"<<bound.second
               <<",\"spread\":"<<(bound.second-bound.first)
               <<",\"reconstructed\":"<<(scalar(quantized)*1e-9)
               <<",\"eligibleCoordinate\":"<<(bound.first==bound.second ? "true":"false")<<"}";
        }
        out<<"]\n";
    }
    if (allKeys.empty()) { return; }

    // Plane candidates refer to cells before the first seam cut. A cut
    // retains one half at that index and appends the other: later planes
    // must follow BOTH descendants, rather than only the retained half.
    // Keep each original cut cell distinct even when it shares an octree
    // leaf with other pieces; the whole-cell baffle guard remains intact.
    const label nOriginal = m.nCells;
    std::vector<label> splitOrigin(nOriginal);
    for (label c = 0; c < nOriginal; ++c) { splitOrigin[c] = c; }
    label nSplit = 0;
    for (const uint64_t gk : allKeys)
    {
        const label S = label(gk >> 62);
        const long long cq = static_cast<long long>(gk & lowMask) - cqBias;
        const auto& bound = exactCoords.at(gk);
        // Distinct candidate planes sharing a quantized key are not assumed
        // physically equivalent. Leave this group uncut; final quality still
        // decides authorization. The decision is identical on every rank.
        if (bound.first != bound.second) { continue; }
        const scalar x = bound.first;
        std::vector<label> cells;
        const auto git = groups.find(std::make_pair(S, cq));
        if (git != groups.end()) { cells = git->second; }
        std::sort(cells.begin(), cells.end());
        cells.erase(std::unique(cells.begin(), cells.end()), cells.end());
        // Planes are unioned globally, but a rank without a candidate root
        // cannot own an eligible descendant. clipPass has no collectives;
        // skip its full-mesh incidence rebuild here while preserving the
        // common global plane order and the final collective count.
        if (cells.empty()) { continue; }

        const point po
        (
            S == 0 ? x : 0, S == 1 ? x : 0, S == 2 ? x : 0
        );
        const vector pn
        (
            S == 0 ? 1 : 0, S == 1 ? 1 : 0, S == 2 ? 1 : 0
        );

        std::vector<bool> originalEligible(nOriginal, false);
        for (label c : cells)
        {
            if (c < nOriginal) { originalEligible[c] = true; }
        }
        // Earlier clips change both the retained and appended half. In a
        // tilted cell they can create a vertex just off this next plane:
        // ancestor eligibility alone would then admit a new sliver. Recheck
        // the current point set and the share of BOTH halves before clipping.
        buildCellFaces(m, cellFaces);
        std::vector<bool> eligible(m.nCells, false);
        label nEligible = 0;
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!m.cellAlive[c] || !originalEligible[splitOrigin[c]]) { continue; }
            boundBox bb(boundBox::invertedBox);
            bool touchesZone = false;
            for (label f : cellFaces[c])
            {
                if (!m.faceAlive[f]) { continue; }
                if (m.zone[f] >= 0) { touchesZone = true; break; }
                for (label v : m.faces[f]) { bb.add(m.points[v]); }
            }
            if (touchesZone || !bb.valid()) { continue; }
            const scalar lo = bb.min().component(S), hi = bb.max().component(S);
            const scalar span = hi - lo;
            if (span <= 0 || x - lo < minShare * span || hi - x < minShare * span) { continue; }
            if (!cleanPlane(c, cellFaces, S, x, minShare * span)) { continue; }
            eligible[c] = true;
            ++nEligible;
        }
        if (!nEligible) { continue; }

        std::vector<signed char> side(m.points.size(), 1);
        for (label p = 0; p < label(m.points.size()); ++p)
        {
            const scalar d = (m.points[p] - po) & pn;
            side[p] = (std::abs(d) <= exactTol) ? 0 : (d < 0 ? -1 : 1);
        }
        std::unordered_map<uint64_t, label> crossings;
        auto crossing = [&](label a, label b) -> label
        {
            const uint64_t key = edgeKey(a, b);
            auto it = crossings.find(key);
            if (it != crossings.end()) { return it->second; }
            label id = -1;
            const bool aFluid = (side[a] >= 0);
            const label pa = aFluid ? a : b;
            const label pb = aFluid ? b : a;
            if (side[pa] == 0)
            {
                id = -2;
            }
            else
            {
                const scalar da = (m.points[pa] - po) & pn;
                const scalar db = (m.points[pb] - po) & pn;
                scalar t = (da - db != 0) ? da / (da - db) : 0.5;
                t = std::min(std::max(t, scalar(0)), scalar(1));
                id = label(m.points.size());
                m.points.push_back(m.points[pa] + t * (m.points[pb] - m.points[pa]));
                side.push_back(0);
            }
            crossings.emplace(key, id);
            return id;
        };
        auto existing = [&](label a, label b) -> label
        {
            auto it = crossings.find(edgeKey(a, b));
            return (it == crossings.end()) ? -1 : it->second;
        };
        auto regionOfFace = [&](const std::vector<label>&) -> label { return -1; };
        passArgs args;
        args.capBoundary = false;
        args.capZone = -1;   // a plain internal face between the two halves
        args.eligible = &eligible;
        args.splitOrigin = &splitOrigin;
        args.onIsA = false;
        args.countRank = s.myRank;
        // Restrict any baffle-tag clearing to faces lying in THIS plane: a
        // baffle the split cuts in two (its plane is perpendicular to this
        // one) keeps its zone on both halves, so no faceZone loses area.
        args.havePlane = true;
        args.planeOrigin = po;
        args.planeNormal = pn;
        label nCut = 0, nFallback = 0, nCaps = 0;
        clipPass(m, side, crossing, existing, regionOfFace, args, nCut, nFallback, nCaps);
        nSplit += nCut;
    }
    r.nSeamSplit += nSplit;
    // (owned cells, summed over the ranks: the cells a seam splits need not
    // be rank 0's, and a rank-0-only count printed nothing for Shinaide's)
    const label nSplitAll = returnReduce(nSplit, sumOp<label>());
    if (nSplitAll)
    {
        Info<< "Cut cells: seam-split " << nSplitAll << " thin cells whose own "
            << "boundary face changes patch mid-face (" << allKeys.size()
            << " seam planes, union over ranks)" << nl;
    }
}


void Foam::dcCartCutCells::cutBaffles
(
    cutMesh& m,
    const dcCartSurfaces& surfaces,
    const settings& s,
    report& r
)
{
    if (getenv("DCCART_DEBUG"))
    {
        Info<< "  folded caps " << gFolded << ": split " << gFoldedSplit
            << ", no facet " << gFoldedNoFacet << ", no feature line " << gFoldedNoLines
            << ", guard " << gFoldedGuard << nl;
    }
    // One pass per SHEET (connected, coplanar part of a baffle). The side
    // of a point is the sign of its distance to the sheet's own plane —
    // no nearest-triangle guess, no dependence on which way the twelve
    // duct baffles' normals point. Both halves survive; the cap is an
    // internal face in the sheet's faceZone.
    // One pass per sheet on the cells within its reach (cutNeighbourhood):
    // the pass splits the cells overlapping the sheet's box and splices the
    // new points into the faces of the cells sharing a vertex with them; it
    // drops nothing and merges no points.
    cutNeighbourhood nbh(m);
    for (label sh = 0; sh < surfaces.nBaffleSheets(); ++sh)
    {
        const dcCartSurfaces::baffleSheet& bs = surfaces.baffleSheetInfo(sh);
        boundBox sbb(bs.bounds);
        if (!sbb.valid()) { continue; }
        if (getenv("DCCART_DEBUG"))
        {
            Info<< "  baffle sheet " << sh << " zone " << bs.zone
                << " n " << bs.normal << " o " << bs.origin
                << " bounds " << bs.bounds << nl;
        }
        sbb.inflate(0.05);
        std::vector<label> reach;
        nbh.reachOfBox(sbb, reach);
        if (reach.empty()) { continue; }
        cutNeighbourhood::part P;
        nbh.extractCells(reach, nullptr, P);
        baffleSheetPass(P.lm, surfaces, s, r, sh, sbb);
        nbh.writeBack(P, nullptr);
    }

    // ---- faces that already lie IN a sheet ---------------------------------
    // A sheet that sits on a grid plane (within the ON tolerance) cuts no
    // cell: the mesh face is the baffle. The builder tagged such faces for
    // its stair-step, but a cell split by another sheet of the same zone
    // (the duct's twelve sheets are one zone) had those tags cleared, and a
    // face the builder never tagged had none. Tag every internal face whose
    // vertices are all on a sheet's plane and that the sheet's triangles
    // cover — split into pieces where they cover it only partly.
    //
    // An exact-outline sheet's faces the builder DID tag are trimmed too: the
    // builder tags a whole grid face when the segment between its two cell
    // centres crosses the sheet, i.e. when the face CENTRE is inside the
    // outline, so a face straddling the sheet's edge overhangs it. Tri_angle
    // (2026-09-23): the first-layer floor put Rack_1's u23 shelf on the grid
    // plane y = 1.0625, the rotated shelf's rear edge ran through a row of
    // faces whose centres were 0.06 mm inside it, and the shelf came out
    // with a 22 mm flap along that edge (0.29326 of 0.288 m2). Trimmed
    // against every coplanar sheet of its zone, so the part another sheet
    // of the zone covers keeps its tag.
    {
        label nTagged = 0, nSplit = 0, nTrimmed = 0;
        std::vector<signed char> dummySide;
        for (label sh = 0; sh < surfaces.nBaffleSheets(); ++sh)
        {
            const dcCartSurfaces::baffleSheet& bs = surfaces.baffleSheetInfo(sh);
            const word& zoneName = surfaces.faceZoneNames()[bs.zone];
            const bool exactOutline = zoneName.size() >= 10
                && zoneName.substr(zoneName.size() - 10) == "_partition"
        || zoneName.find("merged_perforatedBlankingPanel_") == 0
        || zoneName.find("merged_wallBaffle_") == 0
        || zoneName.find("merged_wall_baffle") == 0
        || zoneName == "merged_rack_door";
            const scalar planeTolerance = exactOutline ? exactOutlinePlaneTol(s) : s.onTolerance;
            boundBox sbb(bs.bounds);
            if (!sbb.valid()) { continue; }
            sbb.inflate(2 * s.onTolerance + 1e-6);
            auto trisInBox = [&](const boundBox& bb, std::vector<dcCartSurfaces::triInfo>& out)
            {
                surfaces.baffleSheetTrianglesInBox(sh, bb, out);
            };
            // the zone's sheets in this sheet's plane (itself included)
            std::vector<label> planeMates;
            for (label sh2 = 0; sh2 < surfaces.nBaffleSheets(); ++sh2)
            {
                const dcCartSurfaces::baffleSheet& b2 = surfaces.baffleSheetInfo(sh2);
                if (b2.zone != bs.zone) { continue; }
                if (Foam::mag(b2.normal & bs.normal) < 0.999) { continue; }
                if (Foam::mag((b2.origin - bs.origin) & bs.normal) > planeTolerance) { continue; }
                planeMates.push_back(sh2);
            }
            auto zoneTrisInBox = [&](const boundBox& bb, std::vector<dcCartSurfaces::triInfo>& out)
            {
                out.clear();
                std::vector<dcCartSurfaces::triInfo> one;
                for (const label sh2 : planeMates)
                {
                    surfaces.baffleSheetTrianglesInBox(sh2, bb, one);
                    out.insert(out.end(), one.begin(), one.end());
                }
            };
            const label nF = label(m.faces.size());
            // (a face with every vertex in the box belongs to cells whose box
            // overlaps it: their faces, ascending, are the faces to look at)
            std::vector<label> near, cand;
            nbh.cellsInBox(sbb, near);
            for (label c : near) { for (label f : nbh.facesOf(c)) { if (f < nF) { cand.push_back(f); } } }
            std::sort(cand.begin(), cand.end());
            cand.erase(std::unique(cand.begin(), cand.end()), cand.end());
            for (label f : cand)
            {
                if (!m.faceAlive[f]) { continue; }
                const bool retrim = exactOutline && m.zone[f] == bs.zone;
                if (m.zone[f] >= 0 && !retrim) { continue; }
                if (m.neighbour[f] < 0) { continue; }
                if (!m.cellAlive[m.owner[f]] || (m.neighbour[f] >= 0 && !m.cellAlive[m.neighbour[f]])) { continue; }
                // A baffle face LIES IN the sheet plane: parallel to it and
                // centred on it. Requiring every VERTEX inside the sheet box
                // and on the plane (the old test) rejected faces that carry
                // hanging nodes from a refinement jump across the plane, and
                // those faces are exactly the 18% of Shinaide's con_b plane
                // that went untagged — a hole through the containment baffle.
                // Coverage inside the footprint is still decided by the
                // triangle clip below, so a face merely crossing the plane
                // only gets the pieces the sheet actually covers.
                vector fn; point fc;
                polygonNormalCentre(m.points, m.faces[f], fn, fc);
                const scalar fa = Foam::mag(fn);
                if (fa < 1e-12) { continue; }
                fn /= fa;
                if (Foam::mag(fn & bs.normal) < 0.99) { continue; }
                if (Foam::mag((fc - bs.origin) & bs.normal) > planeTolerance)
                {
                    continue;
                }
                if (!sbb.contains(fc)) { continue; }
                boundBox bb(boundBox::invertedBox);
                for (label v : m.faces[f]) { bb.add(m.points[v]); }
                if (!bb.valid()) { continue; }
                bb.inflate(0.05);
                if (dummySide.size() < m.points.size()) { dummySide.resize(m.points.size(), 0); }
                std::vector<std::pair<std::vector<label>, label>> pieces;
                if (retrim)
                {
                    if (!splitCapBySheet(m, dummySide, zoneTrisInBox, bb, m.faces[f], bs.zone, pieces, nullptr, exactOutline)) { continue; }
                    if (pieces.size() == 1)
                    {
                        if (pieces[0].second < 0) { m.zone[f] = -1; ++nTrimmed; }
                        continue;
                    }
                    ++nTrimmed;
                }
                else
                {
                    if (!splitCapBySheet(m, dummySide, trisInBox, bb, m.faces[f], bs.zone, pieces, nullptr, exactOutline)) { continue; }
                    if (pieces.size() == 1)
                    {
                        if (pieces[0].second >= 0) { m.zone[f] = pieces[0].second; ++nTagged; }
                        continue;
                    }
                }
                bool any = false;
                for (const auto& pc : pieces) { if (pc.second >= 0) { any = true; break; } }
                if (!any) { if (retrim) { m.zone[f] = -1; } continue; }
                // the pieces are built in the face's own orientation
                m.faces[f] = pieces[0].first;
                m.zone[f] = pieces[0].second;
                for (size_t k = 1; k < pieces.size(); ++k)
                {
                    m.faces.push_back(pieces[k].first);
                    m.owner.push_back(m.owner[f]);
                    m.neighbour.push_back(m.neighbour[f]);
                    m.patch.push_back(m.patch[f]);
                    m.zone.push_back(pieces[k].second);
                    m.faceAlive.push_back(true);
                    nbh.faceAppended(label(m.faces.size()) - 1);
                }
                ++nSplit;
            }
        }
        if (nTagged || nSplit || nTrimmed)
        {
            Info<< "Cut cells: baffles — " << nTagged << " in-plane faces tagged, "
                << nSplit << " split by the sheet edge, " << nTrimmed
                << " builder-tagged faces trimmed to an exact outline" << nl;
        }
    }

    // ---- coverage gate: a baffle that lost faces is a leak, not a mesh -----
    // Tagged (faceZone) face area per zone against the sheet triangles' area.
    // A deficit means fluid can cross the baffle between two patches that
    // pretend to seal it; the solve then diverges hundreds of iterations later
    // with nothing left to point at the cause. checkMesh cannot see this — the
    // mesh is topologically consistent either way — so the mesher itself has
    // to refuse. Costs one area sum over the zone faces.
    {
        std::map<label, scalar> tagged;
        for (label f = 0; f < label(m.faces.size()); ++f)
        {
            if (!m.faceAlive[f] || m.zone[f] < 0) { continue; }
            vector n; point cc;
            polygonNormalCentre(m.points, m.faces[f], n, cc);
            tagged[m.zone[f]] += mag(n);
        }
        // A THIN baffle contributes two parallel sheets (its two walls) to one
        // zone, but the mesh tiles only ONE plane between them. Summing both
        // walls double-counts the footprint and reports a phantom 50% hole
        // (Shinaide's merged_wall_baffle read 0.543 against a mesh that tiles
        // it fully). Collapse parallel sheets of a zone onto one plane and
        // count each plane once.
        // The background grid box: footprint of a baffle sheet outside it is
        // outside the fluid domain (a containment baffle poking through the
        // room shell) and can never be tiled — not a hole.
        boundBox domain(boundBox::invertedBox);
        for (label p = 0; p < m.nGridPoints; ++p) { domain.add(m.points[p]); }
        // In parallel each rank holds only its slab of the grid points, so its
        // domain — and every sheet area clipped to it — is not a partition of
        // the room: a sheet parallel to the slab division (a containment side
        // baffle) lies wholly inside every slab's y/z range and is counted in
        // FULL by all eight ranks (want read 44.79 m2 for 12.14 m2 of sheet).
        // Widen to the global box first (min/max over ranks) so the expected
        // area is the same number on every rank and needs no reduction; only
        // the tagged area is summed.
        // Empty local ranks contribute the inverted-box identity but still
        // participate in both collectives. A rank-local validity branch
        // would strand a positive global mesh at this coverage barrier.
        if (Pstream::parRun())
        {
            point dmin = domain.min(), dmax = domain.max();
            reduce(dmin, minOp<point>());
            reduce(dmax, maxOp<point>());
            domain = boundBox(dmin, dmax);
        }
        std::map<label, scalar> sheetArea;
        for (label sh = 0; sh < surfaces.nBaffleSheets(); ++sh)
        {
            const dcCartSurfaces::baffleSheet& bs = surfaces.baffleSheetInfo(sh);
            const scalar off = bs.origin & bs.normal;
            bool samePlane = false;
            for (label o = 0; o < sh; ++o)
            {
                const dcCartSurfaces::baffleSheet& bo = surfaces.baffleSheetInfo(o);
                if (bo.zone != bs.zone) { continue; }
                if (mag(bo.normal ^ bs.normal) > 1e-6) { continue; }
                if (mag((bo.origin & bo.normal) - off) > 0.05) { continue; }
                samePlane = true;
                break;
            }
            if (samePlane) { continue; }
            sheetArea[bs.zone] += surfaces.baffleSheetExposedArea(sh, domain);
        }
        // Zone-level coverage with an exact expected area: tagged faces are
        // counted only when they lie in one of the zone's sheet planes (stair
        // step rims off-plane do not seal anything), and the expected area is
        // one wall per distinct plane, clipped to the background grid box
        // (footprint outside the room shell can never be tiled). Several
        // sheets of a zone may share a plane (merged baffles), so both sides
        // aggregate per zone.
        std::map<label, scalar> zoneTagged;
        for (label f = 0; f < label(m.faces.size()); ++f)
        {
            if (!m.faceAlive[f] || m.zone[f] < 0) { continue; }
            // A face on a slab boundary lives on two ranks (owned here, halo
            // there); counting it on both doubled `have` (21.56 for 10.91 m2
            // of tiling). Count it where its owner cell is owned.
            if (Pstream::parRun() && m.cellRank[m.owner[f]] != Pstream::myProcNo())
            {
                continue;
            }
            vector n; point cc;
            polygonNormalCentre(m.points, m.faces[f], n, cc);
            for (label sh = 0; sh < surfaces.nBaffleSheets(); ++sh)
            {
                const dcCartSurfaces::baffleSheet& bs =
                    surfaces.baffleSheetInfo(sh);
                if (bs.zone != m.zone[f]) { continue; }
                if (Foam::mag((cc - bs.origin) & bs.normal) > 1e-4) { continue; }
                zoneTagged[bs.zone] += Foam::mag(n);
                break;
            }
        }
        // `have` is summed over the ranks below; the expected area must be
        // reduced the same way. Each rank clips the sheets to ITS slab of the
        // background grid, so kv.second is a fraction of the whole: dividing a
        // global have by a local want reported 8.75 for a mesh that tiles
        // 90% (and 1.26 on two ranks), i.e. the gate was blind exactly when
        // it mattered. Same key order on every rank, so the reduces match.
        //
        // The cheap `sheetArea` above counts a whole triangle unless its
        // CENTROID is buried, so it over-counts wherever a body crosses a
        // sheet: on Shinaide the containment baffles are coplanar with the
        // rack faces they seal against (x=1.4907375 is both con_f and
        // Rack_A_01's side) and 0.87 m2 of 13.01 m2 lands inside a rack.
        // `openArea` is the exact measure -- the part of the sheet with fluid
        // on BOTH sides, which is the only part a baffle face can exist in --
        // and it is a pure function of the geometry, so every rank computes
        // the identical number and nothing needs reducing. It is only asked
        // for once the cheap ratio has failed, which is never on a healthy
        // model.
        const scalar finest = 10 * s.onTolerance;   // onToleranceFraction 0.1
        const scalar probeDist = 0.125 * finest;
        const scalar minTriArea = 0.5 * Foam::sqr(finest / 32);
        // The "already closed by a carving surface" probe must be tight: a
        // coplanar rack panel coincides with the sheet to within the snap
        // tolerance, while a panel merely crossing the sheet is millimetres
        // away. The carve's own ON band (0.01*finest) is 10x too wide and
        // would excuse a strip along every crossing, hiding real holes.
        const scalar onTol = 1e-3 * finest;
        auto openArea = [&](label zone) -> scalar
        {
            scalar a = 0;
            for (label sh = 0; sh < surfaces.nBaffleSheets(); ++sh)
            {
                const dcCartSurfaces::baffleSheet& bs = surfaces.baffleSheetInfo(sh);
                if (bs.zone != zone) { continue; }
                const scalar off = bs.origin & bs.normal;
                bool samePlane = false;
                for (label o = 0; o < sh; ++o)
                {
                    const dcCartSurfaces::baffleSheet& bo = surfaces.baffleSheetInfo(o);
                    if (bo.zone != bs.zone) { continue; }
                    if (mag(bo.normal ^ bs.normal) > 1e-6) { continue; }
                    if (mag((bo.origin & bo.normal) - off) > 0.05) { continue; }
                    samePlane = true;
                    break;
                }
                if (samePlane) { continue; }
                a += surfaces.baffleSheetOpenArea
                (
                    sh, domain, probeDist, minTriArea, onTol
                );
            }
            return a;
        };
        for (const auto& kv : sheetArea)
        {
            const word& zn = surfaces.faceZoneNames()[kv.first];
            scalar have = zoneTagged.count(kv.first) ? zoneTagged[kv.first] : 0;
            reduce(have, sumOp<scalar>());
            // global domain above => identical on every rank, no reduction
            const scalar want = kv.second;
            const scalar ratio = want > 0 ? have / want : 1;
            const bool perforated = zn.find("perforated") != word::npos
                || zn.find("door") != word::npos;
            if (Pstream::myProcNo() == 0)
            {
                Info<< "Cut cells: baffle coverage " << zn << ": " << have
                    << " / " << want << " = " << ratio
                    << (perforated ? " (perforated: informational)" : "") << nl;
            }
            // DCCART_BAFFLE_COVERAGE_MIN=off disables the refusal (never the
            // diagnostic) for cases whose deficit is known and accepted.
            const char* minEnv = getenv("DCCART_BAFFLE_COVERAGE_MIN");
            const scalar minRatio =
                (minEnv && word(minEnv) == "off") ? -1 : 0.995;
            if (!perforated && ratio < minRatio)
            {
                const scalar wantOpen = openArea(kv.first);
                const scalar ratioOpen = wantOpen > 0 ? have / wantOpen : 1;
                if (ratioOpen >= minRatio)
                {
                    if (Pstream::myProcNo() == 0)
                    {
                        Info<< "Cut cells: baffle coverage " << zn << ": the "
                            << (want - wantOpen) << " m2 deficit is sealed by "
                            << "solid on one side (sheet open on both sides "
                            << wantOpen << " m2, tiled " << ratioOpen
                            << " of it) - not a hole" << nl;
                    }
                    continue;
                }
                FatalErrorInFunction
                    << "baffle faceZone " << zn << " tiles only " << ratioOpen
                    << " of the sheet area that has fluid on both sides ("
                    << have << " of " << wantOpen << " m2; " << want
                    << " m2 of sheet in domain): the mesh would leak through "
                    << "the gap. Refusing to write a mesh a solve would "
                    << "diverge on."
                    << (s.maxGlobalCells > 0 ? " maxGlobalCells " : "")
                    << (s.maxGlobalCells > 0 ? name(s.maxGlobalCells) : word::null)
                    << (s.maxGlobalCells > 0
                        ? " is in force: if the refinement ceiling was reached,"
                          " this sheet needs a finer level than the ceiling"
                          " allows -- raise maxGlobalCells."
                        : "")
                    << exit(FatalError);
            }
            continue;
        }

        // ---- thin SOLID baffles: the seal is their pair of wall patches -----
        // Containment baffles (con_*) are carved as thin boxes and carry NO
        // faceZone, so the zone gate above never sees them; a hole punched by
        // the sliver passes shows up as a wall patch short of its STL area.
        // Same leak, same refusal.
        std::map<label, scalar> patchArea;
        std::map<label, label> patchFaces;
        for (label f = 0; f < label(m.faces.size()); ++f)
        {
            if (!m.faceAlive[f] || m.neighbour[f] >= 0 || m.patch[f] < 0)
            {
                continue;
            }
            // Halo double-count: a slab-boundary face lives in the face lists
            // of every rank that holds one of its cells — owned on one rank,
            // halo (owner cell of another rank) on the others — and each copy
            // passes the alive/boundary test above. The reduce(sumOp) below
            // then adds the SAME area once per holding rank, so `haveG` (and
            // `nFacesG`) grow with the rank count: coverage read higher on
            // np=8 than np=1 for identical geometry, and a sheet the serial
            // run correctly refused as a hole was accepted in parallel —
            // writing a leaking mesh. Same defect, same fix as the zone gate
            // above: count a face only on the rank that owns its owner cell.
            if (Pstream::parRun() && m.cellRank[m.owner[f]] != Pstream::myProcNo())
            {
                continue;
            }
            vector n; point cc;
            polygonNormalCentre(m.points, m.faces[f], n, cc);
            patchArea[m.patch[f]] += mag(n);
            ++patchFaces[m.patch[f]];
        }
        // A region of a BAFFLE surface never carries a wall patch of its
        // own. The surface is cut as one faceZone, and its faces are handed
        // to that zone -- createBaffles later splits them into the single
        // <zone>_master/_slave pair -- so every one of its region patches
        // stays empty by construction and the zone gate above is the only
        // coverage test that measures anything for it. Testing the region
        // name here measures which stray sliver a particular grid happened
        // to leave behind: DAML-Testbed's merged_wall_baffle.stl has 19 such
        // regions (Baffle_1, Baffle_1_copy, ...) and at meshBaseSize 0.1
        // Baffle_1_copy reads exactly 0 (waived below as an unmapped
        // pairing) while at 0.17 one 70 mm2 corner sliver lands under the
        // name and reads 0.033 -- a fatal "hole" in the same, fully tiled
        // sheet. Derived from the surface list, so it is the same set of
        // regions on every rank and the reduces below stay in step.
        boolList baffleRegion(surfaces.nPatches(), false);
        for (const auto& si : surfaces.info())
        {
            if (!si.internalBaffle && !si.wallBaffle) { continue; }
            for (label r = si.regionOffset; r < si.regionOffset + si.nRegions; ++r)
            {
                if (r >= 0 && r < label(baffleRegion.size())) { baffleRegion[r] = true; }
            }
        }
        for (label rgn = 0; rgn < surfaces.nPatches(); ++rgn)
        {
            if (baffleRegion[rgn]) { continue; }
            const word& pn = surfaces.patchNames()[rgn];
            if (pn.find("perforated") != word::npos
                || pn.find("door") != word::npos
                || pn.find("inlet") != word::npos
                || pn.find("outlet") != word::npos
                || pn.find("vent") != word::npos
                || pn.find("grille") != word::npos
                || (pn.size() > 7 && pn.substr(pn.size() - 7) == "_master")
                || (pn.size() > 6 && pn.substr(pn.size() - 6) == "_slave"))
            {
                continue;
            }
            if (!surfaces.regionIsThinSheet(rgn)) { continue; }
            const scalar want = surfaces.regionArea(rgn);
            if (want <= 0) { continue; }
            const scalar haveP =
                patchArea.count(rgn) ? patchArea[rgn] : 0;
            scalar haveG = haveP;
            reduce(haveG, sumOp<scalar>());
            const scalar ratio = haveG / want;
            if (Pstream::myProcNo() == 0)
            {
                Info<< "Cut cells: thin-baffle coverage " << pn << ": "
                    << haveG << " / " << want << " = " << ratio << nl;
            }
            // A patch carrying (almost) none of its own sheet is a
            // patch/region pairing that did not map -- coplanar merges and
            // relabelCoplanarPatches put its area under whichever patch owns
            // the plane -- not a hole. A PARTIAL deficit is the leak
            // signature this gate exists for.
            //
            // This used to test `ratio > 1e-6`, i.e. an exact zero, and that
            // is a knife edge: DAML-Testbed's R1_switch3_top (a 0.368 m2
            // server top) reads exactly 0 with the sliver coalescing off and
            // 1.45e-4 m2 -- 0.04% -- with it on, because one coplanar sliver
            // happened to land under that name. Same fact either way, so the
            // exemption is a fraction of the sheet, and it says so rather
            // than passing silently. 1% is three orders of magnitude below
            // the 0.995 the leak test wants and far below any deficit a
            // mapped patch can show (Shinaide's genuinely short sheet reads
            // 0.933).
            const scalar unmappedRatio = 0.01;
            const char* minEnv = getenv("DCCART_BAFFLE_COVERAGE_MIN");
            const scalar minRatio =
                (minEnv && word(minEnv) == "off") ? -1 : 0.995;
            if (ratio < unmappedRatio)
            {
                if (Pstream::myProcNo() == 0)
                {
                    Info<< "Cut cells: thin-baffle coverage " << pn << ": only "
                        << haveG << " of " << want << " m2 sits under this name "
                        << "(< " << unmappedRatio << " of the sheet) - the "
                        << "patch/region pairing did not map, its area is under "
                        << "another patch of the same plane. Not a hole." << nl;
                }
                continue;
            }
            // Before calling a shortfall a leak, subtract what a Cartesian
            // grid cannot avoid. A face belongs wholly to one region, so the
            // area tiled under a region's name is quantised to whole cells and
            // can fall short by a band of cells around its rim: about
            // P*h/2 + h*h for perimeter P and cell size h. Taking the square
            // (the compact shape) for P keeps the allowance a lower bound for
            // elongated regions, so a real hole is never forgiven.
            //
            // DAML-Testbed's Ceiling_patch_1 is the case this exists for: a
            // 0.30 x 0.30 m ACU return in a flat ceiling, three cells across,
            // which reads 0.0922 of 0.09 m2 at meshBaseSize 0.193 and 0.0647
            // at 0.170 -- the same geometry and the same 3x3 block of level-1
            // cells, passing or failing purely on which way the cell
            // boundaries fall across the opening. Shinaide's genuinely short
            // containment sheet reads 0.933 against an allowance of 0.011 and
            // is still refused.
            if (ratio < minRatio)
            {
                label nFacesG = patchFaces.count(rgn) ? patchFaces[rgn] : 0;
                reduce(nFacesG, sumOp<label>());
                scalar allowance = 0;
                if (nFacesG > 0 && haveG > VSMALL)
                {
                    const scalar h = std::sqrt(haveG / scalar(nFacesG));
                    allowance = (2 * h * std::sqrt(want) + h * h) / want;
                }
                if (ratio >= minRatio - allowance)
                {
                    if (Pstream::myProcNo() == 0)
                    {
                        Info<< "Cut cells: thin-baffle coverage " << pn << ": "
                            << "short by " << (want - haveG) << " m2, which is "
                            << "within the " << (allowance * want) << " m2 a "
                            << "grid of " << nFacesG << " cell faces cannot "
                            << "avoid on a region this small - quantisation, "
                            << "not a hole" << nl;
                    }
                    continue;
                }
                // Coplanar relabel: relabelCoplanarPatches moves a face onto
                // the facet it actually lies in, so a sheet's area can end up
                // under a NEIGHBOURING patch's name while still being tiled.
                // Count every boundary face lying in this sheet's plane, on
                // the same side, inside its footprint; if those tile the
                // sheet there is no hole, only a naming difference. A real
                // hole has no faces in the plane on that side and still
                // fails. Only the same side is counted, so a hole in one side
                // of a thin solid is not masked by the intact other side.
                {
                    vector sn; boundBox sbb;
                    if (surfaces.regionPlane(rgn, sn, sbb))
                    {
                        const scalar planeTol = max(scalar(1e-4), 4 * s.onTolerance);
                        // Both sums below are reduce(sumOp)'d over ranks, so
                        // they carry the SAME halo double-count the patchArea
                        // accumulation above had: a slab-boundary face is
                        // alive on every rank that holds one of its cells, and
                        // each copy passes the alive/boundary filter. Measured
                        // on Tri_angle's slanted Room_Wall_1 after the primary
                        // sums were fixed: serial and np=4 stayed under the
                        // 0.959 exemption threshold and refused, while np=8
                        // read unionA/want = 1.16057 -- the inflated exemption
                        // ACCEPTED a wall every other rank count refuses, i.e.
                        // the rank-dependent leak this gate exists to stop.
                        // Count each face only on the rank that owns its
                        // owner cell.
                        scalar signOwn = 0;
                        for (label f = 0; f < label(m.faces.size()); ++f)
                        {
                            if (!m.faceAlive[f] || m.neighbour[f] >= 0
                                || m.patch[f] != rgn) { continue; }
                            if (Pstream::parRun()
                                && m.cellRank[m.owner[f]] != Pstream::myProcNo())
                            {
                                continue;
                            }
                            vector fn; point cc;
                            polygonNormalCentre(m.points, m.faces[f], fn, cc);
                            signOwn += fn & sn;
                        }
                        reduce(signOwn, sumOp<scalar>());
                        if (signOwn == 0) { signOwn = 1; }
                        const scalar sSide = (signOwn >= 0 ? 1 : -1);
                        const scalar offPlane = (sbb.midpoint() & sn);
                        const scalar halfThick = 0.5 * mag(sbb.span() & sn) + planeTol;
                        sbb.inflate(planeTol);
                        scalar unionA = 0;
                        for (label f = 0; f < label(m.faces.size()); ++f)
                        {
                            if (!m.faceAlive[f] || m.neighbour[f] >= 0
                                || m.patch[f] < 0) { continue; }
                            if (Pstream::parRun()
                                && m.cellRank[m.owner[f]] != Pstream::myProcNo())
                            {
                                continue;
                            }
                            vector fn; point cc;
                            polygonNormalCentre(m.points, m.faces[f], fn, cc);
                            const scalar fa = mag(fn);
                            if (fa < VSMALL) { continue; }
                            if (!sbb.contains(cc)) { continue; }
                            if (((fn / fa) & sn) * sSide < 0.999) { continue; }
                            if (mag((cc & sn) - offPlane) > halfThick) { continue; }
                            unionA += fa;
                        }
                        reduce(unionA, sumOp<scalar>());
                        if (unionA / want >= minRatio - allowance)
                        {
                            if (Pstream::myProcNo() == 0)
                            {
                                Info<< "Cut cells: thin-baffle coverage " << pn
                                    << ": own patch tiles " << ratio << " but "
                                    << "faces in the sheet's plane tile "
                                    << unionA / want
                                    << " - the rest sits under other patch "
                                       "names in the same plane (coplanar "
                                       "relabel), not a hole" << nl;
                            }
                            continue;
                        }
                    }
                }
                // Solid-solid contact: a thin sheet with SOLID ON BOTH SIDES
                // — the 1U switch stacked flush under the 2U server above it
                // (DAML-Testbed's R1_switch3_top, whose 0.0629 m2 shares 85%
                // of its plane with R1_CPU6_bottom), a panel pressed onto a
                // box face — cannot carry a mesh face over the contact, and
                // none is missing: the two solids seal each other. The raw
                // STL area counts the buried part as a hole. Re-measure the
                // sheet as the area with fluid on at least one side (the
                // same adaptive probe the zone gate's openArea uses) and
                // retest the deficit against that. Pure function of the
                // geometry — every rank computes the identical number,
                // nothing to reduce.
                scalar wantOpen = 0;
                scalar ratioOpen = ratio;
                {
                    // Contact tolerance: the carve's own ON band. Surfaces
                    // closer than this are fused by the snapping anyway,
                    // and a gap thinner than ~1/10 of the finest cell can
                    // hold no cell — its slivers are killed and sealed by
                    // the merge passes — so calling it contact here matches
                    // the mesh that is actually written.
                    const scalar contactTol =
                        max(scalar(1e-4), s.onTolerance);
                    const scalar buried =
                        surfaces.regionContactArea(rgn, contactTol);
                    wantOpen = max(want - buried, scalar(0));
                    ratioOpen = wantOpen > VSMALL ? haveG / wantOpen : 1;
                    scalar allowanceOpen = 0;
                    if (nFacesG > 0 && haveG > VSMALL && wantOpen > VSMALL)
                    {
                        const scalar h = std::sqrt(haveG / scalar(nFacesG));
                        allowanceOpen =
                            (2 * h * std::sqrt(wantOpen) + h * h) / wantOpen;
                    }
                    if (ratioOpen >= minRatio - allowanceOpen)
                    {
                        if (Pstream::myProcNo() == 0)
                        {
                            Info<< "Cut cells: thin-baffle coverage " << pn
                                << ": the " << (want - wantOpen) << " m2 "
                                << "deficit has solid on both sides "
                                << "(fluid-side area " << wantOpen
                                << " m2, tiled " << ratioOpen << " of it) - "
                                << "solid-solid contact, not a hole" << nl;
                        }
                        continue;
                    }
                }
                FatalErrorInFunction
                    << "thin baffle patch " << pn << " covers only " << ratio
                    << " of its STL area (" << haveG << " of " << want
                    << " m2; of the " << wantOpen << " m2 with fluid on "
                    << "either side it tiles " << ratioOpen << "; a " << nFacesG
                    << "-face region on this grid can "
                    << "only account for " << (allowance * want) << " m2 of "
                    << "the shortfall): the mesh would leak through the gap. "
                    << "Refusing to write a mesh a solve would diverge on."
                    << (s.maxGlobalCells > 0 ? " maxGlobalCells " : "")
                    << (s.maxGlobalCells > 0 ? name(s.maxGlobalCells) : word::null)
                    << (s.maxGlobalCells > 0
                        ? " is in force: if the refinement ceiling was reached,"
                          " this sheet needs a finer level than the ceiling"
                          " allows -- raise maxGlobalCells."
                        : "")
                    << exit(FatalError);
            }
        }
    }
}


Foam::label Foam::dcCartCutCells::baffleFaceSide
(
    const cutMesh& m,
    const label f,
    const dcCartSurfaces& surfaces
)
{
    const label z = m.zone[f];
    if (z < 0) { return 0; }
    vector n; point c;
    polygonNormalCentre(m.points, m.faces[f], n, c);
    const scalar a = mag(n);
    if (a <= VSMALL) { return 0; }
    // the sheet triangle under the face: its OWN winding normal, so each
    // solid of a zone keeps its own sense (Tri_angle's 6U zone holds the
    // rack's inlet and outlet panels, facing opposite ways)
    point origin; vector ns;
    if (!surfaces.baffleNearestPlane(z, c, origin, ns)) { return 0; }
    const scalar d = (n & ns) / a;
    // a face standing across the sheet (|d| ~ 0) says nothing about the side
    if (mag(d) < 1e-6) { return 0; }
    return d < 0 ? 1 : -1;
}


Foam::label Foam::dcCartCutCells::splitBaffleZones
(
    cutMesh& m,
    const labelList& masters,
    const labelList& slaves,
    const dcCartSurfaces& surfaces,
    label* nTurned,
    labelList* undecidedPerZone
)
{
    const label nFaces = m.faces.size();
    const label me = Pstream::myProcNo();
    label nSplit = 0;
    label turned = 0;
    if (undecidedPerZone) { undecidedPerZone->setSize(masters.size(), 0); }
    for (label f = 0; f < nFaces; ++f)
    {
        const label z = m.zone[f];
        if (!m.faceAlive[f] || m.neighbour[f] < 0 || z < 0 || z >= masters.size() || masters[z] < 0) { continue; }
        // OpenFOAM's face::reverseFace(), NOT std::reverse: element 0 stays
        // put and elements 1..n-1 are reversed. Same polygon, same outward
        // direction, different starting vertex -- and for a CYCLIC pair the
        // starting vertex is the whole ballgame, because globalMeshData pairs
        // the two sides' points through it. createBaffles hands polyAddFace
        // exactly reverseFace(), and a full std::reverse here makes checkMesh
        // die in globalMeshData::findTransform ("Cannot find (4 1) or 0 ...
        // in 4((0 1) (1 1) (2 1) (3 1))") on a mesh that is otherwise
        // identical. Wall baffles never noticed: nothing couples their points.
        std::vector<label> reversed(m.faces[f].begin(), m.faces[f].end());
        std::reverse(reversed.begin() + 1, reversed.end());
        label masterCell = m.owner[f];
        label slaveCell = m.neighbour[f];
        // The master side comes from the sheet, never from the face's owner:
        // a zone face can be one an earlier pass cut in the sheet's plane
        // with its own orientation -- a sheet's free-edge split
        // (baffleEdgePreSplit, whose plane normal is sign-normalised) or a
        // server face's feature split (featurePreSplit) that cutBaffles then
        // adopted as-is. Keeping the owner made whole rows of a sheet master
        // the other way round (measured on Tri_angle's perforated panels:
        // the U18 and U23 rows of Rack_1's 6U outlet, 44 of 188 faces, and
        // Rack_2's 11U / 13U edge rows), so the two sides of one panel were
        // each a patchwork of both patches. reverseFace() is an involution:
        // turning the face and taking the original as the slave copy keeps
        // element 0 on both.
        const label side = baffleFaceSide(m, f, surfaces);
        if (side < 0)
        {
            m.faces[f].swap(reversed);
            std::swap(masterCell, slaveCell);
            if (m.cellRank.empty() || m.cellRank[masterCell] == me) { ++turned; }
        }
        else if (side == 0 && undecidedPerZone)
        {
            // The geometry could not tell: the face keeps its owner, which is
            // the old rule -- counted, so a return of the patchwork shows.
            if (m.cellRank.empty() || m.cellRank[masterCell] == me) { ++(*undecidedPerZone)[z]; }
        }
        m.owner[f] = masterCell;
        m.neighbour[f] = -1;
        m.patch[f] = masters[z];
        m.faces.push_back(std::move(reversed));
        m.owner.push_back(slaveCell); m.neighbour.push_back(-1);
        m.patch.push_back(slaves[z]); m.zone.push_back(z); m.faceAlive.push_back(true);
        ++nSplit;
    }
    if (nTurned) { *nTurned = turned; }
    return nSplit;
}

// * * * * * * * * * * * * * * * * cellVolume  * * * * * * * * * * * * * * * //

Foam::scalar Foam::dcCartCutCells::cellVolume(const cutMesh& m, label c)
{
    // 1/3 sum over faces of (cf . Sf), Sf outward
    scalar v = 0;
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        if (m.owner[f] != c && m.neighbour[f] != c) { continue; }
        vector n; point cc;
        polygonNormalCentre(m.points, m.faces[f], n, cc);
        const scalar sgn = (m.owner[f] == c) ? 1 : -1;
        v += sgn * (cc & n);
    }
    return v / 3.0;
}


// * * * * * * * * * * * * * * * mergeSmallCells * * * * * * * * * * * * * * //

namespace
{

//- A cell's name on every rank that holds it: the leaf it descends from and
//  which piece of that leaf it is (cutMesh::cellLeaf / cellPiece).
struct cellGid
{
    uint64_t a = 0;
    uint64_t b = 0;
    bool operator==(const cellGid& o) const { return a == o.a && b == o.b; }
    bool operator<(const cellGid& o) const { return a < o.a || (a == o.a && b < o.b); }
};

struct cellGidHash
{
    size_t operator()(const cellGid& g) const { return size_t(splitMix64(g.a ^ splitMix64(g.b))); }
};

inline cellGid gidOf(const Foam::dcCartCutCells::cutMesh& m, Foam::label c)
{
    return cellGid{m.cellLeaf[c], splitMix64(m.cellPiece[c] ^ (uint64_t(m.cellLevel[c]) << 56))};
}

//- Every rank's words, in rank order, on every rank. Without -parallel it
//  is this rank's alone, and the rounds that use it run unchanged.
std::vector<std::vector<uint64_t>> gatherWords(const std::vector<uint64_t>& mine)
{
    using namespace Foam;
    if (!UPstream::parRun())
    {
        return std::vector<std::vector<uint64_t>>(1, mine);
    }
    List<List<uint64_t>> all(UPstream::nProcs());
    List<uint64_t>& buf = all[UPstream::myProcNo()];
    buf.resize(label(mine.size()));
    forAll(buf, i) { buf[i] = mine[i]; }
    Pstream::allGatherList(all);
    std::vector<std::vector<uint64_t>> out(all.size());
    forAll(all, i) { out[i].assign(all[i].begin(), all[i].end()); }
    return out;
}

inline uint64_t bitsOf(Foam::scalar v) { uint64_t u = 0; std::memcpy(&u, &v, sizeof(u)); return u; }
inline Foam::scalar scalarOf(uint64_t u) { Foam::scalar v = 0; std::memcpy(&v, &u, sizeof(v)); return v; }

//- unions the vertex-mean test passed and checkMesh's centroid did not
//  (DCCART_DEBUG reports it)
Foam::label gConvexExactRejects = 0;

//- True when the union of face neighbours c and o is convex enough to be one
//  cell: its centroid lies on the inner side of every face it keeps (the
//  faces between c and o go) by 2% of the cell's thickness scale 3V/A. Not
//  of the face size: a thin convex slab has its centroid a fraction of a
//  millimetre from its big faces, and 2% of a 25 mm face called it concave.
//  The margin itself is there because checkMesh's centroid differs from
//  this one in the last digits, and a union that only just passes here
//  fails there. The face geometry and the neighbour centres come from the
//  caller's caches (the same values, computed once per round).
template<class CentreFn, class FaceGeoFn, class FaceCtrFn>
bool convexUnionT
(
    const Foam::dcCartCutCells::cutMesh& m,
    const std::vector<std::vector<Foam::label>>& cellFaces,
    Foam::label c,
    Foam::label o,
    CentreFn* centres,
    Foam::scalar guardDeg,
    FaceGeoFn& faceGeo,
    FaceCtrFn& faceCtr
)
{
    using namespace Foam;
    std::vector<label> fs;
    for (label f : cellFaces[c])
    {
        if (((m.owner[f] == c) ? m.neighbour[f] : m.owner[f]) != o) { fs.push_back(f); }
    }
    for (label f : cellFaces[o])
    {
        if (((m.owner[f] == o) ? m.neighbour[f] : m.owner[f]) != c) { fs.push_back(f); }
    }
    const point ref = m.cellRefCentre[o];
    std::vector<vector> fn(fs.size());
    std::vector<point> fc(fs.size());
    point cen = point::zero;
    scalar vol = 0, sumA = 0;
    for (size_t i = 0; i < fs.size(); ++i)
    {
        faceGeo(fs[i], fn[i], fc[i]);
        const bool out = (m.owner[fs[i]] == c || m.owner[fs[i]] == o);
        const scalar pv = (out ? 1 : -1) * ((fc[i] - ref) & fn[i]) / 3.0;
        vol += pv;
        cen += pv * (ref + 0.75 * (fc[i] - ref));
        sumA += mag(fn[i]);
    }
    if (vol <= VSMALL) { return false; }
    cen /= vol;
    const scalar hScale = (sumA > VSMALL) ? 3 * vol / sumA : 0;
    // The same test on checkMesh's own geometry: face centres area-weighted
    // (faceCtr), so the union centroid is the one the face pyramids are
    // measured from. The vertex means above put a notched union's centroid
    // up to 3 mm off it (a face with hanging vertices along one edge), and
    // an L-shaped union passed with its centroid 1.2 mm outside the notch
    // face (testbed: 8 incorrectly oriented face pyramids).
    std::vector<point> fx(fs.size());
    point cenX = point::zero;
    scalar volX = 0;
    for (size_t i = 0; i < fs.size(); ++i)
    {
        fx[i] = faceCtr(fs[i]);
        const bool out = (m.owner[fs[i]] == c || m.owner[fs[i]] == o);
        const scalar pv = (out ? 1 : -1) * ((fx[i] - ref) & fn[i]) / 3.0;
        volX += pv;
        cenX += pv * (ref + 0.75 * (fx[i] - ref));
    }
    if (volX <= VSMALL) { return false; }
    cenX /= volX;
    for (size_t i = 0; i < fs.size(); ++i)
    {
        const scalar a = mag(fn[i]);
        if (a < VSMALL) { continue; }
        const bool out = (m.owner[fs[i]] == c || m.owner[fs[i]] == o);
        if ((out ? 1 : -1) * ((fc[i] - cen) & fn[i]) / a <= 0.02 * std::min(std::sqrt(a), hScale))
        {
            return false;
        }
        // (a small margin: the coincident-point merge still moves points by
        // up to 1e-6 m after this)
        if ((out ? 1 : -1) * ((fx[i] - cenX) & fn[i]) / a <= 1e-3 * std::min(std::sqrt(a), hScale))
        {
            ++gConvexExactRejects;
            return false;
        }
    }
    if (centres && guardDeg > 0)
    {
        // The union must not face a neighbour more obliquely than guardDeg,
        // unless c or o already did: a merge that is convex can still leave a
        // zig-zag interface whose small piece is 89 degrees off the line
        // between the two cell centres.
        auto worstOf = [&](label x, const point& cx, label skip) -> scalar
        {
            scalar worst = 0;
            for (label f : cellFaces[x])
            {
                if (m.neighbour[f] < 0) { continue; }
                const label other = (m.owner[f] == x) ? m.neighbour[f] : m.owner[f];
                if (other == skip) { continue; }
                vector n; point fcc;
                faceGeo(f, n, fcc);
                if (m.owner[f] != x) { n = -n; }
                const vector dd = (*centres)(other) - cx;
                const scalar den = mag(n) * mag(dd);
                if (den < VSMALL) { continue; }
                worst = std::max(worst, radToDeg(std::acos(std::min(scalar(1), std::max(scalar(-1), (n & dd) / den)))));
            }
            return worst;
        };
        const scalar before = std::max(worstOf(c, (*centres)(c), -1), worstOf(o, (*centres)(o), -1));
        scalar after = 0;
        for (size_t i = 0; i < fs.size(); ++i)
        {
            const label f = fs[i];
            if (m.neighbour[f] < 0) { continue; }
            const bool out = (m.owner[f] == c || m.owner[f] == o);
            const label other = out ? m.neighbour[f] : m.owner[f];
            const vector n = out ? fn[i] : -fn[i];
            const vector dd = (*centres)(other) - cen;
            const scalar den = mag(n) * mag(dd);
            if (den < VSMALL) { continue; }
            after = std::max(after, radToDeg(std::acos(std::min(scalar(1), std::max(scalar(-1), (n & dd) / den)))));
        }
        if (after > std::max(guardDeg, before) + 1e-9) { return false; }
    }
    return true;
}

//- Face area vector and centre as OpenFOAM computes them
//  (primitiveMeshTools::makeFaceCentresAndAreas: a fan of triangles about
//  the vertex average, the centre weighted by the triangle areas). checkMesh
//  measures face pyramids and skewness with these. polygonNormalCentre's
//  vertex mean is NOT the centroid of a face that carries hanging vertices
//  along one edge, and a cell centre built from vertex means sits
//  millimetres off checkMesh's.
inline void faceAreaCentreOF
(
    const std::vector<Foam::point>& pts,
    const std::vector<Foam::label>& poly,
    Foam::vector& A,
    Foam::point& C
)
{
    using namespace Foam;
    A = vector::zero;
    C = point::zero;
    const label k = label(poly.size());
    if (k < 3)
    {
        for (label v : poly) { C += pts[v]; }
        if (k) { C /= scalar(k); }
        return;
    }
    if (k == 3)
    {
        C = (pts[poly[0]] + pts[poly[1]] + pts[poly[2]]) / 3.0;
        A = 0.5 * ((pts[poly[1]] - pts[poly[0]]) ^ (pts[poly[2]] - pts[poly[0]]));
        return;
    }
    point fc = point::zero;
    for (label i = 0; i < k; ++i) { fc += pts[poly[i]]; }
    fc /= scalar(k);
    vector sumN = vector::zero;
    vector sumAc = vector::zero;
    scalar sumA = 0;
    for (label i = 0; i < k; ++i)
    {
        const point& p = pts[poly[i]];
        const point& q = pts[poly[(i + 1) % k]];
        const vector n = (q - p) ^ (fc - p);
        const scalar a = mag(n);
        sumN += n;
        sumA += a;
        sumAc += a * (p + q + fc);
    }
    if (sumA < ROOTVSMALL)
    {
        C = fc;
        return;
    }
    // Match primitiveMeshTools operation order, including rounding at the
    // strict skewness limit. Algebraically equivalent expressions can fall
    // on opposite sides of checkMesh's boundary threshold.
    C = (1.0 / 3.0) * sumAc / sumA;
    A = 0.5 * sumN;
}


point restore(const std::vector<point>& points,const std::vector<label>& poly,const vector& av,const point& fc){
    struct Source {const std::vector<point>& points;} m{points};
                // New fan points must stay on a bit-literal axis support plane.
                // Keep the original numeric centroid for metrics/conservation;
                // only the coordinate passed to insertPoint is restored.
                point fanCentre=fc;
                const scalar fanAreaSqr=magSqr(av);
                bool finiteFan=poly.size()>=3 && std::isfinite(fanAreaSqr)
                    && fanAreaSqr>0;
                for (direction k=0;k<3;++k)
                    finiteFan=finiteFan && std::isfinite(fc[k]);
                for (label v:poly) for (direction k=0;k<3;++k)
                    finiteFan=finiteFan && std::isfinite(m.points[v][k]);
                if (finiteFan)
                {
                    direction supportAxis=0;label fixedAxes=0;
                    for (direction k=0;k<3;++k)
                    {
                        const scalar plane=m.points[poly[0]][k];bool fixed=true;
                        for (label v:poly)
                        {
                            const scalar value=m.points[v][k];
                            if (std::memcmp(&plane,&value,sizeof(scalar))!=0)
                            { fixed=false;break; }
                        }
                        if (fixed) { supportAxis=k;++fixedAxes; }
                    }
                    // A degenerate line/point or non-axis plane is not this grammar.
                    // Signed-zero mixtures are not a bit-literal fixed component.
                    if (fixedAxes==1)
                        fanCentre[supportAxis]=m.points[poly[0]][supportAxis];
                }

    return fanCentre;
}
struct Control{const char* name;std::vector<point> vertices;point fc;vector av;point expected;};
struct Authentic{label source;std::vector<point> vertices;std::vector<label> canonical;point expectedFc;vector expectedAv;};
int main(){
 struct rlimit core{0,0};setrlimit(RLIMIT_CORE,&core);
 std::vector<Control> controls={{"actual-source-3579", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb333340000000ULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"actual-source-3526", {P(0x3ff0000000000000ULL,0x3ff0000000000000ULL,0x3fee666660000000ULL),P(0x3ff0000000000000ULL,0x3ff4000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3ff4000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3fee666660000000ULL),P(0x3ff0000000000000ULL,0x3fecccccc0000000ULL,0x3fee666660000000ULL)}, P(0x3fed9999a0000000ULL,0x3ff1333330000001ULL,0x3fee66665ffffffeULL), P(0x0000000000000000ULL,0x0000000000000000ULL,0xbfaae147851eb800ULL), P(0x3fed9999a0000000ULL,0x3ff1333330000001ULL,0x3fee666660000000ULL)},{"one-source-vertex-shifted-nonconstant", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000001ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"tilted-no-axis-support", {P(0x3ff07ae14ccccccdULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3ff0ccccd3333333ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3ff0ccccd3333333ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3ff0ccccd3333333ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3ff07ae14ccccccdULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3ff07ae14ccccccdULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"finite-non-axis-triangle", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x3ff0000000000000ULL,0x0000000000000000ULL,0x3ff0000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL)}, P(0x3fd3333333333333ULL,0x3fd3333333333333ULL,0x3fe3333333333333ULL), P(0x3ff0000000000000ULL,0x3ff0000000000000ULL,0xbff0000000000000ULL), P(0x3fd3333333333333ULL,0x3fd3333333333333ULL,0x3fe3333333333333ULL)},{"NaN-original-vertex", {P(0x7ff8000000000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"Inf-original-vertex", {P(0x3feb333340000000ULL,0x7ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"nonfinite-original-centre", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x7ff8000000000000ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x7ff8000000000000ULL,0x3ff666665fffffffULL)},{"nonfinite-area", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0x7ff0000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"overflow-area-square", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0x7fe1ccf385ebc8a0ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"degenerate-line", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL),P(0x0000000000000000ULL,0x4000000000000000ULL,0x4000000000000000ULL)}, P(0x3fb999999999999aULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL), P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3fb999999999999aULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL)},{"degenerate-repeated-point", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL)}, P(0x3fb999999999999aULL,0x3fb999999999999aULL,0x3fb999999999999aULL), P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3fb999999999999aULL,0x3fb999999999999aULL,0x3fb999999999999aULL)},{"tiny-area-square-underflow", {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0x16687e92154ef7acULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL)},{"mixed-signed-zero-support-not-bit-literal", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x8000000000000000ULL,0x3ff0000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL),P(0x0000000000000000ULL,0x0000000000000000ULL,0x3ff0000000000000ULL)}, P(0x3fb999999999999aULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL), P(0x3ff0000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3fb999999999999aULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL)},{"uniform-positive-zero-support", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL),P(0x0000000000000000ULL,0x0000000000000000ULL,0x3ff0000000000000ULL)}, P(0x8000000000000000ULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL), P(0x3ff0000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x0000000000000000ULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL)},{"uniform-negative-zero-support", {P(0x8000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x8000000000000000ULL,0x3ff0000000000000ULL,0x0000000000000000ULL),P(0x8000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL),P(0x8000000000000000ULL,0x0000000000000000ULL,0x3ff0000000000000ULL)}, P(0x0000000000000000ULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL), P(0x3ff0000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x8000000000000000ULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL)},{"fewer-than-three-source-vertices", {P(0x0000000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL),P(0x0000000000000000ULL,0x3ff0000000000000ULL,0x3ff0000000000000ULL)}, P(0x3fb999999999999aULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL), P(0x3ff0000000000000ULL,0x0000000000000000ULL,0x0000000000000000ULL), P(0x3fb999999999999aULL,0x3fe0000000000000ULL,0x3fe0000000000000ULL)}};std::vector<Authentic> actual={{3579, {P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff4000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8000000000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8ccccc0000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3ff8000000000000ULL)}, {0,5,4,3,2,1}, P(0x3feb33333fffffffULL,0x3fee666660000001ULL,0x3ff666665fffffffULL), P(0xbf9eb852147ae000ULL,0x0000000000000000ULL,0x0000000000000000ULL)},{3526, {P(0x3ff0000000000000ULL,0x3ff0000000000000ULL,0x3fee666660000000ULL),P(0x3ff0000000000000ULL,0x3ff4000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3ff4000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3ff0000000000000ULL,0x3fee666660000000ULL),P(0x3feb333340000000ULL,0x3fecccccc0000000ULL,0x3fee666660000000ULL),P(0x3ff0000000000000ULL,0x3fecccccc0000000ULL,0x3fee666660000000ULL)}, {4,3,2,1,0,5}, P(0x3fed9999a0000000ULL,0x3ff1333330000001ULL,0x3fee66665ffffffeULL), P(0x0000000000000000ULL,0x0000000000000000ULL,0xbfaae147851eb800ULL)}};bool good=true;
 for(const auto& c:controls){std::vector<label> poly;for(label i=0;i<label(c.vertices.size());++i)poly.push_back(i);const vector beforeA=c.av;const point beforeC=c.fc;
  feclearexcept(FE_ALL_EXCEPT);const point out=restore(c.vertices,poly,c.av,c.fc);const int exceptions=fetestexcept(FE_ALL_EXCEPT);const bool pass=same(out,c.expected)&&same(c.av,beforeA)&&same(c.fc,beforeC);good=good&&pass;
  std::cout<<"{\"kind\":\"local-property\",\"name\":\""<<c.name<<"\",\"pass\":"<<(pass?"true":"false")<<",\"exceptions\":"<<exceptions<<",\"afterBits\":";emit(out);std::cout<<"}\n";
 }
 for(const auto& c:actual){vector av;point fc;faceAreaCentreOF(c.vertices,c.canonical,av,fc);const vector saved=av;const point savedFc=fc;const point out=restore(c.vertices,c.canonical,av,fc);bool tangential=true;int axes=0;
  for(direction k=0;k<3;++k){bool fixed=true;for(const point& v:c.vertices)fixed=fixed&&(bits(v[k])==bits(c.vertices[0][k]));if(fixed){++axes;tangential=tangential&&bits(out[k])==bits(c.vertices[0][k]);}else tangential=tangential&&bits(out[k])==bits(fc[k]);}
  const bool pass=same(fc,c.expectedFc)&&same(av,c.expectedAv)&&same(av,saved)&&same(fc,savedFc)&&axes==1&&tangential;good=good&&pass;
  std::cout<<"{\"kind\":\"authentic-helper\",\"source\":"<<c.source<<",\"pass\":"<<(pass?"true":"false")<<",\"fcBits\":";emit(fc);std::cout<<",\"avBits\":";emit(av);std::cout<<",\"fanBits\":";emit(out);std::cout<<"}\n";
 }
 // Direct isolated magnitude exception probes, not full mesher reachability.
 for(scalar x:{scalar(1e308),scalar(1e-200),scalar(0),scalar(1)}){
  const vector av(x,0,0);feclearexcept(FE_ALL_EXCEPT);volatile scalar old=mag(av);int oldFlags=fetestexcept(FE_ALL_EXCEPT);feclearexcept(FE_ALL_EXCEPT);volatile scalar added=magSqr(av);int newFlags=fetestexcept(FE_ALL_EXCEPT);
  const bool sameClassification=(oldFlags&(FE_INVALID|FE_DIVBYZERO|FE_OVERFLOW|FE_UNDERFLOW))==(newFlags&(FE_INVALID|FE_DIVBYZERO|FE_OVERFLOW|FE_UNDERFLOW));good=good&&sameClassification;
  std::cout<<"{\"kind\":\"magnitude-flags\",\"value\":"<<std::setprecision(17)<<x<<",\"oldFlags\":"<<oldFlags<<",\"newFlags\":"<<newFlags<<",\"sameClassification\":"<<(sameClassification?"true":"false")<<"}\n";
  for(int mode=0;mode<2;++mode){pid_t pid=fork();if(pid==0){feclearexcept(FE_ALL_EXCEPT);feenableexcept(FE_INVALID|FE_DIVBYZERO|FE_OVERFLOW|FE_UNDERFLOW);volatile scalar input=x;const vector a(input,0,0);volatile scalar result=mode?magSqr(a):mag(a);(void)result;_exit(0);}int status=0;waitpid(pid,&status,0);std::cout<<"{\"kind\":\"isolated-trap\",\"value\":"<<x<<",\"operation\":\""<<(mode?"new-magSqr":"old-mag")<<"\",\"signal\":"<<(WIFSIGNALED(status)?WTERMSIG(status):0)<<",\"exit\":"<<(WIFEXITED(status)?WEXITSTATUS(status):-1)<<"}\n";}
 }
 std::cout<<"{\"kind\":\"summary\",\"localProperties\":"<<controls.size()<<",\"authenticHelperCases\":"<<actual.size()<<",\"pass\":"<<(good?"true":"false")<<"}\n";return good?0:1;
}
