#include "dcCartCutCells.H"
#include "dcCartFinitePointRepair.H"
#include "dcCartWeightUnionPreview.H"
#include "dcCartWriterNumericContext.H"
#include "dcCartCompleteCavityLocalOracle.H"
#include "dcCartCompleteCavityRepair.H"
#include "dcCartCompleteCavityReplayObservation.H"
#include "dcCartCompleteCavityFullCapture.H"
#include "dcCartPhysicalPointLifecycle.H"
namespace dcCartCompleteCavityReplaySourceSidecar
{
thread_local Reference state;
}
namespace Foam { namespace dcCartCompleteCavityReplayObservation
{ thread_local Pending pending; }}
namespace Foam { namespace dcCartCompleteCavityFullCapture
{ thread_local Pending pending; }}
#include "polyMesh.H"
#include "Time.H"
#include "polyPatch.H"
#include "processorPolyPatch.H"
#include "faceZone.H"
#include "cellZone.H"
#include "pointZone.H"
#include "boundBox.H"
#include "Pstream.H"
#include "unitConversion.H"
#include "PstreamReduceOps.H"
#include "clockTime.H"
#include "tetPointRef.H"
#include "symmTensor.H"
#include "IOstreams.H"
#include "IStringStream.H"
#include "OStringStream.H"
#include "OSspecific.H"
#include <fstream>
#include <sstream>
#include <iomanip>
#include <cstring>
#include <string>
#include <map>
#include <set>
#include <unordered_map>
#include <unordered_set>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <functional>
#include <map>
#include <set>
#include <tuple>
#include <numeric>
#include <limits>
#include <type_traits>

// * * * * * * * * * * * * * * * Local helpers * * * * * * * * * * * * * * * //

namespace
{

using Foam::label;
using Foam::point;
using Foam::vector;
using Foam::scalar;

//- 64-bit mixer (splitmix64 finaliser)
inline uint64_t splitMix64(uint64_t x)
{
    x += 0x9E3779B97F4A7C15ull;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ull;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBull;
    return x ^ (x >> 31);
}

//- The piece id of one half of a split piece (half 1 = A, 2 = B)
inline uint64_t pieceHash(uint64_t parent, uint64_t half)
{
    return splitMix64(parent * 0x9E3779B97F4A7C15ull + half);
}

//- Key of a point position, for finding coincident points (0.1 um grid)
inline std::tuple<long long, long long, long long> pointKey(const Foam::point& p)
{
    return std::make_tuple
    (
        (long long)std::llround(p.x() * 1e7),
        (long long)std::llround(p.y() * 1e7),
        (long long)std::llround(p.z() * 1e7)
    );
}

//- One id per position, to a tolerance: a crossing interpolated along one
//  edge and the same crossing made along another differ in the last
//  digits (1e-6 m seen), which a rounded key puts in different buckets.
//  The bucket is 1e-6 m and the 27 buckets around a point are searched.
struct pointCanon
{
    // bucket key -> the id it holds, in a flat open-addressing table (a
    // std::map of tuples cost a tree walk for each of the 27 buckets asked
    // per point; only lookups and overwrites are ever made)
    struct slot
    {
        long long x = 0, y = 0, z = 0;
        Foam::label v = -1;
        bool used = false;
    };
    std::vector<slot> table;
    size_t n = 0;
    Foam::scalar tol = 1e-6;

    static size_t hash3(long long x, long long y, long long z)
    {
        uint64_t h = uint64_t(x) * 0x9E3779B97F4A7C15ULL;
        h ^= uint64_t(y) + 0x632BE59BD9B4E019ULL + (h << 6) + (h >> 2);
        h ^= uint64_t(z) + 0x8CB92BA72F3D8DD7ULL + (h << 6) + (h >> 2);
        h ^= h >> 31;
        h *= 0xBF58476D1CE4E5B9ULL;
        h ^= h >> 27;
        return size_t(h);
    }

    const slot* lookup(long long x, long long y, long long z) const
    {
        if (table.empty()) { return nullptr; }
        const size_t mask = table.size() - 1;
        for (size_t i = hash3(x, y, z) & mask; ; i = (i + 1) & mask)
        {
            const slot& s = table[i];
            if (!s.used) { return nullptr; }
            if (s.x == x && s.y == y && s.z == z) { return &s; }
        }
    }

    void assign(long long x, long long y, long long z, Foam::label v)
    {
        if (2*(n + 1) > table.size())
        {
            std::vector<slot> old;
            old.swap(table);
            table.assign(old.empty() ? size_t(16) : 2*old.size(), slot());
            n = 0;
            for (const slot& o : old) { if (o.used) { assign(o.x, o.y, o.z, o.v); } }
        }
        const size_t mask = table.size() - 1;
        for (size_t i = hash3(x, y, z) & mask; ; i = (i + 1) & mask)
        {
            slot& s = table[i];
            if (!s.used)
            {
                s.x = x; s.y = y; s.z = z; s.v = v; s.used = true;
                ++n;
                return;
            }
            if (s.x == x && s.y == y && s.z == z) { s.v = v; return; }
        }
    }

    Foam::label find(const std::vector<Foam::point>& pts, const Foam::point& p) const
    {
        const long long kx = (long long)std::llround(p.x() * 1e6);
        const long long ky = (long long)std::llround(p.y() * 1e6);
        const long long kz = (long long)std::llround(p.z() * 1e6);
        Foam::label best = -1; Foam::scalar bestD = tol * tol;
        for (int dx = -1; dx <= 1; ++dx) for (int dy = -1; dy <= 1; ++dy) for (int dz = -1; dz <= 1; ++dz)
        {
            const slot* it = lookup(kx + dx, ky + dy, kz + dz);
            if (!it) { continue; }
            const Foam::scalar d = Foam::magSqr(pts[it->v] - p);
            if (d <= bestD) { bestD = d; best = it->v; }
        }
        return best;
    }

    //- Like find(), but a slot may hold a representative that a LATER,
    //  canonical one superseded (alias[r] != r): measure against the
    //  current representative of the cluster, not the stale slot id.
    Foam::label find
    (
        const std::vector<Foam::point>& pts,
        const Foam::point& p,
        const std::vector<Foam::label>& alias
    ) const
    {
        const long long kx = (long long)std::llround(p.x() * 1e6);
        const long long ky = (long long)std::llround(p.y() * 1e6);
        const long long kz = (long long)std::llround(p.z() * 1e6);
        Foam::label best = -1; Foam::scalar bestD = tol * tol;
        for (int dx = -1; dx <= 1; ++dx) for (int dy = -1; dy <= 1; ++dy) for (int dz = -1; dz <= 1; ++dz)
        {
            const slot* it = lookup(kx + dx, ky + dy, kz + dz);
            if (!it) { continue; }
            Foam::label r = it->v;
            label guard = 0;
            while (r >= 0 && r < Foam::label(alias.size()) && alias[r] != r && ++guard < 64)
            {
                r = alias[r];
            }
            if (r < 0) { continue; }
            const Foam::scalar d = Foam::magSqr(pts[r] - p);
            if (d <= bestD) { bestD = d; best = r; }
        }
        return best;
    }
    //- id already there within tol, else insert v and return v
    Foam::label canon(const std::vector<Foam::point>& pts, Foam::label v)
    {
        const Foam::label got = find(pts, pts[v]);
        if (got >= 0) { return got; }
        const Foam::point& p = pts[v];
        assign((long long)std::llround(p.x() * 1e6), (long long)std::llround(p.y() * 1e6), (long long)std::llround(p.z() * 1e6), v);
        return v;
    }

    //- Canonical coordinate order: quantised nm grid, then the raw bits.
    //  A total order on positions that every rank computes the same way.
    static bool coordLess(const Foam::point& a, const Foam::point& b)
    {
        for (int k = 0; k < 3; ++k)
        {
            const long long qa = (long long)std::llround(a.component(k) * 1e9);
            const long long qb = (long long)std::llround(b.component(k) * 1e9);
            if (qa != qb) { return qa < qb; }
        }
        auto bits = [](Foam::scalar v) -> uint64_t
        {
            uint64_t u = 0;
            std::memcpy(&u, &v, sizeof(v));
            return u;
        };
        for (int k = 0; k < 3; ++k)
        {
            const uint64_t ba = bits(a.component(k));
            const uint64_t bb = bits(b.component(k));
            if (ba != bb) { return ba < bb; }
        }
        return false;
    }

    //- Rank-invariant canon. The representative of a coincident cluster is
    //  the CANONICAL MINIMUM of its members — a grid corner beats any cut
    //  point (the lattice must not be dragged 1e-6 m off-grid), and among
    //  cut points the smallest (quantised) coordinate wins — instead of
    //  whichever id this rank happened to append first. Cut points are
    //  appended in each rank's own pass order, so a first-wins rule picks
    //  different surviving coordinates on the owner of an interface cell
    //  and on the rank holding it as halo: the two meshes then disagree
    //  bit-wise, split histories diverge, cell global ids stop matching,
    //  and the processor patches end up with different face sets (a
    //  checkMesh segfault in processorPolyPatch::updateMesh). With the
    //  minimum rule the representative is a pure function of the cluster's
    //  coordinate multiset, which is the same on every rank. Superseding
    //  an incumbent repoints both buckets and chains the old rep through
    //  `alias`, resolved by the caller.
    Foam::label canonMin
    (
        const std::vector<Foam::point>& pts,
        Foam::label v,
        std::vector<Foam::label>& alias,
        const Foam::label nGridPoints
    )
    {
        const Foam::label got = find(pts, pts[v], alias);
        const Foam::point& p = pts[v];
        const long long kx = (long long)std::llround(p.x() * 1e6);
        const long long ky = (long long)std::llround(p.y() * 1e6);
        const long long kz = (long long)std::llround(p.z() * 1e6);
        if (got < 0)
        {
            assign(kx, ky, kz, v);
            return v;
        }
        const bool vGrid = (v < nGridPoints);
        const bool gotGrid = (got < nGridPoints);
        bool vWins;
        if (vGrid != gotGrid) { vWins = vGrid; }
        else if (vGrid && gotGrid) { vWins = (v < got); }
        else { vWins = coordLess(p, pts[got]); }
        if (!vWins) { return got; }
        // v takes over the cluster
        alias[got] = v;
        assign(kx, ky, kz, v);
        const Foam::point& q = pts[got];
        assign
        (
            (long long)std::llround(q.x() * 1e6),
            (long long)std::llround(q.y() * 1e6),
            (long long)std::llround(q.z() * 1e6),
            v
        );
        return v;
    }
};

inline uint64_t edgeKey(label a, label b)
{
    if (a > b) { std::swap(a, b); }
    return (uint64_t(uint32_t(a)) << 32) | uint64_t(uint32_t(b));
}

//- Newell normal (magnitude = area) and centroid of a polygon
void polygonNormalCentre
(
    const std::vector<point>& pts,
    const std::vector<label>& poly,
    vector& n,
    point& c
)
{
    n = vector::zero;
    c = point::zero;
    const label k = label(poly.size());
    if (k < 3) { return; }
    for (label i = 0; i < k; ++i)
    {
        const point& a = pts[poly[i]];
        const point& b = pts[poly[(i + 1) % k]];
        n.x() += (a.y() - b.y()) * (a.z() + b.z());
        n.y() += (a.z() - b.z()) * (a.x() + b.x());
        n.z() += (a.x() - b.x()) * (a.y() + b.y());
        c += a;
    }
    n *= 0.5;
    c /= scalar(k);
}

//- Faces of every live cell
void buildCellFaces
(
    const Foam::dcCartCutCells::cutMesh& m,
    std::vector<std::vector<label>>& cellFaces
)
{
    // counted first: one allocation per cell instead of one per doubling
    const label nF = label(m.faces.size());
    std::vector<label> count(m.nCells, 0);
    for (label f = 0; f < nF; ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        if (m.owner[f] >= 0) { ++count[m.owner[f]]; }
        if (m.neighbour[f] >= 0) { ++count[m.neighbour[f]]; }
    }
    cellFaces.assign(m.nCells, {});
    for (label c = 0; c < m.nCells; ++c)
    {
        if (count[c]) { cellFaces[c].reserve(count[c]); }
    }
    for (label f = 0; f < nF; ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        if (m.owner[f] >= 0) { cellFaces[m.owner[f]].push_back(f); }
        if (m.neighbour[f] >= 0) { cellFaces[m.neighbour[f]].push_back(f); }
    }
}

//- Chain undirected chords into closed loops. False when any vertex has a
//  degree other than 2 (the cap would not close).
bool chainLoops
(
    const std::vector<std::pair<label, label>>& chords,
    std::vector<std::vector<label>>& loops
)
{
    loops.clear();
    if (chords.empty()) { return false; }
    // Ordered by vertex id, not hashed: the loops (and so the vertex order of
    // every cap) must not depend on how the ids hash. The ids keep their
    // relative order on every rank and in a shell's neighbourhood, so a
    // hashed walk started the same loop at different vertices there, and a
    // face split downstream came out in different pieces and patches.
    std::map<label, std::vector<label>> adj;
    for (const auto& e : chords)
    {
        if (e.first == e.second) { continue; }
        adj[e.first].push_back(e.second);
        adj[e.second].push_back(e.first);
    }
    for (const auto& kv : adj)
    {
        if (kv.second.size() != 2) { return false; }
    }
    std::unordered_set<label> used;
    for (const auto& kv : adj)
    {
        if (used.count(kv.first)) { continue; }
        std::vector<label> loop;
        label prev = -1;
        label cur = kv.first;
        while (true)
        {
            loop.push_back(cur);
            used.insert(cur);
            const auto& nb = adj[cur];
            label next = (nb[0] != prev) ? nb[0] : nb[1];
            if (nb[0] == prev && nb[1] == prev) { return false; }
            prev = cur;
            cur = next;
            if (cur == kv.first) { break; }
            if (used.count(cur)) { return false; }
            if (loop.size() > adj.size()) { return false; }
        }
        if (loop.size() < 3) { return false; }
        loops.push_back(loop);
    }
    return !loops.empty();
}


// ---- 2D clipping, for splitting a cut face by the patch regions it spans --

// onLoop: the vertex lies on the cap's boundary loop (a crossing point
// or a point cut on a loop edge) and must stay where it is for the cell to
// close; edgeLoop: the polygon edge leaving this vertex is (part of) a loop
// edge, as opposed to a cut line.
struct v2
{
    scalar x, y; Foam::point p; bool onLoop = true; bool edgeLoop = true;
    // the loop edge (vertex ids) this point lies on, for a point cut on one
    Foam::label ea = -1, eb = -1;
};
//- A point to splice into a cell face: between vertices a and b, insert x
struct chordInsert { Foam::label a, b, x; };

//- Squared distance from p to the segment ab (a point when a == b)
inline scalar pointSegDist2(const point& p, const point& a, const point& b)
{
    const vector ab = b - a;
    const scalar l2 = Foam::magSqr(ab);
    if (!(l2 > 0)) { return Foam::magSqr(p - a); }
    const scalar t = std::min(std::max(((p - a) & ab) / l2, scalar(0)), scalar(1));
    return Foam::magSqr(p - (a + t * ab));
}

//- Squared distance from p to the triangle abc (Ericson, Real-Time Collision
//  Detection 5.1.5). A triangle with no area (STL files carry them: two
//  equal corners, three in a line) is its longest edge, and every division
//  below is by the square of an edge or of the area: such a triangle is
//  measured as its three edges instead (a division by zero is a floating
//  point trap, and the data hall's raised-floor baffle has six of them).
inline scalar pointTriDist2(const point& p, const point& a, const point& b, const point& c)
{
    const vector ab = b - a, ac = c - a, ap = p - a;
    auto edges = [&]()
    {
        return std::min(std::min(pointSegDist2(p, a, b), pointSegDist2(p, b, c)), pointSegDist2(p, c, a));
    };
    if (!(Foam::magSqr(ab ^ ac) > 0)) { return edges(); }
    const scalar d1 = ab & ap, d2 = ac & ap;
    if (d1 <= 0 && d2 <= 0) { return Foam::magSqr(ap); }
    const vector bp = p - b;
    const scalar d3 = ab & bp, d4 = ac & bp;
    if (d3 >= 0 && d4 <= d3) { return Foam::magSqr(bp); }
    const scalar vc = d1 * d4 - d3 * d2;
    if (vc <= 0 && d1 >= 0 && d3 <= 0)
    {
        if (!(d1 - d3 > 0)) { return edges(); }
        const scalar v = d1 / (d1 - d3);
        return Foam::magSqr(p - (a + v * ab));
    }
    const vector cp = p - c;
    const scalar d5 = ab & cp, d6 = ac & cp;
    if (d6 >= 0 && d5 <= d6) { return Foam::magSqr(cp); }
    const scalar vb = d5 * d2 - d1 * d6;
    if (vb <= 0 && d2 >= 0 && d6 <= 0)
    {
        if (!(d2 - d6 > 0)) { return edges(); }
        const scalar w = d2 / (d2 - d6);
        return Foam::magSqr(p - (a + w * ac));
    }
    const scalar va = d3 * d6 - d5 * d4;
    if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0)
    {
        if (!((d4 - d3) + (d5 - d6) > 0)) { return edges(); }
        const scalar w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
        return Foam::magSqr(p - (b + w * (c - b)));
    }
    if (!(va + vb + vc > 0)) { return edges(); }
    const scalar denom = 1.0 / (va + vb + vc);
    const scalar v = vb * denom, w = vc * denom;
    return Foam::magSqr(p - (a + ab * v + ac * w));
}

//- Squared distance between the segments p1-q1 and p2-q2 (Ericson 5.1.9)
inline scalar segSegDist2(const point& p1, const point& q1, const point& p2, const point& q2)
{
    const vector d1 = q1 - p1, d2 = q2 - p2, r = p1 - p2;
    const scalar a = Foam::magSqr(d1), e = Foam::magSqr(d2), f = d2 & r;
    const scalar eps = 1e-30;
    scalar s = 0, t = 0;
    if (a <= eps && e <= eps) { return Foam::magSqr(r); }
    if (a <= eps)
    {
        t = std::min(std::max(f / e, scalar(0)), scalar(1));
    }
    else
    {
        const scalar c = d1 & r;
        if (e <= eps)
        {
            s = std::min(std::max(-c / a, scalar(0)), scalar(1));
        }
        else
        {
            const scalar b = d1 & d2;
            const scalar denom = a * e - b * b;
            s = (denom > eps) ? std::min(std::max((b * f - c * e) / denom, scalar(0)), scalar(1)) : 0;
            t = (b * s + f) / e;
            if (t < 0) { t = 0; s = std::min(std::max(-c / a, scalar(0)), scalar(1)); }
            else if (t > 1) { t = 1; s = std::min(std::max((b - c) / a, scalar(0)), scalar(1)); }
        }
    }
    return Foam::magSqr((p1 + s * d1) - (p2 + t * d2));
}

//- Squared distance between the segment p-q and the triangle abc
inline scalar segTriDist2(const point& p, const point& q, const point& a, const point& b, const point& c)
{
    scalar d = std::min(pointTriDist2(p, a, b, c), pointTriDist2(q, a, b, c));
    d = std::min(d, segSegDist2(p, q, a, b));
    d = std::min(d, segSegDist2(p, q, b, c));
    d = std::min(d, segSegDist2(p, q, c, a));
    const vector n = (b - a) ^ (c - a);
    const scalar dp = (p - a) & n, dq = (q - a) & n;
    if (((dp <= 0 && dq >= 0) || (dp >= 0 && dq <= 0)) && dp != dq)
    {
        const point x = p + (dp / (dp - dq)) * (q - p);
        d = std::min(d, pointTriDist2(x, a, b, c));
    }
    return d;
}

inline scalar cross2(const v2& o, const v2& a, const v2& b)
{
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

inline scalar area2(const std::vector<v2>& P)
{
    scalar a = 0;
    const size_t n = P.size();
    for (size_t i = 0; i < n; ++i)
    {
        const v2& p = P[i];
        const v2& q = P[(i + 1) % n];
        a += p.x * q.y - q.x * p.y;
    }
    return 0.5 * a;
}

//- Sutherland-Hodgman against one half-plane: keep the left of a -> b
std::vector<v2> clipHalfPlane(const std::vector<v2>& P, const v2& a, const v2& b)
{
    std::vector<v2> out;
    const size_t n = P.size();
    if (!n) { return out; }
    // A vertex within 10 um of the clip line is ON it: a polygon edge that
    // runs along the line (a cap edge on the seam of two sheets) then
    // yields no ill-conditioned intersection point a millimetre off.
    const scalar len = std::sqrt((b.x - a.x) * (b.x - a.x) + (b.y - a.y) * (b.y - a.y));
    const scalar eps = 1e-5 * std::max(len, scalar(1e-12));
    for (size_t i = 0; i < n; ++i)
    {
        const v2& s0 = P[i];
        const v2& e0 = P[(i + 1) % n];
        const scalar ds = cross2(a, b, s0);
        const scalar de = cross2(a, b, e0);
        const bool sIn = ds >= -eps, eIn = de >= -eps;
        if (sIn) { out.push_back(s0); }
        if (sIn != eIn)
        {
            const scalar t = ds / (ds - de);
            out.push_back
            ({
                s0.x + t * (e0.x - s0.x),
                s0.y + t * (e0.y - s0.y),
                s0.p + t * (e0.p - s0.p)
            });
        }
    }
    return out;
}

// folded-cap statistics (DCCART_DEBUG)
static Foam::label gFolded = 0, gFoldedNoFacet = 0, gFoldedNoLines = 0, gFoldedGuard = 0, gFoldedSplit = 0;

//- Split a cap polygon into one piece per surface triangle it covers, each
//  piece carrying that triangle's region. This is what lets ONE cell be cut
//  by several patches at once — a cap across a box edge, or across the seam
//  between a face and the patch drawn on it, is no longer voted to a single
//  patch. Pieces are built in the cap's own plane; a triangle counts only
//  when it faces the fluid side of the cap and lies within the cell. False
//  (caller keeps the whole cap) when the triangles do not tile the cap.
template<class TrisFn>
bool splitCapByTriangles
(
    Foam::dcCartCutCells::cutMesh& m,
    std::vector<signed char>& side,
    TrisFn&& trisInBox,
    const Foam::boundBox& cellBox,
    const std::vector<label>& loop,
    const Foam::point& fluidCentre,
    std::vector<std::pair<std::vector<label>, label>>& pieces,
    std::vector<chordInsert>& inserts,
    const Foam::scalar onTol = 0
)
{
    pieces.clear();
    inserts.clear();
    vector n; point cen;
    polygonNormalCentre(m.points, loop, n, cen);
    const scalar area = Foam::mag(n);
    if (area < 1e-14) { return false; }
    const vector nu = n / area;
    vector e(1, 0, 0);
    if (std::abs(nu.x()) > 0.9) { e = vector(0, 1, 0); }
    vector u = e - (e & nu) * nu;
    u /= Foam::mag(u);
    const vector v = nu ^ u;
    auto to2 = [&](const point& p) { const vector d = p - cen; return v2{d & u, d & v, p}; };

    std::vector<v2> P;
    P.reserve(loop.size());
    scalar offPlane = 0;
    for (size_t i = 0; i < loop.size(); ++i)
    {
        const label id = loop[i];
        v2 q = to2(m.points[id]);
        q.ea = id; q.eb = loop[(i + 1) % loop.size()];
        P.push_back(q);
        offPlane = std::max(offPlane, std::abs((m.points[id] - cen) & nu));
    }
    if (area2(P) <= 0) { return false; }
    // A cap that folds over a box edge (crossings on two faces of the shell)
    // is not planar. Its pieces would have to be built on the surface, not on
    // the mean plane, or the cell stops closing — so it stays ONE face, and
    // the region is the one covering most of its projected area, which is
    // what the pieces below are used for in that case.
    //
    // A vertex the carve took as ON the surface is within its ON band of
    // it, not on it, so a flat cap holding one is off its mean plane by up
    // to that band — and is still flat. Tri_angle (2026-09-23): a grid
    // corner 0.29 mm off Rack_1's outlet plane (the band is 0.32 mm) made
    // the bottom-row cap read as folded, it was not split at the outlet_p0 /
    // outlet_wall boundary, and the wall took a 12.8 mm strip of the fan
    // opening.
    const scalar hloop = std::sqrt(area);
    const bool planar = offPlane <= std::max(scalar(1e-3) * hloop, onTol);

    std::vector<Foam::dcCartSurfaces::triInfo> tris;
    trisInBox(cellBox, tris);
    if (tris.empty()) { return false; }
    const scalar diag = Foam::mag(cellBox.span());

    // Triangles that can carry this cap: roughly parallel to it (surface
    // normals point into the fluid, the cap normal from the fluid half into
    // the solid one) and within the cell.
    std::vector<const Foam::dcCartSurfaces::triInfo*> mine;
    // a folded cap wraps a box edge or corner: its facets can stand at 45°
    // (edge) or 55° (corner) to the mean normal, so the facing filter is
    // loose there and tight on a planar cap
    const scalar facing = planar ? -0.7 : -0.3;
    // distance from a point to a triangle (Ericson, Real-Time Collision
    // Detection), for "does the cap touch this facet"
    auto triDist2 = [](const point& p, const point& a, const point& b, const point& c) -> scalar
    {
        const vector ab = b - a, ac = c - a, ap = p - a;
        const scalar d1 = ab & ap, d2 = ac & ap;
        if (d1 <= 0 && d2 <= 0) { return magSqr(ap); }
        const vector bp = p - b;
        const scalar d3 = ab & bp, d4 = ac & bp;
        if (d3 >= 0 && d4 <= d3) { return magSqr(bp); }
        const scalar vc = d1 * d4 - d3 * d2;
        if (vc <= 0 && d1 >= 0 && d3 <= 0) { const scalar v = d1 / (d1 - d3); return magSqr(p - (a + v * ab)); }
        const vector cp = p - c;
        const scalar d5 = ab & cp, d6 = ac & cp;
        if (d6 >= 0 && d5 <= d6) { return magSqr(cp); }
        const scalar vb = d5 * d2 - d1 * d6;
        if (vb <= 0 && d2 >= 0 && d6 <= 0) { const scalar w = d2 / (d2 - d6); return magSqr(p - (a + w * ac)); }
        const scalar va = d3 * d6 - d5 * d4;
        if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0)
        {
            const scalar w = (d4 - d3) / ((d4 - d3) + (d5 - d6));
            return magSqr(p - (b + w * (c - b)));
        }
        const scalar denom = 1.0 / (va + vb + vc);
        const scalar v = vb * denom, w = vc * denom;
        return magSqr(p - (a + ab * v + ac * w));
    };
    for (const auto& t : tris)
    {
        if ((t.normal & nu) > facing) { continue; }
        const point tc = (t.p0 + t.p1 + t.p2) / 3.0;
        if (std::abs((tc - cen) & nu) > diag) { continue; }
        if (!planar)
        {
            // a folded cap spans only the facets its loop actually touches:
            // a crossing point lies ON its facet; an ON vertex that merely
            // sits near a facet's edge (beside the cube's bottom edge) does
            // not, and that facet must not add a phantom piece
            bool touched = false;
            for (label id : loop)
            {
                if (triDist2(m.points[id], t.p0, t.p1, t.p2) < 1e-10) { touched = true; break; }
            }
            if (!touched) { continue; }
        }
        mine.push_back(&t);
    }
    if (!planar) { ++gFolded; }
    if (mine.empty()) { if (!planar) { ++gFoldedNoFacet; } return false; }

    // region of a point of the cap: the triangle whose projection holds it,
    // else the nearest projected triangle
    auto regionAt = [&](const v2& q) -> label
    {
        label best = -1; scalar bestD = Foam::GREAT;
        for (const auto* t : mine)
        {
            std::vector<v2> T{to2(t->p0), to2(t->p1), to2(t->p2)};
            const scalar ta = area2(T);
            if (std::abs(ta) < 1e-16) { continue; }
            if (ta < 0) { std::swap(T[1], T[2]); }
            scalar dmin = Foam::GREAT;
            for (int k = 0; k < 3; ++k)
            {
                const v2& a = T[k]; const v2& b = T[(k + 1) % 3];
                const scalar len = std::sqrt((b.x - a.x) * (b.x - a.x) + (b.y - a.y) * (b.y - a.y));
                dmin = std::min(dmin, cross2(a, b, q) / std::max(len, scalar(1e-12)));
            }
            // dmin >= 0: inside; else -dmin is the distance outside
            const scalar d = (dmin >= 0) ? 0 : -dmin;
            if (d < bestD) { bestD = d; best = t->region; }
            if (d == 0) { break; }
        }
        return best;
    };

    // The cap is split ONLY along region boundaries: edges shared by two
    // triangles of different regions. Splitting along every facet edge of
    // a curved surface made slivers a fraction of a millimetre wide.
    std::map<std::pair<std::tuple<long long, long long, long long>,
                       std::tuple<long long, long long, long long>>, std::set<label>> edgeRegions;
    auto pkey = [](const point& p)
    {
        return std::make_tuple
        (
            (long long)std::llround(p.x() * 1e6),
            (long long)std::llround(p.y() * 1e6),
            (long long)std::llround(p.z() * 1e6)
        );
    };
    for (const auto* t : mine)
    {
        const point* pp[3] = {&t->p0, &t->p1, &t->p2};
        for (int k = 0; k < 3; ++k)
        {
            auto ka = pkey(*pp[k]), kb = pkey(*pp[(k + 1) % 3]);
            if (kb < ka) { std::swap(ka, kb); }
            edgeRegions[std::make_pair(ka, kb)].insert(t->region);
        }
    }
    // FEATURE edges: shared by two facets whose normals differ by > 20°.
    // A folded cap is split along them too, so a box edge inside a cell
    // gives two planar pieces meeting at the edge instead of one chamfer.
    std::map<std::pair<std::tuple<long long, long long, long long>,
                       std::tuple<long long, long long, long long>>, std::vector<vector>> edgeNormals;
    for (const auto* t : mine)
    {
        const point* pp[3] = {&t->p0, &t->p1, &t->p2};
        for (int k = 0; k < 3; ++k)
        {
            auto ka = pkey(*pp[k]), kb = pkey(*pp[(k + 1) % 3]);
            if (kb < ka) { std::swap(ka, kb); }
            edgeNormals[std::make_pair(ka, kb)].push_back(t->normal);
        }
    }
    auto isFeature = [&](const point& pa, const point& pb) -> bool
    {
        auto ka = pkey(pa), kb = pkey(pb);
        if (kb < ka) { std::swap(ka, kb); }
        const auto& ns = edgeNormals[std::make_pair(ka, kb)];
        for (size_t i = 0; i < ns.size(); ++i)
        {
            for (size_t j = i + 1; j < ns.size(); ++j)
            {
                if ((ns[i] & ns[j]) < 0.94) { return true; }
            }
        }
        return false;
    };
    struct seg3 { point a, b; };
    std::vector<seg3> featureSegs;

    // Feature lines that need no shared vertices: the intersection line of
    // every pair of touched facet planes whose normals differ by > 20°
    // (a box's faces are triangulated independently, so their edge
    // vertices rarely coincide). Facets of one plane are grouped first.
    std::vector<std::pair<vector, point>> planes;   // (unit normal, point)
    if (!planar)
    {
        for (const auto* t : mine)
        {
            bool dup = false;
            for (const auto& pl : planes)
            {
                if ((pl.first & t->normal) > 0.94 && std::abs((t->p0 - pl.second) & pl.first) < 1e-6) { dup = true; break; }
            }
            if (!dup) { planes.emplace_back(t->normal, t->p0); }
        }
    }

    struct line2 { v2 a, b; };
    std::vector<line2> cutLines;
    std::set<std::tuple<long, long, long>> lineKeys;
    for (const auto* t : mine)
    {
        const point* pp[3] = {&t->p0, &t->p1, &t->p2};
        for (int k = 0; k < 3; ++k)
        {
            auto ka = pkey(*pp[k]), kb = pkey(*pp[(k + 1) % 3]);
            if (kb < ka) { std::swap(ka, kb); }
            const bool regionEdge = edgeRegions[std::make_pair(ka, kb)].size() >= 2;
            if (!regionEdge) { continue; }
            v2 a = to2(*pp[k]), b = to2(*pp[(k + 1) % 3]);
            scalar nx = -(b.y - a.y), ny = (b.x - a.x);
            const scalar ln = std::sqrt(nx * nx + ny * ny);
            if (ln < 1e-12) { continue; }
            nx /= ln; ny /= ln;
            if (nx < 0 || (nx == 0 && ny < 0)) { nx = -nx; ny = -ny; }
            const scalar off = nx * a.x + ny * a.y;
            const auto key = std::make_tuple
            (
                (long)std::lround(nx * 1e4), (long)std::lround(ny * 1e4),
                (long)std::lround(off * 1e6)
            );
            if (lineKeys.insert(key).second) { cutLines.push_back({a, b}); }
        }
    }
    static const bool foldSplitEnv = (getenv("DCCART_FOLDSPLIT") != nullptr);
    if (!planar && foldSplitEnv)
    {
        for (size_t i = 0; i < planes.size(); ++i)
        {
            for (size_t j = i + 1; j < planes.size(); ++j)
            {
                const vector& n1 = planes[i].first; const vector& n2 = planes[j].first;
                if (std::abs(n1 & n2) > 0.94) { continue; }
                const vector d = n1 ^ n2;
                const scalar dm = Foam::mag(d);
                if (dm < 1e-9) { continue; }
                // a point on both planes nearest the cap centre
                const scalar h1 = (planes[i].second - cen) & n1;
                const scalar h2 = (planes[j].second - cen) & n2;
                const scalar n12 = n1 & n2;
                const scalar det = 1 - n12 * n12;
                const scalar c1 = (h1 - h2 * n12) / det, c2 = (h2 - h1 * n12) / det;
                const point p0 = cen + c1 * n1 + c2 * n2;
                const vector du = d / dm;
                const point pa = p0 - du * diag, pb = p0 + du * diag;
                featureSegs.push_back({pa, pb});
                v2 a = to2(pa), b = to2(pb);
                a.p = pa; b.p = pb;
                scalar nx = -(b.y - a.y), ny = (b.x - a.x);
                const scalar ln = std::sqrt(nx * nx + ny * ny);
                if (ln < 1e-12) { continue; }
                nx /= ln; ny /= ln;
                if (nx < 0 || (nx == 0 && ny < 0)) { nx = -nx; ny = -ny; }
                const scalar off = nx * a.x + ny * a.y;
                const auto key = std::make_tuple
                (
                    (long)std::lround(nx * 1e4), (long)std::lround(ny * 1e4),
                    (long)std::lround(off * 1e6)
                );
                if (lineKeys.insert(key).second) { cutLines.push_back({a, b}); }
            }
        }
    }

    std::vector<std::pair<std::vector<v2>, label>> pcs;
    if (cutLines.empty())
    {
        if (!planar) { ++gFoldedNoLines; }
        pieces.emplace_back(loop, regionAt(v2{0, 0, cen}));
        return true;
    }
    std::vector<std::vector<v2>> polys{P};
    for (const line2& L : cutLines)
    {
        std::vector<std::vector<v2>> next;
        for (const auto& Q : polys)
        {
            std::vector<v2> in, out;
            clipHalfPlaneBoth(Q, L.a, L.b, in, out);
            if (in.size() >= 3 && std::abs(area2(in)) > 1e-12) { next.push_back(std::move(in)); }
            if (out.size() >= 3 && std::abs(area2(out)) > 1e-12) { next.push_back(std::move(out)); }
        }
        if (!next.empty()) { polys = std::move(next); }
    }
    for (auto& Q : polys)
    {
        v2 c{0, 0, cen};
        for (const v2& q : Q) { c.x += q.x; c.y += q.y; }
        c.x /= Q.size(); c.y /= Q.size();
        pcs.emplace_back(std::move(Q), regionAt(c));
    }
    if (pcs.empty()) { return false; }
    // sliver pieces (a region boundary grazing a corner) take the region of
    // the largest piece; coalesceCoplanarFaces folds them in afterwards
    {
        label bigRegion = -1; scalar bigA = -1;
        for (const auto& pc : pcs)
        {
            const scalar a = std::abs(area2(pc.first));
            if (a > bigA) { bigA = a; bigRegion = pc.second; }
        }
        for (auto& pc : pcs)
        {
            if (std::abs(area2(pc.first)) < 0.03 * area) { pc.second = bigRegion; }
        }
    }
    // one region over the whole cap, or a folded cap (pieces would have to
    // be built on the surface, not on the mean plane, or the cell stops
    // closing): the whole loop, one region — the one covering most of it
    bool oneRegion = true;
    for (const auto& pc : pcs) { if (pc.second != pcs[0].second) { oneRegion = false; break; } }
    // A folded cap without feature edges (a finely tessellated pipe) stays
    // ONE face with the region covering most of it; one with feature edges
    // (a box edge or corner) keeps its per-facet pieces, and so does any
    // cap that spans two regions.
    if ((oneRegion && planar) || (oneRegion && featureSegs.empty()))
    {
        std::unordered_map<label, scalar> byRegion;
        label best = -1; scalar bestA = -1;
        for (const auto& pc : pcs)
        {
            const scalar a = (byRegion[pc.second] += std::abs(area2(pc.first)));
            if (a > bestA) { bestA = a; best = pc.second; }
        }
        if (best < 0) { return false; }
        pieces.emplace_back(loop, best);
        return true;
    }

    // vertex ids: the loop's own where a piece vertex coincides with one,
    // shared new points elsewhere (adjacent pieces meet along one edge)
    std::map<std::pair<long long, long long>, label> ids;
    auto keyOf = [](const v2& q)
    {
        return std::make_pair
        (
            (long long)std::llround(q.x * 1e9),
            (long long)std::llround(q.y * 1e9)
        );
    };
    for (size_t i = 0; i < loop.size(); ++i) { ids[keyOf(P[i])] = loop[i]; }
    // a piece corner within 0.1 mm of a loop vertex IS that vertex: the
    // alternative is a sliver face a few square microns big
    // a piece corner within 2% of the cap size (at least 0.1 mm) of a loop
    // vertex IS that vertex: the alternative is a sliver face a few square
    // microns big, with a skewness in the hundreds
    const scalar snapR2 = std::max(scalar(1e-8), 4e-4 * area);
    auto snapLoop = [&](const v2& q) -> label
    {
        for (size_t i = 0; i < loop.size(); ++i)
        {
            const scalar dx = q.x - P[i].x, dy = q.y - P[i].y;
            if (dx * dx + dy * dy < snapR2) { return loop[i]; }
        }
        return -1;
    };
    if (!planar) { ++gFoldedSplit; }
    static const bool foldBoxEnv = (getenv("DCCART_DEBUG_BOX") != nullptr);
    if (!planar && foldBoxEnv)
    {
        static Foam::boundBox fbox(Foam::boundBox::invertedBox);
        static bool fparsed = false;
        if (!fparsed)
        {
            fparsed = true;
            double b[6];
            if (sscanf(getenv("DCCART_DEBUG_BOX"), "%lf %lf %lf %lf %lf %lf", b, b+1, b+2, b+3, b+4, b+5) == 6)
            {
                fbox = Foam::boundBox(Foam::point(b[0], b[1], b[2]), Foam::point(b[3], b[4], b[5]));
            }
        }
        if (fbox.contains(cen))
        {
            Foam::Info<< "  fold split at " << cen << " nu " << nu << " loop:";
            for (label id : loop) { Foam::Info<< " " << m.points[id]; }
            Foam::Info<< Foam::nl << "    planes " << Foam::label(planes.size()) << " lines " << Foam::label(cutLines.size()) << " pieces:";
            for (const auto& pc : pcs)
            {
                Foam::Info<< " [r" << pc.second << " a2=" << area2(pc.first) << " :";
                for (const v2& q : pc.first) { Foam::Info<< " " << (q.onLoop ? "L" : "I") << q.p; }
                Foam::Info<< "]";
            }
            Foam::Info<< Foam::nl;
        }
    }
    // a vertex cut inside the cap (where two cut lines meet: a box corner)
    // is placed on the mean plane; the real corner is on the feature edges,
    // so it moves to the nearest point on them
    // Where a piece corner really is. An interior corner (two cut lines
    // meeting: a box corner) goes to the nearest point on the feature
    // lines. A corner cut on a loop edge (the chord between two crossings on
    // the two facets) goes to where the feature line pierces the hex face
    // that chord lies in — the face is axis-aligned, so it is the
    // coordinate plane the chord's two ends share — and is spliced into
    // that face too, so the cap and the side faces still meet.
    auto nearestOnLines = [&](const point& p, scalar r2, point& out) -> bool
    {
        bool found = false; scalar bestD2 = r2;
        for (const seg3& sg : featureSegs)
        {
            const vector e = sg.b - sg.a;
            const scalar l2 = magSqr(e);
            scalar t = (l2 > 0) ? ((p - sg.a) & e) / l2 : 0;
            t = std::min(std::max(t, scalar(0)), scalar(1));
            const point c = sg.a + t * e;
            const scalar d2 = magSqr(c - p);
            if (d2 < bestD2) { bestD2 = d2; out = c; found = true; }
        }
        return found;
    };
    auto snapFeature = [&](const v2& q) -> point
    {
        static const bool noSnapEnv = (getenv("DCCART_NOSNAP") != nullptr);
        if (featureSegs.empty() || noSnapEnv) { return q.p; }
        if (!q.onLoop)
        {
            // NOT the source of Base604's residual skew faces -- measured
            // three ways on one binary, all against the same scratch control
            // (3,560,418 cells, 21 faces, max skewness 21.3791, aspect
            // 135.851): guarding the chord branch's extrapolation, disabling
            // THIS branch (DCCART_NOSNAP_CORNER, identical mesh and 16% less
            // dcCartMesh time), and disabling both (DCCART_NOSNAP, identical
            // mesh). The old "22 -> 9 faces with NOSNAP" reading predates
            // snapToGridPlanes, which removes those faces by a different route.
            // This branch is still load-bearing: Slab's mesh differs without
            // it (280,701 vs 283,329 cells), so it stays on.
            // DCCART_NOSNAP_CORNER isolates THIS branch (a cap corner moved
            // straight onto the nearest feature line, bounded only by the
            // 0.25*area squared-distance search radius) from the chord branch
            // below, so the two can be A/B'd separately.
            static const bool noCornerEnv = (getenv("DCCART_NOSNAP_CORNER") != nullptr);
            if (noCornerEnv) { return q.p; }
            point c; return nearestOnLines(q.p, 0.25 * area, c) ? c : q.p;
        }
        // a chord point: only when it is not one of the loop's own vertices
        if (q.ea < 0 || q.eb < 0 || q.ea == q.eb) { return q.p; }
        const point& pa = m.points[q.ea]; const point& pb = m.points[q.eb];
        if (magSqr(pa - q.p) < 1e-20 || magSqr(pb - q.p) < 1e-20) { return q.p; }
        int ax = -1;
        for (int k = 0; k < 3; ++k) { if (std::abs(pa[k] - pb[k]) < 1e-9) { ax = k; break; } }
        if (ax < 0) { return q.p; }
        // the feature line nearest the chord point, intersected with that plane
        point c;
        if (!nearestOnLines(q.p, 0.25 * area, c)) { return q.p; }
        const seg3* best = nullptr; scalar bd2 = Foam::GREAT;
        for (const seg3& sg : featureSegs)
        {
            const vector e = sg.b - sg.a; const scalar l2 = magSqr(e);
            scalar t = (l2 > 0) ? ((q.p - sg.a) & e) / l2 : 0;
            t = std::min(std::max(t, scalar(0)), scalar(1));
            const scalar d2 = magSqr(sg.a + t * e - q.p);
            if (d2 < bd2) { bd2 = d2; best = &sg; }
        }
        if (!best) { return q.p; }
        const vector e = best->b - best->a;
        if (std::abs(e[ax]) < 1e-12 * (Foam::mag(e) + 1e-30)) { return c; }
        const scalar t = (pa[ax] - best->a[ax]) / e[ax];
        point x = best->a + t * e;
        x[ax] = pa[ax];
        // Running out along the feature line is conditioned by |e[ax]|: a line
        // nearly perpendicular to the axis makes `t` huge and `x` lands
        // arbitrarily far from the corner it was correcting. Base604 paid for
        // that with cap corners moved 0.21 mm off the surface at z=4.48 onto
        // z=4.480208 -- a coordinate that is neither in any STL nor on any grid
        // plane -- leaving 0.2 mm hairline cells whose 2.0 x 0.2 mm wall faces
        // checkMesh scores 204 (it normalises by max(0.4|d|, the face's own
        // reach), and |d| there is 0.08 mm) and whose Sf.d ~ 0 puts a near-zero
        // pivot in the pressure equation. Hold the correction to the SAME
        // bound the nearest-point search above already uses, 0.5*sqrt(cap
        // area), so a snap can only ever be a local correction: no new
        // constant, and a legitimate snap (a corner a facet edge runs through)
        // is a fraction of the cap's own size.
        if (magSqr(x - q.p) > 0.25 * area) { return q.p; }
        // it must lie ON the chord segment (between its ends, in both free
        // coordinates); otherwise the feature line does not pierce this face
        // where the chord is, and the chord point stays put
        for (int k = 0; k < 3; ++k)
        {
            if (k == ax) { continue; }
            const scalar lo = std::min(pa[k], pb[k]) - 1e-7, hi = std::max(pa[k], pb[k]) + 1e-7;
            if (x[k] < lo || x[k] > hi) { return q.p; }
        }
        return x;
    };
    // A folded cap's pieces are built in 3D from the loop's own points, the
    // chord points and the snapped corners. Each piece must lie ON a facet
    // of the surface (its centroid inside that facet's projection) and face
    // the way that facet does; a piece that does not — the cap's loop ran
    // along ON points beside the cube's bottom edge, say, and the piece is a
    // phantom in the cell's own bottom plane — would be a face the cell
    // centre sits behind. Then the split is abandoned and the cap stays one
    // face, as before.
    if (!planar)
    {
        auto facetAt = [&](const v2& q) -> const Foam::dcCartSurfaces::triInfo*
        {
            for (const auto* t : mine)
            {
                std::vector<v2> T{to2(t->p0), to2(t->p1), to2(t->p2)};
                const scalar ta = area2(T);
                if (std::abs(ta) < 1e-16) { continue; }
                if (ta < 0) { std::swap(T[1], T[2]); }
                bool in = true;
                for (int k = 0; k < 3 && in; ++k)
                {
                    const v2& a = T[k]; const v2& b = T[(k + 1) % 3];
                    const scalar len = std::sqrt((b.x - a.x) * (b.x - a.x) + (b.y - a.y) * (b.y - a.y));
                    if (cross2(a, b, q) / std::max(len, scalar(1e-12)) < -1e-4 * hloop) { in = false; }
                }
                if (in) { return t; }
            }
            return nullptr;
        };
        for (const auto& pc : pcs)
        {
            vector nn(0, 0, 0);
            const auto& Q = pc.first;
            v2 c{0, 0, cen};
            for (size_t i = 0; i < Q.size(); ++i)
            {
                const point p0 = Q[i].onLoop ? Q[i].p : snapFeature(Q[i]);
                const point p1 = Q[(i + 1) % Q.size()].onLoop ? Q[(i + 1) % Q.size()].p : snapFeature(Q[(i + 1) % Q.size()]);
                nn += (p0 - cen) ^ (p1 - cen);
                c.x += Q[i].x; c.y += Q[i].y;
            }
            c.x /= Q.size(); c.y /= Q.size();
            const scalar mn = Foam::mag(nn);
            // piece centre (3D)
            point pcen = point::zero;
            for (const v2& q : Q) { pcen += (q.onLoop ? q.p : snapFeature(q)); }
            pcen /= scalar(Q.size());
            // the facet the piece lies on: the nearest one in 3D (a facet
            // standing steeply to the mean plane projects to a sliver, so a
            // projected point-in-triangle test would miss it)
            const Foam::dcCartSurfaces::triInfo* t = nullptr;
            scalar nearD = Foam::GREAT;
            {
                // a chord point sags off the facet by up to a few % of the
                // cell; a phantom piece in a hex face is a cell away
                scalar bestD2 = Foam::sqr(0.35 * hloop);
                for (const auto* tt : mine)
                {
                    const scalar d2 = triDist2(pcen, tt->p0, tt->p1, tt->p2);
                    nearD = std::min(nearD, std::sqrt(d2));
                    if (d2 < bestD2) { bestD2 = d2; t = tt; }
                }
            }
            (void)facetAt;
            // the cap normal points into the solid, the facet normal into the
            // fluid, and the fluid-side cell centre must be BEHIND the piece
            // (the ON tolerance can hand the loop a strip lying in one of the
            // hex's own faces, whose normal points at the cell centre)
            // (the fluid-side face-centre average is NOT the volume
            // centroid and lands on the solid side of one facet for a cell
            // wrapped round a convex edge, so it is not tested here)
            (void)fluidCentre;
            if (mn < 1e-14 || !t || (nn & t->normal) / mn > -0.5)
            {
                ++gFoldedGuard;
                if (getenv("DCCART_DEBUG_FOLD"))
                {
                    Foam::Info<< "  fold guard at " << cen << " piece n.nu " << (mn > 0 ? (nn & nu) / mn : 0)
                        << " facet " << (t ? (nn & t->normal) / mn : -9) << " nearD/h " << nearD / hloop
                        << " npcs " << Foam::label(pcs.size())
                        << " loop " << Foam::label(loop.size()) << " mine " << Foam::label(mine.size()) << Foam::nl;
                }
                label best = -1; scalar bestA = -1;
                std::unordered_map<label, scalar> byRegion;
                for (const auto& q : pcs)
                {
                    const scalar a = (byRegion[q.second] += std::abs(area2(q.first)));
                    if (a > bestA) { bestA = a; best = q.second; }
                }
                pieces.clear();
                pieces.emplace_back(loop, best);
                return true;
            }
        }
    }
    for (auto& pc : pcs)
    {
        std::vector<label> poly;
        for (const v2& q : pc.first)
        {
            label id = snapLoop(q);
            if (id < 0)
            {
                const auto k = keyOf(q);
                auto it = ids.find(k);
                if (it != ids.end()) { id = it->second; }
                else
                {
                    id = label(m.points.size());
                    m.points.push_back(snapFeature(q));
                    side.push_back(0);
                    ids[k] = id;
                    if (!planar && q.onLoop && q.ea >= 0 && q.eb >= 0 && q.ea != q.eb)
                    {
                        inserts.push_back({q.ea, q.eb, id});
                    }
                }
            }
            if (poly.empty() || poly.back() != id) { poly.push_back(id); }
        }
        while (poly.size() > 1 && poly.front() == poly.back()) { poly.pop_back(); }
        if (poly.size() >= 3) { pieces.emplace_back(std::move(poly), pc.second); }
    }
    return !pieces.empty();
}


//- Both sides of a clip line at once, from ONE classification of every
//  vertex: the two outputs partition P exactly (no sliver overlap, no gap),
//  which the difference "cap minus the sheet's triangles" depends on.
void clipHalfPlaneBoth
(
    const std::vector<v2>& P, const v2& a, const v2& b,
    std::vector<v2>& inside, std::vector<v2>& outside,
    bool onLine = false
)
{
    inside.clear(); outside.clear();
    const size_t n = P.size();
    if (!n) { return; }
    const scalar len = std::sqrt((b.x - a.x) * (b.x - a.x) + (b.y - a.y) * (b.y - a.y));
    const scalar eps = 1e-5 * std::max(len, scalar(1e-12));
    std::vector<signed char> cls(n);
    for (size_t i = 0; i < n; ++i)
    {
        const scalar d = cross2(a, b, P[i]);
        cls[i] = (d >= -eps) ? 1 : -1;
    }
    for (size_t i = 0; i < n; ++i)
    {
        const v2& s0 = P[i];
        const v2& e0 = P[(i + 1) % n];
        const signed char cs = cls[i], ce = cls[(i + 1) % n];
        if (cs > 0) { inside.push_back(s0); } else { outside.push_back(s0); }
        if (cs != ce)
        {
            const scalar ds = cross2(a, b, s0);
            const scalar de = cross2(a, b, e0);
            scalar t = (ds - de != 0) ? ds / (ds - de) : 0.5;
            t = std::min(std::max(t, scalar(0)), scalar(1));
            v2 x{s0.x + t * (e0.x - s0.x), s0.y + t * (e0.y - s0.y), s0.p + t * (e0.p - s0.p)};
            if (onLine)
            {
                // the cut lands on the clip line's own 3D edge
                const scalar lx = b.x - a.x, ly = b.y - a.y;
                const scalar l2 = lx * lx + ly * ly;
                const scalar u = (l2 > 0) ? ((x.x - a.x) * lx + (x.y - a.y) * ly) / l2 : 0;
                x.p = a.p + u * (b.p - a.p);
            }
            // the point sits on the edge s0 -> e0: on the loop iff that edge is
            x.onLoop = s0.edgeLoop;
            x.ea = s0.ea; x.eb = s0.eb;
            // the side that holds s0 continues from x along the CUT LINE; the
            // other side continues along the rest of the original edge
            v2 xs = x, xo = x;
            xs.edgeLoop = false;
            xo.edgeLoop = s0.edgeLoop;
            if (cs > 0) { inside.push_back(xs); outside.push_back(xo); }
            else { outside.push_back(xs); inside.push_back(xo); }
        }
    }
}

//- Make the pieces of a split cap tile its loop exactly, or say they do not.
//  The pieces come from clipping by the infinite lines through the sheet's
//  triangle edges, one triangle after another, with a classification
//  tolerance of 1e-5 m. Near a sheet corner a few microns inside the face
//  that leaves pieces that do not match: one corner computed along two
//  lines lands at two points 0.7 um apart, a piece finished by one triangle
//  does not carry the corner a later triangle puts on its edge, and a piece
//  below 1e-10 m2 is dropped. The global coincident-point merge (1e-6 m)
//  then moves one of the two near corners off the straight edge of the piece
//  that lacks it, and both cells sharing the face are open (testbed: the
//  wall baffle's hole corner 3 um inside a grid face, openness 6.8e-6).
//  Corners within `tau` are welded (a loop vertex is never moved), corners
//  within `tau` of a loop edge are put exactly on it, every corner is spliced
//  into the piece edges it lies on, and the result is checked: interior
//  edges pair up and the others run exactly once along the loop.
bool conformPieces
(
    Foam::dcCartCutCells::cutMesh& m,
    const std::vector<label>& loop,
    const point& cen, const vector& u, const vector& v,
    scalar tau,
    std::vector<std::pair<std::vector<label>, label>>& pieces
)
{
    using namespace Foam;
    std::vector<label> ids;
    std::unordered_map<label, label> slot;
    auto addId = [&](label id) { if (slot.emplace(id, label(ids.size())).second) { ids.push_back(id); } };
    for (label id : loop) { addId(id); }
    const label nL = label(ids.size());
    if (nL != label(loop.size()) || nL < 3) { return false; }
    for (const auto& pc : pieces) { for (label id : pc.first) { addId(id); } }
    const label nI = label(ids.size());
    std::vector<scalar> X(nI), Y(nI);
    auto proj = [&](label k) { const vector d = m.points[ids[k]] - cen; X[k] = d & u; Y[k] = d & v; };
    for (label k = 0; k < nI; ++k) { proj(k); }
    const scalar tau2 = tau * tau;

    // weld: every corner goes to the nearest earlier leader within tau
    std::vector<label> alias(nI);
    std::vector<label> leaders;
    for (label k = 0; k < nI; ++k)
    {
        alias[k] = k;
        if (k >= nL)
        {
            label best = -1; scalar bestD = tau2;
            for (label j : leaders)
            {
                const scalar d = sqr(X[k] - X[j]) + sqr(Y[k] - Y[j]);
                if (d <= bestD) { bestD = d; best = j; }
            }
            if (best >= 0) { alias[k] = best; continue; }
        }
        leaders.push_back(k);
    }
    // a corner near a loop edge goes exactly onto it (the cell's other faces
    // hold that edge straight)
    for (label k : leaders)
    {
        if (k < nL) { continue; }
        label bestI = -1; scalar bestD = tau2, bestT = 0;
        for (label i = 0; i < nL; ++i)
        {
            const label j = (i + 1) % nL;
            const scalar ex = X[j] - X[i], ey = Y[j] - Y[i];
            const scalar l2 = ex * ex + ey * ey;
            if (l2 < VSMALL) { continue; }
            const scalar t = ((X[k] - X[i]) * ex + (Y[k] - Y[i]) * ey) / l2;
            if (t <= 0 || t >= 1) { continue; }
            const scalar d = sqr(X[k] - X[i] - t * ex) + sqr(Y[k] - Y[i] - t * ey);
            if (d <= bestD) { bestD = d; bestI = i; bestT = t; }
        }
        if (bestI < 0) { continue; }
        const point& pa = m.points[ids[bestI]];
        const point& pb = m.points[ids[(bestI + 1) % nL]];
        m.points[ids[k]] = pa + bestT * (pb - pa);
        proj(k);
    }
    // pieces over leaders, consecutive repeats dropped
    std::vector<std::vector<label>> P;
    std::vector<label> Z;
    for (const auto& pc : pieces)
    {
        std::vector<label> q;
        for (label id : pc.first)
        {
            const label k = alias[slot[id]];
            if (q.empty() || q.back() != k) { q.push_back(k); }
        }
        while (q.size() > 1 && q.front() == q.back()) { q.pop_back(); }
        if (q.size() < 3) { continue; }
        P.push_back(std::move(q));
        Z.push_back(pc.second);
    }
    // splice every leader lying on a piece edge into that edge
    for (auto& q : P)
    {
        std::vector<label> out;
        const label n = label(q.size());
        for (label e = 0; e < n; ++e)
        {
            const label a = q[e], b = q[(e + 1) % n];
            out.push_back(a);
            const scalar ex = X[b] - X[a], ey = Y[b] - Y[a];
            const scalar l2 = ex * ex + ey * ey;
            if (l2 < VSMALL) { continue; }
            std::vector<std::pair<scalar, label>> ins;
            for (label s : leaders)
            {
                if (s == a || s == b) { continue; }
                const scalar t = ((X[s] - X[a]) * ex + (Y[s] - Y[a]) * ey) / l2;
                if (t <= 1e-9 || t >= 1 - 1e-9) { continue; }
                if (sqr(X[s] - X[a] - t * ex) + sqr(Y[s] - Y[a] - t * ey) > tau2) { continue; }
                ins.emplace_back(t, s);
            }
            std::sort(ins.begin(), ins.end());
            for (const auto& x : ins) { if (out.back() != x.second) { out.push_back(x.second); } }
        }
        while (out.size() > 1 && out.front() == out.back()) { out.pop_back(); }
        q.swap(out);
    }
    // check: every piece positive, interior edges paired, the rest the loop
    scalar loopA = 0, sumA = 0;
    for (label i = 0; i < nL; ++i) { const label j = (i + 1) % nL; loopA += X[i] * Y[j] - X[j] * Y[i]; }
    loopA *= 0.5;
    std::map<std::pair<label, label>, label> E;
    for (const auto& q : P)
    {
        const label n = label(q.size());
        if (n < 3) { return false; }
        scalar a = 0;
        for (label e = 0; e < n; ++e)
        {
            const label x = q[e], y = q[(e + 1) % n];
            a += X[x] * Y[y] - X[y] * Y[x];
            if (++E[std::make_pair(x, y)] > 1) { return false; }
        }
        if (a <= 0) { return false; }
        sumA += 0.5 * a;
    }
    if (std::abs(sumA - loopA) > 1e-9 * std::abs(loopA)) { return false; }
    std::vector<scalar> along(nL, 0);
    for (const auto& kv : E)
    {
        if (E.count(std::make_pair(kv.first.second, kv.first.first))) { continue; }
        const label a = kv.first.first, b = kv.first.second;
        bool onLoop = false;
        for (label i = 0; i < nL && !onLoop; ++i)
        {
            const label j = (i + 1) % nL;
            const scalar ex = X[j] - X[i], ey = Y[j] - Y[i];
            const scalar l = std::sqrt(ex * ex + ey * ey);
            if (l < VSMALL) { continue; }
            const scalar tolD = 1e-12 + 1e-9 * l;
            const scalar da = ((X[a] - X[i]) * ey - (Y[a] - Y[i]) * ex) / l;
            const scalar db = ((X[b] - X[i]) * ey - (Y[b] - Y[i]) * ex) / l;
            if (std::abs(da) > tolD || std::abs(db) > tolD) { continue; }
            const scalar ta = ((X[a] - X[i]) * ex + (Y[a] - Y[i]) * ey) / l;
            const scalar tb = ((X[b] - X[i]) * ex + (Y[b] - Y[i]) * ey) / l;
            if (ta < -tolD || tb > l + tolD || tb <= ta) { continue; }
            along[i] += tb - ta;
            onLoop = true;
        }
        if (!onLoop) { return false; }
    }
    for (label i = 0; i < nL; ++i)
    {
        const label j = (i + 1) % nL;
        const scalar l = std::sqrt(sqr(X[j] - X[i]) + sqr(Y[j] - Y[i]));
        if (std::abs(along[i] - l) > 1e-12 + 1e-9 * l) { return false; }
    }
    pieces.clear();
    for (size_t q = 0; q < P.size(); ++q)
    {
        std::vector<label> poly;
        poly.reserve(P[q].size());
        for (label k : P[q]) { poly.push_back(ids[k]); }
        pieces.emplace_back(std::move(poly), Z[q]);
    }
    return true;
}

//- Split an INTERNAL cap (a baffle sheet's cut through a cell) into the part
//  the sheet actually covers and the rest. The sheet is cut as its whole
//  plane, so at a free edge or where two sheets meet the plane runs past
//  the sheet inside the cell; that phantom part must still close the two
//  halves — as a plain internal face — while only the covered part joins
//  the faceZone. Pieces carry `zone` (covered) or -1 (phantom). A cap the
//  sheet covers completely, or not at all, stays ONE face.
template<class TrisFn>
bool splitCapBySheet
(
    Foam::dcCartCutCells::cutMesh& m,
    std::vector<signed char>& side,
    TrisFn&& trisInBox,
    const Foam::boundBox& cellBox,
    const std::vector<label>& loop,
    label zone,
    std::vector<std::pair<std::vector<label>, label>>& pieces,
    bool* uncoveredOnly = nullptr,
    bool exactOutline = false
)
{
    // uncoveredOnly: only say whether every piece would be phantom (-1),
    // touching neither the pieces nor the points
    pieces.clear();
    vector n; point cen;
    polygonNormalCentre(m.points, loop, n, cen);
    const scalar area = Foam::mag(n);
    if (area < 1e-14) { return false; }
    const vector nu = n / area;
    vector e(1, 0, 0);
    if (std::abs(nu.x()) > 0.9) { e = vector(0, 1, 0); }
    vector u = e - (e & nu) * nu;
    u /= Foam::mag(u);
    const vector v = nu ^ u;
    auto to2 = [&](const point& p) { const vector d = p - cen; return v2{d & u, d & v, p}; };

    std::vector<v2> P;
    P.reserve(loop.size());
    for (label id : loop) { P.push_back(to2(m.points[id])); }
    if (area2(P) <= 0) { return false; }

    std::vector<Foam::dcCartSurfaces::triInfo> tris;
    trisInBox(cellBox, tris);
    if (tris.empty())
    {
        if (uncoveredOnly) { *uncoveredOnly = true; return true; }
        pieces.emplace_back(loop, -1);
        return true;
    }
    // Pieces are never dropped for being small: a cap that loses a sliver
    // leaves the cell open. Only the numerically empty ones go.
    const scalar tiny = exactOutline ? scalar(1e-12) : std::max(scalar(1e-10), 1e-9 * area);

    // covered / uncovered convex pieces; a triangle takes its part of every
    // uncovered piece, and what it leaves is at most three convex pieces
    std::vector<std::vector<v2>> covered, uncovered{P};
    for (const auto& t : tris)
    {
        if (std::abs(t.normal & nu) < 0.9) { continue; }   // not this plane
        std::vector<v2> T{to2(t.p0), to2(t.p1), to2(t.p2)};
        for (v2& q : T) { q.p = cen + q.x * u + q.y * v; }
        const scalar ta = area2(T);
        if (std::abs(ta) < 1e-16) { continue; }
        if (ta < 0) { std::swap(T[1], T[2]); }
        std::vector<std::vector<v2>> next;
        for (const auto& Q0 : uncovered)
        {
            std::vector<v2> Q = Q0;
            for (int k = 0; k < 3; ++k)
            {
                // outside edge k (and inside the earlier edges): stays uncovered
                std::vector<v2> in, R;
                clipHalfPlaneBoth(Q, T[k], T[(k + 1) % 3], in, R);
                if (R.size() >= 3 && std::abs(area2(R)) > tiny) { next.push_back(std::move(R)); }
                Q = std::move(in);
                if (Q.size() < 3) { break; }
            }
            if (Q.size() >= 3 && std::abs(area2(Q)) > tiny) { covered.push_back(std::move(Q)); }
        }
        uncovered = std::move(next);
        if (uncovered.empty()) { break; }
    }
    scalar covA = 0, uncA = 0;
    for (const auto& Q : covered) { covA += area2(Q); }
    for (const auto& Q : uncovered) { uncA += area2(Q); }
    if (const char* boxEnv = getenv("DCCART_DEBUG_BOX"))
    {
        static double bb6[6];
        static Foam::boundBox dbgB(Foam::boundBox::invertedBox);
        static bool parsed = false;
        if (!parsed)
        {
            parsed = true;
            if (sscanf(boxEnv, "%lf %lf %lf %lf %lf %lf",
                       bb6, bb6+1, bb6+2, bb6+3, bb6+4, bb6+5) == 6)
            {
                dbgB = Foam::boundBox(Foam::point(bb6[0], bb6[1], bb6[2]),
                                      Foam::point(bb6[3], bb6[4], bb6[5]));
            }
        }
        Foam::boundBox lb(Foam::boundBox::invertedBox);
        for (label v : loop) { lb.add(m.points[v]); }
        if (dbgB.overlaps(lb))
        {
            Foam::Pout<< "  [cap] zone " << zone << " loop " << label(loop.size())
                << " box " << lb
                << " area " << area << " cov " << covA << " unc " << uncA
                << " tris " << label(tris.size())
                << " exact " << (exactOutline ? 1 : 0) << Foam::nl;
        }
    }
    const scalar coverageTolerance = exactOutline ? tiny : 1e-4 * area;
    if (covered.empty() || uncA <= coverageTolerance)
    {
        if (uncoveredOnly) { *uncoveredOnly = covered.empty(); return true; }
        pieces.emplace_back(loop, covered.empty() ? label(-1) : zone);
        return true;
    }
    if (covA <= coverageTolerance)
    {
        if (uncoveredOnly) { *uncoveredOnly = true; return true; }
        pieces.emplace_back(loop, -1);
        return true;
    }
    if (std::abs(covA + uncA - area) > 2e-3 * area)
    {
        if (uncoveredOnly) { *uncoveredOnly = (covA < 0.5 * area); return true; }
        pieces.emplace_back(loop, (covA >= 0.5 * area) ? zone : label(-1));
        return true;
    }
    // A sliver piece (the seam grazing a corner) is a face a few square
    // millimetres big with a skewness in the tens: it joins the other side,
    // and coalesceCoplanarFaces then folds it into its neighbour.
    if (!exactOutline)
    {
        const scalar tinyPiece = 0.03 * area;
        std::vector<std::vector<v2>> cov2, unc2;
        for (auto& Q : covered) { (std::abs(area2(Q)) < tinyPiece ? unc2 : cov2).push_back(std::move(Q)); }
        for (auto& Q : uncovered) { (std::abs(area2(Q)) < tinyPiece ? cov2 : unc2).push_back(std::move(Q)); }
        covered.swap(cov2); uncovered.swap(unc2);
        if (uncoveredOnly) { *uncoveredOnly = covered.empty(); return true; }
        if (covered.empty()) { pieces.emplace_back(loop, -1); return true; }
        if (uncovered.empty()) { pieces.emplace_back(loop, zone); return true; }
    }
    if (uncoveredOnly) { *uncoveredOnly = covered.empty(); return true; }

    std::map<std::pair<long long, long long>, label> ids;
    auto keyOf = [](const v2& q)
    {
        return std::make_pair
        (
            (long long)std::llround(q.x * 1e9),
            (long long)std::llround(q.y * 1e9)
        );
    };
    for (size_t i = 0; i < loop.size(); ++i) { ids[keyOf(P[i])] = loop[i]; }
    // a piece corner within 2% of the cap size (at least 0.1 mm) of a loop
    // vertex IS that vertex: the alternative is a sliver face a few square
    // microns big, with a skewness in the hundreds
    const scalar snapR2 = exactOutline ? scalar(1e-12) : std::max(scalar(1e-8), 4e-4 * area);
    auto snapLoop = [&](const v2& q) -> label
    {
        for (size_t i = 0; i < loop.size(); ++i)
        {
            const scalar dx = q.x - P[i].x, dy = q.y - P[i].y;
            if (dx * dx + dy * dy < snapR2) { return loop[i]; }
        }
        return -1;
    };
    auto emit = [&](const std::vector<v2>& Q, label z)
    {
        std::vector<label> poly;
        for (const v2& q : Q)
        {
            label id = snapLoop(q);
            if (id < 0)
            {
                const auto k = keyOf(q);
                auto it = ids.find(k);
                if (it != ids.end()) { id = it->second; }
                else
                {
                    id = label(m.points.size());
                    m.points.push_back(q.p);
                    side.push_back(0);
                    ids[k] = id;
                }
            }
            if (poly.empty() || poly.back() != id) { poly.push_back(id); }
        }
        while (poly.size() > 1 && poly.front() == poly.back()) { poly.pop_back(); }
        if (poly.size() >= 3) { pieces.emplace_back(std::move(poly), z); }
    };
    for (const auto& Q : covered) { emit(Q, zone); }
    for (const auto& Q : uncovered) { emit(Q, -1); }
    // The pieces must tile the loop, or the two cells on the face are open.
    // (tau: twice the clip's classification tolerance, below 1% of the cap)
    if (pieces.size() > 1)
    {
        const scalar tau = std::min(scalar(2e-5), 0.01 * std::sqrt(area));
        if (!conformPieces(m, loop, cen, u, v, tau, pieces))
        {
            scalar covKept = 0;
            for (const auto& Q : covered) { covKept += area2(Q); }
            if (getenv("DCCART_DEBUG"))
            {
                Foam::Info<< "  sheet split at " << cen << " does not tile its loop: kept as one face" << Foam::nl;
            }
            pieces.clear();
            pieces.emplace_back(loop, (covKept >= 0.5 * area) ? zone : label(-1));
        }
    }
    return !pieces.empty();
}


//- Split one polygon by the per-point side labels. Crossing points are
//  supplied by `crossing(a, b)` which returns -1 when the edge has no hit
//  (then the face cannot be split).
//  polyA gets side +1 and ON vertices, polyB side -1 and ON vertices.
//  `chords` are the new edges (pairs of cap vertices) closing the A side.
struct splitResult
{
    bool split = false;
    bool failed = false;        // mixed but an edge had no crossing
    signed char side = 1;       // when not split: +1 A, -1 B
    std::vector<label> polyA, polyB;
    std::vector<std::pair<label, label>> chords;
};

//- Side labels: +1 fluid (A), -1 solid (B), 0 ON the surface seen from
//  the fluid, -2 ON the surface seen from the solid (the blocking hit lands
//  on the point itself). `onIsA`: a fluid-side ON vertex counts as A when
//  deciding whether a face is mixed — a face lying in the wall plane whose
//  lower corners are inside a wall-mounted unit has no strictly fluid
//  vertex, yet it must split along the unit's top. Off for baffles, where
//  an ON vertex is on the cutting surface itself.
template<class CrossFn>
splitResult splitPolygon
(
    const std::vector<label>& poly,
    const std::vector<signed char>& side,
    CrossFn&& crossing,
    bool onIsA = true
)
{
    splitResult r;
    static const bool onOnLookup = getenv("DCCART_ONON") != nullptr;
    const label nSide = label(side.size());
    // a point made during this pass (a crossing) is on the cut
    auto sd = [&](label v) -> signed char { return v < nSide ? side[v] : 0; };
    auto sv = [&](label v) -> signed char { return sd(v) == -2 ? 0 : sd(v); };
    bool hasA = false, hasB = false;
    for (label v : poly)
    {
        if (sd(v) > 0 || (onIsA && sd(v) == 0)) { hasA = true; }
        if (sd(v) == -1) { hasB = true; }
    }
    if (!(hasA && hasB))
    {
        r.side = hasB ? -1 : 1;
        return r;
    }
    const label k = label(poly.size());
    // The walk, with crossings spliced in. Each entry: vertex id, side
    // (+1 / -1 / 0 for ON and crossings).
    struct wv { label v; signed char s; };
    std::vector<wv> walk;
    label start = 0;
    while (start < k && sv(poly[start]) == 0) { ++start; }
    if (start == k) { r.side = 1; return r; }
    for (label t = 0; t < k; ++t)
    {
        const label i = (start + t) % k;
        const label j = (start + t + 1) % k;
        const label vi = poly[i];
        const label vj = poly[j];
        walk.push_back({vi, sv(vi)});
        const int prod = int(sv(vi)) * int(sv(vj));
        if (prod == -1)
        {
            const label x = crossing(vi, vj);
            if (x < 0) { r.failed = true; return r; }
            walk.push_back({x, 0});
        }
        else if
        (
            prod == 0
         && ((sd(vi) == -1 && sd(vj) == 0) || (sd(vj) == -1 && sd(vi) == 0)
          || (onOnLookup && sv(vi) == 0 && sv(vj) == 0 && vi < nSide && vj < nSide))
        )
        {
            // B to ON: the ON vertex may sit on ANOTHER surface (a corner
            // on the wall plane while inside the unit mounted on that
            // wall), in which case the cut crosses this edge somewhere
            // before it. `crossing` answers -2 when the ON vertex itself is
            // the crossing, and a genuine hit otherwise.
            const label x = crossing(vi, vj);
            if (x >= 0) { walk.push_back({x, 0}); }
        }
    }
    const label n = label(walk.size());
    auto at = [&](label i) -> const wv& { return walk[((i % n) + n) % n]; };

    // Cap vertices are the side-0 entries. A CHORD joins two consecutive cap
    // vertices only when a run of B vertices lies between them: that run is
    // what the A polygon has to be closed across. Two cap vertices with
    // nothing between them sit on one original edge — the surface runs along
    // that edge — and that edge is not a new face edge.
    std::vector<label> capIdx;
    for (label i = 0; i < n; ++i) { if (walk[i].s == 0) { capIdx.push_back(i); } }
    const label nc = label(capIdx.size());
    if (nc < 2) { r.side = -1; return r; }
    for (label q = 0; q < nc; ++q)
    {
        const label i0 = capIdx[q];
        const label i1 = capIdx[(q + 1) % nc];
        // run between i0 and i1 (exclusive), cyclic
        signed char run = 0;
        label steps = ((i1 - i0) % n + n) % n;
        if (steps == 0) { steps = n; }
        for (label d = 1; d < steps; ++d) { run = at(i0 + d).s; if (run != 0) { break; } }
        if (run < 0 || (run > 0 && nc == 2))
        {
            const label a = walk[i0].v;
            const label b = walk[i1].v;
            if (a != b) { r.chords.emplace_back(a, b); }
        }
    }
    if (nc == 2 && r.chords.size() > 1) { r.chords.resize(1); }
    if (r.chords.empty())
    {
        // nothing to close across: the surface runs along the boundary of
        // this face, which therefore lies wholly on one side
        r.side = -1;
        return r;
    }

    // polyA: A vertices, crossings and every ON vertex (an ON vertex is on
    // the A boundary whether the cut runs through it or along its edge).
    // polyB: B vertices, crossings, and only the ON vertices that touch a B
    // vertex — the others lie on an A edge the B region never reaches.
    for (label i = 0; i < n; ++i)
    {
        const wv& w = walk[i];
        const bool isCrossing = (w.s == 0 && sd(w.v) != 0) || (w.v >= nSide);
        const bool isOn = (w.s == 0 && !isCrossing);
        if (w.s > 0) { r.polyA.push_back(w.v); }
        else if (w.s < 0) { r.polyB.push_back(w.v); }
        else
        {
            r.polyA.push_back(w.v);
            const bool nearB = (at(i - 1).s < 0) || (at(i + 1).s < 0);
            if (isCrossing || nearB || !isOn) { r.polyB.push_back(w.v); }
        }
    }
    if (r.polyB.size() < 3) { r.polyA.clear(); r.polyB.clear(); r.chords.clear(); r.side = 1; return r; }
    if (r.polyA.size() < 3) { r.polyA.clear(); r.polyB.clear(); r.chords.clear(); r.side = -1; return r; }
    r.split = true;
    return r;
}

} // end anonymous namespace


// * * * * * * * * * * * * * * * fromBuilder * * * * * * * * * * * * * * * * //

void Foam::dcCartCutCells::fromBuilder
(
    const dcCartMeshBuilder& b,
    const dcCartGrid& grid,
    const boolList& origAlive,
    cutMesh& m
)
{
    const pointField& pts = b.points();
    m.points.assign(pts.begin(), pts.end());
    // The builder's points are distinct grid corners. When the finest grid
    // spacing is well above the 1e-6 merge tolerance no two of them can
    // merge, and mergeCoincidentPoints may file them without a search.
    {
        label deepest = 0;
        forAll(b.cellLevel(), c) { deepest = max(deepest, b.cellLevel()[c]); }
        scalar minSpacing = GREAT;
        for (direction d = 0; d < 3; ++d)
        {
            const scalarField& bp = grid.breakpoints(d);
            for (label i = 1; i < bp.size(); ++i) { minSpacing = min(minSpacing, bp[i] - bp[i - 1]); }
        }
        minSpacing /= scalar(label(1) << deepest);
        m.nGridPoints = (minSpacing > 4e-6) ? label(pts.size()) : 0;
    }
    const faceList& fcs = b.faces();
    const label nF = fcs.size();
    m.faces.resize(nF);
    m.owner.assign(nF, -1);
    m.neighbour.assign(nF, -1);
    m.patch.assign(nF, -1);
    m.zone.assign(nF, -1);
    m.faceAlive.assign(nF, true);
    forAll(fcs, f)
    {
        m.faces[f].assign(fcs[f].begin(), fcs[f].end());
        m.owner[f] = b.faceOwner()[f];
        m.neighbour[f] = (f < b.nInternalFaces()) ? b.faceNeighbour()[f] : -1;
    }
    forAll(b.patchStarts(), p)
    {
        for (label i = 0; i < b.patchSizes()[p]; ++i)
        {
            m.patch[b.patchStarts()[p] + i] = p;
        }
    }
    forAll(b.faceZoneFaces(), z)
    {
        for (label f : b.faceZoneFaces()[z]) { m.zone[f] = z; }
    }
    m.nCells = b.nCells();
    m.cellAlive.assign(m.nCells, true);
    m.cellOrigAlive.assign(m.nCells, true);
    m.cellRefVol.assign(m.nCells, 0);
    m.cellRefCentre.assign(m.nCells, point::zero);
    m.cellLevel.assign(m.nCells, 0);
    for (label c = 0; c < m.nCells; ++c)
    {
        const label L = b.cellLevel()[c];
        const labelVector& v = b.cellIndex()[c];
        const boundBox bb = grid.cellBox(L, v);
        m.cellRefVol[c] = bb.volume();
        m.cellRefCentre[c] = bb.centre();
        m.cellLevel[c] = L;
        m.cellOrigAlive[c] = (c < origAlive.size()) ? origAlive[c] : true;
    }
    m.cellRank.assign(m.nCells, 0);
    m.cellLeaf.assign(m.nCells, 0);
    m.cellPiece.assign(m.nCells, 1);
    for (label c = 0; c < m.nCells; ++c)
    {
        const labelVector& v = b.cellIndex()[c];
        m.cellLeaf[c] = dcCartPack(v.x(), v.y(), v.z());
    }
}


// * * * * * * * * * * * * * * classifyCorners * * * * * * * * * * * * * * * //

void Foam::dcCartCutCells::classifyCorners
(
    const pointField& points,
    std::vector<std::vector<label>>& pointCells,
    const std::function<point(label)>& cellCentre,
    const dcCartSurfaces& surfaces,
    scalar tolOn,
    std::vector<signed char>& side,
    const std::vector<bool>* only
)
{
    const label nP = label(points.size());
    side.assign(nP, -1);
    for (label p = 0; p < nP; ++p)
    {
        // (a point nobody will look up keeps -1)
        if (only && (p >= label(only->size()) || !(*only)[p])) { continue; }
        auto& lst = pointCells[p];
        if (lst.empty()) { continue; }
        std::sort(lst.begin(), lst.end());
        lst.erase(std::unique(lst.begin(), lst.end()), lst.end());
        // Reachability first: a point some original live cell centre reaches
        // without crossing a surface is fluid; one every centre is blocked
        // from is solid. ONLY THEN is "on the surface" decided — a fluid
        // point within tolOn of a surface (a grid-aligned wall, a corner the
        // surface passes through) is ON: no edge from it gets a crossing and
        // no cell is re-admitted on its account. A point that is blocked is
        // ON only when the blocking hit lands on the point itself, i.e. the
        // point sits on the surface as seen from the fluid.
        //
        // The order matters. Deciding ON by distance BEFORE reachability
        // made every corner that lies on one surface while being INSIDE
        // another (a wall-mounted unit's corners on the wall plane) ON
        // instead of solid, so the face on the wall plane never split, the
        // cut loop through that cell could not close, and the whole hex was
        // kept stair-step — the unit's top came out a cell too high.
        signed char best = -1;
        bool hitOnPoint = false;
        for (label cc : lst)
        {
            const point c0 = cellCentre(cc);
            point hit; label region;
            if (!surfaces.carvingHit(c0, points[p], hit, region))
            {
                best = 1;
                break;
            }
            if (magSqr(hit - points[p]) <= tolOn * tolOn) { hitOnPoint = true; }
        }
        if (best > 0)
        {
            if (surfaces.nearestCarvingDistance(points[p], 4 * tolOn) <= tolOn)
            {
                best = 0;
            }
        }
        else if (hitOnPoint)
        {
            best = -2;  // ON, seen from the solid: on the surface, not fluid
        }
        side[p] = best;
    }
}


// * * * * * * * * * * * * * * * generic pass * * * * * * * * * * * * * * * //

namespace
{

//- One clip pass. `capBoundary`: the B half is dropped and caps become
//  boundary faces (patch from regionOfCap); else caps are internal faces in
//  `capZone` between the halves.
struct passArgs
{
    bool capBoundary = true;
    label capZone = -1;
    //- cells eligible for splitting (empty = all live cells)
    const std::vector<bool>* eligible = nullptr;
    //- boundary caps: split a cap loop into per-region pieces
    //  (cell, loop, pieces) -> true when pieces are to be used
    const std::function<bool(label, const std::vector<label>&, const point&,
        std::vector<std::pair<std::vector<label>, label>>&,
        std::vector<chordInsert>&)>* splitCap = nullptr;
    //- out: per original cell, whether the pass split it
    std::vector<bool>* splitOut = nullptr;
    //- optional ancestry across successive clip planes; each appended half
    //  inherits its parent's initial-cell index.
    std::vector<label>* splitOrigin = nullptr;
    //- where the surface's feature edges pierce a face between the two
    //  crossings a and b of a chord: the trace of the surface on the face
    //  is the bent polyline a -> x... -> b rather than the chord. Points
    //  come ordered from a to b.
    const std::function<void(label, label, std::vector<point>&)>* pierce = nullptr;
    //- fluid-side ON vertices count as fluid when splitting faces
    bool onIsA = true;
    //- counters count only the cells of this rank (-1: every cell)
    label countRank = -1;
    //- facet normal at a crossing point (carving only): lets an ON vertex
    //  be put on the solid side of a facet that crosses the face next to it
    const std::function<bool(label, vector&)>* hitNormal = nullptr;
    scalar planeTol = 0;
    //- internal caps: true when the cap of this loop would be phantom
    //  whole; a cell all of whose caps are is left unsplit
    const std::function<bool(label, const std::vector<label>&)>* uncoveredCap = nullptr;
    //- The plane this pass splits on. A split supersedes a baffle tag ONLY on
    //  faces that lie in THIS sheet's plane: a cell at the junction of two
    //  sheets of one zone (or of two perpendicular baffles) is split by one
    //  plane while its face in the OTHER plane is still the baffle there.
    //  Clearing that tag too punched a hole straight through the baffle —
    //  the containment-baffle leak that let hot and cold aisles exchange
    //  fluid and diverged the solve hundreds of iterations later.
    bool havePlane = false;
    point planeOrigin = point::zero;
    vector planeNormal = vector::zero;
};

//- Faces of cells that just died: dropped when both sides are dead, flipped
//  to the boundary (patch from `regionOfFace` when none yet) otherwise.
template<class RegionFn>
void dropDeadFaces(Foam::dcCartCutCells::cutMesh& m, RegionFn&& regionOfFace)
{
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        const bool ownDead = (m.owner[f] < 0) || !m.cellAlive[m.owner[f]];
        const bool neiDead = (m.neighbour[f] < 0) || !m.cellAlive[m.neighbour[f]];
        if (ownDead && neiDead) { m.faceAlive[f] = false; continue; }
        if (ownDead)
        {
            // flip: the survivor becomes the owner, normal out of it
            std::reverse(m.faces[f].begin(), m.faces[f].end());
            m.owner[f] = m.neighbour[f];
            m.neighbour[f] = -1;
            if (m.patch[f] < 0) { m.patch[f] = regionOfFace(m.faces[f]); }
            m.zone[f] = -1;
        }
        else if (neiDead && m.neighbour[f] >= 0)
        {
            m.neighbour[f] = -1;
            if (m.patch[f] < 0) { m.patch[f] = regionOfFace(m.faces[f]); }
            m.zone[f] = -1;
        }
    }
}

template<class CrossFn, class LookupFn, class RegionFn>
void clipPass
(
    Foam::dcCartCutCells::cutMesh& m,
    const std::vector<signed char>& side,
    CrossFn&& crossing,          // (a, b) -> new point id or -1
    LookupFn&& existingCrossing, // (a, b) -> point id already made, or -1
    RegionFn&& regionOfFace,     // (face vertex list) -> patch id
    const passArgs& args,
    label& nCut,
    label& nFallback,
    label& nCaps
)
{
    using Foam::dcCartCutCells;
    const label nF0 = label(m.faces.size());

    // ---- 1. split faces ----------------------------------------------------
    // For a split face, fA keeps the original slot (polyA) and fB is appended.
    std::vector<label> faceB(nF0, -1);           // id of the appended B polygon
    std::vector<signed char> faceSide(nF0, 1);   // side of an unsplit face
    std::vector<std::vector<std::pair<label, label>>> faceChords(nF0);
    std::vector<bool> faceFailed(nF0, false);
    std::vector<bool> faceSplit(nF0, false);

    // Only faces of eligible cells: a face of a cell the pass does not
    // touch carries points that were never classified (they default to
    // "outside") next to points that were, and would be split against a
    // crossing that is not there.
    auto counted = [&](label c) -> bool
    {
        return args.countRank < 0
            || (c >= 0 && c < label(m.cellRank.size()) && m.cellRank[c] == args.countRank);
    };
    auto touches = [&](label f) -> bool
    {
        if (!args.eligible) { return true; }
        const label o = m.owner[f], n = m.neighbour[f];
        return (o >= 0 && (*args.eligible)[o]) || (n >= 0 && (*args.eligible)[n]);
    };
    // An ON vertex is ON SOME facet of the shell — not necessarily the one
    // crossing the faces next to it: a corner on a wall unit's side plane,
    // above its curved front, is ON the side and INSIDE the unit as far as
    // the front facet is concerned. Wherever a facet crosses an edge that
    // ends at (or joins two) ON vertices, every ON vertex of that face on
    // the solid side of that facet — the side a -1 vertex of the face is
    // on, or opposite a +1 one — is demoted to -1, for every face alike.
    // Without it the cut loop runs around the ON corners along the side
    // plane and the cap comes out folded over two facets.
    std::vector<signed char> sideEff(side.begin(), side.end());
    // (off by default: a crossing point on one facet of a convex corner is
    // on the solid side of the neighbouring facet's plane too, and the rule
    // demoted it — the rotated cube lost 16 cells to it; DCCART_DEMOTE=1 to
    // try again)
    static const bool demote = getenv("DCCART_DEMOTE") != nullptr;
    if (args.hitNormal && demote)
    {
        const label nP0 = label(sideEff.size());
        label nDemoted = 0;
        std::vector<label> refs;
        auto isOn = [&](label v) -> bool { return v < nP0 && (side[v] == 0 || side[v] == -2); };
        for (label f = 0; f < nF0; ++f)
        {
            if (!m.faceAlive[f] || !touches(f)) { continue; }
            const std::vector<label>& poly = m.faces[f];
            const label k = label(poly.size());
            bool anyOn = false;
            for (label v : poly) { if (isOn(v)) { anyOn = true; break; } }
            if (!anyOn) { continue; }
            for (label i = 0; i < k; ++i)
            {
                const label a = poly[i], b = poly[(i + 1) % k];
                if (a == b || !(isOn(a) || isOn(b))) { continue; }
                if (a >= nP0 || b >= nP0) { continue; }
                const label x = crossing(a, b);
                if (x < 0) { continue; }
                vector n;
                if (!(*args.hitNormal)(x, n)) { continue; }
                const point& px = m.points[x];
                // the solid side of this facet, from the face's decided vertices
                int solidSign = 0;
                bool clash = false;
                for (label v : poly)
                {
                    if (v >= nP0 || side[v] == 0 || side[v] == -2) { continue; }
                    const scalar d = (m.points[v] - px) & n;
                    if (std::abs(d) <= args.planeTol) { continue; }
                    const int sg = (side[v] < 0) ? (d > 0 ? 1 : -1) : (d > 0 ? -1 : 1);
                    if (solidSign && sg != solidSign) { clash = true; }
                    solidSign = sg;
                }
                if (!solidSign || clash) { continue; }
                for (label v : poly)
                {
                    if (!isOn(v) || sideEff[v] == -1) { continue; }
                    const scalar d = (m.points[v] - px) & n;
                    if (std::abs(d) <= args.planeTol) { continue; }
                    if ((d > 0 ? 1 : -1) == solidSign) { sideEff[v] = -1; ++nDemoted; }
                }
            }
        }
        if (nDemoted && getenv("DCCART_DEBUG"))
        {
            Foam::Info<< "    demoted " << nDemoted << " ON vertices to the solid side of a facet crossing next to them" << Foam::nl;
        }
    }
    // a point made during this pass (a crossing) is on the cut
    auto sdE = [&](label v) -> signed char { return v < label(sideEff.size()) ? sideEff[v] : 0; };
    for (label f = 0; f < nF0; ++f)
    {
        if (!m.faceAlive[f] || !touches(f)) { continue; }
        splitResult r = splitPolygon(m.faces[f], sideEff, crossing, args.onIsA);
        if (r.failed) { faceFailed[f] = true; faceSide[f] = 1; continue; }
        if (!r.split) { faceSide[f] = r.side; continue; }
        faceSplit[f] = true;
        if (args.pierce)
        {
            std::vector<std::pair<label, label>> bent;
            for (const auto& ch : r.chords)
            {
                std::vector<point> xs;
                (*args.pierce)(ch.first, ch.second, xs);
                if (xs.empty()) { bent.push_back(ch); continue; }
                std::vector<label> ids;
                for (const point& x : xs)
                {
                    ids.push_back(label(m.points.size()));
                    m.points.push_back(x);
                    const_cast<std::vector<signed char>&>(side).push_back(0);
                }
                auto bend = [&](std::vector<label>& poly)
                {
                    const label k = label(poly.size());
                    for (label i = 0; i < k; ++i)
                    {
                        const label u = poly[i], v = poly[(i + 1) % k];
                        if (u == ch.first && v == ch.second)
                        {
                            poly.insert(poly.begin() + i + 1, ids.begin(), ids.end());
                            return;
                        }
                        if (u == ch.second && v == ch.first)
                        {
                            poly.insert(poly.begin() + i + 1, ids.rbegin(), ids.rend());
                            return;
                        }
                    }
                };
                bend(r.polyA);
                bend(r.polyB);
                label prev = ch.first;
                for (label id : ids) { bent.emplace_back(prev, id); prev = id; }
                bent.emplace_back(prev, ch.second);
            }
            r.chords = bent;
        }
        m.faces[f] = r.polyA;
        m.faces.push_back(r.polyB);
        m.owner.push_back(m.owner[f]);
        m.neighbour.push_back(m.neighbour[f]);
        m.patch.push_back(m.patch[f]);
        m.zone.push_back(m.zone[f]);
        m.faceAlive.push_back(true);
        faceB[f] = label(m.faces.size()) - 1;
        faceChords[f] = std::move(r.chords);
    }

    // A crossing point is a new vertex on an EDGE, and every face around
    // that edge must carry it or the mesh stops being conformal. Faces that
    // were split already have it; a face left whole (all one side, or one
    // whose split failed at a surface's free edge) gets it spliced in as a
    // hanging vertex, exactly as the octree's mid-edge points are.
    // EVERY alive face, not only the touched ones: a cell that only shares
    // an EDGE with an eligible cell (a diagonal neighbour) has faces on
    // that edge too, and without the vertex the mesh is non-conformal —
    // the next pass then finds two nearly coincident points on one edge
    // (80 um apart, seen at a wall unit's curved front) and its loops do
    // not close.
    {
        // endpoints of the split edges: only faces holding one can need a vertex
        std::vector<bool> endPt(m.points.size(), false);
        for (label f = 0; f < nF0; ++f)
        {
            if (!faceSplit[f] || !m.faceAlive[f]) { continue; }
            for (label v : m.faces[f]) { if (v < label(endPt.size())) { endPt[v] = true; } }
            const label fb = faceB[f];
            if (fb >= 0) { for (label v : m.faces[fb]) { if (v < label(endPt.size())) { endPt[v] = true; } } }
        }
        for (label f = 0; f < nF0; ++f)
        {
            if (!m.faceAlive[f] || faceSplit[f]) { continue; }
            const std::vector<label>& poly = m.faces[f];
            const label k = label(poly.size());
            bool any = false;
            for (label v : poly) { if (v < label(endPt.size()) && endPt[v]) { any = true; break; } }
            if (!any) { continue; }
            std::vector<label> out;
            bool changed = false;
            for (label i = 0; i < k; ++i)
            {
                const label a = poly[i];
                const label b = poly[(i + 1) % k];
                out.push_back(a);
                const label x = existingCrossing(a, b);
                if (x >= 0 && x != a && x != b) { out.push_back(x); changed = true; }
            }
            if (changed) { m.faces[f] = out; }
        }
    }

    // ---- 2. split cells ----------------------------------------------------
    std::vector<std::vector<label>> cellFaces;
    // cell faces over the ORIGINAL face ids (B copies are handled through faceB)
    {
        std::vector<label> count(m.nCells, 0);
        for (label f = 0; f < nF0; ++f)
        {
            if (!m.faceAlive[f]) { continue; }
            if (m.owner[f] >= 0) { ++count[m.owner[f]]; }
            if (m.neighbour[f] >= 0) { ++count[m.neighbour[f]]; }
        }
        cellFaces.assign(m.nCells, {});
        for (label c = 0; c < m.nCells; ++c)
        {
            if (count[c]) { cellFaces[c].reserve(count[c]); }
        }
    }
    for (label f = 0; f < nF0; ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        if (m.owner[f] >= 0) { cellFaces[m.owner[f]].push_back(f); }
        if (m.neighbour[f] >= 0) { cellFaces[m.neighbour[f]].push_back(f); }
    }

    const label nCells0 = m.nCells;
    std::vector<label> halfB(nCells0, -1);     // id of the B half, -1 unsplit
    if (args.splitOut) { args.splitOut->assign(nCells0, false); }

    for (label c = 0; c < nCells0; ++c)
    {
        if (!m.cellAlive[c]) { continue; }
        if (args.eligible && !(*args.eligible)[c]) { continue; }
        // mixed corners?
        bool hasA = false, hasB = false, anySplit = false, anyFailed = false;
        std::vector<std::pair<label, label>> chords;
        // ON-ON edges of B-side faces: the cut runs along that edge, and it
        // is a cap edge exactly once — twice (both faces B) means the edge
        // is interior to the solid part and is dropped again.
        std::unordered_map<uint64_t, label> onEdgeUse;
        for (label f : cellFaces[c])
        {
            if (faceFailed[f]) { anyFailed = true; }
            if (faceSplit[f])
            {
                anySplit = true;
                chords.insert(chords.end(), faceChords[f].begin(), faceChords[f].end());
            }
            else if (faceSide[f] < 0)
            {
                const std::vector<label>& poly = m.faces[f];
                const label k = label(poly.size());
                for (label i = 0; i < k; ++i)
                {
                    const label a = poly[i], b = poly[(i + 1) % k];
                    if (a != b && sdE(a) == 0 && sdE(b) == 0) { ++onEdgeUse[edgeKey(a, b)]; }
                }
            }
            for (label v : m.faces[f])
            {
                if (sdE(v) > 0) { hasA = true; }
                else if (sdE(v) == -1) { hasB = true; }
            }
            if (faceB[f] >= 0)
            {
                for (label v : m.faces[faceB[f]])
                {
                    if (sdE(v) == -1) { hasB = true; }
                }
            }
        }
        if (!(hasA && hasB)) { continue; }
        for (label f : cellFaces[c])
        {
            if (faceSplit[f] || faceSide[f] >= 0) { continue; }
            const std::vector<label>& poly = m.faces[f];
            const label k = label(poly.size());
            for (label i = 0; i < k; ++i)
            {
                const label a = poly[i], b = poly[(i + 1) % k];
                if (a == b || sdE(a) != 0 || sdE(b) != 0) { continue; }
                auto it = onEdgeUse.find(edgeKey(a, b));
                if (it != onEdgeUse.end() && it->second == 1)
                {
                    chords.emplace_back(a, b);
                    it->second = 0;   // once
                }
            }
        }
        // Coincident points with different ids (a crossing made on an
        // edge and a cap-piece corner at the same spot) would leave the
        // chain open: chain on one id per position. toPolyMesh merges the
        // duplicates for good.
        {
            pointCanon canon;
            auto canonOf = [&](label v) -> label { return canon.canon(m.points, v); };
            for (auto& ch : chords)
            {
                ch.first = canonOf(ch.first);
                ch.second = canonOf(ch.second);
            }
            // a chord that became degenerate, or a duplicate of another, is dropped
            std::set<std::pair<label, label>> seen;
            std::vector<std::pair<label, label>> uniq;
            for (const auto& ch : chords)
            {
                if (ch.first == ch.second) { continue; }
                const auto key = std::minmax(ch.first, ch.second);
                if (seen.insert(key).second) { uniq.push_back(ch); }
            }
            chords.swap(uniq);
        }
        std::vector<std::vector<label>> loops;
        // (no split face but a closed loop of ON-ON edges: the cut runs
        // exactly along face edges, e.g. a facet plane through the cell
        // that a neighbour's pre-split already drew on the shared faces)
        // ---- DCCART_DEBUG_CELL: split-decision trace (temporary) ----------
        if (getenv("DCCART_DEBUG_CELL"))
        {
            double bc[6] = {0, 0, 0, 0, 0, 0};
            if (sscanf(getenv("DCCART_DEBUG_CELL"), "%lf,%lf,%lf,%lf,%lf,%lf",
                       &bc[0], &bc[1], &bc[2], &bc[3], &bc[4], &bc[5]) == 6)
            {
                const Foam::boundBox tbox(Foam::point(bc[0], bc[1], bc[2]), Foam::point(bc[3], bc[4], bc[5]));
                Foam::boundBox cb(Foam::boundBox::invertedBox);
                for (const label f : cellFaces[c])
                {
                    for (const label v : m.faces[f]) { cb.add(m.points[v]); }
                }
                if (cb.valid() && cb.overlaps(tbox))
                {
                    std::vector<std::vector<label>> dbgLoops;
                    const bool dbgChained = chainLoops(chords, dbgLoops);
                    Foam::Pout<< "SPLITDEC rank " << Foam::Pstream::myProcNo()
                        << " cell " << c
                        << " hasA " << (hasA ? 1 : 0) << " hasB " << (hasB ? 1 : 0)
                        << " anySplit " << (anySplit ? 1 : 0)
                        << " anyFailed " << (anyFailed ? 1 : 0)
                        << " chords " << label(chords.size())
                        << " chained " << (dbgChained ? 1 : 0)
                        << " loops " << label(dbgLoops.size())
                        << " capZone " << args.capZone
                        << " onIsA " << (args.onIsA ? 1 : 0)
                        << " plane " << (args.havePlane ? args.planeOrigin : Foam::point::zero)
                        << " n " << (args.havePlane ? args.planeNormal : Foam::vector::zero)
                        << Foam::nl;
                    for (const label f : cellFaces[c])
                    {
                        Foam::Pout<< "  FACE rank " << Foam::Pstream::myProcNo()
                            << " cell " << c << " f " << f
                            << " alive " << (m.faceAlive[f] ? 1 : 0)
                            << " touches " << (touches(f) ? 1 : 0)
                            << " split " << (faceSplit[f] ? 1 : 0)
                            << " failed " << (faceFailed[f] ? 1 : 0)
                            << " fside " << int(faceSide[f])
                            << " own " << m.owner[f] << " nbr " << m.neighbour[f]
                            << " poly";
                        for (const label v : m.faces[f])
                        {
                            Foam::Pout<< " [" << v << " " << m.points[v]
                                << " s" << int(sdE(v)) << "]";
                        }
                        Foam::Pout<< Foam::nl;
                    }
                    for (const auto& ch : chords)
                    {
                        Foam::Pout<< "  CHORD rank " << Foam::Pstream::myProcNo()
                            << " cell " << c
                            << " " << m.points[ch.first]
                            << " -> " << m.points[ch.second] << Foam::nl;
                    }
                }
            }
        }
        if (anyFailed || chords.empty() || !chainLoops(chords, loops))
        {
            if (counted(c)) { ++nFallback; }
            if (getenv("DCCART_DEBUG"))
            {
                const point& rc = m.cellRefCentre[c];
                Foam::Info<< "  fallback cell " << c << " at " << rc
                    << (anyFailed ? " face-split-failed" : "")
                    << (!anySplit ? " no-face-split" : "")
                    << ((!anyFailed && anySplit) ? " chain-failed" : "")
                    << " chords " << label(chords.size())
                    << " orig " << (m.cellOrigAlive[c] ? 1 : 0)
                    << " level " << m.cellLevel[c] << Foam::nl;
                static label nDetail = 0;
                static const char* boxEnv = getenv("DCCART_DEBUG_BOX");
                static Foam::boundBox dbgBox(Foam::boundBox::invertedBox);
                static bool boxParsed = false;
                if (boxEnv && !boxParsed)
                {
                    boxParsed = true;
                    double b[6];
                    if (sscanf(boxEnv, "%lf %lf %lf %lf %lf %lf", b, b+1, b+2, b+3, b+4, b+5) == 6)
                    {
                        dbgBox = Foam::boundBox(Foam::point(b[0], b[1], b[2]), Foam::point(b[3], b[4], b[5]));
                    }
                }
                const bool wanted = boxEnv ? dbgBox.contains(rc) : (nDetail < 8);
                if (wanted)
                {
                    ++nDetail;
                    for (label f : cellFaces[c])
                    {
                        Foam::Info<< "    face " << f << (faceSplit[f] ? " SPLIT" : "")
                            << (faceFailed[f] ? " FAILED" : "") << " sides:";
                        for (label v : m.faces[f])
                        {
                            Foam::Info<< " " << label(sdE(v)) << "@" << m.points[v];
                        }
                        Foam::Info<< " chords:";
                        for (const auto& ch : faceChords[f])
                        {
                            Foam::Info<< " [" << m.points[ch.first] << "-" << m.points[ch.second] << "]";
                        }
                        Foam::Info<< Foam::nl;
                    }
                    Foam::Info<< "    all chords (ids):";
                    for (const auto& ch : chords)
                    {
                        Foam::Info<< " " << ch.first << "-" << ch.second;
                    }
                    Foam::Info<< Foam::nl;
                }
            }
            continue;
        }

        //- True when face f's tag belongs to THIS pass's sheet, i.e. the cap
        //  this split creates really replaces it. A face lying in another
        //  sheet's plane keeps its tag: see passArgs::havePlane.
        auto tagSuperseded = [&](label f) -> bool
        {
            if (args.capZone < 0 || m.zone[f] != args.capZone) { return false; }
            if (!args.havePlane) { return true; }
            vector nn; point cc;
            polygonNormalCentre(m.points, m.faces[f], nn, cc);
            const scalar tol = Foam::max(args.planeTol, scalar(Foam::SMALL));
            return Foam::mag((cc - args.planeOrigin) & args.planeNormal) <= tol;
        };

        // A cell that meets the sheet's plane only beyond the sheet (every
        // cap phantom) stays whole: halving it leaves two slivers that may
        // merge apart, the one up and the other down, into a stepped pair.
        // The sheet does not pass through it, so the stair-step baffle tags
        // the builder put on its faces go as they do when a cell is halved
        // (kept, they were a wall 42 mm under the wall baffle's hole edge)
        if (args.uncoveredCap)
        {
            bool allUncovered = true;
            for (const auto& loop : loops)
            {
                if (!(*args.uncoveredCap)(c, loop)) { allUncovered = false; break; }
            }
            if (allUncovered)
            {
                for (label f : cellFaces[c])
                {
                    if (tagSuperseded(f))
                    {
                        m.zone[f] = -1;
                        if (faceB[f] >= 0) { m.zone[faceB[f]] = -1; }
                    }
                }
                continue;
            }
        }

        // the B half
        const label cB = m.nCells++;
        if (args.splitOrigin) { args.splitOrigin->push_back((*args.splitOrigin)[c]); }
        m.cellAlive.push_back(true);
        m.cellOrigAlive.push_back(m.cellOrigAlive[c]);
        m.cellRank.push_back(c < label(m.cellRank.size()) ? m.cellRank[c] : 0);
        m.cellLeaf.push_back(c < label(m.cellLeaf.size()) ? m.cellLeaf[c] : 0);
        {
            const uint64_t parent = (c < label(m.cellPiece.size())) ? m.cellPiece[c] : 1;
            m.cellPiece.push_back(pieceHash(parent, 2));
            if (c < label(m.cellPiece.size())) { m.cellPiece[c] = pieceHash(parent, 1); }
        }
        m.cellRefVol.push_back(m.cellRefVol[c]);
        m.cellRefCentre.push_back(m.cellRefCentre[c]);
        m.cellLevel.push_back(m.cellLevel[c]);
        halfB[c] = cB;
        if (counted(c)) { ++nCut; }
        if (args.splitOut) { (*args.splitOut)[c] = true; }

        auto reassign = [&](label f)
        {
            if (m.owner[f] == c) { m.owner[f] = cB; }
            else if (m.neighbour[f] == c) { m.neighbour[f] = cB; }
        };
        for (label f : cellFaces[c])
        {
            if (faceSplit[f]) { reassign(faceB[f]); }
            else if (faceSide[f] < 0) { reassign(f); }
            // a baffle tag on this cell's own face is superseded by the cap
            if (tagSuperseded(f))
            {
                m.zone[f] = -1;
                if (faceB[f] >= 0) { m.zone[faceB[f]] = -1; }
            }
        }

        // caps: oriented from the A half to the B half
        point cA = point::zero; label nA = 0;
        point cBc = point::zero; label nB = 0;
        for (label f : cellFaces[c])
        {
            vector nn; point cc;
            if (faceSplit[f] || faceSide[f] >= 0)
            {
                polygonNormalCentre(m.points, m.faces[f], nn, cc); cA += cc; ++nA;
            }
            if (faceSplit[f])
            {
                polygonNormalCentre(m.points, m.faces[faceB[f]], nn, cc); cBc += cc; ++nB;
            }
            else if (faceSide[f] < 0)
            {
                polygonNormalCentre(m.points, m.faces[f], nn, cc); cBc += cc; ++nB;
            }
        }
        if (nA) { cA /= scalar(nA); }
        if (nB) { cBc /= scalar(nB); }
        static const bool splitBoxEnv = (getenv("DCCART_DEBUG_BOX") != nullptr);
        if (splitBoxEnv)
        {
            static Foam::boundBox sbox(Foam::boundBox::invertedBox);
            static bool sparsed = false;
            if (!sparsed)
            {
                sparsed = true; double b[6];
                if (sscanf(getenv("DCCART_DEBUG_BOX"), "%lf %lf %lf %lf %lf %lf", b, b+1, b+2, b+3, b+4, b+5) == 6)
                { sbox = Foam::boundBox(Foam::point(b[0], b[1], b[2]), Foam::point(b[3], b[4], b[5])); }
            }
            if (sbox.contains(m.cellRefCentre[c]))
            {
                const int oldPrec = Foam::Info().precision(12);
                Foam::Info<< "  split cell " << c << " at " << m.cellRefCentre[c] << " loops " << label(loops.size()) << Foam::nl;
                for (const auto& loop : loops)
                {
                    Foam::Info<< "    loop:";
                    for (label v : loop) { Foam::Info<< " " << label(sdE(v)) << "#" << v << "@" << m.points[v]; }
                    Foam::Info<< Foam::nl;
                }
                for (label f : cellFaces[c])
                {
                    Foam::Info<< "    face " << f << (faceSplit[f] ? " SPLIT" : "") << " side " << label(faceSide[f]) << ":";
                    for (label v : m.faces[f]) { Foam::Info<< " " << label(sdE(v)) << "#" << v << "@" << m.points[v]; }
                    Foam::Info<< Foam::nl;
                }
                Foam::Info().precision(oldPrec);
            }
        }
        for (auto& loop : loops)
        {
            vector nn; point cc;
            polygonNormalCentre(m.points, loop, nn, cc);
            // A baffle cap separates side A (the owner, +planeNormal) from side
            // B (the neighbour): its normal must point owner -> neighbour, i.e.
            // along -planeNormal. The cell-centre heuristic gets that wrong for
            // a cell another sheet already split — the cap came out reversed:
            // a dark back-face in the preview and a wrong flux sign in the
            // solve. The plane fixes the direction; the heuristic stays for
            // solid carve passes, which have no sheet plane.
            const vector dir = args.havePlane
                ? -args.planeNormal
                : (nB ? (cBc - cA) : (cc - cA));
            if ((nn & dir) < 0) { std::reverse(loop.begin(), loop.end()); }
            std::vector<std::pair<std::vector<label>, label>> pieces;
            std::vector<chordInsert> inserts;
            if
            (
                args.splitCap
             && (*args.splitCap)(c, loop, cA, pieces, inserts) && !pieces.empty()
            )
            {
                // a piece corner placed on a feature edge where it pierces a
                // hex face goes into that face's polygon too (between the
                // chord's ends), so the side faces stay conformal with the cap
                for (const chordInsert& ci : inserts)
                {
                    auto splice = [&](label f)
                    {
                        std::vector<label>& poly = m.faces[f];
                        const label k = label(poly.size());
                        label ia = -1, ib = -1;
                        for (label i = 0; i < k; ++i)
                        {
                            if (poly[i] == ci.a) { ia = i; }
                            if (poly[i] == ci.b) { ib = i; }
                        }
                        if (ia < 0 || ib < 0) { return; }
                        // the run from a to b (the shorter way round) may
                        // already hold points spliced by earlier cuts — all
                        // of them on the chord's line; x goes in by its
                        // distance from a
                        const label fwd = ((ib - ia) % k + k) % k;
                        const label bwd = ((ia - ib) % k + k) % k;
                        const int dir = (fwd <= bwd) ? 1 : -1;
                        const label steps = (dir > 0) ? fwd : bwd;
                        if (steps == 0) { return; }
                        const point& pa = m.points[ci.a];
                        const point& pb = m.points[ci.b];
                        const vector ab = pb - pa;
                        const scalar l2 = magSqr(ab);
                        for (label p = 1; p < steps; ++p)
                        {
                            const label idx = ((ia + dir * p) % k + k) % k;
                            const vector d = m.points[poly[idx]] - pa;
                            const scalar t = (l2 > 0) ? (d & ab) / l2 : 0;
                            if (magSqr(d - t * ab) > 1e-14) { return; }   // a real corner in between
                        }
                        const scalar dx = magSqr(m.points[ci.x] - pa);
                        label pos = 1;
                        for (; pos < steps; ++pos)
                        {
                            const label idx = ((ia + dir * pos) % k + k) % k;
                            if (magSqr(m.points[poly[idx]] - pa) > dx) { break; }
                        }
                        const label at = ((ia + dir * pos) % k + k) % k;
                        if (dir > 0) { poly.insert(poly.begin() + at, ci.x); }
                        else { poly.insert(poly.begin() + at + 1, ci.x); }
                        return;
                    };
                    for (label f : cellFaces[c])
                    {
                        splice(f);
                        if (faceB[f] >= 0) { splice(faceB[f]); }
                    }
                }
                // boundary caps: one patch per piece; internal caps: the
                // piece's zone (-1 for the phantom part of a sheet's plane)
                for (auto& pc : pieces)
                {
                    m.faces.push_back(pc.first);
                    m.owner.push_back(c);
                    m.neighbour.push_back(args.capBoundary ? -1 : cB);
                    m.patch.push_back(args.capBoundary ? pc.second : -1);
                    m.zone.push_back(args.capBoundary ? -1 : pc.second);
                    m.faceAlive.push_back(true);
                    if (counted(c)) { ++nCaps; }
                }
                continue;
            }
            m.faces.push_back(loop);
            m.owner.push_back(c);
            m.neighbour.push_back(args.capBoundary ? -1 : cB);
            m.patch.push_back(args.capBoundary ? regionOfFace(loop) : -1);
            m.zone.push_back(args.capBoundary ? -1 : args.capZone);
            m.faceAlive.push_back(true);
            if (counted(c)) { ++nCaps; }
        }
    }

    // ---- 3. drop the solid side ------------------------------------------
    if (args.capBoundary)
    {
        // B halves die; so do unsplit cells with only B corners
        for (label c = 0; c < nCells0; ++c)
        {
            if (!m.cellAlive[c] || halfB[c] >= 0) { continue; }
            bool anyA = false, anyB = false;
            for (label f : cellFaces[c])
            {
                for (label v : m.faces[f])
                {
                    if (sdE(v) > 0) { anyA = true; }
                    else if (sdE(v) == -1) { anyB = true; }
                }
            }
            if (anyB && !anyA) { m.cellAlive[c] = false; }
        }
        for (label c = 0; c < nCells0; ++c)
        {
            if (halfB[c] >= 0) { m.cellAlive[halfB[c]] = false; }
        }
        dropDeadFaces(m, regionOfFace);
    }
}

} // end anonymous namespace


// * * * * * * * * * * * * * * * * cutCarving  * * * * * * * * * * * * * * * //

namespace
{

//- Part of the line p0 + t d inside triangle (a, b, c) (the line lies in
//  the triangle's plane): the parameter interval, empty when none.
bool lineInTriangle
(
    const Foam::point& p0, const Foam::vector& d,
    const Foam::point& a, const Foam::point& b, const Foam::point& c,
    const Foam::vector& n,
    Foam::scalar& t0, Foam::scalar& t1
)
{
    using namespace Foam;
    t0 = -GREAT; t1 = GREAT;
    const point* v[3] = {&a, &b, &c};
    for (int k = 0; k < 3; ++k)
    {
        const point& e0 = *v[k]; const point& e1 = *v[(k + 1) % 3];
        // inward edge normal in the plane
        vector en = n ^ (e1 - e0);
        const point& opp = *v[(k + 2) % 3];
        if (((opp - e0) & en) < 0) { en = -en; }
        const scalar f0 = (p0 - e0) & en;   // >= 0 inside
        const scalar fd = d & en;
        if (std::abs(fd) < 1e-14)
        {
            // A feature line lies exactly on both triangles' shared edge.
            // Plane-intersection roundoff can put it a few ulps outside;
            // rejecting that line skips the pre-split at rotated box edges.
            if (f0 < -1e-10 * mag(en)) { return false; }
            continue;
        }
        const scalar t = -f0 / fd;
        if (fd > 0) { t0 = std::max(t0, t); } else { t1 = std::min(t1, t); }
    }
    return t1 - t0 > 1e-7;
}

} // end anonymous namespace


void Foam::dcCartCutCells::featurePreSplit
(
    cutMesh& m,
    const dcCartSurfaces& surfaces,
    label sh,
    const settings& s,
    report& r,
    std::vector<bool>& everSplit,
    bool completePlanes
)
{
    // the shell's feature segments: pairs of triangles > 20° apart whose
    // planes' intersection line runs inside both
    std::vector<dcCartSurfaces::triInfo> tris;
    surfaces.solidShellTrianglesInBox(sh, surfaces.solidShellBounds(sh), tris);
    // a finely tessellated shell (a pipe) has its facets a few degrees
    // apart and its few real edges (end caps, reducer rim) made of many
    // short segments; splitting cells by all of them makes slivers, so
    // such shells keep the single folded cap
    if (tris.size() < 2 || tris.size() > 500) { return; }
    struct featSeg { point a, b; vector n; point o; bool curved; };   // segment + split plane
    std::vector<featSeg> segs;
    std::vector<boundBox> tb(tris.size(), boundBox(boundBox::invertedBox));
    for (size_t i = 0; i < tris.size(); ++i)
    {
        tb[i].add(tris[i].p0); tb[i].add(tris[i].p1); tb[i].add(tris[i].p2);
        tb[i].inflate(1e-6);
    }
    // A facet of a CURVED surface (a neighbour turned by 2-20 degrees: the
    // 16 facets of a wall unit's quarter-round front) does not pre-split:
    // its plane would slice 0.8 mm slivers off the cells between it and the
    // next facet, and the segments between those points run along the
    // surface where no crossing can be found. Only the flat facets (a box
    // face, the unit's side) draw their plane through the cells.
    std::vector<bool> curved(tris.size(), false);
    for (size_t i = 0; i < tris.size(); ++i)
    {
        for (size_t j = i + 1; j < tris.size(); ++j)
        {
            if (!tb[i].overlaps(tb[j])) { continue; }
            const scalar c = std::abs(tris[i].normal & tris[j].normal);
            if (c > 0.94 && c < 0.9994) { curved[i] = true; curved[j] = true; }
        }
    }
    for (size_t i = 0; i < tris.size(); ++i)
    {
        for (size_t j = i + 1; j < tris.size(); ++j)
        {
            const vector& n1 = tris[i].normal; const vector& n2 = tris[j].normal;
            if (std::abs(n1 & n2) > 0.94) { continue; }
            if (!tb[i].overlaps(tb[j])) { continue; }
            const vector d = n1 ^ n2;
            const scalar dm = mag(d);
            if (dm < 1e-9) { continue; }
            const vector du = d / dm;
            const scalar n12 = n1 & n2;
            const scalar det = 1 - n12 * n12;
            const point ref = tris[i].p0;
            const scalar h1 = 0;
            const scalar h2 = (tris[j].p0 - ref) & n2;
            const point p0 = ref + ((h1 - h2 * n12) / det) * n1 + ((h2 - h1 * n12) / det) * n2;
            scalar a0, a1, b0, b1;
            if (!lineInTriangle(p0, du, tris[i].p0, tris[i].p1, tris[i].p2, n1, a0, a1)) { continue; }
            if (!lineInTriangle(p0, du, tris[j].p0, tris[j].p1, tris[j].p2, n2, b0, b1)) { continue; }
            const scalar t0 = std::max(a0, b0), t1 = std::min(a1, b1);
            if (t1 - t0 < 1e-6) { continue; }
            // both facet planes split: one of them may coincide with the
            // cell's own face (an edge lying on a grid plane), and then only
            // the other one separates the two facets
            // A facet of a CURVED surface (a neighbour turned by 2-20
            // degrees: the 16 facets of a wall unit's quarter-round front)
            // splits too, but a cell takes at most ONE curved plane: one
            // per facet would slice 0.8 mm slivers between neighbouring
            // facet planes. With one, the half beyond the flat plane gets
            // its curved side as a planar cap of its own (the sagitta of a
            // 5-degree facet over a cell is 0.1 mm) instead of a cap folded
            // over the flat plane it shares with the fluid half.
            // (one curved plane per cell was tried: 29 face-pyramid
            // errors and folded caps elsewhere; the curved facets are left
            // to the carve)
            if (!curved[i]) { segs.push_back({p0 + t0 * du, p0 + t1 * du, n1, p0, false}); }
            if (!curved[j]) { segs.push_back({p0 + t0 * du, p0 + t1 * du, n2, p0, false}); }
        }
    }
    if (completePlanes)
    {
        // A rectangular server needs every facet plane, including corners
        // whose adjoining side is represented by the rack frame's shell.
        for (const auto& tri : tris)
        {
            segs.push_back({tri.p0, tri.p1, tri.normal, tri.p0, false});
        }
    }
    if (segs.empty()) { return; }

    // one pass per distinct plane; cells overlapping any of its segments
    std::map<std::tuple<int, long, long, long, long>, std::vector<size_t>> byPlane;
    for (size_t k = 0; k < segs.size(); ++k)
    {
        vector n = segs[k].n;
        label big = 0;
        for (direction dd = 1; dd < 3; ++dd) { if (std::abs(n[dd]) > std::abs(n[big])) { big = dd; } }
        if (n[big] < 0) { n = -n; }
        const scalar off = n & segs[k].o;
        byPlane[std::make_tuple(segs[k].curved ? 1 : 0, (long)std::lround(n.x() * 500), (long)std::lround(n.y() * 500),
                                (long)std::lround(n.z() * 500), (long)std::lround(off * 1e4))].push_back(k);
    }
    // cells (and the halves they make) already split by a curved plane
    std::vector<bool> curvedDone(m.nCells, false);
    // only the finest cells are split: a feature edge crossing a coarse
    // cell would have its two planes run the whole coarse cell through,
    // leaving rods of fluid between plane extensions that no neighbour can
    // absorb convexly (the feature refinement is what puts finest cells on
    // the edges in the first place)
    label maxLevel = 0;
    for (label c = 0; c < m.nCells; ++c) { if (m.cellAlive[c]) { maxLevel = std::max(maxLevel, m.cellLevel[c]); } }
    label nSplitCells = 0;
    // Cell boxes once per shell; a half made by a split inherits its
    // parent's box (conservative). Building cellFaces per plane was the
    // whole cost of this pass.
    std::vector<boundBox> cellBB(m.nCells, boundBox(boundBox::invertedBox));
    {
        std::vector<std::vector<label>> cellFaces;
        buildCellFaces(m, cellFaces);
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!m.cellAlive[c]) { continue; }
            for (label f : cellFaces[c]) { for (label v : m.faces[f]) { cellBB[c].add(m.points[v]); } }
        }
    }
    (void)maxLevel;
    for (const auto& kv : byPlane)
    {
        const featSeg& s0 = segs[kv.second[0]];
        const vector pn = s0.n; const point po = s0.o;
        const bool curvedPlane = s0.curved;
        const label nCellsBefore = m.nCells;
        std::vector<bool> eligible(m.nCells, false);
        label nEligible = 0;
        std::vector<boundBox> sbb;
        for (size_t k : kv.second)
        {
            boundBox bb(segs[k].a, segs[k].a); bb.add(segs[k].b); bb.inflate(1e-6);
            sbb.push_back(bb);
        }
        curvedDone.resize(m.nCells, false);
        for (label c = 0; c < m.nCells; ++c)
        {
            if (curvedPlane && curvedDone[c]) { continue; }
            // (every level: an electric box refined one level short of the
            // racks still wants its edges; the rods the plane extensions
            // once left in coarse cells came from slivers chaining in the
            // merge, which no longer happens)
            if (!m.cellAlive[c] || !cellBB[c].valid()) { continue; }
            bool hit = completePlanes && cellBB[c].overlaps(surfaces.solidShellBounds(sh));
            if (!completePlanes)
            {
                for (const boundBox& b : sbb) { if (cellBB[c].overlaps(b)) { hit = true; break; } }
            }
            if (!hit) { continue; }
            eligible[c] = true; ++nEligible;
        }
        if (!nEligible) { continue; }
        // the plane side of EVERY point (cheap, and no "near" bookkeeping
        // that a face of an eligible cell could fall outside of)
        std::vector<signed char> side(m.points.size(), 1);
        for (label p = 0; p < label(m.points.size()); ++p)
        {
            const scalar d = (m.points[p] - po) & pn;
            side[p] = (std::abs(d) <= s.onToleranceCarve) ? 0 : (d < 0 ? -1 : 1);
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
        args.capZone = -2;   // a phantom face: mergeSmallCells joins the halves again where it can
        args.eligible = &eligible;
        args.splitOut = &splitOut;
        args.onIsA = false;
        args.countRank = s.myRank;
        label nCut = 0, nFallback = 0, nCaps = 0;
        clipPass(m, side, crossing, existing, regionOfFace, args, nCut, nFallback, nCaps);
        nSplitCells += nCut;
        // the halves keep their parent's box
        cellBB.resize(m.nCells, boundBox(boundBox::invertedBox));
        for (label c = 0; c < nCellsBefore && c < label(splitOut.size()); ++c)
        {
            if (!splitOut[c]) { continue; }
            // clipPass appends the B half in split order; find it by ref centre
            // is ambiguous, so rebuild the new halves' boxes from their faces
        }
        if (m.nCells > nCellsBefore)
        {
            std::vector<std::vector<label>> cf;
            buildCellFaces(m, cf);
            for (label c = nCellsBefore; c < m.nCells; ++c)
            {
                for (label f : cf[c]) { for (label v : m.faces[f]) { cellBB[c].add(m.points[v]); } }
            }
        }
        // a pre-split cell counts as handled by the geometry: a re-admitted
        // (thin-plate) cell's fluid half must not be dropped again just
        // because the carving found nothing left to cut in it
        for (label c = 0; c < label(splitOut.size()) && c < label(everSplit.size()); ++c)
        {
            if (splitOut[c]) { everSplit[c] = true; }
        }
        if (curvedPlane)
        {
            curvedDone.resize(m.nCells, false);
            for (label c = 0; c < nCellsBefore && c < label(splitOut.size()); ++c)
            {
                if (splitOut[c]) { curvedDone[c] = true; }
            }
            for (label c = nCellsBefore; c < m.nCells; ++c) { curvedDone[c] = true; }
        }
    }
    r.nPreSplit += nSplitCells;
    if (getenv("DCCART_DEBUG"))
    {
        Info<< "  shell " << sh << " feature segments " << label(segs.size())
            << " planes " << label(byPlane.size()) << " pre-split cells " << nSplitCells << nl;
    }
}


namespace
{

//- The cells, faces and points within reach of a box, taken out of the whole
//  mesh as a small cutMesh and written back after a pass on it. A pass over
//  the whole mesh costs (passes x mesh): every array it sizes to the mesh and
//  every sweep over it. Here cells and points sit in buckets of the largest
//  cell's size; a part holds the cells reachOfBox finds for a box
//  (complete: all their faces), the cells across those faces (placeholders:
//  in the part so the faces have both sides, never looked at by a pass that
//  stays within that reach), the faces' points and optionally every point
//  inside a second box — all in ascending id order, so a pass meets them in
//  the order the whole-mesh pass did, and appends what it makes in the order
//  the whole-mesh pass made it: written back, the ids come out the same.
class cutNeighbourhood
{
    typedef Foam::dcCartCutCells::cutMesh mesh;

    mesh& m_;
    std::vector<std::vector<label>> cellFaces_;
    std::vector<Foam::boundBox> cellBB_;
    Foam::scalar maxEdge_ = 0;
    Foam::boundBox dom_;
    Foam::scalar bucketSize_ = 1;
    Foam::labelVector nb_;
    std::vector<std::vector<label>> cellBuckets_, pointBuckets_;
    std::vector<int> cStamp_, fStamp_, pStamp_;
    std::vector<label> cLoc_, pLoc_;
    int epoch_ = 0;

    label bIdx(Foam::scalar x, Foam::direction d) const
    {
        const label i = label(std::floor((x - dom_.min()[d]) / bucketSize_));
        return std::min(std::max(i, label(0)), nb_[d] - 1);
    }

    template<class Fn>
    void forBuckets(const Foam::boundBox& bb, std::vector<std::vector<label>>& buckets, Fn&& fn)
    {
        const label i0 = bIdx(bb.min().x(), 0), i1 = bIdx(bb.max().x(), 0);
        const label j0 = bIdx(bb.min().y(), 1), j1 = bIdx(bb.max().y(), 1);
        const label k0 = bIdx(bb.min().z(), 2), k1 = bIdx(bb.max().z(), 2);
        for (label k = k0; k <= k1; ++k)
        {
            for (label j = j0; j <= j1; ++j)
            {
                for (label i = i0; i <= i1; ++i)
                {
                    fn(buckets[size_t(i) + size_t(nb_[0]) * (size_t(j) + size_t(nb_[1]) * size_t(k))]);
                }
            }
        }
    }

    void addCell(label c)
    {
        if (!cellBB_[c].valid()) { return; }
        forBuckets(cellBB_[c], cellBuckets_, [&](std::vector<label>& b) { b.push_back(c); });
    }

    void addPoint(label p)
    {
        const Foam::point& x = m_.points[p];
        pointBuckets_[size_t(bIdx(x.x(), 0)) + size_t(nb_[0]) * (size_t(bIdx(x.y(), 1)) + size_t(nb_[1]) * size_t(bIdx(x.z(), 2)))].push_back(p);
    }

public:

    struct part
    {
        mesh lm;
        //- global ids, ascending: cells (complete and placeholders), faces, points
        std::vector<label> cells, faces, points;
        label nC = 0, nF = 0, nP = 0;
        int completeEpoch = 0, faceEpoch = 0;
        //- where writeBack put the items the pass made
        label newPointBase = 0, newFaceBase = 0, newCellBase = 0;
    };

    explicit cutNeighbourhood(mesh& m)
    :
        m_(m),
        dom_(Foam::boundBox::invertedBox),
        nb_(1, 1, 1)
    {
        using namespace Foam;
        buildCellFaces(m_, cellFaces_);
        cellBB_.assign(m_.nCells, boundBox(boundBox::invertedBox));
        for (label c = 0; c < m_.nCells; ++c)
        {
            if (!m_.cellAlive[c]) { continue; }
            for (label f : cellFaces_[c]) { for (label v : m_.faces[f]) { cellBB_[c].add(m_.points[v]); } }
            if (cellBB_[c].valid()) { maxEdge_ = max(maxEdge_, cmptMax(cellBB_[c].span())); }
        }
        for (const point& p : m_.points) { dom_.add(p); }
        if (!dom_.valid()) { dom_ = boundBox(point::zero, point::one); }
        bucketSize_ = max(max(maxEdge_, std::cbrt(max(dom_.volume(), VSMALL) / 1e6)), scalar(1e-6));
        for (direction d = 0; d < 3; ++d)
        {
            nb_[d] = max(label(1), label(std::ceil(dom_.span()[d] / bucketSize_)) + 1);
        }
        cellBuckets_.resize(size_t(nb_[0]) * nb_[1] * nb_[2]);
        pointBuckets_.resize(cellBuckets_.size());
        for (label c = 0; c < m_.nCells; ++c) { if (m_.cellAlive[c]) { addCell(c); } }
        for (label p = 0; p < label(m_.points.size()); ++p) { addPoint(p); }
        cStamp_.assign(m_.nCells, 0);
        fStamp_.assign(m_.faces.size(), 0);
        pStamp_.assign(m_.points.size(), 0);
        cLoc_.assign(m_.nCells, -1);
        pLoc_.assign(m_.points.size(), -1);
    }

    const std::vector<label>& facesOf(label c) const { return cellFaces_[c]; }

    //- Live cells whose box overlaps bb. Intermediate reach sets need not
    //  be sorted: their union is sorted once after both topology rings.
    void cellsInBox(const Foam::boundBox& bb, std::vector<label>& out, bool sorted = true)
    {
        out.clear();
        const int e = ++epoch_;
        forBuckets(bb, cellBuckets_, [&](std::vector<label>& b)
        {
            for (label c : b)
            {
                if (cStamp_[c] == e) { continue; }
                cStamp_[c] = e;
                if (m_.cellAlive[c] && cellBB_[c].overlaps(bb)) { out.push_back(c); }
            }
        });
        if (sorted) { std::sort(out.begin(), out.end()); }
    }

    bool faceIn(const part& P, label f) const
    {
        return f < label(fStamp_.size()) && fStamp_[f] == P.faceEpoch;
    }

    //- The cells a pass on box sbb can look at, ascending: the live cells
    //  overlapping sbb, every live cell overlapping their joint box (which
    //  holds each cell sharing a vertex with one of them), and two face
    //  rings of live cells around those — the anchors a point borrows sit
    //  two face rings out of the cells around it. Cut by topology, not by a
    //  distance: a margin of cell sizes had to allow for the coarsest cell
    //  the rings might run into and took in ~25 times the cells.
    void reachOfBox(const Foam::boundBox& sbb, std::vector<label>& out)
    {
        using namespace Foam;
        out.clear();
        std::vector<label> e0;
        cellsInBox(sbb, e0, false);
        if (e0.empty()) { return; }
        boundBox eb(boundBox::invertedBox);
        for (label c : e0) { eb.add(cellBB_[c]); }
        cellsInBox(eb, out, false);
        const int e = ++epoch_;
        for (label c : out) { cStamp_[c] = e; }
        std::vector<label> frontier(out), next;
        for (int ring = 0; ring < 2; ++ring)
        {
            next.clear();
            for (label c : frontier)
            {
                for (label f : cellFaces_[c])
                {
                    if (!m_.faceAlive[f]) { continue; }
                    for (label o : {m_.owner[f], m_.neighbour[f]})
                    {
                        if (o >= 0 && cStamp_[o] != e && m_.cellAlive[o]) { cStamp_[o] = e; next.push_back(o); }
                    }
                }
            }
            out.insert(out.end(), next.begin(), next.end());
            frontier.swap(next);
        }
        std::sort(out.begin(), out.end());
    }

    //- `complete`: live cells, ascending
    void extractCells(const std::vector<label>& complete, const Foam::boundBox* pointBox, part& P)
    {
        using namespace Foam;
        P = part();
        P.completeEpoch = ++epoch_;
        for (label c : complete) { cStamp_[c] = P.completeEpoch; }
        P.faceEpoch = ++epoch_;
        for (label c : complete)
        {
            for (label f : cellFaces_[c])
            {
                if (!m_.faceAlive[f] || fStamp_[f] == P.faceEpoch) { continue; }
                fStamp_[f] = P.faceEpoch;
                P.faces.push_back(f);
            }
        }
        std::sort(P.faces.begin(), P.faces.end());
        P.cells = complete;
        for (label f : P.faces)
        {
            for (label c : {m_.owner[f], m_.neighbour[f]})
            {
                if (c >= 0 && cStamp_[c] != P.completeEpoch) { P.cells.push_back(c); }
            }
        }
        std::sort(P.cells.begin(), P.cells.end());
        P.cells.erase(std::unique(P.cells.begin(), P.cells.end()), P.cells.end());
        const int pe = ++epoch_;
        for (label f : P.faces)
        {
            for (label v : m_.faces[f]) { if (pStamp_[v] != pe) { pStamp_[v] = pe; P.points.push_back(v); } }
        }
        if (pointBox)
        {
            const boundBox& pb = *pointBox;
            forBuckets(pb, pointBuckets_, [&](std::vector<label>& b)
            {
                for (label p : b) { if (pStamp_[p] != pe && pb.contains(m_.points[p])) { pStamp_[p] = pe; P.points.push_back(p); } }
            });
        }
        std::sort(P.points.begin(), P.points.end());
        P.nC = label(P.cells.size()); P.nF = label(P.faces.size()); P.nP = label(P.points.size());
        P.lm.nGridPoints = label(std::lower_bound(P.points.begin(), P.points.end(), m_.nGridPoints) - P.points.begin());
        for (label i = 0; i < P.nC; ++i) { cLoc_[P.cells[i]] = i; }
        for (label i = 0; i < P.nP; ++i) { pLoc_[P.points[i]] = i; }

        mesh& lm = P.lm;
        lm.points.resize(P.nP);
        for (label i = 0; i < P.nP; ++i) { lm.points[i] = m_.points[P.points[i]]; }
        lm.faces.resize(P.nF); lm.owner.resize(P.nF); lm.neighbour.resize(P.nF);
        lm.patch.resize(P.nF); lm.zone.resize(P.nF); lm.faceAlive.resize(P.nF);
        for (label i = 0; i < P.nF; ++i)
        {
            const label f = P.faces[i];
            std::vector<label>& poly = lm.faces[i];
            poly.resize(m_.faces[f].size());
            for (size_t k = 0; k < poly.size(); ++k) { poly[k] = pLoc_[m_.faces[f][k]]; }
            lm.owner[i] = (m_.owner[f] >= 0) ? cLoc_[m_.owner[f]] : -1;
            lm.neighbour[i] = (m_.neighbour[f] >= 0) ? cLoc_[m_.neighbour[f]] : -1;
            lm.patch[i] = m_.patch[f]; lm.zone[i] = m_.zone[f]; lm.faceAlive[i] = m_.faceAlive[f];
        }
        lm.nCells = P.nC;
        lm.cellAlive.resize(P.nC); lm.cellOrigAlive.resize(P.nC); lm.cellRefVol.resize(P.nC);
        lm.cellRefCentre.resize(P.nC); lm.cellLevel.resize(P.nC); lm.cellRank.resize(P.nC);
        lm.cellLeaf.resize(P.nC); lm.cellPiece.resize(P.nC);
        for (label i = 0; i < P.nC; ++i)
        {
            const label c = P.cells[i];
            lm.cellAlive[i] = m_.cellAlive[c]; lm.cellOrigAlive[i] = m_.cellOrigAlive[c];
            lm.cellRefVol[i] = m_.cellRefVol[c]; lm.cellRefCentre[i] = m_.cellRefCentre[c];
            lm.cellLevel[i] = m_.cellLevel[c];
            lm.cellRank[i] = (c < label(m_.cellRank.size())) ? m_.cellRank[c] : 0;
            lm.cellLeaf[i] = (c < label(m_.cellLeaf.size())) ? m_.cellLeaf[c] : 0;
            lm.cellPiece[i] = (c < label(m_.cellPiece.size())) ? m_.cellPiece[c] : 1;
        }
    }

    //- The pass's result into the whole mesh; `touched` (if given) receives
    //  every face id the part held or made
    void writeBack(part& P, std::vector<label>* touched)
    {
        using namespace Foam;
        mesh& lm = P.lm;
        P.newPointBase = label(m_.points.size());
        P.newFaceBase = label(m_.faces.size());
        P.newCellBase = m_.nCells;
        auto gP = [&](label l) -> label { return (l < P.nP) ? P.points[l] : P.newPointBase + (l - P.nP); };
        auto gC = [&](label l) -> label { return (l < 0) ? l : (l < P.nC) ? P.cells[l] : P.newCellBase + (l - P.nC); };
        for (label l = P.nP; l < label(lm.points.size()); ++l) { m_.points.push_back(lm.points[l]); }
        for (label l = 0; l < label(lm.faces.size()); ++l)
        {
            std::vector<label> poly(lm.faces[l].size());
            for (size_t k = 0; k < poly.size(); ++k) { poly[k] = gP(lm.faces[l][k]); }
            if (touched) { touched->push_back((l < P.nF) ? P.faces[l] : P.newFaceBase + (l - P.nF)); }
            if (l < P.nF)
            {
                const label g = P.faces[l];
                m_.faces[g].swap(poly);
                m_.owner[g] = gC(lm.owner[l]); m_.neighbour[g] = gC(lm.neighbour[l]);
                m_.patch[g] = lm.patch[l]; m_.zone[g] = lm.zone[l]; m_.faceAlive[g] = lm.faceAlive[l];
            }
            else
            {
                m_.faces.push_back(std::move(poly));
                m_.owner.push_back(gC(lm.owner[l])); m_.neighbour.push_back(gC(lm.neighbour[l]));
                m_.patch.push_back(lm.patch[l]); m_.zone.push_back(lm.zone[l]); m_.faceAlive.push_back(lm.faceAlive[l]);
            }
        }
        for (label l = 0; l < lm.nCells; ++l)
        {
            if (l < P.nC)
            {
                const label c = P.cells[l];
                m_.cellAlive[c] = lm.cellAlive[l]; m_.cellOrigAlive[c] = lm.cellOrigAlive[l];
                if (c < label(m_.cellPiece.size())) { m_.cellPiece[c] = lm.cellPiece[l]; }
            }
            else
            {
                ++m_.nCells;
                m_.cellAlive.push_back(lm.cellAlive[l]); m_.cellOrigAlive.push_back(lm.cellOrigAlive[l]);
                m_.cellRefVol.push_back(lm.cellRefVol[l]); m_.cellRefCentre.push_back(lm.cellRefCentre[l]);
                m_.cellLevel.push_back(lm.cellLevel[l]); m_.cellRank.push_back(lm.cellRank[l]);
                m_.cellLeaf.push_back(lm.cellLeaf[l]); m_.cellPiece.push_back(lm.cellPiece[l]);
            }
        }
        // the complete cells and the new ones take their face lists from the
        // pass (a placeholder's faces were not all in it, and none of them
        // changed); new cells and points go into the buckets
        std::vector<std::vector<label>> lcf;
        buildCellFaces(lm, lcf);
        cellFaces_.resize(m_.nCells);
        cellBB_.resize(m_.nCells, boundBox(boundBox::invertedBox));
        cStamp_.resize(m_.nCells, 0); cLoc_.resize(m_.nCells, -1);
        fStamp_.resize(m_.faces.size(), 0);
        pStamp_.resize(m_.points.size(), 0); pLoc_.resize(m_.points.size(), -1);
        for (label l = 0; l < lm.nCells; ++l)
        {
            if (l < P.nC && cStamp_[P.cells[l]] != P.completeEpoch) { continue; }
            const label g = gC(l);
            std::vector<label>& lst = cellFaces_[g];
            lst.clear();
            for (label f : lcf[l]) { lst.push_back((f < P.nF) ? P.faces[f] : P.newFaceBase + (f - P.nF)); }
            if (l >= P.nC)
            {
                for (label f : lst) { for (label v : m_.faces[f]) { cellBB_[g].add(m_.points[v]); } }
                addCell(g);
            }
        }
        for (label p = P.newPointBase; p < label(m_.points.size()); ++p) { addPoint(p); }
    }

    //- A face appended to the whole mesh outside a pass
    void faceAppended(label f)
    {
        if (m_.owner[f] >= 0) { cellFaces_[m_.owner[f]].push_back(f); }
        if (m_.neighbour[f] >= 0) { cellFaces_[m_.neighbour[f]].push_back(f); }
        fStamp_.resize(m_.faces.size(), 0);
    }
};

} // End anonymous namespace


void Foam::dcCartCutCells::syncSideOwners
(
    cutMesh& m,
    std::vector<signed char>& side
)
{
    if (!Pstream::parRun() || Pstream::nProcs() <= 1) { return; }
    {
        const label me = Pstream::myProcNo();
        const label nPr = Pstream::nProcs();
        // lowest owning rank per point: the same on every rank that holds
        // the point (cells and their cellRank are global data), so sender
        // and receiver agree on who decides without exchanging that too
        std::vector<label> minRank(m.points.size(), labelMax);
        for (label f = 0; f < label(m.faces.size()); ++f)
        {
            if (!m.faceAlive[f]) { continue; }
            for (label sd = 0; sd < 2; ++sd)
            {
                const label c = sd ? m.neighbour[f] : m.owner[f];
                if (c < 0 || c >= m.nCells || !m.cellAlive[c]) { continue; }
                const label cr =
                    (c < label(m.cellRank.size())) ? m.cellRank[c] : 0;
                if (cr < 0 || cr >= 63) { continue; }
                for (const label v : m.faces[f])
                {
                    if (v >= label(minRank.size())) { continue; }
                    if (cr < minRank[v]) { minRank[v] = cr; }
                }
            }
        }
        // quantised position key (0.1 um): coordinates of a shared point are
        // bit-identical across ranks (grid corners, or crossings computed
        // from them by the same arithmetic), and domains stay far below the
        // label range at this scale
        auto keyOf = [&](label v, label& kx, label& ky, label& kz)
        {
            const point& x = m.points[v];
            kx = label(std::llround(x.x() * 1e7));
            ky = label(std::llround(x.y() * 1e7));
            kz = label(std::llround(x.z() * 1e7));
        };
        // The band to send: the neighbour's halo reaches haloLayers leaf
        // layers into this rank's side of the slab, so walk that far (in
        // LEAF steps -- split pieces of one leaf cost no depth) from every
        // cell that touches the slab interface and collect the points of
        // the cells walked through. A point whose owner is this rank but
        // which lies a few layers inside it is exactly what the neighbour
        // holds and cannot classify identically (its own anchors there are
        // truncated), so those are the values that must travel.
        std::vector<std::vector<label>> cfS;
        buildCellFaces(m, cfS);
        auto sendBand = [&](label nbrRank) -> List<label>
        {
            DynamicList<label> out;
            if (nbrRank < 0 || nbrRank >= nPr || nbrRank == me) { return List<label>(); }
            const label depthLimit = 6;   // haloLayers (4) + margin
            std::vector<label> depth(m.nCells, -1);
            std::vector<label> frontier;
            for (label c = 0; c < m.nCells; ++c)
            {
                if (!m.cellAlive[c]) { continue; }
                const label cr = (c < label(m.cellRank.size())) ? m.cellRank[c] : 0;
                if (cr != me) { continue; }
                bool touches = false;
                for (const label f : cfS[c])
                {
                    if (!m.faceAlive[f]) { continue; }
                    const label o = m.owner[f], nb = m.neighbour[f];
                    for (label sd = 0; sd < 2; ++sd)
                    {
                        const label x = sd ? nb : o;
                        if (x < 0 || x >= m.nCells || !m.cellAlive[x]) { continue; }
                        const label xr = (x < label(m.cellRank.size())) ? m.cellRank[x] : 0;
                        if (xr == nbrRank) { touches = true; break; }
                    }
                    if (touches) { break; }
                }
                if (touches) { depth[c] = 0; frontier.push_back(c); }
            }
            for (label d = 0; d < depthLimit && !frontier.empty(); ++d)
            {
                std::vector<label> next;
                for (const label c : frontier)
                {
                    for (const label f : cfS[c])
                    {
                        if (!m.faceAlive[f]) { continue; }
                        const label o = m.owner[f], nb = m.neighbour[f];
                        for (label sd = 0; sd < 2; ++sd)
                        {
                            const label x = sd ? nb : o;
                            if (x < 0 || x >= m.nCells || !m.cellAlive[x]) { continue; }
                            const label xr = (x < label(m.cellRank.size())) ? m.cellRank[x] : 0;
                            if (xr != me) { continue; }
                            const label step =
                                (m.cellLeaf[x] != m.cellLeaf[c]) ? 1 : 0;
                            const label nd = depth[c] + step;
                            if (nd > depthLimit) { continue; }
                            if (depth[x] >= 0 && depth[x] <= nd) { continue; }
                            depth[x] = nd;
                            next.push_back(x);
                        }
                    }
                }
                frontier.swap(next);
            }
            std::vector<char> sent(m.points.size(), 0);
            for (label c = 0; c < m.nCells; ++c)
            {
                if (depth[c] < 0) { continue; }
                for (const label f : cfS[c])
                {
                    if (!m.faceAlive[f]) { continue; }
                    for (const label v : m.faces[f])
                    {
                        if (v >= label(minRank.size()) || sent[v]) { continue; }
                        sent[v] = 1;
                        if (minRank[v] != me) { continue; }
                        label kx, ky, kz;
                        keyOf(v, kx, ky, kz);
                        out.push_back(kx); out.push_back(ky); out.push_back(kz);
                        out.push_back(label(side[v]));
                    }
                }
            }
            return List<label>(std::move(out));
        };
        const List<label> sendW = sendBand(me - 1);
        const List<label> sendE = sendBand(me + 1);
        List<label> recvW, recvE;
        {
            PstreamBuffers pBufs(Pstream::commsTypes::nonBlocking);
            if (me > 0) { UOPstream toW(me - 1, pBufs); toW << sendW; }
            if (me + 1 < nPr) { UOPstream toE(me + 1, pBufs); toE << sendE; }
            pBufs.finishedSends();
            if (me + 1 < nPr) { UIPstream fromE(me + 1, pBufs); fromE >> recvE; }
            if (me > 0) { UIPstream fromW(me - 1, pBufs); fromW >> recvW; }
        }
        auto applyOverride = [&](const List<label>& flat, label ownerRank)
        {
            if (flat.empty() || ownerRank < 0) { return; }
            std::unordered_map<uint64_t, label> mp;
            mp.reserve(size_t(flat.size() / 4 * 2 + 16));
            for (label i = 0; i + 3 < label(flat.size()); i += 4)
            {
                uint64_t h = splitMix64
                (
                    uint64_t(flat[i]) * 0x9E3779B97F4A7C15ULL
                  ^ uint64_t(flat[i + 1]) * 0x632BE59BD9B4E019ULL
                  ^ uint64_t(flat[i + 2]) * 0x8CB92BA72F3D8DD7ULL
                );
                mp[h] = i;   // last wins on a (practically impossible) clash
            }
            for (label v = 0; v < label(m.points.size()); ++v)
            {
                if (minRank[v] != ownerRank) { continue; }
                label kx, ky, kz;
                keyOf(v, kx, ky, kz);
                const uint64_t h = splitMix64
                (
                    uint64_t(kx) * 0x9E3779B97F4A7C15ULL
                  ^ uint64_t(ky) * 0x632BE59BD9B4E019ULL
                  ^ uint64_t(kz) * 0x8CB92BA72F3D8DD7ULL
                );
                const auto it = mp.find(h);
                if (it == mp.end()) { continue; }
                const label i = it->second;
                if (flat[i] != kx || flat[i + 1] != ky || flat[i + 2] != kz)
                {
                    continue;   // hash clash: leave the local value
                }
                side[v] = static_cast<signed char>(flat[i + 3]);
            }
        };
        applyOverride(recvW, me - 1);
        applyOverride(recvE, me + 1);
    }
}


namespace
{
// defined with the merges below; the in-plane region split measures with them
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

//- The worst checkMesh skewness among the BOUNDARY and BAFFLE faces of cell
//  c alone (o < 0) or of the union of face neighbours c and o (the faces
//  between them gone). checkMesh measures a boundary face by the offset of
//  its centre from the cell centre's foot on the face plane, over the larger
//  of 0.4 x the centre's distance to that plane and the face's own reach in
//  the offset's direction. A narrow strip at the side of a cell scores high:
//  the 0.5 mm piece of a rack frame's face that a rejoin across a pre-split
//  plane leaves on a 25 mm slab, or the notch face of an L-shaped rejoin. A
//  baffle face is measured the same way: createBaffles makes it a wall of
//  the cell on each side, and a piece that passes as an internal face (the
//  centre line of two cells crosses near it) fails as a wall once it lies
//  far to the side of its own cell's centre.
template<class GeoFn>
Foam::scalar boundarySkewT
(
    const Foam::dcCartCutCells::cutMesh& m,
    const std::vector<std::vector<Foam::label>>& cellFaces,
    Foam::label c,
    Foam::label o,
    GeoFn& geo
)
{
    using namespace Foam;
    std::vector<label> fs;
    bool anyBoundary = false;
    for (label f : cellFaces[c])
    {
        if (o >= 0 && ((m.owner[f] == c) ? m.neighbour[f] : m.owner[f]) == o) { continue; }
        fs.push_back(f);
        anyBoundary = anyBoundary || (m.neighbour[f] < 0) || (m.zone[f] >= 0);
    }
    if (o >= 0)
    {
        for (label f : cellFaces[o])
        {
            if (((m.owner[f] == o) ? m.neighbour[f] : m.owner[f]) == c) { continue; }
            fs.push_back(f);
            anyBoundary = anyBoundary || (m.neighbour[f] < 0) || (m.zone[f] >= 0);
        }
    }
    if (!anyBoundary) { return 0; }
    const point ref = m.cellRefCentre[c];
    scalar vol = 0;
    point cen = point::zero;
    for (label f : fs)
    {
        vector A; point C;
        geo(f, A, C);
        const bool out = (m.owner[f] == c) || (o >= 0 && m.owner[f] == o);
        const scalar pv = (out ? 1 : -1) * ((C - ref) & A) / 3.0;
        vol += pv;
        cen += pv * (ref + 0.75 * (C - ref));
    }
    if (vol <= VSMALL) { return 0; }
    cen /= vol;
    // Faces are measured as they end up. Boundary faces of one patch that lie
    // in one plane and touch are ONE face: coalesceCoplanarFaces joins them
    // before the mesh is written, so a strip beside a face of the same wall is
    // no strip at all. Baffle pieces of one faceZone that lie in one plane and
    // share an opposite cell can coalesce too. Pieces paired with different
    // cells remain separate after createBaffles. A baffle area vector is
    // turned out of the union: its owner may be the cell across.
    std::vector<label> bf, bKey, bOther;
    std::vector<vector> bA;
    std::vector<point> bC;
    std::vector<boundBox> bB;
    for (label f : fs)
    {
        const bool bnd = (m.neighbour[f] < 0);
        if (!bnd && m.zone[f] < 0) { continue; }
        vector A; point C;
        geo(f, A, C);
        if (mag(A) < VSMALL) { continue; }
        if (!bnd && !(m.owner[f] == c || (o >= 0 && m.owner[f] == o))) { A = -A; }
        boundBox bb(boundBox::invertedBox);
        for (label v : m.faces[f]) { bb.add(m.points[v]); }
        bb.inflate(1e-9);
        bf.push_back(f); bKey.push_back(bnd ? m.patch[f] : label(-3 - m.zone[f]));
        bOther.push_back(bnd ? -1 : ((m.owner[f] == c || (o >= 0 && m.owner[f] == o)) ? m.neighbour[f] : m.owner[f]));
        bA.push_back(A); bC.push_back(C); bB.push_back(bb);
    }
    const label nb = label(bf.size());
    auto sharesEdge = [&](label fa, label fb) -> bool
    {
        const auto& a = m.faces[fa]; const auto& b = m.faces[fb];
        for (size_t i = 0; i < a.size(); ++i)
        {
            const point& p = m.points[a[i]];
            const vector e = m.points[a[(i + 1) % a.size()]] - p;
            const scalar l2 = magSqr(e);
            if (l2 < 1e-20) { continue; }
            for (size_t j = 0; j < b.size(); ++j)
            {
                const vector c = m.points[b[j]] - p;
                const vector d = m.points[b[(j + 1) % b.size()]] - p;
                const scalar tc = (c & e) / l2, td = (d & e) / l2;
                if (magSqr(c - tc * e) > 1e-12 || magSqr(d - td * e) > 1e-12) { continue; }
                const scalar overlap = min(scalar(1), max(tc, td)) - max(scalar(0), min(tc, td));
                if (overlap * std::sqrt(l2) > 1e-8) { return true; }
            }
        }
        return false;
    };
    std::vector<label> grp(nb);
    for (label i = 0; i < nb; ++i) { grp[i] = i; }
    auto root = [&](label i) { while (grp[i] != i) { i = grp[i] = grp[grp[i]]; } return i; };
    for (label i = 0; i < nb; ++i)
    {
        const vector ni = bA[i] / mag(bA[i]);
        for (label j = i + 1; j < nb; ++j)
        {
            if (bKey[i] != bKey[j]) { continue; }
            // coalesceCoplanarFaces retains a face per cell pair. A baffle
            // split against different opposite cells will remain several
            // faces after createBaffles, so measure those pieces separately.
            if (bOther[i] != bOther[j]) { continue; }
            if ((ni & (bA[j] / mag(bA[j]))) < 1 - 1e-6) { continue; }
            if (std::abs((bC[j] - bC[i]) & ni) > 1e-6) { continue; }
            if (!bB[i].overlaps(bB[j])) { continue; }
            // Vertex-only contact cannot be coalesced into one simple face.
            // Treating it as a shared edge hides the tiny face's true skew.
            if (!sharesEdge(bf[i], bf[j])) { continue; }
            const label ri = root(i), rj = root(j);
            if (ri != rj) { grp[std::max(ri, rj)] = std::min(ri, rj); }
        }
    }
    scalar worst = 0;
    for (label g = 0; g < nb; ++g)
    {
        if (root(g) != g) { continue; }
        vector A = vector::zero;
        point C = point::zero;
        scalar aSum = 0;
        for (label i = 0; i < nb; ++i)
        {
            if (root(i) != g) { continue; }
            A += bA[i];
            C += mag(bA[i]) * bC[i];
            aSum += mag(bA[i]);
        }
        const scalar a = mag(A);
        if (a < VSMALL || aSum < VSMALL) { continue; }
        C /= aSum;
        const vector nh = A / a;
        const vector Cpf = C - cen;
        const scalar dn = nh & Cpf;
        const vector sv = Cpf - dn * nh;
        const scalar svm = mag(sv);
        if (svm < VSMALL) { continue; }
        const vector svHat = sv / svm;
        scalar fd = 0.4 * std::abs(dn);
        for (label i = 0; i < nb; ++i)
        {
            if (root(i) != g) { continue; }
            for (label v : m.faces[bf[i]]) { fd = std::max(fd, std::abs(svHat & (m.points[v] - C))); }
        }
        worst = std::max(worst, svm / std::max(fd, VSMALL));
    }
    // Also protect the internal faces beside a cut cell. Removing a small
    // wall fragment must not trade boundary skewness for an equally skewed
    // owner/neighbour interpolation stencil.
    for (label f : fs)
    {
        if (m.neighbour[f] < 0 || m.zone[f] >= 0) { continue; }
        const label other = (m.owner[f] == c || (o >= 0 && m.owner[f] == o)) ? m.neighbour[f] : m.owner[f];
        if (other < 0 || !m.cellAlive[other]) { continue; }
        const point refOther = m.cellRefCentre[other];
        point otherCentre(point::zero);
        scalar otherVolume = 0;
        for (label g : cellFaces[other])
        {
            vector A; point C;
            geo(g, A, C);
            const scalar v = (m.owner[g] == other ? 1 : -1) * ((C - refOther) & A) / 3.0;
            otherVolume += v; otherCentre += v * (refOther + 0.75 * (C - refOther));
        }
        if (otherVolume <= VSMALL) { continue; }
        otherCentre /= otherVolume;
        vector A; point C;
        geo(f, A, C);
        const vector d = otherCentre - cen, cpf = C - cen;
        const scalar den = A & d;
        if (std::abs(den) <= VSMALL) { continue; }
        const vector sv = cpf - ((A & cpf) / den) * d;
        const scalar svm = mag(sv);
        if (svm <= VSMALL) { continue; }
        scalar fd = 0.2 * mag(d);
        for (label v : m.faces[f]) { fd = max(fd, std::abs((sv / svm) & (m.points[v] - C))); }
        worst = max(worst, svm / max(fd, VSMALL));
    }
    return worst;
}

} // End anonymous namespace


void Foam::dcCartCutCells::mergeSmallCells
(
    cutMesh& m,
    const settings& s,
    report& r,
    const dcCartSurfaces* surfaces,
    const boolList* wallPatch
)
{
    // Every step below runs in ROUNDS whose outcome does not depend on the
    // number of ranks; a run without -parallel is simply the one-rank case.
    //  1. each rank proposes for the cells it OWNS, from the mesh as it
    //     stands at the start of the round (a cell and its face neighbours
    //     are the same on every rank that holds them: the halo is 3 deep);
    //  2. the proposals of all ranks are gathered on every rank;
    //  3. every rank resolves them in one global order, at most one merge per
    //     cell per round (the gathered list is bit-identical everywhere:
    //     each proposal was computed once, by the owner of its cell);
    //  4. every rank applies each accepted merge whose two cells it holds.
    // A merge may join the cells of two ranks. The union keeps the host's
    // rank, and dropGhostCells turns its faces with the other rank's cells
    // into processor faces on both sides.
    const label me = s.myRank;

    // The contract above ("the same on every rank that holds them") is only
    // true for the GEOMETRY: the split-history piece hash diverges across
    // ranks for interface cells (degenerate splits and splices run in each
    // rank's own reach order). Re-key every piece from the geometry before
    // the first gidOf, so the rounds below arbitrate on names that denote
    // the same cell on every rank. Pieces then stay frozen for the whole
    // function — nothing here splits cells — so the incremental `local` map
    // keys stay valid.
    rekeyPiecesByGeometry(m);

    // how many slivers a host has taken already: a host that keeps growing
    // becomes the 60-face blob the solver chokes on
    std::vector<label> absorbed(m.nCells, 0);

    std::unordered_map<cellGid, label, cellGidHash> local;
    label nDuplicate = 0;
    for (label c = 0; c < m.nCells; ++c)
    {
        if (m.cellAlive[c] && !local.emplace(gidOf(m, c), c).second) { ++nDuplicate; }
    }
    if (nDuplicate)
    {
        Pout<< "dcCartCutCells::mergeSmallCells: " << nDuplicate
            << " cells share a global id with another cell" << endl;
    }
    auto localOf = [&](const cellGid& g) -> label
    {
        const auto it = local.find(g);
        return (it == local.end()) ? -1 : it->second;
    };
    // DCCART_DEBUG_BOX: explain every decision about the cells centred in it
    Foam::boundBox dbgBox(Foam::boundBox::invertedBox);
    if (const char* e = getenv("DCCART_DEBUG_BOX"))
    {
        double b[6];
        if (sscanf(e, "%lf %lf %lf %lf %lf %lf", b, b+1, b+2, b+3, b+4, b+5) == 6)
        {
            dbgBox = Foam::boundBox(Foam::point(b[0], b[1], b[2]), Foam::point(b[3], b[4], b[5]));
        }
    }
    auto centreOf = [&](const std::vector<std::vector<label>>& cellFaces, label c) -> point
    {
        point cen = point::zero; label nf = 0;
        for (label f : cellFaces[c])
        {
            vector n; point cc;
            polygonNormalCentre(m.points, m.faces[f], n, cc);
            cen += cc; ++nf;
        }
        return nf ? point(cen / scalar(nf)) : cen;
    };
    // accepted merges of which this rank holds one cell only: the outer
    // layer of its halo, whose partner lies beyond (dropped either way)
    label nHalfHeld = 0;

    // Face normals and centres, once each: steps 1 and 2 only change which
    // cells a face separates, never its points (step 3 reverses faces and
    // computes its own).
    std::vector<vector> geoN(m.faces.size());
    std::vector<point> geoC(m.faces.size());
    std::vector<char> geoOk(m.faces.size(), 0);
    auto faceGeo = [&](label f, vector& n, point& cc)
    {
        if (!geoOk[f])
        {
            polygonNormalCentre(m.points, m.faces[f], geoN[f], geoC[f]);
            geoOk[f] = 1;
        }
        n = geoN[f];
        cc = geoC[f];
    };
    // The same, the way checkMesh computes them (area-weighted centres): for
    // the convexity test's second look and for the skew measure
    std::vector<vector> ofA(m.faces.size());
    std::vector<point> ofC(m.faces.size());
    std::vector<char> ofOk(m.faces.size(), 0);
    auto faceGeoOF = [&](label f, vector& a, point& cc)
    {
        if (!ofOk[f])
        {
            faceAreaCentreOF(m.points, m.faces[f], ofA[f], ofC[f]);
            ofOk[f] = 1;
        }
        a = ofA[f];
        cc = ofC[f];
    };
    auto faceCtrX = [&](label f) -> point
    {
        vector a; point cc;
        faceGeoOF(f, a, cc);
        return cc;
    };
    const scalar skewLimit = s.mergeMaxSkew;
    // a cell's own worst face, once per round (the round's proposals read
    // the mesh as it stood at its start)
    std::vector<scalar> skewOwnV;
    std::vector<char> skewOwnOk;
    auto resetSkewOwn = [&]()
    {
        skewOwnV.assign(m.nCells, 0);
        skewOwnOk.assign(m.nCells, 0);
    };
    auto skewOwn = [&](const std::vector<std::vector<label>>& cellFaces, label x) -> scalar
    {
        if (!skewOwnOk[x])
        {
            skewOwnOk[x] = 1;
            skewOwnV[x] = boundarySkewT(m, cellFaces, x, label(-1), faceGeoOF);
        }
        return skewOwnV[x];
    };
    // true when joining c and o would leave a boundary or baffle face above
    // the limit that neither had on its own
    auto skewWorse = [&](const std::vector<std::vector<label>>& cellFaces, label c, label o, scalar* suOut) -> bool
    {
        if (skewLimit <= 0) { return false; }
        const scalar su = boundarySkewT(m, cellFaces, c, o, faceGeoOF);
        if (suOut) { *suOut = su; }
        if (su <= skewLimit) { return false; }
        return su > std::max(skewOwn(cellFaces, c), skewOwn(cellFaces, o)) + 1e-9;
    };
    // A union across a baffle face would take that piece out of the baffle.
    // (the guard above and the free-edge split make such pairs: a strip's
    // covered piece and its phantom piece between the same two cells)
    const bool zoneGuard = (skewLimit > 0 || s.baffleEdgeSplit);
    auto sharesZoneFace = [&](const std::vector<std::vector<label>>& cellFaces, label c, label o) -> bool
    {
        if (!zoneGuard) { return false; }
        for (label f : cellFaces[c])
        {
            if (m.zone[f] >= 0 && ((m.owner[f] == c) ? m.neighbour[f] : m.owner[f]) == o) { return true; }
        }
        return false;
    };
    // How far a union may reach. A rank holds its own leaves and s.haloLayers
    // layers of face neighbours around them, and a union keeps its host's
    // rank. Unbounded, chains of unions crept: crumbs joined crumbs pass after
    // pass along a raised floor into a rod 170 mm long whose far end lay beyond
    // its owner's halo (data hall, 8 ranks); the owner wrote the face there as a
    // wall, the rank beside it wrote a processor face, and the mesh could not
    // be reconstructed. Reach is counted in face-neighbour hops between leaves
    // (the halo's own measure), through the leaves the union is made of:
    //  - a sliver may join a host when every leaf of it is within
    //    reachSliver hops of its own leaf, and
    //  - within reachUnion hops of the host's leaf.
    // Then every union lies within reachUnion hops of its owner's leaf (the
    // owner holds all its neighbours), both cells of any merge beside a rank's
    // own cells are held by that rank (it applies the merge as their owner
    // does), and the proposer, whose sliver is its own, sees the host it names
    // as the host's owner does, as long as reachSliver + 1 + reachUnion does
    // not exceed the halo. One rule for any number of ranks; checked below.
    // (one layer for the sliver and two for the union need a four-layer
    // halo; one and one, which a three-layer halo allows, left 1e-3 volume
    // ratios in the testbed's duct, and a sliver of one leaf only left pairs
    // of floor crumbs to be handed to the solid in the data hall)
    const label reachSliver = 1;
    const label reachUnion = 2;
    if (UPstream::parRun() && reachSliver + 1 + reachUnion > s.haloLayers)
    {
        FatalErrorInFunction
            << "sliver reach " << reachSliver << " + 1 + union reach " << reachUnion
            << " exceeds the " << s.haloLayers << "-layer halo" << exit(FatalError);
    }
    // the leaves a union is made of besides its host's own (absorb keeps it)
    std::unordered_map<label, std::vector<std::pair<label, uint64_t>>> unionLeaves;
    auto leafNear = [](label La, uint64_t a, label Lb, uint64_t b) -> bool
    {
        const labelVector ia = dcCartUnpack(a), ib = dcCartUnpack(b);
        const label Lf = std::max(La, Lb);
        const label sa = label(1) << (Lf - La), sb = label(1) << (Lf - Lb);
        label nOverlap = 0;
        for (direction d = 0; d < 3; ++d)
        {
            const label a0 = ia[d] * sa, b0 = ib[d] * sb;
            const label lo = std::max(a0, b0), hi = std::min(a0 + sa, b0 + sb);
            if (hi < lo) { return false; }
            if (hi > lo) { ++nOverlap; }
        }
        return nOverlap >= 2;   // the same leaf, or across a face
    };
    using leafId = std::pair<label, uint64_t>;
    // a cell's leaves, its own first
    auto leavesOf = [&](label x, std::vector<leafId>& out)
    {
        out.assign(1, leafId(m.cellLevel[x], m.cellLeaf[x]));
        const auto it = unionLeaves.find(x);
        if (it != unionLeaves.end()) { out.insert(out.end(), it->second.begin(), it->second.end()); }
    };
    // every leaf of `targets` is within maxHops of `from` (a leaf of `graph`),
    // walking through the leaves of `graph` (targets included)
    auto withinHops = [&](const leafId& from, const std::vector<leafId>& graph, const std::vector<leafId>& targets, label maxHops) -> bool
    {
        std::vector<leafId> nodes(1, from);
        for (const leafId& x : graph) { if (std::find(nodes.begin(), nodes.end(), x) == nodes.end()) { nodes.push_back(x); } }
        std::vector<label> dist(nodes.size(), -1);
        dist[0] = 0;
        std::vector<label> queue(1, 0);
        for (size_t qi = 0; qi < queue.size(); ++qi)
        {
            const label a = queue[qi];
            if (dist[a] >= maxHops) { continue; }
            for (label b = 0; b < label(nodes.size()); ++b)
            {
                if (dist[b] >= 0) { continue; }
                if (leafNear(nodes[a].first, nodes[a].second, nodes[b].first, nodes[b].second))
                {
                    dist[b] = dist[a] + 1;
                    queue.push_back(b);
                }
            }
        }
        for (const leafId& t : targets)
        {
            const label k = label(std::find(nodes.begin(), nodes.end(), t) - nodes.begin());
            if (k >= label(nodes.size()) || dist[k] < 0) { return false; }
        }
        return true;
    };
    auto volumeOfCached = [&](const std::vector<std::vector<label>>& cellFaces, label c) -> scalar
    {
        scalar v = 0;
        for (label f : cellFaces[c])
        {
            vector n; point cc;
            faceGeo(f, n, cc);
            v += ((m.owner[f] == c) ? 1 : -1) * (cc & n);
        }
        return v / 3.0;
    };
    // cell centres, each computed when first asked for in a round: the
    // round's proposals read them from the mesh as it stood at its start
    std::vector<point> ctrV;
    std::vector<char> ctrOk;
    const std::vector<std::vector<label>>* ctrFaces = nullptr;
    auto resetCentres = [&](const std::vector<std::vector<label>>& cellFaces)
    {
        ctrFaces = &cellFaces;
        ctrV.assign(m.nCells, point::zero);
        ctrOk.assign(m.nCells, 0);
    };
    auto centreAt = [&](label x) -> point
    {
        if (!ctrOk[x])
        {
            ctrOk[x] = 1;
            if (m.cellAlive[x])
            {
                const point ref = m.cellRefCentre[x];
                point cen = point::zero; scalar v = 0;
                for (label f : (*ctrFaces)[x])
                {
                    vector n; point cc;
                    faceGeo(f, n, cc);
                    const scalar pv = ((m.owner[f] == x) ? 1 : -1) * ((cc - ref) & n) / 3.0;
                    v += pv; cen += pv * (ref + 0.75 * (cc - ref));
                }
                ctrV[x] = (std::abs(v) > VSMALL) ? point(cen / v) : ref;
            }
        }
        return ctrV[x];
    };

    auto volumeOf = [&](const std::vector<std::vector<label>>& cellFaces, label c) -> scalar
    {
        scalar v = 0;
        for (label f : cellFaces[c])
        {
            vector n; point cc;
            polygonNormalCentre(m.points, m.faces[f], n, cc);
            v += ((m.owner[f] == c) ? 1 : -1) * (cc & n);
        }
        return v / 3.0;
    };

    // absorb `sliver` into `host`: their shared faces vanish, the sliver's
    // other faces become the host's
    auto absorb = [&](const std::vector<std::vector<label>>& cellFaces, label host, label sliver, bool count = true)
    {
        {
            std::vector<std::pair<label, uint64_t>>& to = unionLeaves[host];
            auto addLeaf = [&](label L, uint64_t leaf)
            {
                if (L == m.cellLevel[host] && leaf == m.cellLeaf[host]) { return; }
                for (const auto& x : to) { if (x.first == L && x.second == leaf) { return; } }
                to.emplace_back(L, leaf);
            };
            addLeaf(m.cellLevel[sliver], m.cellLeaf[sliver]);
            const auto it = unionLeaves.find(sliver);
            if (it != unionLeaves.end())
            {
                for (const auto& x : it->second) { addLeaf(x.first, x.second); }
            }
        }
        for (label g : cellFaces[sliver])
        {
            if (!m.faceAlive[g] || (m.owner[g] != sliver && m.neighbour[g] != sliver)) { continue; }
            const label other = (m.owner[g] == sliver) ? m.neighbour[g] : m.owner[g];
            if (other == host) { m.faceAlive[g] = false; continue; }
            if (m.owner[g] == sliver) { m.owner[g] = host; } else { m.neighbour[g] = host; }
            if (m.owner[g] >= 0 && m.owner[g] == m.neighbour[g]) { m.faceAlive[g] = false; }
        }
        m.cellAlive[sliver] = false;
        if (count) { absorbed[host] += 1 + absorbed[sliver]; }
        local.erase(gidOf(m, sliver));
    };
    // An accepted merge is applied by EVERY rank that holds either cell, or by
    // none. A rank holding one side only cannot apply it, and its view of
    // that cell's owner then differs from the ranks that did: on Base604 at 16
    // ranks, bandAbsorb chained rank-9 slabs into hosts on ranks 8 and 10;
    // each outer rank held the slabs but not the far host, kept them as
    // rank-9 cells, and wrote processor faces the middle rank never had
    // (checkMesh -parallel then segfaulted). The accepted list is identical
    // on every rank, so a merge any rank holds on one side only is vetoed
    // everywhere, before anything is applied. The cells stay as they are
    // this round; a later round may propose them again.
    auto vetoHalfHeld = [&](const std::vector<std::pair<cellGid, cellGid>>& accepted)
    {
        List<label> veto(label(accepted.size()), 0);
        for (size_t i = 0; i < accepted.size(); ++i)
        {
            const bool a = localOf(accepted[i].first) >= 0;
            const bool b = localOf(accepted[i].second) >= 0;
            if (a != b) { veto[label(i)] = 1; ++nHalfHeld; }
        }
        if (Pstream::parRun())
        {
            Pstream::listCombineGather(veto, maxEqOp<label>());
            Pstream::listCombineScatter(veto);
        }
        std::vector<bool> out(accepted.size());
        for (size_t i = 0; i < accepted.size(); ++i) { out[i] = veto[label(i)] != 0; }
        return out;
    };
    // A merge must not face a neighbour more obliquely than this, unless one
    // of the two cells already did: a union can be convex and still leave a
    // zig-zag interface whose small piece is ~90 degrees off the centre line.
    const scalar orthoGuard = s.mergeMaxNonOrtho;

    // ---- 1. phantom faces from the feature pre-split ------------------------
    // Where a facet plane ran past its facet the two halves are both fluid
    // and their union is convex (the plane cut a convex cell): join them
    // again so the pre-split leaves no trace there. Where the union would
    // be concave (the wedge between two facets of the solid), the phantom
    // face stays as an ordinary internal face. The halves come from one
    // leaf, so they have one owner, and that owner proposes.
    for (label pass = 0; pass < 4; ++pass)
    {
        std::vector<std::vector<label>> cellFaces;
        buildCellFaces(m, cellFaces);
        resetCentres(cellFaces);
        resetSkewOwn();
        auto* ctrFn = (orthoGuard > 0) ? &centreAt : nullptr;
        std::vector<uint64_t> mine;
        std::set<std::pair<label, label>> seen;
        for (label f = 0; f < label(m.faces.size()); ++f)
        {
            if (!m.faceAlive[f] || m.zone[f] != -2) { continue; }
            const label c = m.owner[f], o = m.neighbour[f];
            if (c < 0 || o < 0 || !m.cellAlive[c] || !m.cellAlive[o]) { continue; }
            if (m.cellRank[c] != me || m.cellRank[o] != me) { continue; }
            if (!seen.emplace(std::min(c, o), std::max(c, o)).second) { continue; }
            if (dbgBox.valid() && (dbgBox.contains(centreOf(cellFaces, c)) || dbgBox.contains(centreOf(cellFaces, o))))
            {
                label nSh = 0;
                for (label g : cellFaces[c]) { if (((m.owner[g] == c) ? m.neighbour[g] : m.owner[g]) == o) { ++nSh; } }
                Pout<< "  [phantom pass " << pass << "] " << c << " at " << centreOf(cellFaces, c)
                    << " + " << o << " at " << centreOf(cellFaces, o) << " shared " << nSh
                    << " convex " << (convexUnionT(m, cellFaces, c, o, ctrFn, 0, faceGeo, faceCtrX) ? 1 : 0) << endl;
            }
            if (!convexUnionT(m, cellFaces, c, o, ctrFn, orthoGuard, faceGeo, faceCtrX)) { continue; }
            if (sharesZoneFace(cellFaces, c, o)) { continue; }
            // A rejoin across a pre-split plane that leaves a narrow boundary
            // strip at the side of the union (the facet's face beyond the
            // plane), or an L-shaped cell whose notch face is one, is not
            // made here when the halves can do without it: a half of at
            // least minVolumeFraction is a cell of its own, and a smaller
            // one goes to the sliver step when a neighbour takes it convexly
            // without such a strip (the cell on its own side of the plane,
            // across the grid face, which makes a box). A half with no such
            // neighbour rejoins as before: a strip is better than a crumb.
            {
                scalar su = 0;
                if (skewWorse(cellFaces, c, o, &su))
                {
                    if (dbgBox.valid() && (dbgBox.contains(centreOf(cellFaces, c)) || dbgBox.contains(centreOf(cellFaces, o))))
                    {
                        Pout<< "  [phantom pass " << pass << "] " << c << " + " << o
                            << " union skewness " << su << " refused" << endl;
                    }
                    ++r.nSkewRefused;
                    continue;
                }
            }
            cellGid h = gidOf(m, c), g = gidOf(m, o);
            if (g < h) { std::swap(h, g); }
            // the smaller piece and the shared face area decide who goes first
            const scalar fracC = (m.cellRefVol[c] > 0) ? volumeOfCached(cellFaces, c) / m.cellRefVol[c] : 1;
            const scalar fracO = (m.cellRefVol[o] > 0) ? volumeOfCached(cellFaces, o) / m.cellRefVol[o] : 1;
            scalar area = 0;
            for (label g2 : cellFaces[c])
            {
                if (((m.owner[g2] == c) ? m.neighbour[g2] : m.owner[g2]) != o) { continue; }
                vector n; point cc;
                faceGeo(g2, n, cc);
                area += mag(n);
            }
            mine.push_back(h.a); mine.push_back(h.b);
            mine.push_back(g.a); mine.push_back(g.b);
            mine.push_back(bitsOf(std::min(fracC, fracO))); mine.push_back(bitsOf(area));
        }
        struct join
        {
            scalar frac = 0;
            scalar area = 0;
            cellGid h, g;
        };
        std::vector<join> props;
        for (const auto& words : gatherWords(mine))
        {
            for (size_t i = 0; i + 6 <= words.size(); i += 6)
            {
                join j;
                j.h = cellGid{words[i], words[i + 1]};
                j.g = cellGid{words[i + 2], words[i + 3]};
                j.frac = scalarOf(words[i + 4]);
                j.area = scalarOf(words[i + 5]);
                props.push_back(j);
            }
        }
        // The smallest piece joins first, into the sibling it shares the most
        // face with. Otherwise its siblings join each other first, and a
        // corner piece at 5e-5 of its hex is left with nothing but concave
        // unions for the rest of the run.
        std::sort
        (
            props.begin(), props.end(),
            [](const join& x, const join& y)
            {
                if (x.frac != y.frac) { return x.frac < y.frac; }
                if (x.area != y.area) { return x.area > y.area; }
                return x.h < y.h || (x.h == y.h && x.g < y.g);
            }
        );
        std::unordered_set<cellGid, cellGidHash> used;
        std::vector<std::pair<cellGid, cellGid>> accepted;
        for (const join& pr : props)
        {
            if (used.count(pr.h) || used.count(pr.g)) { continue; }
            used.insert(pr.h);
            used.insert(pr.g);
            accepted.emplace_back(pr.h, pr.g);
        }
        const std::vector<bool> vetoed = vetoHalfHeld(accepted);
        label nJoined = 0;
        for (size_t i = 0; i < accepted.size(); ++i)
        {
            if (vetoed[i]) { continue; }
            ++nJoined;
            const label h = localOf(accepted[i].first), o = localOf(accepted[i].second);
            // (a rejoin restores a cell; it does not count toward the host's cap)
            if (h >= 0 && o >= 0) { absorb(cellFaces, h, o, false); }
        }
        r.nPhantomJoined += nJoined;
        if (!nJoined) { break; }
    }
    for (label f = 0; f < label(m.faces.size()); ++f) { if (m.zone[f] == -2) { m.zone[f] = -1; } }

    // ---- 2. slivers into a face neighbour -----------------------------------
    auto killExtremeSlivers = [&]()
    {
        // ---- 3. extreme slivers: handed to the solid ----------------------------
        // What is left below killVolumeFraction has no convex host (an L-shaped
        // union fails the face pyramids) and is typically a 0.5 mm slab or bar
        // between a grid plane and a surface: a 1e-7 volume ratio against its
        // neighbour, which the segregated solver cannot take. Hand it to the
        // solid: the faces it shared with live cells become boundary faces
        // under the patch that dominates the sliver's own boundary (the surface
        // it was cut off by), so the wall moves by the sliver's thickness. A
        // sliver with no boundary face of its own (one lying between live cells
        // only, or against a baffle sheet alone) has no patch to give and stays.
        // Every kill is decided before any is applied, so a cell's patch areas
        // never depend on which neighbour went first.
        // ---- 2b. degenerate cells: absorbed whatever they touch ------------------
        // A cell below degenerateVolumeFraction of its hex has no volume to
        // speak of; whatever exemption keeps it alive for the kill below
        // (a baffle face, a thin closed shell) is worth nothing against a
        // zero-volume cell in the solver. It goes into a neighbour across a
        // plain internal face, no quality guard on the host's shape. Decided
        // on the owning rank, gathered, applied wherever both cells are
        // local, in rounds, so every rank sees the same mesh.
        //
        // WHICH neighbour is not free, though. "The union IS the host" holds
        // only when the cell lies against that host alone. A flat flap that
        // runs on under a second cell brings its face with that cell into
        // the union, lying in the flap's plane at the far edge of the host,
        // and the host's centre is on the wrong side of it: checkMesh's
        // "faces are incorrectly oriented". DAML-Testbed: a wall-baffle sheet
        // dips 45 um below the grid plane z=0.93173 at x=2.3471875, the
        // pieces of leaf column (.., 0.931731] above it were degenerate, and
        // the one under a tilted wedge took the wedge as its largest-face
        // host -- keeping a 1.4e-8 m2 triangle with the cell above in the
        // plane the wedge's centre sits 1.1 mm over. The neighbour across
        // the strip's x face continues the same strip between the same two
        // planes, and the union with it is a convex wedge. So the hosts are
        // tried in order -- live cells before degenerate ones (a degenerate
        // host makes a bigger flap, not a cell), then largest shared face --
        // and the first whose union keeps every face pyramid (measured as
        // checkMesh measures: area-weighted face centres, pyramid centroid)
        // is taken. Only when none does is the cell absorbed into the
        // largest-face host as before; nDegenerateInverted counts those.
        if (s.degenerateVolumeFraction > 0)
        {
            // (measured afresh: the cache may predate a face reversal)
            std::fill(ofOk.begin(), ofOk.end(), 0);
            // checkMesh's face-pyramid test on the union of c and o: every
            // face it keeps has the union's centre on its inner side
            auto unionPyramidsOk = [&](const std::vector<std::vector<label>>& cellFaces, label c, label o) -> bool
            {
                std::vector<label> fs;
                for (label f : cellFaces[c])
                {
                    if (m.faceAlive[f] && ((m.owner[f] == c) ? m.neighbour[f] : m.owner[f]) != o) { fs.push_back(f); }
                }
                for (label f : cellFaces[o])
                {
                    if (m.faceAlive[f] && ((m.owner[f] == o) ? m.neighbour[f] : m.owner[f]) != c) { fs.push_back(f); }
                }
                if (fs.size() < 4) { return false; }
                point est = point::zero;
                for (label f : fs) { vector a; point fc; faceGeoOF(f, a, fc); est += fc; }
                est /= scalar(fs.size());
                point cen = point::zero;
                scalar v3 = 0;
                for (label f : fs)
                {
                    vector a; point fc;
                    faceGeoOF(f, a, fc);
                    const bool out = (m.owner[f] == c || m.owner[f] == o);
                    const scalar p3 = (out ? 1 : -1) * (a & (fc - est));
                    cen += p3 * (0.75 * fc + 0.25 * est);
                    v3 += p3;
                }
                if (v3 <= VSMALL) { return false; }
                cen /= v3;
                for (label f : fs)
                {
                    vector a; point fc;
                    faceGeoOF(f, a, fc);
                    const bool out = (m.owner[f] == c || m.owner[f] == o);
                    if ((out ? 1 : -1) * (a & (fc - cen)) <= VSMALL) { return false; }
                }
                return true;
            };
            // Rounds: a column of degenerate cells sandwiched between two
            // sheets reaches a live host only through its ends, one cell per
            // round (Tri_angle: 244 in one column).
            for (label round = 0; round < 64; ++round)
            {
                std::vector<std::vector<label>> cellFaces;
                buildCellFaces(m, cellFaces);
                std::vector<uint64_t> mine;
                for (label c = 0; c < m.nCells; ++c)
                {
                    if (!m.cellAlive[c] || m.cellRank[c] != me || m.cellRefVol[c] <= 0) { continue; }
                    const scalar v = volumeOf(cellFaces, c);
                    if (!(v < s.degenerateVolumeFraction * m.cellRefVol[c])) { continue; }
                    label best = -1;
                    scalar bestA = 0;
                    // (host, its largest face with c)
                    std::vector<std::pair<label, scalar>> hosts;
                    for (label f : cellFaces[c])
                    {
                        if (!m.faceAlive[f] || m.neighbour[f] < 0 || m.zone[f] >= 0) { continue; }
                        const label o = (m.owner[f] == c) ? m.neighbour[f] : m.owner[f];
                        if (o == c || !m.cellAlive[o]) { continue; }
                        vector n; point cc;
                        polygonNormalCentre(m.points, m.faces[f], n, cc);
                        const scalar a = mag(n);
                        if (a > bestA) { bestA = a; best = o; }
                        bool known = false;
                        for (auto& hp : hosts)
                        {
                            if (hp.first == o) { hp.second = max(hp.second, a); known = true; break; }
                        }
                        if (!known) { hosts.emplace_back(o, a); }
                    }
                    if (best < 0) { continue; }
                    // live hosts first, then the largest face, then the global id
                    // (not the face order, which is each rank's own)
                    std::vector<std::pair<label, std::pair<label, scalar>>> ranked;
                    for (const auto& hp : hosts)
                    {
                        const label o = hp.first;
                        const bool degen =
                            m.cellRefVol[o] > 0
                         && volumeOf(cellFaces, o) < s.degenerateVolumeFraction * m.cellRefVol[o];
                        ranked.emplace_back(degen ? 1 : 0, hp);
                    }
                    std::sort
                    (
                        ranked.begin(), ranked.end(),
                        [&](const std::pair<label, std::pair<label, scalar>>& x, const std::pair<label, std::pair<label, scalar>>& y)
                        {
                            if (x.first != y.first) { return x.first < y.first; }
                            if (x.second.second != y.second.second) { return x.second.second > y.second.second; }
                            return gidOf(m, x.second.first) < gidOf(m, y.second.first);
                        }
                    );
                    label pick = -1;
                    for (const auto& rh : ranked)
                    {
                        if (unionPyramidsOk(cellFaces, c, rh.second.first)) { pick = rh.second.first; break; }
                    }
                    const bool inverted = (pick < 0);
                    if (inverted) { pick = best; }
                    const cellGid g = gidOf(m, c), h = gidOf(m, pick);
                    if (getenv("DCCART_DEBUG"))
                    {
                        Pout<< "  degenerate round " << round << " cell at " << centreOf(cellFaces, c)
                            << " frac " << v / m.cellRefVol[c] << " -> host " << pick
                            << " at " << centreOf(cellFaces, pick)
                            << (inverted ? " (no host keeps the face pyramids)" : "")
                            << (pick != best ? " (not the largest-face host)" : "") << endl;
                    }
                    mine.push_back(g.a); mine.push_back(g.b); mine.push_back(h.a); mine.push_back(h.b);
                    mine.push_back(inverted ? 1 : 0);
                }
                std::unordered_set<cellGid, cellGidHash> used;
                label nThisRound = 0;
                for (const auto& words : gatherWords(mine))
                {
                    for (size_t i = 0; i + 5 <= words.size(); i += 5)
                    {
                        const cellGid g{words[i], words[i + 1]}, h{words[i + 2], words[i + 3]};
                        if (used.count(g) || used.count(h)) { continue; }
                        used.insert(g);
                        used.insert(h);
                        ++nThisRound;
                        if (words[i + 4]) { ++r.nDegenerateInverted; }
                        const label sl = localOf(g), hl = localOf(h);
                        if (sl >= 0 && hl >= 0) { absorb(cellFaces, hl, sl, false); }
                    }
                }
                r.nDegenerateAbsorbed += nThisRound;
                if (!nThisRound) { break; }
            }
        }
        if (s.killVolumeFraction > 0)
        {
            // Rounds until none is left: a sliver whose only boundary faces
            // are the ones a killed neighbour handed it goes in the next round
            // (a one-shot pass left such cells behind at 1e-8 of their hex).
            const label killRounds = 4;
            label nKilled = 0;
            for (label round = 0; round < killRounds; ++round)
            {
                std::vector<std::vector<label>> cellFaces;
                buildCellFaces(m, cellFaces);
                std::vector<uint64_t> mine;
                for (label c = 0; c < m.nCells; ++c)
                {
                    if (!m.cellAlive[c] || m.cellRank[c] != me || m.cellRefVol[c] <= 0) { continue; }
                    if (volumeOf(cellFaces, c) >= s.killVolumeFraction * m.cellRefVol[c]) { continue; }
                    // Removing either side would punch a hole in a physical
                    // baffle and discard its faceZone. Keep these cells even
                    // when a nearby wall makes their volume very small.
                    bool touchesBaffle = false;
                    for (label f : cellFaces[c]) { touchesBaffle = touchesBaffle || m.zone[f] >= 0; }
                    // A thin SOLID baffle (containment wall) carries no
                    // faceZone: its cells are slivers against the reference
                    // hex and the killer would delete the whole baffle,
                    // connecting the aisles it seals (Shinaide's con_* patches
                    // came out with zero faces). Keep every cell inside a
                    // thin closed shell.
                    if (!touchesBaffle && surfaces)
                    {
                        touchesBaffle =
                            surfaces->pointInThinSheetShell(centreOf(cellFaces, c));
                    }
                    if (touchesBaffle) { continue; }
                    std::map<label, scalar> patchArea;   // ordered: a tie goes to the lower patch id
                    for (label f : cellFaces[c])
                    {
                        if (m.neighbour[f] >= 0 || m.patch[f] < 0) { continue; }
                        vector n; point cc;
                        polygonNormalCentre(m.points, m.faces[f], n, cc);
                        patchArea[m.patch[f]] += mag(n);
                    }
                    if (patchArea.empty()) { continue; }
                    label dom = -1;
                    scalar domA = -1;
                    for (const auto& kv : patchArea)
                    {
                        if (kv.second > domA) { domA = kv.second; dom = kv.first; }
                    }
                    const cellGid g = gidOf(m, c);
                    if (getenv("DCCART_DEBUG"))
                    {
                        Pout<< "  kill round " << round << " cell at " << centreOf(cellFaces, c)
                            << " frac " << volumeOf(cellFaces, c) / m.cellRefVol[c] << " patch " << dom << endl;
                    }
                    mine.push_back(g.a); mine.push_back(g.b); mine.push_back(uint64_t(dom));
                }
                label nThisRound = 0;
                for (const auto& words : gatherWords(mine))
                {
                    for (size_t i = 0; i + 3 <= words.size(); i += 3)
                    {
                        ++nThisRound;
                        const label c = localOf(cellGid{words[i], words[i + 1]});
                        if (c < 0) { continue; }
                        const label dom = label(words[i + 2]);
                        for (label f : cellFaces[c])
                        {
                            if (!m.faceAlive[f] || (m.owner[f] != c && m.neighbour[f] != c)) { continue; }
                            if (m.neighbour[f] < 0)
                            {
                                m.faceAlive[f] = false;   // its own boundary vanishes
                                continue;
                            }
                            // internal -> boundary of the other cell, normal out of it
                            if (m.owner[f] == c)
                            {
                                std::reverse(m.faces[f].begin(), m.faces[f].end());
                                m.owner[f] = m.neighbour[f];
                            }
                            m.neighbour[f] = -1;
                            m.patch[f] = dom;
                            m.zone[f] = -1;
                        }
                        m.cellAlive[c] = false;
                        local.erase(gidOf(m, c));
                    }
                }
                nKilled += nThisRound;
                if (!nThisRound) { break; }
            }
            r.nKilled += nKilled;
        }
        std::fill(geoOk.begin(), geoOk.end(), 0);
        std::fill(ofOk.begin(), ofOk.end(), 0);
        if (surfaces) { relabelCoplanarPatches(m, *surfaces, s); }
    };
    const label nSliverPasses = 6;
    const label nPasses = nSliverPasses + (skewLimit > 0 ? 6 : 1);
    // a face below this fraction of a union's largest face is "tiny" (5%
    // deferred 85 merges on the testbed and left more slivers than it saved:
    // min volume ratio 3e-4; 1% defers the 19 that matter)
    const scalar tinyFaceFraction = 0.01;
    for (label pass = 0; pass < nPasses; ++pass)
    {
        if (pass == nSliverPasses + (skewLimit > 0 ? 3 : 0))
        {
            killExtremeSlivers();
            if (skewLimit <= 0) { break; }
        }
        const bool qualityPass = pass >= nSliverPasses;
        const bool canDefer = qualityPass ? (pass - nSliverPasses) % 3 < 2 : pass < nSliverPasses - 1;
        std::vector<std::vector<label>> cellFaces;
        buildCellFaces(m, cellFaces);
        std::vector<scalar> vol(m.nCells, 0);
        for (label c = 0; c < m.nCells; ++c)
        {
            if (m.cellAlive[c]) { vol[c] = volumeOfCached(cellFaces, c); }
        }
        resetCentres(cellFaces);
        resetSkewOwn();
        auto* ctrFn = (orthoGuard > 0) ? &centreAt : nullptr;
        auto isSliver = [&](label c) -> bool
        {
            return m.cellRefVol[c] > 0 && vol[c] < s.minVolumeFraction * m.cellRefVol[c];
        };

        // a proposal: the sliver, its volume fraction, the hosts it can take
        // in order of preference
        std::vector<uint64_t> mine;
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!m.cellAlive[c] || m.cellRank[c] != me) { continue; }
            if (qualityPass ? skewOwn(cellFaces, c) <= skewLimit : !isSliver(c)) { continue; }
            if (!(vol[c] == vol[c])) { continue; }
            // The host is the neighbour BEHIND the sliver — across the face
            // facing away from the surface — so the union is the hex the
            // sliver came from, extended, rather than an L-shaped cell.
            // "Away from the surface" is the direction from the hex centre
            // (on the solid / far side) to the sliver's own centroid.
            point cen = point::zero;
            label nf = 0;
            for (label f : cellFaces[c])
            {
                vector n; point cc;
                faceGeo(f, n, cc);
                cen += cc; ++nf;
            }
            if (nf) { cen /= scalar(nf); }
            vector away = cen - m.cellRefCentre[c];
            const scalar awayMag = mag(away);
            if (awayMag > VSMALL) { away /= awayMag; }
            // The preferred host is across the LARGEST plain face shared with
            // one neighbour: that union is the closest to a convex cell.
            // "Facing away from the surface" only breaks near-ties.
            std::unordered_map<label, scalar> sharedArea, sharedAway;
            for (label f : cellFaces[c])
            {
                if (m.neighbour[f] < 0 || m.zone[f] >= 0) { continue; }
                const label o = (m.owner[f] == c) ? m.neighbour[f] : m.owner[f];
                if (!m.cellAlive[o] || absorbed[o] >= 8) { continue; }
                // a sliver does not join another sliver until the last
                // pass: slab slivers along a rail would otherwise chain into
                // a rod half a metre long (each step convex, the result not
                // a cell anyone wants)
                // (a crumb below killVolumeFraction may: it cannot make a rod,
                // and waiting leaves it with nothing but concave unions)
                if
                (
                    !qualityPass && pass < nSliverPasses - 1 && isSliver(o)
                 && vol[c] >= s.killVolumeFraction * m.cellRefVol[c]
                )
                {
                    continue;
                }
                vector n; point cc;
                faceGeo(f, n, cc);
                const scalar a = mag(n);
                if (a < VSMALL) { continue; }
                const vector nOut = (m.owner[f] == c) ? n / a : -n / a;
                sharedArea[o] += a;
                sharedAway[o] += a * (awayMag > VSMALL ? (nOut & away) : 0);
            }
            const bool dbg = dbgBox.valid() && dbgBox.contains(cen);
            if (dbg)
            {
                Pout<< "  [merge pass " << pass << "] sliver " << c << " at " << cen
                    << " frac " << vol[c] / m.cellRefVol[c] << " faces " << label(cellFaces[c].size()) << ":";
                for (label f : cellFaces[c])
                {
                    if (m.neighbour[f] < 0) { Pout<< " [bnd p" << m.patch[f] << "]"; continue; }
                    if (m.zone[f] >= 0) { Pout<< " [zone " << m.zone[f] << "]"; continue; }
                    const label o = (m.owner[f] == c) ? m.neighbour[f] : m.owner[f];
                    // zs: c and o ALSO share a baffle face, so sharesZoneFace
                    // refuses the union (it would take that piece out of the
                    // faceZone). su/bad: the skew guard's own verdict.
                    scalar dbgSu = 0;
                    const int dbgBad = skewWorse(cellFaces, c, o, &dbgSu) ? 1 : 0;
                    Pout<< " [" << o << " frac " << (m.cellRefVol[o] > 0 ? vol[o] / m.cellRefVol[o] : -1)
                        << " abs " << absorbed[o]
                        << " convex " << (convexUnionT(m, cellFaces, c, o, ctrFn, 0, faceGeo, faceCtrX) ? 1 : 0)
                        << " zs " << (sharesZoneFace(cellFaces, c, o) ? 1 : 0)
                        << " su " << dbgSu << " bad " << dbgBad << "]";
                }
                Pout<< endl;
            }
            // never a non-convex union: a sliver with no convex host stays a
            // small, well-shaped cell of its own
            // A host that would introduce an over-limit skewed face is
            // refused even when it is the only available neighbour.
            std::vector<std::pair<scalar, cellGid>> hosts;
            std::map<cellGid, std::vector<cellGid>> hostDeps;
            std::vector<leafId> sLeaves, hLeaves;
            leavesOf(c, sLeaves);
            if (!withinHops(sLeaves[0], sLeaves, sLeaves, reachSliver)) { ++r.nMergeOutOfReach; continue; }
            for (const auto& kv : sharedArea)
            {
                if (sharesZoneFace(cellFaces, c, kv.first)) { continue; }
                leavesOf(kv.first, hLeaves);
                hLeaves.insert(hLeaves.end(), sLeaves.begin(), sLeaves.end());
                if (!withinHops(hLeaves[0], hLeaves, sLeaves, reachUnion)) { ++r.nMergeOutOfReach; continue; }
                if (!convexUnionT(m, cellFaces, c, kv.first, ctrFn, orthoGuard, faceGeo, faceCtrX)) { continue; }
                scalar su = 0;
                const int bad = skewWorse(cellFaces, c, kv.first, &su) ? 1 : 0;
                // The volume threshold requests agglomeration; it must not
                // force a geometrically valid small cell into a much worse
                // interpolation stencil when no suitable host exists.
                if (bad) { ++r.nSkewRejected; continue; }
                // A tiny boundary face can be highly skewed even on a cell
                // whose volume is healthy. Repair it only with a convex
                // neighbour union whose final boundary/baffle faces all pass.
                //
                // When NO host can bring the cell under the limit, the last
                // three quality passes take the merge that strictly lowers the
                // cell's own worst boundary/baffle skewness instead of leaving
                // a face checkMesh fails on. checkMesh normalises a face's
                // skewness by the face's OWN reach, so a 0.2 mm strip at the
                // side of a 21 mm cell scores 204 whatever it is joined to;
                // joining it to the neighbour that swallows the strip takes
                // that face off the mesh altogether. The criterion is `<`, not
                // `< eps`, so every accepted merge strictly lowers a bounded
                // quantity and the passes terminate -- the monotone local
                // operator knife uses for the same debt
                // (knife/src/triangle.c:1624-1661).
                if (qualityPass && su > skewLimit)
                {
                    if (pass < nPasses - 3 || su >= skewOwn(cellFaces, c) - 1e-9)
                    {
                        continue;
                    }
                    ++r.nSkewRepairBelowLimit;
                }
                if (dbg)
                {
                    Pout<< "    host " << kv.first << " union skewness " << su << endl;
                }
                hosts.emplace_back
                (
                    kv.second * (1 + 0.1 * sharedAway[kv.first] / kv.second),
                    gidOf(m, kv.first)
                );
                // The orthogonality guard judged the union against its
                // neighbours as they stand at the start of the round. Across a
                // TINY face that angle is set by where the neighbour's centre
                // is, and a neighbour merged in the same round moves it: two
                // slivers joined to two different hosts left a 4.5 x 0.4 mm
                // band between the unions at 78 degrees (the wall unit's
                // bottom 0.4 mm off a grid plane), each merge having passed
                // against the other sliver's old centre. The neighbours across
                // such faces go with the proposal; the resolution defers a
                // merge whose tiny-face neighbour already changed this round,
                // and keeps those neighbours as they are for the rest of it.
                const label h = kv.first;
                scalar aMax = 0;
                std::vector<std::pair<scalar, label>> across;
                for (label pass2 = 0; pass2 < 2; ++pass2)
                {
                    const label x = pass2 ? h : c, y = pass2 ? c : h;
                    for (label f : cellFaces[x])
                    {
                        const label o = (m.owner[f] == x) ? m.neighbour[f] : m.owner[f];
                        if (o == y) { continue; }
                        vector n; point cc;
                        faceGeo(f, n, cc);
                        aMax = std::max(aMax, mag(n));
                        if (o >= 0 && m.cellAlive[o]) { across.emplace_back(mag(n), o); }
                    }
                }
                std::vector<cellGid>& deps = hostDeps[gidOf(m, h)];
                for (const auto& an : across)
                {
                    if (an.first < tinyFaceFraction * aMax) { deps.push_back(gidOf(m, an.second)); }
                }
                std::sort(deps.begin(), deps.end());
                deps.erase(std::unique(deps.begin(), deps.end()), deps.end());
            }
            if (hosts.empty()) { continue; }
            std::sort
            (
                hosts.begin(), hosts.end(),
                [](const std::pair<scalar, cellGid>& x, const std::pair<scalar, cellGid>& y)
                {
                    return x.first == y.first ? x.second < y.second : x.first > y.first;
                }
            );
            const cellGid g = gidOf(m, c);
            mine.push_back(g.a); mine.push_back(g.b);
            mine.push_back(bitsOf(vol[c] / m.cellRefVol[c]));
            mine.push_back(uint64_t(hosts.size()));
            for (const auto& hc : hosts)
            {
                const cellGid& hg = hc.second;
                mine.push_back(hg.a); mine.push_back(hg.b);
                const std::vector<cellGid>& deps = hostDeps[hg];
                mine.push_back(uint64_t(deps.size()));
                for (const cellGid& dg : deps) { mine.push_back(dg.a); mine.push_back(dg.b); }
            }
        }

        struct proposal
        {
            scalar frac = 0;
            cellGid sliver;
            std::vector<cellGid> hosts;
            std::vector<std::vector<cellGid>> deps;
        };
        std::vector<proposal> props;
        for (const auto& words : gatherWords(mine))
        {
            size_t i = 0;
            while (i + 4 <= words.size())
            {
                proposal p;
                p.sliver = cellGid{words[i], words[i + 1]};
                p.frac = scalarOf(words[i + 2]);
                const size_t nh = size_t(words[i + 3]);
                i += 4;
                for (size_t k = 0; k < nh && i + 3 <= words.size(); ++k)
                {
                    p.hosts.push_back(cellGid{words[i], words[i + 1]});
                    const size_t nd = size_t(words[i + 2]);
                    i += 3;
                    p.deps.emplace_back();
                    for (size_t q = 0; q < nd && i + 2 <= words.size(); ++q, i += 2)
                    {
                        p.deps.back().push_back(cellGid{words[i], words[i + 1]});
                    }
                }
                props.push_back(std::move(p));
            }
        }
        // Leaf order: the slivers of one region are resolved in one sweep
        // through it. Of the orders measured on the testbed (thinnest first,
        // fewest hosts first, leaf order) it left the fewest slivers and the
        // fewest small-volume-ratio faces. Re-evaluate a temporarily blocked
        // preferred host next round instead of alternating merge direction
        // between otherwise identical extrusion layers.
        std::sort
        (
            props.begin(), props.end(),
            [](const proposal& x, const proposal& y) { return x.sliver < y.sliver; }
        );
        std::unordered_set<cellGid, cellGidHash> used;
        // cells across a tiny face of a union made this round: they keep
        // their shape until the next round (see the proposal)
        std::unordered_set<cellGid, cellGidHash> frozen;
        // (host, sliver) in arbitration order, with the host's rank among the
        // proposal's candidates for the debug line
        std::vector<std::pair<cellGid, cellGid>> accepted;
        std::vector<std::pair<label, label>> acceptedChoice;
        for (const proposal& p : props)
        {
            if (used.count(p.sliver) || frozen.count(p.sliver)) { continue; }
            for (size_t k = 0; k < p.hosts.size(); ++k)
            {
                const cellGid& h = p.hosts[k];
                if (used.count(h) || frozen.count(h))
                {
                    if (canDefer) { break; }
                    continue;
                }
                bool stale = false;
                for (const cellGid& dg : p.deps[k]) { if (used.count(dg)) { stale = true; break; } }
                if (stale)
                {
                    ++r.nMergeDeferred;
                    if (canDefer) { break; }
                    continue;
                }
                for (const cellGid& dg : p.deps[k]) { frozen.insert(dg); }
                used.insert(p.sliver);
                used.insert(h);
                accepted.emplace_back(h, p.sliver);
                acceptedChoice.emplace_back(label(k), label(p.hosts.size()));
                break;
            }
        }
        const std::vector<bool> vetoed = vetoHalfHeld(accepted);
        label nThisPass = 0;
        for (size_t i = 0; i < accepted.size(); ++i)
        {
            const label hl = localOf(accepted[i].first), sl = localOf(accepted[i].second);
            if (dbgBox.valid() && sl >= 0 && dbgBox.contains(centreOf(cellFaces, sl)))
            {
                Pout<< "  [merge pass " << pass << "] sliver " << sl << " -> host " << hl
                    << " (choice " << acceptedChoice[i].first << " of " << acceptedChoice[i].second << ")"
                    << (vetoed[i] ? " VETOED: a rank holds one side only" : "") << endl;
            }
            if (vetoed[i]) { continue; }
            ++nThisPass;
            if (hl >= 0 && sl >= 0) { absorb(cellFaces, hl, sl); }
        }
        if (qualityPass) { r.nQualityMerged += nThisPass; }
        else { r.nMerged += nThisPass; }
    }

    // ---- 4. bandAbsorb: thin-plate slab absorption (MeshGenerator S9e port) ----
    //
    // PORTING PLAN  (source: Ref_code/MeshGenerator/core/src/Cartesian.cpp
    // :23070-23525, stage "S9e bandAbsorb"; dcCart's merge entry is
    // mergeSmallCells, whose gid-arbitrated rounds are the parity of MG's
    // "sliver merges in rounds").
    //
    // WHY: a near-coincident / tilted surface junction carves full-footprint
    // THIN PLATES whose thickness t = V/A_iface is far below the leaf size but
    // whose VOLUME FRACTION V/Vref can be >= minVolumeFraction, so the sliver
    // round above (trigger V < 0.10*Vref) never proposes them, and
    // convexUnionT's 2%-margin convexity strands the rest. On DAML-Testbed
    // these leave ~102 faces < 1e-6 m^2 in a diagonal band and poison the
    // p_rgh matrix (steady solve: k collapses to ~3.5e-7 by step 25). MG cures
    // it with a THICKNESS trigger and a WEAKER validity gate; this is that cure
    // re-expressed in dcCart's arbitration so it stays rank-invariant.
    //
    // TRIGGER (MG §W-SB-1): a cell is a thin-plate slab when
    //     t = V / A_iface < h_leaf / 8,
    //   A_iface = area of its LARGEST plain internal face, h_leaf =
    //   cbrt(cellRefVol) (the leaf edge the cell was cut from). Hosts are tried
    //   most-shared-area first (the largest face -> the union grows ACROSS the
    //   thin direction, the host selection repairThinWallCells documents as
    //   missing from the largest-shared-area sliver round), then the remaining
    //   conformal neighbours in gid order until one passes the four gates --
    //   MG's slab branch names only the largest face; the multi-host search is
    //   MG's violator-branch "absorb-into-any-conformal-nbr" applied to slabs,
    //   a strict superset (absorbs >= the single-host port: 214 vs 109 on
    //   DAML-Testbed). MG skips slabs with < 5 faces (tetrahedra are not band
    //   slabs); mirrored here.
    //
    // FOUR GATES (MG :23082-23084, :23385-23433), evaluated on the UNION of
    // slab+target purely from geometry, so the owner's verdict is identical on
    // every rank that holds the pair:
    //   G1 Sigma-Sf closure  |sum of outward face area vectors| <= tolClose
    //                          (MG baTolClose = 1e-10*H^2; here 1e-9*sumA, a
    //                           relative closure on the union's own face area --
    //                           structurally ~0 in dcCart's conformal face mesh)
    //   G2 positive volume    V_union > VSMALL            (MG polyVolumeCentroidIds)
    //   G3 foamOut validity   max_f Sf.(C - fc)/|Sf| <= planeTol, C = the union's
    //                          pyramid cell-centre (MG foamCentroidMaxOutIds <=
    //                          planeTolE1 = 1e-9*H; here planeTol = 1e-9*h_leaf).
    //                          This is convexUnionT's centroid-inside test with
    //                          the 2% margin relaxed to ~0 -- the exact gap that
    //                          strands DAML's thin cells. A concave union whose
    //                          centre falls outside a face is still refused.
    //   G4 face skew          boundarySkewT(union) within mergeMaxSkew, via the
    //                          existing skewWorse (MG s9dSkewOfFace <=
    //                          kS9dSkewMax = 3.85; boundarySkewT is the same
    //                          formula and already pre-joins coplanar boundary
    //                          faces, so MG's inline baUnionPass + mirror splice
    //                          are unnecessary -- coalesceCoplanarFaces joins
    //                          the strips downstream).
    //   Any gate failure = loud skip, zero side effects (tallied below).
    //
    // BOOKKEEPING reuses the existing merge mechanism, not a new one: an accept
    // is applied by absorb(host=target, sliver=slab) -- shared faces vanish, the
    // slab's other faces (baffle pieces included) repoint to the target, the slab
    // dies, local.erase(gid). Coplanar strips left on the union are joined later
    // by coalesceCoplanarFaces; pieces are already geometry-keyed by the
    // rekeyPiecesByGeometry call at function entry.
    //
    // RANK-INVARIANCE (the redline; MG runs S9e in a rank0 serial-replay region,
    // dcCart cannot, so the port proves it instead):
    //   * trigger, host choice and all four gates read ONLY geometry (points,
    //     faces, owner/neighbour, cellRefVol) and global gids -- never a local
    //     face/cell index or insertion order. The largest-face tie-break is by
    //     neighbour cellGid (global), not face order, so the chosen target does
    //     not depend on how m.faces happens to be ordered on a rank. The gate
    //     reductions are sum/max/min over the union face SET (commutative).
    //   * the owner emits each accepted pair as global words (slabGid, tgtGid,
    //     bitsOf(t)); gatherWords + sort-by-slabGid gives one global order and
    //     the `used` set enforces <=1 absorb per cell per round -- the same
    //     contract the sliver round above already relies on for any-np==serial.
    //   * absorbed[]/unionLeaves[] are updated by absorb() on every rank holding
    //     the cells, so at bandAbsorb entry they are the mesh's own (the sliver
    //     rounds are rank-invariant) and the reach/cap checks read them alike.
    //   => the gathered proposal list, and thus nBandAbsorbed, is bit-identical
    //      for any rank count.
    label nBandAbsorbed = 0, nBandRounds = 0;
    label nBandCand = 0, baSkipGate = 0, baSkipSkew = 0, baSkipTgt = 0, baSkipReach = 0;
    const bool bandOn =
        [](){ const char* e = getenv("DCCART_BAND_ABSORB"); return !e || e[0] != '0'; }();
    if (bandOn)
    {
        const scalar baThickDiv = 8.0;     // t < h_leaf / 8   (MG §W-SB-1)
        const label  baCap      = 8;       // host absorb cap  (reuses absorbed[])
        const label  baMaxRound = 16;      // MG: <= 16 rounds
        // G1-G3 on the union of slab c into target o (pure geometry, no local
        // order): closure, positive volume, foamOut cell-centre validity.
        auto bandAbsorbGates =
            [&](const std::vector<std::vector<label>>& cellFaces,
                label c, label o, scalar hLeaf) -> bool
        {
            std::vector<label> fs;
            fs.reserve(cellFaces[c].size() + cellFaces[o].size());
            for (label f : cellFaces[c])
                if (((m.owner[f] == c) ? m.neighbour[f] : m.owner[f]) != o) { fs.push_back(f); }
            for (label f : cellFaces[o])
                if (((m.owner[f] == o) ? m.neighbour[f] : m.owner[f]) != c) { fs.push_back(f); }
            if (fs.size() < 4) { return false; }
            const point ref = m.cellRefCentre[o];
            vector sumSf = vector::zero;
            point  cen   = point::zero;
            scalar V = 0, sumA = 0;
            for (label f : fs)
            {
                vector A; point fc; faceGeoOF(f, A, fc);
                const bool out = (m.owner[f] == c || m.owner[f] == o);
                sumSf += out ? A : vector(-A);
                const scalar a = mag(A); sumA += a;
                const scalar pv = (out ? 1 : -1) * ((fc - ref) & A) / 3.0;
                V   += pv;
                cen += pv * (ref + 0.75 * (fc - ref));
            }
            if (mag(sumSf) > 1e-9 * sumA) { return false; }   // G1 closure
            if (V <= VSMALL) { return false; }                // G2 volume
            cen /= V;
            const scalar planeTol = 1e-9 * hLeaf;             // G3 foamOut
            scalar maxOut = -GREAT;
            for (label f : fs)
            {
                vector A; point fc; faceGeoOF(f, A, fc);
                const bool out = (m.owner[f] == c || m.owner[f] == o);
                const vector So = out ? A : vector(-A);
                const scalar an = mag(So);
                if (an < VSMALL) { continue; }
                maxOut = max(maxOut, (So & (cen - fc)) / an);
            }
            return maxOut <= planeTol;
        };
        for (label round = 0; round < baMaxRound; ++round)
        {
            std::vector<std::vector<label>> cellFaces;
            buildCellFaces(m, cellFaces);
            std::vector<scalar> vol(m.nCells, 0);
            for (label c = 0; c < m.nCells; ++c)
                if (m.cellAlive[c]) { vol[c] = volumeOfCached(cellFaces, c); }
            resetSkewOwn();
            std::vector<uint64_t> mine;
            for (label c = 0; c < m.nCells; ++c)
            {
                if (!m.cellAlive[c] || m.cellRank[c] != me) { continue; }
                if (cellFaces[c].size() < 5) { continue; }   // MG: tets are not band slabs
                const scalar V = vol[c];
                if (!(V > VSMALL) || m.cellRefVol[c] <= 0) { continue; }
                const scalar hLeaf = std::cbrt(m.cellRefVol[c]);
                if (!(hLeaf > 0)) { continue; }
                // Candidate hosts = every plain internal-face neighbour. The
                // thin-plate trigger uses the LARGEST single internal face area
                // (MG: A_iface). The host order is most-shared-area first (across
                // the thin direction, so the union grows away from the wall and
                // y_P rises), tie-broken by neighbour gid -- both global/geometry,
                // so the order never depends on this rank's face numbering. MG's
                // single largest-face host is the first tried; when its union
                // fails a gate the next host is tried (MG's violator branch
                // "absorb-into-any-conformal-nbr"), all under the SAME four gates.
                scalar bestA = -1;
                std::map<label, scalar> sharedArea;   // local nbr -> shared area
                for (label f : cellFaces[c])
                {
                    if (m.neighbour[f] < 0 || m.zone[f] >= 0) { continue; }
                    const label o = (m.owner[f] == c) ? m.neighbour[f] : m.owner[f];
                    if (o < 0 || !m.cellAlive[o]) { continue; }
                    vector A; point fc; faceGeoOF(f, A, fc);
                    const scalar a = mag(A);
                    if (!(a > 0)) { continue; }
                    sharedArea[o] += a;
                    bestA = max(bestA, a);
                }
                if (sharedArea.empty() || !(bestA > 0)) { continue; }
                const scalar t = V / bestA;
                if (!(t < hLeaf / baThickDiv)) { continue; }   // thin-plate trigger
                ++nBandCand;
                std::vector<leafId> sL;
                leavesOf(c, sL);
                if (!withinHops(sL[0], sL, sL, reachSliver)) { ++baSkipReach; continue; }
                struct baHost { scalar area; cellGid gid; label o; };
                std::vector<baHost> hosts;
                hosts.reserve(sharedArea.size());
                for (const auto& kv : sharedArea)
                {
                    hosts.push_back(baHost{kv.second, gidOf(m, kv.first), kv.first});
                }
                std::sort
                (
                    hosts.begin(), hosts.end(),
                    [](const baHost& x, const baHost& y)
                    {
                        if (x.area != y.area) { return x.area > y.area; }
                        return x.gid < y.gid;
                    }
                );
                label chosen = -1; cellGid chosenG{0, 0};
                for (const baHost& h : hosts)
                {
                    const label o = h.o;
                    if (absorbed[o] >= baCap || sharesZoneFace(cellFaces, c, o))
                    {
                        ++baSkipTgt; continue;
                    }
                    std::vector<leafId> hL;
                    leavesOf(o, hL);
                    hL.insert(hL.end(), sL.begin(), sL.end());
                    if (!withinHops(hL[0], hL, sL, reachUnion)) { ++baSkipReach; continue; }
                    if (!bandAbsorbGates(cellFaces, c, o, hLeaf)) { ++baSkipGate; continue; }
                    scalar su = 0;
                    if (skewWorse(cellFaces, c, o, &su)) { ++baSkipSkew; continue; }
                    chosen = o; chosenG = h.gid; break;
                }
                if (chosen < 0) { continue; }   // no conformal host passed the gates
                const cellGid gc = gidOf(m, c);
                mine.push_back(gc.a); mine.push_back(gc.b);
                mine.push_back(chosenG.a); mine.push_back(chosenG.b);
                mine.push_back(bitsOf(t));
            }
            struct baProp { cellGid slab, tgt; scalar t; };
            std::vector<baProp> props;
            for (const auto& words : gatherWords(mine))
            {
                for (size_t i = 0; i + 5 <= words.size(); i += 5)
                {
                    props.push_back
                    (
                        baProp
                        {
                            cellGid{words[i], words[i + 1]},
                            cellGid{words[i + 2], words[i + 3]},
                            scalarOf(words[i + 4])
                        }
                    );
                }
            }
            // one global order (slab gid, then target gid): deterministic and
            // rank-invariant, the same arbitration the sliver round uses
            std::sort
            (
                props.begin(), props.end(),
                [](const baProp& x, const baProp& y)
                {
                    if (!(x.slab == y.slab)) { return x.slab < y.slab; }
                    return x.tgt < y.tgt;
                }
            );
            std::unordered_set<cellGid, cellGidHash> used;
            std::vector<std::pair<cellGid, cellGid>> accepted;   // (target, slab)
            for (const baProp& p : props)
            {
                if (used.count(p.slab) || used.count(p.tgt)) { continue; }
                used.insert(p.slab); used.insert(p.tgt);
                accepted.emplace_back(p.tgt, p.slab);
            }
            // chains of these (a slab into a host that took a slab last round)
            // are what carry a union across a narrow middle rank: see
            // vetoHalfHeld
            const std::vector<bool> vetoed = vetoHalfHeld(accepted);
            label nApplied = 0;
            for (size_t i = 0; i < accepted.size(); ++i)
            {
                if (vetoed[i]) { continue; }
                ++nApplied;   // arbitrated list and veto are both global => mesh-global
                const label hl = localOf(accepted[i].first), sl = localOf(accepted[i].second);
                if (hl >= 0 && sl >= 0) { absorb(cellFaces, hl, sl); }
            }
            nBandAbsorbed += nApplied;
            nBandRounds = round + 1;
            if (!nApplied) { break; }
        }
        // nBandAbsorbed is mesh-global (counted in the arbitrated apply loop, so
        // identical on every rank); the cand/skip tallies are owner-local and
        // reduced here. All ranks reach this collective (bandOn is uniform).
        const label candAll  = returnReduce(nBandCand,   sumOp<label>());
        const label gateAll  = returnReduce(baSkipGate,  sumOp<label>());
        const label skewAll  = returnReduce(baSkipSkew,  sumOp<label>());
        const label tgtAll   = returnReduce(baSkipTgt,   sumOp<label>());
        const label reachAll = returnReduce(baSkipReach, sumOp<label>());
        Info<< "dcCartCutCells::mergeSmallCells bandAbsorb (S9e): rounds "
            << nBandRounds << ", absorbed " << nBandAbsorbed
            << " thin-plate slabs (t < h_leaf/8); candidates " << candAll
            << ", skipped: gates " << gateAll << ", skew " << skewAll
            << ", target " << tgtAll << ", reach " << reachAll << nl;
    }

    // ---- 5. wedgeKill: the terminal y_P-floor round (route 3 of the DAML
    // thin-wall wedge WP, docs/superpowers/specs/2026-09-18). Everything a
    // merge could do has been tried and refused by now (sliver rounds,
    // quality rounds, bandAbsorb): what is left below the floor is the
    // concave-union residue -- the spiral-riser tetrahedra whose union with
    // EVERY neighbour fails the foamOut gate, so no merge can ever lift their
    // y_P, and whose V/Vref (~1e-5) is too big for killVolumeFraction
    // (1e-6). Hand them to the solid, the action killExtremeSlivers takes,
    // under a STRICT admission the ungated repairThinWallCells /
    // killVolumeFraction experiments lacked:
    //   * V < wedgeVolumeFraction of the cell's own hex -- a micro wedge
    //     only; the healthy corner-cut and merged cells whose centroid merely
    //     PROJECTS near a wall plane (V/Vref 0.6-8, measured on DAML at
    //     y_P 3.9e-4..4.9e-4) are never touched, which is what the old
    //     ungated runs got wrong (75 stranded films, skew 3.97 -> 4.81);
    //   * y_P < wedgeWallDistance, measured exactly the way
    //     meshQualityCensus measures it (volume centroid to the nearest
    //     plain-wall face plane, zones and processor patches excluded);
    //   * NO faceZone face (see the exemption comment below) and not inside
    //     a thin closed shell (Shinaide's containment walls);
    //   * the cell has a boundary patch to hand its faces to (dominant patch,
    //     same rule as killExtremeSlivers).
    // On DAML this takes exactly the 12 riser wedges (worst y_P 1.30e-4, the
    // WP-V6 eps=3.9e11 killer), leaves max skewness at 3.9998 and checkMesh
    // at Mesh OK.
    // RANK-INVARIANCE: the identical contract killExtremeSlivers runs under.
    // Admission reads only geometry + global patch ids + gid; the owner
    // proposes (gid, domPatch); gatherWords + one global apply list; every
    // rank applies the kills it holds (halo copies included), so the mesh is
    // the same for any rank count. Decisions are made on the round-start
    // snapshot before any apply, so adjacent kills cannot see each other's
    // patch areas.
    if (s.wedgeVolumeFraction > 0 && s.wedgeWallDistance > 0 && wallPatch)
    {
        const bool wedgeDbg = (getenv("DCCART_WEDGE_DEBUG") != nullptr);
        const label wedgeRounds = 4;
        label nWedgeKilled = 0, nWedgeRounds = 0;
        label wSkipZone = 0, wSkipShell = 0, wSkipNoPatch = 0;
        for (label round = 0; round < wedgeRounds; ++round)
        {
            std::vector<std::vector<label>> cellFaces;
            buildCellFaces(m, cellFaces);
            std::vector<uint64_t> mine;
            for (label c = 0; c < m.nCells; ++c)
            {
                if (!m.cellAlive[c] || m.cellRank[c] != me || m.cellRefVol[c] <= 0) { continue; }
                const scalar V = volumeOf(cellFaces, c);
                if (!(V > VSMALL) || V >= s.wedgeVolumeFraction * m.cellRefVol[c]) { continue; }
                // ANY faceZone face exempts the cell. Baffle-side wedges
                // are NOT killable, measured on DAML three ways: killing the
                // micron tetrahedra at a sheet junction leaves the receiver
                // an orphan micron notch at its far corner (skew ~36 on 12
                // faces, checkMesh Failed) -- no coplanar partner to coalesce
                // into, no snap mate within any budget, and merging the wedge
                // back is the concave union foamOut refuses; the >=5-face
                // rule and the axis-aligned-micro-face rule both still let
                // junction wedges through to the same failure. A cyclic kill
                // would also orphan the partner face of the pair. The baffle
                // micro wedges are instead NOT CREATED: baffleEdgeSplit off
                // (DCCART_NO_BAFFLE_EDGE_SPLIT) removed the V=1e-11 y_P=2.5e-5
                // junction tetrahedra outright (min cell volume 9.6e-8 ->
                // 5.3e-7 of its hex, max skewness unchanged). What survives
                // on the sheets is the grazing plate row (few cells,
                // OpenFOAM |cf-C| ~ 1e-4) -- left for the solver to prove out.
                bool touchesZone = false;
                for (label f : cellFaces[c])
                {
                    if (m.zone[f] >= 0) { touchesZone = true; break; }
                }
                if (touchesZone) { ++wSkipZone; continue; }
                // volume centroid, exactly cellCentreVolOF's divergence sum
                // (the census metric), so the admission and the reported
                // quality floor measure the same y
                const point e = m.cellRefCentre[c];
                vector acc = vector::zero;
                scalar v3 = 0;
                for (label f : cellFaces[c])
                {
                    vector a; point fc;
                    faceGeoOF(f, a, fc);
                    const scalar p3 = (m.owner[f] == c) ? (a & (fc - e)) : (a & (e - fc));
                    acc += p3 * (0.75 * fc + 0.25 * e);
                    v3 += p3;
                }
                const point cen = (mag(v3) > VSMALL) ? point(acc / v3) : e;
                if (surfaces && surfaces->pointInThinSheetShell(cen)) { ++wSkipShell; continue; }
                scalar yBest = GREAT;
                for (label f : cellFaces[c])
                {
                    if (m.neighbour[f] >= 0) { continue; }
                    if (m.zone[f] >= 0 || m.patch[f] < 0) { continue; }
                    if (m.patch[f] >= wallPatch->size() || !(*wallPatch)[m.patch[f]]) { continue; }
                    vector a; point fc;
                    faceGeoOF(f, a, fc);
                    const scalar am = mag(a);
                    if (am < VSMALL) { continue; }
                    yBest = min(yBest, std::abs(scalar((a / am) & (fc - cen))));
                }
                if (!(yBest < s.wedgeWallDistance)) { continue; }
                std::map<label, scalar> patchArea;   // ordered: tie -> lower patch id
                for (label f : cellFaces[c])
                {
                    if (m.neighbour[f] >= 0 || m.patch[f] < 0) { continue; }
                    vector n; point cc;
                    polygonNormalCentre(m.points, m.faces[f], n, cc);
                    patchArea[m.patch[f]] += mag(n);
                }
                if (patchArea.empty()) { ++wSkipNoPatch; continue; }
                label dom = -1;
                scalar domA = -1;
                for (const auto& kv : patchArea)
                {
                    if (kv.second > domA) { domA = kv.second; dom = kv.first; }
                }
                const cellGid g = gidOf(m, c);
                if (wedgeDbg)
                {
                    Pout<< "  wedgeKill round " << round << " cell at " << cen
                        << " V/Vref " << V / m.cellRefVol[c] << " y_P " << yBest
                        << " -> patch " << dom << endl;
                }
                mine.push_back(g.a); mine.push_back(g.b); mine.push_back(uint64_t(dom));
            }
            label nThisRound = 0;
            for (const auto& words : gatherWords(mine))
            {
                for (size_t i = 0; i + 3 <= words.size(); i += 3)
                {
                    ++nThisRound;
                    const label c = localOf(cellGid{words[i], words[i + 1]});
                    if (c < 0) { continue; }
                    const label dom = label(words[i + 2]);
                    for (label f : cellFaces[c])
                    {
                        if (!m.faceAlive[f] || (m.owner[f] != c && m.neighbour[f] != c)) { continue; }
                        if (m.neighbour[f] < 0)
                        {
                            m.faceAlive[f] = false;   // its own boundary vanishes
                            continue;
                        }
                        // internal -> boundary of the other cell, normal out of it
                        if (m.owner[f] == c)
                        {
                            std::reverse(m.faces[f].begin(), m.faces[f].end());
                            m.owner[f] = m.neighbour[f];
                        }
                        m.neighbour[f] = -1;
                        m.patch[f] = dom;
                        m.zone[f] = -1;
                    }
                    m.cellAlive[c] = false;
                    local.erase(gidOf(m, c));
                }
            }
            nWedgeKilled += nThisRound;
            nWedgeRounds = round + 1;
            if (!nThisRound) { break; }
        }
        // nWedgeKilled came off the gathered list: mesh-global, identical on
        // every rank. The skips are owner-local tallies.
        const label skipZoneAll = returnReduce(wSkipZone, sumOp<label>());
        const label skipShellAll = returnReduce(wSkipShell, sumOp<label>());
        const label skipNoPatchAll = returnReduce(wSkipNoPatch, sumOp<label>());
        r.nWedgeKilled += nWedgeKilled;
        Info<< "dcCartCutCells::mergeSmallCells wedgeKill (y_P < "
            << s.wedgeWallDistance << ", V/Vref < " << s.wedgeVolumeFraction
            << "): rounds " << nWedgeRounds << ", handed " << nWedgeKilled
            << " wedges to the solid; skipped: zone " << skipZoneAll
            << ", thin shell " << skipShellAll
            << ", no patch " << skipNoPatchAll << nl;
        std::fill(geoOk.begin(), geoOk.end(), 0);
        std::fill(ofOk.begin(), ofOk.end(), 0);
        if (surfaces) { relabelCoplanarPatches(m, *surfaces, s); }
    }

    if (getenv("DCCART_DEBUG"))
    {
        std::vector<std::vector<label>> cellFaces;
        buildCellFaces(m, cellFaces);
        label nLeft = 0;
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!m.cellAlive[c] || m.cellRank[c] != me || m.cellRefVol[c] <= 0) { continue; }
            if (volumeOf(cellFaces, c) < s.minVolumeFraction * m.cellRefVol[c]) { ++nLeft; }
        }
        const label nLeftAll = returnReduce(nLeft, sumOp<label>());
        const label nHalfAll = returnReduce(nHalfHeld, sumOp<label>());
        Info<< "  slivers left after merge: " << nLeftAll
            << "; accepted merges held on one side only: " << nHalfAll
            << "; unions refused on checkMesh's centroid only: " << gConvexExactRejects << nl;
    }


}


// * * * * * * * * * * * * * * coalesceCoplanarFaces * * * * * * * * * * * * //

namespace
{

//- Merge two polygons that share an edge chain into one, both oriented the
//  same way. Vertices of one lying on an edge of the other are spliced in
//  first, so hanging vertices do not hide a shared edge. False when the
//  remaining edges do not chain into a single loop.
bool mergePolygons
(
    const std::vector<Foam::point>& pts,
    const std::vector<label>& A,
    const std::vector<label>& B,
    std::vector<label>& out
)
{
    using namespace Foam;
    auto splice = [&](const std::vector<label>& poly, const std::vector<label>& other)
    {
        std::vector<label> res;
        const label k = label(poly.size());
        for (label i = 0; i < k; ++i)
        {
            const label a = poly[i], b = poly[(i + 1) % k];
            res.push_back(a);
            const vector e = pts[b] - pts[a];
            const scalar le2 = magSqr(e);
            if (le2 < 1e-24) { continue; }
            // other's vertices strictly inside edge a-b, ordered along it
            std::vector<std::pair<scalar, label>> ins;
            for (label v : other)
            {
                if (v == a || v == b) { continue; }
                const vector d = pts[v] - pts[a];
                const scalar t = (d & e) / le2;
                if (t <= 1e-9 || t >= 1 - 1e-9) { continue; }
                if (magSqr(d - t * e) > 1e-14) { continue; }
                ins.emplace_back(t, v);
            }
            std::sort(ins.begin(), ins.end());
            for (const auto& p : ins) { res.push_back(p.second); }
        }
        return res;
    };
    const std::vector<label> A2 = splice(A, B);
    const std::vector<label> B2 = splice(B, A);
    // directed edges; shared ones (opposite directions) cancel
    std::set<std::pair<label, label>> eb;
    for (size_t i = 0; i < B2.size(); ++i) { eb.emplace(B2[i], B2[(i + 1) % B2.size()]); }
    std::map<label, label> next;
    label nShared = 0;
    auto addEdge = [&](label a, label b) -> bool
    {
        if (next.count(a)) { return false; }   // a vertex with two outgoing edges
        next[a] = b; return true;
    };
    bool ok = true;
    for (size_t i = 0; i < A2.size() && ok; ++i)
    {
        const label a = A2[i], b = A2[(i + 1) % A2.size()];
        if (eb.count(std::make_pair(b, a))) { eb.erase(std::make_pair(b, a)); ++nShared; continue; }
        ok = addEdge(a, b);
    }
    if (!ok || !nShared) { return false; }
    for (const auto& e : eb) { if (!addEdge(e.first, e.second)) { return false; } }
    // chain into one loop
    out.clear();
    if (next.empty()) { return false; }
    const label start = next.begin()->first;
    label cur = start;
    do
    {
        out.push_back(cur);
        auto it = next.find(cur);
        if (it == next.end()) { return false; }
        cur = it->second;
        if (out.size() > next.size()) { return false; }
    } while (cur != start);
    if (out.size() != next.size() || out.size() < 3) { return false; }
    // a pinched union (one vertex visited twice) is not one simple polygon
    {
        std::set<label> seen;
        for (label v : out) { if (!seen.insert(v).second) { return false; } }
    }
    // collinear vertices are kept: a neighbour face may still reference them
    return true;
}

} // end anonymous namespace


void Foam::dcCartCutCells::rekeyPiecesByGeometry(cutMesh& m)
{
    // See the declaration: the split-history piece hash the clip passes
    // stamp is rank-local (degenerate splits and splices run in each rank's
    // own reach order), while the GEOMETRY of a shared cell converges to the
    // same polygons on every rank. The gid-arbitrated rounds must name cells
    // by something every rank computes identically, so the piece becomes a
    // hash of the quantised geometry: centroid, bounding box, face count and
    // the per-face sorted vertex coordinates, faces combined by SUM so the
    // cell's face order (local ids) cannot leak into the name.
    std::vector<std::vector<label>> cf;
    buildCellFaces(m, cf);
    for (label c = 0; c < m.nCells; ++c)
    {
        if (!m.cellAlive[c]) { continue; }
        point lo(GREAT, GREAT, GREAT), hi(-GREAT, -GREAT, -GREAT);
        // Centroid accumulated in QUANTISED INTEGER space, not in doubles:
        // `sum += x` follows this rank's local face order and per-face
        // vertex order, so two ranks can differ by an ulp, and
        // llround(cen * 1e7) flips at a half-integer boundary — a rare,
        // rank-dependent gid. Summing the same 1e7-quantised integers the
        // per-face hash already uses and dividing by the count is a pure
        // function of integers, bit-identical on every rank.
        long long sumQx = 0, sumQy = 0, sumQz = 0;
        label nPts = 0, nFc = 0;
        uint64_t faceSum = 0;
        for (const label f : cf[c])
        {
            if (!m.faceAlive[f]) { continue; }
            ++nFc;
            std::vector<std::tuple<long long, long long, long long>> qs;
            qs.reserve(m.faces[f].size());
            for (const label v : m.faces[f])
            {
                const point& x = m.points[v];
                const long long qx = (long long)std::llround(x.x() * 1e7);
                const long long qy = (long long)std::llround(x.y() * 1e7);
                const long long qz = (long long)std::llround(x.z() * 1e7);
                qs.emplace_back(qx, qy, qz);
                lo = min(lo, x);
                hi = max(hi, x);
                sumQx += qx; sumQy += qy; sumQz += qz;
                ++nPts;
            }
            std::sort(qs.begin(), qs.end());
            uint64_t h = 0xcbf29ce484222325ULL;
            for (const auto& q : qs)
            {
                h = (h ^ (uint64_t(std::get<0>(q)) + 0x9E3779B97F4A7C15ULL)) * 0x100000001b3ULL;
                h = (h ^ (uint64_t(std::get<1>(q)) + 0x632BE59BD9B4E019ULL)) * 0x100000001b3ULL;
                h = (h ^ (uint64_t(std::get<2>(q)) + 0x8CB92BA72F3D8DD7ULL)) * 0x100000001b3ULL;
            }
            faceSum += splitMix64(h);
        }
        if (!nPts) { continue; }
        uint64_t key = faceSum;
        auto mix = [&key](long long v)
        {
            key = splitMix64(key ^ (uint64_t(v) + 0x9E3779B97F4A7C15ULL));
        };
        // integer centroid (truncation toward zero is the same C++ rule on
        // every rank); every other hash component is unchanged
        mix(sumQx / nPts);
        mix(sumQy / nPts);
        mix(sumQz / nPts);
        mix((long long)std::llround(lo.x() * 1e7));
        mix((long long)std::llround(lo.y() * 1e7));
        mix((long long)std::llround(lo.z() * 1e7));
        mix((long long)std::llround(hi.x() * 1e7));
        mix((long long)std::llround(hi.y() * 1e7));
        mix((long long)std::llround(hi.z() * 1e7));
        mix(nFc);
        mix(nPts);
        m.cellPiece[c] = key;
    }
}


void Foam::dcCartCutCells::mergeCoincidentPoints
(
    cutMesh& m, const boundBox* within, std::vector<label>* flattenedAlias
)
{
    // Crossings, cap pieces and the octree's hanging vertices can put two
    // ids on one position; the polyMesh must have one point there or the
    // faces around it do not share it. `within`: only the points in that
    // box (a shell's neighbourhood) — the whole mesh costs 0.4 s a pass.
    //
    // canonMin, not canon: the surviving coordinate of a cluster is its
    // canonical minimum (grid corner preferred), NOT the first id this
    // rank appended. Cut-point ids follow each rank's own pass order, so a
    // first-wins rule leaves the owner of an interface cell and the rank
    // holding it as halo with bit-different coordinates for the same
    // geometry: split histories diverge, cell global ids stop matching
    // across ranks, the merge rounds arbitrate on names that are not the
    // same cell, and the two ranks' processor patches end up holding
    // different face sets — checkMesh then segfaults exchanging indices
    // with a neighbour whose list does not mirror its own (DAML-Testbed
    // rank2/rank3, four faces apart at a GPU server straddling the slab).
    pointCanon canon;
    std::vector<label> alias(m.points.size());
    label nDup = 0;
    for (label p = 0; p < label(m.points.size()); ++p)
    {
        alias[p] = p;
        if (within && !within->contains(m.points[p])) { continue; }
        const label c = canon.canonMin(m.points, p, alias, m.nGridPoints);
        alias[p] = c;
        if (c != p) { ++nDup; }
    }
    // A representative superseded late in the sweep leaves a chain behind
    // (alias[alias[p]] != alias[p]); flatten before rewriting the faces.
    for (label p = 0; p < label(alias.size()); ++p)
    {
        label r = alias[p];
        label guard = 0;
        while (alias[r] != r && ++guard < 128) { r = alias[r]; }
        alias[p] = r;
    }
    if (flattenedAlias) { *flattenedAlias=alias; }
    // UNCONDITIONAL face rewrite — nDup is not a safe gate. canonMin's
    // supersede path (canonical minimum arrives late) sets alias[got] = v
    // and returns v, so neither v itself (c == p at the caller) nor the
    // displaced got (swept earlier, its own call returned c == p) is ever
    // counted in nDup. A pass whose within-box holds only pure cut-vs-cut
    // clusters with the minimum arriving late would see nDup == 0 and skip
    // the rewrite, while a rank whose local append order made the minimum
    // arrive first rewrites: the two ranks' faces then reference different
    // ids whose coordinates differ by ~1e-6 m, breaking byte-identity and
    // every rekey/gid/sync step built on it. The flattened alias is a
    // rank-invariant pure function of the cluster coordinate multisets,
    // and when nothing moved alias[p] == p for every p, so the sweep below
    // writes each id back to itself — a semantic no-op costing one linear
    // pass over the face table, the same order as the point sweep above.
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        for (label& v : m.faces[f]) { v = alias[v]; }
    }
    if (nDup && !within)
    {
        Info<< "Cut mesh: merged " << nDup << " coincident points" << nl;
    }
}


void Foam::dcCartCutCells::dedupFaceVertices(cutMesh& m)
{
    // after mergeCoincidentPoints a face can list one id twice in a row
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        std::vector<label>& poly = m.faces[f];
        // (most faces have nothing to drop: look before building a copy)
        bool repeat = (poly.size() > 1 && poly.front() == poly.back());
        for (size_t i = 1; i < poly.size() && !repeat; ++i) { repeat = (poly[i] == poly[i - 1]); }
        if (repeat)
        {
            std::vector<label> out;
            out.reserve(poly.size());
            for (label v : poly) { if (out.empty() || out.back() != v) { out.push_back(v); } }
            while (out.size() > 1 && out.front() == out.back()) { out.pop_back(); }
            if (out.size() != poly.size()) { poly.swap(out); }
        }
        if (poly.size() < 3) { m.faceAlive[f] = false; }
    }
}


Foam::label Foam::dcCartCutCells::refineLongCells(cutMesh& m, label myRank, scalar maxAspect)
{
    label total = 0;
    for (label round = 0; round < 5; ++round)
    {
        // Geometry-keyed global ids, same reason as mergeSmallCells' entry:
        // the split-history piece is rank-local for interface cells — and
        // this function's own splits stamp history hashes too, so re-key at
        // the start of EVERY round, before the proposals read gidOf.
        rekeyPiecesByGeometry(m);
        std::vector<std::vector<label>> cf;
        buildCellFaces(m, cf);
        std::vector<uint64_t> mine;
        std::unordered_map<cellGid, label, cellGidHash> local;
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!m.cellAlive[c]) { continue; }
            local.emplace(gidOf(m, c), c);
            if (m.cellRank[c] != myRank) { continue; }
            vector sumMag(vector::zero);
            scalar vol = 0;
            boundBox bb(boundBox::invertedBox);
            for (label f : cf[c])
            {
                vector A; point C;
                faceAreaCentreOF(m.points, m.faces[f], A, C);
                sumMag += cmptMag(A);
                vol += (m.owner[f] == c ? 1 : -1) * ((C - m.cellRefCentre[c]) & A) / 3.0;
                for (label v : m.faces[f]) { bb.add(m.points[v]); }
            }
            if (vol <= VSMALL || !bb.valid()) { continue; }
            const scalar aspect = max(cmptMax(sumMag) / max(cmptMin(sumMag), VSMALL),
                cmptSum(sumMag) / (6 * std::pow(vol, 2.0 / 3.0)));
            if (aspect <= maxAspect) { continue; }
            const vector span = bb.span();
            direction axis = 0;
            for (direction d = 1; d < 3; ++d) { if (span[d] > span[axis]) { axis = d; } }
            scalar transverse = 0;
            for (direction d = 0; d < 3; ++d) { if (d != axis) { transverse = max(transverse, span[d]); } }
            if (span[axis] < 4 * transverse) { continue; }
            const cellGid gid = gidOf(m, c);
            mine.push_back(gid.a); mine.push_back(gid.b); mine.push_back(axis);
            mine.push_back(bitsOf(bb.centre()[axis]));
        }
        // The owner chooses the plane; every halo holding that cell applies
        // the same split. Cells sharing a plane are processed together.
        std::map<std::pair<label, long long>, std::set<cellGid>> groups;
        for (const auto& words : gatherWords(mine))
        {
            for (size_t i = 0; i + 4 <= words.size(); i += 4)
            {
                groups[{label(words[i + 2]), std::llround(scalarOf(words[i + 3]) * 1e9)}]
                    .insert(cellGid{words[i], words[i + 1]});
            }
        }
        if (groups.empty()) { break; }
        for (const auto& group : groups)
        {
            const label axis = group.first.first;
            const scalar plane = scalar(group.first.second) * 1e-9;
            std::vector<bool> eligible(m.nCells, false);
            bool any = false;
            for (const cellGid& g : group.second)
            {
                const auto it = local.find(g);
                if (it != local.end()) { eligible[it->second] = true; any = true; }
            }
            if (!any) { continue; }
            std::vector<signed char> side(m.points.size());
            for (label p = 0; p < label(m.points.size()); ++p)
            {
                const scalar d = m.points[p][axis] - plane;
                side[p] = std::abs(d) <= 1e-10 ? 0 : (d < 0 ? -1 : 1);
            }
            std::unordered_map<uint64_t, label> crossings;
            auto crossing = [&](label a, label b) -> label
            {
                const uint64_t key = edgeKey(a, b);
                const auto it = crossings.find(key);
                if (it != crossings.end()) { return it->second; }
                const scalar da = m.points[a][axis] - plane, db = m.points[b][axis] - plane;
                if (side[a] == 0 || side[b] == 0) { return -2; }
                point p = m.points[a] + (da / (da - db)) * (m.points[b] - m.points[a]);
                p[axis] = plane;
                const label id = m.points.size();
                m.points.push_back(p); side.push_back(0); crossings[key] = id;
                return id;
            };
            auto existing = [&](label a, label b) -> label
            {
                const auto it = crossings.find(edgeKey(a, b));
                return it == crossings.end() ? -1 : it->second;
            };
            auto region = [&](const std::vector<label>&) -> label { return -1; };
            passArgs args;
            args.capBoundary = false; args.capZone = -1; args.eligible = &eligible;
            args.onIsA = false; args.countRank = myRank;
            label split = 0, fallback = 0, caps = 0;
            clipPass(m, side, crossing, existing, region, args, split, fallback, caps);
            total += split;
        }
    }
    return total;
}


Foam::label Foam::dcCartCutCells::refineThinAxisBoxes
(
    cutMesh& m, const boolList& protectedPatch, label myRank
)
{
    // Experiment only: preserve source vertices and physical polygons. A
    // coordinate key is never used to reconstruct the actual source plane.
    label total=0;
    label nThin=0,nProtected=0,nNotBox=0,nUnclean=0,nProposed=0;
    const bool currentStarGuard=getenv("DCCART_THIN_BOX_CURRENT_STAR_GUARD")!=nullptr;
    label nCurrentVeto=0,nExactReuse=0,nCleanFallback=0;
    std::ofstream guardEvidence;
    if (currentStarGuard && getenv("DCCART_CONE_EVIDENCE"))
    { guardEvidence.open("thin-current-star-rank"+std::to_string(myRank)+".jsonl");guardEvidence<<std::setprecision(17); }
    auto finitePoint=[](const point& p)
    { return std::isfinite(p.x()) && std::isfinite(p.y()) && std::isfinite(p.z()); };
    for (label round=0;round<5;++round)
    {
        rekeyPiecesByGeometry(m);
        std::vector<std::vector<label>> cf;
        buildCellFaces(m,cf);
        std::vector<bool> protectedPoint(m.points.size(),false);
        for (label f=0;f<label(m.faces.size());++f)
        {
            if (!m.faceAlive[f]) { continue; }
            const label o=m.owner[f],n=m.neighbour[f],p=m.patch[f];
            const bool coupled=p>=0 && p<protectedPatch.size() && protectedPatch[p];
            const bool remote=o>=0 && n>=0 && m.cellRank[o]!=m.cellRank[n];
            if (m.zone[f]>=0 || coupled || remote)
            { for (label v:m.faces[f]) { protectedPoint[v]=true; } }
        }
        // The point/face index is updated after each local clip, including
        // diagonal-neighbour edge splices. No stale round-start star is used.
        std::vector<std::vector<label>> pointFaces;
        if (currentStarGuard)
        {
            pointFaces.resize(m.points.size());
            for (label f=0;f<label(m.faces.size());++f) if (m.faceAlive[f])
            { for (label v:m.faces[f]) { pointFaces[v].push_back(f); } }
        }
        auto starOf=[&](label c,std::set<label>& faces,std::set<label>& points)
        {
            std::set<label> vertices,cells;
            for (label f:cf[c]) for (label v:m.faces[f]) { vertices.insert(v); }
            for (label v:vertices) for (label f:pointFaces[v])
            {
                if (!m.faceAlive[f]) { continue; }
                if (m.owner[f]>=0 && m.cellAlive[m.owner[f]]) { cells.insert(m.owner[f]); }
                if (m.neighbour[f]>=0 && m.cellAlive[m.neighbour[f]]) { cells.insert(m.neighbour[f]); }
            }
            for (label cell:cells) for (label f:cf[cell]) if (m.faceAlive[f])
            { faces.insert(f);for (label v:m.faces[f]) { points.insert(v); } }
        };
        // A predicted polygon must retain a writer-legal decomposition. This
        // is only a preflight; the complete stock checker still authorizes it.
        auto polygonPossible=[&](const std::vector<point>& input)->bool
        {
            if (input.size()<3) { return false; }
            std::vector<point> pts=input;std::vector<label> best;
            for (label reverse=0;reverse<2;++reverse) for (size_t k=0;k<pts.size();++k)
            {
                std::vector<label> order;
                for (size_t j=0;j<pts.size();++j)
                { order.push_back(label((k+(reverse?pts.size()-j:j))%pts.size())); }
                auto less=[&](const std::vector<label>& a,const std::vector<label>& b)
                {
                    for (size_t j=0;j<a.size();++j) for (direction d=0;d<3;++d)
                    { if (pts[a[j]][d]!=pts[b[j]][d]) { return pts[a[j]][d]<pts[b[j]][d]; } }
                    return false;
                };
                if (best.empty() || less(order,best)) { best=std::move(order); }
            }
            vector A;point C;faceAreaCentreOF(pts,best,A,C);
            const scalar area=mag(A);
            if (!finitePoint(A) || !finitePoint(C) || !(area>VSMALL)) { return false; }
            const vector normal=A/area;bool convex=true,simple=true;
            auto writable=[&](const std::vector<label>& ids)
            { vector nn;point centre;polygonNormalCentre(pts,ids,nn,centre);
              return finitePoint(nn) && finitePoint(centre) && mag(nn)>1e-12; };
            if (!writable(best)) { return false; }
            if (best.size()==3) { return true; }
            const scalar tol=1e-12*std::sqrt(area);
            for (size_t i=0;i<best.size();++i)
            {
                const point& a=pts[best[i]];const point& b=pts[best[(i+1)%best.size()]];
                const vector e=b-a;const scalar len=mag(e);
                if (!(len>1e-12) || !std::isfinite(len)) { return false; }
                for (label v:best) if (((e^(pts[v]-a))&normal)<-tol*len) { convex=false; }
            }
            auto onSegment=[&](const point& a,const point& b,const point& q)
            {
                const vector e=b-a;const scalar length=mag(e);
                const scalar along=((q-a)&e)/max(magSqr(e),VSMALL);
                return mag((e^(q-a))&normal)<=tol*length && along>=-1e-12 && along<=1+1e-12;
            };
            if (!convex) for (size_t i=0;i<best.size();++i) for (size_t j=i+1;j<best.size();++j)
            {
                const size_t in=(i+1)%best.size(),jn=(j+1)%best.size();
                if (in==j || jn==i) { continue; }
                const point& a=pts[best[i]];const point& b=pts[best[in]];
                const point& c=pts[best[j]];const point& d=pts[best[jn]];
                const scalar s1=((b-a)^(c-a))&normal,s2=((b-a)^(d-a))&normal;
                const scalar t1=((d-c)^(a-c))&normal,t2=((d-c)^(b-c))&normal;
                if ((s1*s2<0 && t1*t2<0) || onSegment(a,b,c) || onSegment(a,b,d)
                 || onSegment(c,d,a) || onSegment(c,d,b)) { simple=false; }
            }
            if (!simple) { return false; }
            for (size_t k=0;k<best.size();++k)
            {
                bool positive=true;
                for (size_t j=1;j+1<best.size();++j)
                {
                    const label a=best[k],b=best[(k+j)%best.size()],c=best[(k+j+1)%best.size()];
                    const scalar amount=(((pts[b]-pts[a])^(pts[c]-pts[a]))&normal)*.5;
                    if (!std::isfinite(amount) || amount<=1e-15*area || !writable({a,b,c})) { positive=false;break; }
                }
                if (positive) { return true; }
            }
            if (!convex) { return false; }
            const label centre=pts.size();pts.push_back(C);
            for (size_t j=0;j<best.size();++j)
            { if (!writable({best[j],best[(j+1)%best.size()],centre})) { return false; } }
            return true;
        };
        struct faceBefore { label f,o,n;std::vector<label> poly; };
        auto updateIndex=[&](const std::vector<faceBefore>& before,label firstNew)
        {
            auto remove=[](std::vector<label>& values,label f)
            { values.erase(std::remove(values.begin(),values.end(),f),values.end()); };
            for (const auto& old:before)
            {
                for (label v:old.poly) { remove(pointFaces[v],old.f); }
                if (old.o>=0) { remove(cf[old.o],old.f); }
                if (old.n>=0) { remove(cf[old.n],old.f); }
            }
            cf.resize(m.nCells);pointFaces.resize(m.points.size());protectedPoint.resize(m.points.size(),false);
            auto add=[&](label f)
            {
                if (!m.faceAlive[f]) { return; }
                const label o=m.owner[f],n=m.neighbour[f],p=m.patch[f];
                if (o>=0) { cf[o].push_back(f); }if (n>=0) { cf[n].push_back(f); }
                const bool protectedFace=m.zone[f]>=0 || (p>=0 && p<protectedPatch.size() && protectedPatch[p])
                    || (o>=0 && n>=0 && m.cellRank[o]!=m.cellRank[n]);
                for (label v:m.faces[f]) { pointFaces[v].push_back(f);if (protectedFace) { protectedPoint[v]=true; } }
            };
            for (const auto& old:before) { add(old.f); }
            for (label f=firstNew;f<label(m.faces.size());++f) { add(f); }
        };
        std::vector<uint64_t> mine;
        std::unordered_map<cellGid,label,cellGidHash> local;
        for (label c=0;c<m.nCells;++c)
        {
            if (!m.cellAlive[c]) { continue; }
            local.emplace(gidOf(m,c),c);
            if (m.cellRank[c]!=myRank) { continue; }
            boundBox bb(boundBox::invertedBox);
            std::set<label> vertices;
            bool finite=true,protectedCell=false;
            for (label f:cf[c]) { for (label v:m.faces[f])
            {
                vertices.insert(v);bb.add(m.points[v]);
                finite=finite && finitePoint(m.points[v]);
                protectedCell=protectedCell || protectedPoint[v];
            } }
            if (!finite || !bb.valid()) { continue; }
            const vector span=bb.span();
            const scalar longest=cmptMax(span),shortest=cmptMin(span);
            if (!(shortest>3e-6 && longest>4*shortest)) { continue; }
            ++nThin;
            if (protectedCell) { ++nProtected;continue; }
            const scalar tol=1e-12*longest;
            scalar vol=0;
            bool box=true;
            std::map<std::pair<label,label>,label> directed;
            for (label f:cf[c])
            {
                const auto& poly=m.faces[f];
                if (poly.size()<3) { box=false;break; }
                vector A;point C;faceAreaCentreOF(m.points,poly,A,C);
                if (!finitePoint(A) || !finitePoint(C) || !(mag(A)>1e-12)) { box=false;break; }
                const vector outward=(m.owner[f]==c?1:-1)*A;
                direction normal=0;
                for (direction d=1;d<3;++d) { if (mag(outward[d])>mag(outward[normal])) { normal=d; } }
                if (!(mag(outward[normal])>0)) { box=false;break; }
                for (direction d=0;d<3;++d)
                { if (d!=normal && mag(outward[d])>1e-12*mag(outward[normal])) { box=false; } }
                const scalar plane=outward[normal]>0?bb.max()[normal]:bb.min()[normal];
                for (label v:poly) { if (mag(m.points[v][normal]-plane)>tol) { box=false; } }
                for (label v:vertices)
                { if (((m.points[v]-C)&outward)>tol*mag(outward)) { box=false; } }
                // Convex support lines keep collinear source seam nodes.
                const vector unit=A/mag(A);
                for (size_t i=0;i<poly.size();++i)
                {
                    const label a=poly[i],b=poly[(i+1)%poly.size()];
                    const vector e=m.points[b]-m.points[a];
                    if (!(mag(e)>1e-12)) { box=false;break; }
                    for (label v:poly)
                    { if (((e^(m.points[v]-m.points[a]))&unit)<-tol*mag(e)) { box=false; } }
                    const auto edge=m.owner[f]==c?std::make_pair(a,b):std::make_pair(b,a);
                    ++directed[edge];
                }
                vol+=((C-bb.centre())&outward)/3;
                if (!box) { break; }
            }
            for (const auto& edge:directed)
            {
                const auto reverse=directed.find({edge.first.second,edge.first.first});
                if (edge.second!=1 || reverse==directed.end() || reverse->second!=1) { box=false; }
            }
            const scalar bboxVol=span.x()*span.y()*span.z();
            if (!std::isfinite(vol) || !(bboxVol>VSMALL) || mag(vol-bboxVol)>1e-10*bboxVol) { box=false; }
            if (!box) { ++nNotBox;continue; }
            direction axis=0;
            for (direction d=1;d<3;++d) { if (span[d]>span[axis]) { axis=d; } }
            std::set<label> clearanceFaces,clearancePoints;
            if (currentStarGuard) { starOf(c,clearanceFaces,clearancePoints); }
            else { clearancePoints=vertices; }
            auto clean=[&](scalar plane)
            {
                if (!std::isfinite(plane)) { return false; }
                const scalar share=min(plane-bb.min()[axis],bb.max()[axis]-plane)/span[axis];
                if (!(share>=.02)) { return false; }
                for (label v:clearancePoints)
                {
                    const scalar distance=mag(m.points[v][axis]-plane);
                    if (distance!=0 && distance<max(3e-6,.02*span[axis])) { return false; }
                }
                return true;
            };
            scalar plane=bb.centre()[axis],distance=GREAT;
            bool sourceSeam=false;
            for (label v:vertices)
            {
                const scalar candidate=m.points[v][axis];
                if (!(candidate>bb.min()[axis]+tol && candidate<bb.max()[axis]-tol) || !clean(candidate)) { continue; }
                const scalar delta=mag(candidate-bb.centre()[axis]);
                if (delta<distance || (delta==distance && candidate<plane))
                { plane=candidate;distance=delta;sourceSeam=true; }
            }
            if (!sourceSeam && !clean(plane))
            {
                bool found=false;
                if (currentStarGuard)
                {
                    std::set<scalar> coordinates{bb.min()[axis],bb.max()[axis]};
                    for (label v:clearancePoints)
                    { const scalar x=m.points[v][axis];if (x>bb.min()[axis] && x<bb.max()[axis]) { coordinates.insert(x); } }
                    scalar bestDistance=GREAT;auto previous=coordinates.begin();
                    for (auto next=std::next(previous);next!=coordinates.end();previous=next,++next)
                    {
                        const scalar x=*previous+(*next-*previous)*.5;
                        const scalar delta=mag(x-bb.centre()[axis]);
                        if (clean(x) && (!found || delta<bestDistance || (delta==bestDistance && x<plane)))
                        { found=true;plane=x;bestDistance=delta; }
                    }
                }
                if (!found) { ++nUnclean;continue; }++nCleanFallback;
            }
            const cellGid gid=gidOf(m,c);
            mine.push_back(gid.a);mine.push_back(gid.b);mine.push_back(axis);mine.push_back(bitsOf(plane));
            ++nProposed;
        }
        // Actual double planes and geometry-keyed cells define the common
        // collective order; all ranks enter, even with no local proposals.
        std::map<std::pair<label,scalar>,std::set<cellGid>> groups;
        for (const auto& words:gatherWords(mine))
        {
            for (size_t i=0;i+4<=words.size();i+=4)
            {
                const scalar plane=scalarOf(words[i+3]);
                if (!std::isfinite(plane)) { continue; }
                groups[{label(words[i+2]),plane}].insert({words[i],words[i+1]});
            }
        }
        if (groups.empty()) { break; }
        label roundSplits=0;
        for (const auto& group:groups)
        {
            const label axis=group.first.first;
            const scalar plane=group.first.second;
            std::vector<bool> eligible(m.nCells,false);
            bool any=false;
            for (const cellGid& gid:group.second)
            {
                const auto it=local.find(gid);
                if (it!=local.end()) { eligible[it->second]=true;any=true; }
            }
            // This collective veto precedes every local no-op. The original
            // GID identifies the retained cell slot; its CURRENT geometry is
            // rechecked after any earlier group's split/splice.
            std::set<cellGid> localVeto;std::set<label> starFaces,starPoints;
            std::map<label,std::string> vetoReason;std::map<label,label> witnessFace;
            std::map<label,cellGid> candidateGid;
            std::map<uint64_t,std::set<label>> edgeCells;
            std::unordered_map<uint64_t,label> exactReuse;
            std::unordered_map<uint64_t,point> predictedCrossing;
            std::vector<faceBefore> before;
            const label firstNew=m.faces.size();
            if (currentStarGuard)
            {
                for (const cellGid& gid:group.second)
                {
                    const auto it=local.find(gid);if (it==local.end()) { continue; }
                    const label c=it->second;candidateGid[c]=gid;
                    std::set<label> fs,ps;starOf(c,fs,ps);starFaces.insert(fs.begin(),fs.end());starPoints.insert(ps.begin(),ps.end());
                    boundBox current(boundBox::invertedBox);bool safe=m.cellAlive[c];
                    for (label f:cf[c]) for (label v:m.faces[f])
                    { current.add(m.points[v]);safe=safe && finitePoint(m.points[v]) && !protectedPoint[v]; }
                    const scalar span=current.valid()?current.span()[axis]:0;
                    const scalar share=span>0?min(plane-current.min()[axis],current.max()[axis]-plane)/span:0;
                    if (!(share>=.02)) { safe=false; }
                    for (label v:ps)
                    {
                        const scalar distance=mag(m.points[v][axis]-plane);
                        if (!finitePoint(m.points[v]) || (distance!=0 && distance<max(3e-6,.02*span))) { safe=false; }
                    }
                    if (!safe) { localVeto.insert(gid);vetoReason[c]="current_clearance_or_protection";continue; }
                    for (label f:cf[c]) for (size_t j=0;j<m.faces[f].size();++j)
                    {
                        const label a=m.faces[f][j],b=m.faces[f][(j+1)%m.faces[f].size()];
                        const scalar da=m.points[a][axis]-plane,db=m.points[b][axis]-plane;
                        if ((da<0 && db>0) || (da>0 && db<0)) { edgeCells[edgeKey(a,b)].insert(c); }
                    }
                }
                std::map<std::tuple<scalar,scalar,scalar>,label> original;
                for (label v:starPoints)
                { const point& p=m.points[v];original.emplace(std::make_tuple(p.x(),p.y(),p.z()),v); }
                for (const auto& edge:edgeCells)
                {
                    const label a=label(edge.first>>32),b=label(edge.first&0xffffffffu);
                    const scalar da=m.points[a][axis]-plane,db=m.points[b][axis]-plane;
                    point p=m.points[a]+(da/(da-db))*(m.points[b]-m.points[a]);p[axis]=plane;
                    predictedCrossing.emplace(edge.first,p);bool safe=finitePoint(p);
                    const auto old=original.find(std::make_tuple(p.x(),p.y(),p.z()));
                    if (old!=original.end()) { exactReuse[edge.first]=old->second; }
                    else for (label v:starPoints) if (mag(p-m.points[v])<=3e-6) { safe=false;break; }
                    if (!safe) for (label c:edge.second) { localVeto.insert(candidateGid.at(c));vetoReason[c]="intersection_near_unequal_original"; }
                }
                // Also reject distinct new intersections closer than the late
                // writer's point canon. Exact coordinates may share one node.
                for (auto a=predictedCrossing.begin();a!=predictedCrossing.end();++a)
                for (auto b=std::next(a);b!=predictedCrossing.end();++b)
                {
                    const point& pa=a->second;const point& pb=b->second;
                    if (pa!=pb && mag(pa-pb)<=3e-6)
                    { for (label c:edgeCells.at(a->first)) { localVeto.insert(candidateGid.at(c));vetoReason[c]="unequal_intersections_too_close"; }
                      for (label c:edgeCells.at(b->first)) { localVeto.insert(candidateGid.at(c));vetoReason[c]="unequal_intersections_too_close"; } }
                }
                std::vector<signed char> predictedSide(m.points.size());
                for (label v=0;v<label(m.points.size());++v)
                { const scalar d=m.points[v][axis]-plane;predictedSide[v]=d==0?0:(d<0?-1:1); }
                std::unordered_map<uint64_t,label> virtualLabels;std::vector<point> virtualPoints;
                for (const auto& edge:predictedCrossing)
                {
                    const auto old=exactReuse.find(edge.first);
                    if (old!=exactReuse.end()) { virtualLabels[edge.first]=old->second; }
                    else { virtualLabels[edge.first]=m.points.size()+virtualPoints.size();virtualPoints.push_back(edge.second); }
                }
                auto predictedPoint=[&](label v)->const point&
                { return v<label(m.points.size())?m.points[v]:virtualPoints[v-m.points.size()]; };
                auto checkPoly=[&](const std::vector<label>& poly)
                { std::vector<point> coords;for (label v:poly) { coords.push_back(predictedPoint(v)); }return polygonPossible(coords); };
                for (const auto& candidate:candidateGid)
                {
                    const label c=candidate.first;if (localVeto.count(candidate.second)) { continue; }
                    std::set<std::tuple<scalar,scalar,scalar>> capPositions;
                    for (label f:cf[c]) for (size_t j=0;j<m.faces[f].size();++j)
                    {
                        const label v=m.faces[f][j];const point& p=m.points[v];
                        if (p[axis]==plane) { capPositions.insert(std::make_tuple(p.x(),p.y(),p.z())); }
                        const auto crossing=predictedCrossing.find(edgeKey(v,m.faces[f][(j+1)%m.faces[f].size()]));
                        if (crossing!=predictedCrossing.end())
                        { const point& q=crossing->second;capPositions.insert(std::make_tuple(q.x(),q.y(),q.z())); }
                    }
                    std::vector<point> cap;point centre(point::zero);
                    for (const auto& pos:capPositions) { cap.emplace_back(std::get<0>(pos),std::get<1>(pos),std::get<2>(pos));centre+=cap.back(); }
                    if (!cap.empty()) { centre/=scalar(cap.size()); }
                    const direction u=(axis+1)%3,v=(axis+2)%3;
                    std::sort(cap.begin(),cap.end(),[&](const point& p,const point& q)
                    {
                        const scalar pa=std::atan2(p[v]-centre[v],p[u]-centre[u]),qa=std::atan2(q[v]-centre[v],q[u]-centre[u]);
                        if (pa!=qa) { return pa<qa; }
                        return std::make_tuple(p.x(),p.y(),p.z())<std::make_tuple(q.x(),q.y(),q.z());
                    });
                    if (!polygonPossible(cap)) { localVeto.insert(candidate.second);vetoReason[c]="predicted_cap_writer_fan"; }
                }
                for (label f:starFaces)
                {
                    std::set<label> contributors;const auto& poly=m.faces[f];
                    for (size_t j=0;j<poly.size();++j)
                    { const auto found=edgeCells.find(edgeKey(poly[j],poly[(j+1)%poly.size()]));
                      if (found!=edgeCells.end()) { contributors.insert(found->second.begin(),found->second.end()); } }
                    const bool touched=(m.owner[f]>=0 && eligible[m.owner[f]]) || (m.neighbour[f]>=0 && eligible[m.neighbour[f]]);
                    if (!touched && contributors.empty()) { continue; }
                    if (touched)
                    { if (candidateGid.count(m.owner[f])) { contributors.insert(m.owner[f]); }
                      if (candidateGid.count(m.neighbour[f])) { contributors.insert(m.neighbour[f]); } }
                    bool safe=true;
                    if (touched)
                    {
                        auto crossing=[&](label a,label b)->label
                        { if (predictedSide[a]==0 || predictedSide[b]==0) { return -2; }
                          const auto it=virtualLabels.find(edgeKey(a,b));return it==virtualLabels.end()?-1:it->second; };
                        const splitResult result=splitPolygon(poly,predictedSide,crossing,false);
                        if (result.failed) { safe=false; }
                        else if (result.split) { safe=checkPoly(result.polyA) && checkPoly(result.polyB); }
                        else { safe=checkPoly(poly); }
                    }
                    else
                    {
                        std::vector<label> expanded;
                        for (size_t j=0;j<poly.size();++j)
                        { expanded.push_back(poly[j]);const auto it=virtualLabels.find(edgeKey(poly[j],poly[(j+1)%poly.size()]));
                          if (it!=virtualLabels.end() && it->second!=poly[j] && it->second!=poly[(j+1)%poly.size()]) { expanded.push_back(it->second); } }
                        safe=checkPoly(expanded);
                    }
                    if (!safe) for (label c:contributors)
                    { localVeto.insert(candidateGid.at(c));vetoReason[c]="predicted_face_writer_fan";witnessFace[c]=f; }
                }
                std::vector<uint64_t> vetoWords;
                for (const cellGid& gid:localVeto) { vetoWords.push_back(gid.a);vetoWords.push_back(gid.b); }
                std::set<cellGid> globalVeto;
                for (const auto& words:gatherWords(vetoWords)) for (size_t j=0;j+2<=words.size();j+=2)
                { globalVeto.insert({words[j],words[j+1]}); }
                for (const cellGid& gid:globalVeto)
                { const auto it=local.find(gid);if (it!=local.end()) { eligible[it->second]=false;++nCurrentVeto; } }
                any=false;for (const auto& candidate:candidateGid)
                {
                    const label c=candidate.first;any=any || eligible[c];
                    if (guardEvidence)
                    {
                        const auto reason=vetoReason.find(c);const auto face=witnessFace.find(c);
                        guardEvidence<<"{\"round\":"<<round<<",\"cell\":"<<c<<",\"gidA\":"<<candidate.second.a
                            <<",\"gidB\":"<<candidate.second.b<<",\"axis\":"<<axis<<",\"plane\":"<<plane
                            <<",\"globalVeto\":"<<(!eligible[c]?"true":"false")<<",\"reason\":\""
                            <<(reason==vetoReason.end()?(!eligible[c]?"remote_veto":"eligible"):reason->second)
                            <<"\",\"witnessFace\":"<<(face==witnessFace.end()?-1:face->second)
                            <<",\"groupStarPoints\":"<<starPoints.size()<<",\"groupStarFaces\":"<<starFaces.size()<<"}\n";
                    }
                }
                if (any) for (label f:starFaces) { before.push_back({f,m.owner[f],m.neighbour[f],m.faces[f]}); }
            }
            if (!any) { continue; }
            std::vector<signed char> side(m.points.size());
            for (label p=0;p<label(m.points.size());++p)
            {
                const scalar delta=m.points[p][axis]-plane;
                side[p]=delta==0?0:(delta<0?-1:1);
            }
            std::unordered_map<uint64_t,label> crossings;
            auto crossing=[&](label a,label b)->label
            {
                const uint64_t key=edgeKey(a,b);
                const auto it=crossings.find(key);
                if (it!=crossings.end()) { return it->second; }
                if (side[a]==0 || side[b]==0) { return -2; }
                const scalar da=m.points[a][axis]-plane,db=m.points[b][axis]-plane;
                point p=m.points[a]+(da/(da-db))*(m.points[b]-m.points[a]);
                p[axis]=plane;
                if (currentStarGuard)
                {
                    // The preflight uses the canonical original edge endpoint
                    // order. Consume those exact new coordinates; reversing
                    // arithmetic must not create another ULP-different node.
                    const auto predicted=predictedCrossing.find(key);
                    if (predicted==predictedCrossing.end()) { return -1; }
                    p=predicted->second;
                }
                if (currentStarGuard)
                {
                    const auto old=exactReuse.find(key);
                    if (old!=exactReuse.end()) { crossings[key]=old->second;++nExactReuse;return old->second; }
                    for (const auto& existing:crossings)
                    { if (m.points[existing.second]==p) { crossings[key]=existing.second;return existing.second; } }
                }
                const label id=m.points.size();m.points.push_back(p);side.push_back(0);crossings[key]=id;
                return id;
            };
            auto existing=[&](label a,label b)->label
            { const auto it=crossings.find(edgeKey(a,b));return it==crossings.end()?-1:it->second; };
            auto region=[&](const std::vector<label>&)->label { return -1; };
            passArgs args;args.capBoundary=false;args.capZone=-1;args.eligible=&eligible;
            args.onIsA=false;args.countRank=myRank;
            label split=0,fallback=0,caps=0;
            clipPass(m,side,crossing,existing,region,args,split,fallback,caps);
            total+=split;roundSplits+=split;
            if (currentStarGuard) { updateIndex(before,firstNew); }
        }
        // Never let a local no-op skip the next round's collectives.
        if (returnReduce(roundSplits,sumOp<label>())==0) { break; }
    }
    const label thin=returnReduce(nThin,sumOp<label>());
    const label prot=returnReduce(nProtected,sumOp<label>());
    const label notBox=returnReduce(nNotBox,sumOp<label>());
    const label unclean=returnReduce(nUnclean,sumOp<label>());
    const label proposed=returnReduce(nProposed,sumOp<label>());
    const label split=returnReduce(total,sumOp<label>());
    const label currentVeto=returnReduce(nCurrentVeto,sumOp<label>());
    const label exactReuse=returnReduce(nExactReuse,sumOp<label>());
    const label cleanFallback=returnReduce(nCleanFallback,sumOp<label>());
    if (Pstream::master())
    { Info<< "Thin axis-box experiment: thin "<<thin<<", protected "<<prot<<", nonbox "<<notBox
        <<", unclean "<<unclean<<", proposals "<<proposed<<", splits "<<split
        <<", current-star guard "<<currentStarGuard<<", current veto "<<currentVeto
        <<", exact original reuse "<<exactReuse<<", clean fallback "<<cleanFallback
        <<"; final stock allGeometry remains required"<<nl; }
    return total;
}


Foam::label Foam::dcCartCutCells::stitchOpenCellEdges(cutMesh& m)
{
    std::vector<std::vector<label>> cf;
    buildCellFaces(m, cf);
    std::unordered_map<uint64_t, std::vector<label>> inserts;
    for (label c = 0; c < m.nCells; ++c)
    {
        if (!m.cellAlive[c]) { continue; }
        std::unordered_map<uint64_t, label> usage;
        for (label f : cf[c])
        {
            const auto& poly = m.faces[f];
            for (size_t i = 0; i < poly.size(); ++i) { ++usage[edgeKey(poly[i], poly[(i + 1) % poly.size()])]; }
        }
        std::vector<label> vertices;
        for (const auto& edge : usage)
        {
            vertices.push_back(label(edge.first >> 32)); vertices.push_back(label(uint32_t(edge.first)));
        }
        std::sort(vertices.begin(), vertices.end());
        vertices.erase(std::unique(vertices.begin(), vertices.end()), vertices.end());
        for (const auto& edge : usage)
        {
            if (edge.second != 1) { continue; }
            const label a = label(edge.first >> 32), b = label(uint32_t(edge.first));
            const point& p = m.points[a];
            const vector ab = m.points[b] - p;
            const scalar l2 = magSqr(ab);
            if (l2 < 1e-20) { continue; }
            for (label v : vertices)
            {
                if (v == a || v == b) { continue; }
                const vector av = m.points[v] - p;
                const scalar t = (av & ab) / l2;
                if (t <= 1e-9 || t >= 1 - 1e-9 || magSqr(av - t * ab) > 1e-16) { continue; }
                inserts[edge.first].push_back(v);
            }
        }
    }
    if (inserts.empty()) { return 0; }
    for (auto& entry : inserts)
    {
        const point& a = m.points[label(entry.first >> 32)];
        auto& ids = entry.second;
        std::sort(ids.begin(), ids.end(), [&](label x, label y)
        {
            const scalar dx = magSqr(m.points[x] - a), dy = magSqr(m.points[y] - a);
            return dx == dy ? x < y : dx < dy;
        });
        ids.erase(std::unique(ids.begin(), ids.end()), ids.end());
    }
    label changed = 0;
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        auto& poly = m.faces[f];
        std::vector<label> out;
        for (size_t i = 0; i < poly.size(); ++i)
        {
            const label a = poly[i], b = poly[(i + 1) % poly.size()];
            out.push_back(a);
            const auto it = inserts.find(edgeKey(a, b));
            if (it == inserts.end()) { continue; }
            const auto& ids = it->second;
            if (a < b) { out.insert(out.end(), ids.begin(), ids.end()); }
            else { out.insert(out.end(), ids.rbegin(), ids.rend()); }
        }
        if (out.size() != poly.size()) { poly.swap(out); ++changed; }
    }
    return changed;
}


Foam::label Foam::dcCartCutCells::dropHangingFaceCorners
(
    cutMesh& m,
    const labelList& procPatch
)
{
    // A cut face that keeps a corner of the cell it was cut from -- a corner
    // none of the cell's other faces shares -- leaves TWO edges used once, so
    // the cell's face loop does not close: checkMesh's "open cells" and
    // "max cell openness", plus one of its "highly skew faces", because the
    // out-of-place corner drags the face centre off the centre-to-centre
    // line. DAML-Testbed's duct riser does this at every base size tried
    // (0.193 aside): the riser's cap is very nearly horizontal, so it grazes
    // a cell's bottom grid plane, and the clip that cuts the cap's corner
    // off the bottom face leaves the bottom's FAR corner in the cap's loop --
    // a 1.4 mm2 corner triangle stretched into a 70 mm2 non-planar quad laid
    // over the face it was cut from. Dropping that one corner restores the
    // loop, the plane and the cell's volume balance at once.
    //
    // stitchOpenCellEdges cannot reach this: it repairs a used-once edge by
    // INSERTING a vertex lying exactly on it, and here nothing lies on the
    // two dangling edges. This is the mirror repair, and it is taken only
    // when all three of the corner's edges are used exactly once -- so the
    // shortcut between the corner's two neighbours already exists and
    // becomes the shared edge the two dangling ones were missing -- and only
    // when it makes the cell measurably smaller in |sum(Sf)|, the very
    // quantity checkMesh calls openness. A face is never trimmed unless the
    // cell closes by it.
    //
    // Processor-patch faces are left alone: they are matched face-for-face
    // with another rank, and a rank interface is not where a clip goes wrong.
    label firstProcPatch = labelMax;
    for (const label pp : procPatch)
    {
        if (pp >= 0) { firstProcPatch = min(firstProcPatch, pp); }
    }

    std::vector<std::vector<label>> cf;
    buildCellFaces(m, cf);

    label changed = 0;
    for (label c = 0; c < m.nCells; ++c)
    {
        if (!m.cellAlive[c]) { continue; }

        // A corner is dropped at a time and the cell re-measured, so a face
        // carrying two of them is repaired in two passes; the bound only
        // stops a pathological loop.
        for (label attempt = 0; attempt < 8; ++attempt)
        {
            std::unordered_map<uint64_t, label> usage;
            vector openness = vector::zero;
            for (label f : cf[c])
            {
                if (!m.faceAlive[f]) { continue; }
                const std::vector<label>& poly = m.faces[f];
                for (size_t i = 0; i < poly.size(); ++i)
                {
                    ++usage[edgeKey(poly[i], poly[(i + 1) % poly.size()])];
                }
                // A face's stored order points OUT of its owner, so this is
                // the sign that orients it out of cell c.
                vector n; point fc;
                polygonNormalCentre(m.points, poly, n, fc);
                openness += (m.owner[f] == c ? n : -n);
            }
            label nDangle = 0;
            for (const auto& kv : usage) { if (kv.second == 1) { ++nDangle; } }
            if (nDangle == 0) { break; }

            const scalar magBefore = mag(openness);
            label bestFace = -1;
            scalar bestMag = magBefore;
            std::vector<label> bestPoly;

            for (label f : cf[c])
            {
                if (!m.faceAlive[f] || m.neighbour[f] >= 0) { continue; }
                if (m.patch[f] < 0 || m.patch[f] >= firstProcPatch) { continue; }
                const std::vector<label>& poly = m.faces[f];
                const label n = label(poly.size());
                if (n < 4) { continue; }
                const scalar sgn = (m.owner[f] == c ? 1 : -1);
                vector nf; point pf;
                polygonNormalCentre(m.points, poly, nf, pf);
                for (label i = 0; i < n; ++i)
                {
                    const label prev = poly[(i + n - 1) % n];
                    const label v = poly[i];
                    const label next = poly[(i + 1) % n];
                    if (prev == next) { continue; }
                    const auto a = usage.find(edgeKey(prev, v));
                    const auto b = usage.find(edgeKey(v, next));
                    const auto s = usage.find(edgeKey(prev, next));
                    if (a == usage.end() || a->second != 1) { continue; }
                    if (b == usage.end() || b->second != 1) { continue; }
                    if (s == usage.end() || s->second != 1) { continue; }

                    std::vector<label> trial;
                    trial.reserve(n - 1);
                    for (label k = 0; k < n; ++k)
                    {
                        if (k != i) { trial.push_back(poly[k]); }
                    }
                    vector nt; point pt;
                    polygonNormalCentre(m.points, trial, nt, pt);
                    // Trimming a corner must not grow the face (it would mean
                    // the corner was a real notch, not a hanging one).
                    if (mag(nt) > mag(nf) + VSMALL) { continue; }
                    const vector open2 = openness - sgn * nf + sgn * nt;
                    if (!(mag(open2) < magBefore * (1 - 1e-6))) { continue; }
                    // The corner may only go if the triangle it spans is
                    // ALREADY covered by another face of this cell -- the
                    // duplicate-coverage case (a clip left a corner in a face
                    // that the face it was cut from already spans, so the
                    // corner's triangle is counted twice). Trimming area that
                    // nothing else covers punches a hole in the sheet: at
                    // meshBaseSize 0.193 DAML's R2_bottomswitch_bottom lost
                    // 39% of itself to a trim that merely lowered the cell's
                    // openness, and the coverage gate then (rightly) refused.
                    {
                        const point tc =
                            (m.points[prev] + m.points[v] + m.points[next]) / 3.0;
                        bool covered = false;
                        for (label g : cf[c])
                        {
                            if (g == f || !m.faceAlive[g]) { continue; }
                            const std::vector<label>& gp = m.faces[g];
                            vector gn; point gc;
                            polygonNormalCentre(m.points, gp, gn, gc);
                            const scalar ga = mag(gn);
                            if (ga < VSMALL) { continue; }
                            gn /= ga;
                            if (mag((tc - gc) & gn) > 1e-6) { continue; }
                            bool inside = true;
                            for (size_t q = 0; q < gp.size() && inside; ++q)
                            {
                                const vector e =
                                    m.points[gp[(q + 1) % gp.size()]] - m.points[gp[q]];
                                if (((e ^ (tc - m.points[gp[q]])) & gn) * sgn < -1e-9)
                                {
                                    inside = false;
                                }
                            }
                            if (inside) { covered = true; break; }
                        }
                        if (!covered) { continue; }
                    }
                    if (bestFace < 0 || mag(open2) < bestMag)
                    {
                        bestFace = f;
                        bestMag = mag(open2);
                        bestPoly.swap(trial);
                    }
                }
            }
            if (bestFace < 0) { break; }
            m.faces[bestFace].swap(bestPoly);
            ++changed;
        }
    }
    return changed;
}


Foam::label Foam::dcCartCutCells::collapseMicroFacePoints
(
    cutMesh& m,
    const settings& s,
    const labelList& procPatch
)
{
    const scalar microFraction = s.microFaceFraction;
    const scalar snapFraction = s.microSnapFraction;
    if (microFraction <= 0 || snapFraction <= 0) { return 0; }

    // An STL that is not quite flat leaves the cut with a face whose whole
    // extent is a modelling error: DAML-Testbed's duct riser has a cap that
    // dips 0.48 mm -- 1% of the cell -- below the grid plane it otherwise
    // lies in, and at meshBaseSize 0.17 the clip honours that dip with a
    // 1.4 mm2 triangle and a 0.29 mm2 sliver beside a 1783 mm2 bottom. Both
    // sit at the far corner of the cell, so checkMesh scores them 11.5
    // against a limit of 4 and the mesh is refused. The geometry cannot be
    // honoured at this cell size and should not be: the repair is to move the
    // one cut point that carries the dip onto the point 0.48 mm away, which
    // flattens the triangle into the bottom face it was cut from and takes
    // the sliver out entirely.
    //
    // Only a cell that ALREADY fails the skewness limit is touched, with a
    // margin: a mesh checkMesh passes keeps every point it had, so this is a
    // repair for a sub-cell modelling error and never a general reshape.
    // Every rank holds a 4-layer halo, so any rank that holds a point holds
    // every cell and face around it and reaches the same verdict; the
    // candidate cells are visited in global-id order, so the moves come out
    // the same on any rank count.
    const scalar skewTrigger = s.mergeMaxSkew + 0.5;

    // A point on a rank interface belongs to another rank's copy of the same
    // face too, and only this rank sees the cell that wants it moved: leave
    // every one of them exactly where it is.
    label firstProcPatch = labelMax;
    for (const label pp : procPatch)
    {
        if (pp >= 0) { firstProcPatch = min(firstProcPatch, pp); }
    }

    std::vector<std::vector<label>> cf;
    buildCellFaces(m, cf);

    // OpenFOAM's face geometry: checkMesh measures with the area-weighted
    // centre, not the vertex mean, and so does the skew below.
    std::vector<vector> geoA(m.faces.size());
    std::vector<point> geoC(m.faces.size());
    std::vector<char> geoOk(m.faces.size(), 0);
    auto faceGeo = [&](label f, vector& a, point& c)
    {
        if (!geoOk[f])
        {
            faceAreaCentreOF(m.points, m.faces[f], geoA[f], geoC[f]);
            geoOk[f] = 1;
        }
        a = geoA[f];
        c = geoC[f];
    };

    std::vector<label> pfOwner;   // point -> alive faces, flat
    std::vector<label> pfStart(m.points.size() + 1, 0);
    {
        for (label f = 0; f < label(m.faces.size()); ++f)
        {
            if (!m.faceAlive[f]) { continue; }
            for (label v : m.faces[f]) { ++pfStart[v + 1]; }
        }
        for (label p = 0; p < label(m.points.size()); ++p) { pfStart[p + 1] += pfStart[p]; }
        pfOwner.assign(pfStart.back(), -1);
        std::vector<label> cur(pfStart.begin(), pfStart.end() - 1);
        for (label f = 0; f < label(m.faces.size()); ++f)
        {
            if (!m.faceAlive[f]) { continue; }
            for (label v : m.faces[f]) { pfOwner[cur[v]++] = f; }
        }
    }
    // Faces added to a point's list by a move so far (kept separate so the
    // flat index above stays valid)
    std::unordered_map<label, std::vector<label>> pfExtra;

    std::vector<scalar> microAreaOf(m.nCells, 0);
    std::vector<label> cand;
    for (label c = 0; c < m.nCells; ++c)
    {
        if (!m.cellAlive[c]) { continue; }
        const scalar refVol = (c < label(m.cellRefVol.size()) ? m.cellRefVol[c] : 0);
        if (refVol <= 0) { continue; }
        const scalar cellSize = Foam::cbrt(refVol);
        const scalar microArea = microFraction * cellSize * cellSize;
        bool hasMicro = false;
        for (label f : cf[c])
        {
            if (!m.faceAlive[f]) { continue; }
            vector a; point cc;
            faceGeo(f, a, cc);
            if (mag(a) < microArea) { hasMicro = true; break; }
        }
        if (!hasMicro) { continue; }
        microAreaOf[c] = microArea;
        cand.push_back(c);
    }
    if (cand.empty()) { return 0; }

    std::sort
    (
        cand.begin(),
        cand.end(),
        [&](label a, label b)
        {
            if (m.cellLevel[a] != m.cellLevel[b]) { return m.cellLevel[a] < m.cellLevel[b]; }
            if (m.cellLeaf[a] != m.cellLeaf[b]) { return m.cellLeaf[a] < m.cellLeaf[b]; }
            return m.cellPiece[a] < m.cellPiece[b];
        }
    );

    //- The loop of `poly` with every `v` replaced by `w`, consecutive and
    //  wrapping duplicates gone
    auto rewired = [&](const std::vector<label>& poly, label v, label w)
    {
        std::vector<label> dd;
        dd.reserve(poly.size());
        for (label x : poly)
        {
            const label y = (x == v ? w : x);
            if (dd.empty() || dd.back() != y) { dd.push_back(y); }
        }
        while (dd.size() > 1 && dd.front() == dd.back()) { dd.pop_back(); }
        return dd;
    };

    const bool dbg = (getenv("DCCART_MICRO_DEBUG") != nullptr);
    label nSkew = 0, nPair = 0, nReject = 0;
    label nMoved = 0;
    for (label c : cand)
    {
        const scalar microArea = microAreaOf[c];
        if (microArea <= 0) { continue; }
        const scalar sk = boundarySkewT(m, cf, c, label(-1), faceGeo);
        if (sk <= skewTrigger) { continue; }
        ++nSkew;
        if (dbg && Pstream::myProcNo() == 0)
        {
            Info<< "micro: cell " << c << " (level " << m.cellLevel[c]
                << ", leaf " << m.cellLeaf[c] << ", piece " << m.cellPiece[c]
                << ") skew " << sk << " refVol " << m.cellRefVol[c]
                << " microArea " << microArea << nl;
        }

        const scalar snapDist = snapFraction * Foam::cbrt(m.cellRefVol[c]);
        std::vector<label> verts;
        for (label f : cf[c])
        {
            if (!m.faceAlive[f]) { continue; }
            for (label v : m.faces[f]) { verts.push_back(v); }
        }
        std::sort(verts.begin(), verts.end());
        verts.erase(std::unique(verts.begin(), verts.end()), verts.end());

        // The closest pair (a cut point of a micro face, any point of the
        // cell). A grid corner is never the one that moves: it is shared with
        // the whole background grid.
        label bestV = -1, bestW = -1;
        scalar bestD = snapDist;
        for (label f : cf[c])
        {
            if (!m.faceAlive[f]) { continue; }
            vector a; point cc;
            faceGeo(f, a, cc);
            if (mag(a) >= microArea) { continue; }
            for (label v : m.faces[f])
            {
                if (v < m.nGridPoints) { continue; }
                for (label w : verts)
                {
                    if (w == v) { continue; }
                    const scalar d = mag(m.points[v] - m.points[w]);
                    if (d <= 1e-12 || d > bestD) { continue; }
                    bestD = d; bestV = v; bestW = w;
                }
            }
        }
        if (bestV < 0)
        {
            if (dbg && Pstream::myProcNo() == 0)
            {
                Info<< "micro: cell " << c << " has no cut point within "
                    << snapDist << " m of another of its points" << nl;
            }
            continue;
        }
        ++nPair;

        std::vector<label> touched;
        touched.reserve(8);
        for (label i = pfStart[bestV]; i < pfStart[bestV + 1]; ++i)
        {
            if (pfOwner[i] >= 0) { touched.push_back(pfOwner[i]); }
        }
        const auto ex = pfExtra.find(bestV);
        if (ex != pfExtra.end())
        {
            for (label f : ex->second) { touched.push_back(f); }
        }

        // Trial the move over EVERY face the point carries, not just this
        // cell's. A face that survives may only be reshaped by a micro face's
        // worth of area -- or a quarter of its own, for a face already small
        // -- and one that collapses to nothing must itself be micro, so the
        // move can only ever take away a feature the cell was never going to
        // honour. Everything else is settled by measurement, not by a rule:
        // the cells around the point are checked before and after, and the
        // move is undone if any of them got worse. checkMesh's two facts
        // about a cell are that its face loop closes (every edge used twice)
        // and that its face area vectors sum to zero, so those are the two a
        // repair may not damage.
        bool ok = true;
        label nGone = 0;
        for (label f : touched)
        {
            if (!m.faceAlive[f]) { continue; }
            if (m.neighbour[f] < 0 && m.patch[f] >= firstProcPatch)
            {
                ok = false; break;
            }
            const std::vector<label> dd = rewired(m.faces[f], bestV, bestW);
            vector a0; point c0;
            faceGeo(f, a0, c0);
            if (dd.size() >= 3)
            {
                vector a1; point c1;
                faceAreaCentreOF(m.points, dd, a1, c1);
                const scalar allow = max(microArea, 0.25 * mag(a0));
                if (std::abs(mag(a1) - mag(a0)) > allow) { ok = false; break; }
                continue;
            }
            if (mag(a0) >= microArea) { ok = false; break; }
            ++nGone;
        }
        if (!ok || nGone == 0)
        {
            ++nReject;
            if (dbg && Pstream::myProcNo() == 0)
            {
                Info<< "micro: cell " << c << " move " << bestV << " ("
                    << m.points[bestV] << ") -> " << bestW << " ("
                    << m.points[bestW] << ") refused (ok=" << ok
                    << ", gone=" << nGone << ", touched=" << touched.size()
                    << ")" << nl;
                for (label f : touched)
                {
                    if (!m.faceAlive[f]) { continue; }
                    vector a0; point c0;
                    faceGeo(f, a0, c0);
                    const std::vector<label> dd = rewired(m.faces[f], bestV, bestW);
                    vector a1; point c1;
                    if (dd.size() >= 3) { faceAreaCentreOF(m.points, dd, a1, c1); }
                    Info<< "    f" << f << " nPts " << m.faces[f].size()
                        << " -> " << dd.size() << " A " << mag(a0) << " -> "
                        << (dd.size() >= 3 ? mag(a1) : 0)
                        << " neighbour " << m.neighbour[f]
                        << " patch " << m.patch[f] << nl;
                }
            }
            continue;
        }

        // The cells the move reaches: both sides of every face the point
        // carries. A collapsed face takes its two cells off each other's
        // adjacency list, which a polyMesh is perfectly happy about -- they
        // simply stop being neighbours -- as long as each still closes.
        std::vector<label> around;
        around.reserve(2 * touched.size());
        for (label f : touched)
        {
            if (!m.faceAlive[f]) { continue; }
            if (m.owner[f] >= 0 && m.cellAlive[m.owner[f]]) { around.push_back(m.owner[f]); }
            if (m.neighbour[f] >= 0 && m.cellAlive[m.neighbour[f]]) { around.push_back(m.neighbour[f]); }
        }
        std::sort(around.begin(), around.end());
        around.erase(std::unique(around.begin(), around.end()), around.end());

        auto cellFaults = [&](label x, scalar& openMag) -> label
        {
            std::unordered_map<uint64_t, label> usage;
            vector sum = vector::zero;
            for (label f : cf[x])
            {
                if (!m.faceAlive[f]) { continue; }
                const std::vector<label>& poly = m.faces[f];
                for (size_t i = 0; i < poly.size(); ++i)
                {
                    ++usage[edgeKey(poly[i], poly[(i + 1) % poly.size()])];
                }
                vector n; point fc;
                polygonNormalCentre(m.points, poly, n, fc);
                sum += (m.owner[f] == x ? n : -n);
            }
            label dangle = 0;
            for (const auto& kv : usage) { if (kv.second == 1) { ++dangle; } }
            openMag = mag(sum);
            return dangle;
        };
        std::vector<label> dangleBefore(around.size(), 0);
        std::vector<scalar> openBefore(around.size(), 0);
        for (size_t i = 0; i < around.size(); ++i)
        {
            dangleBefore[i] = cellFaults(around[i], openBefore[i]);
        }

        std::vector<std::vector<label>> savedLoop(touched.size());
        std::vector<char> savedAlive(touched.size(), 0);
        for (size_t i = 0; i < touched.size(); ++i)
        {
            savedLoop[i] = m.faces[touched[i]];
            savedAlive[i] = (m.faceAlive[touched[i]] ? 1 : 0);
        }
        const point savedP = m.points[bestV];

        for (label f : touched)
        {
            if (!m.faceAlive[f]) { continue; }
            const std::vector<label> dd = rewired(m.faces[f], bestV, bestW);
            geoOk[f] = 0;
            if (dd.size() < 3) { m.faceAlive[f] = false; continue; }
            m.faces[f] = dd;
        }
        m.points[bestV] = m.points[bestW];

        bool good = true;
        for (size_t i = 0; i < around.size() && good; ++i)
        {
            scalar openAfter = 0;
            const label dangleAfter = cellFaults(around[i], openAfter);
            if (dangleAfter > dangleBefore[i]) { good = false; }
            else if (openAfter > openBefore[i] + 1e-12) { good = false; }
        }
        if (!good)
        {
            for (size_t i = 0; i < touched.size(); ++i)
            {
                m.faces[touched[i]].swap(savedLoop[i]);
                m.faceAlive[touched[i]] = (savedAlive[i] != 0);
                geoOk[touched[i]] = 0;
            }
            m.points[bestV] = savedP;
            ++nReject;
            if (dbg && Pstream::myProcNo() == 0)
            {
                Info<< "micro: cell " << c << " move " << bestV << " -> "
                    << bestW << " reverted: a cell around the point did not"
                    << " stay closed (" << around.size() << " cells checked)"
                    << nl;
            }
            continue;
        }
        pfExtra.erase(bestV);
        for (label f : touched)
        {
            if (m.faceAlive[f]) { pfExtra[bestW].push_back(f); }
        }
        ++nMoved;
    }
    if (dbg)
    {
        Info<< "micro: candidates " << cand.size() << ", over the skew limit "
            << nSkew << ", with a movable point " << nPair << ", refused "
            << nReject << ", moved " << nMoved << " on rank "
            << Pstream::myProcNo() << nl;
    }
    return nMoved;
}


// * * * * * * * * * * * * * * repairThinWallCells * * * * * * * * * * * * * //

namespace
{

//- Centre and volume of one cell from a face list, the way OpenFOAM computes
//  them (primitiveMeshTools::makeCellCentresAndVols: the mean of the face
//  centres as the pyramid apex, then the volume-weighted pyramid centres).
//  The apex has to be the same one checkMesh uses or the y measured here is
//  not the y the wall function sees.
void cellCentreVolOF
(
    const Foam::dcCartCutCells::cutMesh& m,
    Foam::label cell,
    const std::vector<Foam::label>& fs,
    const std::vector<char>& faceOk,
    Foam::point& cen,
    Foam::scalar& vol
)
{
    using namespace Foam;
    point e = point::zero;
    label n = 0;
    for (label f : fs)
    {
        if (!faceOk[f]) { continue; }
        vector a; point c;
        faceAreaCentreOF(m.points, m.faces[f], a, c);
        e += c;
        ++n;
    }
    if (n) { e /= scalar(n); }
    point acc = point::zero;
    scalar v3 = 0;
    for (label f : fs)
    {
        if (!faceOk[f]) { continue; }
        vector a; point c;
        faceAreaCentreOF(m.points, m.faces[f], a, c);
        const scalar p3 = (m.owner[f] == cell) ? (a & (c - e)) : (a & (e - c));
        acc += p3 * (0.75 * c + 0.25 * e);
        v3 += p3;
    }
    vol = v3 / 3.0;
    cen = (mag(v3) > VSMALL) ? point(acc / v3) : e;
}

//- The smallest distance from a cell's own PLAIN wall face to that cell's
//  centre: the y every law-of-the-wall divides by, and the quantity
//  epsilonWallFunction's viscous branch eps = 2*k*nu/y^2 is unbounded in.
//  Baffle zones do not count (deleting one punches a hole in a physical sheet,
//  and createBaffles has not run yet so it is not a wall yet) and neither do
//  processor patches (another rank's face, and no law of the wall is applied
//  to it). `worstFace` receives the face it came from, or -1 when the cell has
//  no such face at all.
Foam::scalar minWallDistanceOf
(
    const Foam::dcCartCutCells::cutMesh& m,
    const std::vector<Foam::label>& fs,
    const std::vector<char>& faceOk,
    Foam::label firstProcPatch,
    const Foam::boolList& wallPatch,
    const Foam::point& cen,
    Foam::label& worstFace
)
{
    using namespace Foam;
    scalar best = GREAT;
    worstFace = -1;
    for (label f : fs)
    {
        if (!faceOk[f] || m.neighbour[f] >= 0) { continue; }
        if (m.zone[f] >= 0 || m.patch[f] < 0) { continue; }
        if (m.patch[f] >= firstProcPatch) { continue; }
        // only a plain wall carries a law of the wall: an inlet or an opening
        // may legitimately have its cell centre microns away
        if (m.patch[f] >= wallPatch.size() || !wallPatch[m.patch[f]]) { continue; }
        vector a; point fc;
        faceAreaCentreOF(m.points, m.faces[f], a, fc);
        const scalar am = mag(a);
        if (am < VSMALL) { continue; }
        const scalar y = std::abs(scalar((a / am) & (fc - cen)));
        if (y < best) { best = y; worstFace = f; }
    }
    return best;
}

} // End anonymous namespace


void Foam::dcCartCutCells::repairThinWallCells
(
    cutMesh& m,
    const settings& s,
    const labelList& procPatch,
    const boolList& wallPatch,
    report& r
)
{
    if (s.minWallCellDistance <= 0 || s.wallSliverVolumeFraction <= 0) { return; }

    label firstProcPatch = labelMax;
    for (const label pp : procPatch)
    {
        if (pp >= 0) { firstProcPatch = min(firstProcPatch, pp); }
    }
    const bool dbg = (getenv("DCCART_THINWALL_DEBUG") != nullptr);
    label nMerged = 0, nKilled = 0, nKept = 0, nStranded = 0;

    for (label round = 0; round < 4; ++round)
    {
        std::vector<std::vector<label>> cf;
        buildCellFaces(m, cf);
        std::vector<char> faceOk(m.faces.size(), 0);
        for (label f = 0; f < label(m.faces.size()); ++f)
        {
            faceOk[f] = (m.faceAlive[f] ? 1 : 0);
        }
        // Every alive cell's centre, once: a trial merge has to be scored the
        // way checkMesh will score it, and that needs the centres on the far
        // side of the union's faces too.
        std::vector<point> cellCen(m.nCells, point::zero);
        std::vector<scalar> cellVol(m.nCells, 0);
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!m.cellAlive[c]) { continue; }
            cellCentreVolOF(m, c, cf[c], faceOk, cellCen[c], cellVol[c]);
        }

        struct candidate { label cell; label face; scalar y; scalar floorY; };
        std::vector<candidate> list;
        for (label c = 0; c < m.nCells; ++c)
        {
            if (!m.cellAlive[c] || m.cellRefVol[c] <= 0) { continue; }
            point cen; scalar vol;
            cellCentreVolOF(m, c, cf[c], faceOk, cen, vol);
            if (vol <= VSMALL) { continue; }
            // Above this fraction of its own hex the cell is a RESOLVED thin
            // feature (a 1 mm panel in a 12.6 mm cell is 0.08) and is left
            // exactly as it is.
            if (vol >= s.wallSliverVolumeFraction * m.cellRefVol[c]) { continue; }
            label wf = -1;
            const scalar y = minWallDistanceOf
                (m, cf[c], faceOk, firstProcPatch, wallPatch, cen, wf);
            if (wf < 0) { continue; }
            const scalar dx = Foam::cbrt(m.cellRefVol[c]);
            const scalar floorY = min(s.minWallCellDistance, 0.25 * dx);
            if (y >= floorY) { continue; }
            // The two exemptions that stay: a baffle face (deleting the cell
            // punches a hole in a sheet) and a rank boundary (the other rank
            // would keep its copy of every face this one deletes). The
            // thin-closed-shell exemption killExtremeSlivers uses is
            // deliberately NOT applied here -- see the declaration.
            bool blocked = false;
            for (label f : cf[c])
            {
                if (!faceOk[f]) { continue; }
                if (m.zone[f] >= 0) { blocked = true; break; }
                if (m.neighbour[f] < 0 && m.patch[f] >= firstProcPatch)
                {
                    blocked = true; break;
                }
            }
            if (blocked) { ++nKept; continue; }
            list.push_back(candidate{c, wf, y, floorY});
        }
        if (list.empty()) { break; }
        // The order every rank agrees on without exchanging anything
        std::sort
        (
            list.begin(), list.end(),
            [&](const candidate& a, const candidate& b)
            {
                if (m.cellLevel[a.cell] != m.cellLevel[b.cell])
                {
                    return m.cellLevel[a.cell] < m.cellLevel[b.cell];
                }
                if (m.cellLeaf[a.cell] != m.cellLeaf[b.cell])
                {
                    return m.cellLeaf[a.cell] < m.cellLeaf[b.cell];
                }
                return m.cellPiece[a.cell] < m.cellPiece[b.cell];
            }
        );

        std::vector<char> used(m.nCells, 0);
        label did = 0;
        for (const candidate& k : list)
        {
            const label c = k.cell;
            if (used[c] || !m.cellAlive[c]) { continue; }

            // ---- the relaxed tier: merge AWAY from the wall --------------
            // The host is chosen by the wall face's normal, not by the shared
            // area, so the union grows out of the wall instead of along it and
            // y_P actually rises. That is AMReX MakeITracker's rule.
            vector wa; point wc;
            faceAreaCentreOF(m.points, m.faces[k.face], wa, wc);
            const scalar wam = mag(wa);
            const vector nWall =
                (wam < VSMALL) ? vector::zero
              : ((m.owner[k.face] == c) ? wa / wam : -wa / wam);
            std::vector<std::pair<scalar, label>> hosts;
            for (label g : cf[c])
            {
                if (!faceOk[g] || m.neighbour[g] < 0 || m.zone[g] >= 0) { continue; }
                if (m.patch[g] >= 0 && m.patch[g] >= firstProcPatch) { continue; }
                const label o = (m.owner[g] == c) ? m.neighbour[g] : m.owner[g];
                if (o < 0 || o == c || !m.cellAlive[o] || used[o]) { continue; }
                vector a; point cc;
                faceAreaCentreOF(m.points, m.faces[g], a, cc);
                const scalar am = mag(a);
                if (am < VSMALL) { continue; }
                const vector nOut = (m.owner[g] == c) ? a / am : -a / am;
                // 2 when the face points straight away from the wall, 0 when
                // straight into it; area only breaks near-ties
                hosts.emplace_back((1 - (nOut & nWall)) + 1e-6 * am, o);
            }
            std::sort
            (
                hosts.begin(), hosts.end(),
                [&](const std::pair<scalar, label>& x, const std::pair<scalar, label>& y)
                {
                    if (std::abs(x.first - y.first) > 1e-9) { return x.first > y.first; }
                    if (m.cellLevel[x.second] != m.cellLevel[y.second])
                    {
                        return m.cellLevel[x.second] < m.cellLevel[y.second];
                    }
                    if (m.cellLeaf[x.second] != m.cellLeaf[y.second])
                    {
                        return m.cellLeaf[x.second] < m.cellLeaf[y.second];
                    }
                    return m.cellPiece[x.second] < m.cellPiece[y.second];
                }
            );

            label bestHost = -1;
            for (const auto& hp : hosts)
            {
                const label h = hp.second;
                if (used[h] || !m.cellAlive[h]) { continue; }
                std::vector<label> aff;
                aff.reserve(cf[c].size() + cf[h].size());
                for (label g : cf[c]) { aff.push_back(g); }
                for (label g : cf[h]) { aff.push_back(g); }
                std::sort(aff.begin(), aff.end());
                aff.erase(std::unique(aff.begin(), aff.end()), aff.end());

                std::vector<label> savedO(aff.size()), savedN(aff.size());
                std::vector<char> savedA(aff.size(), 0);
                for (size_t i = 0; i < aff.size(); ++i)
                {
                    savedO[i] = m.owner[aff[i]];
                    savedN[i] = m.neighbour[aff[i]];
                    savedA[i] = faceOk[aff[i]];
                }
                const bool savedC = m.cellAlive[c];
                for (label g : aff)
                {
                    if (!faceOk[g]) { continue; }
                    const bool hasC = (m.owner[g] == c || m.neighbour[g] == c);
                    const bool hasH = (m.owner[g] == h || m.neighbour[g] == h);
                    if (hasC && hasH) { faceOk[g] = 0; m.faceAlive[g] = false; continue; }
                    if (!hasC) { continue; }
                    if (m.owner[g] == c) { m.owner[g] = h; } else { m.neighbour[g] = h; }
                    if (m.neighbour[g] >= 0 && m.owner[g] == m.neighbour[g])
                    {
                        faceOk[g] = 0; m.faceAlive[g] = false;
                    }
                }
                m.cellAlive[c] = false;

                std::vector<label> uni;
                uni.reserve(aff.size());
                for (label g : aff) { if (faceOk[g]) { uni.push_back(g); } }
                point nc; scalar nv;
                cellCentreVolOF(m, h, uni, faceOk, nc, nv);
                bool ok = (nv > VSMALL);
                if (ok)
                {
                    // checkMesh's other word on a reshaped cell: every face
                    // pyramid must keep its apex inside
                    for (label g : uni)
                    {
                        vector a; point fc;
                        faceAreaCentreOF(m.points, m.faces[g], a, fc);
                        const scalar sgn = (m.owner[g] == h) ? scalar(1) : scalar(-1);
                        if (sgn * (a & (fc - nc)) <= VSMALL) { ok = false; break; }
                    }
                }
                label stillBad = -1;
                scalar yAfter = GREAT;
                scalar worstSkew = 0;
                if (ok)
                {
                    yAfter = minWallDistanceOf
                        (m, uni, faceOk, firstProcPatch, wallPatch, nc, stillBad);
                    // the merge is worth doing only if it actually lifts the
                    // wall clear of the floor
                    ok = (stillBad < 0) || (yAfter >= k.floorY);
                }
                if (ok)
                {
                    // A merge only ever removes faces, but it MOVES a centre,
                    // and a narrow strip at the far corner of the union scores
                    // as badly under checkMesh as the one we started with --
                    // measured: without this the 20 merges took max skewness
                    // from 3.97 to 22.85 and "Mesh OK" to Failed. Score every
                    // face of the union exactly the way checkMesh does
                    // (primitiveMeshTools::faceSkewness for an internal face,
                    // boundaryFaceSkewness for a boundary one; a coupled face
                    // is skipped because checkMesh scores that against the
                    // real cell on the other side, which one rank cannot see).
                    for (label g : uni)
                    {
                        vector a; point fc;
                        faceAreaCentreOF(m.points, m.faces[g], a, fc);
                        const scalar am = mag(a);
                        if (am < VSMALL) { continue; }
                        const label og = m.owner[g], ng = m.neighbour[g];
                        const bool coupled =
                            (og != h && og >= 0 && og < m.nCells && !m.cellAlive[og])
                         || (ng < 0 && m.patch[g] >= 0 && m.patch[g] >= firstProcPatch);
                        if (coupled) { continue; }
                        const point oC = (og == h) ? nc : cellCen[og];
                        const vector Cpf = fc - oC;
                        vector dv;
                        scalar fd0;
                        if (ng >= 0 && ng < m.nCells && m.cellAlive[ng])
                        {
                            const point nC = (ng == h) ? nc : cellCen[ng];
                            dv = nC - oC;
                            fd0 = 0.2;
                        }
                        else
                        {
                            if (m.zone[g] < 0 && m.patch[g] >= 0
                             && m.patch[g] >= firstProcPatch) { continue; }
                            const vector nn = a / am;
                            dv = nn * (nn & Cpf);
                            fd0 = 0.4;
                        }
                        const vector sv =
                            Cpf - ((a & Cpf) / ((a & dv) + ROOTVSMALL)) * dv;
                        const scalar svm = mag(sv);
                        if (svm < ROOTVSMALL) { continue; }
                        const vector svHat = sv / (svm + ROOTVSMALL);
                        scalar fd = fd0 * mag(dv);
                        for (label v : m.faces[g])
                        {
                            fd = max(fd, mag(svHat & (m.points[v] - fc)));
                        }
                        worstSkew = max(worstSkew, svm / max(fd, ROOTVSMALL));
                    }
                    ok = (worstSkew <= s.mergeMaxSkewRelaxed);
                }

                for (size_t i = 0; i < aff.size(); ++i)
                {
                    m.owner[aff[i]] = savedO[i];
                    m.neighbour[aff[i]] = savedN[i];
                    faceOk[aff[i]] = savedA[i];
                    m.faceAlive[aff[i]] = (savedA[i] != 0);
                }
                m.cellAlive[c] = savedC;

                if (dbg)
                {
                    Pout<< "thinwall: cell " << c << " y " << k.y << " floor "
                        << k.floorY << " trial host " << h << " -> "
                        << (ok ? "ok" : "refused") << " yAfter " << yAfter
                        << " worstSkew " << worstSkew << " union vol " << nv << nl;
                }
                if (ok) { bestHost = h; break; }
            }

            if (bestHost >= 0)
            {
                // ---- apply the merge
                const label h = bestHost;
                std::vector<label> aff;
                aff.reserve(cf[c].size() + cf[h].size());
                for (label g : cf[c]) { aff.push_back(g); }
                for (label g : cf[h]) { aff.push_back(g); }
                std::sort(aff.begin(), aff.end());
                aff.erase(std::unique(aff.begin(), aff.end()), aff.end());
                for (label g : aff)
                {
                    if (!m.faceAlive[g]) { continue; }
                    const bool hasC = (m.owner[g] == c || m.neighbour[g] == c);
                    const bool hasH = (m.owner[g] == h || m.neighbour[g] == h);
                    if (hasC && hasH) { m.faceAlive[g] = false; faceOk[g] = 0; continue; }
                    if (!hasC) { continue; }
                    if (m.owner[g] == c) { m.owner[g] = h; } else { m.neighbour[g] = h; }
                    if (m.neighbour[g] >= 0 && m.owner[g] == m.neighbour[g])
                    {
                        m.faceAlive[g] = false; faceOk[g] = 0;
                    }
                }
                m.cellAlive[c] = false;
                cf[h].clear();
                for (label g : aff) { if (m.faceAlive[g]) { cf[h].push_back(g); } }
                cf[c].clear();
                used[c] = 1; used[h] = 1;
                for (label g : cf[h])
                {
                    const label o = (m.owner[g] == h) ? m.neighbour[g] : m.owner[g];
                    if (o >= 0 && o < m.nCells) { used[o] = 1; }
                }
                ++nMerged; ++did;
                if (dbg)
                {
                    Pout<< "thinwall: cell " << c << " (y " << k.y << ") merged"
                        << " away from the wall into " << h << nl;
                }
                continue;
            }

            // ---- no host lifts it clear: hand it to the solid -----------
            // The same action killExtremeSlivers takes, and the one every
            // surveyed code takes for a small cell it cannot place: FDS blocks
            // and logs, code_saturne disables, AMReX covers. The cell's own
            // boundary faces vanish and each face it shared with a live cell
            // becomes that cell's boundary face under the patch that dominated
            // the sliver, so the wall moves in by the sliver's thickness.
            //
            // OFF unless DCCART_THINWALL_KILL is set, and measured: on
            // DAML-Testbed the 31 kills this pass found took max skewness from
            // 3.97 to 22.85 (7 faces) and checkMesh from "Mesh OK" to Failed,
            // because each kill hands the neighbours a boundary face that is a
            // narrow strip at their far corner -- the same failure the
            // killVolumeFraction=0.005 revert produced at skew 151. Nothing
            // downstream can repair it: repairSkewFaces needs either a
            // coplanar same-patch partner to join or an internal face to merge
            // across, and a freshly exposed wall strip has neither. A merge
            // never creates a boundary face, only removes one, so merging is
            // the safe half of this and killing is not -- yet.
            if (getenv("DCCART_THINWALL_KILL") == nullptr)
            {
                used[c] = 1;
                ++nStranded;
                continue;
            }
            std::map<label, scalar> patchArea;
            for (label f : cf[c])
            {
                if (!m.faceAlive[f] || m.neighbour[f] >= 0 || m.patch[f] < 0) { continue; }
                vector a; point cc;
                faceAreaCentreOF(m.points, m.faces[f], a, cc);
                patchArea[m.patch[f]] += mag(a);
            }
            if (patchArea.empty()) { used[c] = 1; ++nKept; continue; }
            label dom = -1;
            scalar domA = -1;
            for (const auto& kv : patchArea)
            {
                if (kv.second > domA) { domA = kv.second; dom = kv.first; }
            }
            for (label f : cf[c])
            {
                if (!m.faceAlive[f] || (m.owner[f] != c && m.neighbour[f] != c)) { continue; }
                if (m.neighbour[f] < 0) { m.faceAlive[f] = false; faceOk[f] = 0; continue; }
                if (m.owner[f] == c)
                {
                    std::reverse(m.faces[f].begin(), m.faces[f].end());
                    m.owner[f] = m.neighbour[f];
                }
                m.neighbour[f] = -1;
                m.patch[f] = dom;
                m.zone[f] = -1;
            }
            m.cellAlive[c] = false;
            cf[c].clear();
            used[c] = 1;
            ++nKilled; ++did;
            if (dbg)
            {
                Pout<< "thinwall: cell " << c << " (y " << k.y << " < floor "
                    << k.floorY << ", vol " << k.y << ") handed to the solid"
                    << " under patch " << dom << nl;
            }
        }
        if (!did) { break; }
    }

    r.nThinWallMerged += returnReduce(nMerged, sumOp<label>());
    r.nThinWallKilled += returnReduce(nKilled, sumOp<label>());
    r.nThinWallKept += returnReduce(nKept + nStranded, sumOp<label>());
    if (r.nThinWallMerged || r.nThinWallKilled || nStranded)
    {
        Info<< "Cut cells: thin wall slivers — merged " << r.nThinWallMerged
            << " away from the wall, handed " << r.nThinWallKilled
            << " to the solid, left " << returnReduce(nStranded, sumOp<label>())
            << " stranded (no host lifts them clear), kept " << r.nThinWallKept
            << " (baffle / rank boundary)" << nl;
    }
}


void Foam::dcCartCutCells::meshQualityCensus
(
    const cutMesh& m,
    const labelList& procPatch,
    const boolList& wallPatch,
    const scalar thinFloor,
    report& r
)
{
    label firstProcPatch = labelMax;
    for (const label pp : procPatch)
    {
        if (pp >= 0) { firstProcPatch = min(firstProcPatch, pp); }
    }
    std::vector<std::vector<label>> cf;
    buildCellFaces(m, cf);
    const label nF = label(m.faces.size());
    std::vector<char> faceOk(m.faces.size(), 0);
    std::vector<vector> fA(m.faces.size(), vector::zero);
    std::vector<point> fC(m.faces.size(), point::zero);
    scalar minA = GREAT;
    for (label f = 0; f < nF; ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        faceOk[f] = 1;
        faceAreaCentreOF(m.points, m.faces[f], fA[f], fC[f]);
        minA = min(minA, mag(fA[f]));
    }
    // a boundary face this rank owns outright (not another rank's)
    auto ownBoundary = [&](label f) -> bool
    {
        return m.neighbour[f] < 0 && m.patch[f] >= 0 && m.patch[f] < firstProcPatch;
    };
    scalar minY = GREAT, minFrac = 1, minYb = GREAT, minYo = GREAT, minT = GREAT;
    label nThin1 = 0, nThin = 0, nThinB = 0, nThinI = 0;
    // Per LOCAL patch index: the patch list grows per rank (processor and
    // processorCyclic patches), so this is never combined across ranks by
    // index -- the caller gathers (name, count) pairs instead.
    labelList perPatch(wallPatch.size(), 0);
    for (label c = 0; c < m.nCells; ++c)
    {
        if (!m.cellAlive[c]) { continue; }
        point cen; scalar vol;
        cellCentreVolOF(m, c, cf[c], faceOk, cen, vol);
        if (vol <= VSMALL) { continue; }
        if (m.cellRefVol[c] > 0)
        {
            minFrac = min(minFrac, vol / m.cellRefVol[c]);
        }
        label wf = -1;
        const scalar y = minWallDistanceOf(m, cf[c], faceOk, firstProcPatch, wallPatch, cen, wf);
        if (wf >= 0) { minY = min(minY, y); }

        // the other boundary classes, and the slab thickness t = V / A_max
        scalar aMax = 0, bigA = 0;
        label bigPatch = -1;
        bool hasZone = false;
        for (label f : cf[c])
        {
            if (!faceOk[f]) { continue; }
            const scalar am = mag(fA[f]);
            aMax = max(aMax, am);
            if (m.zone[f] >= 0)
            {
                // a baffle sheet: internal until split, a wall after
                hasZone = true;
                if (am > VSMALL)
                {
                    minYb = min(minYb, std::abs(scalar((fA[f] / am) & (fC[f] - cen))));
                }
                continue;
            }
            if (!ownBoundary(f)) { continue; }
            if (am > bigA) { bigA = am; bigPatch = m.patch[f]; }
            const bool wall = (m.patch[f] < wallPatch.size() && wallPatch[m.patch[f]]);
            if (!wall && am > VSMALL)
            {
                minYo = min(minYo, std::abs(scalar((fA[f] / am) & (fC[f] - cen))));
            }
        }
        if (aMax <= VSMALL) { continue; }
        const scalar t = vol / aMax;
        minT = min(minT, t);
        if (t < 1e-3) { ++nThin1; }
        if (thinFloor > 0 && t < thinFloor)
        {
            ++nThin;
            if (hasZone) { ++nThinB; }
            if (bigPatch >= 0 && bigPatch < perPatch.size()) { ++perPatch[bigPatch]; }
            else if (!hasZone) { ++nThinI; }
        }
    }
    reduce(minY, minOp<scalar>());
    reduce(minFrac, minOp<scalar>());
    reduce(minA, minOp<scalar>());
    reduce(minYb, minOp<scalar>());
    reduce(minYo, minOp<scalar>());
    reduce(minT, minOp<scalar>());
    reduce(nThin1, sumOp<label>());
    reduce(nThin, sumOp<label>());
    reduce(nThinB, sumOp<label>());
    reduce(nThinI, sumOp<label>());
    r.minWallDistance = minY;
    r.minVolumeFractionSeen = minFrac;
    r.minFaceAreaSeen = minA;
    r.minBaffleDistance = minYb;
    r.minOpenDistance = minYo;
    r.thinFloor = thinFloor;
    r.minThickness = minT;
    r.nThinCells1mm = nThin1;
    r.nThinCells = nThin;
    r.nThinAtBaffle = nThinB;
    r.nThinInterior = nThinI;
    r.thinCellsPerPatch = perPatch;
}
Foam::label Foam::dcCartCutCells::repairSkewFaces
(
    cutMesh& m,
    const settings& s,
    const labelList& procPatch,
    const boolList& coupledPatch
)
{
    if (s.mergeMaxSkew <= 0) { return 0; }
    // Repair borderline faces as well: write-out changes face accumulation
    // order by a few ULPs. The external checkMesh gate stays strict at 4;
    // this smaller target gives repaired cells a numerical safety margin.
    const scalar limit = min(s.mergeMaxSkew, scalar(4) - scalar(1e-8));

    // A processor face is another rank's face as well: joining it would leave
    // the two ranks holding different polygons, and merging it away would
    // leave the neighbour rank pointing at a cell this rank no longer has.
    label firstProcPatch = labelMax;
    for (const label pp : procPatch)
    {
        if (pp >= 0) { firstProcPatch = min(firstProcPatch, pp); }
    }
    const bool dbg = (getenv("DCCART_SKEW_DEBUG") != nullptr);

    const label nFc = label(m.faces.size());
    std::vector<vector> geoA(m.faces.size());
    std::vector<point> geoC(m.faces.size());
    std::vector<char> geoOk(m.faces.size(), 0);
    auto faceGeo = [&](label f, vector& a, point& c)
    {
        if (!geoOk[f])
        {
            faceAreaCentreOF(m.points, m.faces[f], geoA[f], geoC[f]);
            geoOk[f] = 1;
        }
        a = geoA[f];
        c = geoC[f];
    };

    // The order every rank agrees on without exchanging anything: the halo is
    // gone by now, so each cell is one rank's alone, and (level, leaf, piece)
    // names it the same everywhere (see collapseMicroFacePoints).
    auto gidLess = [&](label a, label b) -> bool
    {
        if (m.cellLevel[a] != m.cellLevel[b]) { return m.cellLevel[a] < m.cellLevel[b]; }
        if (m.cellLeaf[a] != m.cellLeaf[b]) { return m.cellLeaf[a] < m.cellLeaf[b]; }
        return m.cellPiece[a] < m.cellPiece[b];
    };

    label nJoined = 0, nMerged = 0, nUnited = 0;

    for (label round = 0; round < 10; ++round)
    {
        std::vector<std::vector<label>> cf;
        buildCellFaces(m, cf);

        // ---- cell centres and volumes exactly as checkMesh computes them
        //  (primitiveMeshTools::makeCellCentresAndVols: the mean of the face
        //  centres as the pyramid apex, then the volume-weighted pyramid
        //  centres). A repair is only worth anything if it moves the number
        //  checkMesh reports, so it has to measure the same way.
        std::vector<point> est(m.nCells, point::zero);
        std::vector<label> nFacesOf(m.nCells, 0);
        for (label f = 0; f < nFc; ++f)
        {
            if (!m.faceAlive[f]) { continue; }
            vector a; point c;
            faceGeo(f, a, c);
            const label o = m.owner[f], n = m.neighbour[f];
            if (o >= 0 && o < m.nCells) { est[o] += c; ++nFacesOf[o]; }
            if (n >= 0 && n < m.nCells) { est[n] += c; ++nFacesOf[n]; }
        }
        for (label c = 0; c < m.nCells; ++c)
        {
            if (nFacesOf[c]) { est[c] /= scalar(nFacesOf[c]); }
        }
        std::vector<point> cC(m.nCells, point::zero);
        std::vector<scalar> cV(m.nCells, 0);
        for (label f = 0; f < nFc; ++f)
        {
            if (!m.faceAlive[f]) { continue; }
            vector a; point c;
            faceGeo(f, a, c);
            const label o = m.owner[f], n = m.neighbour[f];
            if (o >= 0 && o < m.nCells)
            {
                const scalar p3 = (a & (c - est[o]));
                cC[o] += p3 * (0.75 * c + 0.25 * est[o]);
                cV[o] += p3;
            }
            if (n >= 0 && n < m.nCells)
            {
                const scalar p3 = (a & (est[n] - c));
                cC[n] += p3 * (0.75 * c + 0.25 * est[n]);
                cV[n] += p3;
            }
        }
        for (label c = 0; c < m.nCells; ++c)
        {
            if (mag(cV[c]) > VSMALL) { cC[c] /= cV[c]; }
            else { cC[c] = est[c]; }
            cV[c] /= 3.0;
        }

        // ---- checkMesh's own skewness, against the centres above
        auto skewWith = [&](label f, const point& ownC, const point& neiC, const bool internal) -> scalar
        {
            const std::vector<label>& poly = m.faces[f];
            if (poly.size() < 3) { return 0; }
            vector a; point fc;
            faceGeo(f, a, fc);
            const scalar am = mag(a);
            if (am < VSMALL) { return 0; }
            const vector Cpf = fc - ownC;
            vector d;
            scalar fd0;
            if (internal) { d = neiC - ownC; fd0 = 0.2; }
            else { const vector nn = a / am; d = nn * (nn & Cpf); fd0 = 0.4; }
            const vector sv = Cpf - ((a & Cpf) / ((a & d) + ROOTVSMALL)) * d;
            const scalar svm = mag(sv);
            if (svm < ROOTVSMALL) { return 0; }
            const vector svHat = sv / (svm + ROOTVSMALL);
            scalar fd = fd0 * mag(d);
            for (label v : poly) { fd = max(fd, mag(svHat & (m.points[v] - fc))); }
            return svm / max(fd, ROOTVSMALL);
        };
        auto skewOf = [&](label f) -> scalar
        {
            if (!m.faceAlive[f]) { return 0; }
            const label o = m.owner[f], n = m.neighbour[f];
            if (o < 0 || o >= m.nCells || !m.cellAlive[o]) { return 0; }
            // A coupled face is measured by checkMesh against the cell centre
            // on the OTHER side, which this rank does not have: leave it out
            // rather than score it against a mirror image it does not have
            // either. (polyMeshTools::faceSkewness -> swapBoundaryCellPositions)
            if (n < 0 && m.patch[f] >= 0
             && m.patch[f] < label(coupledPatch.size()) && coupledPatch[m.patch[f]])
            {
                return 0;
            }
            if (n >= 0 && n < m.nCells && m.cellAlive[n])
            {
                return skewWith(f, cC[o], cC[n], true);
            }
            return skewWith(f, cC[o], cC[o], false);
        };
        //- The same for one cell whose centre is not the cached one (a trial
        //  union, or a cell a join has just reshaped)
        auto skewWithCell = [&](label f, label x, const point& xc) -> scalar
        {
            const label o = m.owner[f], n = m.neighbour[f];
            const bool ownIsX = (o == x);
            const label other = ownIsX ? n : o;
            if (other >= 0 && other < m.nCells && other != x && m.cellAlive[other])
            {
                return ownIsX ? skewWith(f, xc, cC[other], true)
                              : skewWith(f, cC[other], xc, true);
            }
            return skewWith(f, xc, xc, false);
        };
        //- Centre and volume of an arbitrary face list of cell x
        auto cellCV = [&](label x, const std::vector<label>& fs, point& cen, scalar& vol)
        {
            point e = point::zero;
            label nf = 0;
            for (label f : fs) { vector a; point c; faceGeo(f, a, c); e += c; ++nf; }
            if (nf) { e /= scalar(nf); }
            point acc = point::zero;
            scalar v3 = 0;
            for (label f : fs)
            {
                vector a; point c;
                faceGeo(f, a, c);
                const scalar p3 = (m.owner[f] == x) ? (a & (c - e)) : (a & (e - c));
                acc += p3 * (0.75 * c + 0.25 * e);
                v3 += p3;
            }
            vol = v3 / 3.0;
            cen = (mag(v3) > VSMALL) ? point(acc / v3) : e;
        };

        //- checkMesh's other word on a reshaped cell: every face pyramid must
        //  have its apex on the inside (primitiveMeshTools::facePyramidVolume
        //  with minPyrVol -SMALL, i.e. the outward area vector must point away
        //  from the cell centre). A concave union can put the centre outside
        //  one of its own faces, and that is "faces are incorrectly oriented".
        auto pyramidsOk = [&](label x, const std::vector<label>& fs, const point& xc) -> bool
        {
            for (label f : fs)
            {
                if (!m.faceAlive[f]) { continue; }
                vector a; point fc;
                faceGeo(f, a, fc);
                const scalar sgn = (m.owner[f] == x) ? scalar(1) : scalar(-1);
                if (sgn * (a & (fc - xc)) <= VSMALL) { return false; }
            }
            return true;
        };

        // ---- what is over the limit, and who carries it
        std::vector<char> hasBad(m.nCells, 0);
        label nBad = 0;
        for (label f = 0; f < nFc; ++f)
        {
            if (!m.faceAlive[f] || skewOf(f) <= limit) { continue; }
            ++nBad;
            const label o = m.owner[f], n = m.neighbour[f];
            if (o >= 0 && o < m.nCells && m.cellAlive[o]) { hasBad[o] = 1; }
            if (n >= 0 && n < m.nCells && m.cellAlive[n]) { hasBad[n] = 1; }
        }
        if (!nBad) { break; }
        std::vector<label> cand;
        for (label c = 0; c < m.nCells; ++c) { if (hasBad[c]) { cand.push_back(c); } }
        std::sort(cand.begin(), cand.end(), gidLess);
        if (dbg)
        {
            Pout<< "skewrepair: round " << round << " rank " << Pstream::myProcNo()
                << ": " << nBad << " face(s) over " << limit << " in "
                << cand.size() << " cell(s)" << nl;
            for (label f = 0; f < nFc; ++f)
            {
                if (!m.faceAlive[f] || skewOf(f) <= limit) { continue; }
                Pout<< "    bad f " << f << " skew " << skewOf(f)
                    << " own " << m.owner[f] << " nei " << m.neighbour[f]
                    << " patch " << m.patch[f] << " zone " << m.zone[f]
                    << " nPts " << label(m.faces[f].size()) << nl;
            }
        }

        std::vector<char> used(m.nCells, 0);
        label repaired = 0;
        for (label c : cand)
        {
            if (used[c] || !m.cellAlive[c]) { continue; }
            std::vector<label> bad;
            for (label f : cf[c])
            {
                if (m.faceAlive[f] && skewOf(f) > limit) { bad.push_back(f); }
            }
            if (bad.empty()) { continue; }
            std::sort(bad.begin(), bad.end());

            for (label f : bad)
            {
                if (!m.faceAlive[f] || skewOf(f) <= limit) { continue; }

                // ---------- join: coplanar pieces of ONE cell's wall -------
                // A baffle wall is two faces of two cells; where the sheet
                // runs on past a cell boundary the pieces of one cell's side
                // are coplanar and touch, but no earlier pass may join them
                // because an internal face has one neighbour and the pieces
                // have two. Now that splitBaffleZones has made them walls of
                // one cell, they can be.
                if (m.neighbour[f] < 0)
                {
                    const label p = m.patch[f];
                    const label z = m.zone[f];
                    if (p < 0 || p >= firstProcPatch) { continue; }
                    // a coupled pair (cyclic, processor) is matched face for
                    // face with the other side: joining one only breaks it
                    if (p < label(coupledPatch.size()) && coupledPatch[p]) { continue; }
                    vector a1; point c1;
                    faceGeo(f, a1, c1);
                    const scalar m1 = mag(a1);
                    if (m1 < VSMALL) { continue; }
                    const vector n1 = a1 / m1;

                    label overBefore = 0;
                    for (label q : cf[c])
                    {
                        if (m.faceAlive[q] && skewWithCell(q, c, cC[c]) > limit) { ++overBefore; }
                    }

                    label bestG = -1, bestOver = overBefore;
                    std::vector<label> bestLoop;
                    for (label g : cf[c])
                    {
                        if (g == f || !m.faceAlive[g]) { continue; }
                        if (m.neighbour[g] >= 0 || m.patch[g] != p || m.zone[g] != z) { continue; }
                        vector a2; point c2;
                        faceGeo(g, a2, c2);
                        const scalar m2 = mag(a2);
                        if (m2 < VSMALL) { continue; }
                        if ((a1 & a2) < (1 - 1e-6) * m1 * m2) { continue; }
                        if (std::abs((c2 - c1) & n1) > 1e-6) { continue; }
                        std::vector<label> out;
                        if (!mergePolygons(m.points, m.faces[f], m.faces[g], out)) { continue; }
                        vector au; point cu;
                        polygonNormalCentre(m.points, out, au, cu);
                        // the union must stay simple: its area is the sum
                        if (std::abs(mag(au) - (m1 + m2)) > 1e-6 * (m1 + m2)) { continue; }

                        const std::vector<label> savedF = m.faces[f];
                        const bool savedG = m.faceAlive[g];
                        m.faces[f] = out;
                        m.faceAlive[g] = false;
                        geoOk[f] = 0;
                        std::vector<label> trial;
                        trial.reserve(cf[c].size());
                        for (label q : cf[c])
                        {
                            if (q != g && m.faceAlive[q]) { trial.push_back(q); }
                        }
                        point nc; scalar nv;
                        cellCV(c, trial, nc, nv);
                        label over = labelMax;
                        if (nv > VSMALL && pyramidsOk(c, trial, nc))
                        {
                            over = 0;
                            for (label q : trial)
                            {
                                if (skewWithCell(q, c, nc) > limit) { ++over; }
                            }
                        }
                        const bool keep = (over < bestOver);
                        m.faces[f].swap(const_cast<std::vector<label>&>(savedF));
                        m.faceAlive[g] = savedG;
                        geoOk[f] = 0;
                        if (keep) { bestOver = over; bestG = g; bestLoop = out; }
                    }
                    // An isolated small wall fragment has no coplanar
                    // partner in this cell. It still admits a safe centre
                    // repair by absorbing its owner into a fluid neighbour.
                    // Preserve every boundary face and require the same
                    // pyramid/skewness gates used below for internal faces.
                    if (bestG >= 0)
                    {
                        m.faces[f] = bestLoop;
                        m.faceAlive[bestG] = false;
                        geoOk[f] = 0;
                        std::vector<label> now;
                        now.reserve(cf[c].size());
                        for (label q : cf[c])
                        {
                            if (q != bestG && m.faceAlive[q]) { now.push_back(q); }
                        }
                        cf[c].swap(now);
                        cellCV(c, cf[c], cC[c], cV[c]);
                        used[c] = 1;
                        ++nJoined; ++repaired;
                        if (dbg)
                        {
                            Pout<< "skewrepair: cell " << c << " joined face " << f
                                << " + " << bestG << " (over " << overBefore << " -> "
                                << bestOver << ")" << nl;
                        }
                        break;
                    }
                }

                // ---------- unite the far side of a coplanar strip ---------
                // A plane face of a cell that the grid divides into several
                // coplanar pieces is scored by checkMesh piece by piece: a
                // narrow piece at the cell's far corner has a centre the
                // centre line misses, while the joined face -- same centre
                // line, its reach grown to the whole strip -- passes easily.
                // coalesceCoplanarFaces may not join the pieces because they
                // face different cells, and a face has one neighbour. Uniting
                // those two cells removes the objection: two pieces of one
                // plane separated by a grid line are neighbours across it, so
                // the union is one absorb away, and the pieces then join.
                {
                    bool united = false;
                    if (m.zone[f] >= 0) { united = true; }   // a baffle: leave it
                    label sides2[2] = { m.owner[f], m.neighbour[f] };
                    for (label si = 0; si < 2 && !united; ++si)
                    {
                        const label X = sides2[si];
                        const label N = sides2[1 - si];
                        if (X < 0 || N < 0 || !m.cellAlive[X] || !m.cellAlive[N]) { continue; }
                        if (used[X] || used[N]) { continue; }
                        vector aF; point cF;
                        faceGeo(f, aF, cF);
                        const scalar mF = mag(aF);
                        if (mF < VSMALL) { continue; }
                        const vector nF = ((m.owner[f] == X) ? aF : -aF) / mF;
                        std::set<uint64_t> fEdges;
                        {
                            const std::vector<label>& pf = m.faces[f];
                            for (size_t i = 0; i < pf.size(); ++i)
                            {
                                fEdges.insert(edgeKey(pf[i], pf[(i + 1) % pf.size()]));
                            }
                        }
                        for (label g : cf[X])
                        {
                            if (g == f || !m.faceAlive[g]) { continue; }
                            if (m.neighbour[g] < 0 || m.zone[g] >= 0) { continue; }
                            if
                            (
                                m.patch[g] >= 0
                             && m.patch[g] < label(coupledPatch.size())
                             && coupledPatch[m.patch[g]]
                            )
                            {
                                continue;
                            }
                            const label W = (m.owner[g] == X) ? m.neighbour[g] : m.owner[g];
                            if (W < 0 || W == N || W == X || !m.cellAlive[W] || used[W]) { continue; }
                            vector aG; point cG;
                            faceGeo(g, aG, cG);
                            const scalar mG = mag(aG);
                            if (mG < VSMALL) { continue; }
                            const vector nG = ((m.owner[g] == X) ? aG : -aG) / mG;
                            if ((nF & nG) < 1 - 1e-6) { continue; }
                            if (std::abs((cG - cF) & nF) > 1e-6) { continue; }
                            bool shares = false;
                            {
                                const std::vector<label>& pg = m.faces[g];
                                for (size_t i = 0; i < pg.size() && !shares; ++i)
                                {
                                    if (fEdges.count(edgeKey(pg[i], pg[(i + 1) % pg.size()])))
                                    {
                                        shares = true;
                                    }
                                }
                            }
                            if (!shares) { continue; }
                            // N and W have to be neighbours for one absorb to
                            // unite them, across a face that is not a baffle
                            // (that would take the sheet out) or a coupled one
                            bool bridge = false;
                            for (label q : cf[N])
                            {
                                if (!m.faceAlive[q] || m.neighbour[q] < 0) { continue; }
                                if (m.zone[q] >= 0) { continue; }
                                if
                                (
                                    m.patch[q] >= 0
                                 && m.patch[q] < label(coupledPatch.size())
                                 && coupledPatch[m.patch[q]]
                                )
                                {
                                    continue;
                                }
                                const label o = (m.owner[q] == N) ? m.neighbour[q] : m.owner[q];
                                if (o == W) { bridge = true; break; }
                            }
                            if (!bridge) { continue; }

                            // ---- trial: absorb the smaller of N and W, join
                            std::vector<label> aff;
                            aff.reserve(cf[X].size() + cf[N].size() + cf[W].size());
                            for (label q : cf[X]) { aff.push_back(q); }
                            for (label q : cf[N]) { aff.push_back(q); }
                            for (label q : cf[W]) { aff.push_back(q); }
                            std::sort(aff.begin(), aff.end());
                            aff.erase(std::unique(aff.begin(), aff.end()), aff.end());

                            label overBefore = 0;
                            scalar worstBefore = 0;
                            for (label q : aff)
                            {
                                if (!m.faceAlive[q]) { continue; }
                                const scalar sg = skewOf(q);
                                worstBefore = max(worstBefore, sg);
                                if (sg > limit) { ++overBefore; }
                            }

                            std::vector<label> savedO(aff.size()), savedN(aff.size());
                            std::vector<char> savedA(aff.size(), 0);
                            for (size_t i = 0; i < aff.size(); ++i)
                            {
                                savedO[i] = m.owner[aff[i]];
                                savedN[i] = m.neighbour[aff[i]];
                                savedA[i] = (m.faceAlive[aff[i]] ? 1 : 0);
                            }
                            const std::vector<label> savedF = m.faces[f];
                            const std::vector<label> savedG = m.faces[g];
                            const bool savedNAlive = m.cellAlive[N];
                            const bool savedWAlive = m.cellAlive[W];

                            const label A1 = (cV[N] <= cV[W]) ? N : W;
                            const label B1 = (A1 == N) ? W : N;
                            for (label q : aff)
                            {
                                if (!m.faceAlive[q]) { continue; }
                                const bool hasA = (m.owner[q] == A1 || m.neighbour[q] == A1);
                                const bool hasB = (m.owner[q] == B1 || m.neighbour[q] == B1);
                                if (hasA && hasB) { m.faceAlive[q] = false; continue; }
                                if (!hasA) { continue; }
                                if (m.owner[q] == A1) { m.owner[q] = B1; }
                                else { m.neighbour[q] = B1; }
                                if (m.neighbour[q] >= 0 && m.owner[q] == m.neighbour[q])
                                {
                                    m.faceAlive[q] = false;
                                }
                            }
                            m.cellAlive[A1] = false;

                            label over = labelMax;
                            scalar worstAfter = 0;
                            std::vector<label> joined;
                            std::vector<label> B = m.faces[g];
                            if (m.owner[g] != m.owner[f]) { std::reverse(B.begin(), B.end()); }
                            if (mergePolygons(m.points, m.faces[f], B, joined))
                            {
                                vector au; point cu;
                                polygonNormalCentre(m.points, joined, au, cu);
                                vector a1; point c1;
                                faceGeo(f, a1, c1);
                                vector a2; point c2;
                                faceGeo(g, a2, c2);
                                if (std::abs(mag(au) - (mag(a1) + mag(a2))) > 1e-6 * (mag(a1) + mag(a2)))
                                {
                                    joined.clear();
                                }
                            }
                            else { joined.clear(); }

                            if (!joined.empty())
                            {
                                m.faces[f] = joined;
                                m.faceAlive[g] = false;
                                geoOk[f] = 0;
                                geoOk[g] = 0;
                                std::vector<label> fx, fb;
                                for (label q : aff)
                                {
                                    if (!m.faceAlive[q]) { continue; }
                                    if (m.owner[q] == X || m.neighbour[q] == X) { fx.push_back(q); }
                                    if (m.owner[q] == B1 || m.neighbour[q] == B1) { fb.push_back(q); }
                                }
                                point xc, bc; scalar xv, bv;
                                cellCV(X, fx, xc, xv);
                                cellCV(B1, fb, bc, bv);
                                if
                                (
                                    xv > VSMALL && bv > VSMALL
                                 && pyramidsOk(X, fx, xc)
                                 && pyramidsOk(B1, fb, bc)
                                )
                                {
                                    over = 0;
                                    for (label q : aff)
                                    {
                                        if (!m.faceAlive[q]) { continue; }
                                        const label oq = m.owner[q], nq = m.neighbour[q];
                                        const point oc =
                                            (oq == X) ? xc : ((oq == B1) ? bc : cC[oq]);
                                        scalar sg;
                                        if (nq >= 0 && nq < m.nCells && m.cellAlive[nq])
                                        {
                                            const point ncp =
                                                (nq == X) ? xc : ((nq == B1) ? bc : cC[nq]);
                                            sg = skewWith(q, oc, ncp, true);
                                        }
                                        else
                                        {
                                            sg = skewWith(q, oc, oc, false);
                                        }
                                        worstAfter = max(worstAfter, sg);
                                        if (sg > limit) { ++over; }
                                    }
                                }
                            }

                            // ---- revert
                            for (size_t i = 0; i < aff.size(); ++i)
                            {
                                m.owner[aff[i]] = savedO[i];
                                m.neighbour[aff[i]] = savedN[i];
                                m.faceAlive[aff[i]] = (savedA[i] != 0);
                            }
                            m.faces[f] = savedF;
                            m.faces[g] = savedG;
                            geoOk[f] = 0;
                            geoOk[g] = 0;
                            m.cellAlive[N] = savedNAlive;
                            m.cellAlive[W] = savedWAlive;

                            if (dbg)
                            {
                                Pout<< "    trial unite " << N << " + " << W
                                    << " and join " << f << " + " << g
                                    << " of cell " << X << ": over " << overBefore
                                    << " -> " << over << ", worst " << worstBefore
                                    << " -> " << worstAfter << nl;
                            }
                            if (over >= overBefore) { continue; }

                            // ---- apply for real
                            for (label q : aff)
                            {
                                if (!m.faceAlive[q]) { continue; }
                                const bool hasA = (m.owner[q] == A1 || m.neighbour[q] == A1);
                                const bool hasB = (m.owner[q] == B1 || m.neighbour[q] == B1);
                                if (hasA && hasB) { m.faceAlive[q] = false; continue; }
                                if (!hasA) { continue; }
                                if (m.owner[q] == A1) { m.owner[q] = B1; }
                                else { m.neighbour[q] = B1; }
                                if (m.neighbour[q] >= 0 && m.owner[q] == m.neighbour[q])
                                {
                                    m.faceAlive[q] = false;
                                }
                            }
                            m.cellAlive[A1] = false;
                            m.faces[f] = joined;
                            m.faceAlive[g] = false;
                            geoOk[f] = 0;
                            geoOk[g] = 0;
                            std::vector<label> fx, fb;
                            for (label q : aff)
                            {
                                if (!m.faceAlive[q]) { continue; }
                                if (m.owner[q] == X || m.neighbour[q] == X) { fx.push_back(q); }
                                if (m.owner[q] == B1 || m.neighbour[q] == B1) { fb.push_back(q); }
                            }
                            cf[X].swap(fx);
                            cf[B1].swap(fb);
                            cf[A1].clear();
                            cellCV(X, cf[X], cC[X], cV[X]);
                            cellCV(B1, cf[B1], cC[B1], cV[B1]);
                            used[X] = 1; used[B1] = 1; used[A1] = 1;
                            for (label q : cf[X]) 
                            {
                                const label o = (m.owner[q] == X) ? m.neighbour[q] : m.owner[q];
                                if (o >= 0 && o < m.nCells) { used[o] = 1; }
                            }
                            for (label q : cf[B1])
                            {
                                const label o = (m.owner[q] == B1) ? m.neighbour[q] : m.owner[q];
                                if (o >= 0 && o < m.nCells) { used[o] = 1; }
                            }
                            ++nUnited; ++nJoined; ++repaired;
                            united = true;
                            if (dbg)
                            {
                                Pout<< "skewrepair: united " << A1 << " into " << B1
                                    << " and joined faces " << f << " + " << g
                                    << " (over " << overBefore << " -> " << over
                                    << ")" << nl;
                            }
                            break;
                        }
                    }
                    if (united) { break; }
                }
                // ---------- merge: one side of the bad face goes -----------
                // What the sliver rounds leave behind: the cell is too big to
                // be a sliver (or the union too bent to be convex), and the
                // face it shares with its neighbour is skewed past the limit
                // because the centre line of the two cells misses it.
                // Absorbing either side moves a centre and takes the face out
                // of the mesh; which side, and into which neighbour, is
                // settled by measurement, not by a volume rule -- the smaller
                // side is tried first only because it is the smaller change.
                const label n = m.neighbour[f];
                if (n >= 0 && !m.cellAlive[n]) { continue; }
                if (m.zone[f] >= 0) { continue; }
                if (m.patch[f] >= 0 && m.patch[f] >= firstProcPatch) { continue; }
                const label oW = m.owner[f];
                if (oW < 0 || !m.cellAlive[oW]) { continue; }
                const label nSides = (n >= 0) ? 2 : 1;
                label sides[2] = { oW, n };
                if (n >= 0 && cV[n] < cV[oW]) { sides[0] = n; sides[1] = oW; }

                label bestAbsorb = -1, bestH = -1, bestOver = labelMax;
                scalar bestWorst = GREAT, bestBefore = 0;
                for (label si = 0; si < nSides && bestOver != 0; ++si)
                {
                    const label small = sides[si];
                    if (used[small] || !m.cellAlive[small]) { continue; }
                    std::map<label, scalar> shared;
                    for (label g : cf[small])
                    {
                        if (!m.faceAlive[g] || m.zone[g] >= 0) { continue; }
                        if (m.neighbour[g] < 0) { continue; }
                        if (m.patch[g] >= 0 && m.patch[g] >= firstProcPatch) { continue; }
                        const label o = (m.owner[g] == small) ? m.neighbour[g] : m.owner[g];
                        if (o < 0 || o == small || !m.cellAlive[o] || used[o]) { continue; }
                        vector a; point cc;
                        faceGeo(g, a, cc);
                        shared[o] += mag(a);
                    }
                    // (shared area, host) -- built field by field: a
                    // pair<scalar,label> constructed from a map's
                    // pair<const label,scalar> would swap the two
                    std::vector<std::pair<scalar, label>> hosts;
                    hosts.reserve(shared.size());
                    for (const auto& kv : shared) { hosts.emplace_back(kv.second, kv.first); }
                    std::sort
                    (
                        hosts.begin(), hosts.end(),
                        [&](const std::pair<scalar, label>& x, const std::pair<scalar, label>& y)
                        {
                            if (x.first != y.first) { return x.first > y.first; }
                            return gidLess(x.second, y.second);
                        }
                    );
                    if (dbg)
                    {
                        Pout<< "    merge: bad face " << f << ", absorbing " << small
                            << " (vol " << cV[small] << "): " << hosts.size()
                            << " host(s)" << nl;
                    }
                    for (const auto& hp : hosts)
                    {
                        const label h = hp.second;
                        if (h < 0 || h >= m.nCells || used[h] || !m.cellAlive[h])
                        {
                            continue;
                        }
                        // never absorb across a baffle: that would take the
                        // sheet out of the mesh
                        bool zoneShared = false;
                        for (label g : cf[small])
                        {
                            if (!m.faceAlive[g] || m.zone[g] < 0) { continue; }
                            if (m.neighbour[g] < 0) { continue; }
                            const label o = (m.owner[g] == small) ? m.neighbour[g] : m.owner[g];
                            if (o == h) { zoneShared = true; break; }
                        }
                        if (zoneShared)
                        {
                            if (dbg)
                            {
                                Pout<< "    merge: host " << h << " skipped, a"
                                    << " baffle face between the two would be"
                                    << " lost" << nl;
                            }
                            continue;
                        }

                        std::vector<label> aff;
                        aff.reserve(cf[small].size() + cf[h].size());
                        for (label g : cf[small]) { aff.push_back(g); }
                        for (label g : cf[h]) { aff.push_back(g); }
                        std::sort(aff.begin(), aff.end());
                        aff.erase(std::unique(aff.begin(), aff.end()), aff.end());

                        label overBefore = 0;
                        scalar worstBefore = 0;
                        for (label g : aff)
                        {
                            if (!m.faceAlive[g]) { continue; }
                            const scalar sg = skewOf(g);
                            worstBefore = max(worstBefore, sg);
                            if (sg > limit) { ++overBefore; }
                        }

                        std::vector<label> savedO(aff.size()), savedN(aff.size());
                        std::vector<char> savedA(aff.size(), 0);
                        for (size_t i = 0; i < aff.size(); ++i)
                        {
                            savedO[i] = m.owner[aff[i]];
                            savedN[i] = m.neighbour[aff[i]];
                            savedA[i] = (m.faceAlive[aff[i]] ? 1 : 0);
                        }
                        const bool savedSmall = m.cellAlive[small];
                        for (label g : aff)
                        {
                            if (!m.faceAlive[g]) { continue; }
                            const bool hasS = (m.owner[g] == small || m.neighbour[g] == small);
                            const bool hasH = (m.owner[g] == h || m.neighbour[g] == h);
                            if (hasS && hasH) { m.faceAlive[g] = false; continue; }
                            if (!hasS) { continue; }
                            if (m.owner[g] == small) { m.owner[g] = h; }
                            else { m.neighbour[g] = h; }
                            if (m.neighbour[g] >= 0 && m.owner[g] == m.neighbour[g])
                            {
                                m.faceAlive[g] = false;
                            }
                        }
                        m.cellAlive[small] = false;

                        std::vector<label> uni;
                        uni.reserve(aff.size());
                        for (label g : aff) { if (m.faceAlive[g]) { uni.push_back(g); } }
                        point nc; scalar nv;
                        cellCV(h, uni, nc, nv);
                        label over = labelMax;
                        scalar worstAfter = 0;
                        label worstFace = -1;
                        const bool pyrOk = (nv > VSMALL) && pyramidsOk(h, uni, nc);
                        if (pyrOk)
                        {
                            over = 0;
                            for (label g : uni)
                            {
                                const scalar sg = skewWithCell(g, h, nc);
                                if (sg > worstAfter) { worstAfter = sg; worstFace = g; }
                                if (sg > limit) { ++over; }
                            }
                        }

                        for (size_t i = 0; i < aff.size(); ++i)
                        {
                            m.owner[aff[i]] = savedO[i];
                            m.neighbour[aff[i]] = savedN[i];
                            m.faceAlive[aff[i]] = (savedA[i] != 0);
                        }
                        m.cellAlive[small] = savedSmall;

                        if (dbg)
                        {
                            Pout<< "    trial merge " << small << " (vol " << cV[small]
                                << ") -> " << h << " (vol " << cV[h] << "): union vol "
                                << nv << ", over " << overBefore << " -> " << over
                                << ", worst " << worstBefore << " -> " << worstAfter
                                << ", pyramids " << (pyrOk ? "ok" : "BAD")
                                << ", worst face " << worstFace << nl;
                        }
                        const bool better =
                            (over < bestOver)
                         || (over == bestOver && worstAfter < bestWorst - 1e-9);
                        if (better)
                        {
                            bestOver = over; bestWorst = worstAfter;
                            bestH = h; bestAbsorb = small; bestBefore = worstBefore;
                        }
                    }
                }
                // Accept only real progress: every over-limit face gone, or
                // the same count with a strictly lower worst face, which the
                // next round carries on from.
                const bool progress =
                    (bestH >= 0 && bestAbsorb >= 0 && bestOver < labelMax)
                 && (bestOver == 0 || bestWorst < bestBefore - 1e-6);
                if (!progress) { continue; }

                // ---- apply for real
                const label small = bestAbsorb;
                const label h = bestH;
                std::vector<label> aff;
                aff.reserve(cf[small].size() + cf[h].size());
                for (label g : cf[small]) { aff.push_back(g); }
                for (label g : cf[h]) { aff.push_back(g); }
                std::sort(aff.begin(), aff.end());
                aff.erase(std::unique(aff.begin(), aff.end()), aff.end());
                for (label g : aff)
                {
                    if (!m.faceAlive[g]) { continue; }
                    const bool hasS = (m.owner[g] == small || m.neighbour[g] == small);
                    const bool hasH = (m.owner[g] == h || m.neighbour[g] == h);
                    if (hasS && hasH) { m.faceAlive[g] = false; continue; }
                    if (!hasS) { continue; }
                    if (m.owner[g] == small) { m.owner[g] = h; }
                    else { m.neighbour[g] = h; }
                    if (m.neighbour[g] >= 0 && m.owner[g] == m.neighbour[g])
                    {
                        m.faceAlive[g] = false;
                    }
                }
                m.cellAlive[small] = false;
                std::vector<label> uni;
                uni.reserve(aff.size());
                for (label g : aff) { if (m.faceAlive[g]) { uni.push_back(g); } }
                cellCV(h, uni, cC[h], cV[h]);
                cf[h].swap(uni);
                cf[small].clear();
                // every cell the union touches has a face whose centre line
                // moved: leave them to the next round, which measures again
                used[small] = 1; used[h] = 1;
                for (label g : cf[h])
                {
                    const label o = (m.owner[g] == h) ? m.neighbour[g] : m.owner[g];
                    if (o >= 0 && o < m.nCells) { used[o] = 1; }
                }
                ++nMerged; ++repaired;
                if (dbg)
                {
                    Pout<< "skewrepair: cell " << small << " absorbed by " << h
                        << " (bad face " << f << ", over -> " << bestOver << ")" << nl;
                }
                break;
            }
        }
        if (!repaired) { break; }
    }

    // One line, so a run that had to repair says how, and a mesh checkMesh
    // already passed says nothing at all.
    label joined = nJoined, merged = nMerged, united = nUnited;
    reduce(joined, sumOp<label>());
    reduce(merged, sumOp<label>());
    reduce(united, sumOp<label>());
    if (joined || merged || united)
    {
        Info<< "Cut mesh: skewness repair — joined " << joined
            << " coplanar wall piece(s), absorbed " << merged
            << " cell(s) an over-limit face straddled, united " << united
            << " strip far-side pair(s)" << nl;
    }
    return joined + merged + united;
}


void Foam::dcCartCutCells::coalesceCoplanarFaces
(
    cutMesh& m,
    report& r,
    label myRank,
    const boolList* plainWall
)
{
    // Base604's residual checkMesh skew faces are all one structure: two
    // boundary faces of the SAME cell, coplanar in the raised-floor plane
    // y=0.8, sharing the edge x=11.4032609 -- a 3.26 mm strip (the surface
    // x=11.400000 to the level-4 plane 11.4032609) beside a 42.4 mm piece.
    // checkMesh normalises a boundary face's skewness by max(0.4*|d|, the
    // face's own reach); on a 3.8 mm-thick slab |d| is 1.9 mm, so the strip
    // scores 21.4, while the joined 45.7 mm face -- same centre-to-centre
    // line, reach 22.8 mm -- scores ~0.85. Nothing but the differing wall
    // patch NAME keeps them apart, which is what the group key below encodes.
    const bool crossWalls =
        plainWall && (getenv("DCCART_COOALESCE_WALLS") != nullptr);
    mergeCoincidentPoints(m);
    // Faces between the SAME two cells (or the same cell and the same
    // patch / zone) that are coplanar and share an edge become one face:
    // a sliver merged into its host leaves the host's face in two pieces,
    // and a cap split along a region boundary that then lands in one region
    // does too. Two small faces where one belongs is what checkMesh reports
    // as skewness (the centre-to-centre line misses the small piece).
    // Groups (a, b, patch, zone) of two or more faces. A group's joins touch
    // only its own faces, so groups are taken cell by cell (a = the lower cell
    // of the pair) instead of through one ordered map of every face in the
    // mesh; within a group the faces stay in ascending id order.
    const label nFc = label(m.faces.size());
    std::vector<label> nOfLower(m.nCells + 1, 0);
    auto lowerOf = [&](label f) -> label
    {
        const label o = m.owner[f], n = m.neighbour[f];
        return (n < 0) ? o : std::min(o, n);
    };
    for (label f = 0; f < nFc; ++f)
    {
        if (!m.faceAlive[f] || m.owner[f] < 0) { continue; }
        ++nOfLower[lowerOf(f) + 1];
    }
    for (label c = 0; c < m.nCells; ++c) { nOfLower[c + 1] += nOfLower[c]; }
    std::vector<label> byLower(nOfLower[m.nCells]);
    {
        std::vector<label> fill(nOfLower.begin(), nOfLower.end() - 1);
        for (label f = 0; f < nFc; ++f)
        {
            if (!m.faceAlive[f] || m.owner[f] < 0) { continue; }
            byLower[fill[lowerOf(f)]++] = f;
        }
    }
    std::vector<std::vector<label>> groups;
    {
        std::vector<label> run;
        for (label c = 0; c < m.nCells; ++c)
        {
            const label s0 = nOfLower[c], s1 = nOfLower[c + 1];
            if (s1 - s0 < 2) { continue; }
            run.assign(byLower.begin() + s0, byLower.begin() + s1);
            auto keyOf = [&](label f)
            {
                const label n = m.neighbour[f];
                // A plain-wall boundary face groups with the cell's other
                // plain-wall boundary faces; coplanarity, orientation and the
                // shared edge are still decided below, so this only stops a
                // patch NAME from holding two pieces of one flat wall apart.
                // Zone faces and internal faces keep their exact patch.
                label pk = m.patch[f];
                if
                (
                    crossWalls && n < 0 && m.zone[f] < 0
                 && pk >= 0 && pk < plainWall->size() && (*plainWall)[pk]
                )
                {
                    pk = -1;
                }
                return std::make_tuple((n < 0) ? label(-1) : std::max(m.owner[f], n), pk, m.zone[f], f);
            };
            std::sort(run.begin(), run.end(), [&](label x, label y) { return keyOf(x) < keyOf(y); });
            for (size_t i = 0; i < run.size(); )
            {
                size_t j = i + 1;
                while
                (
                    j < run.size()
                 && std::get<0>(keyOf(run[j])) == std::get<0>(keyOf(run[i]))
                 && std::get<1>(keyOf(run[j])) == std::get<1>(keyOf(run[i]))
                 && std::get<2>(keyOf(run[j])) == std::get<2>(keyOf(run[i]))
                )
                {
                    ++j;
                }
                if (j - i >= 2) { groups.emplace_back(run.begin() + i, run.begin() + j); }
                i = j;
            }
        }
    }
    // ---- DCCART_DEBUG_BOX: what the grouping saw, before any join ----
    boundBox dbgCoalesceBox(boundBox::invertedBox);
    bool dbgCoalesce = false;
    if (const char* e0 = getenv("DCCART_DEBUG_BOX"))
    {
        double b0[6];
        if (sscanf(e0, "%lf %lf %lf %lf %lf %lf", b0, b0+1, b0+2, b0+3, b0+4, b0+5) == 6)
        {
            dbgCoalesceBox = boundBox(point(b0[0], b0[1], b0[2]), point(b0[3], b0[4], b0[5]));
            dbgCoalesce = true;
        }
    }
    if (dbgCoalesce)
    {
        std::vector<bool> boxed(nFc, false);
        for (label f = 0; f < nFc; ++f)
        {
            if (!m.faceAlive[f] || m.owner[f] < 0) { continue; }
            vector nn; point cc;
            polygonNormalCentre(m.points, m.faces[f], nn, cc);
            if (!dbgCoalesceBox.contains(cc)) { continue; }
            boxed[f] = true;
            Pout<< "  [cz-in] f " << f << " own " << m.owner[f]
                << " nei " << m.neighbour[f] << " patch " << m.patch[f]
                << " zone " << m.zone[f] << " rank " << m.cellRank[m.owner[f]]
                << " nPts " << label(m.faces[f].size())
                << " c " << cc << " n " << (mag(nn) > VSMALL ? nn / mag(nn) : vector::zero)
                << nl;
        }
        for (const auto& g : groups)
        {
            bool any = false;
            for (label f : g) { if (boxed[f]) { any = true; break; } }
            if (!any) { continue; }
            Pout<< "  [cz-grp] size " << label(g.size()) << " faces";
            for (label f : g) { Pout<< " " << f; }
            Pout<< " own " << m.owner[g[0]] << " nei " << m.neighbour[g[0]] << nl;
        }
    }
    // A coplanar sliver of one tag beside a face of another is what leaves
    // Base604's last checkMesh failures. The raised floor y=0.8 carries the
    // faceZone Room_Raising_Floor_part1; where the sheet's free edge runs
    // 2.8 mm off the grid line z=13.0219 (the geometry is 8.7 degrees off
    // axis there), the interface between the 3.8 mm slab cell the floor
    // leaves above the level-4 plane y=0.803846 and the cell below comes out
    // in THREE coplanar pieces: a 882 mm2 quad in the zone, a 903 mm2
    // pentagon out of it, and a 25.6 mm2 triangle in the zone again. The
    // group key above keeps the zone apart from the rest, the two zone
    // pieces do not touch, and the triangle survives alone -- checkMesh then
    // measures it against a centre-to-centre line 33.6 mm away and reports
    // skewness 4.52 against its limit of 4. Joined to the pentagon beside it
    // the same interface is one 928 mm2 rectangle and scores 1.35 (measured:
    // both on the two-cell mesh this leaves behind).
    //
    // So a second pass lets a piece join an edge-adjacent coplanar piece of
    // a DIFFERENT patch / zone when it is a sliver of it, and hands the union
    // the tag of the LARGER piece: the surface that owns the plane keeps it.
    // Only faces of one peer take part -- the two cells of an internal or
    // baffle face, or one cell's boundary faces -- and only below
    // tinyTagFraction of the neighbour's area, so what moves between a
    // baffle and the hole beside it is a few percent of one cell's interface
    // -- far under the geometry's own error -- and a containment sheet is
    // never opened up the way the tag bug in passArgs did. A boundary sliver
    // crosses only into another plain wall, so no inlet, outlet or baffle
    // ever loses outline to its neighbour.
    //
    // The same gate covers the other residual shape: the 3.26 mm strip the
    // surface x=11.400000 leaves beside the level-4 plane x=11.4032609 on
    // the raised floor, two boundary walls of one cell whose only difference
    // is the patch NAME (see the head of this function).
    // 0.25 covers every residual Base604 case measured (the widest strip is
    // 353 mm2 beside 1810 mm2, a ratio of 0.195; 0.10 left 11 faces, 0.25
    // is the smallest round value above every one of them).
    static scalar tinyTagFraction = 0.25;
    {
        static bool tinyRead = false;
        if (!tinyRead)
        {
            tinyRead = true;
            if (const char* e = getenv("DCCART_COOALESCE_TINY_FRACTION"))
            {
                const double v = atof(e);
                if (v >= 0) { tinyTagFraction = scalar(v); }
            }
        }
    }

    label nMerged = 0;
    label nTagMerged = 0;
    auto joinGroup = [&](std::vector<label>& fs, const bool crossTag)
    {
        // (runs before dropGhostCells: a pair of an owned and a halo cell
        // holds the same pieces on both ranks, so both join them alike)
        const label a0 = lowerOf(fs[0]);
        const label b0 = (m.neighbour[fs[0]] < 0) ? label(-1) : std::max(m.owner[fs[0]], m.neighbour[fs[0]]);
        const bool countHere =
            std::min(m.cellRank[a0], (b0 >= 0 ? m.cellRank[b0] : m.cellRank[a0])) == myRank;
        bool merged = true;
        while (merged && fs.size() >= 2)
        {
            merged = false;
            for (size_t i = 0; i < fs.size() && !merged; ++i)
            {
                for (size_t j = i + 1; j < fs.size() && !merged; ++j)
                {
                    const label f1 = fs[i], f2 = fs[j];
                    vector n1, n2; point c1, c2;
                    polygonNormalCentre(m.points, m.faces[f1], n1, c1);
                    polygonNormalCentre(m.points, m.faces[f2], n2, c2);
                    const scalar a1 = mag(n1), a2 = mag(n2);
                    if (a1 < VSMALL || a2 < VSMALL) { continue; }
                    const bool sameTag =
                        (m.patch[f1] == m.patch[f2] && m.zone[f1] == m.zone[f2]);
                    if (!sameTag)
                    {
                        // A sliver may cross a tag; anything bigger may not.
                        const bool b1 = (m.neighbour[f1] < 0), b2 = (m.neighbour[f2] < 0);
                        bool ok =
                            crossTag
                         && tinyTagFraction > 0
                         && b1 == b2
                         && std::min(a1, a2) <= tinyTagFraction * std::max(a1, a2);
                        // Two boundary faces: only a plain wall may take the
                        // sliver of another, and only into the wall that owns
                        // the plane. An inlet, an outlet or a baffle keeps its
                        // own outline -- renaming one would move a boundary
                        // condition, not just a report line.
                        if (ok && b1)
                        {
                            const label p1 = m.patch[f1], p2 = m.patch[f2];
                            ok = plainWall
                              && m.zone[f1] < 0 && m.zone[f2] < 0
                              && p1 >= 0 && p2 >= 0
                              && p1 < label(plainWall->size())
                              && p2 < label(plainWall->size())
                              && (*plainWall)[p1] && (*plainWall)[p2];
                        }
                        if (!ok) { continue; }
                    }
                    // same orientation as seen from a common owner
                    std::vector<label> B = m.faces[f2];
                    if (m.owner[f2] != m.owner[f1]) { std::reverse(B.begin(), B.end()); n2 = -n2; }
                    static const char* dbgBox = getenv("DCCART_DEBUG_BOX");
                    static Foam::boundBox cbox(Foam::boundBox::invertedBox);
                    static bool cparsed = false;
                    if (dbgBox && !cparsed)
                    {
                        cparsed = true;
                        double b[6];
                        if (sscanf(dbgBox, "%lf %lf %lf %lf %lf %lf", b, b+1, b+2, b+3, b+4, b+5) == 6)
                        {
                            cbox = Foam::boundBox(Foam::point(b[0], b[1], b[2]), Foam::point(b[3], b[4], b[5]));
                        }
                    }
                    const bool dbg = dbgBox && cbox.contains(c1);
                    const char* why = nullptr;
                    std::vector<label> out;
                    vector nu; point cu;
                    if ((n1 & n2) < (1 - 1e-6) * a1 * a2) { why = "normals"; }
                    else if (std::abs((c2 - c1) & (n1 / a1)) > 1e-6) { why = "offplane"; }
                    else if (!mergePolygons(m.points, m.faces[f1], B, out)) { why = "chain"; }
                    else
                    {
                        // the union must stay simple: its area is the sum
                        polygonNormalCentre(m.points, out, nu, cu);
                        if (std::abs(mag(nu) - (a1 + a2)) > 1e-6 * (a1 + a2)) { why = "area"; }
                    }
                    if (why)
                    {
                        if (dbg)
                        {
                            Pout<< "  coalesce " << f1 << "+" << f2 << " at " << c1 << " failed: " << why
                                << " A " << a1 << " " << a2 << " n " << n1 / a1 << " " << n2 / a2 << nl;
                        }
                        continue;
                    }
                    m.faces[f1] = out;
                    m.faceAlive[f2] = false;
                    if (!sameTag)
                    {
                        // the union belongs to whichever piece owns the plane
                        const label keep = (a1 >= a2) ? f1 : f2;
                        m.patch[f1] = m.patch[keep];
                        m.zone[f1] = m.zone[keep];
                        if (countHere) { ++nTagMerged; }
                    }
                    fs.erase(fs.begin() + j);
                    merged = true;
                    if (countHere) { ++nMerged; }
                }
            }
        }
    };
    for (auto& fs : groups) { joinGroup(fs, false); }

    // Second pass: the same cell pair, this time across a patch / zone
    // difference, so a sliver can join the piece beside it. Grouped by the
    // cell pair alone (byLower's buckets are still valid: the first pass
    // changed face vertices and killed faces, never an owner or neighbour).
    if (tinyTagFraction > 0)
    {
        std::vector<std::vector<label>> pairGroups;
        std::vector<label> run;
        for (label c = 0; c < m.nCells; ++c)
        {
            const label s0 = nOfLower[c], s1 = nOfLower[c + 1];
            if (s1 - s0 < 2) { continue; }
            run.clear();
            for (label k = s0; k < s1; ++k)
            {
                const label f = byLower[k];
                if (m.faceAlive[f]) { run.push_back(f); }
            }
            if (run.size() < 2) { continue; }
            // The peer a face is shared with: the other cell of an internal
            // or baffle face, -1 for a boundary face (its own cell is the
            // bucket, so a cell's boundary pieces group together).
            auto pairOf = [&](label f) -> label
            {
                return (m.neighbour[f] < 0) ? label(-1) : std::max(m.owner[f], m.neighbour[f]);
            };
            std::sort(run.begin(), run.end(), [&](label x, label y)
            {
                const label px = pairOf(x), py = pairOf(y);
                return (px == py) ? (x < y) : (px < py);
            });
            for (size_t i = 0; i < run.size(); )
            {
                size_t j = i + 1;
                while (j < run.size() && pairOf(run[j]) == pairOf(run[i])) { ++j; }
                if (j - i >= 2)
                {
                    bool mixed = false;
                    for (size_t k = i + 1; k < j && !mixed; ++k)
                    {
                        mixed = (m.patch[run[k]] != m.patch[run[i]])
                             || (m.zone[run[k]] != m.zone[run[i]]);
                    }
                    if (mixed) { pairGroups.emplace_back(run.begin() + i, run.begin() + j); }
                }
                i = j;
            }
        }
        for (auto& fs : pairGroups) { joinGroup(fs, true); }
    }
    r.nCoalesced += nMerged;
    r.nTagCoalesced += nTagMerged;
}



namespace
{
using WriterCutMesh=Foam::dcCartCutCells::cutMesh;
using WriterBindings=Foam::dcCartCutCells::writerBindings;
using WriterBaseline=Foam::dcCartCutCells::writerCoupledBaseline;
using WriterFace=Foam::dcCartCutCells::writerCoupledFace;

// DP equality is a representation promise, not tolerance equality. In
// particular -0/+0 differ and identical nonfinite bit patterns are refused.
bool writerSameScalar(scalar a,scalar b)
{
    return std::isfinite(a) && std::isfinite(b)
        && std::memcmp(&a,&b,sizeof(scalar))==0;
}
bool writerSamePoint(const vector& a,const vector& b)
{
    return writerSameScalar(a.x(),b.x()) && writerSameScalar(a.y(),b.y())
        && writerSameScalar(a.z(),b.z());
}
bool writerFinitePoint(const vector& p)
{
    return std::isfinite(p.x()) && std::isfinite(p.y()) && std::isfinite(p.z());
}
bool writerCoupledType(const Foam::word& type)
{
    return type.find("cyclic")==0 || type.find("processor")==0
        || type.find("mapped")==0 || type.find("AMI")!=std::string::npos;
}
std::string writerPatchRole(const Foam::polyPatch& patch)
{
    Foam::OStringStream stream;
    stream.precision(std::numeric_limits<scalar>::max_digits10);
    patch.write(stream);
    Foam::IStringStream input(stream.str());
    Foam::dictionary role(input);
    // The sole offset excluded from the certificate. The actual face map
    // and patch-relative index separately prove the remapped startFace.
    role.remove("startFace");
    Foam::OStringStream normalized;
    normalized.precision(std::numeric_limits<scalar>::max_digits10);
    role.writeEntries(normalized);
    return normalized.str();
}
std::vector<std::pair<std::string,bool>> writerZoneRoles
(
    const Foam::polyMesh& mesh,label f
)
{
    std::vector<std::pair<std::string,bool>> roles;
    forAll(mesh.faceZones(),z)
    {
        const Foam::faceZone& zone=mesh.faceZones()[z];
        const label local=zone.whichFace(f);
        if (local>=0) { roles.emplace_back(std::string(zone.name()),zone.flipMap()[local]); }
    }
    return roles;
}
// Exact live-container payload estimate. Allocator headers, transient canon
// hashes and OpenFOAM auxiliary caches are deliberately not called RSS.
std::size_t writerCutPayloadBytes(const WriterCutMesh& m)
{
    std::size_t n=sizeof(m)+m.points.capacity()*sizeof(point)
        +m.faces.capacity()*sizeof(std::vector<label>);
    for (const auto& f:m.faces) { n+=f.capacity()*sizeof(label); }
    for (const auto* v:{&m.owner,&m.neighbour,&m.patch,&m.zone,&m.cellLevel,
        &m.cellRank,&m.coneEvidenceParent,&m.coneEvidenceBase})
    { n+=v->capacity()*sizeof(label); }
    for (const auto* v:{&m.faceAlive,&m.cellAlive,&m.cellOrigAlive})
    { n+=(v->capacity()+7)/8; }
    n+=m.cellRefVol.capacity()*sizeof(scalar)+m.cellRefCentre.capacity()*sizeof(point)
        +(m.cellLeaf.capacity()+m.cellPiece.capacity())*sizeof(uint64_t);
    return n;
}
std::string writerJsonQuote(const std::string& input)
{
    std::ostringstream out;out<<'"';
    for (unsigned char c:input)
    {
        if (c=='"' || c=='\\') { out<<'\\'<<c; }
        else if (c<32) { out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<unsigned(c)<<std::dec; }
        else { out<<c; }
    }
    out<<'"';return out.str();
}
void writerHex(std::ostream& out,scalar value)
{
    const auto* bytes=reinterpret_cast<const unsigned char*>(&value);
    out<<'"';for (std::size_t j=0;j<sizeof(scalar);++j)
    { out<<std::hex<<std::setw(2)<<std::setfill('0')<<unsigned(bytes[j]); }
    out<<std::dec<<'"';
}
void writerPointBits(std::ostream& out,const vector& p)
{
    out<<'[';writerHex(out,p.x());out<<',';writerHex(out,p.y());out<<',';writerHex(out,p.z());out<<']';
}
bool writeWriterCertificate
(
    const WriterBaseline& baseline,const Foam::fileName& path,
    const char* status,label failures,scalar verifySeconds
)
{
    std::ofstream out(path.c_str());if (!out) { return false; }
    const uint16_t endianMarker=1;
    const char* byteOrder=*reinterpret_cast<const unsigned char*>(&endianMarker) ? "little" : "big";
    out<<std::setprecision(17)
        <<"{\"format\":\"installed-writer-coupled-certificate/v1\",\"byteOrder\":"<<writerJsonQuote(byteOrder)<<",\"status\":"<<writerJsonQuote(status)
        <<",\"rank\":"<<Foam::Pstream::myProcNo()<<",\"scalarBytes\":"<<sizeof(scalar)
        <<",\"failures\":"<<failures<<",\"copySeconds\":"<<baseline.copySeconds
        <<",\"constructSeconds\":"<<baseline.constructSeconds<<",\"snapshotSeconds\":"<<baseline.snapshotSeconds
        <<",\"verifySeconds\":"<<verifySeconds<<",\"cutPayloadBytes\":"<<baseline.cutPayloadBytes
        <<",\"writerGeometryBytes\":"<<baseline.writerGeometryBytes
        <<",\"baselineImmutable\":true,\"remoteQualityRecomputed\":false,\"coupledPatchRoles\":{";
    bool first=true;
    for (const auto& p:baseline.patchRoles)
    { if (!first) { out<<','; }first=false;out<<writerJsonQuote(p.first)<<':'<<writerJsonQuote(p.second); }
    out<<"},\"coupledPatchSizes\":{";first=true;
    for (const auto& p:baseline.patchSizes)
    { if (!first) { out<<','; }first=false;out<<writerJsonQuote(p.first)<<':'<<p.second; }
    out<<"},\"faces\":[";
    for (std::size_t k=0;k<baseline.faces.size();++k)
    {
        if (k) { out<<','; }const auto& f=baseline.faces[k];
        out<<"{\"nativeFace\":"<<f.nativeFace<<",\"nativeOwner\":"<<f.nativeOwner
            <<",\"nativeNeighbour\":"<<f.nativeNeighbour<<",\"rawPatch\":"<<f.rawPatch<<",\"rawZone\":"<<f.rawZone
            <<",\"relativeFace\":"<<f.relativeFace<<",\"compactFace\":"<<f.compactFace
            <<",\"compactOwner\":"<<f.compactOwner<<",\"patchName\":"<<writerJsonQuote(f.patchName)
            <<",\"patchType\":"<<writerJsonQuote(f.patchType)<<",\"patchRole\":"<<writerJsonQuote(f.patchRole);
        auto labels=[&](const char* name,const std::vector<label>& values)
        {
            out<<",\""<<name<<"\":[";for (std::size_t i=0;i<values.size();++i)
            { if (i) { out<<','; }out<<values[i]; }out<<']';
        };
        labels("nativePoints",f.nativePoints);labels("pointAliases",f.pointAliases);labels("ringAliases",f.ringAliases);
        labels("compactPoints",f.compactPoints);
        out<<",\"coordinateBytes\":[";
        for (std::size_t i=0;i<f.coordinates.size();++i)
        { if (i) { out<<','; }writerPointBits(out,f.coordinates[i]); }
        out<<"],\"SfBytes\":";writerPointBits(out,f.area);out<<",\"FCBytes\":";writerPointBits(out,f.faceCentre);
        out<<",\"CCBytes\":";writerPointBits(out,f.cellCentre);out<<",\"VBytes\":";writerHex(out,f.volume);
        out<<",\"zoneRoles\":[";
        for (std::size_t i=0;i<f.zoneRoles.size();++i)
        { if (i) { out<<','; }out<<'['<<writerJsonQuote(f.zoneRoles[i].first)<<','<<(f.zoneRoles[i].second?"true":"false")<<']'; }
        out<<"]}";
    }
    out<<"]}\n";return bool(out);
}
bool writeWriterCheckpoint(const WriterCutMesh& m,const Foam::fileName& prefix)
{
    bool ok=true;
    auto raw=[&](const char* name,const auto& values)
    {
        using Value=typename std::decay<decltype(values)>::type::value_type;
        std::ofstream out((prefix+name).c_str(),std::ios::binary);
        out.write(reinterpret_cast<const char*>(values.data()),values.size()*sizeof(Value));ok=ok&&bool(out);
    };
    raw("points.f64",m.points);raw("owner.i32",m.owner);raw("neighbour.i32",m.neighbour);
    raw("patch.i32",m.patch);raw("zone.i32",m.zone);raw("cell-ref-volume.f64",m.cellRefVol);
    raw("cell-ref-centre.f64",m.cellRefCentre);raw("cell-level.i32",m.cellLevel);raw("cell-rank.i32",m.cellRank);
    raw("cell-leaf.u64",m.cellLeaf);raw("cell-piece.u64",m.cellPiece);
    raw("cone-parent.i32",m.coneEvidenceParent);raw("cone-base.i32",m.coneEvidenceBase);
    std::vector<label> offsets{0},vertices;
    for (const auto& f:m.faces) { vertices.insert(vertices.end(),f.begin(),f.end());offsets.push_back(vertices.size()); }
    raw("face-offsets.i32",offsets);raw("face-vertices.i32",vertices);
    auto bits=[&](const char* name,const std::vector<bool>& values)
    {
        std::vector<unsigned char> expanded;expanded.reserve(values.size());
        for (bool v:values) { expanded.push_back(v); }raw(name,expanded);
    };
    bits("face-alive.u8",m.faceAlive);bits("cell-alive.u8",m.cellAlive);bits("cell-original.u8",m.cellOrigAlive);
    std::ofstream meta((prefix+"manifest.json").c_str());
    meta<<"{\"format\":\"preoperator-cutmesh-checkpoint/v1\",\"cells\":"<<m.nCells
        <<",\"points\":"<<m.points.size()<<",\"faces\":"<<m.faces.size()<<",\"nGridPoints\":"<<m.nGridPoints
        <<",\"labelBytes\":"<<sizeof(label)<<",\"scalarBytes\":"<<sizeof(scalar)<<"}\n";
    return ok&&bool(meta);
}
label snapshotWriterCoupled
(
    const WriterCutMesh& original,const Foam::polyMesh& mesh,
    const WriterBindings& map,WriterBaseline& baseline
)
{
    label bad=0;
    if (map.cell.size()!=std::size_t(original.nCells) || map.face.size()!=original.faces.size()
     || map.point.size()!=original.points.size() || map.alias.size()!=original.points.size()) { return 1; }
    const auto& cc=mesh.cellCentres();const auto& v=mesh.cellVolumes();
    const auto& sf=mesh.faceAreas();const auto& fc=mesh.faceCentres();
    baseline.writerGeometryBytes=cc.size()*sizeof(point)+v.size()*sizeof(scalar)
        +(sf.size()+fc.size())*sizeof(vector);
    std::vector<label> nativePoint(mesh.nPoints(),-1);
    for (label p=0;p<label(map.point.size());++p) if (map.point[p]>=0) { nativePoint[map.point[p]]=p; }
    forAll(mesh.boundaryMesh(),pi)
    {
        const auto& patch=mesh.boundaryMesh()[pi];if (!patch.coupled() && !writerCoupledType(patch.type())) { continue; }
        const std::string name=patch.name(),role=writerPatchRole(patch);
        if (!baseline.patchRoles.emplace(name,role).second) { ++bad; }
        baseline.patchSizes[name]=patch.size();
    }
    for (label nf=0;nf<label(original.faces.size());++nf)
    {
        const label f=map.face[nf];if (f<mesh.nInternalFaces()) { continue; }
        if (f<0 || f>=mesh.nFaces()) { ++bad;continue; }
        const label pi=mesh.boundaryMesh().whichPatch(f);
        if (pi<0) { ++bad;continue; }const auto& patch=mesh.boundaryMesh()[pi];
        if (!patch.coupled() && !writerCoupledType(patch.type())) { continue; }
        WriterFace row;row.nativeFace=nf;row.nativeOwner=original.owner[nf];row.nativeNeighbour=original.neighbour[nf];
        row.rawPatch=original.patch[nf];row.rawZone=original.zone[nf];row.relativeFace=f-patch.start();
        row.patchName=patch.name();row.patchType=patch.type();row.patchRole=baseline.patchRoles.at(row.patchName);
        row.nativePoints=original.faces[nf];
        for (label p:row.nativePoints)
        {
            if (p<0 || p>=label(map.alias.size())) { ++bad;continue; }
            row.pointAliases.push_back(map.alias[p]);
        }
        for (label p:mesh.faces()[f])
        {
            if (p<0 || p>=mesh.nPoints() || nativePoint[p]<0) { ++bad;continue; }
            row.ringAliases.push_back(nativePoint[p]);row.compactPoints.push_back(p);
            row.coordinates.push_back(mesh.points()[p]);
            if (!writerFinitePoint(mesh.points()[p])) { ++bad; }
        }
        const label owner=mesh.faceOwner()[f];
        if (row.nativeOwner<0 || row.nativeOwner>=label(map.cell.size()) || map.cell[row.nativeOwner]!=owner) { ++bad; }
        row.compactFace=f;row.compactOwner=owner;
        row.area=sf[f];row.faceCentre=fc[f];row.cellCentre=cc[owner];row.volume=v[owner];
        row.zoneRoles=writerZoneRoles(mesh,f);
        if (!writerFinitePoint(row.area) || !writerFinitePoint(row.faceCentre) || !writerFinitePoint(row.cellCentre)
         || !std::isfinite(row.volume) || row.volume<=0 || row.coordinates.size()<3
         || row.nativePoints.size()!=row.pointAliases.size()) { ++bad; }
        baseline.faces.push_back(std::move(row));
    }
    return bad;
}
label verifyWriterCoupled
(
    const WriterCutMesh& m,const Foam::polyMesh& mesh,const WriterBindings& map,
    const WriterBaseline& baseline
)
{
    label bad=0;
    const auto& cc=mesh.cellCentres();const auto& v=mesh.cellVolumes();
    const auto& sf=mesh.faceAreas();const auto& fc=mesh.faceCentres();
    if (!baseline.checkpoint || map.cell.size()!=std::size_t(m.nCells) || map.face.size()!=m.faces.size()
     || map.point.size()!=m.points.size() || map.alias.size()!=m.points.size()) { return 1; }
    std::map<std::string,std::string> roles;std::map<std::string,label> sizes;
    forAll(mesh.boundaryMesh(),pi)
    {
        const auto& patch=mesh.boundaryMesh()[pi];if (!patch.coupled() && !writerCoupledType(patch.type())) { continue; }
        if (!roles.emplace(std::string(patch.name()),writerPatchRole(patch)).second) { ++bad; }
        sizes[std::string(patch.name())]=patch.size();
    }
    if (roles!=baseline.patchRoles || sizes!=baseline.patchSizes) { ++bad; }
    label coupledFaces=0;for (const auto& p:sizes) { coupledFaces+=p.second; }
    if (std::size_t(coupledFaces)!=baseline.faces.size()) { ++bad; }
    for (const auto& row:baseline.faces)
    {
        const label nf=row.nativeFace,no=row.nativeOwner;
        if (nf<0 || nf>=label(map.face.size()) || no<0 || no>=label(map.cell.size())
         || !m.faceAlive[nf] || !m.cellAlive[no]) { ++bad;continue; }
        const label f=map.face[nf],owner=map.cell[no];
        if (f<mesh.nInternalFaces() || f>=mesh.nFaces() || owner<0 || owner>=mesh.nCells()) { ++bad;continue; }
        const label pi=mesh.boundaryMesh().whichPatch(f);if (pi<0) { ++bad;continue; }
        const auto& patch=mesh.boundaryMesh()[pi];const auto& ring=mesh.faces()[f];
        bool same=mesh.faceOwner()[f]==owner && row.nativeNeighbour==-1
            && m.neighbour[nf]==-1 && m.patch[nf]==row.rawPatch && m.zone[nf]==row.rawZone
            && std::string(patch.name())==row.patchName && std::string(patch.type())==row.patchType
            && f-patch.start()==row.relativeFace && roles.count(row.patchName)
            && roles.at(row.patchName)==row.patchRole
            && ring.size()==label(row.coordinates.size()) && ring.size()==label(row.ringAliases.size())
            && row.nativePoints.size()==row.pointAliases.size()
            && writerSamePoint(sf[f],row.area) && writerSamePoint(fc[f],row.faceCentre)
            && writerSamePoint(cc[owner],row.cellCentre) && writerSameScalar(v[owner],row.volume)
            && writerZoneRoles(mesh,f)==row.zoneRoles;
        for (std::size_t j=0;same && j<row.nativePoints.size();++j)
        {
            const label p=row.nativePoints[j];
            same=p>=0 && p<label(map.alias.size()) && map.alias[p]==row.pointAliases[j];
            const label rep=same ? map.alias[p] : -1;
            same=same && rep>=0 && rep<label(m.points.size())
                && rep<label(baseline.checkpoint->points.size())
                && writerSamePoint(m.points[rep],baseline.checkpoint->points[rep]);
        }
        for (label j=0;same && j<ring.size();++j)
        {
            const label rep=row.ringAliases[j];
            same=rep>=0 && rep<label(map.point.size()) && map.point[rep]==ring[j]
                && writerSamePoint(mesh.points()[ring[j]],row.coordinates[j]);
        }
        if (!same) { ++bad; }
    }
    return bad;
}
}

Foam::label Foam::dcCartCutCells::decomposeConformingCones
(
    cutMesh& m, const wordList& patchNames, const wordList& patchTypes,
    const wordList& patchNeighbours, label myRank,
    const Time& runTime, const wordList& faceZoneNames,
    const wordList& patchGroups,
    const weightUnionAuthority& weightAuthority,
    const completeCavityAuthority& cavityAuthority,
    const bool earliestPhysicalPointRepair
)
{
    const char* p17ObservationFlag=getenv("DCCART_LEGACY_JOURNAL_OBSERVE");
    dcCartLegacyJournalObservation::Scope p17ObservationScope(p17ObservationFlag&&std::strcmp(p17ObservationFlag,"1")==0,myRank);
    // Optional conservative final subdivision. No original coordinate moves.
    // All unsupported inputs fail before writer authorization. Coupled bases
    // use exact coordinate-canonical cycles and collective closure.
    const char* completeContextFlag=getenv("DCCART_COMPLETE_CAVITY_CONTEXT");
    const label completeContextRequested=completeContextFlag&&std::string(completeContextFlag)=="1";
    const char* completeRepairFlag=getenv("DCCART_COMPLETE_CAVITY_REPAIR");
    const label completeRepairRequested=completeRepairFlag&&std::string(completeRepairFlag)=="1";
    // Same fixed two setup collectives encode both flags on EVERY rank.
    // No trial or local early-return branch contains MPI.
    const label completeContextFlags=completeContextRequested+2*completeRepairRequested+4*label(earliestPhysicalPointRepair);
    const label completeContextMin=returnReduce(completeContextFlags,minOp<label>());
    const label completeContextMax=returnReduce(completeContextFlags,maxOp<label>());
    if(completeContextMin!=completeContextMax)
    {FatalErrorInFunction<<"complete-cavity context flag differs across ranks"<<exit(FatalError);}
    const bool completeContextEnabled=(completeContextMin&1)!=0;
    const bool completeRepairEnabled=(completeContextMin&2)!=0;
    const bool physicalPointEnabled=(completeContextMin&4)!=0;
    if(physicalPointEnabled&&(!completeContextEnabled||!completeRepairEnabled))
    {FatalErrorInFunction<<"physical point repair requires the normal source/current context and complete final guards"<<exit(FatalError);}
    m.completeCavityRepairRequested=completeRepairEnabled;
    if(completeRepairEnabled&&!completeContextEnabled)
    {FatalErrorInFunction<<"complete-cavity repair requires its normal installed context"<<exit(FatalError);}
    label physicalPointCommitted=0;
    const label oldCells=m.nCells,oldFaces=label(m.faces.size());
    // Source-only state. No cavity is replaced/enabled by this setup.
    // Origins are fixed before any cone appends or accepted union changes.
    auto cavityAvailable=[&]()
    {
        if (!cavityAuthority.ready()) return false;
        const scalar seconds=cavityAuthority.remainingSeconds();
        return std::isfinite(seconds) && seconds>0;
    };
    if (cavityAuthority.ready())
        m.completeCavityLineage.initialize
            (std::size_t(oldCells),cavityAuthority.recordedParent,cavityAvailable);
    std::vector<std::vector<label>> cf;buildCellFaces(m,cf);
    std::vector<vector> areas(oldFaces,vector::zero);
    std::vector<point> centres(oldFaces,point::zero),apex(oldCells,point::zero);
    std::vector<scalar> parentVolume(oldCells,0);
    std::vector<bool> marked(oldCells,false),splitFace(oldFaces,false),coupled(oldFaces,false),badFace(oldFaces,false);
    std::vector<std::vector<label>> canonical(oldFaces);
    std::vector<label> badReason(oldFaces,0);std::vector<scalar> offPlane(oldFaces,0);
    std::vector<std::string> keys(oldFaces);
    label unsupportedFaces=0,seeded=0,closureWaves=0;
    auto lessPoint=[&](label a,label b)
    {
        const point& x=m.points[a];const point& y=m.points[b];
        if (x.x()!=y.x()) { return x.x()<y.x(); }
        if (x.y()!=y.y()) { return x.y()<y.y(); }
        return x.z()<y.z();
    };
    auto canonicalCycle=[&](const std::vector<label>& poly)
    {
        // Compare the same ordered rotations through views. Only the selected
        // cycle allocates storage; coordinate comparison and tie order stay exact.
        label bestStart=0,bestDirection=1;
        bool haveBest=false;
        for (label direction:{label(1),label(-1)}) for (label start=0;start<label(poly.size());++start)
        {
            bool better=false;
            if (haveBest) for (label j=0;j<label(poly.size());++j)
            {
                const label candidate=poly[(start+direction*j+label(poly.size())*2)%poly.size()];
                const label current=poly[(bestStart+bestDirection*j+label(poly.size())*2)%poly.size()];
                if (lessPoint(candidate,current)) { better=true;break; }
                if (lessPoint(current,candidate)) { break; }
            }
            if (!haveBest || better)
            { bestStart=start;bestDirection=direction;haveBest=true; }
        }
        std::vector<label> best;best.reserve(poly.size());
        for (label j=0;j<label(poly.size());++j)
        { best.push_back(poly[(bestStart+bestDirection*j+label(poly.size())*2)%poly.size()]); }
        return best;
    };
    auto exactKey=[&](const std::vector<label>& poly)
    {
        std::ostringstream out;out<<std::hexfloat;
        for (label p:poly) { out<<m.points[p].x()<<','<<m.points[p].y()<<','<<m.points[p].z()<<';'; }
        return out.str();
    };
    auto gatherKeys=[&](const std::vector<std::string>& local)
    {
        List<List<string>> byRank(Pstream::nProcs());
        List<string>& row=byRank[Pstream::myProcNo()];row.setSize(local.size());
        forAll(row,i) { row[i]=string(local[i]); }
        if (Pstream::parRun()) { Pstream::gatherList(byRank);Pstream::scatterList(byRank); }
        std::vector<std::string> all;
        forAll(byRank,rank) forAll(byRank[rank],i) { all.emplace_back(byRank[rank][i]); }
        return all;
    };
    std::vector<std::string> localCoupled;
    for (label f=0;f<oldFaces;++f)
    {
        if (!m.faceAlive[f]) { continue; }
        const auto& poly=m.faces[f];canonical[f]=canonicalCycle(poly);
        faceAreaCentreOF(m.points,canonical[f],areas[f],centres[f]);
        vector original;point originalCentre;faceAreaCentreOF(m.points,poly,original,originalCentre);
        if ((areas[f]&original)<0) { areas[f]=-areas[f]; }
        const scalar ar=mag(areas[f]);scalar span=0;
        for (label p:poly) { span=max(span,mag(m.points[p]-centres[f])); }
        if (poly.size()<3 || !std::isfinite(ar) || ar<=VSMALL || span<=VSMALL
         || !std::isfinite(centres[f].x()) || !std::isfinite(centres[f].y()) || !std::isfinite(centres[f].z()))
        { badFace[f]=true;badReason[f]|=1;++unsupportedFaces;continue; }
        const vector normal=areas[f]/ar;
        bool convex=true,collinear=false,planar=true;
        std::set<std::tuple<scalar,scalar,scalar>> unique;
        for (size_t j=0;j<poly.size();++j)
        {
            const label q=poly[j],a=poly[(j+poly.size()-1)%poly.size()],b=poly[(j+1)%poly.size()];
            const point& x=m.points[q];unique.emplace(x.x(),x.y(),x.z());
            offPlane[f]=max(offPlane[f],mag((x-centres[f])&normal));
            if (!std::isfinite(x.x()) || !std::isfinite(x.y()) || !std::isfinite(x.z())
             || mag((x-centres[f])&normal)>1e-10*span) { planar=false; }
            const vector edge=m.points[b]-x;const scalar len=mag(edge);
            if (len<=VSMALL) { planar=false;break; }
            for (label v:poly)
            { if ((((edge^(m.points[v]-x))&normal)/len)<-1e-12*span) { convex=false; } }
            const vector da=m.points[a]-x,db=m.points[b]-x;
            if ((da&db)<0 && mag(da^db)<=1e-12*mag(da)*mag(db)) { collinear=true; }
        }
        splitFace[f]=collinear || !convex;
        if (!planar) { badReason[f]|=2; }
        if (unique.size()!=poly.size()) { badReason[f]|=4; }
        if (mag(original-areas[f])/ar>1e-10 || mag(originalCentre-centres[f])/span>1e-10) { badReason[f]|=8; }
        // Keeping one original warped base changes neither its discrete
        // polygon nor its area/centre. Only split warped bases are refused.
        const bool keepWarped=getenv("DCCART_CONES_KEEP_UNSPLIT_WARPED")!=nullptr && !splitFace[f];
        if ((!planar && !keepWarped) || unique.size()!=poly.size() || (badReason[f]&8))
        { badFace[f]=true;++unsupportedFaces; }
        const label patch=m.patch[f];
        if (patch>=0 && patch<patchTypes.size())
        {
            const word& type=patchTypes[patch];
            coupled[f]=type=="processor" || type=="processorCyclic" || type=="cyclic";
            if (coupled[f])
            {
                // Bind geometry to the actual generated patch pair and ranks,
                // so an unrelated coincident coupled face cannot be its twin.
                auto cyclePair=[&](const std::string& name) -> std::string
                {
                    label index=-1;forAll(patchNames,i) if (patchNames[i]==name) { index=i;break; }
                    if (index<0 || index>=patchNeighbours.size() || patchTypes[index]!="cyclic") { return {}; }
                    const std::string other=patchNeighbours[index];label partner=-1;
                    forAll(patchNames,i) if (patchNames[i]==other) { partner=i;break; }
                    if (partner<0 || partner>=patchNeighbours.size() || patchTypes[partner]!="cyclic"
                     || patchNeighbours[partner]!=name) { return {}; }
                    return min(name,other)+":"+max(name,other);
                };
                std::string identity;
                if (type=="cyclic")
                {
                    const auto pair=cyclePair(patchNames[patch]);
                    if (!pair.empty()) { identity="local-cycle:"+std::to_string(myRank)+":"+pair; }
                }
                else
                {
                    const std::string name=patchNames[patch];
                    const std::string prefix="procBoundary"+std::to_string(myRank)+"to";
                    if (name.compare(0,prefix.size(),prefix)==0)
                    {
                        size_t end=prefix.size();while (end<name.size() && name[end]>='0' && name[end]<='9') { ++end; }
                        if (end>prefix.size())
                        {
                            const long neighbourRank=std::stol(name.substr(prefix.size(),end-prefix.size()));
                            if (neighbourRank>=0 && neighbourRank<Pstream::nProcs() && neighbourRank!=myRank)
                            {
                                const std::string ranks=std::to_string(min(long(myRank),neighbourRank))+":"+std::to_string(max(long(myRank),neighbourRank));
                                if (type=="processor" && end==name.size()) { identity="processor:"+ranks; }
                                else if (type=="processorCyclic" && name.compare(end,7,"through")==0)
                                {
                                    const auto pair=cyclePair(name.substr(end+7));
                                    if (!pair.empty()) { identity="processor-cycle:"+ranks+":"+pair; }
                                }
                            }
                        }
                    }
                }
                if (identity.empty()) { badFace[f]=true;badReason[f]|=16;++unsupportedFaces; }
                else
                {
                    keys[f]=identity+":"+exactKey(canonical[f]);
                    // Geometry alone must not let two copies on one side
                    // impersonate a missing twin. Preserve rank, actual
                    // patch role and outward orientation in the same gather.
                    vector sharedArea;point sharedCentre;
                    faceAreaCentreOF(m.points,canonical[f],sharedArea,sharedCentre);
                    const int sense=(sharedArea&original)>0 ? 1 : -1;
                    localCoupled.push_back(keys[f]+"\t"+std::to_string(myRank)
                        +"\t"+std::string(patchNames[patch])+"\t"+std::to_string(sense));
                }
            }
            else if (type.find("cyclic")==0 || type.find("mapped")==0 || type.find("AMI")!=std::string::npos)
            { badFace[f]=true;badReason[f]|=16;++unsupportedFaces; }
        }
    }
    struct coupledRole { label rank;std::string patch;int sense; };
    std::map<std::string,std::vector<coupledRole>> coupledRoles;
    for (const auto& record:gatherKeys(localCoupled))
    {
        const auto c=record.rfind('\t'),b=record.rfind('\t',c-1),a=record.rfind('\t',b-1);
        const std::string key=record.substr(0,a);
        coupledRoles[key].push_back(coupledRole{label(std::stol(record.substr(a+1,b-a-1))),
            record.substr(b+1,c-b-1),std::stoi(record.substr(c+1))});
    }
    auto qualifiedTwin=[&](const std::string& key)
    {
        const auto found=coupledRoles.find(key);
        if (found==coupledRoles.end() || found->second.size()!=2) { return false; }
        const auto& a=found->second[0];const auto& b=found->second[1];
        if (a.sense!=-b.sense) { return false; }
        if (key.compare(0,12,"local-cycle:")==0) { return a.rank==b.rank && a.patch!=b.patch; }
        if (a.rank==b.rank) { return false; }
        if (key.compare(0,16,"processor-cycle:")==0)
        {
            const auto ia=a.patch.find("through"),ib=b.patch.find("through");
            return ia!=std::string::npos && ib!=std::string::npos && a.patch.substr(ia+7)!=b.patch.substr(ib+7);
        }
        return true;
    };
    for (label c=0;c<oldCells;++c)
    {
        if (!m.cellAlive[c] || m.cellRank[c]!=myRank || cf[c].empty()) { continue; }
        point estimate(point::zero);for (label f:cf[c]) { estimate+=centres[f]; }estimate/=cf[c].size();
        scalar volume3=0;point moment(point::zero);
        for (label f:cf[c])
        {
            const vector a=m.owner[f]==c ? areas[f] : -areas[f];
            const scalar v=a&(centres[f]-estimate);volume3+=v;moment+=v*(.75*centres[f]+.25*estimate);
        }
        parentVolume[c]=volume3/3;apex[c]=moment/max(volume3,VSMALL);
        bool flag=false;
        for (label f:cf[c])
        {
            if (splitFace[f] || badFace[f]) { flag=true;break; }
            vector normal=areas[f]/max(mag(areas[f]),VSMALL);if (m.owner[f]!=c) { normal=-normal; }
            for (label other:cf[c]) if (other!=f)
            {
                const vector d=centres[other]-centres[f];
                if ((d&normal)/max(mag(d),VSMALL)>-1e-6) { flag=true;break; }
            }
            if (flag) { break; }
        }
        if (flag) { marked[c]=true;++seeded; }
    }
    // Sharing a split base creates mandatory neighbour closure, including
    // exact-coordinate coupled counterparts. No rank-local ID enters it.
    while (true)
    {
        label changed=0;std::vector<std::string> selected;
        for (label f=0;f<oldFaces;++f)
        {
            if (!m.faceAlive[f] || !splitFace[f]) { continue; }
            const label o=m.owner[f],n=m.neighbour[f];
            if (n>=0 && (marked[o] || marked[n]))
            {
                if (!marked[o]) { marked[o]=true;++changed; }
                if (!marked[n]) { marked[n]=true;++changed; }
            }
            if (coupled[f] && marked[o]) { selected.push_back(keys[f]); }
        }
        std::set<std::string> global;
        for (const auto& key:gatherKeys(selected)) { global.insert(key); }
        for (label f=0;f<oldFaces;++f)
        {
            if (m.faceAlive[f] && coupled[f] && splitFace[f] && global.count(keys[f]) && !marked[m.owner[f]])
            { marked[m.owner[f]]=true;++changed; }
        }
        if (!returnReduce(changed,sumOp<label>())) { break; }
        ++closureWaves;
    }
    label selectedCells=0,badKernel=0,badBases=0,badCoupled=0,badClosure=0;
    for (label c=0;c<oldCells;++c)
    {
        if (!marked[c]) { continue; }++selectedCells;
        const point& cc=apex[c];
        if (!std::isfinite(parentVolume[c]) || parentVolume[c]<=VSMALL
         || !std::isfinite(cc.x()) || !std::isfinite(cc.y()) || !std::isfinite(cc.z())) { ++badKernel;continue; }
        std::map<std::pair<label,label>,std::pair<label,label>> edgeCounts;
        for (label f:cf[c])
        {
            if (badFace[f]) { ++badBases; }
            const vector a=m.owner[f]==c ? areas[f] : -areas[f];
            const scalar margin=(a&(centres[f]-cc))/max(mag(a),VSMALL);
            if (!std::isfinite(margin) || margin<=3e-6) { ++badKernel; }
            if (coupled[f] && splitFace[f] && !qualifiedTwin(keys[f])) { ++badCoupled; }
            const auto& poly=m.faces[f];
            for (size_t j=0;j<poly.size();++j)
            {
                label a=poly[j],b=poly[(j+1)%poly.size()];if (m.owner[f]!=c) { std::swap(a,b); }
                auto& row=edgeCounts[std::minmax(a,b)];++row.first;row.second+=a<b ? 1 : -1;
            }
        }
        for (const auto& edge:edgeCounts) if (edge.second.first!=2 || edge.second.second!=0) { ++badClosure; }
    }
    if (getenv("DCCART_CONE_EVIDENCE")!=nullptr)
    {
        std::ofstream out("conforming-cone-preflight-rank"+std::to_string(Pstream::myProcNo())+".json");out<<std::setprecision(17);
        auto xyz=[&](const vector& p) { out<<'['<<p.x()<<','<<p.y()<<','<<p.z()<<']'; };
        out<<"{\"rank\":"<<Pstream::myProcNo()<<",\"badBaseFaces\":[";bool first=true;
        for (label f=0;f<oldFaces;++f)
        {
            if (!m.faceAlive[f] || !badFace[f]) { continue; }
            const label o=m.owner[f],n=m.neighbour[f];if (!marked[o] && !(n>=0 && marked[n])) { continue; }
            if (!first) { out<<','; }first=false;
            vector originalArea;point originalCentre;faceAreaCentreOF(m.points,m.faces[f],originalArea,originalCentre);
            out<<"{\"face\":"<<f<<",\"owner\":"<<o<<",\"neighbour\":"<<n<<",\"patch\":"<<m.patch[f]
                <<",\"zone\":"<<m.zone[f]<<",\"reasonMask\":"<<badReason[f]<<",\"splitRequired\":"<<(splitFace[f] ? "true" : "false")
                <<",\"maxOffPlane\":"<<offPlane[f]<<",\"originalCentre\":";xyz(originalCentre);
            out<<",\"canonicalCentre\":";xyz(centres[f]);out<<",\"originalArea\":";xyz(originalArea);
            out<<",\"canonicalOrientedArea\":";xyz(areas[f]);out<<",\"ownerApex\":";xyz(apex[o]);
            if (n>=0) { out<<",\"neighbourApex\":";xyz(apex[n]); }
            out<<",\"points\":[";
            for (size_t j=0;j<m.faces[f].size();++j) { if (j) { out<<','; }xyz(m.points[m.faces[f][j]]); }
            out<<"]}";
        }
        out<<"]}\n";
    }
    selectedCells=returnReduce(selectedCells,sumOp<label>());seeded=returnReduce(seeded,sumOp<label>());
    badKernel=returnReduce(badKernel,sumOp<label>());badBases=returnReduce(badBases,sumOp<label>());
    badCoupled=returnReduce(badCoupled,sumOp<label>());badClosure=returnReduce(badClosure,sumOp<label>());
    Pout<<"Conforming cone synchronized preflight: rank "<<Pstream::myProcNo()<<", global selection "<<selectedCells
        <<", bad kernel/base/coupled/closure "<<badKernel<<' '<<badBases<<' '<<badCoupled<<' '<<badClosure<<endl;
    Info<<"Conforming cone preflight: seed cells "<<seeded<<", closed selection "<<selectedCells
        <<", closure waves "<<closureWaves<<"; unsupported [kernel,base,coupled,closure] "
        <<badKernel<<' '<<badBases<<' '<<badCoupled<<' '<<badClosure<<endl;
    Pstream::barrier(Pstream::worldComm);
    if (badKernel || badBases || badCoupled || badClosure)
    { FatalErrorInFunction<<"conforming cone unsupported geometry; source coordinates preserved; no mesh authorization"<<exit(FatalError); }
    // Spatial guard includes every existing AND every newly inserted point.
    // This prevents the writer's 1-micrometre canonicalization changing bases.
    struct gridKey { long long x,y,z;bool operator==(const gridKey& b) const { return x==b.x&&y==b.y&&z==b.z; } };
    struct gridHash { size_t operator()(const gridKey& q) const { return splitMix64(uint64_t(q.x)^splitMix64(uint64_t(q.y))^splitMix64(uint64_t(q.z))); } };
    std::unordered_map<gridKey,std::vector<label>,gridHash> pointGrid;
    auto gridOf=[](const point& p) { return gridKey{(long long)std::floor(p.x()/3e-6),(long long)std::floor(p.y()/3e-6),(long long)std::floor(p.z()/3e-6)}; };
    for (label p=0;p<label(m.points.size());++p) { pointGrid[gridOf(m.points[p])].push_back(p); }
    auto insertPoint=[&](const point& p,bool allowExact) -> label
    {
        const gridKey q=gridOf(p);label exact=-1;
        for (label x=-1;x<=1;++x) for (label y=-1;y<=1;++y) for (label z=-1;z<=1;++z)
        {
            auto found=pointGrid.find(gridKey{q.x+x,q.y+y,q.z+z});if (found==pointGrid.end()) { continue; }
            for (label old:found->second)
            {
                const scalar d=magSqr(p-m.points[old]);
                if (d<=9e-12)
                {
                    if (allowExact && d==0) { exact=old; }
                    else { return -1; }
                }
            }
        }
        if (exact>=0) { return exact; }
        const label id=m.points.size();m.points.push_back(p);pointGrid[q].push_back(id);return id;
    };
    const bool traceCones=getenv("DCCART_CONE_EVIDENCE")!=nullptr;
    const bool literalUnions=getenv("DCCART_CONE_LITERAL_UNIONS")!=nullptr;
    const bool samePhysicalRole=literalUnions && getenv("DCCART_CONE_SAME_PHYSICAL_ROLE")!=nullptr;

    // Exact flag, inside the existing all-rank literal experiment only.
    // Missing/mismatched authority refuses W while retaining the old D path.
    bool weightUnions=false;
    if (literalUnions)
    {
        const char* flag=getenv("DCCART_CROSS_PARENT_WEIGHT_UNIONS");
        const label requested=flag && std::string(flag)=="1";
        // Unconditional agreement at the same all-rank setup as W.
        const char* dflag=getenv("DCCART_CROSS_PARENT_D_UNIONS");
        const label drequested=dflag && std::string(dflag)=="1";
        const label dlo=returnReduce(drequested,minOp<label>());
        const label dhi=returnReduce(drequested,maxOp<label>());
        if(dlo!=dhi)
        { FatalErrorInFunction<<"D-seed flag differs across ranks; no mesh written"<<exit(FatalError); }
        const label lo=returnReduce(requested,minOp<label>());
        const label hi=returnReduce(requested,maxOp<label>());
        if (lo!=hi)
        { FatalErrorInFunction<<"W-only flag differs across ranks; no mesh written"<<exit(FatalError); }
        if (lo)
        {
            m.weightUnionState=std::make_shared<writerWeightUnionState>();
            auto& state=*m.weightUnionState;state.authority=weightAuthority;
            const bool local=weightAuthority.globallyNoMaterialNoBulk
                && dcCartWeightUnionPreview::sha(weightAuthority.rawSha256)
                && dcCartWeightUnionPreview::sha(weightAuthority.rolesDigest)
                && !weightAuthority.homogeneousFluidKey.empty()
                && bool(weightAuthority.homogeneousOrigins)
                && bool(weightAuthority.remainingSeconds);
            List<string> roles(Pstream::nProcs());
            roles[Pstream::myProcNo()]=weightAuthority.rawSha256+"|"
                +weightAuthority.rolesDigest+"|"+weightAuthority.homogeneousFluidKey;
            Pstream::gatherList(roles);Pstream::scatterList(roles);
            bool agree=true;for (const auto& row:roles) { agree=agree&&row==roles[0]; }
            state.enabled=returnReduce(label(local&&agree),minOp<label>())==1;
            state.dEnabled=dcCartWeightUnionPreview::dAuthority(dlo,state.enabled);
            weightUnions=state.enabled;
            if (!weightUnions && Pstream::master())
            { WarningInFunction<<"W-only homogeneous source authority absent/unknown; old literal path retained"<<nl; }
        }
    }
    // Algorithm ancestry is local and independent of optional evidence output.
    // No allocation or iteration is added when this experiment is off.
    std::vector<label> unionParent;
    if (literalUnions)
    {
        unionParent.resize(oldCells);
        std::iota(unionParent.begin(),unionParent.end(),label(0));
    }
    // Source-facet lineage is algorithm state, never optional trace state.
    std::vector<label> unionBase;
    if (samePhysicalRole)
    {
        unionBase.resize(oldFaces);std::iota(unionBase.begin(),unionBase.end(),label(0));
    }
    if (traceCones)
    {
        m.coneEvidenceParent.resize(oldCells);m.coneEvidenceBase.resize(oldFaces);
        for (label c=0;c<oldCells;++c) { m.coneEvidenceParent[c]=c; }
        for (label f=0;f<oldFaces;++f) { m.coneEvidenceBase[f]=f; }
        const std::string prefix="conforming-source-rank"+std::to_string(myRank)+"-";
        auto writeRaw=[&](const std::string& name,const char* data,size_t bytes)
        {
            std::ofstream out(prefix+name,std::ios::binary);out.write(data,bytes);
            if (!out) { FatalErrorInFunction<<"could not write optional cone source evidence "<<name<<exit(FatalError); }
        };
        writeRaw("points.f64",reinterpret_cast<const char*>(m.points.data()),m.points.size()*sizeof(point));
        writeRaw("owner.i32",reinterpret_cast<const char*>(m.owner.data()),oldFaces*sizeof(label));
        writeRaw("neighbour.i32",reinterpret_cast<const char*>(m.neighbour.data()),oldFaces*sizeof(label));
        writeRaw("patch.i32",reinterpret_cast<const char*>(m.patch.data()),oldFaces*sizeof(label));
        writeRaw("zone.i32",reinterpret_cast<const char*>(m.zone.data()),oldFaces*sizeof(label));
        writeRaw("apex.f64",reinterpret_cast<const char*>(apex.data()),oldCells*sizeof(point));
        writeRaw("parent-volume.f64",reinterpret_cast<const char*>(parentVolume.data()),oldCells*sizeof(scalar));
        std::vector<uint64_t> offsets(oldFaces+1,0);
        for (label f=0;f<oldFaces;++f) { offsets[f+1]=offsets[f]+m.faces[f].size(); }
        writeRaw("face-offsets.u64",reinterpret_cast<const char*>(offsets.data()),offsets.size()*sizeof(uint64_t));
        std::ofstream pointsFile(prefix+"face-points.i32",std::ios::binary);
        for (label f=0;f<oldFaces;++f) { pointsFile.write(reinterpret_cast<const char*>(m.faces[f].data()),m.faces[f].size()*sizeof(label)); }
        std::vector<unsigned char> faceAlive(oldFaces,0),cellAlive(oldCells,0);
        for (label f=0;f<oldFaces;++f) { faceAlive[f]=m.faceAlive[f]; }
        for (label c=0;c<oldCells;++c) { cellAlive[c]=m.cellAlive[c]; }
        writeRaw("face-alive.u8",reinterpret_cast<const char*>(faceAlive.data()),faceAlive.size());
        writeRaw("cell-alive.u8",reinterpret_cast<const char*>(cellAlive.data()),cellAlive.size());
        if (!pointsFile) { FatalErrorInFunction<<"could not write optional cone face evidence"<<exit(FatalError); }
        const uint16_t endianMarker=1;
        std::ofstream meta(prefix+"manifest.json");meta<<"{\"format\":\"conforming-input-stage/v1\",\"rank\":"<<myRank
            <<",\"points\":"<<m.points.size()<<",\"faces\":"<<oldFaces<<",\"cells\":"<<oldCells
            <<",\"facePointLabels\":"<<offsets.back()<<",\"labelBytes\":"<<sizeof(label)
            <<",\"scalarBytes\":"<<sizeof(scalar)<<",\"pointStrideBytes\":"<<sizeof(point)
            <<",\"littleEndian\":"<<(reinterpret_cast<const char*>(&endianMarker)[0]==1 ? "true":"false")
            <<",\"parentScope\":\"local alive cell immediately before this optional decomposition; not a surface or octree ancestor\"}\n";
        if (!meta) { FatalErrorInFunction<<"could not write optional cone manifest"<<exit(FatalError); }
    }
    // Explicit diagnostic candidate only. The original input-stage snapshot
    // above remains unchanged; the final stock checker is still mandatory.
    // No case coordinates or selection are part of the default algorithm.
    if (const char* kernelTest=getenv("DCCART_CONE_KERNEL_TEST"))
    {
        label rank=-1,parent=-1;point candidate(point::zero);std::string extra;
        std::istringstream input(kernelTest);
        bool parse=bool(input>>rank>>parent>>candidate.x()>>candidate.y()>>candidate.z());
        parse=parse && !(input>>extra) && rank>=0 && rank<Pstream::nProcs()
            && parent>=0 && std::isfinite(candidate.x()) && std::isfinite(candidate.y()) && std::isfinite(candidate.z());
        label applied=0,invalid=parse ? 0 : 1;
        if (parse && rank==myRank)
        {
            if (parent>=oldCells || !m.cellAlive[parent] || !marked[parent]) { ++invalid; }
            else
            {
                scalar margin=GREAT;
                for (label f:cf[parent])
                {
                    const vector a=m.owner[f]==parent ? areas[f] : -areas[f];
                    const scalar d=(a&(centres[f]-candidate))/max(mag(a),VSMALL);
                    if (!std::isfinite(d)) { ++invalid; }margin=min(margin,d);
                }
                if (!std::isfinite(margin) || margin<=3e-6) { ++invalid; }
                if (!invalid)
                {
                    Pout.precision(17);Pout<<"Conforming kernel test: rank "<<myRank<<", parent "<<parent
                        <<", original "<<apex[parent]<<", candidate "<<candidate<<", minimum plane margin "<<margin<<endl;
                    apex[parent]=candidate;++applied;
                }
            }
        }
        invalid=returnReduce(invalid,sumOp<label>());applied=returnReduce(applied,sumOp<label>());
        if (invalid || applied!=1)
        { FatalErrorInFunction<<"explicit kernel diagnostic refused: invalid "<<invalid<<", applied "<<applied<<exit(FatalError); }
    }
    std::vector<std::vector<label>> piecesOf(oldFaces);
    label partitioned=0,faceFan=0,anchorFan=0,partitionFailures=0,closePoints=0,momentCorrections=0;
    std::ofstream partitionAudit;
    bool firstPartitionWitness=true;
    if (getenv("DCCART_CONE_EVIDENCE"))
    {
        partitionAudit.open("conforming-cone-partition-rank"+std::to_string(myRank)+".json");
        partitionAudit<<std::setprecision(17)<<"[";
    }
    // Match the writer's absolute area gate, as well as the signed fan
    // criterion. Roundoff-positive collinear triangles would otherwise be
    // discarded by toPolyMesh, leaving a three-faced open tetrahedron.
    auto writablePolygon=[&](const std::vector<label>& poly)
    {
        vector n;point c;polygonNormalCentre(m.points,poly,n,c);
        return std::isfinite(n.x()) && std::isfinite(n.y()) && std::isfinite(n.z())
            && std::isfinite(c.x()) && std::isfinite(c.y()) && std::isfinite(c.z())
            && mag(n)>1e-12;
    };
    label qualityFanTriggered=0,qualityFanChanged=0,qualityFanUnsupported=0;
    struct fanQuality
    {
        bool geometry=true,possible=true;
        scalar weight=.5,ratio=1,shape=GREAT,triangleBound=GREAT;
    };
    const bool strongTriangleBound=getenv("DCCART_CONES_STRONG_TRIANGLE_BOUND")!=nullptr;
    const bool selectedFourTriangleBound=getenv("DCCART_CONES_SELECTED_FOUR_TRIANGLE_BOUND")!=nullptr;
    std::map<std::array<label,4>,scalar> triangleBoundCache;
    // Directed, enclosing long-double operations give an upper bound from
    // the exact binary input coordinates. A low upper bound proves only that
    // this physical TRIANGLE cannot be the base of an accepted tetrahedron.
    // It does not exclude preserving that triangle on a larger polyhedron.
    auto perimeterTriangleUpperBound=[&](const std::vector<label>& poly,label coefficient) -> scalar
    {
        struct interval { long double lo,hi; };
        const long double inf=std::numeric_limits<long double>::infinity();
        auto down=[&](long double x) { return std::nextafter(x,-inf); };
        auto up=[&](long double x) { return std::nextafter(x,inf); };
        auto sub=[&](interval a,interval b) { return interval{down(a.lo-b.hi),up(a.hi-b.lo)}; };
        auto add=[&](interval a,interval b) { return interval{down(a.lo+b.lo),up(a.hi+b.hi)}; };
        auto mul=[&](interval a,interval b)
        {
            const std::array<long double,4> v{{a.lo*b.lo,a.lo*b.hi,a.hi*b.lo,a.hi*b.hi}};
            return interval{down(*std::min_element(v.begin(),v.end())),up(*std::max_element(v.begin(),v.end()))};
        };
        auto sq=[&](interval a)
        {
            const long double low=a.lo<=0 && a.hi>=0 ? 0 : std::min(a.lo*a.lo,a.hi*a.hi);
            return interval{std::max(0.L,down(low)),up(std::max(a.lo*a.lo,a.hi*a.hi))};
        };
        auto difference=[&](label a,label b)
        {
            std::array<interval,3> d;
            for (label axis=0;axis<3;++axis)
            {
                const long double x=m.points[a][axis],y=m.points[b][axis];
                d[axis]=sub(interval{x,x},interval{y,y});
            }
            return d;
        };
        if (poly.size()!=3) { return GREAT; }
        std::array<label,4> key{{poly[0],poly[1],poly[2],coefficient}};std::sort(key.begin(),key.begin()+3);
        auto found=triangleBoundCache.find(key);if (found!=triangleBoundCache.end()) { return found->second; }
        // Memoization cannot make a shared decision depend on which local
        // rotated triangle happened to fill the cache first. Compute every
        // interval from the same exact coordinate order on either side.
        std::array<label,3> triangle{{poly[0],poly[1],poly[2]}};
        std::sort(triangle.begin(),triangle.end(),[&](label a,label b)
        {
            const point& x=m.points[a];const point& y=m.points[b];
            return std::make_tuple(x.x(),x.y(),x.z())<std::make_tuple(y.x(),y.y(),y.z());
        });
        const auto a=difference(triangle[1],triangle[0]),b=difference(triangle[2],triangle[0]);
        std::array<interval,3> cross;
        for (label axis=0;axis<3;++axis)
        { const label i=(axis+1)%3,j=(axis+2)%3;cross[axis]=sub(mul(a[i],b[j]),mul(a[j],b[i])); }
        interval crossSqr{0,0};for (const auto& x:cross) { crossSqr=add(crossSqr,sq(x)); }
        const long double areaSqrUpper=up(crossSqr.hi/4);
        long double perimeterLower=0;
        for (label edge=0;edge<3;++edge)
        {
            const auto d=difference(triangle[(edge+1)%3],triangle[edge]);interval lengthSqr{0,0};
            for (const auto& x:d) { lengthSqr=add(lengthSqr,sq(x)); }
            if (!std::isfinite(lengthSqr.lo) || lengthSqr.lo<=0) { return GREAT; }
            perimeterLower=down(perimeterLower+down(std::sqrt(lengthSqr.lo)));
        }
        const long double p2=down(perimeterLower*perimeterLower),p4=down(p2*p2);
        const long double bound=up(up(static_cast<long double>(coefficient)*areaSqrUpper)/p4);
        if (!std::isfinite(bound) || bound<=0 || p4<=0) { return GREAT; }
        scalar out=scalar(bound);
        if (static_cast<long double>(out)<bound) { out=std::nextafter(out,std::numeric_limits<scalar>::infinity()); }
        triangleBoundCache.emplace(key,out);return out;
    };
    auto fanScore=[&](const std::vector<std::vector<label>>& pieces,const vector& normal,bool physical)
    {
        fanQuality out;
        struct edgeGeometry { size_t piece;label a,b; };
        std::map<std::pair<label,label>,std::vector<edgeGeometry>> edges;
        std::vector<scalar> amounts;std::vector<point> fcs;
        for (size_t k=0;k<pieces.size();++k)
        {
            const auto& poly=pieces[k];vector a;point fc;faceAreaCentreOF(m.points,poly,a,fc);
            const scalar amount=mag(a);scalar perimeter=0,maxEdge=0;
            if (poly.size()<3 || !writablePolygon(poly) || !std::isfinite(amount) || amount<=VSMALL || (a&normal)<=0)
            { out.geometry=false; }
            for (size_t j=0;j<poly.size();++j)
            {
                const point& pa=m.points[poly[j]];const point& pb=m.points[poly[(j+1)%poly.size()]];
                const point& pc=m.points[poly[(j+2)%poly.size()]];
                const vector edge=pb-pa;const scalar length=mag(edge);
                perimeter+=length;maxEdge=max(maxEdge,length);
                // Every retained edge node is essential: collinear adjacent
                // edges create coplanar cone sides in the stock cell check.
                const scalar turn=((edge^(pc-pb))&normal);
                if (!std::isfinite(turn) || turn<=1e-12*amount || length<=VSMALL) { out.geometry=false; }
                for (label q:poly)
                { if ((((edge^(m.points[q]-pa))&normal)/max(length,VSMALL)) < -1e-12*std::sqrt(amount)) { out.geometry=false; } }
                edges[std::minmax(poly[j],poly[(j+1)%poly.size()])].push_back(edgeGeometry{k,poly[j],poly[(j+1)%poly.size()]});
            }
            const scalar shape=2*amount/max(sqr(perimeter),VSMALL);
            if (!std::isfinite(shape) || shape<=0) { out.geometry=false; }out.shape=min(out.shape,shape);
            if (poly.size()==3 && (physical || selectedFourTriangleBound))
            {
                // Proven any-apex upper bound for a tetrahedron with this
                // fixed physical base and three selected internal side faces.
                // It only rejects impossibility; a larger bound is not proof.
                // S >= sqrt(A^2+h^2 P^2/4) and max x^2/(1+x)^3=4/27
                // imply D <= 54 A^2/P^4, independently of the apex.
                // All four internal/coupled Sf yield the separate bound
                // D <= 128 A^2/P^4 (average area uses four faces). A physical
                // base is excluded by OF and retains the three-side bound 54.
                const scalar bound=(strongTriangleBound || !physical)
                    ? perimeterTriangleUpperBound(poly,physical ? 54 : 128)
                    : scalar(729)*sqr(amount)/(32*max(sqr(sqr(maxEdge)),VSMALL));
                out.triangleBound=min(out.triangleBound,bound);
                if (!std::isfinite(bound) || bound<.001) { out.possible=false; }
            }
            amounts.push_back(amount);fcs.push_back(fc);
        }
        for (const auto& row:edges)
        {
            const auto& use=row.second;if (use.size()==1) { continue; }
            if (use.size()!=2 || use[0].a!=use[1].b || use[0].b!=use[1].a || use[0].piece==use[1].piece)
            { out.geometry=false;continue; }
            const vector edge=m.points[use[0].b]-m.points[use[0].a];
            const point& origin=m.points[use[0].a];
            const scalar da=mag((edge^(fcs[use[0].piece]-origin))&normal);
            const scalar db=mag((edge^(fcs[use[1].piece]-origin))&normal);
            const scalar weight=min(da,db)/max(da+db,VSMALL);
            const scalar ratio=min(amounts[use[0].piece],amounts[use[1].piece])/max(amounts[use[0].piece],amounts[use[1].piece]);
            out.weight=min(out.weight,weight);out.ratio=min(out.ratio,ratio);
        }
        if (!std::isfinite(out.weight) || !std::isfinite(out.ratio) || out.weight<.05 || out.ratio<.01) { out.possible=false; }
        return out;
    };
    auto improveCanonicalFan=[&](const std::vector<label>& poly,const vector& normal,bool physical,
                                  std::vector<std::vector<label>>& pieces)
    {
        if (poly.size()>8 || pieces.size()<2) { return; }
        const fanQuality old=fanScore(pieces,normal,physical);
        if (old.geometry && old.possible) { return; }++qualityFanTriggered;
        std::vector<std::vector<label>> best;std::array<scalar,4> bestScore{{-GREAT,-GREAT,-GREAT,-GREAT}};
        const size_t n=poly.size();scalar faceArea=0;vector av;point fc;
        faceAreaCentreOF(m.points,poly,av,fc);faceArea=mag(av);
        for (size_t anchor=0;anchor<n;++anchor)
        {
            bool positive=true;
            for (size_t j=1;j+1<n;++j)
            {
                const scalar a=(((m.points[poly[(anchor+j)%n]]-m.points[poly[anchor]])
                    ^(m.points[poly[(anchor+j+1)%n]]-m.points[poly[anchor]]))&normal)*.5;
                if (!std::isfinite(a) || a<=1e-15*faceArea
                 || !writablePolygon({poly[anchor],poly[(anchor+j)%n],poly[(anchor+j+1)%n]})) { positive=false;break; }
            }
            if (!positive) { continue; }
            std::vector<std::vector<label>> trial;
            std::function<void(size_t)> walk=[&](size_t first)
            {
                if (first==n-1)
                {
                    const fanQuality score=fanScore(trial,normal,physical);
                    if (!score.geometry || !score.possible) { return; }
                    const std::array<scalar,4> key{{score.shape,score.weight,score.ratio,-scalar(trial.size())}};
                    // Canonical coordinate order, anchor order and recursion
                    // order resolve exact ties identically on both sides.
                    if (best.empty() || key>bestScore) { bestScore=key;best=trial; }return;
                }
                for (size_t count=1;count<=3 && first+count<=n-1;++count)
                {
                    std::vector<label> merged{poly[anchor]};
                    for (size_t j=first;j<=first+count;++j) { merged.push_back(poly[(anchor+j)%n]); }
                    const fanQuality local=fanScore({merged},normal,physical);
                    if (!local.geometry || !local.possible) { continue; }
                    trial.push_back(std::move(merged));walk(first+count);trial.pop_back();
                }
            };
            walk(1);
        }
        if (getenv("DCCART_CONES_EAR_REFAN"))
        {
            // The canonical indices, not rank-local point labels, define every
            // diagonal, triangle, merge and tie. Enumerate only n<=8 outlines:
            // at most 132 triangulations and 32 connected merge subsets each.
            // A visible diagonal cannot touch any other original boundary node.
            // Consequently all original outline nodes remain on the boundary.
            const scalar span=max(std::sqrt(faceArea),scalar(1e-6));
            const scalar areaTol=1e-12*faceArea;
            auto at=[&](size_t i) -> const point& { return m.points[poly[i]]; };
            auto crossAt=[&](const point& a,const point& b,const point& c)
            { return (((b-a)^(c-a))&normal); };
            auto onSegment=[&](const point& a,const point& b,const point& q)
            {
                const vector e=b-a;const scalar l=mag(e),t=((q-a)&e)/max(magSqr(e),VSMALL);
                return mag(crossAt(a,b,q))<=areaTol && t>=-1e-12 && t<=1+1e-12 && l>1e-12*span;
            };
            label drop=0;for (label axis=1;axis<3;++axis) if (mag(normal[axis])>mag(normal[drop])) { drop=axis; }
            const label xAxis=(drop+1)%3,yAxis=(drop+2)%3;
            auto inside=[&](const point& q)
            {
                bool hit=false;
                for (size_t i=0;i<n;++i)
                {
                    const point& a=at(i);const point& b=at((i+1)%n);
                    if (onSegment(a,b,q)) { return false; }
                    if ((a[yAxis]>q[yAxis])!=(b[yAxis]>q[yAxis]))
                    {
                        const scalar x=a[xAxis]+(q[yAxis]-a[yAxis])*(b[xAxis]-a[xAxis])/(b[yAxis]-a[yAxis]);
                        if (q[xAxis]<x) { hit=!hit; }
                    }
                }
                return hit;
            };
            std::vector<std::vector<bool>> diagonal(n,std::vector<bool>(n,false));
            for (size_t i=0;i<n;++i) for (size_t j=i+1;j<n;++j)
            {
                bool valid=j==i+1 || (i==0 && j+1==n);
                if (!valid)
                {
                    valid=true;const point& a=at(i);const point& b=at(j);
                    for (size_t k=0;k<n;++k)
                    { if (k!=i && k!=j && onSegment(a,b,at(k))) { valid=false;break; } }
                    for (size_t k=0;valid && k<n;++k)
                    {
                        const size_t next=(k+1)%n;
                        if (k==i || k==j || next==i || next==j) { continue; }
                        const point& c=at(k);const point& d=at(next);
                        const scalar s1=crossAt(a,b,c),s2=crossAt(a,b,d),t1=crossAt(c,d,a),t2=crossAt(c,d,b);
                        if ((s1*s2<0 && t1*t2<0) || onSegment(a,b,c) || onSegment(a,b,d)
                         || onSegment(c,d,a) || onSegment(c,d,b)) { valid=false; }
                    }
                    if (valid && !inside(.5*(a+b))) { valid=false; }
                }
                diagonal[i][j]=diagonal[j][i]=valid;
            }
            using triangleIndices=std::array<size_t,3>;
            using triangulation=std::vector<triangleIndices>;
            std::map<std::pair<size_t,size_t>,std::vector<triangulation>> cache;
            std::function<const std::vector<triangulation>&(size_t,size_t)> triangulate;
            triangulate=[&](size_t first,size_t last) -> const std::vector<triangulation>&
            {
                const auto key=std::make_pair(first,last);auto found=cache.find(key);
                if (found!=cache.end()) { return found->second; }
                auto& out=cache[key];
                if (last<=first+1) { out.push_back({});return out; }
                for (size_t middle=first+1;middle<last;++middle)
                {
                    if (!diagonal[first][middle] || !diagonal[middle][last] || !diagonal[first][last]) { continue; }
                    const scalar amount=.5*crossAt(at(first),at(middle),at(last));
                    if (!std::isfinite(amount) || amount<=1e-15*faceArea
                     || !writablePolygon({poly[first],poly[middle],poly[last]})) { continue; }
                    const auto& left=triangulate(first,middle);const auto& right=triangulate(middle,last);
                    for (const auto& a:left) for (const auto& b:right)
                    {
                        triangulation trial=a;trial.insert(trial.end(),b.begin(),b.end());
                        trial.push_back(triangleIndices{{first,middle,last}});out.push_back(std::move(trial));
                    }
                }
                return out;
            };
            const auto& all=triangulate(0,n-1);
            std::set<std::vector<std::vector<size_t>>> seen;
            for (const auto& triangles:all)
            {
                if (triangles.size()!=n-2) { continue; }
                std::map<std::pair<size_t,size_t>,std::vector<size_t>> edgeUses;
                for (size_t i=0;i<triangles.size();++i) for (size_t e=0;e<3;++e)
                { edgeUses[std::minmax(triangles[i][e],triangles[i][(e+1)%3])].push_back(i); }
                std::vector<std::pair<size_t,size_t>> joins;bool legal=true;
                for (const auto& edge:edgeUses)
                {
                    if (edge.second.size()==2) { joins.emplace_back(edge.second[0],edge.second[1]); }
                    else if (edge.second.size()!=1) { legal=false; }
                }
                if (!legal || joins.size()!=n-3) { continue; }
                for (size_t mask=0;mask<(size_t(1)<<joins.size());++mask)
                {
                    std::vector<size_t> parent(triangles.size());std::iota(parent.begin(),parent.end(),size_t(0));
                    auto root=[&](size_t a) { while (parent[a]!=a) { a=parent[a]; }return a; };
                    for (size_t e=0;e<joins.size();++e) if (mask&(size_t(1)<<e))
                    { const size_t a=root(joins[e].first),b=root(joins[e].second);parent[max(a,b)]=min(a,b); }
                    std::map<size_t,std::vector<size_t>> groups;
                    for (size_t i=0;i<triangles.size();++i) { groups[root(i)].push_back(i); }
                    std::vector<std::vector<size_t>> cycles;bool closed=true;
                    for (const auto& group:groups)
                    {
                        std::map<std::pair<size_t,size_t>,std::vector<std::pair<size_t,size_t>>> boundary;
                        for (size_t i:group.second) for (size_t e=0;e<3;++e)
                        {
                            const size_t a=triangles[i][e],b=triangles[i][(e+1)%3];
                            boundary[std::minmax(a,b)].emplace_back(a,b);
                        }
                        std::map<size_t,size_t> next;std::map<size_t,label> incoming;
                        for (const auto& edge:boundary)
                        {
                            const auto& use=edge.second;
                            if (use.size()==2)
                            { if (use[0].first!=use[1].second || use[0].second!=use[1].first) { closed=false; }continue; }
                            if (use.size()!=1 || !next.emplace(use[0].first,use[0].second).second) { closed=false;break; }
                            ++incoming[use[0].second];
                        }
                        if (!closed || next.size()<3) { closed=false;break; }
                        for (const auto& edge:next) if (incoming[edge.first]!=1) { closed=false; }
                        if (!closed) { break; }
                        const size_t first=next.begin()->first;size_t current=first;std::vector<size_t> cycle;
                        do
                        {
                            if (cycle.size()>=next.size() || !next.count(current)) { closed=false;break; }
                            cycle.push_back(current);current=next[current];
                        } while (current!=first);
                        if (!closed || cycle.size()!=next.size()) { closed=false;break; }
                        cycles.push_back(std::move(cycle));
                    }
                    if (!closed) { continue; }std::sort(cycles.begin(),cycles.end());
                    if (!seen.insert(cycles).second) { continue; }
                    std::vector<std::vector<label>> trial;std::set<size_t> retained;
                    for (const auto& cycle:cycles)
                    {
                        std::vector<label> face;for (size_t index:cycle) { face.push_back(poly[index]);retained.insert(index); }
                        trial.push_back(std::move(face));
                    }
                    if (retained.size()!=n) { continue; }
                    const fanQuality score=fanScore(trial,normal,physical);
                    if (!score.geometry || !score.possible) { continue; }
                    const std::array<scalar,4> key{{score.shape,score.weight,score.ratio,-scalar(trial.size())}};
                    if (best.empty() || key>bestScore) { bestScore=key;best=std::move(trial); }
                }
            }
        }
        if (best.empty()) { ++qualityFanUnsupported;return; }
        if (best!=pieces) { pieces=std::move(best);++qualityFanChanged; }
    };
    // Plan every base in coordinate-canonical order before changing incidence.
    for (label f=0;f<oldFaces;++f)
    {
        if (!m.faceAlive[f]) { continue; }
        const label o=m.owner[f],n=m.neighbour[f];
        const bool used=marked[o] || (n>=0 && marked[n]);
        if (!used || !splitFace[f]) { piecesOf[f].push_back(f);continue; }
        std::vector<label> poly=canonical[f];vector av;point fc;faceAreaCentreOF(m.points,poly,av,fc);
        const vector normal=av/mag(av);bool convex=true;
        for (size_t j=0;j<poly.size();++j)
        {
            const vector edge=m.points[poly[(j+1)%poly.size()]]-m.points[poly[j]];
            const scalar len=mag(edge);
            for (label q:poly) if ((((edge^(m.points[q]-m.points[poly[j]]))&normal)/len)<-1e-12*std::sqrt(mag(av))) { convex=false; }
        }
        std::vector<std::vector<label>> pieces;
        if (convex)
        {
            if (getenv("DCCART_CONES_PREFER_VERTEX_FAN")!=nullptr) for (size_t k=0;k<poly.size();++k)
            {
                std::vector<std::vector<label>> trial;bool positive=true;
                for (size_t j=1;j+1<poly.size();++j)
                {
                    const label a=poly[k],b=poly[(k+j)%poly.size()],c=poly[(k+j+1)%poly.size()];
                    const scalar area=(((m.points[b]-m.points[a])^(m.points[c]-m.points[a]))&normal)*.5;
                    if (!std::isfinite(area) || area<=1e-15*mag(av)
                     || !writablePolygon({a,b,c})) { positive=false;break; }trial.push_back({a,b,c});
                }
                if (positive) { pieces=std::move(trial);break; }
            }
            if (!pieces.empty()) { ++anchorFan; }
            else
            {
                const label q=insertPoint(fc,true);if (q<0) { ++closePoints;continue; }
                for (size_t j=0;j<poly.size();++j) { pieces.push_back({poly[j],poly[(j+1)%poly.size()],q}); }
                ++faceFan;
            }
        }
        else
        {
            // A simple, planar star polygon may have a strictly positive
            // original-vertex fan. Otherwise refuse; never overlap triangles.
            bool simple=true;
            auto onSegment=[&](const point& a,const point& b,const point& p)
            {
                const vector edge=b-a;const scalar length=mag(edge);
                const scalar support=mag((edge^(p-a))&normal);
                const scalar along=((p-a)&edge)/max(magSqr(edge),VSMALL);
                return support<=1e-12*length*std::sqrt(mag(av)) && along>=-1e-12 && along<=1+1e-12;
            };
            for (size_t i=0;i<poly.size();++i) for (size_t j=i+1;j<poly.size();++j)
            {
                const size_t in=(i+1)%poly.size(),jn=(j+1)%poly.size();
                if (i==j || in==j || jn==i) { continue; }
                const point& a=m.points[poly[i]];const point& b=m.points[poly[in]];
                const point& c=m.points[poly[j]];const point& d=m.points[poly[jn]];
                const scalar s1=((b-a)^(c-a))&normal,s2=((b-a)^(d-a))&normal;
                const scalar t1=((d-c)^(a-c))&normal,t2=((d-c)^(b-c))&normal;
                if ((s1*s2<0 && t1*t2<0) || onSegment(a,b,c) || onSegment(a,b,d)
                 || onSegment(c,d,a) || onSegment(c,d,b)) { simple=false; }
            }
            if (simple) for (size_t k=0;k<poly.size();++k)
            {
                std::vector<std::vector<label>> trial;bool positive=true;
                for (size_t j=1;j+1<poly.size();++j)
                {
                    const label a=poly[k],b=poly[(k+j)%poly.size()],c=poly[(k+j+1)%poly.size()];
                    const scalar area=(((m.points[b]-m.points[a])^(m.points[c]-m.points[a]))&normal)*.5;
                    if (!std::isfinite(area) || area<=1e-15*mag(av)
                     || !writablePolygon({a,b,c})) { positive=false;break; }
                    trial.push_back({a,b,c});
                }
                if (positive) { pieces=std::move(trial);break; }
            }
            if (pieces.empty()) { ++partitionFailures;continue; }++anchorFan;
        }
        const label priorQualityFanChanges=qualityFanChanged;
        if (getenv("DCCART_CONES_REFAN_QUALITY"))
        { improveCanonicalFan(poly,normal,n<0 && !coupled[f],pieces); }
        const bool qualityRefanSelected=qualityFanChanged>priorQualityFanChanges;
        scalar totalArea=0;point areaMoment(point::zero);vector totalVector(vector::zero);
        for (const auto& p:pieces)
        {
            vector a;point c;faceAreaCentreOF(m.points,p,a,c);const scalar amount=mag(a);
            if (!std::isfinite(amount) || !std::isfinite(c.x()) || !std::isfinite(c.y()) || !std::isfinite(c.z())
             || !std::isfinite(a&av) || (a&av)<=0 || amount<=VSMALL || !writablePolygon(p)) { ++partitionFailures; }
            totalArea+=amount;areaMoment+=amount*c;totalVector+=a;
        }
        point geometricCentre=fc;bool signedReference=false;
        if (!convex && getenv("DCCART_CONES_SIGNED_MOMENT"))
        {
            // Preflight requires this split base to be planar. The simple
            // boundary and positive fan have already been checked above.
            // OpenFOAM's absolute fan weights can give a nonconvex polygon
            // a biased in-plane centre; conserve the signed outline moment.
            scalar signedArea=0;point signedMoment(point::zero);
            const point& origin=m.points[poly[0]];
            for (size_t j=1;j+1<poly.size();++j)
            {
                const point& b=m.points[poly[j]];const point& c=m.points[poly[j+1]];
                const scalar weight=(((b-origin)^(c-origin))&normal)*.5;
                signedArea+=weight;signedMoment+=weight*(origin+b+c)/3;
            }
            if (!std::isfinite(signedArea) || signedArea<=VSMALL || mag(signedArea-mag(av))/mag(av)>1e-10)
            { ++partitionFailures;continue; }
            geometricCentre=signedMoment/signedArea;
            if (!std::isfinite(geometricCentre.x()) || !std::isfinite(geometricCentre.y()) || !std::isfinite(geometricCentre.z()))
            { ++partitionFailures;continue; }
            signedReference=true;++momentCorrections;
        }
        const scalar vectorError=mag(totalVector-av)/mag(av);
        const scalar areaError=mag(totalArea-mag(av))/mag(av);
        const scalar centreScale=max(std::sqrt(mag(av)),scalar(1e-6));
        const scalar oldOFCentreError=mag(areaMoment/max(totalArea,VSMALL)-fc)/centreScale;
        const scalar centreError=mag(areaMoment/max(totalArea,VSMALL)-geometricCentre)/centreScale;
        const bool conservationFailure=!std::isfinite(vectorError) || !std::isfinite(areaError)
            || !std::isfinite(centreError) || !std::isfinite(totalArea)
            || !std::isfinite(totalVector.x()) || !std::isfinite(totalVector.y()) || !std::isfinite(totalVector.z())
            || !std::isfinite(areaMoment.x()) || !std::isfinite(areaMoment.y()) || !std::isfinite(areaMoment.z())
            || vectorError>1e-10 || areaError>1e-10 || centreError>1e-10;
        if (conservationFailure || signedReference || qualityRefanSelected)
        {
            if (conservationFailure) { ++partitionFailures; }
            if (partitionAudit.is_open())
            {
                scalar signedArea=0;point signedMoment(point::zero);
                const point& origin=m.points[poly[0]];
                for (size_t j=1;j+1<poly.size();++j)
                {
                    const point& b=m.points[poly[j]];const point& c=m.points[poly[j+1]];
                    const scalar weight=((b-origin)^(c-origin))&normal;
                    signedArea+=weight;signedMoment+=weight*(origin+b+c)/3;
                }
                auto outPoint=[&](const point& p)
                { partitionAudit<<"["<<p.x()<<","<<p.y()<<","<<p.z()<<"]"; };
                if (!firstPartitionWitness) { partitionAudit<<","; }firstPartitionWitness=false;
                partitionAudit<<"{\"face\":"<<f<<",\"owner\":"<<o<<",\"neighbour\":"<<n
                  <<",\"patch\":"<<m.patch[f]<<",\"zone\":"<<m.zone[f]
                  <<",\"projectedConvex\":"<<(convex ? "true":"false")
                  <<",\"vectorError\":"<<vectorError<<",\"areaError\":"<<areaError
                  <<",\"centreError\":"<<centreError<<",\"oldOFCentreError\":"<<oldOFCentreError
                  <<",\"signedReference\":"<<(signedReference ? "true":"false")
                  <<",\"qualityRefanSelected\":"<<(qualityRefanSelected ? "true":"false")
                  <<",\"conservationFailure\":"<<(conservationFailure ? "true":"false")
                  <<",\"oldOFcentre\":";outPoint(fc);
                partitionAudit<<",\"geometricReference\":";outPoint(geometricCentre);
                partitionAudit<<",\"pieceCentre\":";outPoint(areaMoment/max(totalArea,VSMALL));
                partitionAudit<<",\"signedCentre\":";outPoint(signedMoment/max(signedArea,VSMALL));
                partitionAudit<<",\"area\":";outPoint(av);
                partitionAudit<<",\"ownerApex\":";outPoint(apex[o]);
                partitionAudit<<",\"neighbourApex\":";outPoint(n>=0 ? apex[n] : point::zero);
                partitionAudit<<",\"points\":[";
                for (size_t j=0;j<poly.size();++j) { if (j) { partitionAudit<<","; }outPoint(m.points[poly[j]]); }
                partitionAudit<<"],\"pieces\":[";
                for (size_t j=0;j<pieces.size();++j)
                {
                    if (j) { partitionAudit<<","; }partitionAudit<<"[";
                    for (size_t k=0;k<pieces[j].size();++k)
                    { if (k) { partitionAudit<<","; }outPoint(m.points[pieces[j][k]]); }
                    partitionAudit<<"]";
                }
                partitionAudit<<"]}";
            }
            if (conservationFailure) { continue; }
        }
        const bool reverse=(av&areas[f])<0;
        for (size_t j=0;j<pieces.size();++j)
        {
            // Keep the shared canonical vertex zero when flipping outward
            // orientation; cyclic addressing reverses the remaining cycle.
            if (reverse) { std::reverse(pieces[j].begin()+1,pieces[j].end()); }
            label id=f;
            if (j)
            {
                id=m.faces.size();m.faces.push_back(pieces[j]);m.owner.push_back(o);m.neighbour.push_back(n);
                m.patch.push_back(m.patch[f]);m.zone.push_back(m.zone[f]);m.faceAlive.push_back(true);
                if (traceCones) { m.coneEvidenceBase.push_back(f); }
                if (samePhysicalRole) { unionBase.push_back(f); }
            }
            else { m.faces[f]=pieces[j]; }
            piecesOf[f].push_back(id);
        }
        ++partitioned;
    }
    if (partitionAudit.is_open()) { partitionAudit<<"]\n";partitionAudit.flush(); }
    if (getenv("DCCART_CONES_REFAN_QUALITY"))
    {
        qualityFanTriggered=returnReduce(qualityFanTriggered,sumOp<label>());
        qualityFanChanged=returnReduce(qualityFanChanged,sumOp<label>());
        qualityFanUnsupported=returnReduce(qualityFanUnsupported,sumOp<label>());
        Info<<"Conforming canonical quality re-fan experiment: triggered "<<qualityFanTriggered
            <<", changed "<<qualityFanChanged<<", unsupported "<<qualityFanUnsupported
            <<"; all outline nodes retained, shared partition geometry-only; full stock checker required"<<endl;
    }
    partitionFailures=returnReduce(partitionFailures,sumOp<label>());closePoints=returnReduce(closePoints,sumOp<label>());
    momentCorrections=returnReduce(momentCorrections,sumOp<label>());
    Info<<"Conforming planar signed-moment references "<<momentCorrections<<" (all original coordinates retained)"<<endl;
    if (partitionFailures || closePoints)
    { FatalErrorInFunction<<"conforming base partition refused: invalid fans "<<partitionFailures<<", near-coincident new points "<<closePoints<<exit(FatalError); }
    std::vector<std::vector<label>> bases(oldCells);
    for (label c=0;c<oldCells;++c) if (marked[c]) for (label f:cf[c])
    { bases[c].insert(bases[c].end(),piecesOf[f].begin(),piecesOf[f].end()); }
    if (getenv("DCCART_CONE_AXIS_KERNEL_CENTRE"))
    {
        // Optional exploration only: evaluate the actual planned cones after
        // shared face partitioning, before changing any owner/neighbour IDs.
        // The final unmodified full stock checker remains the authorization.
        const bool pareto=getenv("DCCART_CONE_AXIS_KERNEL_PARETO")!=nullptr;
        const bool axisCandidates=getenv("DCCART_CONE_KERNEL_AXIS_CANDIDATES")!=nullptr && pareto;
        struct coneProfile
        {
            bool valid=true;
            std::array<std::vector<scalar>,5> metrics;
            scalar volume=0;point moment=point::zero;
        };
        struct childGeometry
        {
            label base;point centre;scalar volume;
            std::vector<vector> areas;std::vector<point> centres;
            std::vector<std::vector<point>> polygons;
        };
        struct profileEdge { size_t child;label a,b; };
        auto finitePoint=[](const point& p)
        { return std::isfinite(p.x()) && std::isfinite(p.y()) && std::isfinite(p.z()); };
        const bool qualityKernelCandidates=getenv("DCCART_CONE_QUALITY_KERNEL_CANDIDATES")!=nullptr && pareto;
        const bool nonOrthogonalKernelCandidates=getenv("DCCART_CONE_NONORTH_KERNEL_CANDIDATES")!=nullptr && qualityKernelCandidates;
        // Proposal construction only. Planar cone moments give affine signed
        // face distances and volumes. Near-planar floating input is sampled
        // numerically; no feasibility certificate or quality authorization is
        // inferred from this fit. Every proposal still passes the original
        // geometry, actual-neighbour profile, Pareto and final stock checker.
        // Fixed facets only: points/rings and owner/neighbour are immutable
        // in this scope. Clear at each source-parent change; read apex[] live.
        label cachedKernelParent=-1;
        std::map<label,std::pair<vector,point>> kernelFixedFacetGeometry;
        auto kernelFaceGeometry=[&](label parent,label f,vector& area,point& centre)
        {
            if (cachedKernelParent!=parent)
            { kernelFixedFacetGeometry.clear();cachedKernelParent=parent; }
            auto found=kernelFixedFacetGeometry.find(f);
            if (found==kernelFixedFacetGeometry.end())
            {
                vector a;point c;faceAreaCentreOF(m.points,m.faces[f],a,c);
                found=kernelFixedFacetGeometry.emplace(f,std::make_pair(a,c)).first;
            }
            area=found->second.first;centre=found->second.second;
        };
        // Ordered topology only. This scope holds points, rings and incidence
        // fixed; candidate and neighbouring apex geometry is always read live.
        // Retain one source-parent recipe and rebuild on every parent change.
        struct kernelParentTopology
        {
            std::vector<std::vector<label>> polygons;
            std::map<std::pair<label,label>,std::vector<profileEdge>> edges;
        };
        label cachedKernelTopologyParent=-1;
        kernelParentTopology kernelFixedParentTopology;
        auto kernelTopologyFor=[&](label parent) -> const kernelParentTopology&
        {
            if (cachedKernelTopologyParent!=parent)
            {
                kernelFixedParentTopology.polygons.clear();
                kernelFixedParentTopology.edges.clear();
                for (label f:bases[parent])
                {
                    std::vector<label> poly=m.faces[f];
                    if (m.owner[f]!=parent) { std::reverse(poly.begin()+1,poly.end()); }
                    for (size_t j=0;j<poly.size();++j)
                    {
                        const label a=poly[j],b=poly[(j+1)%poly.size()];
                        kernelFixedParentTopology.edges[std::minmax(a,b)].push_back
                            (profileEdge{kernelFixedParentTopology.polygons.size(),a,b});
                    }
                    kernelFixedParentTopology.polygons.push_back(std::move(poly));
                }
                cachedKernelTopologyParent=parent;
            }
            return kernelFixedParentTopology;
        };
        auto qualityValues=[&](label parent,const point& proposed,bool includeNonOrthogonal)
        {
            struct qualityChild { point centre;scalar volume; };
            std::vector<qualityChild> children;std::vector<scalar> out;
            const auto& edges=kernelTopologyFor(parent).edges;
            auto interface=[&](const vector& a,const point& fc,const qualityChild& own,const qualityChild& nei,
                               bool sharedApex,const vector& fixedEdge)
            {
                const scalar dOwn=a&(fc-own.centre),dNei=a&(nei.centre-fc);
                out.insert(out.end(),{dOwn,dNei,own.volume,nei.volume,
                    19*dOwn-dNei,19*dNei-dOwn,100*own.volume-nei.volume,100*nei.volume-own.volume});
                if (!includeNonOrthogonal) { return; }
                const vector d=nei.centre-own.centre;
                const scalar theta=70*constant::mathematical::pi/180;
                if (!finitePoint(d) || !finitePoint(a)) { out.push_back(-1);return; }
                if (sharedApex)
                {
                    // Sf is orthogonal to this fixed original edge. The
                    // common AP cancels from the two pyramid centres, so
                    // d is fixed and Sf(AP) is affine. In the edge-normal
                    // plane, the circular N cone is exactly a two-row wedge.
                    const scalar edgeLength=mag(fixedEdge),distance=mag(d);
                    if (!std::isfinite(edgeLength) || !std::isfinite(distance)
                     || edgeLength<=VSMALL || distance<=VSMALL) { out.push_back(-1);return; }
                    const vector e=fixedEdge/edgeLength;
                    vector u=d-e*(e&d);const scalar transverse=mag(u);
                    if (!std::isfinite(transverse) || transverse<=VSMALL) { out.push_back(-1);return; }
                    u/=transverse;const vector v=e^u;
                    const scalar alpha=std::cos(theta)*distance/transverse;
                    if (!std::isfinite(alpha) || alpha<=0 || alpha>=1) { out.push_back(-1);return; }
                    const scalar beta=std::sqrt(1-alpha*alpha)/alpha;
                    const scalar s=a&u,t=a&v;
                    out.insert(out.end(),{s,beta*s-t,beta*s+t});
                }
                else
                {
                    // Here Sf is fixed and d(AP) is affine. A regular
                    // inscribed 16-gon is a sufficient inner approximation
                    // to the circular N cone, never a relaxed acceptance.
                    const scalar amount=mag(a);
                    if (!std::isfinite(amount) || amount<=VSMALL) { out.push_back(-1);return; }
                    const vector n=a/amount;label axis=0;
                    for (label j=1;j<3;++j) if (mag(n[j])<mag(n[axis])) { axis=j; }
                    vector reference(vector::zero);reference[axis]=1;
                    vector u=n^reference;const scalar length=mag(u);
                    if (!std::isfinite(length) || length<=VSMALL) { out.push_back(-1);return; }
                    u/=length;const vector v=n^u;const scalar s=n&d;
                    const scalar coefficient=std::cos(constant::mathematical::pi/16)*std::tan(theta);
                    out.push_back(s);
                    for (label j=0;j<16;++j)
                    {
                        const scalar angle=2*constant::mathematical::pi*j/16;
                        const vector direction=std::cos(angle)*u+std::sin(angle)*v;
                        out.push_back(coefficient*s-(direction&d));
                    }
                }
            };
            for (label f:bases[parent])
            {
                const bool own=m.owner[f]==parent;vector area;point centre;
                kernelFaceGeometry(parent,f,area,centre);if (!own) { area=-area; }
                const qualityChild child{.75*centre+.25*proposed,(area&(centre-proposed))/3};
                const label other=own ? m.neighbour[f] : m.owner[f];
                if (other>=0)
                {
                    const qualityChild nei{marked[other] ? .75*centre+.25*apex[other] : apex[other],
                        marked[other] ? ((-area)&(centre-apex[other]))/3 : parentVolume[other]};
                    interface(area,centre,child,nei,false,vector::zero);
                }
                children.push_back(child);
            }
            for (const auto& row:edges)
            {
                const auto& use=row.second;
                if (use.size()!=2 || use[0].a!=use[1].b || use[0].b!=use[1].a || use[0].child==use[1].child)
                { return std::vector<scalar>{std::numeric_limits<scalar>::quiet_NaN()}; }
                const point& a=m.points[use[0].a];const point& b=m.points[use[0].b];
                interface(.5*((a-b)^(proposed-b)),(a+b+proposed)/3,children[use[0].child],children[use[1].child],true,a-b);
            }
            return out;
        };
        auto qualityProposals=[&](label parent,const point& lo,const point& hi,bool includeNonOrthogonal)
        {
            struct halfplane { vector a;scalar b; };
            std::vector<point> proposals;std::vector<halfplane> planes;
            const vector size=hi-lo;const point mid=.5*(lo+hi);
            const auto centreValues=qualityValues(parent,mid,includeNonOrthogonal);std::array<std::vector<scalar>,3> samples;
            for (label axis=0;axis<3;++axis)
            { point q=mid;q[axis]+=.25*size[axis];samples[axis]=qualityValues(parent,q,includeNonOrthogonal); }
            for (const auto& row:samples) if (row.size()!=centreValues.size()) { return proposals; }
            for (size_t i=0;i<centreValues.size();++i)
            {
                vector a;scalar scale=mag(centreValues[i]);
                if (!std::isfinite(centreValues[i])) { return proposals; }
                for (label axis=0;axis<3;++axis)
                {
                    if (!std::isfinite(samples[axis][i])) { return proposals; }
                    a[axis]=4*(samples[axis][i]-centreValues[i]);scale=max(scale,mag(samples[axis][i]));
                }
                const scalar norm=mag(a),b=centreValues[i]-.5*(a.x()+a.y()+a.z());
                if (!std::isfinite(norm) || !std::isfinite(b)) { return proposals; }
                if (norm<=1e-12*max(scale,VSMALL))
                { if (centreValues[i]<-1e-12*max(scale,VSMALL)) { return proposals; }continue; }
                planes.push_back(halfplane{a/norm,b/norm});
            }
            point l,h;
            for (label axis=0;axis<3;++axis)
            { l[axis]=3.00001e-6/size[axis];h[axis]=1-l[axis];if (!std::isfinite(l[axis]) || l[axis]>=h[axis]) { return proposals; } }
            for (label axis=0;axis<3;++axis)
            {
                vector a(vector::zero);a[axis]=1;planes.push_back(halfplane{a,-l[axis]});
                a[axis]=-1;planes.push_back(halfplane{a,h[axis]});
            }
            const point p000(l.x(),l.y(),l.z()),p100(h.x(),l.y(),l.z()),p110(h.x(),h.y(),l.z()),p010(l.x(),h.y(),l.z());
            const point p001(l.x(),l.y(),h.z()),p101(h.x(),l.y(),h.z()),p111(h.x(),h.y(),h.z()),p011(l.x(),h.y(),h.z());
            std::vector<std::vector<point>> faces{{p000,p010,p110,p100},{p001,p101,p111,p011},
                {p000,p100,p101,p001},{p010,p011,p111,p110},{p000,p001,p011,p010},{p100,p110,p111,p101}};
            auto uniquePoint=[](std::vector<point>& out,const point& q)
            { for (const point& p:out) if (magSqr(p-q)<=1e-24) { return; }out.push_back(q); };
            for (const halfplane& plane:planes)
            {
                std::vector<std::vector<point>> next;std::vector<point> cap;
                for (const auto& face:faces)
                {
                    std::vector<point> cut;
                    for (size_t i=0;i<face.size();++i)
                    {
                        const point& a=face[i];const point& b=face[(i+1)%face.size()];
                        const scalar da=(plane.a&a)+plane.b,db=(plane.a&b)+plane.b;
                        if (!std::isfinite(da) || !std::isfinite(db)) { return proposals; }
                        const bool inA=da>=0,inB=db>=0;
                        if (inA!=inB)
                        {
                            const scalar t=da/(da-db);const point q=a+t*(b-a);
                            if (!finitePoint(q)) { return proposals; }uniquePoint(cut,q);uniquePoint(cap,q);
                        }
                        if (inB) { uniquePoint(cut,b); }
                    }
                    if (cut.size()>=3) { next.push_back(std::move(cut)); }
                }
                if (cap.size()>=3)
                {
                    point centre(point::zero);for (const point& q:cap) { centre+=q; }centre/=cap.size();
                    label axis=0;for (label i=1;i<3;++i) if (mag(plane.a[i])<mag(plane.a[axis])) { axis=i; }
                    vector reference(vector::zero);reference[axis]=1;
                    vector u=plane.a^reference;u/=max(mag(u),VSMALL);const vector v=plane.a^u;
                    std::sort(cap.begin(),cap.end(),[&](const point& a,const point& b)
                    {
                        const vector da=a-centre,db=b-centre;
                        const scalar aa=std::atan2(da&v,da&u),bb=std::atan2(db&v,db&u);
                        if (aa!=bb) { return aa<bb; }
                        return std::make_tuple(a.x(),a.y(),a.z())<std::make_tuple(b.x(),b.y(),b.z());
                    });next.push_back(std::move(cap));
                }
                faces=std::move(next);if (faces.empty()) { return proposals; }
            }
            std::vector<point> vertices;for (const auto& face:faces) for (const point& q:face) { uniquePoint(vertices,q); }
            if (vertices.size()<4) { return proposals; }
            point mean(point::zero),boxLo(GREAT,GREAT,GREAT),boxHi(-GREAT,-GREAT,-GREAT);
            for (const point& q:vertices) { mean+=q;boxLo=min(boxLo,q);boxHi=max(boxHi,q); }mean/=vertices.size();
            point near;
            for (label axis=0;axis<3;++axis) { near[axis]=(apex[parent][axis]-lo[axis])/size[axis]; }
            // Bounded cyclic projection proposes a point near the current AP.
            // Its convergence is not an acceptance test; the full profile is.
            for (label sweep=0;sweep<256;++sweep)
            {
                scalar worst=0;
                for (const halfplane& plane:planes)
                {
                    const scalar distance=(plane.a&near)+plane.b;
                    if (distance<0) { near-=distance*plane.a;worst=max(worst,-distance); }
                }
                if (!finitePoint(near)) { return proposals; }if (worst<1e-12) { break; }
            }
            std::vector<point> normalized{mean,.5*(boxLo+boxHi),near,.75*near+.25*mean,.5*(near+mean),.25*near+.75*mean};
            for (const point& q:normalized)
            {
                point actual;for (label axis=0;axis<3;++axis) { actual[axis]=lo[axis]+q[axis]*size[axis]; }
                if (finitePoint(actual)) { uniquePoint(proposals,actual); }
            }
            return proposals;
        };
        auto profile=[&](label parent,const point& proposed)
        {
            coneProfile out;std::vector<childGeometry> children;
            const auto& topology=kernelTopologyFor(parent);
            const auto& edges=topology.edges;size_t baseIndex=0;
            auto addInterface=[&](const vector& area,const point& centre,const std::vector<point>& polygon,
                                  const point& own,scalar ownV,const point& nei,scalar neiV,bool internal)
            {
                const scalar amount=mag(area);vector d;
                if (internal)
                {
                    const scalar a=mag(area&(centre-own)),b=mag(area&(nei-centre));
                    out.metrics[1].push_back(min(a,b)/max(a+b,VSMALL));
                    out.metrics[2].push_back(min(ownV,neiV)/max(ownV,neiV));d=nei-own;
                    const scalar cosine=(area&d)/max(amount*mag(d),VSMALL);
                    out.metrics[4].push_back(std::acos(max(scalar(-1),min(scalar(1),cosine)))*180/constant::mathematical::pi);
                }
                else { const vector n=area/max(amount,VSMALL);d=n*(n&(centre-own)); }
                const vector cpf=centre-own;
                const vector skew=cpf-((area&cpf)/((area&d)+ROOTVSMALL))*d;
                const vector direction=skew/(mag(skew)+ROOTVSMALL);
                scalar reach=(internal ? .2 : .4)*mag(d)+ROOTVSMALL;
                for (const point& p:polygon) { reach=max(reach,mag(direction&(p-centre))); }
                out.metrics[3].push_back(mag(skew)/reach);
                // Both real adjacent centres must pass the same face-tet fans.
                bool anchorFound=false;
                for (size_t k=0;k<polygon.size();++k)
                {
                    const scalar oq=tetPointRef(polygon[k],polygon[(k+1)%polygon.size()],centre,own).quality();
                    const scalar nq=internal ? tetPointRef(polygon[k],polygon[(k+1)%polygon.size()],centre,nei).quality() : 1;
                    if (!std::isfinite(oq) || !std::isfinite(nq) || oq>=-1e-15 || nq<=1e-15) { out.valid=false; }
                }
                for (size_t anchor=0;anchor<polygon.size() && !anchorFound;++anchor)
                {
                    bool good=true;
                    for (size_t j=1;j+1<polygon.size();++j)
                    {
                        const point& a=polygon[anchor];const point& b=polygon[(anchor+j)%polygon.size()];
                        const point& c=polygon[(anchor+j+1)%polygon.size()];
                        const scalar oq=tetPointRef(a,b,c,own).quality();
                        const scalar nq=internal ? tetPointRef(a,b,c,nei).quality() : 1;
                        if (!std::isfinite(oq) || !std::isfinite(nq) || oq>=-1e-15 || nq<=1e-15) { good=false;break; }
                    }
                    anchorFound=good;
                }
                if (!anchorFound) { out.valid=false; }
            };
            for (label f:bases[parent])
            {
                const bool own=m.owner[f]==parent;vector area;point centre;
                kernelFaceGeometry(parent,f,area,centre);if (!own) { area=-area; }
                childGeometry child;child.base=f;child.centre=.75*centre+.25*proposed;
                child.volume=(area&(centre-proposed))/3;
                if (!finitePoint(child.centre) || !std::isfinite(child.volume) || child.volume<=VSMALL) { out.valid=false; }
                child.areas.push_back(area);child.centres.push_back(centre);
                const auto& poly=topology.polygons[baseIndex++];
                std::vector<point> base;for (label q:poly) { base.push_back(m.points[q]); }
                child.polygons.push_back(base);
                for (size_t j=0;j<poly.size();++j)
                {
                    const label a=poly[j],b=poly[(j+1)%poly.size()];
                    const point& pa=m.points[a];const point& pb=m.points[b];
                    const vector sf=.5*((pa-pb)^(proposed-pb));
                    child.areas.push_back(sf);child.centres.push_back((pa+pb+proposed)/3);
                    child.polygons.push_back({pb,pa,proposed});
                }
                scalar internalArea=0,allArea=0;label nInternal=0;vector closure(vector::zero);
                scalar span=0;for (const point& q:base) { span=max(span,mag(q-proposed)); }
                const label other=own ? m.neighbour[f] : m.owner[f];
                for (size_t j=0;j<child.areas.size();++j)
                {
                    const vector& a=child.areas[j];const scalar ar=mag(a);const point& fc=child.centres[j];
                    if (!finitePoint(a) || !finitePoint(fc) || ar<=1e-12) { out.valid=false; }
                    const scalar pyramid=(a&(fc-child.centre))/3;
                    if (!std::isfinite(pyramid) || pyramid<=1e-15*child.volume) { out.valid=false; }
                    closure+=a;allArea+=ar;
                    if (j || other>=0) { internalArea+=ar;++nInternal; }
                    const vector normal=a/max(ar,VSMALL);
                    for (const auto& face:child.polygons) for (const point& q:face)
                    { if (((q-fc)&normal)>1e-10*max(span,scalar(1e-6))) { out.valid=false; } }
                    for (size_t k=0;k<child.centres.size();++k) if (k!=j)
                    {
                        const vector delta=child.centres[k]-fc;
                        const scalar cosine=(normal&delta)/max(mag(delta),VSMALL);
                        if (!std::isfinite(cosine) || cosine>=-1.0001e-6) { out.valid=false; }
                    }
                }
                if (mag(closure)/max(allArea,VSMALL)>1e-12 || !nInternal || internalArea<=VSMALL) { out.valid=false; }
                const scalar mean=internalArea/max(nInternal,label(1));symmTensor tensor(Zero);
                for (size_t j=0;j<child.areas.size();++j) if (j || other>=0) { tensor+=sqr(child.areas[j]/max(mean,VSMALL)); }
                out.metrics[0].push_back(mag(det(tensor))/8);
                out.volume+=child.volume;out.moment+=child.volume*child.centre;
                point neighbour=child.centre;scalar neighbourV=child.volume;
                if (other>=0)
                {
                    neighbour=marked[other] ? .75*centre+.25*apex[other] : apex[other];
                    neighbourV=marked[other] ? ((-area)&(centre-apex[other]))/3 : parentVolume[other];
                    if (!finitePoint(neighbour) || !std::isfinite(neighbourV) || neighbourV<=VSMALL) { out.valid=false; }
                }
                addInterface(area,centre,base,child.centre,child.volume,neighbour,neighbourV,other>=0);
                children.push_back(std::move(child));
            }
            for (const auto& row:edges)
            {
                const auto& use=row.second;
                if (use.size()!=2 || use[0].a!=use[1].b || use[0].b!=use[1].a || use[0].child==use[1].child)
                { out.valid=false;continue; }
                const point& a=m.points[use[0].a];const point& b=m.points[use[0].b];
                const vector sf=.5*((a-b)^(proposed-b));const point fc=(a+b+proposed)/3;
                const auto& own=children[use[0].child];const auto& nei=children[use[1].child];
                addInterface(sf,fc,{b,a,proposed},own.centre,own.volume,nei.centre,nei.volume,true);
            }
            if (!std::isfinite(out.volume) || out.volume<=VSMALL || !finitePoint(out.moment)) { out.valid=false; }
            for (const auto& metric:out.metrics) for (scalar value:metric) if (!std::isfinite(value)) { out.valid=false; }
            return out;
        };
        auto improvesWithoutRegression=[](const coneProfile& old,const coneProfile& next)
        {
            const std::array<scalar,5> threshold{{.001,.05,.01,4-1e-8,70}};bool improved=false;
            if (!old.valid || !next.valid) { return false; }
            for (size_t k=0;k<threshold.size();++k)
            {
                if (old.metrics[k].size()!=next.metrics[k].size()) { return false; }
                label oldBad=0,newBad=0;scalar oldWorst=k<3 ? GREAT : 0,newWorst=oldWorst;
                for (size_t j=0;j<old.metrics[k].size();++j)
                {
                    const scalar a=old.metrics[k][j],b=next.metrics[k][j];
                    const bool badA=k<3 ? a<threshold[k] : a>threshold[k];
                    const bool badB=k<3 ? b<threshold[k] : b>threshold[k];
                    if (!badA && badB) { return false; }oldBad+=badA;newBad+=badB;
                    if (k<3) { oldWorst=min(oldWorst,a);newWorst=min(newWorst,b); }
                    else { oldWorst=max(oldWorst,a);newWorst=max(newWorst,b); }
                }
                if (newBad>oldBad) { return false; }
                if (oldBad && (k<3 ? newWorst<oldWorst*(1-1e-10) : newWorst>oldWorst*(1+1e-10))) { return false; }
                improved=improved || newBad<oldBad || (oldBad && (k<3 ? newWorst>oldWorst*(1+1e-10) : newWorst<oldWorst*(1-1e-10)));
            }
            return improved;
        };
        std::ofstream kernelAudit;
        if (traceCones && pareto)
        { kernelAudit.open("conforming-kernel-candidates-rank"+std::to_string(myRank)+".jsonl");kernelAudit<<std::setprecision(17); }
        label kernelProposalPass=0;
        auto auditCandidate=[&](label c,const point& candidate,const char* decision,const coneProfile& old,const coneProfile& next)
        {
            if (!kernelAudit.is_open()) { return; }
            auto xyz=[&](const point& p) { kernelAudit<<'['<<p.x()<<','<<p.y()<<','<<p.z()<<']'; };
            auto scalarOut=[&](scalar v) { if (std::isfinite(v)) { kernelAudit<<v; }else { kernelAudit<<"null"; } };
            auto metricOut=[&](const coneProfile& p)
            {
                kernelAudit<<"{\"valid\":"<<(p.valid ? "true" : "false")<<",\"volume\":";scalarOut(p.volume);
                kernelAudit<<",\"moment\":";xyz(p.moment);kernelAudit<<",\"metrics\":[";
                for (size_t k=0;k<p.metrics.size();++k)
                {
                    if (k) { kernelAudit<<','; }kernelAudit<<'[';
                    for (size_t j=0;j<p.metrics[k].size();++j) { if (j) { kernelAudit<<','; }scalarOut(p.metrics[k][j]); }
                    kernelAudit<<']';
                }
                kernelAudit<<"]}";
            };
            kernelAudit<<"{\"rank\":"<<myRank<<",\"parent\":"<<c<<",\"proposalPass\":"<<kernelProposalPass<<",\"decision\":\""<<decision
                <<"\",\"metricOrder\":[\"determinant\",\"weight\",\"volumeRatio\",\"skew\",\"nonOrthDegrees\"]"
                <<",\"original\":";xyz(apex[c]);kernelAudit<<",\"candidate\":";xyz(candidate);
            kernelAudit<<",\"baseOrder\":[";
            for (size_t j=0;j<bases[c].size();++j) { if (j) { kernelAudit<<','; }kernelAudit<<bases[c][j]; }
            kernelAudit<<"],\"old\":";metricOut(old);kernelAudit<<",\"next\":";metricOut(next);kernelAudit<<"}\n";
        };
        // Cheap determinant screen only. Full geometry profiles are never
        // swept over every marked parent. This permits testing unchanged
        // kernel centres only when an actual planned child needs repair.
        auto hasBadPlannedDeterminant=[&](label c)
        {
            for (label f:bases[c])
            {
                vector a;point centre;kernelFaceGeometry(c,f,a,centre);
                const bool own=m.owner[f]==c;const label other=own ? m.neighbour[f] : m.owner[f];
                std::vector<vector> selected;
                if (other>=0) { selected.push_back(a); }
                scalar total=other>=0 ? mag(a) : 0;
                const auto& poly=m.faces[f];
                for (size_t j=0;j<poly.size();++j)
                {
                    const vector sf=.5*((m.points[poly[j]]-m.points[poly[(j+1)%poly.size()]])
                        ^(apex[c]-m.points[poly[(j+1)%poly.size()]]));
                    selected.push_back(sf);total+=mag(sf);
                }
                const scalar mean=total/max(label(selected.size()),label(1));symmTensor tensor(Zero);
                for (const vector& sf:selected) { tensor+=sqr(sf/max(mean,VSMALL)); }
                const scalar d=mag(det(tensor))/8;
                if (!std::isfinite(d) || d<.001) { return true; }
            }
            return false;
        };
        auto profileKey=[](const coneProfile& p)
        {
            const std::array<scalar,5> threshold{{.001,.05,.01,4-1e-8,70}};
            std::array<label,5> bad{{0,0,0,0,0}};
            std::array<scalar,5> worst{{GREAT,GREAT,GREAT,0,0}};label total=0;
            for (size_t k=0;k<5;++k)
            {
                for (scalar v:p.metrics[k])
                {
                    bad[k]+=(k<3 ? v<threshold[k] : v>threshold[k]);
                    worst[k]=k<3 ? min(worst[k],v) : max(worst[k],v);
                }
                total+=bad[k];if (!bad[k]) { worst[k]=threshold[k]; }
            }
            return std::array<scalar,7>{{-scalar(total),-scalar(bad[0]),worst[0],worst[1],worst[2],-worst[3],-worst[4]}};
        };
        label moved=0,nonAxis=0,unsupported=0,nearExisting=0,unchanged=0,coupledSkip=0,geometryVeto=0,qualityVeto=0;
        label screenedBadParents=0,evaluatedCandidates=0,screenedQualityParents=0,qualityProposalCount=0;
        scalar maxMove=0;
        // Complete the previously tested axis/W/R pass first. Optional N
        // proposals are a later monotonic pass whose "old" geometry is the
        // actual already-selected baseline, never the initial source AP.
        for (kernelProposalPass=0;kernelProposalPass<(nonOrthogonalKernelCandidates ? 2 : 1);++kernelProposalPass)
        for (label c=0;c<oldCells;++c)
        {
            if (!marked[c]) { continue; }
            point lo(-GREAT,-GREAT,-GREAT),hi(GREAT,GREAT,GREAT);bool axisOnly=true,hasCoupled=false;
            for (label f:cf[c])
            {
                hasCoupled=hasCoupled || coupled[f];
                const vector a=m.owner[f]==c ? areas[f] : -areas[f];const scalar amount=mag(a);
                label d=0;if (mag(a.y())>mag(a[d])) { d=1; }if (mag(a.z())>mag(a[d])) { d=2; }
                for (label j=0;j<3;++j) if (j!=d && mag(a[j])>1e-12*amount) { axisOnly=false; }
                if (!std::isfinite(amount) || amount<=VSMALL || !std::isfinite(centres[f][d])) { axisOnly=false; }
                const scalar plane=centres[f][d];scalar scale=0;
                for (label q:m.faces[f]) { scale=max(scale,mag(m.points[q]-centres[f])); }
                for (label q:m.faces[f]) if (mag(m.points[q][d]-plane)>1e-10*max(scale,scalar(1e-6))) { axisOnly=false; }
                if (a[d]>0) { hi[d]=min(hi[d],plane); }else { lo[d]=max(lo[d],plane); }
            }
            if (!axisOnly) { ++nonAxis;continue; }
            const point candidate=.5*(lo+hi);bool valid=true;
            for (label d=0;d<3;++d)
            { if (!std::isfinite(lo[d]) || !std::isfinite(hi[d]) || mag(lo[d])>=GREAT/2 || mag(hi[d])>=GREAT/2 || lo[d]>=hi[d]) { valid=false; } }
            for (label f:cf[c])
            {
                const vector a=m.owner[f]==c ? areas[f] : -areas[f];
                const scalar value=(a&(centres[f]-candidate))/max(mag(a),VSMALL);
                if (!std::isfinite(value) || value<=3e-6) { valid=false; }
            }
            if (!valid) { ++unsupported;continue; }
            const bool needsRepair=axisCandidates && !hasCoupled && hasBadPlannedDeterminant(c);
            bool needsQualityRepair=false;
            if (qualityKernelCandidates && !hasCoupled)
            {
                const auto values=qualityValues(c,apex[c],false);
                for (scalar v:values) if (std::isfinite(v) && v<0) { needsQualityRepair=true;break; }
                if (needsQualityRepair) { ++screenedQualityParents; }
            }
            if (needsRepair) { ++screenedBadParents; }
            if (kernelProposalPass && !needsRepair && !needsQualityRepair) { ++unchanged;continue; }
            const scalar displacement=mag(candidate-apex[c]);
            if (displacement<=1e-12*max(mag(hi-lo),scalar(1e-6)) && !needsRepair && !needsQualityRepair) { ++unchanged;continue; }
            if (pareto && hasCoupled) { ++coupledSkip;continue; }
            auto tooNear=[&](const point& trial)
            {
                const gridKey q=gridOf(trial);
                for (label x=-1;x<=1;++x) for (label y=-1;y<=1;++y) for (label z=-1;z<=1;++z)
                {
                    auto bucket=pointGrid.find(gridKey{q.x+x,q.y+y,q.z+z});if (bucket==pointGrid.end()) { continue; }
                    for (label old:bucket->second) if (magSqr(trial-m.points[old])<=9e-12) { return true; }
                }
                return false;
            };
            if (!pareto)
            {
                if (tooNear(candidate)) { ++nearExisting;continue; }
                apex[c]=candidate;++moved;maxMove=max(maxMove,displacement);continue;
            }
            const coneProfile old=profile(c,apex[c]);
            if (!old.valid) { auditCandidate(c,candidate,"geometry-refused",old,old);++geometryVeto;continue; }
            std::vector<point> proposals;
            if (!kernelProposalPass) { proposals.push_back(candidate); }
            if (needsRepair && !kernelProposalPass)
            {
                // Fixed, bounded, geometry-normalized exploration. No mesh
                // IDs or case coordinates; at most thirteen candidates for
                // parents selected by the cheap actual child determinant.
                for (label axis=0;axis<3;++axis) for (scalar fraction:{scalar(.125),scalar(.25),scalar(.75),scalar(.875)})
                {
                    point q=candidate;q[axis]=lo[axis]+fraction*(hi[axis]-lo[axis]);proposals.push_back(q);
                }
            }
            if (qualityKernelCandidates && (needsRepair || needsQualityRepair))
            {
                // Pass zero is the unchanged tested W/R proposal set;
                // pass one adds N candidates to that selected actual mesh.
                const auto derived=qualityProposals(c,lo,hi,kernelProposalPass!=0);qualityProposalCount+=derived.size();
                proposals.insert(proposals.end(),derived.begin(),derived.end());
            }
            point best=apex[c];coneProfile bestProfile=old,last=old;point lastPoint=candidate;
            auto bestKey=profileKey(old);bool accepted=false,anyGeometry=false;
            for (const point& trial:proposals)
            {
                if (mag(trial-apex[c])<=1e-12*max(mag(hi-lo),scalar(1e-6)) || tooNear(trial)) { continue; }
                bool inKernel=true;
                for (label f:cf[c])
                {
                    const vector a=m.owner[f]==c ? areas[f] : -areas[f];
                    const scalar margin=(a&(centres[f]-trial))/max(mag(a),VSMALL);
                    if (!std::isfinite(margin) || margin<=3e-6) { inKernel=false;break; }
                }
                if (!inKernel) { continue; }++evaluatedCandidates;
                const coneProfile next=profile(c,trial);last=next;lastPoint=trial;
                if (!next.valid || mag(next.volume-old.volume)/max(old.volume,VSMALL)>1e-10
                 || mag(next.moment-old.moment)/(max(old.volume,VSMALL)*max(mag(hi-lo),scalar(1e-6)))>1e-10) { continue; }
                anyGeometry=true;if (!improvesWithoutRegression(old,next)) { continue; }
                const auto key=profileKey(next);
                if (!accepted || key>bestKey) { best=trial;bestProfile=next;bestKey=key;accepted=true; }
            }
            if (!accepted)
            {
                if (!anyGeometry) { auditCandidate(c,lastPoint,"geometry-refused",old,last);++geometryVeto; }
                else { auditCandidate(c,lastPoint,"quality-refused",old,last);++qualityVeto; }continue;
            }
            auditCandidate(c,best,"selected-off-experiment",old,bestProfile);
            maxMove=max(maxMove,mag(best-apex[c]));apex[c]=best;++moved;
        }
        moved=returnReduce(moved,sumOp<label>());nonAxis=returnReduce(nonAxis,sumOp<label>());
        unsupported=returnReduce(unsupported,sumOp<label>());nearExisting=returnReduce(nearExisting,sumOp<label>());
        unchanged=returnReduce(unchanged,sumOp<label>());coupledSkip=returnReduce(coupledSkip,sumOp<label>());
        geometryVeto=returnReduce(geometryVeto,sumOp<label>());qualityVeto=returnReduce(qualityVeto,sumOp<label>());
        maxMove=returnReduce(maxMove,maxOp<scalar>());
        screenedBadParents=returnReduce(screenedBadParents,sumOp<label>());
        evaluatedCandidates=returnReduce(evaluatedCandidates,sumOp<label>());
        screenedQualityParents=returnReduce(screenedQualityParents,sumOp<label>());
        qualityProposalCount=returnReduce(qualityProposalCount,sumOp<label>());
        Info<<"Conforming axis-plane kernel centre experiment: moved "<<moved<<", non-axis "<<nonAxis
            <<", unsupported "<<unsupported<<", near old points "<<nearExisting<<", unchanged "<<unchanged
            <<", coupled skipped "<<coupledSkip<<", geometry veto "<<geometryVeto<<", Pareto veto "<<qualityVeto
            <<", screened bad parents "<<screenedBadParents<<", evaluated candidates "<<evaluatedCandidates
            <<", screened quality parents "<<screenedQualityParents<<", approximate quality-kernel proposals "<<qualityProposalCount
            <<", nonorthogonal inner-cone proposal factor "<<nonOrthogonalKernelCandidates
            <<", sequential proposal passes "<<(nonOrthogonalKernelCandidates ? 2 : 1)
            <<", max displacement "<<maxMove<<"; original bases retained; full checkMesh required"<<endl;
    }
    label added=0,decomposed=0,volumeFailures=0,edgeFailures=0;
    for (label c=0;c<oldCells;++c)
    {
        if (!marked[c]) { continue; }
        const label ap=insertPoint(apex[c],false);if (ap<0) { ++closePoints;continue; }
        struct edgeUse { label child,a,b; };
        std::map<std::pair<label,label>,std::vector<edgeUse>> edges;
        const bool orig=m.cellOrigAlive[c];const scalar refV=m.cellRefVol[c];const point refC=m.cellRefCentre[c];
        const label level=m.cellLevel[c],rank=m.cellRank[c];const uint64_t leaf=m.cellLeaf[c],piece=m.cellPiece[c];
        scalar childVolume=0;bool first=true;
        for (label f:bases[c])
        {
            const bool own=m.owner[f]==c;
            if (!own && m.neighbour[f]!=c) { FatalErrorInFunction<<"conforming base lost parent incidence"<<exit(FatalError); }
            vector area;point centre;faceAreaCentreOF(m.points,m.faces[f],area,centre);
            const scalar v=((own ? area : -area)&(centre-apex[c]))/3;
            if (!std::isfinite(v) || v<=VSMALL || !writablePolygon(m.faces[f])) { ++volumeFailures; }childVolume+=v;
            const label child=first ? c : m.nCells++;
            if (!first)
            {
                m.cellAlive.push_back(true);m.cellOrigAlive.push_back(orig);m.cellRefVol.push_back(refV);m.cellRefCentre.push_back(refC);
                m.cellLevel.push_back(level);m.cellRank.push_back(rank);m.cellLeaf.push_back(leaf);m.cellPiece.push_back(piece);++added;
                if (traceCones) { m.coneEvidenceParent.push_back(c); }
                if (literalUnions) { unionParent.push_back(c); }
                m.completeCavityLineage.inherit(child,c,cavityAvailable);
            }
            first=false;
            const auto& poly=m.faces[f];
            for (size_t j=0;j<poly.size();++j)
            {
                label a=poly[j],b=poly[(j+1)%poly.size()];if (!own) { std::swap(a,b); }
                edges[std::minmax(a,b)].push_back(edgeUse{child,a,b});
            }
            if (own) { m.owner[f]=child; }else { m.neighbour[f]=child; }
        }
        if (!std::isfinite(childVolume) || mag(childVolume-parentVolume[c])/parentVolume[c]>1e-10) { ++volumeFailures; }
        for (const auto& row:edges)
        {
            const auto& e=row.second;
            if (e.size()!=2 || e[0].a!=e[1].b || e[0].b!=e[1].a || e[0].child==e[1].child)
            { ++edgeFailures;continue; }
            const std::vector<label> side{e[0].b,e[0].a,ap};
            if (!writablePolygon(side)) { ++edgeFailures;continue; }
            m.faces.push_back(side);m.owner.push_back(e[0].child);m.neighbour.push_back(e[1].child);
            m.patch.push_back(-1);m.zone.push_back(-1);m.faceAlive.push_back(true);
            if (traceCones) { m.coneEvidenceBase.push_back(-1); }
            if (samePhysicalRole) { unionBase.push_back(-1); }
        }
        ++decomposed;
    }
    closePoints=returnReduce(closePoints,sumOp<label>());volumeFailures=returnReduce(volumeFailures,sumOp<label>());
    edgeFailures=returnReduce(edgeFailures,sumOp<label>());
    if (closePoints || volumeFailures || edgeFailures)
    { FatalErrorInFunction<<"conforming cone refused: point/volume/edge failures "<<closePoints<<' '<<volumeFailures<<' '<<edgeFailures<<exit(FatalError); }
    if(completeRepairEnabled&&!literalUnions)
    {FatalErrorInFunction<<"complete-cavity repair requires the existing earliest writer oracle"<<exit(FatalError);}
    if (literalUnions)
    {
        // OBS only: retain the ALREADY constructed oracle by ownership transfer,
        // not a fresh construct/cache evaluation. No observation is a decision.
        autoPtr<polyMesh> replayEarliest;writerBindings replayEarliestBindings;
        std::unique_ptr<dcCartPhysicalPointLifecycle::Transaction> physicalStage;
        std::shared_ptr<dcCartPhysicalPointLifecycle::Frame> physicalFrame;
        labelList physicalPatchMap;
        if(physicalPointEnabled)
        {
            if(!samePhysicalRole||!m.weightUnionState||!cavityAuthority.ready())
            {FatalErrorInFunction<<"physical point repair requires actual source creator, normal W/D state and fresh complete authority"<<exit(FatalError);}
            physicalStage.reset(new dcCartPhysicalPointLifecycle::Transaction());
        }
        // One all-rank, no-write installed oracle before ANY literal union.
        // The immutable raw checkpoint survives all candidates until the final
        // writer certificate. Its baseline is never refreshed after mutation.
        clockTime oracleClock;
        if (m.writerBaseline)
        { FatalErrorInFunction<<"preoperator writer baseline must be captured once"<<exit(FatalError); }
        auto baseline=std::make_shared<writerCoupledBaseline>();
        baseline->cutPayloadBytes=writerCutPayloadBytes(m);
        auto checkpoint=std::make_shared<cutMesh>(m);
        checkpoint->writerBaseline.reset();
        checkpoint->weightUnionState.reset(); // never alias mutable operator state into earliest input
        baseline->checkpoint=checkpoint;
        cutMesh working(*checkpoint);
        baseline->copySeconds=oracleClock.timeIncrement();
        writerBindings oracleBindings;
        label baselineFailures=0;
        {
            autoPtr<polyMesh> oracle=toPolyMesh
            (
                working,runTime,polyMesh::defaultRegion,patchNames,patchTypes,
                faceZoneNames,patchNeighbours,patchGroups,true,&oracleBindings,
                std::function<scalar()>(),false,physicalPointEnabled?&physicalPatchMap:nullptr
            );
            // Demand-driven installed geometry is materialized before the
            // construct clock stops; no quality checker/remote metric is run.
            oracle->cellCentres();oracle->cellVolumes();oracle->faceAreas();oracle->faceCentres();
            baseline->constructSeconds=oracleClock.timeIncrement();
            baselineFailures=snapshotWriterCoupled(*checkpoint,*oracle,oracleBindings,*baseline);

            if(completeContextEnabled)
            {
                // Real normal producer before the actual installed oracle is
                // destroyed. Owned bounded views, never union arithmetic caches.
                m.completeCavityContext=std::make_shared<writerCurrentContext>();
                std::vector<std::vector<label>> currentRawFaces;
                buildCellFaces(*checkpoint,currentRawFaces);
                dcCartCompleteCavityLocalOracle::captureCatalogue
                    (*checkpoint,*oracle,oracleBindings,cavityAuthority,currentRawFaces,*m.completeCavityContext);
                if(dcCartLegacyJournalObservation::enabled())
                {
                    dcCartLegacyJournalObservation::state.proposed=m.completeCavityContext->proposed;
                    dcCartLegacyJournalObservation::state.captured=m.completeCavityContext->captured;
                }
            }

            if(physicalPointEnabled&&baselineFailures==0&&m.completeCavityContext)
            {
                // The same earliest immutable checkpoint is installed once.
                // It is never replaced by the candidate's current geometry.
                m.writerBaseline=baseline;
                auto& catalogue=*m.completeCavityContext;
                std::vector<label> physicalCreator=unionBase;
                const bool sources=dcCartCompleteCavityLocalOracle::retainNormalFacetSources
                    (m,physicalCreator,cavityAuthority,catalogue);
                m.weightUnionState->clock=clockTime(); // original W admission start, before physical work
                label capturedPhysical=0,stagedPhysical=0;
                dcCartPhysicalPointLifecycle::Statistics pointStats;
                if(sources&&physicalStage->begin(cavityAuthority))
                {
                    const auto candidates=dcCartPhysicalPointLifecycle::discover(*oracle,*physicalStage);
                    if(candidates.complete)for(label point:candidates.compactPoints)
                    {
                        if(!physicalStage->available())break;
                        if(!physicalStage->capture(m,*oracle,oracleBindings,cavityAuthority,
                            catalogue,physicalPatchMap,point))continue;
                        ++capturedPhysical;Foam::point midpoint;
                        if(!physicalStage->midpoint(midpoint))continue;
                        const Foam::point old=m.points[physicalStage->representative()];
                        bool qualified=false;
                        for(scalar fraction:{scalar(.25),scalar(.5),scalar(.75),scalar(1)})
                        {
                            if(!physicalStage->available())break;
                            if(physicalStage->local(old+fraction*(midpoint-old),pointStats))
                            {qualified=true;break;}
                        }
                        if(!qualified)continue;
                        if(!physicalStage->stage(runTime,patchNames,patchTypes,faceZoneNames,
                            *m.weightUnionState,oracleBindings,physicalPatchMap,unionBase,unionParent,pointStats))break;
                        ++stagedPhysical;
                        // All dependent wrappers are allocated BEFORE the first
                        // live point write. Publication itself is no-throw swaps.
                        // Transaction privately owns prepared wrappers; no
                        // mutable staged frame is exposed to caller callbacks.
                        if(!physicalStage->commit(m,physicalFrame))break;
                        physicalPointCommitted=1;break;
                    }
                }
                Pout<<"PRIVATE normal physical-point lifecycle: rank "<<myRank
                    <<", captured/staged/committed "<<capturedPhysical<<'/'<<stagedPhysical<<'/'<<label(bool(physicalFrame))
                    <<", D old/new "<<pointStats.oldD<<'/'<<pointStats.newD
                    <<"; original earliest and W/D clock retained; full writer guard required"<<endl;
            }
            if (weightUnions && !physicalFrame)
            {
                // cf still holds the pre-cone source-facet IDs; patch/zone
                // properties of retained source slots are unchanged by partition.
                // This normal ancestry does not infer a fluid/material role.
                auto sourceOrigin=[&](label parent) -> weightUnionOrigin
                {
                    // Normal immutable APP snapshot supplies the complete
                    // pre-cone shape/roles/identity; never TRACE or current
                    // partitioned m.faces standing in for the old outline.
                    return weightAuthority.normalOriginFromSnapshot
                        ? weightAuthority.normalOriginFromSnapshot(parent)
                        : weightUnionOrigin();
                };
                // The real oracle, not cut arithmetic, selects actual W-bad.
                // Every value and native binding is copied before oracle destruction.
                if(!physicalPointEnabled)m.weightUnionState->clock=clockTime();
                dcCartWeightUnionPreview::capture
                    (*m.weightUnionState,*checkpoint,*oracle,oracleBindings,unionParent,sourceOrigin);
            }
            if(dcCartCompleteCavityReplayObservation::enabled()&&m.completeCavityContext
               &&!m.completeCavityContext->views.empty()
               &&dcCartCompleteCavityReplayObservation::cachedGeometry(*oracle)
               &&dcCartCompleteCavityReplayObservation::bindingsBytes(oracleBindings)<=dcCartCompleteCavityReplayObservation::Limits::bytes)
            {
                // Move-only, O(1); no point/geometry/binding copy or callback.
                replayEarliestBindings=std::move(oracleBindings);
                replayEarliest=std::move(oracle);
            }
        }
        // Working data and actual oracle must not remain aliases/references
        // in the stored certificate. Every recorded value is an owned copy.
        working=cutMesh();oracleBindings=writerBindings();
        baseline->snapshotSeconds=oracleClock.timeIncrement();
        if (getenv("DCCART_CONE_EVIDENCE"))
        {
            const fileName output=runTime.path()/("conforming-writer-baseline-rank"+std::to_string(Pstream::myProcNo())+".json");
            if (!writeWriterCertificate(*baseline,output,"earliest-baseline",baselineFailures,0))
            { ++baselineFailures; }
        }
        const label globalBaselineFailures=returnReduce(baselineFailures,sumOp<label>());
        if (globalBaselineFailures)
        {
            const fileName prefix=runTime.path()/("conforming-preoperator-rank"+std::to_string(Pstream::myProcNo())+"-");
            writeWriterCheckpoint(*checkpoint,prefix);
            FatalErrorInFunction<<"preoperator installed writer baseline refused: "
                <<globalBaselineFailures<<"; no mesh authorization"<<exit(FatalError);
        }
        m.writerBaseline=std::move(baseline);
        Info<<"Conforming preoperator installed writer oracle: coupled faces "<<m.writerBaseline->faces.size()
            <<", local copy/construct/snapshot "<<m.writerBaseline->copySeconds<<'/'
            <<m.writerBaseline->constructSeconds<<'/'<<m.writerBaseline->snapshotSeconds
            <<" s; cut payload "<<m.writerBaseline->cutPayloadBytes<<" bytes, DP geometry "
            <<m.writerBaseline->writerGeometryBytes
            <<" bytes; estimate excludes allocator headers/canon caches; full API deadline unchanged"<<endl;
        std::unordered_set<label> writerCertifiedFaces;
        for (const auto& record:m.writerBaseline->faces) { writerCertifiedFaces.insert(record.nativeFace); }
        // Literal mode only deletes one unzoned internal face. A separate
        // opt-in may synchronously join two physical triangles of the SAME
        // source facet/patch/zone; all their outline nodes and inherited roles
        // remain mandatory. Coupled/zone cavities are unsupported.
        clockTime unionClock;
        std::vector<std::vector<label>> unionCF;
        buildCellFaces(m,unionCF);
        struct unionFace { label owner,neighbour;std::vector<label> polygon;vector area;point centre; };
        std::vector<vector> unionArea(m.faces.size());
        std::vector<point> unionCentre(m.faces.size());
        for (label f=0;f<label(m.faces.size());++f) if (m.faceAlive[f])
        { faceAreaCentreOF(m.points,m.faces[f],unionArea[f],unionCentre[f]); }
        auto coupledType=[&](label f)
        {
            const label p=m.patch[f];if (p<0) { return false; }
            if (p>=patchTypes.size()) { return true; }
            const word& type=patchTypes[p];
            return type.find("cyclic")==0 || type.find("processor")==0
                || type.find("mapped")==0 || type.find("AMI")!=std::string::npos;
        };
        auto finiteUnionPoint=[](const vector& p)
        { return std::isfinite(p.x())&&std::isfinite(p.y())&&std::isfinite(p.z()); };
        struct unionCellGeometry
        {
            point centre=point::zero;scalar volume=0,determinant=0;
            bool finite=false;std::vector<vector> areas;std::vector<point> centres;
        };
        // Unmodified coupled faces on outside cells need no remote guess
        // only when their complete own-side state remains bit-exact. Cavity
        // faces still cannot be coupled. Every rank uses this same rule, so
        // coupled owners on either side retain CC/V and interface geometry.
        struct unionCoupledSnapshot
        {
            label face,owner,neighbour,patch,zone;word type;
            std::vector<label> polygon;std::vector<point> points;
            vector area;point centre;
        };
        auto bitSameScalar=[](scalar a,scalar b)
        { return std::isfinite(a)&&std::isfinite(b)&&std::memcmp(&a,&b,sizeof(scalar))==0; };
        auto bitSamePoint=[&](const vector& a,const vector& b)
        { return bitSameScalar(a.x(),b.x())&&bitSameScalar(a.y(),b.y())&&bitSameScalar(a.z(),b.z()); };
        auto supportedUnchangedCoupled=[&](label f)
        {
            const label p=m.patch[f];
            if (p<0 || p>=patchTypes.size() || m.neighbour[f]>=0) { return false; }
            const word& type=patchTypes[p];
            return type=="processor" || type=="processorCyclic" || type=="cyclic";
        };
        // Cache current CC/V/D only. Candidate geometry and topology remain
        // local; a candidate never rebuilds the whole mesh's cell faces.
        std::vector<point> unionCC(m.nCells,point::zero);
        std::vector<scalar> unionVolume(m.nCells,0),unionDet(m.nCells,0);
        std::vector<unsigned char> unionValid(m.nCells,0);
        auto evaluateUnionCell=[&](label c,const std::vector<label>& faces,
                                  const std::map<label,unionFace>& changed,bool strict)
        {
            unionCellGeometry out;
            if (faces.size()<4) { return out; }
            point estimate(point::zero);scalar totalArea=0,selectedArea=0;label selectedCount=0;
            std::vector<label> vertices;std::map<std::pair<label,label>,std::pair<label,label>> edges;
            for (label f:faces)
            {
                const auto it=changed.find(f);const unionFace* row=it==changed.end() ? nullptr : &it->second;
                const label owner=row ? row->owner : m.owner[f];
                const vector a=(owner==c ? 1 : -1)*(row ? row->area : unionArea[f]);
                const point fc=row ? row->centre : unionCentre[f];
                if (!finiteUnionPoint(a) || !finiteUnionPoint(fc) || mag(a)<=1e-12) { return out; }
                out.areas.push_back(a);out.centres.push_back(fc);estimate+=fc;totalArea+=mag(a);
                if (m.patch[f]<0 || coupledType(f)) { selectedArea+=mag(a);++selectedCount; }
                if (!strict) { continue; }
                const auto& poly=row ? row->polygon : m.faces[f];
                vector writerArea;point writerCentre;polygonNormalCentre(m.points,poly,writerArea,writerCentre);
                if (!finiteUnionPoint(writerArea) || mag(writerArea)<=1e-12) { return out; }
                for (size_t j=0;j<poly.size();++j)
                {
                    label a=poly[j],b=poly[(j+1)%poly.size()];
                    if (owner!=c) { std::swap(a,b); }
                    auto& e=edges[std::minmax(a,b)];++e.first;e.second+=a<b ? 1 : -1;
                    vertices.push_back(a);
                }
            }
            estimate/=faces.size();scalar pyramid3Sum=0;point weighted(point::zero);
            for (size_t j=0;j<faces.size();++j)
            {
                const scalar p=out.areas[j]&(out.centres[j]-estimate);
                pyramid3Sum+=p;weighted+=p*(.75*out.centres[j]+.25*estimate);
            }
            if (!std::isfinite(pyramid3Sum) || pyramid3Sum<=VSMALL || !finiteUnionPoint(weighted)) { return out; }
            out.volume=pyramid3Sum/3;out.centre=weighted/pyramid3Sum;
            if (!finiteUnionPoint(out.centre) || !selectedCount || selectedArea<=VSMALL) { return out; }
            const scalar mean=selectedArea/selectedCount;symmTensor tensor(Zero);
            for (size_t j=0;j<faces.size();++j)
            { if (m.patch[faces[j]]<0 || coupledType(faces[j])) { tensor+=sqr(out.areas[j]/mean); } }
            out.determinant=mag(det(tensor))/8;
            if (!std::isfinite(out.determinant)) { return out; }
            if (strict)
            {
                vector closure(vector::zero);point lo(GREAT,GREAT,GREAT),hi(-GREAT,-GREAT,-GREAT);
                std::sort(vertices.begin(),vertices.end());vertices.erase(std::unique(vertices.begin(),vertices.end()),vertices.end());
                for (label p:vertices)
                { if (!finiteUnionPoint(m.points[p])) { return out; }lo=min(lo,m.points[p]);hi=max(hi,m.points[p]); }
                const scalar span=max(mag(hi-lo),scalar(1e-6));
                for (const auto& e:edges) if (e.second.first!=2 || e.second.second!=0) { return out; }
                for (size_t j=0;j<faces.size();++j)
                {
                    const label f=faces[j];const auto it=changed.find(f);
                    const auto& poly=it==changed.end() ? m.faces[f] : it->second.polygon;
                    const vector a=out.areas[j],normal=a/mag(a);const point fc=out.centres[j];closure+=a;
                    const scalar pv=(a&(fc-out.centre))/3;
                    if (!std::isfinite(pv) || pv<=1e-15*out.volume) { return out; }
                    for (label p:poly)
                    { if (mag((m.points[p]-fc)&normal)>1e-10*span) { return out; } }
                    for (label p:vertices)
                    { if (((m.points[p]-fc)&normal)>1e-10*span) { return out; } }
                    // Official checkConcaveCells also refuses coplanar tiles.
                    for (size_t k=0;k<faces.size();++k) if (j!=k)
                    {
                        const vector d=out.centres[k]-fc;const scalar cosine=(normal&d)/max(mag(d),VSMALL);
                        if (!std::isfinite(cosine) || cosine>-1e-6) { return out; }
                    }
                }
                if (mag(closure)/max(totalArea,VSMALL)>1e-12) { return out; }
            }
            out.finite=true;return out;
        };
        const std::map<label,unionFace> noChanges;
        auto currentUnionCell=[&](label c)
        {
            if (!unionValid[c])
            {
                const auto g=evaluateUnionCell(c,unionCF[c],noChanges,false);
                unionCC[c]=g.centre;unionVolume[c]=g.volume;unionDet[c]=g.determinant;unionValid[c]=g.finite ? 1 : 2;
            }
            return unionValid[c]==1;
        };
        using unionGeometryKey=std::vector<std::tuple<scalar,scalar,scalar>>;
        auto geometryKey=[&](label c)
        {
            unionGeometryKey key;std::set<label> ids;
            for (label f:unionCF[c]) if (m.faceAlive[f]) for (label p:m.faces[f]) { ids.insert(p); }
            for (label p:ids) { key.emplace_back(m.points[p].x(),m.points[p].y(),m.points[p].z()); }
            std::sort(key.begin(),key.end());return key;
        };
        auto trueUnionTet=[&](label c)
        {
            if (unionCF[c].size()!=4) { return false; }
            std::set<label> points;
            for (label f:unionCF[c])
            {
                if (!m.faceAlive[f] || m.faces[f].size()!=3) { return false; }
                for (label p:m.faces[f]) { points.insert(p); }
            }
            return points.size()==4;
        };
        // Cheap selected-Sf determinant census first. Most healthy tetrahedra
        // never allocate cell geometry or populate the CC/V cache.
        auto cheapUnionDet=[&](label c)
        {
            scalar total=0;label count=0;
            for (label f:unionCF[c]) if (m.patch[f]<0 || coupledType(f)) { total+=mag(unionArea[f]);++count; }
            if (!count || !std::isfinite(total) || total<=VSMALL) { return scalar(0); }
            const scalar mean=total/count;symmTensor tensor(Zero);
            for (label f:unionCF[c]) if (m.patch[f]<0 || coupledType(f)) { tensor+=sqr(unionArea[f]/mean); }
            return mag(det(tensor))/8;
        };
        const scalar cacheSeconds=unionClock.elapsedTime();
        std::vector<std::pair<unionGeometryKey,label>> seeds;
        for (label c=0;c<m.nCells;++c) if (m.cellAlive[c] && unionParent[c]>=0 && trueUnionTet(c))
        {
            const scalar d=cheapUnionDet(c);
            if (!std::isfinite(d) || d>=.001 || !currentUnionCell(c)) { continue; }
            seeds.emplace_back(geometryKey(c),c);
        }
        std::sort(seeds.begin(),seeds.end());
        const scalar censusSeconds=unionClock.elapsedTime()-cacheSeconds;
        std::set<std::pair<label,label>> tested;
        label examined=0,accepted=0,protectedCavity=0,geometryRejected=0,qualityRejected=0,capReached=0,rounds=0;
        label roleTried=0,roleAccepted=0;std::array<label,6> roleRefused{{0,0,0,0,0,0}};
        label unchangedCoupledAccepted=0,unchangedCoupledRefused=0;
        std::ofstream unionEvidence;
        if (traceCones) { unionEvidence.open("conforming-literal-unions-rank"+std::to_string(myRank)+".jsonl");unionEvidence<<std::setprecision(17); }
        struct unionMetrics { std::array<scalar,5> v{{GREAT,GREAT,GREAT,0,0}};bool valid=false; };
        auto faceUnionMetrics=[&](label f,const unionFace* row,const point& own,scalar ov,const point& nei,scalar nv,bool internal)
        {
            unionMetrics q;const vector sf=row ? row->area : unionArea[f];const point fc=row ? row->centre : unionCentre[f];
            const auto& polygon=row ? row->polygon : m.faces[f];const scalar ar=mag(sf);
            if (!finiteUnionPoint(sf) || !finiteUnionPoint(fc) || ar<=1e-12 || !finiteUnionPoint(own)
             || !std::isfinite(ov) || ov<=VSMALL) { return q; }
            vector d;
            if (internal)
            {
                if (!finiteUnionPoint(nei) || !std::isfinite(nv) || nv<=VSMALL) { return q; }
                const scalar a=mag(sf&(fc-own)),b=mag(sf&(nei-fc));
                q.v[1]=min(a,b)/(a+b+VSMALL);q.v[2]=min(ov,nv)/(max(ov,nv)+VSMALL);d=nei-own;
                const scalar cosine=(sf&d)/(ar*mag(d)+ROOTVSMALL);
                q.v[4]=std::acos(max(scalar(-1),min(scalar(1),cosine)))*180/constant::mathematical::pi;
            }
            else { const vector n=sf/ar;d=n*(n&(fc-own));q.v[1]=q.v[2]=1; }
            const vector cpf=fc-own,sv=cpf-((sf&cpf)/((sf&d)+ROOTVSMALL))*d;
            const vector direction=sv/(mag(sv)+ROOTVSMALL);scalar reach=(internal ? .2 : .4)*mag(d)+ROOTVSMALL;
            for (label p:polygon) { reach=max(reach,mag(direction&(m.points[p]-fc))); }
            q.v[3]=mag(sv)/reach;
            bool anchor=false;
            for (size_t j=0;j<polygon.size();++j)
            {
                const point& a=m.points[polygon[j]];const point& b=m.points[polygon[(j+1)%polygon.size()]];
                const scalar oq=tetPointRef(a,b,fc,own).quality(),nq=internal ? tetPointRef(a,b,fc,nei).quality() : 1;
                if (!std::isfinite(oq) || !std::isfinite(nq) || oq>=-1e-15 || nq<=1e-15) { return q; }
            }
            for (size_t k=0;k<polygon.size() && !anchor;++k)
            {
                bool good=true;
                for (size_t j=1;j+1<polygon.size();++j)
                {
                    const point& a=m.points[polygon[k]];const point& b=m.points[polygon[(k+j)%polygon.size()]];
                    const point& c=m.points[polygon[(k+j+1)%polygon.size()]];
                    const scalar oq=tetPointRef(a,b,c,own).quality(),nq=internal ? tetPointRef(a,b,c,nei).quality() : 1;
                    if (!std::isfinite(oq) || !std::isfinite(nq) || oq>=-1e-15 || nq<=1e-15) { good=false;break; }
                }
                anchor=good;
            }
            if (!anchor) { return q; }
            for (scalar value:q.v) if (!std::isfinite(value)) { return q; }
            q.valid=true;return q;
        };
        const std::array<scalar,5> unionThreshold{{.001,.05,.01,4-1e-8,70}};
        auto passes=[&](scalar value,size_t metric)
        { return metric<3 ? value>=unionThreshold[metric] : value<=unionThreshold[metric]; };
        // Constant-size fallback: a true cone tetra has at most one physical
        // source base, hence a pair supplies at most one possible triangle pair.
        // Return codes: no pair, wrong role, outline/plane, area/moment.
        auto physicalQuad=[&](label keep,std::vector<label>& outer,
            std::map<label,unionFace>& changed,std::vector<label>& joined,
            scalar& areaError,scalar& momentError)->label
        {
            std::vector<label> physical;
            for (label f:outer) if (m.patch[f]>=0 && m.neighbour[f]<0 && !coupledType(f)) { physical.push_back(f); }
            if (physical.size()!=2) { return 1; }
            const label fa=physical[0],fb=physical[1];
            if (m.patch[fa]!=m.patch[fb] || m.zone[fa]!=m.zone[fb]
             || fa>=label(unionBase.size()) || fb>=label(unionBase.size())
             || unionBase[fa]<0 || unionBase[fa]!=unionBase[fb]) { return 2; }
            const auto& a=changed.at(fa);const auto& b=changed.at(fb);
            if (a.owner!=keep || b.owner!=keep || a.neighbour>=0 || b.neighbour>=0
             || a.polygon.size()!=3 || b.polygon.size()!=3) { return 2; }
            std::set<label> originalPoints;
            std::map<std::pair<label,label>,std::vector<std::pair<label,label>>> incidence;
            for (const auto* face:{&a,&b}) for (size_t j=0;j<3;++j)
            {
                const label x=face->polygon[j],y=face->polygon[(j+1)%3];originalPoints.insert(x);
                incidence[std::minmax(x,y)].emplace_back(x,y);
            }
            if (originalPoints.size()!=4) { return 3; }
            // First physical extension is exactly axis-supported in binary
            // coordinates. Warped/rotated outlines remain unsupported here;
            // numerical area/moment tolerances never establish coplanarity.
            bool exactAxisPlane=false;
            for (label axis=0;axis<3;++axis)
            {
                const scalar plane=m.points[*originalPoints.begin()][axis];bool same=std::isfinite(plane);
                for (label point:originalPoints) { same=same && m.points[point][axis]==plane; }
                exactAxisPlane=exactAxisPlane || same;
            }
            if (!exactAxisPlane) { return 3; }
            std::map<label,label> successor;std::map<label,label> incoming;label sharedEdges=0;
            for (const auto& row:incidence)
            {
                const auto& uses=row.second;
                if (uses.size()==2)
                {
                    if (uses[0].first!=uses[1].second || uses[0].second!=uses[1].first) { return 3; }
                    ++sharedEdges;
                }
                else if (uses.size()==1)
                {
                    if (!successor.emplace(uses[0]).second || ++incoming[uses[0].second]!=1) { return 3; }
                }
                else { return 3; }
            }
            if (sharedEdges!=1 || successor.size()!=4) { return 3; }
            std::vector<label> poly;label p=a.polygon[0];
            for (label j=0;j<4;++j)
            {
                if (!successor.count(p) || std::find(poly.begin(),poly.end(),p)!=poly.end()) { return 3; }
                poly.push_back(p);p=successor.at(p);
            }
            if (p!=poly[0] || std::set<label>(poly.begin(),poly.end())!=originalPoints) { return 3; }
            unionFace quad=a;quad.polygon=poly;faceAreaCentreOF(m.points,poly,quad.area,quad.centre);
            const scalar ar=mag(quad.area),oldA=mag(a.area)+mag(b.area);
            if (!finiteUnionPoint(quad.area) || !finiteUnionPoint(quad.centre) || !std::isfinite(oldA)
             || ar<=1e-12 || oldA<=1e-12 || (quad.area&a.area)<=0 || (quad.area&b.area)<=0) { return 3; }
            scalar span=0;for (label x:poly) { span=max(span,mag(m.points[x]-quad.centre)); }
            if (!std::isfinite(span) || span<=VSMALL) { return 3; }
            const vector normal=quad.area/ar;
            for (label x:poly) if (!finiteUnionPoint(m.points[x]) || mag((m.points[x]-quad.centre)&normal)>1e-10*span) { return 3; }
            // Every nonincident vertex is strictly on the inward edge side:
            // no concavity, crossing, touching or dropped collinear vertex.
            for (size_t j=0;j<4;++j) for (size_t k=0;k<4;++k) if (k!=j && k!=(j+1)%4)
            {
                const vector edge=m.points[poly[(j+1)%4]]-m.points[poly[j]];
                const scalar turn=(edge^(m.points[poly[k]]-m.points[poly[j]]))&normal;
                if (!std::isfinite(turn) || turn<=1e-12*span*span) { return 3; }
            }
            areaError=max(mag(quad.area-a.area-b.area)/oldA,mag(ar-oldA)/oldA);
            const vector delta=ar*(quad.centre-a.centre)-mag(b.area)*(b.centre-a.centre);
            momentError=mag(delta)/(oldA*span);
            if (!std::isfinite(areaError) || !std::isfinite(momentError) || areaError>1e-10 || momentError>1e-10) { return 4; }
            changed[fa]=std::move(quad);changed.erase(fb);
            outer.erase(std::find(outer.begin(),outer.end(),fb));joined={fa,fb};return 0;
        };
        auto tryLiteralUnion=[&](label a,label b,label keep,label drop,label round,label& acceptedRound,
            const dcCartWeightUnionPreview::Packet* weightPacket) -> bool
        {
            label& cavityProtected=weightPacket ? m.weightUnionState->protectedRefused:protectedCavity;
            label& cavityGeometry=weightPacket ? m.weightUnionState->geometryRefused:geometryRejected;
            label& cavityQuality=weightPacket ? m.weightUnionState->qualityRefused:qualityRejected;
                    std::set<label> af(unionCF[a].begin(),unionCF[a].end()),bf(unionCF[b].begin(),unionCF[b].end());
                    std::vector<label> shared,outer;
                    std::set_intersection(af.begin(),af.end(),bf.begin(),bf.end(),std::back_inserter(shared));
                    std::set_symmetric_difference(af.begin(),af.end(),bf.begin(),bf.end(),std::back_inserter(outer));
                    if (shared.size()!=1 || outer.size()>6 || outer.size()<4 || m.patch[shared[0]]>=0 || m.zone[shared[0]]>=0)
                    { ++cavityProtected;return false; }
                    bool protect=false;std::set<label> affected{keep,drop};
                    for (label f:outer)
                    {
                        protect=protect || coupledType(f) || m.zone[f]>=0;
                        if (m.neighbour[f]>=0) { affected.insert(m.owner[f]);affected.insert(m.neighbour[f]); }
                    }
                    if(physicalFrame&&physicalFrame->physicalJournal
                      &&!physicalFrame->physicalJournal->disjoint
                          (std::vector<label>(affected.begin(),affected.end()),outer))
                    {++cavityProtected;return false;}
                    // Snapshot only unchanged outside coupled facets. Unknown
                    // coupling kinds remain unsupported; no interface is changed.
                    std::vector<unionCoupledSnapshot> outsideCoupled;bool outsideCoupledInvalid=false;
                    for (label c:affected) for (label f:unionCF[c]) if (coupledType(f))
                    {
                        if (c==keep || c==drop) { protect=true;break; }
                        if (!m.faceAlive[f] || !supportedUnchangedCoupled(f) || !writerCertifiedFaces.count(f) || m.owner[f]!=c
                         || m.faces[f].size()<3 || !finiteUnionPoint(unionArea[f]) || !finiteUnionPoint(unionCentre[f]))
                        { protect=true;outsideCoupledInvalid=true;break; }
                        unionCoupledSnapshot snapshot{f,m.owner[f],m.neighbour[f],m.patch[f],m.zone[f],
                            patchTypes[m.patch[f]],m.faces[f],{},unionArea[f],unionCentre[f]};
                        for (label p:m.faces[f])
                        {
                            if (p<0 || p>=label(m.points.size()) || !finiteUnionPoint(m.points[p]))
                            { protect=true;outsideCoupledInvalid=true;break; }
                            snapshot.points.push_back(m.points[p]);
                        }
                        if (protect) { break; }
                        outsideCoupled.push_back(std::move(snapshot));
                    }
                    if (protect) { ++cavityProtected;if (outsideCoupledInvalid) { ++unchangedCoupledRefused; }return false; }
                    if (weightPacket && !dcCartWeightUnionPreview::dependency(*weightPacket,m,unionCF))
                    { ++m.weightUnionState->dependencyRefused;return false; }
                    std::map<label,unionFace> changed;
                    for (label f:outer)
                    {
                        unionFace row{m.owner[f],m.neighbour[f],m.faces[f],unionArea[f],unionCentre[f]};
                        if (row.owner==drop) { row.owner=keep; }if (row.neighbour==drop) { row.neighbour=keep; }
                        if (row.neighbour>=0 && row.owner>row.neighbour)
                        { std::swap(row.owner,row.neighbour);std::reverse(row.polygon.begin()+1,row.polygon.end()); }
                        faceAreaCentreOF(m.points,row.polygon,row.area,row.centre);changed.emplace(f,std::move(row));
                    }
                    std::map<label,unionCellGeometry> next,oldGeometry;bool valid=true;
                    for (label c:affected)
                    {
                        const auto old=evaluateUnionCell(c,unionCF[c],noChanges,true);oldGeometry.emplace(c,old);valid=valid&&old.finite;
                        if (c==drop) { continue; }
                        const auto neo=evaluateUnionCell(c,c==keep ? outer : unionCF[c],changed,true);next.emplace(c,neo);valid=valid&&neo.finite;
                    }
                    std::vector<label> joinedPhysical;scalar facetAreaError=0,facetMomentError=0;
                    if (!valid && samePhysicalRole && !weightPacket)
                    {
                        ++roleTried;const label reason=physicalQuad(keep,outer,changed,joinedPhysical,facetAreaError,facetMomentError);
                        if (reason) { ++roleRefused[reason-1]; }
                        else
                        {
                            next.clear();valid=true;
                            for (label c:affected)
                            {
                                valid=valid&&oldGeometry.at(c).finite;if (c==drop) { continue; }
                                const auto neo=evaluateUnionCell(c,c==keep ? outer : unionCF[c],changed,true);
                                next.emplace(c,neo);valid=valid&&neo.finite;
                            }
                            if (!valid) { ++roleRefused[4]; }
                        }
                    }
                    if (!valid) { ++cavityGeometry;return false; }
                    std::set<label> certifiedCoupled;
                    bool unchangedCoupled=true;
                    for (const auto& snapshot:outsideCoupled)
                    {
                        const label f=snapshot.face,c=snapshot.owner;
                        const auto old=oldGeometry.find(c),neo=next.find(c);
                        bool same=changed.find(f)==changed.end() && m.faceAlive[f]
                            && old!=oldGeometry.end() && neo!=next.end()
                            && old->second.finite && neo->second.finite
                            && bitSamePoint(old->second.centre,neo->second.centre)
                            && bitSameScalar(old->second.volume,neo->second.volume)
                            && m.owner[f]==snapshot.owner && m.neighbour[f]==snapshot.neighbour
                            && m.patch[f]==snapshot.patch && m.zone[f]==snapshot.zone
                            && supportedUnchangedCoupled(f) && patchTypes[m.patch[f]]==snapshot.type
                            && m.faces[f]==snapshot.polygon && snapshot.polygon.size()>=3
                            && snapshot.points.size()==snapshot.polygon.size()
                            && bitSamePoint(unionArea[f],snapshot.area)
                            && bitSamePoint(unionCentre[f],snapshot.centre);
                        for (size_t j=0;same && j<snapshot.polygon.size();++j)
                        { same=bitSamePoint(m.points[snapshot.polygon[j]],snapshot.points[j]); }
                        if (!same) { unchangedCoupled=false;break; }
                        certifiedCoupled.insert(f);
                    }
                    if (!unchangedCoupled)
                    {
                        ++cavityProtected;++unchangedCoupledRefused;
                        if (!joinedPhysical.empty()) { ++roleRefused[4]; }
                        return false;
                    }
                    const auto& ng=next.at(keep);const auto& ka=oldGeometry.at(keep);const auto& kb=oldGeometry.at(drop);
                    const scalar oldV=ka.volume+kb.volume;
                    const scalar vr=mag(ng.volume-oldV)/max(oldV,VSMALL);
                    scalar unionSpan=1e-6;
                    for (label f:outer) for (label p:m.faces[f]) { unionSpan=max(unionSpan,mag(m.points[p]-ka.centre)); }
                    const vector momentDelta=ng.volume*(ng.centre-ka.centre)-kb.volume*(kb.centre-ka.centre);
                    const scalar mr=mag(momentDelta)/max(oldV*unionSpan,VSMALL);
                    if (!std::isfinite(vr) || !std::isfinite(mr) || vr>1e-10 || mr>1e-10)
                    { ++cavityGeometry;if (!joinedPhysical.empty()) { ++roleRefused[4]; }return false; }
                    // A repaired cell must pass D, inheriting BOTH old roles.
                    bool quality=ng.determinant>=unionThreshold[0];label passingRoleRegressions=0;
                    std::array<scalar,5> oldShell{{min(ka.determinant,kb.determinant),1,1,0,0}};
                    std::array<scalar,5> newShell{{ng.determinant,1,1,0,0}};
                    std::array<label,5> oldShellBad{{label(!passes(ka.determinant,0))+label(!passes(kb.determinant,0)),0,0,0,0}};
                    std::array<label,5> newShellBad{{label(!passes(ng.determinant,0)),0,0,0,0}};
                    if ((passes(ka.determinant,0) || passes(kb.determinant,0)) && !passes(ng.determinant,0)) { ++passingRoleRegressions; }
                    for (const auto& row:next)
                    {
                        const scalar oldD=oldGeometry.at(row.first).determinant,newD=row.second.determinant;
                        if (passes(oldD,0) && !passes(newD,0)) { ++passingRoleRegressions; }
                        quality=quality && (!passes(oldD,0) || passes(newD,0))
                            && (passes(oldD,0) || newD>=oldD*(1-1e-10));
                    }
                    struct inheritedFacetRole { label oldFace,newFace;std::array<scalar,5> before,after; };
                    std::vector<inheritedFacetRole> inheritedFacetRoles;
                    std::set<label> facesToCheck;
                    for (label c:affected) for (label f:unionCF[c]) if (f!=shared[0]) { facesToCheck.insert(f); }
                    for (label f:facesToCheck)
                    {
                        // All own-side coupled data are certified unchanged;
                        // stock W/R/N/S are identical for any unchanged remote
                        // CC/V. Never evaluate these as physical boundary faces.
                        if (coupledType(f))
                        { if (!certifiedCoupled.count(f)) { quality=false;break; }continue; }
                        // The removed physical facet retains its own BEFORE
                        // role and maps to the joined facet's AFTER metric.
                        const label destination=joinedPhysical.size()==2 && f==joinedPhysical[1] ? joinedPhysical[0] : f;
                        const auto it=changed.find(destination);const unionFace* row=it==changed.end() ? nullptr : &it->second;
                        const label owner=row ? row->owner : m.owner[destination],neighbour=row ? row->neighbour : m.neighbour[destination];
                        const label oo=m.owner[f],on=m.neighbour[f];
                        if (!currentUnionCell(oo) || (on>=0 && !currentUnionCell(on))) { quality=false;break; }
                        auto cc=[&](label c) { const auto t=next.find(c);return t==next.end() ? unionCC[c] : t->second.centre; };
                        auto volume=[&](label c) { const auto t=next.find(c);return t==next.end() ? unionVolume[c] : t->second.volume; };
                        if (!currentUnionCell(owner) || (neighbour>=0 && !currentUnionCell(neighbour))) { quality=false;break; }
                        const auto before=faceUnionMetrics(f,nullptr,unionCC[oo],unionVolume[oo],on>=0 ? unionCC[on] : unionCC[oo],on>=0 ? unionVolume[on] : unionVolume[oo],on>=0);
                        const auto after=faceUnionMetrics(destination,row,cc(owner),volume(owner),neighbour>=0 ? cc(neighbour) : cc(owner),neighbour>=0 ? volume(neighbour) : volume(owner),neighbour>=0);
                        if (!before.valid || !after.valid) { quality=false;break; }
                        if (row) { inheritedFacetRoles.push_back({f,destination,before.v,after.v}); }
                        for (size_t k=1;k<5;++k)
                        {
                            if (row)
                            {
                                oldShell[k]=k<3 ? min(oldShell[k],before.v[k]) : max(oldShell[k],before.v[k]);
                                oldShellBad[k]+=!passes(before.v[k],k);
                                if (f==destination)
                                {
                                    newShell[k]=k<3 ? min(newShell[k],after.v[k]) : max(newShell[k],after.v[k]);
                                    newShellBad[k]+=!passes(after.v[k],k);
                                }
                            }
                            if (passes(before.v[k],k) && !passes(after.v[k],k)) { quality=false;++passingRoleRegressions; }
                            if (!passes(before.v[k],k) && (k<3 ? after.v[k]<before.v[k]*(1-1e-10) : after.v[k]>before.v[k]*(1+1e-10))) { quality=false; }
                        }
                    }
                    if (weightPacket)
                    {
                        const dcCartWeightUnionPreview::RawFace* deleted=nullptr;
                        for(const auto& f:weightPacket->faces) if(f.id==shared[0])
                        { if(deleted) {quality=false;break;}deleted=&f; }
                        std::vector<scalar> shellW;
                        for(label f:outer)
                        {
                            const auto it=changed.find(f);const unionFace* row=it==changed.end()?nullptr:&it->second;
                            if(!row) {quality=false;break;}
                            auto cc=[&](label x){const auto t=next.find(x);return t==next.end()?unionCC[x]:t->second.centre;};
                            auto vol=[&](label x){const auto t=next.find(x);return t==next.end()?unionVolume[x]:t->second.volume;};
                            const label o=row->owner,n=row->neighbour;
                            const auto q=faceUnionMetrics(f,row,cc(o),vol(o),n>=0?cc(n):cc(o),n>=0?vol(n):vol(o),n>=0);
                            if(!q.valid) {quality=false;break;}shellW.push_back(q.v[1]);
                        }
                        quality=quality&&deleted&&dcCartWeightUnionPreview::inheritsDeletedPassingWeight(deleted->quality[0],shellW);
                    }
                    if (!quality) { ++cavityQuality;if (!joinedPhysical.empty()) { ++roleRefused[5]; }return false; }
                    // Journal failure never vetoes or changes an already-accepted old union.
                    dcCartCompleteCavityLocalOracle::LegacyCommit legacyCommit;
                    auto* legacyJournal=(!weightPacket&&m.completeCavityContext
                        &&m.completeCavityContext->legacyJournal)
                        ?m.completeCavityContext->legacyJournal.get():nullptr;
                    if(legacyJournal)
                        dcCartCompleteCavityLocalOracle::prepareLegacyCommit
                            (*legacyJournal,m,cavityAuthority,unionCF,patchTypes,keep,drop,accepted,legacyCommit);
                    // No mutation precedes complete geometry/role preflight.
                    if (weightPacket)
                    {
                        if (shared[0]!=weightPacket->nativeFace || !joinedPhysical.empty())
                        { ++cavityQuality;return false; }
                        auto record=*weightPacket;record.keep=keep;record.drop=drop;
                        m.weightUnionState->committed.push_back(std::move(record));
                        m.weightUnionState->survivorOrigins[keep]=weightPacket->origins;
                        // Optional evidence must not pretend a multi-origin survivor is one cone parent.
                        if (traceCones) { m.coneEvidenceParent[keep]=-1; }
                        ++m.weightUnionState->accepted;
                    }
                    // Record every surviving origin; failure only makes this
                    // new permission unavailable, never alters the old union.
                    m.completeCavityLineage.join(keep,drop,cavityAvailable);
                    m.faceAlive[shared[0]]=false;m.cellAlive[drop]=false;unionCF[drop].clear();unionCF[keep]=outer;
                    if (!joinedPhysical.empty())
                    {
                        m.faceAlive[joinedPhysical[1]]=false;++roleAccepted;
                        if (m.weightUnionState)
                        { ++m.weightUnionState->authorizedPhysicalPruned[m.patch[joinedPhysical[1]]]; }
                    }
                    for (auto& row:changed)
                    {
                        const label f=row.first;auto& value=row.second;
                        m.owner[f]=value.owner;m.neighbour[f]=value.neighbour;m.faces[f].swap(value.polygon);
                        unionArea[f]=value.area;unionCentre[f]=value.centre;
                    }
                    for (const auto& row:next)
                    { const label c=row.first;unionCC[c]=row.second.centre;unionVolume[c]=row.second.volume;unionDet[c]=row.second.determinant;unionValid[c]=1; }
                    unionValid[drop]=2;if (!weightPacket) { ++accepted; }++acceptedRound;
                    if(legacyJournal)
                        dcCartCompleteCavityLocalOracle::finishLegacyCommit
                            (*legacyJournal,m,cavityAuthority,unionCF,patchTypes,accepted,legacyCommit);
                    if (!certifiedCoupled.empty()) { ++unchangedCoupledAccepted; }
                    if (unionEvidence.is_open())
                    {
                        unionEvidence<<"{\"round\":"<<round<<",\"keep\":"<<keep<<",\"drop\":"<<drop
                            <<",\"sourceParent\":"<<(weightPacket ? label(-1) : unionParent[keep])<<",\"deletedFace\":"<<shared[0]
                            <<",\"exteriorFaces\":[";
                        for (size_t j=0;j<outer.size();++j) { if (j) { unionEvidence<<','; }unionEvidence<<outer[j]; }
                        unionEvidence<<"],\"oldD\":["<<ka.determinant<<','<<kb.determinant<<"],\"newD\":"<<ng.determinant
                            <<",\"relativeVolumeError\":"<<vr<<",\"relativeFirstMomentError\":"<<mr
                            <<",\"passingRoleRegressions\":"<<passingRoleRegressions
                            <<",\"metricOrder\":[\"determinant\",\"weight\",\"volumeRatio\",\"skew\",\"nonOrthDegrees\"]";
                        auto writeMetric=[&](const char* name,const std::array<scalar,5>& values)
                        { unionEvidence<<",\""<<name<<"\":[";for (size_t j=0;j<5;++j) { if (j) { unionEvidence<<','; }unionEvidence<<values[j]; }unionEvidence<<']'; };
                        auto writeBad=[&](const char* name,const std::array<label,5>& values)
                        { unionEvidence<<",\""<<name<<"\":[";for (size_t j=0;j<5;++j) { if (j) { unionEvidence<<','; }unionEvidence<<values[j]; }unionEvidence<<']'; };
                        writeMetric("oldShellMinimumDWRMaximumSN",oldShell);writeMetric("newShellMinimumDWRMaximumSN",newShell);
                        writeBad("oldShellBadCounts",oldShellBad);writeBad("newShellBadCounts",newShellBad);
                        if (!joinedPhysical.empty())
                        {
                            unionEvidence<<",\"joinedPhysicalOriginalFaces\":["<<joinedPhysical[0]<<','<<joinedPhysical[1]
                                <<"],\"joinedPhysicalSurvivor\":"<<joinedPhysical[0]
                                <<",\"joinedPhysicalSourceBase\":"<<unionBase[joinedPhysical[0]]
                                <<",\"facetRelativeAreaError\":"<<facetAreaError
                                <<",\"facetRelativeFirstMomentError\":"<<facetMomentError
                                <<",\"inheritedOriginalFacetRoles\":[";
                            for (size_t j=0;j<inheritedFacetRoles.size();++j)
                            {
                                if (j) { unionEvidence<<','; }const auto& r=inheritedFacetRoles[j];
                                unionEvidence<<"{\"oldFace\":"<<r.oldFace<<",\"newFace\":"<<r.newFace<<",\"beforeWRSN\":[";
                                for (size_t k=1;k<5;++k) { if (k>1) { unionEvidence<<','; }unionEvidence<<r.before[k]; }
                                unionEvidence<<"],\"afterWRSN\":[";
                                for (size_t k=1;k<5;++k) { if (k>1) { unionEvidence<<','; }unionEvidence<<r.after[k]; }
                                unionEvidence<<"],\"oldPassingWRSN\":[";
                                for (size_t k=1;k<5;++k) { if (k>1) { unionEvidence<<','; }unionEvidence<<(passes(r.before[k],k) ? "true":"false"); }
                                unionEvidence<<"]}";
                            }
                            unionEvidence<<']';
                        }
                        unionEvidence<<",\"unchangedOutsideCoupledFaces\":[";
                        for (size_t j=0;j<outsideCoupled.size();++j)
                        {
                            if (j) { unionEvidence<<','; }const auto& r=outsideCoupled[j];
                            const auto& old=oldGeometry.at(r.owner);
                            unionEvidence<<"{\"face\":"<<r.face<<",\"owner\":"<<r.owner<<",\"neighbour\":"<<r.neighbour
                                <<",\"patch\":"<<r.patch<<",\"zone\":"<<r.zone
                                <<",\"type\":\""<<r.type<<"\",\"point0\":"<<r.polygon[0]<<",\"pointIDs\":[";
                            for (size_t k=0;k<r.polygon.size();++k) { if (k) { unionEvidence<<','; }unionEvidence<<r.polygon[k]; }
                            unionEvidence<<"],\"pointCoordinates\":[";
                            for (size_t k=0;k<r.points.size();++k)
                            {
                                if (k) { unionEvidence<<','; }const auto& p=r.points[k];
                                unionEvidence<<'['<<p.x()<<','<<p.y()<<','<<p.z()<<']';
                            }
                            auto writeVector=[&](const char* name,const vector& p)
                            { unionEvidence<<",\""<<name<<"\":["<<p.x()<<','<<p.y()<<','<<p.z()<<']'; };
                            unionEvidence<<']';writeVector("Sf",r.area);writeVector("FC",r.centre);writeVector("ownCC",old.centre);
                            unionEvidence<<",\"ownV\":"<<old.volume<<",\"ownCCVBytes\":[";
                            const scalar values[4]={old.centre.x(),old.centre.y(),old.centre.z(),old.volume};
                            for (size_t k=0;k<4;++k)
                            {
                                if (k) { unionEvidence<<','; }const unsigned char* bytes=reinterpret_cast<const unsigned char*>(&values[k]);
                                unionEvidence<<'[';for (size_t b=0;b<sizeof(scalar);++b) { if (b) { unionEvidence<<','; }unionEvidence<<unsigned(bytes[b]); }unionEvidence<<']';
                            }
                            unionEvidence<<"],\"ownSideBitExact\":true,\"remoteQualityRecomputed\":false}";
                        }
                        unionEvidence<<"],\"absoluteFivePass\":"<<(std::all_of(newShellBad.begin(),newShellBad.end(),[](label n){return !n;}) ? "true" : "false")<<"}\n";
                    }
                    return true;
        };
        for (label round=0;round<2;++round)
        {
            ++rounds;label acceptedRound=0;tested.clear();
            for (const auto& seed:seeds)
            {
                const label a=seed.second;if (!m.cellAlive[a] || !currentUnionCell(a) || unionDet[a]>=.001) { continue; }
                std::vector<std::pair<unionGeometryKey,label>> neighbours;
                for (label f:unionCF[a]) if (m.faceAlive[f] && m.patch[f]<0 && m.zone[f]<0 && m.neighbour[f]>=0)
                {
                    const label b=m.owner[f]==a ? m.neighbour[f] : m.owner[f];
                    if (b>=0 && m.cellAlive[b] && m.cellRank[a]==myRank && m.cellRank[b]==myRank
                     && unionParent[a]==unionParent[b] && trueUnionTet(b))
                    { neighbours.emplace_back(geometryKey(b),b); }
                }
                std::sort(neighbours.begin(),neighbours.end());
                for (const auto& candidate:neighbours)
                {
                    const label b=candidate.second;const auto pair=std::minmax(a,b);
                    if (!m.cellAlive[a] || !m.cellAlive[b] || !tested.insert(pair).second) { continue; }
                    if (examined>=1024) { capReached=1;break; }++examined;
                    if (tryLiteralUnion(a,b,pair.first,pair.second,round,acceptedRound,nullptr)) { break; }
                }
                if (capReached) { break; }
            }
            if (!acceptedRound || capReached) { break; }
        }

        if (m.weightUnionState && weightUnions)
        {
            auto& state=*m.weightUnionState;
            label acceptedWeightRound=0;
            // A single separately bounded W pass, after all old D rounds.
            // No extra physical-facet coalescence and no trial MPI.
            for (const auto& packet:state.pool)
            {
                if (!state.available() || state.examined>=256) { break; }
                ++state.examined;
                if (!dcCartWeightUnionPreview::dependency(packet,m,unionCF))
                { ++state.dependencyRefused;continue; }
                if (!m.cellAlive[packet.a] || !m.cellAlive[packet.b]
                 || m.cellRank[packet.a]!=myRank || m.cellRank[packet.b]!=myRank
                 || !trueUnionTet(packet.a) || !trueUnionTet(packet.b)
                 || !state.authority.homogeneousOrigins(packet.origins))
                { ++state.guardRefused;continue; }
                // Stable body-world order, never min(label) or a fixture keeper.
                const bool firstA=packet.keeperKeys[0]<packet.keeperKeys[1];
                bool done=false;
                for (label which=0;which<2 && !done;++which)
                {
                    if (!state.available()) { break; }
                    const bool keepA=which==0 ? firstA:!firstA;
                    const label candidateKeep=keepA?packet.a:packet.b,candidateDrop=keepA?packet.b:packet.a;
                    if (!dcCartWeightUnionPreview::noWriterPairTies(packet,candidateKeep,candidateDrop)
                     || !dcCartWeightUnionPreview::writerShellSupported(packet,candidateKeep,candidateDrop))
                    { ++state.guardRefused;continue; }
                    ++state.tried;
                    done=tryLiteralUnion(packet.a,packet.b,
                        keepA?packet.a:packet.b,keepA?packet.b:packet.a,2,acceptedWeightRound,&packet);
                }
                if (!done) { ++state.guardRefused; }
            }
        }
        if(m.completeCavityContext)
        {
            auto& context=*m.completeCavityContext;
            // Existing accepted counts ONLY non-packet literal mutations;
            // tryLiteralUnion separately increments weightUnionState->accepted.
            // Normal creator handles survive physical partition/coalescence;
            // neither TRACE nor a writer compact label is a source permission.
            if(samePhysicalRole)
                dcCartCompleteCavityLocalOracle::retainNormalFacetSources
                    (m,unionBase,cavityAuthority,context);
            const label unjournalledLegacy=accepted;
            using CurrentRefusal=dcCartCompleteCavityLocalOracle::CurrentRefusal;
            std::array<label,unsigned(CurrentRefusal::count)> currentRefused{};
            dcCartLegacyJournalObservation::PhaseScope p17CensusScope(dcCartLegacyJournalObservation::Phase::census);
            for(const auto& view:context.views)
            {
                CurrentRefusal why=CurrentRefusal::none;
                if(dcCartCompleteCavityLocalOracle::current
                    (view,m,cavityAuthority,unjournalledLegacy,unionCF,&why))++context.currentReady;
                else ++currentRefused[unsigned(why)];
            }
            Pout<<"Normal complete-cavity installed context: rank "<<myRank
                <<", proposed/captured/current "<<context.proposed<<'/'<<context.captured<<'/'<<context.currentReady
                <<", refused "<<context.refused<<", capture "<<context.captureSeconds<<" s"<<endl;
            Pout<<"Normal complete-cavity current admission observations: rank "<<myRank
                <<", local legacy accepted "<<unjournalledLegacy<<"; first-predicate refusals ";
            for(unsigned j=0;j<unsigned(CurrentRefusal::count);++j)
                Pout<<dcCartCompleteCavityLocalOracle::currentRefusalName(CurrentRefusal(j))
                    <<'='<<currentRefused[j]<<' ';
            Pout<<"; observations do not change admission or refresh the earliest oracle"<<endl;
            if(completeRepairEnabled)
            {
                // One current complete star replacement/rank, after all old
                // trials. Non-journalled legacy mutations and packet overlap
                // refuse. Neither old W state nor earliest oracle is refreshed.
                dcCartLegacyJournalObservation::PhaseScope p17RepairScope(dcCartLegacyJournalObservation::Phase::repair);
                const auto cavity=dcCartCompleteCavityRepair::apply
                    (m,context,cavityAuthority,unjournalledLegacy,unionCF);
                Pout<<"Normal complete-cavity repair: rank "<<myRank
                    <<", current/attempted/generated/prepared/committed "<<cavity.current<<'/'<<cavity.attempted<<'/'<<cavity.generated
                    <<'/'<<cavity.prepared<<'/'<<cavity.committed<<", plane bases "<<cavity.planeBases
                    <<", prepared bytes "<<cavity.preparedBytes<<", local "<<cavity.seconds<<" s; refusals ";
                for(const auto count:cavity.refused){Pout<<count<<' ';}
                Pout<<"; actual installed prefix/final and earliest coupled guards still required"<<endl;
            }
        }
        // Default-OFF replay observation runs only after all original decisions.
        // No quality/source/admission callback, oracle refresh or mutation.
        if(dcCartCompleteCavityReplayObservation::enabled()&&m.completeCavityContext)
        {
            const fileName output=runTime.path()/("complete-prefix-observation-rank"+std::to_string(myRank)+".json");
            const auto observed=dcCartCompleteCavityReplayObservation::capturePairedRaw
                (true,output.c_str(),*m.completeCavityContext,m,unionCF,patchTypes,
                 dcCartLegacyJournalObservation::state,dcCartLegacyJournalObservation::dependencyStorage(),
                 replayEarliest.get(),&replayEarliestBindings);
            // Destroy only the OBS lease after its selected cache bytes were copied.
            replayEarliest.clear();replayEarliestBindings=writerBindings();
            Pout<<"Complete prefix replay observation: rank "<<myRank
                <<", complete/written "<<observed.complete<<'/'<<observed.written
                <<", reason "<<observed.refusal<<", entries/cells/faces/aliases/members "
                <<observed.entries<<'/'<<observed.cells<<'/'<<observed.faces<<'/'<<observed.aliases<<'/'<<observed.members
                <<", bytes "<<observed.bytes<<", observation "<<observed.seconds<<" s; no repair permission"<<endl;
        }
        // Observation lease of the already-produced current recipes only
        // after their last normal consumer; no recapture or authority callback.
        dcCartCompleteCavityFullCapture::handoffCF
            (dcCartFullCaptureIO::enabled(),std::move(unionCF),m);
        // One unconditional gather after all local work, including empty
        // ranks. The statistics never affect the subsequent mesh algorithm.
        List<List<scalar>> summaries(Pstream::nProcs());List<scalar> mine(21,0);
        mine[0]=examined;mine[1]=accepted;mine[2]=protectedCavity;mine[3]=geometryRejected;
        mine[4]=qualityRejected;mine[5]=capReached;mine[6]=rounds;mine[7]=unionClock.elapsedTime();
        mine[8]=cacheSeconds;mine[9]=censusSeconds;mine[10]=mine[7]-cacheSeconds-censusSeconds;
        mine[11]=roleTried;mine[12]=roleAccepted;for (label j=0;j<6;++j) { mine[13+j]=roleRefused[j]; }
        mine[19]=unchangedCoupledAccepted;mine[20]=unchangedCoupledRefused;
        summaries[Pstream::myProcNo()]=mine;Pstream::gatherList(summaries);
        if (Pstream::master())
        {
            List<scalar> total(21,0);
            for (const auto& row:summaries)
            {
                for (label j=0;j<6;++j) { total[j]+=row[j]; }
                for (label j=6;j<11;++j) { total[j]=max(total[j],row[j]); }
                for (label j=11;j<21;++j) { total[j]+=row[j]; }
            }
            Info<<"Conforming literal two-cell union experiment: examined "<<total[0]<<", accepted "<<total[1]
                <<", protected/geometry/quality refused "<<total[2]<<'/'<<total[3]<<'/'<<total[4]
                <<", capped ranks "<<total[5]<<", rounds "<<total[6]<<", max wall "<<total[7]
                <<" s [cache,census,local] "<<total[8]<<'/'<<total[9]<<'/'<<total[10]
                <<(physicalPointCommitted ? "; source-backed physical-point prefix with protected current geometry and exterior outlines; full checkMesh required" : "; fixed original points and literal or qualified same-role exterior outlines; full checkMesh required")<<endl;
            Info<<"Conforming unchanged outside coupled guard: accepted candidates "<<total[19]
                <<", guard refused "<<total[20]
                <<"; finite own CC/V bytes and ordered interface data unchanged; no remote quality recomputation; full checkMesh required"<<endl;
            if (samePhysicalRole)
            {
                Info<<"Conforming physical same-source-facet quad experiment: tried "<<total[11]<<", accepted "<<total[12]
                    <<", refused [no-pair,role,outline,conservation,geometry,quality] ";
                for (label j=13;j<19;++j) { if (j>13) { Info<<'/'; }Info<<total[j]; }
                Info<<"; complete original facet passing-role lineage retained; full checkMesh required"<<endl;
            }
        }
    }
    if (m.completeCavityLineage.started())
    {
        const auto& lineage=m.completeCavityLineage;
        Pout<<"Normal complete-cavity lineage: rank "<<myRank<<", active "<<lineage.active()
            <<", native cell keys/composite sets "<<lineage.size()<<'/'<<lineage.compositeCount()
            <<", key/sparse capacity bytes "<<lineage.keyCapacityBytes()<<'/'<<lineage.compositeCapacityBytes()
            <<", refusals [none,missing,budget,cell-cap,index,unknown,origin-cap,set-cap,repeat,binding] ";
        for (const auto count:lineage.refusals()) { Pout<<count<<' '; }
        Pout<<"; source provenance only, installed geometry guards remain independent"<<endl;
    }
    rekeyPiecesByGeometry(m);
    decomposed=returnReduce(decomposed,sumOp<label>());added=returnReduce(added,sumOp<label>());
    partitioned=returnReduce(partitioned,sumOp<label>());faceFan=returnReduce(faceFan,sumOp<label>());anchorFan=returnReduce(anchorFan,sumOp<label>());
    Info<<"Conforming mixed cone experiment: decomposed "<<decomposed<<", added cells "<<added
        <<", partitioned bases "<<partitioned<<", convex centre fans "<<faceFan<<", original-vertex fans "<<anchorFan
        <<(physicalPointCommitted ? "; source-authorized physical point moved; other original coordinates retained; full checkMesh still required" : "; exact original coordinates retained; full checkMesh still required")<<nl;
    return decomposed;
}


Foam::label Foam::dcCartCutCells::auditRegularTransitionRoofs
(
    cutMesh& m, const boolList& coupledPatch
)
{
    // Diagnostic only: proposed displacements are restored before returning.
    // Strict regular 2x2, no geometry smoothing or quality authorization.
    std::vector<std::vector<label>> cf;
    buildCellFaces(m,cf);
    std::vector<unsigned char> protectedPoint(m.points.size(),0);
    for (label f=0; f<label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        unsigned char mask=m.zone[f]>=0 ? 4 : 0;
        const label patch=m.patch[f];
        if (patch>=0)
        {
            mask |= patch<coupledPatch.size() && coupledPatch[patch] ? 2 : 1;
        }
        if (mask) { for (label p:m.faces[f]) { protectedPoint[p] |= mask; } }
    }
    auto bounds = [&](label c, point& lo, point& hi)
    {
        lo=point(GREAT,GREAT,GREAT); hi=point(-GREAT,-GREAT,-GREAT);
        for (label f:cf[c]) for (label p:m.faces[f])
        { lo=min(lo,m.points[p]); hi=max(hi,m.points[p]); }
    };
    auto regularFaces = [&](label c, const point& lo, const point& hi,
                            scalar tol, std::vector<label> groups[6]) -> bool
    {
        for (label f:cf[c])
        {
            label which=-1;
            for (label d=0;d<3;++d) for (label side=0;side<2;++side)
            {
                bool on=true;
                const scalar plane=side ? hi[d] : lo[d];
                for (label p:m.faces[f])
                { if (mag(m.points[p][d]-plane)>tol) { on=false; break; } }
                if (on) { if (which>=0) { return false; } which=2*d+side; }
            }
            if (which<0) { return false; }
            groups[which].push_back(f);
        }
        return true;
    };
    struct packet
    {
        label cell, axis; scalar sign, size, plane;
        std::vector<label> proposalPoints;
        std::vector<vector> proposalDisplacement;
        std::vector<label> points;
        std::vector<vector> displacement;
        std::set<label> star;
        bool overlap=false;
    };
    std::vector<packet> packets;
    const bool rectangular=getenv("DCCART_TRANSITION_ROOF_RECTANGULAR")!=nullptr;
    const bool components=getenv("DCCART_TRANSITION_ROOF_COMPONENTS")!=nullptr;
    label nineFaces=0,bboxRejected=0,axisRejected=0,layoutRejected=0,tileRejected=0;
    label detected=0,physical=0,coupled=0,zone=0;
    for (label c=0;c<m.nCells;++c)
    {
        if (!m.cellAlive[c] || cf[c].size()!=9) { continue; }
        ++nineFaces;
        point lo,hi; bounds(c,lo,hi);
        const vector extent=hi-lo;
        const scalar h=max(extent.x(),max(extent.y(),extent.z()));
        const scalar shortest=min(extent.x(),min(extent.y(),extent.z()));
        const scalar tol=max(scalar(1e-9),1e-8*shortest);
        if (!std::isfinite(lo.x()) || !std::isfinite(lo.y()) || !std::isfinite(lo.z())
         || !std::isfinite(hi.x()) || !std::isfinite(hi.y()) || !std::isfinite(hi.z())
         || !std::isfinite(h) || !std::isfinite(shortest) || shortest<=1e-6
         || (rectangular && h/shortest>1.1)
         || (!rectangular && (h-shortest)>tol))
        { ++bboxRejected;continue; }
        std::vector<label> groups[6];
        if (!regularFaces(c,lo,hi,tol,groups)) { ++axisRejected;continue; }
        label side=-1; bool regular=true;
        for (label j=0;j<6;++j)
        {
            if (groups[j].size()==4) { if (side>=0) { regular=false; } side=j; }
            else if (groups[j].size()!=1) { regular=false; }
        }
        if (!regular || side<0) { ++layoutRejected;continue; }
        const label axis=side/2, u=(axis+1)%3, v=(axis+2)%3;
        const scalar sign=side%2 ? 1 : -1;
        const scalar plane=side%2 ? hi[axis] : lo[axis];
        label grid[9]; std::fill(grid,grid+9,-1);
        std::set<label> fine;
        for (label f:groups[side])
        {
            if (m.faces[f].size()!=4 || m.neighbour[f]<0 || m.patch[f]>=0)
            { regular=false; break; }
            const label n=m.owner[f]==c ? m.neighbour[f] : m.owner[f];
            if (n<0 || !m.cellAlive[n] || cf[n].size()!=6 || !fine.insert(n).second)
            { regular=false; break; }
            point nlo,nhi; bounds(n,nlo,nhi);
            for (label d=0;d<3;++d)
            { if (mag((nhi[d]-nlo[d])-.5*extent[d])>tol) { regular=false; } }
            if (mag((sign>0 ? nlo[axis] : nhi[axis])-plane)>tol)
            { regular=false; }
            std::vector<label> ng[6];
            if (!regularFaces(n,nlo,nhi,tol,ng)) { regular=false; }
            for (label d=0;d<6;++d) { if (ng[d].size()!=1) { regular=false; } }
            label minI=2,maxI=0,minJ=2,maxJ=0;
            std::set<label> tile;
            for (label p:m.faces[f])
            {
                const scalar s=2*(m.points[p][u]-lo[u])/extent[u];
                const scalar t=2*(m.points[p][v]-lo[v])/extent[v];
                if (!std::isfinite(s) || !std::isfinite(t)) { regular=false;break; }
                const label i=label(std::lround(s)),j=label(std::lround(t));
                if (i<0 || i>2 || j<0 || j>2
                 || mag(s-i)>2*tol/extent[u] || mag(t-j)>2*tol/extent[v])
                { regular=false; break; }
                label& node=grid[3*i+j];
                if (node>=0 && node!=p) { regular=false; break; }
                node=p;tile.insert(3*i+j);minI=min(minI,i);maxI=max(maxI,i);
                minJ=min(minJ,j);maxJ=max(maxJ,j);
            }
            if (tile.size()!=4 || maxI-minI!=1 || maxJ-minJ!=1) { regular=false; }
            if (!regular) { break; }
        }
        for (label j=0;j<9;++j) { if (grid[j]<0) { regular=false; } }
        if (!regular) { ++tileRejected;continue; }
        ++detected;
        packet p; p.cell=c;p.axis=axis;p.sign=sign;p.size=h;p.plane=plane;
        unsigned char mask=0;
        for (label i=0;i<3;++i) for (label j=0;j<3;++j)
        {
            const scalar fraction=(i==1 ? .5 : 0)+(j==1 ? .5 : 0);
            const label node=grid[3*i+j];
            vector delta(vector::zero);delta[axis]=sign*(5e-4*extent[axis])*fraction;
            p.proposalPoints.push_back(node);p.proposalDisplacement.push_back(delta);
            if (!fraction) { continue; }
            mask |= protectedPoint[node];
            p.points.push_back(node);p.displacement.push_back(delta);
        }
        if (mask)
        {
            physical+=(mask&1)!=0;coupled+=(mask&2)!=0;zone+=(mask&4)!=0;
            continue;
        }
        packets.push_back(std::move(p));
    }
    // A packet's full point star includes cells outside its five boxes.
    std::unordered_map<label,std::vector<label>> atPoint;
    for (label k=0;k<label(packets.size());++k)
    { for (label p:packets[k].points) { atPoint[p].push_back(k); } }
    for (label f=0;f<label(m.faces.size());++f)
    {
        if (!m.faceAlive[f]) { continue; }
        for (label p:m.faces[f])
        {
            const auto found=atPoint.find(p);if (found==atPoint.end()) { continue; }
            for (label k:found->second)
            {
                packets[k].star.insert(m.owner[f]);
                if (m.neighbour[f]>=0) { packets[k].star.insert(m.neighbour[f]); }
            }
        }
    }
    std::unordered_map<label,label> cellPacket;
    for (label k=0;k<label(packets.size());++k)
    {
        for (label c:packets[k].star)
        {
            auto inserted=cellPacket.emplace(c,k);
            if (!inserted.second)
            { packets[k].overlap=true;packets[inserted.first->second].overlap=true; }
        }
    }
    label overlap=0,planarity=0,facePlane=0,geometricCandidates=0;
    for (packet& p:packets)
    {
        if (p.overlap) { ++overlap;continue; }
        std::vector<point> saved;
        for (size_t i=0;i<p.points.size();++i)
        { saved.push_back(m.points[p.points[i]]);m.points[p.points[i]]+=p.displacement[i]; }
        bool planar=true,strict=true;
        for (label c:p.star)
        {
            std::vector<vector> areas;std::vector<point> centres;
            std::set<label> vertices;scalar span=p.size;
            for (label f:cf[c])
            {
                vector a;point fc;faceAreaCentreOF(m.points,m.faces[f],a,fc);
                if (m.owner[f]!=c) { a=-a; }
                const scalar ar=mag(a);
                if (!std::isfinite(ar) || ar<=VSMALL) { planar=false;strict=false;break; }
                const vector n=a/ar;
                for (label q:m.faces[f])
                {
                    vertices.insert(q);
                    const scalar off=mag((m.points[q]-fc)&n);
                    if (!std::isfinite(off) || off>1e-10*span) { planar=false; }
                }
                areas.push_back(n);centres.push_back(fc);
            }
            if (!planar) { break; }
            for (size_t i=0;i<centres.size();++i)
            {
                for (label q:vertices)
                {
                    const scalar off=(m.points[q]-centres[i])&areas[i];
                    if (!std::isfinite(off) || off>1e-10*span) { strict=false; }
                }
                for (size_t j=0;j<centres.size();++j)
                {
                    if (i==j) { continue; }
                    const vector d=centres[j]-centres[i];
                    const scalar angle=(d&areas[i])/max(mag(d),VSMALL);
                    if (!std::isfinite(angle) || angle>=-1.0001e-6) { strict=false; }
                }
            }
            if (!strict) { break; }
        }
        // Restoration uses saved coordinates, not subtracting rounded deltas.
        for (size_t i=0;i<p.points.size();++i) { m.points[p.points[i]]=saved[i]; }
        if (!planar) { ++planarity; }
        else if (!strict) { ++facePlane; }
        else { ++geometricCandidates; }
    }

    if (components)
    {
        // Union by full affected-cell stars, not merely equal moved points.
        std::vector<label> parent(packets.size());
        for (label i=0;i<label(parent.size());++i) { parent[i]=i; }
        auto root = [&](label i)
        {
            label r=i;while (parent[r]!=r) { r=parent[r]; }
            while (parent[i]!=i) { const label n=parent[i];parent[i]=r;i=n; }
            return r;
        };
        for (label k=0;k<label(packets.size());++k) for (label c:packets[k].star)
        {
            const label a=root(k),b=root(cellPacket.at(c));
            if (a!=b) { parent[max(a,b)]=min(a,b); }
        }
        std::map<label,std::vector<label>> groups;
        for (label k=0;k<label(packets.size());++k) { groups[root(k)].push_back(k); }
        enum reason { conflict,unsupportedSize,coupledStar,planarityFail,polygonFail,
                      closureFail,cellPlaneFail,volumeFail,detFail,tetFail,
                      weightFail,ratioFail,skewFail,conservationFail,nReasons };
        label veto[nReasons]={};label survived=0,singlePlane=0,multiAxis=0;
        label maxPackets=0,maxPoints=0,maxStar=0,conflictPoints=0,fixedConflict=0,axisConflict=0;
        label examples=0;std::set<label> exampleReasons;
        std::ofstream evidence;
        if (getenv("DCCART_TRANSITION_ROOF_EVIDENCE")!=nullptr)
        {
            evidence.open("transition-roof-components-rank"+std::to_string(Pstream::myProcNo())+".jsonl");
            evidence<<std::setprecision(17);
            if (!evidence.good()) { FatalErrorInFunction<<"cannot save requested roof audit evidence"<<exit(FatalError); }
        }
        for (const auto& group:groups)
        {
            struct proposal { vector delta;label axis;scalar sign,plane; };
            std::map<label,proposal> proposals;
            std::set<label> star,badPoints;
            std::set<std::tuple<label,scalar,scalar>> planes;
            std::set<label> axes;
            for (label k:group.second)
            {
                const packet& p=packets[k];star.insert(p.star.begin(),p.star.end());
                planes.emplace(p.axis,p.sign,p.plane);axes.insert(p.axis);
                // Zero corner proposals are constraints too: a neighbour's
                // midpoint must not silently move this packet's fixed corner.
                for (size_t j=0;j<p.proposalPoints.size();++j)
                {
                    const label node=p.proposalPoints[j];
                    const proposal next{p.proposalDisplacement[j],p.axis,p.sign,p.plane};
                    auto inserted=proposals.emplace(node,next);
                    if (inserted.second) { continue; }
                    const proposal& old=inserted.first->second;
                    const bool zeroOld=magSqr(old.delta)==0,zeroNew=magSqr(next.delta)==0;
                    const bool different=magSqr(old.delta-next.delta)>0
                     || (!zeroOld && !zeroNew && (old.axis!=next.axis
                          || old.sign!=next.sign || old.plane!=next.plane));
                    if (different && badPoints.insert(node).second)
                    {
                        ++conflictPoints;fixedConflict+=zeroOld!=zeroNew;
                        axisConflict+=old.axis!=next.axis;
                    }
                }
            }
            singlePlane+=planes.size()==1;multiAxis+=axes.size()>1;
            label nMoved=0;
            for (const auto& item:proposals) { nMoved+=magSqr(item.second.delta)>0; }
            maxPackets=max(maxPackets,label(group.second.size()));
            maxPoints=max(maxPoints,nMoved);maxStar=max(maxStar,label(star.size()));
            label why=-1;
            label witnessCell=-1,witnessFace=-1,witnessOtherFace=-1,witnessPoint=-1;
            if (!badPoints.empty()) { why=conflict;witnessPoint=*badPoints.begin(); }
            // Diagnostic workload cap, never an authorization of skipped cells.
            if (why<0 && (star.size()>100000 || group.second.size()>10000))
            { why=unsupportedSize; }
            if (why<0)
            {
                for (label c:star) for (label f:cf[c])
                {
                    const label patch=m.patch[f];
                    if (patch>=0 && patch<coupledPatch.size() && coupledPatch[patch])
                    { why=coupledStar;witnessCell=c;witnessFace=f;break; }
                }
            }
            std::set<label> qualityCells=star;
            if (why<0)
            {
                for (label c:star) for (label f:cf[c])
                {
                    if (m.neighbour[f]>=0)
                    { qualityCells.insert(m.owner[f]);qualityCells.insert(m.neighbour[f]); }
                }
            }
            struct faceGeometry { vector area;point centre; };
            struct cellGeometry { point centre;scalar volume; };
            std::map<label,faceGeometry> faces;
            std::map<label,cellGeometry> cells;
            auto faceGeo = [&](label f) -> const faceGeometry&
            {
                auto found=faces.find(f);
                if (found!=faces.end()) { return found->second; }
                faceGeometry g;faceAreaCentreOF(m.points,m.faces[f],g.area,g.centre);
                return faces.emplace(f,g).first->second;
            };
            auto cellGeo = [&](label c) -> const cellGeometry&
            {
                auto found=cells.find(c);if (found!=cells.end()) { return found->second; }
                point estimate(point::zero);
                for (label f:cf[c]) { estimate+=faceGeo(f).centre; }
                estimate/=scalar(cf[c].size());
                scalar pyramid3=0;point moment(point::zero);
                for (label f:cf[c])
                {
                    const auto& g=faceGeo(f);const vector a=m.owner[f]==c ? g.area : -g.area;
                    const scalar pv=a&(g.centre-estimate);
                    pyramid3+=pv;moment+=pv*(.75*g.centre+.25*estimate);
                }
                const cellGeometry g{moment/max(pyramid3,VSMALL),pyramid3/3};
                return cells.emplace(c,g).first->second;
            };
            scalar oldVolume=0;
            std::map<label,point> saved;
            if (why<0)
            {
                for (label c:star) { oldVolume+=cellGeo(c).volume; }
                faces.clear();cells.clear();
                for (const auto& item:proposals)
                {
                    if (magSqr(item.second.delta)==0) { continue; }
                    saved.emplace(item.first,m.points[item.first]);
                    m.points[item.first]+=item.second.delta;
                }
                for (label c:qualityCells)
                {
                    const auto& cg=cellGeo(c);
                    if (!std::isfinite(cg.volume) || cg.volume<=VSMALL
                     || !std::isfinite(cg.centre.x()) || !std::isfinite(cg.centre.y())
                     || !std::isfinite(cg.centre.z())) { why=volumeFail;break; }
                    point lo,hi;bounds(c,lo,hi);const scalar span=max(mag(hi-lo),scalar(1e-6));
                    std::set<label> vertices;vector sumArea(vector::zero);scalar areaSum=0;
                    std::map<std::pair<label,label>,std::pair<label,label>> edges;
                    scalar meanInternalArea=0;label nInternal=0;
                    for (label f:cf[c])
                    {
                        const auto& g=faceGeo(f);const scalar ar=mag(g.area);
                        if (!std::isfinite(ar) || ar<=VSMALL) { why=polygonFail;break; }
                        const vector normal=g.area/ar;
                        for (label q:m.faces[f])
                        {
                            vertices.insert(q);
                            const scalar off=mag((m.points[q]-g.centre)&normal);
                            if (!std::isfinite(off) || off>1e-10*span) { why=planarityFail;witnessCell=c;witnessFace=f;witnessPoint=q;break; }
                        }
                        if (why>=0) { break; }
                        const auto& poly=m.faces[f];
                        for (size_t j=0;j<poly.size();++j)
                        {
                            const label a=poly[j],b=poly[(j+1)%poly.size()];
                            const vector edge=m.points[b]-m.points[a];
                            const scalar len=mag(edge);
                            if (!std::isfinite(len) || len<=VSMALL) { why=polygonFail;break; }
                            for (label q:poly)
                            {
                                const scalar support=((edge^(m.points[q]-m.points[a]))&normal)/len;
                                if (!std::isfinite(support) || support < -1e-10*span)
                                { why=polygonFail;break; }
                            }
                            label aa=a,bb=b;if (m.owner[f]!=c) { std::swap(aa,bb); }
                            auto& usage=edges[std::minmax(aa,bb)];
                            ++usage.first;usage.second+=aa<bb ? 1 : -1;
                        }
                        if (why>=0) { break; }
                        const vector outward=m.owner[f]==c ? g.area : -g.area;
                        sumArea+=outward;areaSum+=ar;
                        const scalar pyramid=(outward&(g.centre-cg.centre))/3;
                        if (!std::isfinite(pyramid) || pyramid<=1e-15*cg.volume)
                        { why=volumeFail;break; }
                        const label patch=m.patch[f];
                        if (m.neighbour[f]>=0 || (patch>=0 && patch<coupledPatch.size() && coupledPatch[patch]))
                        { meanInternalArea+=ar;++nInternal; }
                    }
                    if (why>=0) { break; }
                    for (const auto& edge:edges)
                    { if (edge.second.first!=2 || edge.second.second!=0) { why=closureFail;break; } }
                    if (mag(sumArea)/max(areaSum,VSMALL)>1e-12) { why=closureFail; }
                    if (why>=0) { break; }
                    for (label f:cf[c])
                    {
                        const auto& g=faceGeo(f);vector normal=g.area/mag(g.area);
                        if (m.owner[f]!=c) { normal=-normal; }
                        for (label q:vertices)
                        {
                            const scalar off=(m.points[q]-g.centre)&normal;
                            if (!std::isfinite(off) || off>1e-10*span) { why=cellPlaneFail;witnessCell=c;witnessFace=f;witnessPoint=q;break; }
                        }
                        for (label other:cf[c])
                        {
                            if (other==f) { continue; }
                            const vector d=faceGeo(other).centre-g.centre;
                            const scalar cosine=(d&normal)/max(mag(d),VSMALL);
                            if (!std::isfinite(cosine) || cosine>=-1.0001e-6)
                            { why=cellPlaneFail;witnessCell=c;witnessFace=f;witnessOtherFace=other;break; }
                        }
                        if (why>=0) { break; }
                    }
                    if (why>=0) { break; }
                    if (!nInternal || meanInternalArea<=ROOTVSMALL) { why=detFail;break; }
                    meanInternalArea/=nInternal;symmTensor tensor(Zero);
                    for (label f:cf[c])
                    {
                        const label patch=m.patch[f];
                        if (m.neighbour[f]>=0 || (patch>=0 && patch<coupledPatch.size() && coupledPatch[patch]))
                        { tensor+=sqr(faceGeo(f).area/meanInternalArea); }
                    }
                    const scalar determinant=mag(det(tensor))/8;
                    if (!std::isfinite(determinant) || determinant<.001) { why=detFail;break; }
                }
                std::set<label> checkedFaces;
                if (why<0) for (label c:star) for (label f:cf[c]) { checkedFaces.insert(f); }
                if (why<0) for (label f:checkedFaces)
                {
                    const auto& g=faceGeo(f);const auto& poly=m.faces[f];
                    const auto& own=cellGeo(m.owner[f]);const label nei=m.neighbour[f];
                    const point nc=nei>=0 ? cellGeo(nei).centre : own.centre;
                    const scalar tol=1e-15;
                    for (size_t j=0;j<poly.size();++j)
                    {
                        const point& a=m.points[poly[j]];const point& b=m.points[poly[(j+1)%poly.size()]];
                        const scalar oq=tetPointRef(a,b,g.centre,own.centre).quality();
                        const scalar nq=nei>=0 ? tetPointRef(a,b,g.centre,nc).quality() : 1;
                        if (!std::isfinite(oq) || !std::isfinite(nq) || oq>=-tol || nq<=tol)
                        { why=tetFail;break; }
                    }
                    if (why>=0) { break; }
                    bool baseFound=false;
                    for (size_t anchor=0;anchor<poly.size() && !baseFound;++anchor)
                    {
                        bool good=true;const point& a=m.points[poly[anchor]];
                        for (size_t j=1;j+1<poly.size();++j)
                        {
                            const point& b=m.points[poly[(anchor+j)%poly.size()]];
                            const point& d=m.points[poly[(anchor+j+1)%poly.size()]];
                            const scalar oq=tetPointRef(a,b,d,own.centre).quality();
                            const scalar nq=nei>=0 ? tetPointRef(a,b,d,nc).quality() : 1;
                            if (!std::isfinite(oq) || !std::isfinite(nq) || oq>=-tol || nq<=tol)
                            { good=false;break; }
                        }
                        baseFound=good;
                    }
                    if (!baseFound) { why=tetFail;break; }
                    vector d;
                    if (nei>=0)
                    {
                        const auto& neighbour=cellGeo(nei);
                        const scalar a=mag(g.area&(g.centre-own.centre));
                        const scalar b=mag(g.area&(neighbour.centre-g.centre));
                        const scalar weight=min(a,b)/max(a+b,VSMALL);
                        const scalar ratio=min(own.volume,neighbour.volume)/max(own.volume,neighbour.volume);
                        if (!std::isfinite(weight) || weight<.05) { why=weightFail;break; }
                        if (!std::isfinite(ratio) || ratio<.01) { why=ratioFail;break; }
                        d=neighbour.centre-own.centre;
                    }
                    else
                    {
                        const vector n=g.area/mag(g.area);
                        d=n*(n&(g.centre-own.centre));
                    }
                    const vector cpf=g.centre-own.centre;
                    const vector skew=cpf-((g.area&cpf)/((g.area&d)+ROOTVSMALL))*d;
                    const vector sh=skew/(mag(skew)+ROOTVSMALL);
                    scalar reach=(nei>=0 ? .2 : .4)*mag(d)+ROOTVSMALL;
                    for (label q:poly) { reach=max(reach,mag(sh&(m.points[q]-g.centre))); }
                    const scalar value=mag(skew)/reach;
                    if (!std::isfinite(value) || value>4-1e-8) { why=skewFail;break; }
                }
                scalar newVolume=0;
                if (why<0) for (label c:star) { newVolume+=cellGeo(c).volume; }
                if (why<0 && (!std::isfinite(oldVolume) || oldVolume<=VSMALL
                 || mag(newVolume-oldVolume)/oldVolume>1e-10)) { why=conservationFail; }
            }
            // All paths restore exact original values; no writer-visible move.
            for (const auto& item:saved) { m.points[item.first]=item.second; }
            if (why>=0) { ++veto[why]; } else { ++survived; }
            const bool emit=examples<3 && (exampleReasons.insert(why).second || group.second.size()<4);
            if (emit)
            {
                ++examples;
                Pout<< "Transition roof component example: packets "<<group.second.size()
                    <<", moved points "<<nMoved<<", full-star cells "<<star.size()
                    <<", planes "<<planes.size()<<", axes "<<axes.size()
                    <<", exact point conflicts "<<badPoints.size()<<", veto reason "<<why
                    <<" (-1=all local gates pass; no mutation)"<<nl;
            }

            if (emit && evidence.is_open() && evidence.good())
            {
                const size_t np=min(group.second.size(),size_t(8));
                std::set<label> sampleCells,sampleFaces,samplePoints;
                if (witnessCell>=0) { sampleCells.insert(witnessCell); }
                if (witnessPoint>=0) { samplePoints.insert(witnessPoint); }
                for (label c:star) { if (sampleCells.size()==32) { break; } sampleCells.insert(c); }
                for (size_t j=0;j<np;++j)
                {
                    const packet& p=packets[group.second[j]];sampleCells.insert(p.cell);
                    samplePoints.insert(p.proposalPoints.begin(),p.proposalPoints.end());
                }
                for (label c:sampleCells) for (label f:cf[c])
                { sampleFaces.insert(f);samplePoints.insert(m.faces[f].begin(),m.faces[f].end()); }
                auto xyz = [&](const vector& a) { evidence<<'['<<a.x()<<','<<a.y()<<','<<a.z()<<']'; };
                evidence<<"{\"rank\":"<<Pstream::myProcNo()<<",\"vetoReason\":"<<why
                    <<",\"totalPackets\":"<<group.second.size()<<",\"totalStarCells\":"<<star.size()
                    <<",\"totalMovingPoints\":"<<nMoved<<",\"exactConflictPoints\":"<<badPoints.size()
                    <<",\"exportedPackets\":"<<np<<",\"exportedCells\":"<<sampleCells.size()
                    <<",\"truncated\":"<<((np<group.second.size() || sampleCells.size()<star.size()) ? "true" : "false")
                    <<",\"witness\":{\"cell\":"<<witnessCell<<",\"face\":"<<witnessFace
                    <<",\"otherFace\":"<<witnessOtherFace<<",\"point\":"<<witnessPoint<<'}'
                    <<",\"scope\":\"Read-only native proposal witness; original coordinates restored. Limited sample does not prove whole-component quality.\",\"packets\":[";
                for (size_t j=0;j<np;++j)
                {
                    if (j) { evidence<<','; }const packet& p=packets[group.second[j]];
                    point lo,hi;bounds(p.cell,lo,hi);
                    evidence<<"{\"cell\":"<<p.cell<<",\"axis\":"<<p.axis<<",\"sign\":"<<p.sign
                        <<",\"plane\":"<<p.plane<<",\"normalSpan\":"<<hi[p.axis]-lo[p.axis]
                        <<",\"bboxMin\":";xyz(lo);evidence<<",\"bboxMax\":";xyz(hi);
                    evidence<<",\"pointProposals\":[";
                    for (size_t k=0;k<p.proposalPoints.size();++k)
                    {
                        if (k) { evidence<<','; }const label node=p.proposalPoints[k];
                        evidence<<"{\"point\":"<<node<<",\"original\":";xyz(m.points[node]);
                        evidence<<",\"delta\":";xyz(p.proposalDisplacement[k]);evidence<<'}';
                    }
                    evidence<<"]}";
                }
                evidence<<"],\"conflictWitnessProposals\":[";bool conflictFirst=true;
                if (why==conflict) for (label index:group.second)
                {
                    const packet& p=packets[index];
                    for (size_t k=0;k<p.proposalPoints.size();++k)
                    {
                        if (p.proposalPoints[k]!=witnessPoint) { continue; }
                        if (!conflictFirst) { evidence<<','; }conflictFirst=false;
                        evidence<<"{\"cell\":"<<p.cell<<",\"axis\":"<<p.axis<<",\"sign\":"<<p.sign
                            <<",\"plane\":"<<p.plane<<",\"point\":"<<witnessPoint<<",\"delta\":";
                        xyz(p.proposalDisplacement[k]);evidence<<'}';
                    }
                }
                evidence<<"],\"cells\":[";bool first=true;
                for (label c:sampleCells)
                {
                    if (!first) { evidence<<','; }first=false;
                    evidence<<"{\"cell\":"<<c<<",\"belongsToFullStar\":"<<(star.count(c) ? "true" : "false")<<",\"faces\":[";
                    for (size_t j=0;j<cf[c].size();++j) { if (j) { evidence<<','; }evidence<<cf[c][j]; }
                    evidence<<"]}";
                }
                evidence<<"],\"faces\":[";first=true;
                for (label f:sampleFaces)
                {
                    if (!first) { evidence<<','; }first=false;const label patch=m.patch[f];
                    const bool coupled=patch>=0 && patch<coupledPatch.size() && coupledPatch[patch];
                    evidence<<"{\"face\":"<<f<<",\"owner\":"<<m.owner[f]<<",\"neighbour\":"<<m.neighbour[f]
                        <<",\"patch\":"<<patch<<",\"zone\":"<<m.zone[f]<<",\"coupled\":"<<(coupled ? "true" : "false")
                        <<",\"physical\":"<<(patch>=0 && !coupled ? "true" : "false")<<",\"points\":[";
                    for (size_t j=0;j<m.faces[f].size();++j) { if (j) { evidence<<','; }evidence<<m.faces[f][j]; }
                    evidence<<"]}";
                }
                evidence<<"],\"points\":[";first=true;
                for (label q:samplePoints)
                {
                    if (!first) { evidence<<','; }first=false;
                    evidence<<"{\"point\":"<<q<<",\"protectedMask\":"<<label(protectedPoint[q])<<",\"original\":";
                    xyz(m.points[q]);evidence<<",\"finalProposalDelta\":";
                    auto proposed=proposals.find(q);
                    xyz(proposed==proposals.end() ? vector::zero : proposed->second.delta);
                    evidence<<",\"deltaFromCompleteComponent\":true}";
                }
                evidence<<"]}\n";
            }
        }
        label count=returnReduce(label(groups.size()),sumOp<label>());
        survived=returnReduce(survived,sumOp<label>());singlePlane=returnReduce(singlePlane,sumOp<label>());
        multiAxis=returnReduce(multiAxis,sumOp<label>());conflictPoints=returnReduce(conflictPoints,sumOp<label>());
        fixedConflict=returnReduce(fixedConflict,sumOp<label>());axisConflict=returnReduce(axisConflict,sumOp<label>());
        for (label j=0;j<nReasons;++j) { veto[j]=returnReduce(veto[j],sumOp<label>()); }
        reduce(maxPackets,maxOp<label>());reduce(maxPoints,maxOp<label>());reduce(maxStar,maxOp<label>());
        Info<< "Transition roof component audit (no mutation): components "<<count
            <<", single-plane "<<singlePlane<<", multi-axis "<<multiAxis
            <<"; maximum packets/points/star "<<maxPackets<<'/'<<maxPoints<<'/'<<maxStar
            <<"; conflicting points "<<conflictPoints<<", fixed-corner "<<fixedConflict<<", multi-axis "<<axisConflict
            <<"; vetoes [conflict,size,coupledStar,planarity,polygon,closure,cellPlane,volume,det,tet,weight,ratio,skew,conservation] ";
        for (label j=0;j<nReasons;++j) { Info<<veto[j]<<' '; }
        Info<<"; all-local-gate survivors "<<survived<<" (still not full-mesh acceptance)"<<nl;
    }

    nineFaces=returnReduce(nineFaces,sumOp<label>());
    bboxRejected=returnReduce(bboxRejected,sumOp<label>());
    axisRejected=returnReduce(axisRejected,sumOp<label>());
    layoutRejected=returnReduce(layoutRejected,sumOp<label>());
    tileRejected=returnReduce(tileRejected,sumOp<label>());
    detected=returnReduce(detected,sumOp<label>());
    physical=returnReduce(physical,sumOp<label>());coupled=returnReduce(coupled,sumOp<label>());
    zone=returnReduce(zone,sumOp<label>());overlap=returnReduce(overlap,sumOp<label>());
    planarity=returnReduce(planarity,sumOp<label>());facePlane=returnReduce(facePlane,sumOp<label>());
    geometricCandidates=returnReduce(geometricCandidates,sumOp<label>());
    Info<< "Transition roof audit (no mutation): rectangular="<<rectangular
        << "; 9-face cells "<<nineFaces<<", bbox rejection "<<bboxRejected
        << ", non-axis-box rejection "<<axisRejected<<", non-2x2 layout rejection "<<layoutRejected
        << ", tile/fine-box rejection "<<tileRejected
        << "; regular 2x2 candidates "<<detected
        << "; physical veto "<<physical<<", coupled veto "<<coupled<<", zone veto "<<zone
        << "; overlap/full-star intersection veto "<<overlap
        << "; full-star planarity veto "<<planarity<<", face-plane veto "<<facePlane
        << "; geometry-only survivors "<<geometricCandidates
        << " (not quality-authorized; determinant/tet/weight/ratio still required)"<<nl;
    return geometricCandidates;
}

Foam::label Foam::dcCartCutCells::removeRedundantEdgePoints
(
    cutMesh& m, const boolList& coupledPatch
)
{
    // Remove a point only when EVERY incident live polygon uses the same
    // unordered predecessor/successor pair and the point lies strictly on
    // the segment between them. This replaces the same two edge segments
    // everywhere, never a T junction. No coordinates, cell incidences or
    // patch/zone identities change. Coupled and zone vertices are vetoed.
    const label nPoints = label(m.points.size());
    std::vector<unsigned char> state(nPoints, 0);
    std::vector<std::pair<label,label>> neighbours(nPoints, {-1,-1});
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        const auto& poly = m.faces[f];
        const label patch = m.patch[f];
        const bool protectedFace = m.zone[f] >= 0
         || (patch >= 0 && patch < coupledPatch.size() && coupledPatch[patch]);
        for (size_t i = 0; i < poly.size(); ++i)
        {
            const label p = poly[i];
            if (state[p] == 2) { continue; }
            if (protectedFace || poly.size() < 4) { state[p] = 2; continue; }
            const label a = poly[(i+poly.size()-1)%poly.size()];
            const label b = poly[(i+1)%poly.size()];
            const vector da = m.points[a]-m.points[p], db = m.points[b]-m.points[p];
            const scalar scale = mag(da)*mag(db);
            const scalar dot = da & db, cross = mag(da ^ db);
            if (!std::isfinite(scale) || !std::isfinite(dot) || !std::isfinite(cross)
             || scale <= VSMALL || dot >= 0 || cross > 1e-12*scale)
            { state[p] = 2; continue; }
            const std::pair<label,label> pair = std::minmax(a,b);
            if (state[p] == 0) { state[p] = 1; neighbours[p] = pair; }
            else if (neighbours[p] != pair) { state[p] = 2; }
        }
    }
    // Removing several adjacent candidates together must leave a polygon.
    // Veto all candidates of a too-small face, then propagate the veto.
    label geometryVetoes = 0;
    bool changed = true;
    while (changed)
    {
        changed = false;
        for (label f = 0; f < label(m.faces.size()); ++f)
        {
            if (!m.faceAlive[f]) { continue; }
            bool candidate = false;
            for (const label p : m.faces[f]) { candidate = candidate || state[p] == 1; }
            if (!candidate) { continue; }
            label first = -1, second = -1, retained = 0;
            for (const label p : m.faces[f])
            {
                if (state[p] == 1 || p == first || p == second) { continue; }
                if (retained == 0) { first = p; }
                else if (retained == 1) { second = p; }
                if (++retained == 3) { break; }
            }
            bool good = retained >= 3;
            if (good)
            {
                std::vector<label> poly;
                for (const label p : m.faces[f]) { if (state[p] != 1) { poly.push_back(p); } }
                vector before, after; point beforeCentre, afterCentre;
                faceAreaCentreOF(m.points,m.faces[f],before,beforeCentre);
                faceAreaCentreOF(m.points,poly,after,afterCentre);
                point lo(GREAT,GREAT,GREAT), hi(-GREAT,-GREAT,-GREAT);
                for (const label p : m.faces[f]) { lo=min(lo,m.points[p]); hi=max(hi,m.points[p]); }
                const scalar scale = max(mag(hi-lo),scalar(1e-6));
                const scalar ar = mag(before);
                good = std::isfinite(ar) && ar > VSMALL;
                if (good)
                {
                    const vector n = before/ar;
                    for (const label p : m.faces[f])
                    { if (mag((m.points[p]-beforeCentre)&n) > 1e-12*scale) { good=false; break; } }
                }
                const scalar areaError = mag(after-before)/max(ar,VSMALL);
                const scalar centreError = mag(afterCentre-beforeCentre)/scale;
                good = good && std::isfinite(areaError) && std::isfinite(centreError)
                 && areaError <= 1e-10 && centreError <= 1e-10;
            }
            if (good) { continue; }
            ++geometryVetoes;
            for (const label p : m.faces[f])
            { if (state[p] == 1) { state[p] = 2; changed = true; } }
        }
    }
    std::map<label,std::vector<label>> replacement;
    std::set<label> affected;
    scalar largestAreaError = 0, largestCentreError = 0;
    label removed = 0;
    for (const unsigned char s : state) { removed += s == 1; }
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        std::vector<label> poly;
        for (const label p : m.faces[f]) { if (state[p] != 1) { poly.push_back(p); } }
        if (poly.size() == m.faces[f].size()) { continue; }
        vector before, after; point beforeCentre, afterCentre;
        faceAreaCentreOF(m.points,m.faces[f],before,beforeCentre);
        faceAreaCentreOF(m.points,poly,after,afterCentre);
        point lo(GREAT,GREAT,GREAT), hi(-GREAT,-GREAT,-GREAT);
        for (const label p : m.faces[f]) { lo=min(lo,m.points[p]); hi=max(hi,m.points[p]); }
        const scalar areaError = mag(after-before)/max(mag(before),VSMALL);
        const scalar centreError = mag(afterCentre-beforeCentre)/max(mag(hi-lo),scalar(1e-6));
        largestAreaError = max(largestAreaError,areaError);
        largestCentreError = max(largestCentreError,centreError);
        if (!std::isfinite(areaError) || !std::isfinite(centreError)
         || areaError > 1e-10 || centreError > 1e-10)
        { FatalErrorInFunction << "redundant edge removal changed polygon geometry" << exit(FatalError); }
        replacement.emplace(f,std::move(poly));
        affected.insert(m.owner[f]);
        if (m.neighbour[f] >= 0) { affected.insert(m.neighbour[f]); }
    }
    if (!replacement.empty())
    {
        std::vector<std::vector<label>> cf;
        buildCellFaces(m,cf);
        for (const label c : affected)
        {
            std::map<std::pair<label,label>,std::pair<label,label>> edges;
            for (const label f : cf[c])
            {
                const auto found = replacement.find(f);
                const auto& poly = found == replacement.end() ? m.faces[f] : found->second;
                for (size_t i = 0; i < poly.size(); ++i)
                {
                    label a=poly[i], b=poly[(i+1)%poly.size()];
                    if (m.owner[f] != c) { std::swap(a,b); }
                    auto& usage = edges[std::minmax(a,b)];
                    ++usage.first; usage.second += a < b ? 1 : -1;
                }
            }
            for (const auto& edge : edges)
            {
                if (edge.second.first != 2 || edge.second.second != 0)
                { FatalErrorInFunction << "redundant edge removal violated cell edge closure" << exit(FatalError); }
            }
        }
        for (auto& face : replacement) { m.faces[face.first].swap(face.second); }
    }
    const label countAll = returnReduce(removed,sumOp<label>());
    const label facesAll = returnReduce(label(replacement.size()),sumOp<label>());
    const label vetoesAll = returnReduce(geometryVetoes,sumOp<label>());
    reduce(largestAreaError,maxOp<scalar>()); reduce(largestCentreError,maxOp<scalar>());
    Info<< "Redundant edge point experiment: removed " << countAll
        << " point(s) from " << facesAll << " faces; max relative area error "
        << largestAreaError << ", centre error " << largestCentreError
        << "; candidate-face geometry vetoes " << vetoesAll << nl;
    return countAll;
}


void Foam::dcCartCutCells::dropGhostCells
(
    cutMesh& m, label myRank, const std::vector<label>& procPatchOfRank
)
{
    label nGhost = 0, nProcFaces = 0;
    auto ghost = [&](label c) -> bool { return c >= 0 && m.cellRank[c] != myRank; };
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        const label o = m.owner[f], n = m.neighbour[f];
        const bool og = ghost(o), ng = (n >= 0) && ghost(n);
        if (o < 0) { m.faceAlive[f] = false; continue; }
        if (og && (n < 0 || ng)) { m.faceAlive[f] = false; continue; }   // halo-only face
        if (!og && !ng) { continue; }                                    // owned face
        // between an owned cell and a halo cell: a processor face, owned by
        // the owned cell, normal pointing out of it
        const label ghostRank = og ? m.cellRank[o] : m.cellRank[n];
        const label p = (ghostRank >= 0 && ghostRank < label(procPatchOfRank.size())) ? procPatchOfRank[ghostRank] : -1;
        if (p < 0) { m.faceAlive[f] = false; continue; }
        if (og)
        {
            std::reverse(m.faces[f].begin(), m.faces[f].end());
            m.owner[f] = n;
        }
        m.neighbour[f] = -1;
        m.patch[f] = p;
        ++nProcFaces;
    }
    for (label c = 0; c < m.nCells; ++c)
    {
        if (m.cellAlive[c] && ghost(c)) { m.cellAlive[c] = false; ++nGhost; }
    }
    if (UPstream::parRun())
    {
        Pout<< "Parallel: dropped " << nGhost << " halo cells, "
            << nProcFaces << " processor faces" << endl;
    }
}


// * * * * * * * * * * * * * * * * toPolyMesh  * * * * * * * * * * * * * * * //

void Foam::dcCartCutCells::lowerWriterNumeric
(
    cutMesh& m, const wordList& patchNames, const wordList& patchTypes,
    const wordList& faceZoneNames, writerNumericData& out, writerBindings* bindings
)
{
    // Shared NORMAL writer lowering. No IO, patch collectives, registration
    // or quality/source authority is performed by this numeric routine.
    // ---- cells: compact ---------------------------------------------------
    auto& newCell=out.newCell;newCell.assign(m.nCells,-1);
    label& nC=out.nC;nC=0;
    for (label c = 0; c < m.nCells; ++c)
    {
        if (m.cellAlive[c]) { newCell[c] = nC++; }
    }

    if (bindings) { mergeCoincidentPoints(m,nullptr,&bindings->alias); }
    else { mergeCoincidentPoints(m); }

    // ---- faces: orient owner < neighbour, order --------------------------
    auto& internalIds=out.internalIds;auto& boundaryIds=out.boundaryIds;internalIds.clear();boundaryIds.clear();
    for (label f = 0; f < label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        // Collapse repeated vertices; a face that degenerates to a line or a
        // point has no area and is dropped.
        {
            std::vector<label>& poly = m.faces[f];
            // (most faces are clean: look for a repeat or a spur before
            // building the cleaned copy)
            bool dirtyPoly = (poly.size() > 1 && poly.front() == poly.back());
            for (size_t q = 1; q < poly.size() && !dirtyPoly; ++q) { dirtyPoly = (poly[q] == poly[q - 1]); }
            if (!dirtyPoly && poly.size() >= 3)
            {
                const size_t k = poly.size();
                for (size_t q = 0; q < k && !dirtyPoly; ++q) { dirtyPoly = (poly[q] == poly[(q + 2) % k]); }
            }
            std::vector<label> clean;
            if (dirtyPoly)
            {
            for (label v : poly)
            {
                if (clean.empty() || clean.back() != v) { clean.push_back(v); }
            }
            while (clean.size() > 1 && clean.front() == clean.back()) { clean.pop_back(); }
            // spurs (a, b, a): a zero-width slit, dropped
            for (bool again = true; again && clean.size() >= 3;)
            {
                again = false;
                const label k = label(clean.size());
                for (label i = 0; i < k; ++i)
                {
                    if (clean[i] == clean[(i + 2) % k])
                    {
                        // remove entries i+1 and i+2 (cyclic)
                        const label i1 = (i + 1) % k, i2 = (i + 2) % k;
                        std::vector<label> nxt;
                        for (label j = 0; j < k; ++j) { if (j != i1 && j != i2) { nxt.push_back(clean[j]); } }
                        clean = nxt; again = true; break;
                    }
                }
            }
            }
            else
            {
                clean.swap(poly);
            }
            static const bool debugFaces = (getenv("DCCART_DEBUG") != nullptr);
            if (debugFaces)
            {
                std::set<label> seen; bool dup = false;
                for (label v : clean) { if (!seen.insert(v).second) { dup = true; } }
                if (dup)
                {
                    Info<< "  face " << f << " has a repeated vertex:";
                    for (label v : clean) { Info<< " " << v << "@" << m.points[v]; }
                    Info<< " patch " << m.patch[f] << " zone " << m.zone[f] << nl;
                }
            }
            poly.swap(clean);
            if (poly.size() >= 3)
            {
                vector nn; point cc;
                polygonNormalCentre(m.points, poly, nn, cc);
                // a face below 1e-12 m2 carries no flux and only feeds
                // checkMesh a 1e15 skewness
                if (mag(nn) <= 1e-12) { m.faceAlive[f] = false; continue; }
            }
        }
        if (m.faces[f].size() < 3) { continue; }
        label o = m.owner[f] >= 0 ? newCell[m.owner[f]] : -1;
        label n = m.neighbour[f] >= 0 ? newCell[m.neighbour[f]] : -1;
        if (o < 0 && n < 0) { continue; }
        if (o < 0)
        {
            std::reverse(m.faces[f].begin(), m.faces[f].end());
            o = n; n = -1;
        }
        if (n >= 0 && o > n)
        {
            std::reverse(m.faces[f].begin(), m.faces[f].end());
            std::swap(o, n);
        }
        m.owner[f] = o;
        m.neighbour[f] = n;
        if (n >= 0) { internalIds.push_back(f); }
        else { boundaryIds.push_back(f); }
    }
    std::sort(internalIds.begin(), internalIds.end(), [&](label a, label b)
    {
        if (m.owner[a] != m.owner[b]) { return m.owner[a] < m.owner[b]; }
        return m.neighbour[a] < m.neighbour[b];
    });
    std::stable_sort(boundaryIds.begin(), boundaryIds.end(), [&](label a, label b)
    {
        return std::max(label(0), m.patch[a]) < std::max(label(0), m.patch[b]);
    });
    // A shared polygon's exact coordinate cycle is a common, total order.
    // A tolerance comparator on face centres is not transitive and violates
    // std::sort's contract. Canonical cycles also avoid summation-dependent
    // centre coordinates and local point labels as a shared tie-break.
    {
        using coordinate=std::tuple<scalar,scalar,scalar>;
        using polygonKey=std::vector<coordinate>;
        auto keyOf=[&](const std::vector<label>& face)
        {
            polygonKey best;
            for (label direction:{label(1),label(-1)}) for (label start=0;start<label(face.size());++start)
            {
                polygonKey candidate;candidate.reserve(face.size());
                for (label q=0;q<label(face.size());++q)
                {
                    const point& x=m.points[face[(start+direction*q+label(face.size())*2)%face.size()]];
                    if (!std::isfinite(x.x()) || !std::isfinite(x.y()) || !std::isfinite(x.z()))
                    { FatalErrorInFunction<<"non-finite coupled polygon coordinate"<<exit(FatalError); }
                    candidate.emplace_back(x.x(),x.y(),x.z());
                }
                if (best.empty() || candidate<best) { best=std::move(candidate); }
            }
            return best;
        };
        size_t i=0;
        while (i<boundaryIds.size())
        {
            const label p=std::max(label(0),m.patch[boundaryIds[i]]);
            size_t j=i;
            while (j<boundaryIds.size() && std::max(label(0),m.patch[boundaryIds[j]])==p) { ++j; }
            if (p<patchTypes.size() && (patchTypes[p]=="processor" || patchTypes[p]=="processorCyclic"
                || (patchTypes[p]=="cyclic" && getenv("DCCART_CONFORMING_CONES"))))
            {
                // The optional cone path qualifies coincident coupled twins
                // before subdivision. No transformed interface is inferred
                // from a local world-coordinate ordering.
                std::vector<std::pair<polygonKey,label>> keyed;keyed.reserve(j-i);
                for (size_t k=i;k<j;++k) { keyed.emplace_back(keyOf(m.faces[boundaryIds[k]]),boundaryIds[k]); }
                std::sort(keyed.begin(),keyed.end(),[](const std::pair<polygonKey,label>& a,const std::pair<polygonKey,label>& b)
                { return a.first<b.first; });
                for (size_t k=i;k<j;++k)
                {
                    if (k>i && keyed[k-i].first==keyed[k-i-1].first)
                    { FatalErrorInFunction<<"duplicate exact coupled polygon in patch "<<patchNames[p]<<exit(FatalError); }
                    boundaryIds[k]=keyed[k-i].second;
                    auto& face=m.faces[boundaryIds[k]];size_t best=0;
                    for (size_t q=1;q<face.size();++q)
                    {
                        const point& a=m.points[face[q]];const point& b=m.points[face[best]];
                        if (coordinate(a.x(),a.y(),a.z())<coordinate(b.x(),b.y(),b.z())) { best=q; }
                    }
                    if (best) { std::rotate(face.begin(),face.begin()+best,face.end()); }
                }
            }
            i=j;
        }
    }

    // ---- points: keep only used ones -------------------------------------
    auto& newPoint=out.newPoint;newPoint.assign(m.points.size(),-1);
    label& nP=out.nP;nP=0;
    auto usePoints = [&](label f)
    {
        for (label v : m.faces[f]) { if (newPoint[v] < 0) { newPoint[v] = nP++; } }
    };
    for (label f : internalIds) { usePoints(f); }
    for (label f : boundaryIds) { usePoints(f); }
    auto& pts=out.points;pts.setSize(nP);
    for (label p = 0; p < label(m.points.size()); ++p)
    {
        if (newPoint[p] >= 0) { pts[newPoint[p]] = m.points[p]; }
    }

    label& nInt=out.nInt;nInt=label(internalIds.size());
    label& nF=out.nF;nF=nInt+label(boundaryIds.size());
    auto& fcs=out.faces;fcs.setSize(nF);
    auto& own=out.owner;auto& nei=out.neighbour;own.setSize(nF);nei.setSize(nInt);
    auto& finalPatch=out.finalPatch;finalPatch.assign(nF,-1);
    label i = 0;
    for (label f : internalIds)
    {
        face& fc = fcs[i];
        fc.setSize(label(m.faces[f].size()));
        forAll(fc, k) { fc[k] = newPoint[m.faces[f][k]]; }
        own[i] = m.owner[f]; nei[i] = m.neighbour[f];
        ++i;
    }
    // A zone can contain tens of thousands of faces: grow geometrically
    // instead of reallocating and copying a labelList on every append.
    auto& zoneFaces=out.zoneFaces;zoneFaces.setSize(faceZoneNames.size());for(auto& zone:zoneFaces)zone.clear();
    {
        label ii = 0;
        for (label f : internalIds)
        {
            if (m.zone[f] >= 0 && m.zone[f] < faceZoneNames.size())
            {
                zoneFaces[m.zone[f]].append(ii);
            }
            ++ii;
        }
    }
    for (label f : boundaryIds)
    {
        face& fc = fcs[i];
        fc.setSize(label(m.faces[f].size()));
        forAll(fc, k) { fc[k] = newPoint[m.faces[f][k]]; }
        own[i] = m.owner[f];
        finalPatch[i] = std::max(label(0), m.patch[f]);
        // A baffle can coincide with a processor interface. dropGhostCells
        // keeps its zone tag when it turns the internal face into a boundary
        // face; retain that tag here as well. Otherwise reconstruction and
        // constrained redistribution silently lose this part of the baffle.
        const label z = m.zone[f];
        const label p = finalPatch[i];
        if
        (
            z >= 0 && z < zoneFaces.size()
         && p < patchTypes.size() && patchTypes[p] == "processor"
        )
        {
            zoneFaces[z].append(i);
        }
        ++i;
    }

    if (bindings)
    {
        bindings->cell=newCell;bindings->point=newPoint;
        bindings->face.assign(m.faces.size(),-1);label q=0;
        for (label f:internalIds) { bindings->face[f]=q++; }
        for (label f:boundaryIds) { bindings->face[f]=q++; }
    }
}
 
Foam::autoPtr<Foam::polyMesh> Foam::dcCartCutCells::toPolyMesh
(
    cutMesh& m,
    const Time& runTime,
    const word& regionName,
    const wordList& patchNames,
    const wordList& patchTypes,
    const wordList& faceZoneNames,
    const wordList& patchNeighbours,
    const wordList& patchGroups,
    const bool baselineOnly,
    writerBindings* requestedBindings,
    const std::function<scalar()>& remainingPointRepairSeconds,
    const bool singletonSourceRoleEligible,
    labelList* writtenPatchAddressMap
)
{
    if (!baselineOnly && remainingPointRepairSeconds)
    {
        // This first prototype requires the immutable earliest installed
        // coupled certificate captured by the literal-union path. All ranks
        // refuse together before normalizing or changing any point.
        const label missingBaseline=returnReduce(label(!m.writerBaseline),sumOp<label>());
        if (missingBaseline)
        {
            FatalErrorInFunction
                << "Shared-point repair requires the earliest literal-union writer baseline on every rank; no mesh written"
                << exit(FatalError);
        }
    }
    // The oracle executes this exact normalizer on a separate deep copy.
    // It is unregistered/NO_WRITE and explicitly bypasses TRACE file output.
    writerBindings finalBindings;
    writerBindings* bindings=requestedBindings;
    if (!baselineOnly && m.writerBaseline && !bindings) { bindings=&finalBindings; }
    // PRIVATE default-OFF owned raw lease; no authority/quality/cache callback.
    auto fullCapture=dcCartCompleteCavityFullCapture::retain
        (!baselineOnly&&dcCartFullCaptureIO::enabled(),m);
    writerNumericData normalized;
    lowerWriterNumeric(m,patchNames,patchTypes,faceZoneNames,normalized,bindings);
    auto& newCell=normalized.newCell;auto& newPoint=normalized.newPoint;
    auto& internalIds=normalized.internalIds;auto& boundaryIds=normalized.boundaryIds;
    auto& pts=normalized.points;auto& fcs=normalized.faces;
    auto& own=normalized.owner;auto& nei=normalized.neighbour;
    auto& finalPatch=normalized.finalPatch;auto& zoneFaces=normalized.zoneFaces;
    const label nC=normalized.nC,nP=normalized.nP,nInt=normalized.nInt,nF=normalized.nF;
    if (!baselineOnly && !m.coneEvidenceParent.empty())
    {
        if (m.coneEvidenceParent.size()!=size_t(m.nCells) || m.coneEvidenceBase.size()!=m.faces.size())
        { FatalErrorInFunction<<"inconsistent optional cone evidence addressing"<<exit(FatalError); }
        const std::string prefix="conforming-written-rank"+std::to_string(Pstream::myProcNo())+"-";
        auto writeLabels=[&](const std::string& name,const std::vector<label>& values)
        {
            std::ofstream out(prefix+name,std::ios::binary);
            out.write(reinterpret_cast<const char*>(values.data()),values.size()*sizeof(label));
            if (!out) { FatalErrorInFunction<<"could not write optional cone compact map "<<name<<exit(FatalError); }
        };
        std::vector<label> parent(nC,-1),nativeCell(nC,-1),base(nF,-1),nativeFace(nF,-1),nativePoint(nP,-1);
        for (label c=0;c<m.nCells;++c) if (newCell[c]>=0)
        { parent[newCell[c]]=m.coneEvidenceParent[c];nativeCell[newCell[c]]=c; }
        label q=0;
        for (label f:internalIds) { base[q]=m.coneEvidenceBase[f];nativeFace[q++]=f; }
        for (label f:boundaryIds) { base[q]=m.coneEvidenceBase[f];nativeFace[q++]=f; }
        for (label p=0;p<label(newPoint.size());++p) if (newPoint[p]>=0) { nativePoint[newPoint[p]]=p; }
        writeLabels("cell-parent.i32",parent);writeLabels("cell-native.i32",nativeCell);
        writeLabels("face-base.i32",base);writeLabels("face-native.i32",nativeFace);writeLabels("point-native.i32",nativePoint);
        std::ofstream meta(prefix+"manifest.json");meta<<"{\"format\":\"conforming-written-stage/v1\",\"rank\":"<<Pstream::myProcNo()
            <<",\"cells\":"<<nC<<",\"faces\":"<<nF<<",\"points\":"<<nP<<",\"labelBytes\":"<<sizeof(label)
            <<",\"mappingScope\":\"indices follow actual compact polyMesh cell/face/point order; -1 face-base marks new cone side\"}\n";
        if (!meta) { FatalErrorInFunction<<"could not write optional cone written map manifest"<<exit(FatalError); }
    }

    // ---- drop the patch names no rank put a face in ----------------------
    // The mesher names a patch for every solid in every STL, for the six
    // domain sides, and for every baffle pair. Most of those never receive a
    // face: a solid wholly inside another, one the project hid, a domain side
    // the named surfaces cover completely. The pipeline used to end with
    // redistributePar and createBaffles, and both rebuild the boundary, so
    // every one of them disappeared on the way out -- a solved case saw 170
    // patch names on DAML-Testbed, none of them empty. The partitioned path
    // writes THIS boundary straight to disk and skipped both utilities, so it
    // carried 255 names, 86 of them empty everywhere, and the runner wrote 86
    // boundary conditions for faces that do not exist. Prune them here, by the
    // GLOBAL face count, so the two paths hand the solver the same list and
    // every rank still agrees on it (a per-rank count would drop a patch one
    // rank touches and another does not, and the ranks would then disagree on
    // the boundary -- which is legal but is not what decomposePar writes).
    // A name another KEPT patch refers to stays: a cyclic's neighbourPatch and
    // a processorCyclic's referPatch are names, and one pointing at nothing is
    // a boundary OpenFOAM refuses to read.
    std::vector<char> keepPatch(patchNames.size(), 1);
    label nEmptyPatches = 0;
    if (getenv("DCCART_KEEP_EMPTY_PATCHES") == nullptr)
    {
        // patchNames is NOT the same length on every rank: each one appends
        // its own procBoundary<me>to<nbr> (and processorCyclic) patches after
        // the common part, so a listCombine over the whole list truncates MPI
        // ("Message from rank 1 truncated"). Tally the common part -- every
        // surface region, the six domain sides and the baffle pairs, which
        // every rank builds identically -- in a compact list of that part
        // alone and reduce THAT. The processor patches are decided locally: a
        // rank with no face to a neighbour has a zero-face pair on both sides,
        // so the two ranks drop their own half independently and still agree.
        auto isProc = [&](label q) -> bool
        {
            return q < patchTypes.size()
                && (patchTypes[q] == "processor" || patchTypes[q] == "processorCyclic");
        };
        labelList indexOf(patchNames.size(), -1);
        label nCommon = 0;
        forAll(patchNames, q)
        {
            if (!isProc(q)) { indexOf[q] = nCommon++; }
        }
        // duplicate solid names fold onto one patch below, so count them as
        // the one name they are
        std::unordered_map<std::string, label> firstOf;
        forAll(patchNames, q)
        {
            if (indexOf[q] >= 0) { firstOf.emplace(std::string(patchNames[q]), indexOf[q]); }
        }
        labelList named(nCommon, 0);
        labelList procTally(patchNames.size(), 0);
        for (label f = nInt; f < nF; ++f)
        {
            const label p = finalPatch[f];
            if (p < 0 || p >= patchNames.size()) { continue; }
            if (indexOf[p] >= 0) { ++named[firstOf[std::string(patchNames[p])]]; }
            else { ++procTally[p]; }
        }
        if (nCommon > 0)
        {
            Pstream::listCombineGather(named, plusEqOp<label>());
            Pstream::listCombineScatter(named);
        }
        std::unordered_set<std::string> keepNames;
        forAll(patchNames, q)
        {
            if (indexOf[q] < 0) { continue; }
            const std::string nm(patchNames[q]);
            if (named[firstOf[nm]] > 0) { keepNames.insert(nm); }
        }
        // A name another KEPT patch refers to stays: a cyclic's
        // neighbourPatch and a processorCyclic's referPatch are names, and one
        // pointing at nothing is a boundary OpenFOAM refuses to read.
        auto refer = [&](const word& nb)
        {
            if (nb == word::null || nb.empty()) { return; }
            if (patchNames.find(nb) >= 0) { keepNames.insert(std::string(nb)); }
        };
        forAll(patchNames, q)
        {
            const std::string nm(patchNames[q]);
            const bool kept = (indexOf[q] < 0) ? (procTally[q] > 0)
                                               : (keepNames.count(nm) != 0);
            if (!kept) { continue; }
            refer(q < patchNeighbours.size() ? patchNeighbours[q] : word::null);
            if (q < patchTypes.size() && patchTypes[q] == "processorCyclic")
            {
                // procBoundary<me>to<nbr>through<referPatch>
                const size_t t = nm.find("through");
                if (t != std::string::npos) { refer(word(nm.substr(t + 7))); }
            }
        }
        std::unordered_set<std::string> dropped;
        forAll(patchNames, q)
        {
            const std::string nm(patchNames[q]);
            const bool kept = (indexOf[q] < 0) ? (procTally[q] > 0)
                                               : (keepNames.count(nm) != 0);
            keepPatch[q] = kept ? 1 : 0;
            if (!kept) { dropped.insert(nm); }
        }
        nEmptyPatches = label(dropped.size());
    }

    // One name = ONE patch. Two regions of two surfaces can carry the same
    // solid name -- the runner splits a wall baffle out of a merged STL and
    // the new file keeps the original region name (Base604:
    // Room_Raising_Floor_part1 is both a region of merged_floor_ceiling.stl
    // and the split-out wallBaffle_*.stl; Slab: Raising_floor_part1) -- and
    // polyMesh refuses a boundary that repeats a name:
    //   ****Duplicate boundary patch 5693 named X of type wall.
    //   ***Boundary definition is in error.
    // That makes dcCartMesh's OWN output unreadable to checkMesh,
    // redistributePar and every other utility until createBaffles happens to
    // rename one of them into _master/_slave. Fold each repeat onto the first
    // patch of that name: both are the same wall region, so the faces, their
    // order and the boundary condition are unchanged -- only the duplicate
    // entry disappears.
    wordList bndNames;
    wordList bndTypes;
    wordList bndNeighbours;
    wordList bndGroups;
    labelList patchOf(patchNames.size(), 0);
    {
        std::unordered_map<std::string, label> seen;
        DynamicList<word> nm(patchNames.size());
        DynamicList<word> ty(patchNames.size());
        DynamicList<word> nb(patchNames.size());
        DynamicList<word> gp(patchNames.size());
        forAll(patchNames, q)
        {
            if (!keepPatch[q]) { patchOf[q] = -1; continue; }
            const std::string key(patchNames[q]);
            const auto it = seen.find(key);
            if (it != seen.end()) { patchOf[q] = it->second; continue; }
            patchOf[q] = nm.size();
            seen.emplace(key, patchOf[q]);
            nm.append(patchNames[q]);
            ty.append(q < patchTypes.size() ? patchTypes[q] : word("patch"));
            nb.append(q < patchNeighbours.size() ? patchNeighbours[q] : word::null);
            gp.append(q < patchGroups.size() ? patchGroups[q] : word::null);
        }
        bndNames = wordList(std::move(nm));
        bndTypes = wordList(std::move(ty));
        bndNeighbours = wordList(std::move(nb));
        bndGroups = wordList(std::move(gp));
    }
    if (nEmptyPatches)
    {
        Info<< "Cut mesh: dropped " << nEmptyPatches
            << " patch name(s) no rank put a face in" << nl;
    }
    if (bndNames.size() != patchNames.size())
    {
        for (label f = nInt; f < nF; ++f) { finalPatch[f] = patchOf[finalPatch[f]]; }
        const label nFolded =
            label(patchNames.size()) - nEmptyPatches - label(bndNames.size());
        if (nFolded > 0)
        {
            Info<< "Cut mesh: folded " << nFolded
                << " duplicate boundary patch name(s) onto the first patch of"
                << " that name" << nl;
        }
    }

    labelList patchSizes(bndNames.size(), 0);
    labelList patchStarts(bndNames.size(), nInt);
    for (label f = nInt; f < nF; ++f) { ++patchSizes[finalPatch[f]]; }
    {
        label start = nInt;
        forAll(patchSizes, p) { patchStarts[p] = start; start += patchSizes[p]; }
    }

    autoPtr<polyMesh> meshPtr
    (
        new polyMesh
        (
            IOobject
            (
                baselineOnly ? word("dcCartPreOperatorWriterOracle") : regionName,
                runTime.constant(),
                runTime,
                IOobject::NO_READ,
                baselineOnly ? IOobject::NO_WRITE : IOobject::AUTO_WRITE,
                !baselineOnly
            ),
            std::move(pts),
            std::move(fcs),
            std::move(own),
            std::move(nei)
        )
    );
    polyMesh& mesh = *meshPtr;

    List<polyPatch*> patches(bndNames.size());
    forAll(bndNames, p)
    {
        if (bndTypes[p] == "processor" || bndTypes[p] == "processorCyclic")
        {
            // procBoundary<me>to<nbr>[through<referPatch>]. A baffle whose two
            // cells the slab split put on different ranks becomes one of these
            // per side, exactly as decomposePar splits a cyclic pair: the
            // rank whose face points the master's way (baffleFaceSide, a
            // function of the geometry alone) refers to the master and the
            // other to the slave, so the two ranks agree without exchanging
            // anything.
            const std::string nm = bndNames[p];
            const size_t a = nm.find("procBoundary"), b = nm.find("to", a + 12);
            const int me = std::atoi(nm.substr(a + 12, b - a - 12).c_str());
            const size_t t = nm.find("through", b + 2);
            const int nb = std::atoi
            (
                nm.substr(b + 2, (t == std::string::npos ? nm.size() : t) - (b + 2)).c_str()
            );
            dictionary d;
            d.add("type", bndTypes[p]);
            d.add("nFaces", patchSizes[p]);
            d.add("startFace", patchStarts[p]);
            d.add("myProcNo", label(me));
            d.add("neighbProcNo", label(nb));
            if (t != std::string::npos)
            {
                d.add("referPatch", word(nm.substr(t + 7)));
            }
            patches[p] = polyPatch::New(bndNames[p], d, p, mesh.boundaryMesh()).ptr();
            continue;
        }
        if (bndTypes[p] == "cyclic")
        {
            dictionary d;
            d.add("type", word("cyclic"));
            d.add("nFaces", patchSizes[p]);
            d.add("startFace", patchStarts[p]);
            if (bndNeighbours[p] != word::null)
            {
                d.add("neighbourPatch", bndNeighbours[p]);
            }
            // A zero-thickness baffle's two sides are the same polygon in the
            // same place, and splitBaffleZones appends the slave copies in
            // ascending master order, so face k of one IS the partner of face
            // k of the other -- which is what coincidentFullMatch states.
            // Left at the default `unknown`, globalMeshData has to rediscover
            // that by geometry and fails on the pairs (findTransform:
            // "Cannot find (4 1) ... in 4((0 1) (1 1) (2 1) (3 1))"). This is
            // also the value the pipeline always ended up with: createBaffles
            // wrote `unknown` and fix_cyclic_match_tolerance rewrote it.
            d.add("transform", word("coincidentFullMatch"));
            if (bndGroups[p] != word::null)
            {
                d.add("inGroups", wordList(1, bndGroups[p]));
            }
            patches[p] = polyPatch::New(bndNames[p], d, p, mesh.boundaryMesh()).ptr();
            continue;
        }
        patches[p] = polyPatch::New
        (
            bndTypes[p], bndNames[p], patchSizes[p], patchStarts[p], p,
            mesh.boundaryMesh()
        ).ptr();
    }
    // validBoundary=false: the processor patches' geometry exchange happens
    // in the solver / reconstructParMesh; here the two sides are only
    // required to hold the same faces in the same order
    mesh.addPatches(patches, false);

    List<pointZone*> pz;
    List<faceZone*> fz(faceZoneNames.size());
    List<cellZone*> cz;
    forAll(faceZoneNames, z)
    {
        boolList zoneFlips(zoneFaces[z].size(), false);
        forAll(zoneFaces[z], j)
        {
            const label facei = zoneFaces[z][j];
            if (facei < nInt) { continue; }
            const polyPatch& pp = mesh.boundaryMesh()[finalPatch[facei]];
            if (isA<processorPolyPatch>(pp))
            {
                // The two processor copies point in opposite directions. A
                // shared faceZone must therefore have opposite flip bits too;
                // choose the lower rank as its common orientation. Internal
                // faces retain the existing, unflipped convention.
                zoneFlips[j] = !refCast<const processorPolyPatch>(pp).owner();
            }
            else
            {
                // A baffle pair this mesher split itself. createBaffles'
                // convention is master unflipped, slave flipped, and the pair
                // is named <zone>_master / <zone>_slave by the caller. The
                // cast above used to be unconditional, so every wall-baffle
                // face in a zone was reinterpreted as a processor patch and
                // read its flip out of unrelated memory.
                const word& nm = pp.name();
                zoneFlips[j] = (nm.size() > 6 && nm.substr(nm.size() - 6) == "_slave");
            }
        }
        fz[z] = new faceZone
        (
            faceZoneNames[z],
            zoneFaces[z],
            zoneFlips,
            z,
            mesh.faceZones()
        );
    }
    mesh.addZones(pz, fz, cz);

    auto verifyWeightStage=[&](bool literalShell,bool capturePrefix)
    {
        if (baselineOnly || !m.weightUnionState) { return; }
        // All ranks have state, even disabled/no-candidate/expired ranks.
        // This is BEFORE point repair; its own installed baseline therefore
        // includes every new W-prefix passing role. Existing coupled oracle
        // remains earliest and runs after point repair without recapture.
        // Missing authority or an empty bounded pool retains the old path,
        // including callers that do not request typed final bindings.
        const label localWeightFailures=m.weightUnionState->committed.empty() ? 0
            : (bindings
                ? dcCartWeightUnionPreview::verify
                    (*m.weightUnionState,m,mesh,*bindings,literalShell,capturePrefix):1);
        label weightEvidenceFailures=0;
        if (getenv("DCCART_CONE_EVIDENCE") || localWeightFailures)
        {
            const fileName file=runTime.path()/("conforming-weight-unions-"
                +std::string(literalShell ? "installed-prefix":"installed-final")
                +"-rank"+std::to_string(Pstream::myProcNo())+".json");
            std::ofstream out(file.c_str());
            if (out)
            {
                const auto& state=*m.weightUnionState;
                out<<std::setprecision(17)<<"{\"format\":\"normal-W-union-lineage/v1\",\"failures\":"
                    <<localWeightFailures<<",\"rawSha256\":"<<writerJsonQuote(state.authority.rawSha256)
                    <<",\"rolesDigest\":"<<writerJsonQuote(state.authority.rolesDigest)
                    <<",\"homogeneousFluidKey\":"<<writerJsonQuote(state.authority.homogeneousFluidKey)
                    <<",\"ordinaryCCVExactRequired\":false,\"coupledEarliestCertificateUnchanged\":true,\"records\":[";
                for (size_t i=0;i<state.committed.size();++i)
                {
                    if (i) { out<<','; }const auto& p=state.committed[i];
                    const label compact=bindings ? dcCartWeightUnionPreview::mappedCell(*bindings,p.keep):-1;
                    out<<"{\"keep\":"<<p.keep<<",\"drop\":"<<p.drop<<",\"compactSurvivor\":"<<compact
                       <<",\"deletedInternalFace\":"<<p.nativeFace<<",\"installedSeedW\":"<<p.weight
                       <<",\"normalOrigins\":[";
                    for (size_t j=0;j<p.origins.size();++j)
                    {
                        if (j) { out<<','; }const auto& o=p.origins[j];
                        out<<"{\"parentHandle\":"<<o.nativeParent<<",\"rank\":"<<o.rank
                           <<",\"level\":"<<o.level<<",\"leaf\":"<<o.leaf<<",\"piece\":"<<o.piece
                           <<",\"region\":"<<writerJsonQuote(o.region)<<",\"sourceFacetPatchZone\":[";
                        for (size_t k=0;k<o.sourceFacets.size();++k)
                        { if (k) { out<<','; }out<<'['<<o.sourceFacets[k][0]<<','<<o.sourceFacets[k][1]<<','<<o.sourceFacets[k][2]<<']'; }
                        out<<"]}";
                    }
                    out<<"],\"literalExteriorNativeFaces\":[";
                    for (size_t j=0;j<p.outer.size();++j) { if (j) { out<<','; }out<<p.outer[j]; }
                    out<<"]}";
                }
                out<<"]}\n";
            }
            if (!out) { ++weightEvidenceFailures; }
        }
        const label globalWeightFailures=returnReduce(localWeightFailures+weightEvidenceFailures,sumOp<label>());
        Pout<<"Conforming W-only installed verification: rank "<<Pstream::myProcNo()
            <<", captured "<<m.weightUnionState->pool.size()
            <<", examined/tried/accepted "<<m.weightUnionState->examined<<'/'
            <<m.weightUnionState->tried<<'/'<<m.weightUnionState->accepted
            <<", dependency/guard refused "<<m.weightUnionState->dependencyRefused<<'/'
            <<m.weightUnionState->guardRefused<<", protected/geometry/quality refused "
            <<m.weightUnionState->protectedRefused<<'/'
            <<m.weightUnionState->geometryRefused<<'/'
            <<m.weightUnionState->qualityRefused<<", failures "<<localWeightFailures
            <<", D discovery/capture seconds "<<m.weightUnionState->discoveryCaptureSeconds
            <<", cross-class ambiguous refused "<<m.weightUnionState->crossClassRefused
            <<", literal-shell/prefix-capture "<<literalShell<<'/'<<capturePrefix
            <<"; ordinary CC/V DP is not a physical gate; coupled certificate unchanged"<<endl;
        if (globalWeightFailures)
        {
            if (m.writerBaseline)
            {
                const fileName prefix=runTime.path()/("conforming-preoperator-rank"
                    +std::to_string(Pstream::myProcNo())+"-");
                writeWriterCheckpoint(*m.writerBaseline->checkpoint,prefix);
            }
            FatalErrorInFunction<<"W-only actual installed lineage/geometry/source guard refused on "
                <<globalWeightFailures<<" packet(s); no mesh written"<<exit(FatalError);
        }
    };
    auto verifyCompleteCavityStage=[&](bool finalStage)
    {
        if(baselineOnly||!m.completeCavityRepairRequested)return;
        dcCartCompleteCavityPreparedTopology::Certificate report;
        label local=m.completeCavityState
            ? (bindings?dcCartCompleteCavityRepair::verify(m,mesh,*bindings,*m.completeCavityState,report):1):0;
        if(m.completeCavityContext&&m.completeCavityContext->physicalPointJournal)
        {
            const bool pointOk=bindings&&m.completeCavityContext->physicalPointJournal->verifyInstalled(m,mesh,*bindings);
            if(!pointOk)++local;
            Pout<<"PRIVATE normal physical-point installed "<<(finalStage?"final":"prefix")
                <<": rank "<<Pstream::myProcNo()<<", sealed source/alias/full-halo failure "<<label(!pointOk)
                <<"; earliest coupled certificate remains separate"<<endl;
        }
        // Fixed protocol, including no-candidate/expired/unsupported ranks.
        const label failures=returnReduce(local,sumOp<label>());
        if(m.completeCavityState)
        {
            Pout<<"Normal complete-cavity installed "<<(finalStage?"final":"prefix")
                <<": rank "<<Pstream::myProcNo()<<", failure "<<local
                <<", D old/new "<<report.oldDBad<<'/'<<report.newDBad<<", W/R/S/N old/new ";
            for(unsigned k=0;k<4;++k){Pout<<report.oldBad[k]<<'/'<<report.newBad[k]<<' ';}
            Pout<<"; literal source/physical/alias rings, actual installed fields, no threshold credit"<<endl;
        }
        if(failures)
        {
            const fileName prefix=runTime.path()/("conforming-preoperator-rank"
                +std::to_string(Pstream::myProcNo())+"-");
            const bool preserved=writeWriterCheckpoint(*m.writerBaseline->checkpoint,prefix);
            FatalErrorInFunction<<"complete-cavity actual installed "<<(finalStage?"final":"prefix")
                <<" guard refused on "<<failures<<" rank(s); original checkpoint preserved "
                <<preserved<<"; no mesh written"<<exit(FatalError);
        }
    };
    verifyWeightStage(true,true);
    verifyCompleteCavityStage(false);
    if (!baselineOnly && remainingPointRepairSeconds)
    {
        // All ranks enter every gate, including false/empty ranks. The
        // default false proof cannot authorize singleton3 via empty zones.
        const label minimumSourceProof=returnReduce
            (label(singletonSourceRoleEligible),minOp<label>());
        const label maximumSourceProof=returnReduce
            (label(singletonSourceRoleEligible),maxOp<label>());
        const label anyCellZones=returnReduce
            (label(!mesh.cellZones().empty()),maxOp<label>());
        const bool sourceScopeEligible=minimumSourceProof==1
            && maximumSourceProof==1 && !anyCellZones;
        if (Pstream::master() && minimumSourceProof!=maximumSourceProof)
        {
            WarningInFunction
                << "singleton3 source proof differs across ranks; refused" << nl;
        }
        if (Pstream::master() && anyCellZones)
        {
            WarningInFunction
                << "singleton3 requires globally empty installed cellZones; refused" << nl;
        }
        std::function<bool(const std::vector<label>&)> singletonSourceRoleContract;
        if (sourceScopeEligible)
        {
            // These are FINAL compact writer bindings, produced above after
            // aliasing/face ordering. Boundary/faceZone/pointZone/coreCoupled
            // incident protection remains in the V5 eligible/Stencil guards;
            // no point-to-surface/material identity is invented for interiors.
            singletonSourceRoleContract=[&](const std::vector<label>& moving) -> bool
            {
                if (!bindings || bindings->cell.size()!=size_t(m.nCells)
                 || bindings->face.size()!=m.faces.size()
                 || bindings->point.size()!=m.points.size()
                 || bindings->alias.size()!=m.points.size()
                 || moving.size()!=1) return false;
                const label p=moving.front();
                return p>=0 && p<mesh.nPoints();
            };
        }
        // Requires the separately reviewed V5 three-argument stage header.
        // Empty callback preserves all previous non-singleton behavior.
        dcCartFinitePointRepair::apply
            (mesh,remainingPointRepairSeconds,singletonSourceRoleContract);
    }
    // The ACTUAL final installed state, after any point-repair refresh.
    verifyWeightStage(false,false);
    verifyCompleteCavityStage(true);
    if (!baselineOnly && m.writerBaseline)
    {
        clockTime verifyClock;
        // This is the REAL final polyMesh after point aliasing, compact
        // owner/face ordering, patch normalization and zone construction.
        // Every rank enters the same reduction, including empty/cert-zero ranks.
        label localFailures=bindings
            ? verifyWriterCoupled(m,mesh,*bindings,*m.writerBaseline) : 1;
        writerCoupledBaseline actual;
        actual.checkpoint=m.writerBaseline->checkpoint;
        actual.copySeconds=m.writerBaseline->copySeconds;
        actual.constructSeconds=m.writerBaseline->constructSeconds;
        actual.snapshotSeconds=m.writerBaseline->snapshotSeconds;
        actual.cutPayloadBytes=m.writerBaseline->cutPayloadBytes;
        // Evidence carries the REAL final compact bindings and installed
        // bytes, not a second serialization of expected baseline values.
        if (bindings) { localFailures+=snapshotWriterCoupled
            (*actual.checkpoint,mesh,*bindings,actual); }
        const scalar verifySeconds=verifyClock.elapsedTime();
        label ioFailures=0;
        if (getenv("DCCART_CONE_EVIDENCE") || localFailures)
        {
            const fileName output=runTime.path()/("conforming-writer-final-rank"+std::to_string(Pstream::myProcNo())+".json");
            if (!writeWriterCertificate(actual,output,
                localFailures?"local-rejected":"local-pass-awaiting-global",localFailures,verifySeconds)) { ++ioFailures; }
        }
        const label globalFailures=returnReduce(localFailures+ioFailures,sumOp<label>());
        if (globalFailures)
        {
            // Preserve the raw PREOPERATOR input, never the altered candidate
            // polyMesh. APP has not called write() and obtains no result.
            const fileName prefix=runTime.path()/("conforming-preoperator-rank"+std::to_string(Pstream::myProcNo())+"-");
            const bool preserved=writeWriterCheckpoint(*m.writerBaseline->checkpoint,prefix);
            Pout<<"Conforming installed writer rejected: local "<<localFailures
                <<", preoperator checkpoint preserved "<<preserved<<endl;
            FatalErrorInFunction<<"coupled postwriter DP/ring/alias/role invariance failed on "
                <<globalFailures<<" face/metadata record(s); no mesh written"<<exit(FatalError);
        }
        Pout<<"Conforming installed postwriter coupled certificate: rank "<<Pstream::myProcNo()
            <<", faces "<<m.writerBaseline->faces.size()<<", failures "<<localFailures
            <<", verify "<<verifySeconds<<" s; earliest installed baseline and real final DP bytes; full checkMesh required"<<endl;
        if(dcCartFullCaptureIO::enabled())
        {
            const fileName output=runTime.path()/("point-star-full-capture-rank"
                +std::to_string(Pstream::myProcNo())+".bin");
            const auto observed=dcCartCompleteCavityFullCapture::capture
                (output.c_str(),fullCapture,m,mesh,bindings);
            Pout<<"Point-star full capture: rank "<<Pstream::myProcNo()
                <<", complete "<<observed.complete<<", reason "<<observed.refusal
                <<", bytes "<<observed.bytes<<", copy/write "<<observed.copySeconds<<'/'
                <<observed.writeSeconds<<" s; actual final cache only, no permission"<<endl;
        }
        m.writerBaseline.reset();
    }
    if(!baselineOnly&&dcCartCompleteCavityReplayObservation::enabled())
    {
        const fileName output=runTime.path()/("complete-prefix-final-observation-rank"+std::to_string(Pstream::myProcNo())+".json");
        const auto observed=dcCartCompleteCavityReplayObservation::captureFinal(true,output.c_str(),mesh,bindings);
        Pout<<"Complete prefix final observation: rank "<<Pstream::myProcNo()
            <<", complete/written "<<observed.complete<<'/'<<observed.written
            <<", reason "<<observed.refusal<<", bytes "<<observed.bytes
            <<", observation "<<observed.seconds<<" s; cached fields only, no quality permission"<<endl;
    }
    // Optional metadata-only export of the effective construction-to-written
    // boundary map, after every installed-writer guard and topology commit.
    if (writtenPatchAddressMap) { *writtenPatchAddressMap = patchOf; }
    return meshPtr;
}

namespace dcCartLegacyJournalObservation
{
thread_local Store state{};
thread_local Store* active=nullptr;
}
namespace Foam
{
void dcCartFlushLegacyJournalObservation() noexcept
{
    if(!dcCartLegacyJournalObservation::state.enabled||dcCartLegacyJournalObservation::state.sealed)return;
    char path[128];
    const int length=std::snprintf(path,sizeof(path),"legacy-journal-observation-rank%d.jsonl",dcCartLegacyJournalObservation::state.rank);
    if(length>0&&std::size_t(length)<sizeof(path))dcCartLegacyJournalObservation::flush(path);
}
}
