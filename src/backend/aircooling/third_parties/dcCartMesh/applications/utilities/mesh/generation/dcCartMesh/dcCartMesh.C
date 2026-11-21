/*---------------------------------------------------------------------------*\
Application
    dcCartMesh

Description
    Cut-cell Cartesian mesh generator for data-centre CFD.

    Builds a graded, axis-aligned Cartesian grid, refines it octree-style
    around the geometry, carves the solid away with a straight stair-step cut,
    and writes an OpenFOAM polyMesh — in ONE pass, in one process, with no
    surface snapping anywhere.

    This is deliberately not snappyHexMesh with the snapping switched off.
    snappyHexMesh discovers its refinement iteratively and rebuilds the whole
    mesh through polyTopoChange after every iteration (14 rebuilds on a typical
    data-hall case, plus a load re-partition between most of them). Here the
    octree is decided first and the mesh is assembled exactly once.

    Reads system/dcCartMeshDict for the background grid and options. Geometry,
    refinementSurfaces and locationsInMesh are read from system/snappyHexMeshDict
    when it exists, so the mesher consumes the dictionaries the pipeline already
    writes and produces the same patch names the boundary conditions bind to.

    Time budget
        Meshing has no time limit by default. An optional wall-clock budget
        can be set with `timeLimitSeconds` in the dict or `-timeLimit <s>` on
        the command line (0 disables). An enabled budget is checked at stage
        boundaries and exceeding it exits non-zero.

\*---------------------------------------------------------------------------*/

#include "argList.H"
#include "Time.H"
#include "IOdictionary.H"
#include "polyMesh.H"
#include "dcCartGrid.H"
#include "dcCartOctree.H"
#include "dcCartSurfaces.H"
#include "dcCartSourceRegions.H"
#include "dcCartMeshBuilder.H"
#include "dcCartCutCells.H"
#include "dcCartPointRepairBudget.H"
#include "dcCartFiniteAdmissionObservation.H"
#include "dcCartPointRepairSourceContract.H"
#include "dcCartRawQualityClusterUnion.H"
#include "dcCartWeightUnionSourceContract.H"
#include "dcCartPostCarveFluidOwnershipPreview.H"
#include "dcCartCompleteCavityAuthority.H"
#include "dcCartPostCarveCanonicalWallFan.H"
#include "dcCartPostFanIntrinsicChainCleanup.H"

#include <array>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <string>
#include <future>
#include <thread>

//- Wall-clock budget for the sliver-merge phase, in seconds (0 = unlimited).
//
// A model whose surfaces sit a fraction of a millimetre apart (rows off their
// column line, a containment baffle a hair short of the face it seals) hands
// mergeSmallCells a sliver field in which its per-cell neighbour search goes
// quadratic: every core pegged for tens of minutes with no output at all,
// which from the UI is indistinguishable from a deadlock. Running the phase on
// a worker thread with a budget turns that into a failure that names the phase
// and the usual cause, instead of a run nobody can tell from a hung server.
// The worker is the only thread touching MPI while the main thread waits, so
// Pstream stays single-threaded; on timeout FatalError MPI_Aborts every rank
// and the abandoned worker dies with the process.
static double mergeBudgetSeconds()
{
    const char* e = std::getenv("DCCART_MERGE_TIMEOUT");
    if (!e || !*e) { return 240; }
    const double v = std::atof(e);
    return v > 0 ? v : 0;
}

//- Memory guard (DCCARTMESH.md "Memory"). The whole mesh is resident at
//  once and every rank duplicates the octree + carve bookkeeping, so the
//  peak RISES with the rank count:
//
//      total ~ FIXED + leaves x SHARED + ranks x (BASE + leaves x PER_RANK)
//
//  fitted on Base604 (16.07M octree leaves: 49.6 / 87.4 GB summed rank
//  peaks at 8 / 16 ranks, 2026-10-19) and Slab (329k leaves: 0.80 / 1.60 /
//  4.56 GB at 1 / 4 / 16 ranks). The runner (stlgen_runner.py
//  DCCART_MEM_*) carries the SAME constants; change both.
static constexpr double kMemFixedBytes = 370.0 * 1024.0 * 1024.0;
static constexpr double kMemSharedBytesPerLeaf = 740.0;
static constexpr double kMemRankBytesPerLeaf = 285.0;
static constexpr double kMemRankBaseBytes = 155.0 * 1024.0 * 1024.0;
static constexpr double kMemSafety = 1.15;
static constexpr double kMemHeadroomBytes = 4.0 * 1024.0 * 1024.0 * 1024.0;

static double memEstimateBytes(const double nLeaves, const double nRanks)
{
    return
        (
            kMemFixedBytes
          + nLeaves*kMemSharedBytesPerLeaf
          + nRanks*(kMemRankBaseBytes + nLeaves*kMemRankBytesPerLeaf)
        )*kMemSafety;
}

//- "Key:   N kB" from /proc/meminfo or /proc/self/status, in bytes; -1 when
//  the key is absent. NOT Foam::memInfo, which reads MemFree — a few GB on
//  any host with a page cache while hundreds of GB are reclaimable.
static double procKeyBytes(const char* file, const char* key)
{
    std::ifstream in(file);
    std::string line;
    const size_t klen = std::strlen(key);
    while (std::getline(in, line))
    {
        if (line.compare(0, klen, key) == 0)
        {
            return std::atof(line.c_str() + klen)*1024.0;
        }
    }
    return -1;
}

//- memory.max - memory.current of the tightest cgroup (v2) above this
//  process; -1 when nothing limits us (every level reads "max").
static double cgroupMemRemainingBytes()
{
    std::ifstream in("/proc/self/cgroup");
    std::string line, path;
    while (std::getline(in, line))
    {
        if (line.compare(0, 3, "0::") == 0) { path = line.substr(3); break; }
    }
    if (path.empty()) { return -1; }
    const std::string root = "/sys/fs/cgroup";
    std::string dir = root + path;
    double remaining = -1;
    while (true)
    {
        std::ifstream mx(dir + "/memory.max");
        std::string v;
        if (mx && std::getline(mx, v) && !v.empty() && v != "max")
        {
            std::ifstream cur(dir + "/memory.current");
            std::string c;
            const double used = (cur && std::getline(cur, c)) ? std::atof(c.c_str()) : 0;
            const double left = std::atof(v.c_str()) - used;
            if (remaining < 0 || left < remaining) { remaining = left; }
        }
        if (dir.size() <= root.size()) { break; }
        const size_t slash = dir.rfind('/');
        if (slash == std::string::npos || slash < root.size()) { break; }
        dir = dir.substr(0, slash);
    }
    return remaining;
}

//- What the mesh may use: DCCART_MEM_AVAILABLE_BYTES (the test seam), else
//  the smaller of MemAvailable and the cgroup remainder; -1 when unknown.
static double memBudgetBytes()
{
    if (const char* e = std::getenv("DCCART_MEM_AVAILABLE_BYTES"))
    {
        if (*e) { return std::atof(e); }
    }
    const double avail = procKeyBytes("/proc/meminfo", "MemAvailable:");
    const double cg = cgroupMemRemainingBytes();
    if (avail < 0) { return cg; }
    if (cg < 0) { return avail; }
    return std::min(avail, cg);
}

static bool memGuardEnabled()
{
    const char* g = std::getenv("DCCART_MEM_GUARD");
    if (!g || !*g) { return true; }
    std::string v(g);
    for (auto& ch : v) { ch = char(std::tolower(static_cast<unsigned char>(ch))); }
    return !(v == "off" || v == "0" || v == "false" || v == "no");
}

//- This process's peak resident set (VmHWM), for the self-calibration line.
static double peakRssBytes()
{
    return procKeyBytes("/proc/self/status", "VmHWM:");
}
#include "clockTime.H"
#include "Pstream.H"
#include "PstreamReduceOps.H"
#include "processorPolyPatch.H"
#include "processorCyclicPolyPatch.H"
#include "Tuple2.H"
#include "OFstream.H"
#include <sstream>
#include <cstdio>
#include <fcntl.h>
#include <unistd.h>
#include <deque>
#include <map>
#include <tuple>
#include <set>
#include <unordered_set>
#include <cstring>
#include <vector>
#include <algorithm>

using namespace Foam;


//- Patch ids for the six sides of the domain box, appended after the STL
//  regions so a geometry region and a domain side never collide.
class dcResolver
:
    public dcCartMeshBuilder::patchResolver
{
    const dcCartSurfaces& surfaces_;
    const dcCartGrid& grid_;
    const label domainOffset_;
    //- per leaf: the carving surfaces near it (proximity walk), found
    //  through (level, key)
    const std::vector<std::unordered_map<uint64_t, label>>* leafId_ = nullptr;
    const std::vector<uint64_t>* nearMask_ = nullptr;

public:

    dcResolver
    (
        const dcCartSurfaces& surfaces,
        const dcCartGrid& grid,
        label domainOffset
    )
    :
        surfaces_(surfaces),
        grid_(grid),
        domainOffset_(domainOffset)
    {}

    void useMasks
    (
        const std::vector<std::unordered_map<uint64_t, label>>& leafId,
        const std::vector<uint64_t>& nearMask
    )
    {
        leafId_ = &leafId;
        nearMask_ = &nearMask;
    }

    virtual label patchId
    (
        label level,
        const labelVector& cellIdx,
        direction cmpt,
        bool positive,
        const point& faceCentre,
        bool domainBoundary
    ) const override
    {
        if (domainBoundary)
        {
            return domainOffset_ + 2 * label(cmpt) + (positive ? 1 : 0);
        }

        // The face is exposed because the neighbour cell was carved away.
        // Whichever surface blocked the walk between the two cell centres is
        // the surface this face lies on — so the carve and the patch naming
        // are answered by one and the same query.
        point other(grid_.cellCentre(level, cellIdx));
        const scalar h =
            (grid_.planeCoord(cmpt, level, cellIdx[cmpt] + 1)
           - grid_.planeCoord(cmpt, level, cellIdx[cmpt]));
        other[cmpt] += (positive ? 1.0 : -1.0) * h;

        // The segment stays inside the cell's box grown by half its diagonal
        // (it reaches one cell size out): only the surfaces near the cell can
        // block it.
        uint64_t mask = ~uint64_t(0);
        if (leafId_ && nearMask_ && level < label(leafId_->size()))
        {
            const auto it = (*leafId_)[level].find(dcCartPack(cellIdx.x(), cellIdx.y(), cellIdx.z()));
            if (it != (*leafId_)[level].end()) { mask = (*nearMask_)[it->second]; }
        }
        const label region = surfaces_.blockingRegionMasked
        (
            grid_.cellCentre(level, cellIdx), other, mask
        );
        if (region >= 0)
        {
            return region;
        }
        // Carved by a surface we could not re-hit (a grazing cut): the
        // nearest carving region is the surface this face stands in for.
        // Only when there is none at all does it fall to the domain side.
        const label near = surfaces_.nearestCarvingRegionProbe
        (
            faceCentre, 2*mag(grid_.cellBox(level, cellIdx).span())
        );
        if (near >= 0) { return near; }
        return domainOffset_ + 2 * label(cmpt) + (positive ? 1 : 0);
    }
};


//- Tags internal faces that lie on a zero-thickness baffle, by asking which
//  faceZone's surface the segment between the two cell centres crosses.
class dcZoneResolver
:
    public dcCartMeshBuilder::faceZoneResolver
{
    const dcCartSurfaces& surfaces_;
    const dcCartGrid& grid_;
    const std::vector<std::unordered_map<uint64_t, label>>* leafId_ = nullptr;
    const std::vector<uint64_t>* nearMask_ = nullptr;

public:

    dcZoneResolver(const dcCartSurfaces& s, const dcCartGrid& g)
    :
        surfaces_(s), grid_(g)
    {}

    void useMasks
    (
        const std::vector<std::unordered_map<uint64_t, label>>& leafId,
        const std::vector<uint64_t>& nearMask
    )
    {
        leafId_ = &leafId;
        nearMask_ = &nearMask;
    }

    virtual label faceZoneId
    (
        label level,
        const labelVector& cellIdx,
        direction cmpt,
        bool positive
    ) const override
    {
        const point pc = grid_.cellCentre(level, cellIdx);
        point other(pc);
        other[cmpt] +=
            (positive ? 1.0 : -1.0)
          * (grid_.planeCoord(cmpt, level, cellIdx[cmpt] + 1)
           - grid_.planeCoord(cmpt, level, cellIdx[cmpt]));
        // (the segment reaches one cell size out of the cell: within its box
        // grown by half its diagonal, where the proximity walk looked)
        uint64_t mask = ~uint64_t(0);
        if (leafId_ && nearMask_ && level < label(leafId_->size()))
        {
            const auto it = (*leafId_)[level].find(dcCartPack(cellIdx.x(), cellIdx.y(), cellIdx.z()));
            if (it != (*leafId_)[level].end()) { mask = (*nearMask_)[it->second]; }
        }
        return surfaces_.crossedFaceZoneMasked(pc, other, mask);
    }
};


//- Debug: comma-separated post-cutBaffles stages to skip, for bisecting which
//  one loses baffle caps (DCCART_SKIP=merge,coalesce,refine,stitch,relabel,
//  dropghost,strip,splitwall). Zero cost when unset.
static bool skipStage(const char* name)
{
    static const std::string list = []() -> std::string
    {
        const char* e = getenv("DCCART_SKIP");
        return e ? std::string(e) : std::string();
    }();
    if (list.empty()) { return false; }
    const std::string n(name);
    std::string::size_type i = 0;
    while (i < list.size())
    {
        std::string::size_type j = list.find(',', i);
        if (j == std::string::npos) { j = list.size(); }
        if (list.substr(i, j - i) == n) { return true; }
        i = j + 1;
    }
    return false;
}


//- Debug: DCCART_DEBUG_BOX="xmin,ymin,zmin,xmax,ymax,zmax" dumps every live
//  cell whose faces touch that box at each tagged stage, to
//  iface_dbg/<tag>.<rank>.txt in the case directory. Cells are identified by
//  their GLOBAL id (level, leaf, piece) and faces by their neighbour's global
//  id, so a serial run and any rank of a parallel run can be diffed line by
//  line to find the stage where one rank's cut of a shared interface cell
//  diverges from the canonical (serial / owner-rank) cut. Zero cost when the
//  variable is unset.
//- Debug: DCCART_PYR_DEBUG=1 runs checkMesh's face-pyramid test (OpenFOAM
//  face and cell centres) on the cut mesh after every stage dbgDumpCells is
//  called at, and prints per rank how many faces fail it, the first dozen by
//  centre and by the global ids of their two cells -- so the stage that
//  first makes a "faces are incorrectly oriented" is one grep away
//  (`grep pyrdbg log`). Zero cost when unset.
static void dbgPyramids
(
    const Foam::dcCartCutCells::cutMesh& m,
    const char* tag
)
{
    static const bool on = (getenv("DCCART_PYR_DEBUG") != nullptr);
    if (!on) { return; }
    using namespace Foam;
    const label nF = label(m.faces.size());
    std::vector<vector> fA(nF, vector::zero);
    std::vector<point> fC(nF, point::zero);
    for (label f = 0; f < nF; ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        const std::vector<label>& poly = m.faces[f];
        const label n = label(poly.size());
        if (n < 3) { continue; }
        point fc = point::zero;
        for (label v : poly) { fc += m.points[v]; }
        fc /= scalar(n);
        vector sumN = vector::zero; scalar sumA = 0; vector sumAc = vector::zero;
        for (label i = 0; i < n; ++i)
        {
            const point& a = m.points[poly[i]];
            const point& b = m.points[poly[(i + 1) % n]];
            const vector c = a + b + fc;
            const vector nn = (b - a) ^ (fc - a);
            const scalar aa = mag(nn);
            sumN += nn; sumA += aa; sumAc += aa * c;
        }
        fC[f] = (sumA > ROOTVSMALL) ? point(sumAc / (3 * sumA)) : fc;
        fA[f] = 0.5 * sumN;
    }
    std::vector<point> est(m.nCells, point::zero);
    std::vector<label> cnt(m.nCells, 0);
    for (label f = 0; f < nF; ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        const label o = m.owner[f], nb = m.neighbour[f];
        if (o >= 0 && o < m.nCells) { est[o] += fC[f]; ++cnt[o]; }
        if (nb >= 0 && nb < m.nCells) { est[nb] += fC[f]; ++cnt[nb]; }
    }
    for (label c = 0; c < m.nCells; ++c) { if (cnt[c]) { est[c] /= scalar(cnt[c]); } }
    std::vector<point> cc(m.nCells, point::zero);
    std::vector<scalar> cv(m.nCells, 0);
    for (label f = 0; f < nF; ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        const label o = m.owner[f], nb = m.neighbour[f];
        if (o >= 0 && o < m.nCells)
        {
            const scalar p3 = fA[f] & (fC[f] - est[o]);
            cc[o] += p3 * (0.75 * fC[f] + 0.25 * est[o]); cv[o] += p3;
        }
        if (nb >= 0 && nb < m.nCells)
        {
            const scalar p3 = fA[f] & (est[nb] - fC[f]);
            cc[nb] += p3 * (0.75 * fC[f] + 0.25 * est[nb]); cv[nb] += p3;
        }
    }
    for (label c = 0; c < m.nCells; ++c)
    {
        cc[c] = (mag(cv[c]) > VSMALL) ? point(cc[c] / cv[c]) : est[c];
    }
    auto gid = [&](label c) -> std::string
    {
        return std::to_string(m.cellLevel[c]) + ":" + std::to_string(m.cellLeaf[c])
             + ":" + std::to_string(m.cellPiece[c]);
    };
    label nBad = 0;
    for (label f = 0; f < nF; ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        const label o = m.owner[f], nb = m.neighbour[f];
        if (o < 0 || o >= m.nCells || !m.cellAlive[o]) { continue; }
        const scalar po = fA[f] & (fC[f] - cc[o]);
        scalar pn = 1;
        if (nb >= 0 && nb < m.nCells && m.cellAlive[nb]) { pn = fA[f] & (cc[nb] - fC[f]); }
        if (po > 0 && pn > 0) { continue; }
        ++nBad;
        if (nBad <= 12)
        {
            Pout<< "pyrdbg " << tag << ": face " << f << " C " << fC[f]
                << " |A| " << mag(fA[f]) << " own " << gid(o) << " pyr " << po
                << " nbr " << (nb >= 0 ? gid(nb) : std::string("-")) << " pyr " << pn
                << " patch " << m.patch[f] << " zone " << m.zone[f]
                << " nPts " << label(m.faces[f].size()) << endl;
        }
    }
    Pout<< "pyrdbg " << tag << ": " << nBad << " face(s) with a bad pyramid" << endl;
    // checkMesh's interpolation weight: the shorter of the two cell-centre
    // distances to the face along its normal, over their sum
    label nLow = 0;
    for (label f = 0; f < nF; ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        const label o = m.owner[f], nb = m.neighbour[f];
        if (o < 0 || nb < 0 || o >= m.nCells || nb >= m.nCells) { continue; }
        if (!m.cellAlive[o] || !m.cellAlive[nb]) { continue; }
        const scalar a = mag(fA[f]);
        if (a < VSMALL) { continue; }
        const vector n = fA[f] / a;
        const scalar dOwn = mag(n & (fC[f] - cc[o]));
        const scalar dNei = mag(n & (cc[nb] - fC[f]));
        const scalar w = (dOwn + dNei > VSMALL) ? min(dOwn, dNei) / (dOwn + dNei) : 0.5;
        if (w >= 0.05) { continue; }
        ++nLow;
        if (nLow <= 12)
        {
            Pout<< "wgtdbg " << tag << ": face " << f << " C " << fC[f] << " w " << w
                << " own " << gid(o) << " nbr " << gid(nb) << " zone " << m.zone[f] << endl;
        }
    }
    Pout<< "wgtdbg " << tag << ": " << nLow << " face(s) with interpolation weight < 0.05" << endl;
}


static void dbgDumpCells
(
    const Foam::dcCartCutCells::cutMesh& m,
    const char* tag
)
{
    dbgPyramids(m, tag);
    // Stage-only cell snapshots avoid the per-shell global fingerprint
    // scans used by DCCART_DEBUG_BOX when locating a late topology change.
    const char* e = getenv("DCCART_CELL_DEBUG_BOX");
    if (!e) { e = getenv("DCCART_DEBUG_BOX"); }
    if (!e) { return; }
    double b[6];
    if (sscanf(e, "%lf,%lf,%lf,%lf,%lf,%lf", &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) != 6)
    {
        return;
    }
    const Foam::boundBox dbg
    (
        Foam::point(b[0], b[1], b[2]),
        Foam::point(b[3], b[4], b[5])
    );
    const int rank = Foam::Pstream::parRun() ? Foam::Pstream::myProcNo() : 0;

    // cell -> faces
    std::vector<std::vector<Foam::label>> cf(m.nCells);
    for (Foam::label f = 0; f < Foam::label(m.faces.size()); ++f)
    {
        if (!m.faceAlive[f]) { continue; }
        if (m.owner[f] >= 0 && m.owner[f] < m.nCells) { cf[m.owner[f]].push_back(f); }
        if (m.neighbour[f] >= 0 && m.neighbour[f] < m.nCells) { cf[m.neighbour[f]].push_back(f); }
    }
    auto gid = [&](Foam::label c) -> std::string
    {
        return std::to_string(m.cellLevel[c]) + ":"
             + std::to_string(m.cellLeaf[c]) + ":"
             + std::to_string(m.cellPiece[c]);
    };
    // collect matching cells, order by gid string so ranks align
    std::vector<std::pair<std::string, Foam::label>> picked;
    for (Foam::label c = 0; c < m.nCells; ++c)
    {
        if (!m.cellAlive[c]) { continue; }
        bool hit = false;
        for (const Foam::label f : cf[c])
        {
            for (const Foam::label p : m.faces[f])
            {
                if (dbg.contains(m.points[p])) { hit = true; break; }
            }
            if (hit) { break; }
        }
        if (hit) { picked.emplace_back(gid(c), c); }
    }
    std::sort(picked.begin(), picked.end());

    Foam::mkDir("iface_dbg");
    Foam::OFstream os
    (
        Foam::fileName("iface_dbg") / (Foam::word(tag) + "." + Foam::name(rank) + ".txt")
    );
    for (const auto& pc : picked)
    {
        const Foam::label c = pc.second;
        os << "cell " << pc.first
           << " rank=" << m.cellRank[c]
           << " orig=" << (m.cellOrigAlive[c] ? 1 : 0) << nl;
        // faces sorted by their point list rendered as text (rank-stable)
        std::vector<std::string> flines;
        for (const Foam::label f : cf[c])
        {
            std::vector<Foam::string> pts;
            for (const Foam::label p : m.faces[f])
            {
                const Foam::point& x = m.points[p];
                char buf[96];
                snprintf(buf, sizeof(buf), "(%.17g %.17g %.17g)", x.x(), x.y(), x.z());
                pts.emplace_back(buf);
            }
            std::sort(pts.begin(), pts.end());
            std::string line("  face");
            for (const auto& s : pts) { line += " "; line += s; }
            line += " | own=" + gid(m.owner[f]);
            line += " nbr=" + (m.neighbour[f] >= 0 ? gid(m.neighbour[f]) : std::string("-"));
            line += " patch=" + std::to_string(m.patch[f]);
            line += " zone=" + std::to_string(m.zone[f]);
            flines.push_back(line);
        }
        std::sort(flines.begin(), flines.end());
        for (const auto& l : flines) { os << l << nl; }
    }
}

namespace Foam { void dcCartFlushLegacyJournalObservation() noexcept; }

int main(int argc, char *argv[])
{
    argList::addNote
    (
        "Cut-cell Cartesian mesh generator (dcdtuni). Reads"
        " system/dcCartMeshDict and writes constant/polyMesh."
    );
    argList::addBoolOption
    (
        "overwrite",
        "Write the mesh into constant/ rather than a new time directory"
    );
    argList::addOption
    (
        "timeLimit",
        "seconds",
        "Wall-clock budget for the whole run (default 0: disabled)."
        " Overrides timeLimitSeconds in dcCartMeshDict."
    );

    #include "setRootCase.H"
    #include "createTime.H"

    clockTime timer;

    IOdictionary meshDict
    (
        IOobject
        (
            "dcCartMeshDict",
            runTime.system(),
            runTime,
            IOobject::MUST_READ,
            IOobject::NO_WRITE
        )
    );

    // ------------------------------------------------------------------
    // Background grid
    // ------------------------------------------------------------------
    dcCartGrid grid(meshDict.subDict("background"));
    Info<< "Grid: " << grid.info().c_str() << nl;

    const label nBuffer = meshDict.getOrDefault<label>("nCellsBetweenLevels", 1);
    // rings of same-level cells kept around every surface-refined cell
    const label surfaceBuffer = meshDict.getOrDefault<label>("surfaceBufferCells", 1);

    // Wall-clock budget. Checked at every stage boundary — the stages are the
    // only places where abandoning the run leaves nothing half-written.
    scalar timeLimit = meshDict.getOrDefault<scalar>("timeLimitSeconds", 0);
    args.readIfPresent("timeLimit", timeLimit);
    auto budgetCheck = [&](const char* stage)
    {
        if (timeLimit > 0 && timer.elapsedTime() > timeLimit)
        {
            FatalErrorInFunction
                << "dcCartMesh exceeded its " << timeLimit << " s time budget"
                << " (" << timer.elapsedTime() << " s elapsed) after '" << stage
                << "'. Aborting rather than running on: fix the mesher or raise"
                << " timeLimitSeconds in dcCartMeshDict." << nl
                << exit(FatalError);
        }
    };
    if (timeLimit > 0)
    {
        Info<< "Time budget: " << timeLimit << " s" << nl;
    }

    // ------------------------------------------------------------------
    // Geometry. Read from snappyHexMeshDict when present so the mesher
    // consumes the dictionaries the pipeline already writes.
    // ------------------------------------------------------------------
    autoPtr<IOdictionary> snappyDict;
    const dictionary* geometryDict = meshDict.findDict("geometry");
    const dictionary* refineDict = meshDict.findDict("refinementSurfaces");
    const dictionary* regionsDict = meshDict.findDict("refinementRegions");
    const dictionary* seedSource = &meshDict;
    const dictionary* identitySource = &meshDict;

    if (!geometryDict)
    {
        snappyDict.reset
        (
            new IOdictionary
            (
                IOobject
                (
                    "snappyHexMeshDict",
                    runTime.system(),
                    runTime,
                    IOobject::MUST_READ,
                    IOobject::NO_WRITE
                )
            )
        );
        geometryDict = &snappyDict->subDict("geometry");
        identitySource = &snappyDict();
        const dictionary& cmc = snappyDict->subDict("castellatedMeshControls");
        refineDict = &cmc.subDict("refinementSurfaces");
        regionsDict = cmc.findDict("refinementRegions");
        seedSource = &cmc;
        Info<< "Geometry read from snappyHexMeshDict" << nl;
    }

    // coordSnapFraction: the coordinate canonicalisation scale, as a fraction
    // of the finest cell (FDS SNAP_DIST_FACTOR = 1e-4*dx, geom.f90:5348).
    // 0 keeps the exported coordinates bit-for-bit.
    // Measured, per case, through the frontend export (the authoritative path):
    //   coordSnap 1e-4 / gridSnap 0.01 -> Tri_angle Mesh OK, Slab Mesh OK,
    //                                     Shinaide Mesh OK, Base604 22 faces
    //   coordSnap 0.05 / gridSnap 0.05 -> Tri_angle FAILS (3 faces, max 4.75,
    //                                     aspect 210 -> 379), Base604 no better
    //                                     (23 faces, max unchanged)
    // so the tight export-quantisation scale stays the default. The wider
    // 0.05 scale does help when the geometry is read from an already-exported
    // triSurface (Base604 22 -> 9 faces, max 21.4 -> 7.57, and 20 coordinates
    // is where the clustering saturates -- 0.10 is byte-identical), which is
    // what DCCART_COORDSNAP_FRACTION / DCCART_GRIDSNAP_FRACTION and the two
    // dictionary keys are for.
    const char* coordSnapEnv = getenv("DCCART_COORDSNAP_FRACTION");
    const scalar coordSnapFraction = coordSnapEnv
        ? scalar(atof(coordSnapEnv))
        : meshDict.getOrDefault<scalar>("coordSnapFraction", 1e-4);
    // gridSnapFraction: how close a surface may sit to a grid plane before it
    // is rewritten onto it (0 disables). 0.01 of the finest cell; 0.05 breaks
    // Tri_angle (see coordSnapFraction above).
    // DCCART_GRIDSNAP_FRACTION overrides the dictionary for A/B runs, the same
    // way DCCART_NOCOORDSNAP / DCCART_NOSEAMSPLIT do for their stages.
    const char* gridSnapEnv = getenv("DCCART_GRIDSNAP_FRACTION");
    const scalar gridSnapFraction = gridSnapEnv
        ? scalar(atof(gridSnapEnv))
        : meshDict.getOrDefault<scalar>("gridSnapFraction", 0.01);
    // weldTolerance: the DAML thin-wall wedge work package (route 1 of
    // docs/superpowers/specs/2026-09-18-daml-thin-wedge-weld-design.md).
    // Surfaces that NEARLY touch the leaf grid carve hairline films and
    // micro-wedges whose y_P sinks below the 5.0e-4 wall-function floor
    // (measured on DAML-Testbed: 0.2-1.2 mm plane/grid offsets give films at
    // y_P = t/2, and tilted planes 2e-5..3e-4 off a grid line give the
    // y_P 2.5e-5 duct-baffle row and the 1.3e-4 spiral-riser tetrahedra the
    // merge machinery cannot reach). The weld rewrites those near-coincidences
    // into exact ones before the carver sees them -- see
    // dcCartSurfaces::weldNearCoincident. The measured pathology band tops out
    // at 1.44 mm on DAML, and a film needs >= 1.0 mm to keep y_P >= 5.0e-4,
    // so the default is 1.2e-3 m -- NOT the spec's 0.2-0.5 x h_leaf candidate
    // (5-12.5 mm here), which would move real geometry (server positions) by
    // centimetres to cure a millimetre-scale defect.
    // OFF unless asked: it rewrites surface coordinates, which changes the
    // mesh of every case whose surfaces graze the grid, and the pre-weld
    // cases are validated against their recorded coverage numbers.
    //   DCCART_WELD_TOL=<m>  enable with that tolerance
    //   DCCART_WELD=1        enable with the dict value or 1.2e-3 m
    //   DCCART_WELD=0        force off
    //   weldTolerance <m>;   dcCartMeshDict key (runner wiring, future)
    const char* weldEnv = getenv("DCCART_WELD_TOL");
    scalar weldTolerance = weldEnv
        ? scalar(atof(weldEnv))
        : meshDict.getOrDefault<scalar>("weldTolerance", 0);
    {
        const char* w = getenv("DCCART_WELD");
        if (w)
        {
            if (w[0] == '0' && !w[1]) { weldTolerance = 0; }
            else if (weldTolerance <= 0) { weldTolerance = 1.2e-3; }
        }
    }
    const label weldRounds = meshDict.getOrDefault<label>("weldRounds", 3);
    // First-layer floor (the 6Sigma snap-to-grid guarantee at the cut-cell
    // scale, see dcCartSurfaces::weldNearCoincident): per coordinate class
    // the weld band widens to firstLayerMinFraction x that class's own level
    // spacing, capped at firstLayerMax metres, so no axis-aligned surface
    // carves a slab thinner than that. 0 = off (classic weld only). A gap
    // between two solids wider than firstLayerSealGap (< 0: weldTolerance)
    // is never sealed by it.
    //   DCCART_FIRSTLAYER_FRACTION=<f>  overrides the dict key
    //   DCCART_FIRSTLAYER_MAX=<m>       overrides the cap
    const char* flFracEnv = getenv("DCCART_FIRSTLAYER_FRACTION");
    const scalar firstLayerMinFraction = flFracEnv
        ? scalar(atof(flFracEnv))
        : meshDict.getOrDefault<scalar>("firstLayerMinFraction", 0);
    const char* flMaxEnv = getenv("DCCART_FIRSTLAYER_MAX");
    const scalar firstLayerMax = flMaxEnv
        ? scalar(atof(flMaxEnv))
        : meshDict.getOrDefault<scalar>("firstLayerMax", 0.005);
    const scalar firstLayerSealGap =
        meshDict.getOrDefault<scalar>("firstLayerSealGap", -1);
    if (weldTolerance > 0)
    {
        Info<< "Weld: near-coincidence weld ON, tolerance " << weldTolerance
            << " m (DCCART_WELD_TOL / DCCART_WELD / weldTolerance)" << nl;
        if (firstLayerMinFraction > 0)
        {
            Info<< "Weld: first-layer floor ON, " << firstLayerMinFraction
                << " x the class level spacing, at most " << firstLayerMax
                << " m (firstLayerMinFraction / firstLayerMax / "
                << "firstLayerSealGap)" << nl;
        }
    }
    else if (firstLayerMinFraction > 0)
    {
        Info<< "Weld: first-layer floor requested but the weld is OFF "
            << "(weldTolerance 0): nothing moves" << nl;
    }
    dcCartSurfaces surfaces
    (
        runTime,
        *geometryDict,
        refineDict ? *refineDict : dictionary::null,
        regionsDict ? *regionsDict : dictionary::null,
        (getenv("DCCART_NOCOORDSNAP") ? 0 : grid.medianSpacing()),
        coordSnapFraction,
        (getenv("DCCART_NOGRIDSNAP") ? nullptr : &grid),
        gridSnapFraction,
        weldTolerance,
        weldRounds,
        firstLayerMinFraction,
        firstLayerMax,
        firstLayerSealGap
    );
    if (identitySource->found("dcdtuniSourceSurfaces", keyType::LITERAL))
    {
        const label identities = surfaces.readSourceSurfaceIdentities
        (
            identitySource->subDict("dcdtuniSourceSurfaces")
        );
        Info<< "Source surface identities: " << identities << nl;
        const char* finalizeServers=std::getenv("DCCART_SERVER_COMPONENT_FINALIZATION");
        if(finalizeServers && std::strcmp(finalizeServers,"1")==0)
        {
            budgetCheck("before prepared server component finalization");
            const label groups=surfaces.finalizePreparedServerComponents();
            Info<< "Prepared server component finalization: " << groups << " groups" << nl;
            budgetCheck("prepared server component finalization");
        }
    }

    Info<< "Surfaces: " << surfaces.summary().c_str()
        << "  (" << timer.timeIncrement() << " s)" << nl;

    const label maxLevel = meshDict.getOrDefault<label>
    (
        "maxLevel", max(surfaces.maxLevel(), label(1))
    );

    // snappyHexMesh's cell ceiling, honoured the same way snappy honours it.
    // refinementParameters reads `maxGlobalCells` and
    // meshRefinement::refineCandidates (meshRefinementRefine.C:2247) then
    // refuses to mark a single cell once the running mesh has reached it:
    //     if (totNCells >= maxGlobalCells)
    //         Info<< "No cells marked for refinement since reached limit "
    // snappyHexMeshDriver calls that before EVERY refinement pass, so the cap
    // is a stop-refining bound rather than a hard ceiling -- the pass that
    // crosses it is the one already built. dcCart never read the key at all,
    // so a case configured for two million cells meshed 3,550,304 and every
    // later stage paid for the 78% overshoot.
    //
    // The octree's leaf count is dcCart's mesh-in-progress and the quantity to
    // compare: carving only ever removes leaves, so it is an upper bound on
    // the final cell count and the ceiling therefore bites slightly earlier
    // than snappy's would on the same number. 0 (or absent) means no ceiling,
    // which is what every run before this did.
    const label maxGlobalCells = meshDict.getOrDefault<label>("maxGlobalCells", 0);

    // Private default-OFF refinement experiment. No surface levels, source
    // coordinates, material queries, or downstream acceptance gates change.
    const char* curvatureOption = std::getenv("DCCART_PRECARVE_CURVATURE_REFINE");
    if (curvatureOption && std::strcmp(curvatureOption, "0") != 0
        && std::strcmp(curvatureOption, "1") != 0)
    {
        FatalErrorInFunction << "DCCART_PRECARVE_CURVATURE_REFINE must be 0 or 1"
            << exit(FatalError);
    }
    const bool curvatureRelief = curvatureOption && std::strcmp(curvatureOption, "1") == 0;
    label curvatureRanks = curvatureRelief ? 1 : 0;
    reduce(curvatureRanks, sumOp<label>());
    const label refinementRanks = Pstream::parRun() ? Pstream::nProcs() : 1;
    if (curvatureRanks != 0 && curvatureRanks != refinementRanks)
    {
        FatalErrorInFunction << "Pre-carve curvature refinement option differs across ranks"
            << exit(FatalError);
    }
    std::vector<unsigned char> curvatureSurfaces;
    const std::function<void()> curvatureCheckpoint = [&]()
    {
        budgetCheck("pre-carve curvature refinement");
    };
    if (curvatureRelief)
    {
        curvatureSurfaces = surfaces.curvatureReliefSurfaces(curvatureCheckpoint);
        Info<< "Pre-carve curvature refinement: geometry hint only, one extra level"
            << " above declared surface max, capped by global max " << maxLevel << nl;
    }

    dcCartOctree tree(grid, maxLevel);
    tree.seed(0);
    Info<< "Seeded: " << tree.info().c_str()
        << "  (" << timer.timeIncrement() << " s)" << nl;
    budgetCheck("seed");

    // ------------------------------------------------------------------
    // Refinement: surface-driven, plus any explicit boxes. Levels are decided
    // top-down in one sweep per level — no iterate-and-rebuild.
    // ------------------------------------------------------------------
    List<boundBox> boxes;
    labelList boxLevels;
    if (const dictionary* bd = meshDict.findDict("refinementBoxes"))
    {
        DynamicList<boundBox> bb;
        DynamicList<label> lv;
        for (const entry& e : *bd)
        {
            if (!e.isDict()) { continue; }
            bb.append(boundBox(e.dict().get<point>("min"), e.dict().get<point>("max")));
            lv.append(e.dict().get<label>("level"));
        }
        boxes = List<boundBox>(std::move(bb));
        boxLevels = labelList(std::move(lv));
    }

    // Candidate triangles: a refined cell hands its children the triangles
    // that overlap it (every surface its decision tested; what it was handed
    // for the rest). A child's box lies inside its parent's, so it tests a
    // handful of triangles instead of walking every surface's octree. Cells
    // refined only as a buffer ring hand nothing: their children start from
    // the octrees again.
    const std::vector<uint64_t> allCand = surfaces.refinementCandidates();
    std::vector<uint64_t> parentPool, childPool, keep;
    std::unordered_map<uint64_t, std::pair<size_t, size_t>> parentSpan, childSpan;
    label capLevel = tree.maxLevel();
    for (label L = 0; L < tree.maxLevel(); ++L)
    {
        // The snappy check, in the snappy place: before this pass marks any
        // candidate. meshRefinement::refineCandidates returns an empty list
        // once totNCells >= maxGlobalCells, and snappyHexMeshDriver calls it
        // before every pass, so the ceiling is a stop-refining bound, not a
        // hard cap on the delivered mesh -- the pass that crossed it is the
        // one already built. Breaking out is what the driver does too.
        //
        // dcCart compares its octree LEAF count, because that is its mesh in
        // progress; carving only ever removes leaves afterwards, so this bites
        // slightly earlier than snappy's would on the same final size.
        if (maxGlobalCells > 0 && tree.nLeaves() >= maxGlobalCells)
        {
            capLevel = L;
            Info<< "  maxGlobalCells " << maxGlobalCells << " reached at "
                << tree.nLeaves() << " leaves: no cells marked for refinement "
                << "at level " << L << " or finer (as snappyHexMesh does)"
                << nl;
            break;
        }
        const auto& leaves = tree.leavesAt(L);
        if (leaves.empty()) { continue; }
        childSpan.clear();
        childPool.clear();

        DynamicList<labelVector> hits(label(leaves.size()) / 4 + 16);
        // Leaves the surface itself refines: their face neighbours at this
        // level are refined with them (below), so that every cell the
        // surface will cut has a neighbour of its OWN level behind it. A
        // sliver cut off a fine cell can only be merged into a host whose
        // face it covers; against a coarser neighbour the union is L-shaped
        // and the sliver stays, a 1e-3 volume ratio the solver cannot take.
        std::unordered_set<uint64_t> surfaceHits;
        label curvatureHits = 0;
        for (uint64_t key : leaves)
        {
            const labelVector idx = dcCartUnpack(key);
            const boundBox bb = grid.cellBox(L, idx);

            // Only "does anything want this leaf finer than L?" matters, so
            // every query stops at its first yes.
            const uint64_t* cb = allCand.data();
            const uint64_t* ce = cb + allCand.size();
            if (L > 0)
            {
                const auto pit = parentSpan.find(dcCartPack(idx.x() / 2, idx.y() / 2, idx.z() / 2));
                if (pit != parentSpan.end())
                {
                    cb = parentPool.data() + pit->second.first;
                    ce = cb + pit->second.second;
                }
            }
            bool bySurface = surfaces.levelInBoxAboveCand(bb, L, cb, ce, keep);
            if (!bySurface && surfaces.nShells())
            {
                bySurface = surfaces.shellLevelAbove(grid.cellCentre(L, idx), L);
            }
            bool byCurvature = false;
            if (curvatureRelief && !bySurface)
            {
                byCurvature = surfaces.curvatureReliefInBoxAboveCand
                (
                    bb, L, tree.maxLevel(), curvatureSurfaces,
                    cb, ce, keep, curvatureCheckpoint
                );
            }
            bool refineIt = bySurface || byCurvature;
            forAll(boxes, b)
            {
                if (!refineIt && boxLevels[b] > L && boxes[b].overlaps(bb)) { refineIt = true; }
            }
            if (refineIt)
            {
                childSpan[key] = std::make_pair(childPool.size(), keep.size());
                childPool.insert(childPool.end(), keep.begin(), keep.end());
                hits.append(idx);
                if ((bySurface || byCurvature) && surfaceBuffer > 0) { surfaceHits.insert(key); }
                if (byCurvature) { ++curvatureHits; }
            }
        }
        if (surfaceBuffer > 0 && !surfaceHits.empty())
        {
            std::unordered_set<uint64_t> added;
            std::vector<uint64_t> frontier(surfaceHits.begin(), surfaceHits.end());
            for (label ring = 0; ring < surfaceBuffer; ++ring)
            {
                std::vector<uint64_t> next;
                for (uint64_t key : frontier)
                {
                    const labelVector c = dcCartUnpack(key);
                    for (direction cmpt = 0; cmpt < 3; ++cmpt)
                    {
                        for (label sgn = -1; sgn <= 1; sgn += 2)
                        {
                            labelVector n(c);
                            n[cmpt] += sgn;
                            if (!tree.isLeaf(L, n)) { continue; }
                            const uint64_t nk = dcCartPack(n.x(), n.y(), n.z());
                            if (surfaceHits.count(nk) || added.count(nk)) { continue; }
                            added.insert(nk);
                            next.push_back(nk);
                            hits.append(n);
                            // its children start from the list it was handed
                            // (a superset of what overlaps it)
                            if (L > 0 && !childSpan.count(nk))
                            {
                                const auto pit = parentSpan.find(dcCartPack(n.x() / 2, n.y() / 2, n.z() / 2));
                                if (pit != parentSpan.end())
                                {
                                    const size_t first = pit->second.first, len = pit->second.second;
                                    childSpan[nk] = std::make_pair(childPool.size(), len);
                                    childPool.insert
                                    (
                                        childPool.end(),
                                        parentPool.begin() + first,
                                        parentPool.begin() + first + len
                                    );
                                }
                            }
                        }
                    }
                }
                frontier.swap(next);
            }
        }
        parentPool.swap(childPool);
        parentSpan.swap(childSpan);
        if (curvatureRelief)
        {
            curvatureCheckpoint();
            Info<< "  curvature relief level " << L << ": marked " << curvatureHits
                << " parents; all marks including existing buffer/boxes " << hits.size() << nl;
        }
        if (hits.empty()) { continue; }
        tree.refine(L, hits);
        Info<< "  level " << L << " -> " << L + 1 << ": refined "
            << hits.size() << " cells" << nl;
    }

    Info<< "Refined: " << tree.info().c_str();
    if (capLevel < tree.maxLevel())
    {
        Info<< " (capped at level " << capLevel << " by maxGlobalCells "
            << maxGlobalCells << ")";
    }
    Info<< "  (" << timer.timeIncrement() << " s)" << nl;
    budgetCheck("refine");

    // A cell a surface cuts must not sit against a coarser one. The buffer
    // above holds that for the cells a surface refines itself; it does not
    // for the finer cells other surfaces refined around it. On
    // Shinaide_liquid the raised floor (level 2) cut the 31 mm cells round a
    // rack frame, and a 5 mm strip of floor between two sealed pipe passages
    // was left against a 62 mm cell below (volume ratio 0.006); a 14 mm cell
    // before an ACU front met a 125 mm one. Such neighbours are refined to the
    // cut cell's level until none is left (balance follows). Only a cell
    // cut by axis-aligned facets alone counts, and a neighbour a tilted facet
    // runs through is left as it is: round DAML's tapered duct both cut the
    // refined cells into new slivers (volume-ratio failures 367 -> 546).
    // cutNeighbourRule: all (default) | baffles | none.
    {
        label nSheetRefined = 0;
        const word cutRule = meshDict.getOrDefault<word>("cutNeighbourRule", "all");
        if (cutRule != "all" && cutRule != "baffles" && cutRule != "none")
        {
            FatalErrorInFunction << "cutNeighbourRule must be all, baffles or none, not "
                << cutRule << exit(FatalError);
        }
        const bool allSurfacesRule = (cutRule == "all");
        if (cutRule != "none")
        {
            // Round 0 scans every leaf; a later round only the leaves that
            // qualified in the one before and the children it made.
            // Refinement only makes neighbours finer and the overlap test is
            // a pure function of the box, so a leaf that existed and did not
            // qualify cannot start to — the rounds refine the same cells.
            std::vector<std::pair<label, uint64_t>> candidates, qualified;
            bool fullScan = true;
            for (label round = 0; round <= tree.maxLevel(); ++round)
            {
                std::map<label, std::set<uint64_t>> coarse;
                if (fullScan)
                {
                    candidates.clear();
                    for (label L = 1; L <= tree.deepestLevel(); ++L)
                    {
                        for (const uint64_t key : tree.leavesAt(L))
                        {
                            candidates.emplace_back(L, key);
                        }
                    }
                }
                qualified.clear();
                {
                    for (const auto& lk : candidates)
                    {
                        const label L = lk.first;
                        const uint64_t key = lk.second;
                        if (!fullScan && !tree.leavesAt(L).count(key)) { continue; }
                        const labelVector idx = dcCartUnpack(key);
                        bool anyCoarser = false;
                        label nl[6]; labelVector ni[6]; label k = 0;
                        for (direction cmpt = 0; cmpt < 3; ++cmpt)
                        {
                            for (const bool pos : {false, true})
                            {
                                label lvl = -1; labelVector nIdx;
                                if (tree.neighbour(L, idx, cmpt, pos, lvl, nIdx) == dcCartOctree::COARSER)
                                {
                                    nl[k] = lvl; ni[k] = nIdx; ++k; anyCoarser = true;
                                }
                            }
                        }
                        if (!anyCoarser) { continue; }
                        if (!surfaces.surfaceOverlaps(grid.cellBox(L, idx), !allSurfacesRule, true)) { continue; }
                        qualified.emplace_back(L, key);
                        for (label j = 0; j < k; ++j)
                        {
                            if (surfaces.tiltedOverlaps(grid.cellBox(nl[j], ni[j]))) { continue; }
                            coarse[nl[j]].insert(dcCartPack(ni[j].x(), ni[j].y(), ni[j].z()));
                        }
                    }
                }
                if (coarse.empty()) { break; }
                candidates.swap(qualified);
                for (const auto& kv : coarse)
                {
                    List<uint64_t> keys(label(kv.second.size()));
                    label i = 0;
                    for (const uint64_t key : kv.second)
                    {
                        keys[i++] = key;
                        // only a key that is still a leaf is refined, and
                        // its children are the new leaves
                        if (!tree.leavesAt(kv.first).count(key)) { continue; }
                        const labelVector c = dcCartUnpack(key);
                        for (label d = 0; d < 8; ++d)
                        {
                            candidates.emplace_back
                            (
                                kv.first + 1,
                                dcCartPack
                                (
                                    2*c.x() + (d & 1), 2*c.y() + ((d >> 1) & 1),
                                    2*c.z() + ((d >> 2) & 1)
                                )
                            );
                        }
                    }
                    nSheetRefined += tree.refineKeys(kv.first, keys);
                }
                fullScan = false;
            }
        }
        if (nSheetRefined)
        {
            Info<< "Refined " << nSheetRefined << " coarser neighbours of cut cells"
                << " (cutNeighbourRule " << cutRule << ")" << nl;
        }
    }

    const label nBalanced = tree.balance(nBuffer);
    Info<< "Balanced (nCellsBetweenLevels " << nBuffer << "): refined "
        << nBalanced << " cells -> " << tree.info().c_str()
        << "  (" << timer.timeIncrement() << " s)" << nl;
    budgetCheck("balance");

    // ------------------------------------------------------------------
    // Memory guard: the exact leaf count is known here and nothing has been
    // written yet, so refusing costs nothing. The master reads MemAvailable
    // (never MemFree) and broadcasts, so every rank takes the same decision;
    // in parallel the FatalError MPI_Aborts the whole job.
    // ------------------------------------------------------------------
    {
        const label nRanksMem = Pstream::parRun() ? Pstream::nProcs() : 1;
        const label nLeavesMem = tree.nLeaves();
        const double need = memEstimateBytes(nLeavesMem, nRanksMem);
        scalar avail = Pstream::master() ? memBudgetBytes() : -1;
        Pstream::broadcast(avail);
        const double gb = 1e9;
        if (!memGuardEnabled())
        {
            Info<< "Memory guard: off (DCCART_MEM_GUARD); estimate " << need/gb
                << " GB for " << nLeavesMem << " leaves on " << nRanksMem
                << " rank(s) unchecked" << nl;
        }
        else if (avail < 0)
        {
            Info<< "Memory guard: MemAvailable unreadable; estimate " << need/gb
                << " GB for " << nLeavesMem << " leaves on " << nRanksMem
                << " rank(s) unchecked" << nl;
        }
        else
        {
            Info<< "Memory guard: " << nLeavesMem << " leaves on " << nRanksMem
                << " rank(s): estimate " << need/gb << " GB (+ "
                << kMemHeadroomBytes/gb << " GB headroom), " << avail/gb
                << " GB available" << nl;
            if (need + kMemHeadroomBytes > avail)
            {
                const double perLeaf =
                    (kMemSharedBytesPerLeaf + nRanksMem*kMemRankBytesPerLeaf)*kMemSafety;
                const double fitD =
                    (avail - kMemHeadroomBytes
                   - (kMemFixedBytes + nRanksMem*kMemRankBaseBytes)*kMemSafety)/perLeaf;
                const label fit = fitD > 0 ? label(fitD/100000)*100000 : 0;
                FatalErrorInFunction
                    << "dcCartMesh memory guard: " << nLeavesMem
                    << " octree leaves on " << nRanksMem << " rank(s) would need about "
                    << need/gb << " GB (+ " << kMemHeadroomBytes/gb
                    << " GB headroom) but only " << avail/gb
                    << " GB is available on this host right now. Lower maxGlobalCells (at most "
                    << fit << " leaves fit on " << nRanksMem
                    << " rank(s)) or the refinement levels, or use FEWER ranks: every rank "
                    << "duplicates the octree and carve, so more ranks need MORE memory. "
                    << "DCCART_MEM_GUARD=off runs anyway."
                    << exit(FatalError);
            }
        }
    }

    // ------------------------------------------------------------------
    // Carve: flood fill from locationsInMesh. A face is passable when the
    // segment between the two cell centres crosses no carving surface. What
    // the fill cannot reach is solid, and the cut is a straight stair-step
    // because cells are never moved onto the surface.
    // ------------------------------------------------------------------
    labelList levels;
    List<labelVector> indices;
    tree.flatten(levels, indices);
    const label nLeaves = levels.size();

    // Cells far from every surface can never have a blocked face, so the
    // expensive segment query only runs near the geometry — a few percent of
    // the faces rather than all of them.
    // leaf lookup by (level, key)
    std::vector<std::unordered_map<uint64_t, label>> leafId(tree.maxLevel() + 2);
    for (label c = 0; c < nLeaves; ++c)
    {
        const labelVector& v = indices[c];
        leafId[levels[c]][dcCartPack(v.x(), v.y(), v.z())] = c;
    }
    std::vector<bool> nearSurface(nLeaves, false);
    // per leaf: bit i set when carving surface i comes within half a
    // diagonal of the leaf's box
    std::vector<uint64_t> nearMask(nLeaves, 0);
    {
        // Down the tree from every root, each cell's box inflated by half its
        // diagonal (reach one cell out, so both sides of a cut are flagged):
        // a child's inflated box lies inside its parent's, so it tests only
        // the carving triangles its parent's box overlaps.
        struct proximityWalk
        {
            const dcCartGrid& grid;
            const dcCartSurfaces& surfaces;
            const std::vector<std::unordered_map<uint64_t, label>>& leafId;
            std::vector<bool>& near;
            std::vector<uint64_t>& mask;
            uint64_t carving;
            std::vector<std::vector<uint64_t>> depthCand;

            void walk(label L, const labelVector& v, const uint64_t* cb, const uint64_t* ce)
            {
                boundBox bb = grid.cellBox(L, v);
                bb.inflate(0.5);
                const treeBoundBox tbb(bb);
                if (L < label(leafId.size()))
                {
                    const auto it = leafId[L].find(dcCartPack(v.x(), v.y(), v.z()));
                    if (it != leafId[L].end())
                    {
                        const uint64_t mk = surfaces.overlapMaskCand(tbb, cb, ce);
                        mask[it->second] = mk;
                        near[it->second] = ((mk & carving) != 0);
                        return;
                    }
                }
                if (L + 1 >= label(depthCand.size())) { return; }
                std::vector<uint64_t>& mine = depthCand[L];
                mine.clear();
                surfaces.filterCand(tbb, cb, ce, mine);
                const uint64_t* mb = mine.data();
                const uint64_t* me = mb + mine.size();
                for (label d = 0; d < 8; ++d)
                {
                    walk
                    (
                        L + 1,
                        labelVector(2*v.x() + (d & 1), 2*v.y() + ((d >> 1) & 1), 2*v.z() + ((d >> 2) & 1)),
                        mb, me
                    );
                }
            }
        };
        proximityWalk pw{grid, surfaces, leafId, nearSurface, nearMask, surfaces.carvingBits(), std::vector<std::vector<uint64_t>>(leafId.size() + 1)};
        // carving surfaces decide "near"; the faceZone baffles ride along so
        // the zone lookups of the assembly can skip the ones far away
        const std::vector<uint64_t> rootCand = surfaces.proximityCandidates();
        const label n0x = grid.nAtLevel(0, 0), n0y = grid.nAtLevel(1, 0), n0z = grid.nAtLevel(2, 0);
        for (label i = 0; i < n0x; ++i)
        {
            for (label j = 0; j < n0y; ++j)
            {
                for (label k = 0; k < n0z; ++k)
                {
                    pw.walk(0, labelVector(i, j, k), rootCand.data(), rootCand.data() + rootCand.size());
                }
            }
        }
    }
    Info<< "Surface proximity: "
        << label(std::count(nearSurface.begin(), nearSurface.end(), true))
        << " of " << nLeaves << " cells near geometry"
        << "  (" << timer.timeIncrement() << " s)" << nl;
    budgetCheck("proximity");

    auto idOf = [&](label level, const labelVector& v) -> label
    {
        if (level < 0 || level >= label(leafId.size())) { return -1; }
        auto it = leafId[level].find(dcCartPack(v.x(), v.y(), v.z()));
        return (it == leafId[level].end()) ? -1 : it->second;
    };

    boolList alive(nLeaves, false);

    // Seed cells
    DynamicList<point> seeds;
    if (seedSource->found("locationsInMesh"))
    {
        // ((point) zone) pairs, as snappyHexMeshDict writes them
        List<Tuple2<point, word>> locs
        (
            seedSource->get<List<Tuple2<point, word>>>("locationsInMesh")
        );
        forAll(locs, i) { seeds.append(locs[i].first()); }
    }
    else if (seedSource->found("locationInMesh"))
    {
        seeds.append(seedSource->get<point>("locationInMesh"));
    }
    if (seeds.empty())
    {
        FatalErrorInFunction
            << "No locationsInMesh / locationInMesh seed found; the carve has"
            << " no interior to start from." << exit(FatalError);
    }

    std::deque<label> queue;
    label nSeedCells = 0;
    forAll(seeds, s)
    {
        labelVector idx(labelVector::zero);
        if (!grid.findCell(tree.maxLevel(), seeds[s], idx))
        {
            WarningInFunction
                << "seed " << seeds[s] << " is outside the domain — ignored" << endl;
            continue;
        }
        const label lvl = tree.containingLevel(tree.maxLevel(), idx);
        if (lvl < 0) { continue; }
        labelVector at(idx);
        for (label L = tree.maxLevel(); L > lvl; --L)
        {
            at = labelVector(at.x() / 2, at.y() / 2, at.z() / 2);
        }
        label c = idOf(lvl, at);
        // A seed within half a cell of a surface sits in a leaf whose centre
        // can lie across that surface, inside a solid: the fill then starts
        // in the solid and keeps all of it. DAML-Testbed's seed 6 mm in front
        // of rack R2 (x 4.3539 vs the frame face at 4.36) resolved to a
        // 31 mm leaf centred at x 4.3613 -- inside the frame -- and the fill
        // kept the frame's side walls as fluid (2.2 of 10.7 m2 meshed). The
        // seed's leaf is taken only when the segment from the seed point to
        // its centre crosses no surface; otherwise the first face neighbour
        // whose centre the seed reaches unblocked (axis order, minus side
        // first), else the seed is dropped with a warning.
        if (c >= 0 && nearSurface[c]
         && surfaces.blockingRegionMasked(seeds[s], grid.cellCentre(lvl, at), nearMask[c]) >= 0)
        {
            label pick = -1;
            for (direction cmpt = 0; cmpt < 3 && pick < 0; ++cmpt)
            {
                for (label sgn = 0; sgn < 2 && pick < 0; ++sgn)
                {
                    label nbrLevel = -1;
                    labelVector nbrIdx(labelVector::zero);
                    const dcCartOctree::neighbourType nt =
                        tree.neighbour(lvl, at, cmpt, sgn == 1, nbrLevel, nbrIdx);
                    if (nt == dcCartOctree::OUTSIDE) { continue; }
                    FixedList<labelVector, 4> quad;
                    label nNbr = 1, nbrL = nbrLevel;
                    if (nt == dcCartOctree::FINER)
                    {
                        tree.finerFaceNeighbours(lvl, at, cmpt, sgn == 1, quad);
                        nNbr = 4;
                        nbrL = lvl + 1;
                    }
                    else
                    {
                        quad[0] = nbrIdx;
                    }
                    for (label q = 0; q < nNbr && pick < 0; ++q)
                    {
                        const label n = idOf(nbrL, quad[q]);
                        if (n < 0) { continue; }
                        if (surfaces.blockingRegionMasked(seeds[s], grid.cellCentre(nbrL, quad[q]), nearMask[c] | nearMask[n]) < 0)
                        {
                            pick = n;
                        }
                    }
                }
            }
            if (pick < 0)
            {
                WarningInFunction
                    << "seed " << seeds[s] << " lies in a leaf whose centre is across a"
                    << " surface, and no neighbour centre is reachable from it -- ignored" << endl;
                continue;
            }
            Info<< "Carve seed " << seeds[s] << ": its leaf centre lies across a surface;"
                << " starting from the neighbour at "
                << grid.cellCentre(levels[pick], indices[pick]) << nl;
            c = pick;
        }
        if (c >= 0 && !alive[c])
        {
            alive[c] = true;
            queue.push_back(c);
            ++nSeedCells;
        }
    }
    Info<< "Carve seeds: " << nSeedCells << " of " << seeds.size()
        << " locations resolved to cells" << nl;

    label nBlockedFaces = 0;
    label nQueries = 0;
    // DCCART_CARVE_TRACE="x0 y0 z0 x1 y1 z1": for the cells kept inside the
    // box, print the flood's path back to its seed (how the fill got there)
    boundBox carveBox(boundBox::invertedBox);
    if (const char* e = getenv("DCCART_CARVE_TRACE"))
    {
        double b[6];
        if (sscanf(e, "%lf %lf %lf %lf %lf %lf", b, b+1, b+2, b+3, b+4, b+5) == 6)
        {
            carveBox = boundBox(point(b[0], b[1], b[2]), point(b[3], b[4], b[5]));
        }
    }
    labelList carveParent(carveBox.valid() ? nLeaves : 0, -1);
    while (!queue.empty())
    {
        const label c = queue.front();
        queue.pop_front();
        const label L = levels[c];
        const labelVector& v = indices[c];
        const point pc = grid.cellCentre(L, v);

        for (direction cmpt = 0; cmpt < 3; ++cmpt)
        {
            for (label s = 0; s < 2; ++s)
            {
                const bool positive = (s == 1);

                label nbrLevel = -1;
                labelVector nbrIdx(labelVector::zero);
                const dcCartOctree::neighbourType nt =
                    tree.neighbour(L, v, cmpt, positive, nbrLevel, nbrIdx);
                if (nt == dcCartOctree::OUTSIDE) { continue; }

                FixedList<labelVector, 4> quad;
                label nNbr = 1;
                label nbrL = nbrLevel;
                if (nt == dcCartOctree::FINER)
                {
                    tree.finerFaceNeighbours(L, v, cmpt, positive, quad);
                    nNbr = 4;
                    nbrL = L + 1;
                }
                else
                {
                    quad[0] = nbrIdx;
                }

                for (label q = 0; q < nNbr; ++q)
                {
                    const label n = idOf(nbrL, quad[q]);
                    if (n < 0 || alive[n]) { continue; }

                    if (nearSurface[c] || nearSurface[n])
                    {
                        ++nQueries;
                        // (the segment lies in the two cells' boxes)
                        if (surfaces.blockingRegionMasked(pc, grid.cellCentre(nbrL, quad[q]), nearMask[c] | nearMask[n]) >= 0)
                        {
                            ++nBlockedFaces;
                            continue;
                        }
                    }
                    alive[n] = true;
                    if (carveBox.valid()) { carveParent[n] = c; }
                    queue.push_back(n);
                }
            }
        }
    }
    if (carveBox.valid())
    {
        label nShown = 0;
        forAll(alive, i)
        {
            if (!alive[i] || !carveBox.contains(grid.cellCentre(levels[i], indices[i]))) { continue; }
            if (nShown++ >= 3) { break; }
            Pout<< "[carve trace] cell " << i << " at " << grid.cellCentre(levels[i], indices[i]) << " L" << levels[i] << nl;
            label x = i, steps = 0;
            while (x >= 0 && steps < 400)
            {
                const label px = carveParent[x];
                if (px >= 0)
                {
                    const point a = grid.cellCentre(levels[px], indices[px]);
                    const point b = grid.cellCentre(levels[x], indices[x]);
                    const bool inA = carveBox.contains(a), inB = carveBox.contains(b);
                    if (steps < 6 || inA != inB || getenv("DCCART_CARVE_TRACE_ALL"))
                    {
                        Pout<< "    " << a << " L" << levels[px] << " -> " << b << " L" << levels[x]
                            << " near " << label(nearSurface[px]) << label(nearSurface[x]) << nl;
                    }
                }
                x = px; ++steps;
            }
        }
    }

    label nAlive = 0;
    forAll(alive, i) { if (alive[i]) { ++nAlive; } }
    Info<< "Carved: " << nAlive << " of " << nLeaves << " cells kept ("
        << nLeaves - nAlive << " removed), " << nBlockedFaces
        << " blocked faces from " << nQueries << " segment queries"
        << "  (" << timer.timeIncrement() << " s)" << nl;
    budgetCheck("carve");

    if (nAlive == 0)
    {
        FatalErrorInFunction
            << "The carve kept no cells. Every seed is inside solid geometry,"
            << " or the surfaces do not close." << exit(FatalError);
    }

    // ------------------------------------------------------------------
    // Assemble and write
    // ------------------------------------------------------------------
    wordList patchNames(surfaces.patchNames());
    wordList patchTypes(surfaces.patchTypes());
    // Per patch: its cyclic partner's name and the extra inGroups entry
    // createBaffles would have given it (both null for every other patch).
    // Kept parallel to patchNames through every append below, so the boundary
    // file a baffle pair gets here is the one createBaffles used to write.
    wordList patchNeighbours(patchNames.size());
    wordList patchGroups(patchNames.size());
    const label domainOffset = patchNames.size();
    patchNames.setSize(domainOffset + 6);
    patchTypes.setSize(domainOffset + 6);
    patchNeighbours.setSize(domainOffset + 6);
    patchGroups.setSize(domainOffset + 6);
    const char* sideNames[6] =
    {
        "domain_xMin", "domain_xMax", "domain_yMin",
        "domain_yMax", "domain_zMin", "domain_zMax"
    };
    for (label i = 0; i < 6; ++i)
    {
        patchNames[domainOffset + i] = word(sideNames[i]);
        patchTypes[domainOffset + i] = word("wall");
    }

    labelList wallMasters(surfaces.faceZoneNames().size(), -1);
    labelList wallSlaves(surfaces.faceZoneNames().size(), -1);
    // Flow-through baffles: the pair createBaffles used to build from the
    // faceZone, claimed here instead so the pipeline's last two utility
    // passes (redistributePar, createBaffles) are not needed. Split in
    // splitBaffleZones, after dropGhostCells -- see the call site.
    labelList cycMasters(surfaces.faceZoneNames().size(), -1);
    labelList cycSlaves(surfaces.faceZoneNames().size(), -1);
    // splitBaffles: write the flow-through pairs here instead of leaving them
    // to createBaffles. Off by default so an older runner keeps the old
    // two-pass pipeline; the runner that asks for it also skips those passes.
    const bool splitBaffles = meshDict.getOrDefault<bool>("splitBaffles", false);
    // wallPairZones: zones the RUNNER treats as wall baffles but that carry no
    // `wallBaffle true` of their own -- the pipeline's historical shape was
    // "createBaffles makes every baffle cyclic, then rewrite the wall ones to
    // type wall in the boundary file". A cyclic that is really a wall cannot
    // be split across ranks the way a flow-through one can: there is nothing
    // to couple, each side is simply an independent wall on its own rank, so
    // these get wall patches and their straddling faces go straight into them
    // instead of into a processorCyclic pair (which would then be retyped to
    // wall underneath, leaving a processorCyclic referring to a wall --
    // "Attempt to cast type wall to type coupled" at solver start-up).
    const wordList wallPairZones
    (
        meshDict.lookupOrDefault("wallPairZones", wordList())
    );
    for (const auto& surface : surfaces.info())
    {
        if (!surface.internalBaffle) { continue; }
        const label zone = surfaces.faceZoneNames().find(surface.faceZone);
        if (zone < 0) { continue; }
        const bool wallPair = wallPairZones.found(surface.faceZone);
        if (surface.wallBaffle || !splitBaffles)
        {
            if (!surface.wallBaffle) { continue; }   // leave it to createBaffles
            if (wallMasters[zone] >= 0) { continue; }
            wallMasters[zone] = patchNames.size();
            patchNames.append(surface.faceZone + "_master"); patchTypes.append("wall");
            patchNeighbours.append(word::null); patchGroups.append(word::null);
            wallSlaves[zone] = patchNames.size();
            patchNames.append(surface.faceZone + "_slave"); patchTypes.append("wall");
            patchNeighbours.append(word::null); patchGroups.append(word::null);
            continue;
        }
        if (cycMasters[zone] >= 0) { continue; }
        const word pairType(wallPair ? "wall" : "cyclic");
        cycMasters[zone] = patchNames.size();
        patchNames.append(surface.faceZone + "_master"); patchTypes.append(pairType);
        patchNeighbours.append(wallPair ? word::null : word(surface.faceZone + "_slave"));
        patchGroups.append(surface.faceZone);
        cycSlaves[zone] = patchNames.size();
        patchNames.append(surface.faceZone + "_slave"); patchTypes.append(pairType);
        patchNeighbours.append(wallPair ? word::null : word(surface.faceZone + "_master"));
        patchGroups.append(surface.faceZone);
    }

    dcResolver resolver(surfaces, grid, domainOffset);
    resolver.useMasks(leafId, nearMask);
    dcZoneResolver zoneResolver(surfaces, grid);
    zoneResolver.useMasks(leafId, nearMask);

    // ------------------------------------------------------------------
    // Cut cells (default on). The stair-step carve keeps or drops whole
    // cells; the clip pass then moves every boundary and baffle onto the
    // surface itself. Cells the carve dropped but which still hold fluid
    // corners are brought back first, so the sliver between the last kept
    // cell and the surface is meshed rather than lost.
    // ------------------------------------------------------------------
    const bool cutCells = meshDict.getOrDefault<bool>("cutCells", true);
    // Fixed all-rank agreement before either cutCells mode branch.
    const char* earliestPhysicalFlag = getenv("DCCART_EARLIEST_PHYSICAL_POINT_REPAIR");
    const bool earliestPhysicalRequested = earliestPhysicalFlag
        && std::string(earliestPhysicalFlag) == "1";
    label earliestPhysicalSetup = 0;
    if (earliestPhysicalRequested)
    {
        const char* earliestWeight = getenv("DCCART_CROSS_PARENT_WEIGHT_UNIONS");
        const char* earliestContext = getenv("DCCART_COMPLETE_CAVITY_CONTEXT");
        const char* earliestRepair = getenv("DCCART_COMPLETE_CAVITY_REPAIR");
        earliestPhysicalSetup = 1
            + 2*label(getenv("DCCART_CONFORMING_CONES") != nullptr)
            + 4*label(getenv("DCCART_CONE_LITERAL_UNIONS") != nullptr)
            + 8*label(getenv("DCCART_CONE_SAME_PHYSICAL_ROLE") != nullptr)
            + 16*label(earliestWeight && std::string(earliestWeight) == "1")
            + 32*label(earliestContext && std::string(earliestContext) == "1")
            + 64*label(earliestRepair && std::string(earliestRepair) == "1")
            + 128*label(cutCells);
    }
    const label earliestPhysicalMin = returnReduce(earliestPhysicalSetup,minOp<label>());
    const label earliestPhysicalMax = returnReduce(earliestPhysicalSetup,maxOp<label>());
    if (earliestPhysicalMin != earliestPhysicalMax
     || (earliestPhysicalMax != 0 && earliestPhysicalMax != 255))
    {
        FatalErrorInFunction
            << "earliest physical point repair requires agreed cutCells/conforming/literal/same-role/W/context/repair flags; no mesh written"
            << exit(FatalError);
    }
    const bool earliestPhysicalPointRepair = earliestPhysicalMax == 255;
    dcCartCutCells::settings cutSettings;
    cutSettings.workAvailable = [&timer,timeLimit]()
    { return timeLimit <= 0 || timer.elapsedTime() <= timeLimit; };
    cutSettings.minVolumeFraction =
        meshDict.getOrDefault<scalar>("minCutVolumeFraction", 0.25);
    cutSettings.killVolumeFraction =
        meshDict.getOrDefault<scalar>("killCutVolumeFraction", 1e-6);
    cutSettings.degenerateVolumeFraction =
        meshDict.getOrDefault<scalar>("degenerateCutVolumeFraction", 1e-6);
    // a wall sliver thinner than the ON band goes to the solid; the band is
    // set once the finest cell is known (below), unless the dictionary says
    const scalar killThicknessDict = meshDict.getOrDefault<scalar>("killCutThickness", -1);
    cutSettings.mergeMaxNonOrtho =
        meshDict.getOrDefault<scalar>("mergeMaxNonOrthogonality", 75);
    cutSettings.mergeMaxSkew =
        meshDict.getOrDefault<scalar>("mergeMaxSkewness", 4);
    cutSettings.crumbBaffleSkewFactor =
        meshDict.getOrDefault<scalar>("crumbBaffleSkewFactor", 1.1);
    cutSettings.baffleCrumbFraction =
        meshDict.getOrDefault<scalar>("baffleCrumbFraction", 0.01);
    cutSettings.microFaceFraction =
        meshDict.getOrDefault<scalar>("microFaceFraction", 0.005);
    cutSettings.microSnapFraction =
        meshDict.getOrDefault<scalar>("microSnapFraction", 0.02);
    cutSettings.maxGlobalCells = maxGlobalCells;
    cutSettings.cutBaffles = meshDict.getOrDefault<bool>("cutBaffles", true);
    cutSettings.baffleEdgeSplit = meshDict.getOrDefault<bool>("baffleEdgeSplit", true);
    // DCCART_NO_BAFFLE_EDGE_SPLIT: the DAML thin-wall wedge WP measured the
    // free-edge pre-split as the creator of the baffle-sheet junction
    // tetrahedra (V 1e-11, y_P 2.5e-5 -- the eps=2*k*nu/y^2 killers no merge
    // can absorb and no kill can remove without leaving unrepairable micron
    // notch faces). With the split off the corner pieces stay attached to
    // their cells: DAML min cell volume 9.6e-8 -> 5.3e-7 of its hex, max
    // skewness unchanged at 3.9998, Mesh OK. Opt-in per case (the split still
    // earns its keep where a sheet's free edge crosses big cells).
    if (getenv("DCCART_NO_BAFFLE_EDGE_SPLIT"))
    {
        cutSettings.baffleEdgeSplit = false;
        Info<< "baffleEdgeSplit OFF (DCCART_NO_BAFFLE_EDGE_SPLIT)" << nl;
    }
    cutSettings.baffleEdgeSplitFraction =
        meshDict.getOrDefault<scalar>("baffleEdgeSplitFraction", 0.35);
    // wedgeKill (route 3 of the DAML thin-wall wedge WP): the terminal y_P
    // floor round inside mergeSmallCells -- see settings::wedgeVolumeFraction.
    // OFF by default; the pre-wedge cases are validated against their recorded
    // numbers, so this is opt-in like the weld:
    //   DCCART_WEDGE_KILL=1     enable with 2e-3 / 5.2e-4 (or the dict values)
    //   DCCART_WEDGE_VOL=<f>    V/Vref admission
    //   DCCART_WEDGE_Y=<m>      y_P admission
    //   wedgeVolumeFraction / wedgeWallDistance  dcCartMeshDict keys
    // 5.2e-4 sits just above the 5.0e-4 wall-function floor the converging
    // meshes show, so a cell at 4.9e-4 cannot slip through on rounding.
    cutSettings.wedgeVolumeFraction =
        meshDict.getOrDefault<scalar>("wedgeVolumeFraction", 0);
    cutSettings.wedgeWallDistance =
        meshDict.getOrDefault<scalar>("wedgeWallDistance", 0);
    {
        const char* wv = getenv("DCCART_WEDGE_VOL");
        if (wv) { cutSettings.wedgeVolumeFraction = scalar(atof(wv)); }
        const char* wy = getenv("DCCART_WEDGE_Y");
        if (wy) { cutSettings.wedgeWallDistance = scalar(atof(wy)); }
        const char* wk = getenv("DCCART_WEDGE_KILL");
        if (wk)
        {
            if (wk[0] == '0' && !wk[1])
            {
                cutSettings.wedgeVolumeFraction = 0;
                cutSettings.wedgeWallDistance = 0;
            }
            else
            {
                if (cutSettings.wedgeVolumeFraction <= 0)
                {
                    cutSettings.wedgeVolumeFraction = 2e-3;
                }
                if (cutSettings.wedgeWallDistance <= 0)
                {
                    cutSettings.wedgeWallDistance = 5.2e-4;
                }
            }
        }
    }
    if (cutSettings.wedgeVolumeFraction > 0 && cutSettings.wedgeWallDistance > 0)
    {
        Info<< "WedgeKill: ON (V/Vref < " << cutSettings.wedgeVolumeFraction
            << ", y_P < " << cutSettings.wedgeWallDistance << " m)" << nl;
    }
    // refineLongCells' aspect ceiling. 900 is the historical default and is
    // what checkMesh itself warns at (1000), so it only ever catches the
    // extreme corner cells.
    //
    // Exposed as a dictionary key to test whether tightening it rescues the
    // ALIGNED background, which trades skewness for under-determined cells
    // (Base604: max skewness 21.3791 -> 4.92712 and 22 -> 16 faces, but max
    // aspect 135.9 -> 368.6 and small-determinant cells 1,900 -> 6,339, and
    // that mesh diverges the pressure equation at iteration 93). MEASURED at
    // maxAspectRatio 100 on the aligned Base604 mesh: it split 388 long corner
    // cells and made everything slightly WORSE -- max skewness unchanged at
    // 4.92712 / 16 faces, small-determinant cells 6,339 -> 6,605, small
    // interpolation weight 15,732 -> 16,214, small volume ratio 6,263 -> 6,870.
    // So the under-determined cells come from the alignment itself, not from
    // un-split long cells, and that route is closed. The default stays 900.
    const scalar maxAspectRatio =
        meshDict.getOrDefault<scalar>("maxAspectRatio", 900);

    // Patches of type plain `wall`: the only ones coalesceCoplanarFaces may
    // join across a patch-name difference (DCCART_COOALESCE_WALLS).
    boolList plainWallPatch(patchTypes.size(), false);
    forAll(patchTypes, pi)
    {
        if (patchTypes[pi] == "wall") { plainWallPatch[pi] = true; }
    }

    autoPtr<polyMesh> meshPtr;
    labelList sourcePatchAddressMap;
    const bool recordSourceRegions = identitySource->found("dcdtuniSourceRegions", keyType::LITERAL);
    if (!cutCells)
    {
        dcCartMeshBuilder builder
        (
            tree, alive, patchNames, patchTypes, resolver,
            surfaces.faceZoneNames(),
            surfaces.faceZoneNames().size() ? &zoneResolver : nullptr
        );
        Info<< "Assembled: " << builder.info().c_str()
            << "  (" << timer.timeIncrement() << " s)" << nl;
        forAll(builder.faceZoneNames(), z)
        {
            Info<< "  faceZone " << builder.faceZoneNames()[z] << ": "
                << builder.faceZoneFaces()[z].size() << " faces" << nl;
        }
        budgetCheck("assemble");
        meshPtr = builder.mesh(runTime, polyMesh::defaultRegion);
        if (recordSourceRegions)
        {
            // This builder writes its authored patch list without compaction.
            sourcePatchAddressMap.setSize(patchNames.size());
            forAll(sourcePatchAddressMap, patchi) { sourcePatchAddressMap[patchi] = patchi; }
        }
    }
    else
    {
        // ON tolerance: a small fraction of the finest cell
        scalar finest = GREAT;
        {
            // The TYPICAL background spacing (median), then the deepest
            // refinement. Not the minimum: an aligned grid carries planes a
            // fraction of a millimetre apart wherever two objects nearly
            // touch, and a tolerance scaled from those would snap nothing.
            std::vector<scalar> spacings;
            for (direction d = 0; d < 3; ++d)
            {
                const scalarField& bp = grid.breakpoints(d);
                for (label i = 1; i < bp.size(); ++i)
                {
                    spacings.push_back(bp[i] - bp[i - 1]);
                }
            }
            std::sort(spacings.begin(), spacings.end());
            finest = spacings.empty() ? 1.0 : spacings[spacings.size() / 2];
            finest /= scalar(1 << max(tree.deepestLevel(), label(0)));
        }
        // ON means exactly on the surface (a grid-aligned face): a looser
        // band would bend cut faces out of plane; slivers are merged instead.
        cutSettings.wallZonePatch.setSize(surfaces.faceZoneNames().size(), -1);
        forAll(cutSettings.wallZonePatch, z)
        {
            if (wallPairZones.found(surfaces.faceZoneNames()[z])) { cutSettings.wallZonePatch[z] = cycMasters[z]; }
        }
        cutSettings.onTolerance = meshDict.getOrDefault<scalar>("onToleranceFraction", 0.1) * finest;
        scalar carveOnFraction=meshDict.getOrDefault<scalar>("onToleranceCarveFraction", 0.01);
        if (const char* env=getenv("DCCART_CARVE_ON_FRACTION"))
        {
            char* end=nullptr;const scalar requested=std::strtod(env,&end);
            if (!end || *end || !std::isfinite(requested) || requested<=0 || requested>carveOnFraction)
            { FatalErrorInFunction<<"invalid diagnostic carve ON fraction (must be finite, positive and no larger than configured)"<<exit(FatalError); }
            carveOnFraction=requested;
            Info<<"Diagnostic carve ON fraction "<<carveOnFraction<<", tolerance "<<(carveOnFraction*finest)<<" m"<<endl;
        }
        cutSettings.onToleranceCarve = carveOnFraction * finest;
        cutSettings.tiltedOnFraction = meshDict.getOrDefault<scalar>("tiltedOnFraction", 0.6);
        cutSettings.killThickness =
            killThicknessDict >= 0 ? killThicknessDict : cutSettings.onTolerance;
        // ... and the same band measured on the sliver's own cell, where that
        // is coarser than the finest
        cutSettings.killThicknessFraction =
            killThicknessDict >= 0 ? 0
          : meshDict.getOrDefault<scalar>("onToleranceFraction", 0.1);
        Info<< "Cut cells: finest cell ~" << finest << " m, ON tolerance "
            << cutSettings.onTolerance << " m" << nl;

        // fluid corners, keyed by exact coordinates: a flat open-addressing
        // set of the coordinates' bits (every corner here comes from the same
        // planeCoord arithmetic, so equal points have equal bits)
        struct flatPointSet
        {
            struct slot { uint64_t k[3]; bool used; };
            std::vector<slot> table;
            size_t n = 0;
            static void bits(const point& p, uint64_t k[3])
            {
                for (direction d = 0; d < 3; ++d) { double v = p[d]; std::memcpy(&k[d], &v, sizeof(uint64_t)); }
            }
            static size_t hashOf(const uint64_t k[3])
            {
                uint64_t h = 1469598103934665603ull;
                for (int d = 0; d < 3; ++d) { h ^= k[d]; h *= 1099511628211ull; }
                h ^= h >> 29; h *= 0xBF58476D1CE4E5B9ull; h ^= h >> 32;
                return size_t(h);
            }
            void reserve(size_t m)
            {
                size_t cap = 16;
                while (cap < 2*m) { cap <<= 1; }
                if (cap > table.size()) { rehash(cap); }
            }
            void rehash(size_t cap)
            {
                std::vector<slot> old(cap, slot{{0, 0, 0}, false});
                old.swap(table);
                n = 0;
                for (const slot& o : old) { if (o.used) { insertBits(o.k); } }
            }
            void insertBits(const uint64_t k[3])
            {
                if (2*(n + 1) > table.size()) { rehash(table.empty() ? size_t(16) : 2*table.size()); }
                const size_t mask = table.size() - 1;
                for (size_t i = hashOf(k) & mask; ; i = (i + 1) & mask)
                {
                    slot& s = table[i];
                    if (!s.used) { s.k[0] = k[0]; s.k[1] = k[1]; s.k[2] = k[2]; s.used = true; ++n; return; }
                    if (s.k[0] == k[0] && s.k[1] == k[1] && s.k[2] == k[2]) { return; }
                }
            }
            void insert(const point& p) { uint64_t k[3]; bits(p, k); insertBits(k); }
            size_t count(const point& p) const
            {
                if (table.empty()) { return 0; }
                uint64_t k[3]; bits(p, k);
                const size_t mask = table.size() - 1;
                for (size_t i = hashOf(k) & mask; ; i = (i + 1) & mask)
                {
                    const slot& s = table[i];
                    if (!s.used) { return 0; }
                    if (s.k[0] == k[0] && s.k[1] == k[1] && s.k[2] == k[2]) { return 1; }
                }
            }
        };
        // Only the corners of the dead leaves near geometry are ever looked up
        // (the candidates below): the stair-step corners no such leaf has need
        // no class, and on a room full of racks they are most of them.
        const bool compareCorners = (getenv("DCCART_COMPARE_CORNERS") != nullptr);
        // The corners are found from the requested keys directly (the stair-
        // step corner topology is no longer assembled: half the stage on
        // Base604, byte-identical meshes); DCCART_LEGACY_CORNERS=1 restores
        // the assembled path, DCCART_COMPARE_CORNERS=1 audits one against
        // the other.
        const bool directCorners = compareCorners || (getenv("DCCART_LEGACY_CORNERS") == nullptr);
        const label cornerLevel = max(tree.deepestLevel(), label(1));
        dcCartKeySet requestedGridCorners;
        flatPointSet wantedCorner;
        for (label c = 0; c < nLeaves; ++c)
        {
            if (alive[c] || !nearSurface[c]) { continue; }
            const boundBox bb = grid.cellBox(levels[c], indices[c]);
            for (label k = 0; k < 8; ++k)
            {
                if (directCorners)
                {
                    const label shift = cornerLevel - levels[c];
                    const labelVector& idx = indices[c];
                    requestedGridCorners.insert(dcCartPack
                    (
                        (idx.x() + ((k & 1) ? 1 : 0)) << shift,
                        (idx.y() + ((k & 2) ? 1 : 0)) << shift,
                        (idx.z() + ((k & 4) ? 1 : 0)) << shift
                    ));
                }
                if (!directCorners || compareCorners) { wantedCorner.insert
                (
                    point
                    (
                        (k & 1) ? bb.max().x() : bb.min().x(),
                        (k & 2) ? bb.max().y() : bb.min().y(),
                        (k & 4) ? bb.max().z() : bb.min().z()
                    )
                ); }
            }
        }
        // Pass 1: the corners of the stair-step mesh, to learn which are
        // fluid. Only its corner topology is assembled (cornerCells): each
        // asked corner's live cells come from the faces' point lists, and no
        // other part of that mesh is ever used.
        //
        // Every rank sees the same corners: each classifies a share of them
        // and the answers are combined — a corner's side is a pure function
        // of the stair-step mesh and the surfaces. The share is drawn by a
        // hash of the point id: points are numbered cell by cell, level by
        // level, so consecutive runs cluster in space and cost unevenly.
        const label nRanksC = Pstream::parRun() ? Pstream::nProcs() : 1;
        const label myRankC = Pstream::parRun() ? Pstream::myProcNo() : 0;
        auto shareOf = [&](label p) -> bool
        {
            if (nRanksC <= 1) { return true; }
            uint64_t h = uint64_t(p) + 0x9E3779B97F4A7C15ULL;
            h = (h ^ (h >> 30)) * 0xBF58476D1CE4E5B9ULL;
            h = (h ^ (h >> 27)) * 0x94D049BB133111EBULL;
            h ^= h >> 31;
            return label(h % uint64_t(nRanksC)) == myRankC;
        };
        pointField corners;
        labelList c1Level;
        List<labelVector> c1Index;
        std::vector<bool> wantedPoint;
        std::vector<std::vector<label>> c1PointCells;
        if (directCorners)
        {
            std::vector<uint64_t> requestedKeys;
            requestedKeys.reserve(requestedGridCorners.size());
            for (const uint64_t key : requestedGridCorners) { requestedKeys.push_back(key); }
            std::sort(requestedKeys.begin(), requestedKeys.end());
            requestedGridCorners.clear();
            dcCartMeshBuilder::requestedCornerCells
            (
                tree, alive, requestedKeys,
                [&](label p) { return compareCorners || shareOf(p); },
                corners, c1Level, c1Index, wantedPoint, c1PointCells
            );
            if (compareCorners)
            {
                pointField reference;
                labelList refLevel;
                List<labelVector> refIndex;
                std::vector<bool> refWanted;
                std::vector<std::vector<label>> refCells;
                dcCartMeshBuilder::cornerCells
                (
                    tree, alive,
                    [&](const point& x) { return wantedCorner.count(x) > 0; },
                    [](label) { return true; },
                    reference, refLevel, refIndex, refWanted, refCells
                );
                if (refLevel.size() != c1Level.size())
                {
                    FatalErrorInFunction << "direct corner audit: different live cell counts" << exit(FatalError);
                }
                forAll(refLevel, c)
                {
                    if (refLevel[c] != c1Level[c] || refIndex[c] != c1Index[c])
                    {
                        FatalErrorInFunction << "direct corner audit: live cell identity differs" << exit(FatalError);
                    }
                }
                using coordBits = std::array<uint64_t, 3>;
                auto bitsOf = [&](const point& p) -> coordBits
                {
                    coordBits bits;
                    flatPointSet::bits(p, bits.data());
                    return bits;
                };
                std::vector<std::pair<coordBits, label>> refKeys;
                forAll(reference, p)
                {
                    if (refWanted[p]) { refKeys.emplace_back(bitsOf(reference[p]), p); }
                }
                std::sort(refKeys.begin(), refKeys.end());
                if (refKeys.size() != size_t(corners.size()))
                {
                    FatalErrorInFunction << "direct corner audit: requested point sets differ, direct "
                        << corners.size() << " reference " << refKeys.size() << exit(FatalError);
                }
                forAll(corners, p)
                {
                    const coordBits key = bitsOf(corners[p]);
                    const auto it = std::lower_bound(refKeys.begin(), refKeys.end(),
                        std::make_pair(key, label(-1)));
                    if (it == refKeys.end() || it->first != key)
                    {
                        FatalErrorInFunction << "direct corner audit: point missing " << corners[p] << exit(FatalError);
                    }
                    auto directCells = c1PointCells[p];
                    auto oldCells = refCells[it->second];
                    auto ordered = [](std::vector<label>& v)
                    {
                        std::sort(v.begin(), v.end());
                        v.erase(std::unique(v.begin(), v.end()), v.end());
                    };
                    ordered(directCells); ordered(oldCells);
                    if (directCells != oldCells)
                    {
                        FatalErrorInFunction << "direct corner audit: incident live cells differ at "
                            << corners[p] << exit(FatalError);
                    }
                }
                Info<< "Direct corner audit: exact coordinates and incident live-cell sets match for "
                    << corners.size() << " requested vertices" << nl;
            }
        }
        else
        {
            dcCartMeshBuilder::cornerCells
            (
                tree, alive,
                [&](const point& x) { return wantedCorner.count(x) > 0; },
                shareOf,
                corners, c1Level, c1Index, wantedPoint, c1PointCells
            );
        }
        const label nP1 = label(corners.size());
        label nWanted = 0;
        for (label p = 0; p < nP1; ++p) { if (wantedPoint[p]) { ++nWanted; } }
        std::vector<signed char> side1;
        {
            std::vector<bool> share(nP1, false);
            for (label p = 0; p < nP1; ++p) { share[p] = wantedPoint[p] && shareOf(p); }
            dcCartCutCells::classifyCorners
            (
                corners, c1PointCells,
                [&](label c) { return grid.cellBox(c1Level[c], c1Index[c]).centre(); },
                surfaces, cutSettings.onToleranceCarve, side1, &share
            );
            if (nRanksC > 1)
            {
                // -3: another rank's share (below every side value)
                List<label> all(nP1, -1);
                for (label p = 0; p < nP1; ++p)
                {
                    if (wantedPoint[p]) { all[p] = share[p] ? label(side1[p]) : -3; }
                }
                Pstream::listCombineReduce(all, maxEqOp<label>());
                for (label p = 0; p < nP1; ++p) { side1[p] = static_cast<signed char>(all[p]); }
            }
        }
        Info<< "Cut cells: classified " << nWanted << " of " << label(side1.size()) << " corners"
            << "  (" << timer.timeIncrement() << " s)" << nl;

        flatPointSet fluidCorner;
        for (label p = 0; p < nP1; ++p)
        {
            if (side1[p] > 0) { fluidCorner.insert(corners[p]); }
        }

        // A dead leaf with NO fluid corner can still hold fluid: the tip of a
        // concave corner two surfaces make inside it. Tri_angle's rotated
        // Rack_2 (2026-09-23): its server outlet meets the rail 4.6 mm above
        // a grid plane, so the leaf above that plane holds a 9 x 4.6 mm
        // fluid wedge and not one fluid corner. It was never re-admitted,
        // and the live cell below kept the grid face under the wedge as its
        // boundary — a stair step labelled outlet, 4.6 mm off the outlet
        // plane. The face such a leaf shares with a neighbour holding known
        // fluid shows the wedge: an edge of it, cut at every surface hit,
        // has a piece longer than four carve tolerances whose middle a fluid
        // point of the neighbour reaches without crossing a surface — the
        // reachability rule the corners are classified by. (Not the ON
        // tolerance: at 0.17 m a grid plane passes 0.6 mm from Rack_2's
        // rail/inlet corner, the tip beyond it shows a 1.2 mm piece, and the
        // tip was dropped — a 1.2 mm frame chamfer 0.87 mm off the corner on
        // every row. A piece whose middle is ON a surface is refused below
        // whatever its length.) Every piece, the
        // ones ending on a corner too: a wedge line may pass through a grid
        // corner exactly (Rack_1's rail corner, 12.5 mm deep), which makes
        // that corner ON, not fluid. A middle ON a surface is no fluid (the
        // top face of a leaf under the floor lies in the floor plane).
        //
        // A neighbour's fluid points: a live leaf's centre, a re-admitted
        // leaf's fluid corners (Rack_2's inlet corner in the 10 mm layer
        // under the frame's top plate has no live face neighbour, only
        // re-admitted ones), a wedge leaf's reached point. Wedge leaves are
        // found in rounds, each anchored on the leaves the rounds before it
        // found, for a wedge that only reaches the fluid through another
        // wedge leaf. A round sees only the rounds before it: the answer
        // does not depend on the order the leaves are visited in, and is a
        // pure function of the octree, the carve and the surfaces — every
        // rank decides alike.
        const scalar edgeEps = cutSettings.onToleranceCarve;
        auto edgeRunReached = [&]
        (
            const point& a, const point& b,
            const std::vector<point>& from, point& reached
        ) -> bool
        {
            const vector d = b - a;
            const scalar len = mag(d);
            const scalar minRun = 4*edgeEps;
            if (len <= 2*edgeEps + minRun) { return false; }
            const vector u = d / len;
            std::vector<scalar> cut{0};
            point cur = a + edgeEps*u;
            const point b1 = b - edgeEps*u;
            for (label k = 0; k < 16; ++k)
            {
                point h; label rg = -1;
                if (!surfaces.carvingHit(cur, b1, h, rg)) { break; }
                const scalar t = (h - a) & u;
                cut.push_back(t);
                if (t + 2*edgeEps >= len) { break; }
                cur = a + (t + edgeEps)*u;
            }
            if (cut.size() < 2) { return false; }
            cut.push_back(len);
            for (size_t i = 0; i + 1 < cut.size(); ++i)
            {
                if (cut[i + 1] - cut[i] <= minRun) { continue; }
                const point mid = a + 0.5*(cut[i] + cut[i + 1])*u;
                // ON a surface is not fluid (classifyCorners' own test): an
                // edge lying in the floor plane has its middle ON the floor,
                // and a ray that ends on a surface reports no crossing
                if (surfaces.nearestCarvingDistance(mid, 4*edgeEps) <= edgeEps) { continue; }
                for (const point& f : from)
                {
                    point h; label rg = -1;
                    if (!surfaces.carvingHit(f, mid, h, rg)) { reached = mid; return true; }
                }
            }
            return false;
        };
        // the four edges of the leaf's face normal to `cmpt` on side `positive`
        auto faceHoldsFluid = [&]
        (
            const boundBox& bb, direction cmpt, bool positive,
            const std::vector<point>& from, point& reached
        ) -> bool
        {
            const direction d1 = (cmpt + 1) % 3, d2 = (cmpt + 2) % 3;
            point q[4];
            for (label k = 0; k < 4; ++k)
            {
                q[k][cmpt] = positive ? bb.max()[cmpt] : bb.min()[cmpt];
                q[k][d1] = ((k == 1 || k == 2) ? bb.max() : bb.min())[d1];
                q[k][d2] = (k >= 2 ? bb.max() : bb.min())[d2];
            }
            for (label k = 0; k < 4; ++k)
            {
                if (edgeRunReached(q[k], q[(k + 1) % 4], from, reached)) { return true; }
            }
            return false;
        };
        auto cornerOf = [](const boundBox& bb, label k) -> point
        {
            return point
            (
                (k & 1) ? bb.max().x() : bb.min().x(),
                (k & 2) ? bb.max().y() : bb.min().y(),
                (k & 4) ? bb.max().z() : bb.min().z()
            );
        };

        // Candidates: dead leaves with a fluid corner, or with fluid inside
        // them that no corner sees
        boolList alive2(alive);
        boolList cornerCandidate(nLeaves, false);
        std::vector<label> noFluidCorner;
        label nCandidates = 0, nWedgeCandidates = 0;
        // The global topology is identical on every rank. Classify each
        // dead leaf once, then share the corner decisions before wedge
        // rounds. A wedge round similarly sees the complete previous round,
        // so the result remains independent of rank count and visit order.
        for (label c = 0; c < nLeaves; ++c)
        {
            if (alive[c] || !nearSurface[c] || !shareOf(c)) { continue; }
            const boundBox bb = grid.cellBox(levels[c], indices[c]);
            // a corner in the fluid AND one that is not: a dead cell with all
            // eight corners fluid is inside a thin plate's shadow and no
            // shell would ever split it (the re-admit kill dropped those
            // again; deciding it here keeps every rank's view the same)
            label nFluid = 0;
            for (label k = 0; k < 8; ++k)
            {
                if (fluidCorner.count(cornerOf(bb, k)) > 0) { ++nFluid; }
            }
            if (nFluid > 0 && nFluid < 8)
            {
                alive2[c] = true; cornerCandidate[c] = true; ++nCandidates;
            }
            else if (nFluid == 0)
            {
                noFluidCorner.push_back(c);
            }
        }
        if (nRanksC > 1)
        {
            Pstream::listCombineReduce(alive2, maxEqOp<bool>());
            Pstream::listCombineReduce(cornerCandidate, maxEqOp<bool>());
            reduce(nCandidates, sumOp<label>());
        }
        if (!getenv("DCCART_NO_WEDGE_READMIT"))
        {
            std::unordered_map<label, point> wedgeAnchor;
            for (label round = 0; round < 4; ++round)
            {
                std::vector<std::pair<label, point>> found;
                for (const label c : noFluidCorner)
                {
                    if (alive2[c]) { continue; }
                    const boundBox bb = grid.cellBox(levels[c], indices[c]);
                    const label L = levels[c];
                    const labelVector& v = indices[c];
                    point reached(point::zero);
                    bool hit = false;
                    for (direction cmpt = 0; cmpt < 3 && !hit; ++cmpt)
                    {
                        for (label sd = 0; sd < 2 && !hit; ++sd)
                        {
                            const bool positive = (sd == 1);
                            label nbrLevel = -1;
                            labelVector nbrIdx(labelVector::zero);
                            const dcCartOctree::neighbourType nt =
                                tree.neighbour(L, v, cmpt, positive, nbrLevel, nbrIdx);
                            if (nt == dcCartOctree::OUTSIDE) { continue; }
                            FixedList<labelVector, 4> quad;
                            label nNbr = 1, nbrL = nbrLevel;
                            if (nt == dcCartOctree::FINER)
                            {
                                tree.finerFaceNeighbours(L, v, cmpt, positive, quad);
                                nNbr = 4;
                                nbrL = L + 1;
                            }
                            else
                            {
                                quad[0] = nbrIdx;
                            }
                            for (label k = 0; k < nNbr && !hit; ++k)
                            {
                                const label n = idOf(nbrL, quad[k]);
                                if (n < 0) { continue; }
                                std::vector<point> from;
                                if (alive[n])
                                {
                                    from.push_back(grid.cellCentre(nbrL, quad[k]));
                                }
                                else if (cornerCandidate[n])
                                {
                                    const boundBox nb = grid.cellBox(nbrL, quad[k]);
                                    for (label q = 0; q < 8; ++q)
                                    {
                                        const point x = cornerOf(nb, q);
                                        if (fluidCorner.count(x) > 0) { from.push_back(x); }
                                    }
                                }
                                else
                                {
                                    const auto it = wedgeAnchor.find(n);
                                    if (it != wedgeAnchor.end()) { from.push_back(it->second); }
                                }
                                if (from.empty()) { continue; }
                                hit = faceHoldsFluid(bb, cmpt, positive, from, reached);
                            }
                        }
                    }
                    if (hit) { found.emplace_back(c, reached); }
                }
                if (nRanksC > 1)
                {
                    List<Tuple2<label, point>> mine(found.size());
                    for (size_t i = 0; i < found.size(); ++i)
                    {
                        mine[i] = Tuple2<label, point>(found[i].first, found[i].second);
                    }
                    List<List<Tuple2<label, point>>> byRank(nRanksC);
                    byRank[myRankC] = std::move(mine);
                    Pstream::gatherList(byRank);
                    Pstream::scatterList(byRank);
                    found.clear();
                    for (const auto& rankFound : byRank)
                    {
                        for (const auto& cp : rankFound)
                        {
                            found.emplace_back(cp.first(), cp.second());
                        }
                    }
                }
                if (found.empty()) { break; }
                for (const auto& cp : found)
                {
                    alive2[cp.first] = true;
                    wedgeAnchor[cp.first] = cp.second;
                    ++nCandidates; ++nWedgeCandidates;
                }
            }
        }
        Info<< "Cut cells: " << nCandidates
            << " dropped cells re-admitted as cut candidates ("
            << nWedgeCandidates << " with fluid between their corners only)"
            << "  (" << timer.timeIncrement() << " s)" << nl;

        // ------------------------------------------------------------------
        // Ownership. ONE code path for any number of ranks: a run without
        // -parallel is the one-rank case (it owns every leaf, has no halo
        // and no processor patch). Slabs along x: every rank has the whole
        // octree and carve (they are cheap and deterministic), and assembles
        // and cuts the leaves it owns plus a halo of its neighbours' leaves;
        // the faces between its cells and another rank's become processor
        // patches at the end.
        // ------------------------------------------------------------------
        const label nProcs = Pstream::parRun() ? Pstream::nProcs() : 1;
        const label myProc = Pstream::parRun() ? Pstream::myProcNo() : 0;
        labelList leafOwner(nLeaves, 0);
        labelList procPatch(nProcs, -1);
        boolList aliveMine(nLeaves, false);
        {
            // ownership by cell-centre x: the cut positions are quantiles of
            // the live leaves' centres at the finest column resolution, so
            // the ranks carry equal work; the interface is whatever octree
            // faces separate owned from non-owned leaves (no planarity
            // needed, no sheet to avoid)
            const label maxL = tree.maxLevel();
            const label nCol = grid.nAtLevel(0, maxL);
            labelList colCount(nCol, 0);
            for (label c = 0; c < nLeaves; ++c)
            {
                if (!alive2[c]) { continue; }
                // finest column holding the leaf centre
                const label col = (indices[c].x() << (maxL - levels[c])) + ((1 << (maxL - levels[c])) / 2);
                ++colCount[min(max(col, label(0)), nCol - 1)];
            }
            // NOTE: weighting these counts by the cut work a column carries
            // (1 + popcount of its leaves' nearMask) was MEASURED and did not
            // help. Base604's clip stage spreads 5.8-10.8 s across ranks that
            // are within 7% on cell count, and the weighted split left that
            // spread where it was (6.4-11.1 s) while moving rank 0 from
            // 237,213 cells to 245,469. Carving-surface density is not what
            // makes a slab expensive, so count-balancing stays.
            label total = 0;
            forAll(colCount, i) { total += colCount[i]; }
            labelList cutCol(nProcs + 1, nCol);
            cutCol[0] = 0;
            {
                label acc = 0, r = 1;
                for (label i = 0; i < nCol && r < nProcs; ++i)
                {
                    acc += colCount[i];
                    while (r < nProcs && acc >= (total * r) / nProcs) { cutCol[r++] = i + 1; }
                }
            }

            for (label c = 0; c < nLeaves; ++c)
            {
                const label col = (indices[c].x() << (maxL - levels[c])) + ((1 << (maxL - levels[c])) / 2);
                label r = 0;
                while (r + 1 < nProcs && col >= cutCol[r + 1]) { ++r; }
                leafOwner[c] = r;
            }

            // Both sides of a baffle on one rank. A faceZone baffle that lies
            // on a face between two ranks' leaves becomes a processorCyclic
            // pair, which neither reconstructParMesh (segfault) nor
            // redistributePar (it cannot re-pair them) can handle, so the
            // solve could only run on the mesher's rank count: Alibaba-sige's
            // perforated blanking panels left 24 such patches at 8 ranks and
            // held its solve to 8 ranks. A baffle inside a leaf splits it
            // into pieces of that leaf, which share its rank; only a baffle
            // between two leaves can separate the ranks, and then the segment
            // between their centres crosses it. Such a pair goes to the lower
            // rank, repeated until no interface pair crosses a baffle. The
            // ownership is global data every rank computes alike, and the
            // mesh does not depend on it (the cells are the same for any
            // partition); only the processor faces move.
            if (nProcs > 1 && surfaces.faceZoneNames().size())
            {
                // leaves within two of their own widths of a cut column: the
                // only ones that can neighbour another rank's leaf
                auto nearCut = [&](label c)
                {
                    const label w = label(1) << (maxL - levels[c]);
                    const label lo = indices[c].x() * w;
                    for (label r = 1; r < nProcs; ++r)
                    {
                        if (cutCol[r] >= lo - 2*w && cutCol[r] <= lo + 3*w) { return true; }
                    }
                    return false;
                };
                std::vector<label> band;
                for (label c = 0; c < nLeaves; ++c) { if (alive2[c] && nearCut(c)) { band.push_back(c); } }
                label nMoved = 0, nPass = 0;
                for (bool changed = true; changed && nPass < 64; ++nPass)
                {
                    changed = false;
                    for (const label c : band)
                    {
                        const label L = levels[c];
                        const labelVector& v = indices[c];
                        const point pc = grid.cellCentre(L, v);
                        for (direction cmpt = 0; cmpt < 3; ++cmpt)
                        {
                            for (label sd = 0; sd < 2; ++sd)
                            {
                                label nbrLevel = -1;
                                labelVector nbrIdx(labelVector::zero);
                                const dcCartOctree::neighbourType nt =
                                    tree.neighbour(L, v, cmpt, sd == 1, nbrLevel, nbrIdx);
                                if (nt == dcCartOctree::OUTSIDE) { continue; }
                                FixedList<labelVector, 4> quad;
                                label nNbr = 1, nbrL = nbrLevel;
                                if (nt == dcCartOctree::FINER)
                                {
                                    tree.finerFaceNeighbours(L, v, cmpt, sd == 1, quad);
                                    nNbr = 4; nbrL = L + 1;
                                }
                                else { quad[0] = nbrIdx; }
                                for (label q = 0; q < nNbr; ++q)
                                {
                                    const label n = idOf(nbrL, quad[q]);
                                    if (n < 0 || !alive2[n] || leafOwner[n] == leafOwner[c]) { continue; }
                                    if (surfaces.crossedFaceZoneMasked(pc, grid.cellCentre(nbrL, quad[q]), ~uint64_t(0)) < 0)
                                    {
                                        continue;
                                    }
                                    const label lowR = min(leafOwner[n], leafOwner[c]);
                                    if (leafOwner[c] != lowR) { leafOwner[c] = lowR; }
                                    else { leafOwner[n] = lowR; }
                                    ++nMoved;
                                    changed = true;
                                }
                            }
                        }
                    }
                }
                Info<< "Ownership: " << nMoved << " leaves moved so that no baffle lies between two ranks ("
                    << nPass << " passes over " << label(band.size()) << " leaves near the cuts)" << nl;
            }

            // owned leaves plus a halo of the other ranks' live
            // leaves: the halo is cut exactly as its owner cuts it (every
            // decision at the interface looks no further than two rings),
            // so the faces between owned and halo cells are the same
            // polygons on both ranks; the halo cells are dropped at the end
            std::vector<label> layer(nLeaves, -1);
            std::deque<label> bfs;
            for (label c = 0; c < nLeaves; ++c)
            {
                if (alive2[c] && leafOwner[c] == myProc) { layer[c] = 0; bfs.push_back(c); }
            }
            const label nHalo = cutSettings.haloLayers;
            while (!bfs.empty())
            {
                const label c = bfs.front(); bfs.pop_front();
                if (layer[c] >= nHalo) { continue; }
                const label L = levels[c];
                const labelVector& v = indices[c];
                for (direction cmpt = 0; cmpt < 3; ++cmpt)
                {
                    for (label sd = 0; sd < 2; ++sd)
                    {
                        label nbrLevel = -1;
                        labelVector nbrIdx(labelVector::zero);
                        const dcCartOctree::neighbourType nt =
                            tree.neighbour(L, v, cmpt, sd == 1, nbrLevel, nbrIdx);
                        if (nt == dcCartOctree::OUTSIDE) { continue; }
                        FixedList<labelVector, 4> quad;
                        label nNbr = 1, nbrL = nbrLevel;
                        if (nt == dcCartOctree::FINER)
                        {
                            tree.finerFaceNeighbours(L, v, cmpt, sd == 1, quad);
                            nNbr = 4; nbrL = L + 1;
                        }
                        else { quad[0] = nbrIdx; }
                        for (label q = 0; q < nNbr; ++q)
                        {
                            const label n = idOf(nbrL, quad[q]);
                            if (n < 0 || !alive2[n] || layer[n] >= 0) { continue; }
                            layer[n] = layer[c] + 1;
                            bfs.push_back(n);
                        }
                    }
                }
            }
            forAll(aliveMine, c) { aliveMine[c] = alive2[c] && layer[c] >= 0; }
            // Default-OFF diagnostic reference: remove only rank-local halo
            // truncation. Ownership, geometry, and output ghost removal remain.
            const char* globalAliveReference =
                std::getenv("DCCART_DIAGNOSTIC_GLOBAL_ALIVE_REFERENCE");
            if (globalAliveReference && std::strcmp(globalAliveReference, "1") == 0)
            {
                aliveMine = alive2;
                Pout<< "Diagnostic global-alive reference: rank " << myProc
                    << " assembles all " << total
                    << " globally live leaves; original leafOwner retained; "
                    << "normal owned output after dropGhostCells" << endl;
            }
            // processor patches to every rank that appears in the halo
            std::set<label> haloRanks;
            for (label c = 0; c < nLeaves; ++c)
            {
                if (aliveMine[c] && leafOwner[c] != myProc) { haloRanks.insert(leafOwner[c]); }
            }
            for (label s : haloRanks)
            {
                procPatch[s] = patchNames.size();
                patchNames.append(word("procBoundary" + Foam::name(myProc) + "to" + Foam::name(s)));
                patchTypes.append(word("processor"));
                patchNeighbours.append(word::null);
                patchGroups.append(word::null);
            }
            label nMine = 0, nHaloCells = 0;
            forAll(aliveMine, c) { if (aliveMine[c]) { if (leafOwner[c] == myProc) { ++nMine; } else { ++nHaloCells; } } }
            if (Pstream::parRun())
            {
                Pout<< "Parallel: rank " << myProc << " owns columns ["
                    << cutCol[myProc] << ", " << cutCol[myProc + 1] << ") of " << nCol
                    << ", " << nMine << " live leaves + " << nHaloCells << " halo" << endl;
            }
        }
        cutSettings.myRank = myProc;

        dcCartMeshBuilder b2
        (
            tree, aliveMine, patchNames, patchTypes, resolver,
            surfaces.faceZoneNames(),
            surfaces.faceZoneNames().size() ? &zoneResolver : nullptr
        );
        Info<< "Assembled: " << b2.info().c_str()
            << "  (" << timer.timeIncrement() << " s)" << nl;
        budgetCheck("assemble");

        // original-alive flag and owning rank per builder cell
        boolList orig2(b2.nCells(), true);
        labelList rank2(b2.nCells(), myProc);
        {
            std::vector<std::unordered_map<uint64_t, label>> leafOf(tree.maxLevel() + 2);
            for (label c = 0; c < nLeaves; ++c)
            {
                const labelVector& v = indices[c];
                leafOf[levels[c]][dcCartPack(v.x(), v.y(), v.z())] = c;
            }
            for (label c = 0; c < b2.nCells(); ++c)
            {
                const label L = b2.cellLevel()[c];
                const labelVector& v = b2.cellIndex()[c];
                auto it = leafOf[L].find(dcCartPack(v.x(), v.y(), v.z()));
                orig2[c] = (it != leafOf[L].end()) ? alive[it->second] : true;
                rank2[c] = (it != leafOf[L].end()) ? leafOwner[it->second] : myProc;
            }
        }

        // Untag the faces of a baffle faceZone that lie in NONE of the zone's
        // sheet planes: the octree builder's stair-step approximation of the
        // sheet, which the sheet pass leaves on the grid faces it did not
        // have to split. Counted over all ranks. `onlyZones` limits it to
        // the zones it flags (nullptr: every zone). Pure geometry per face,
        // so a face on a slab boundary is decided alike on both ranks.
        auto stripOffPlaneStairTags = [&]
        (
            dcCartCutCells::cutMesh& mm,
            const boolList* onlyZones
        ) -> label
        {
            label nStripped = 0;
            const scalar planeTol = max(cutSettings.onTolerance, scalar(1e-6));
            // (each sheet's own tolerance: an exact outline's cut runs on its
            // plane, so a grid face 2.8 mm off it is a stair-step and no cap,
            // and with the 3 mm ON tolerance DAML kept it, 516 slivers of zero
            // determinant walled between it and the real cap)
            struct zonePlane { vector n; scalar d; boundBox bb; scalar tol; };
            std::vector<std::vector<zonePlane>> zonePlanes
            (
                surfaces.faceZoneNames().size()
            );
            std::vector<zonePlane> allPlanes;
            for (label sh = 0; sh < surfaces.nBaffleSheets(); ++sh)
            {
                const dcCartSurfaces::baffleSheet& bs = surfaces.baffleSheetInfo(sh);
                if (bs.zone < 0 || bs.zone >= label(zonePlanes.size())) { continue; }
                const vector nu = bs.normal / max(mag(bs.normal), VSMALL);
                const scalar tol = max
                (
                    dcCartCutCells::baffleSheetPlaneTolerance(surfaces, cutSettings, sh),
                    scalar(1e-6)
                );
                zonePlanes[bs.zone].push_back({nu, bs.origin & nu, bs.bounds, tol});
                allPlanes.push_back({nu, bs.origin & nu, bs.bounds, tol});
            }
            for (label f = 0; f < label(mm.faces.size()); ++f)
            {
                if (!mm.faceAlive[f]) { continue; }
                const label zi = mm.zone[f];
                if (zi < 0 || zi >= label(zonePlanes.size())) { continue; }
                if (onlyZones && (zi >= onlyZones->size() || !(*onlyZones)[zi])) { continue; }
                const std::vector<zonePlane>& planes = zonePlanes[zi];
                if (planes.empty()) { continue; }
                vector nn = vector::zero;
                point cc = point::zero;
                const std::vector<label>& poly = mm.faces[f];
                for (size_t i = 0; i < poly.size(); ++i)
                {
                    nn += mm.points[poly[i]] ^ mm.points[poly[(i + 1) % poly.size()]];
                    cc += mm.points[poly[i]];
                }
                const scalar l = mag(nn);
                if (l < VSMALL) { continue; }
                nn /= l;
                cc /= poly.size();
                point fLo = mm.points[poly[0]], fHi = fLo;
                for (const label pi : poly)
                {
                    fLo = min(fLo, mm.points[pi]);
                    fHi = max(fHi, mm.points[pi]);
                }
                // the face overlaps the sheet's box by more than the tolerance
                // along every axis it spans (a face merely touching the sheet's
                // edge does not), and comes within it along the others
                auto onSheet = [&](const boundBox& sb)
                {
                    for (direction k = 0; k < 3; ++k)
                    {
                        const scalar ov = min(fHi[k], sb.max()[k]) - max(fLo[k], sb.min()[k]);
                        const scalar need = (fHi[k] - fLo[k] > planeTol) ? planeTol : -planeTol;
                        if (ov <= need) { return false; }
                    }
                    return true;
                };
                // a zone can hold several sheets in different planes (a merged
                // wall-baffle STL): a cap lies IN one of them, so it stays if
                // it is parallel to a sheet of its zone and on that sheet's
                // plane; stair-step left-overs parallel to another sheet of
                // the zone but off every plane go. On the plane is not enough:
                // the face has to lie on a sheet too -- one of its zone, or
                // any other sheet in that plane (a floor tile's stair-steps
                // around it lie on the raised floor and seal the floor's hole
                // for the tile). A sheet whose plane is a grid plane (DAML's
                // duct collar, y 0.6136) otherwise kept the stair-steps of the
                // TILTED expansion sheet of its zone that land on that plane
                // beside the collar, on no sheet at all -- a second wall under
                // the slope with a zero-thickness wedge between them.
                auto onAPlane = [&](const zonePlane& pl, const bool needSheet)
                {
                    if (mag(nn & pl.n) <= 0.999) { return false; }
                    if (mag((cc & pl.n) - pl.d) > pl.tol) { return false; }
                    return !needSheet || onSheet(pl.bb);
                };
                bool inPlane = false;
                bool inZonePlane = false;
                for (const auto& pl : planes)
                {
                    if (!onAPlane(pl, false)) { continue; }
                    inZonePlane = true;
                    if (onSheet(pl.bb)) { inPlane = true; break; }
                }
                if (inZonePlane && !inPlane)
                {
                    for (const auto& pl : allPlanes)
                    {
                        if (onAPlane(pl, true)) { inPlane = true; break; }
                    }
                }
                if (inPlane) { continue; }
                mm.zone[f] = -1;
                ++nStripped;
            }
            reduce(nStripped, sumOp<label>());
            return nStripped;
        };

        dcCartCutCells::cutMesh m;
        dcCartCutCells::fromBuilder(b2, grid, orig2, m);
        m.cellRank.assign(rank2.begin(), rank2.end());
        dbgDumpCells(m, "00assemble");
        dcCartCutCells::report rep;
        bool normalSolidCarveCompleted=false;
        dcCartCutCells::cutCarving(m, surfaces, cutSettings, rep);
        // This cutCells branch produces a fluid mesh, not a solid/material
        // region. The assignment follows successful completion of all shell
        // carving, not an environment authority or orig-alive anchor.
        normalSolidCarveCompleted=true;
        // (the cut counters count owned cells: summed over the ranks they
        // are the mesh's own, whatever the rank count)
        auto sumAll = [](label v) { return returnReduce(v, sumOp<label>()); };
        // Baffle faces whose master side the sheet could not decide
        // (baffleFaceSide == 0) keep the old fallback -- the face's owner, or
        // the rank order across a rank boundary -- which is exactly how a
        // panel ended up a patchwork of master and slave faces. Summed over
        // the ranks (every rank passes a list of the same length, one entry
        // per faceZone) and named per zone, so that cannot come back
        // silently. Collective: every rank calls it.
        auto warnUndecided = [&](labelList perZone, const char* where)
        {
            Pstream::listCombineReduce(perZone, plusEqOp<label>());
            forAll(perZone, z)
            {
                if (perZone[z] <= 0) { continue; }
                WarningInFunction
                    << where << ": " << perZone[z] << " face(s) of baffle zone "
                    << surfaces.faceZoneNames()[z] << " lie where its sheet"
                    << " cannot tell the master side (no sheet triangle of the"
                    << " zone nearby, a degenerate face, or a face standing"
                    << " across the sheet); they keep the fallback side"
                    << nl << endl;
            }
        };
        {
            const label nCut = sumAll(rep.nCarveCut);
            const label nStair = sumAll(rep.nCarveFallback);
            const label nDropped = sumAll(rep.nReadmitKilled);
            const label nCaps = sumAll(rep.nCapFaces);
            Info<< "Cut cells: carving — " << nCut << " cells clipped, "
                << nStair << " kept stair-step, "
                << nDropped << " re-admitted cells dropped again, "
                << nCaps << " cut faces"
                << "  (" << timer.timeIncrement() << " s)" << nl;
        }
        budgetCheck("cut-carving");
        dbgDumpCells(m, "01carving");
        if (cutSettings.cutBaffles)
        {
            const label caps0 = rep.nCapFaces;
            dcCartCutCells::cutBaffles(m, surfaces, cutSettings, rep);
            const label nSplit = sumAll(rep.nBaffleCut);
            const label nStair = sumAll(rep.nBaffleFallback);
            const label nFaces = sumAll(rep.nCapFaces - caps0);
            Info<< "Cut cells: baffles — " << nSplit << " cells split, "
                << nStair << " kept stair-step, "
                << nFaces << " baffle faces, "
                << sumAll(rep.nBaffleEdgeSplit) << " cells split at a sheet's free edge"
                << "  (" << timer.timeIncrement() << " s)" << nl;
            budgetCheck("cut-baffles");
        }
        dbgDumpCells(m, "02baffles");
        if (!skipStage("splitwall"))
        {
            // A WALL baffle's zone faces become its master/slave walls right
            // here, so its stair-step tags have to go FIRST: the late strip
            // (after the merges) only untags, and by then each off-plane
            // stair face is already a pair of wall faces. That left
            // Tri_angle's u17 rack partition meshed twice — once on its plane
            // (y 0.8001) and once 12.4 mm above it on the nearest grid plane
            // (y 0.8125), a floating second sheet the merges then had to
            // work around (2026-09-23). The coverage gate in cutBaffles has
            // already refused any non-perforated zone whose IN-PLANE faces
            // tile less than 99.5 % of the sheet, so the off-plane tags seal
            // nothing that is not sealed.
            // Every other zone loses its off-plane tags here too, before the
            // merges: a perforated blanking panel on a rack rotated 45 deg
            // left them on the two grid-plane legs of each 0.2 mm wedge its
            // diagonal cut off a hex corner, and a sliver walled by zone
            // faces on three sides has no host -- Tri_angle kept 47
            // zero-determinant cells and 901 volume-ratio faces, and the
            // panel read 1.0008 coverage. A perforated zone is porous, so an
            // untagged step opens nothing the sheet did not.
            if (!skipStage("strip"))
            {
                const label nWallStripped = stripOffPlaneStairTags(m, nullptr);
                if (nWallStripped)
                {
                    Info<< "Cut cells: baffles — dropped " << nWallStripped
                        << " stair-step faces that lie in no sheet plane,"
                        << " before the wall split and the merges" << nl;
                }
            }
            label nTurned = 0;
            labelList undecided;
            dcCartCutCells::splitBaffleZones
            (
                m, wallMasters, wallSlaves, surfaces, &nTurned, &undecided
            );
            nTurned = sumAll(nTurned);
            warnUndecided(undecided, "wall baffle split");
            if (nTurned)
            {
                Info<< "Cut cells: wall baffles — " << nTurned
                    << " face(s) turned to the master side their sheet's"
                    << " normal gives" << nl;
            }
        }
        dbgDumpCells(m, "03splitwall");
        if (!skipStage("relabel"))
        {
            dcCartCutCells::relabelCoplanarPatches(m, surfaces, cutSettings);
        }
        dbgDumpCells(m, "04relabel");
        // After the patches are final and before the sliver rounds: a thin
        // slab whose face changes patch mid-face has to be cut along that
        // seam, or checkMesh scores the narrow piece ~5.9 (see
        // splitSeamedThinCells). Merging cannot repair it -- no convex host.
        if (!skipStage("seamsplit"))
        {
            dcCartCutCells::splitSeamedThinCells(m, cutSettings, rep);
            budgetCheck("seam-split");
        }
        dbgDumpCells(m, "05seamsplit");
        // The plain-wall flag for mergeSmallCells' wedgeKill round, from the
        // patch list AS IT STANDS here: the wall-baffle master/slave pairs are
        // in it (splitBaffleZones above), the processor patches are in it and
        // are not walls, and nothing is appended until the mesh is written.
        boolList wallPatchNow(patchTypes.size(), false);
        forAll(patchTypes, pi)
        {
            wallPatchNow[pi] = (patchTypes[pi] == "wall");
        }
        {
            // mergeSmallCells + coalesceCoplanarFaces under a wall-clock budget:
            // see mergeBudgetSeconds. Both run on the worker so the budget covers
            // the whole silent stretch between the baffle-coverage gate and the
            // merge summary line.
            const double budget = mergeBudgetSeconds();
            if (budget > 0)
            {
                std::promise<void> done;
                auto finished = done.get_future();
                std::thread worker
                (
                    [&]()
                    {
                        try
                        {
                            if (!skipStage("merge"))
                            {
                                dcCartCutCells::mergeSmallCells(m, cutSettings, rep, &surfaces, &wallPatchNow);
                            }
                            if (!skipStage("coalesce"))
                            {
                                dcCartCutCells::coalesceCoplanarFaces(m, rep, myProc, &plainWallPatch);
                            }
                            done.set_value();
                        }
                        catch (...)
                        {
                            done.set_exception(std::current_exception());
                        }
                    }
                );
                if (finished.wait_for(std::chrono::duration<double>(budget))
                    == std::future_status::timeout)
                {
                    FatalErrorInFunction
                        << "the sliver merge (mergeSmallCells + "
                        << "coalesceCoplanarFaces) is still running after "
                        << budget << " s (DCCART_MERGE_TIMEOUT). That phase only "
                        << "goes this slow on a pathological sliver field: two "
                        << "surfaces a fraction of a millimetre apart, almost "
                        << "always a model that is not aligned - rows off their "
                        << "column line, or a containment baffle a hair short of "
                        << "the face it seals. Align the model, or raise "
                        << "DCCART_MERGE_TIMEOUT for a case known to be huge, "
                        << "rather than waiting this out."
                        << exit(FatalError);
                }
                finished.get();   // re-throw a worker-side error on this thread
                worker.join();
            }
            else
            {
                if (!skipStage("merge"))
                {
                    dcCartCutCells::mergeSmallCells(m, cutSettings, rep, &surfaces, &wallPatchNow);
                }
                if (!skipStage("coalesce"))
                {
                    dcCartCutCells::coalesceCoplanarFaces(m, rep, myProc, &plainWallPatch);
                }
            }
        }
        const label nPreSplit = sumAll(rep.nPreSplit);
        Info<< "Cut cells: merged " << rep.nMerged << " slivers (< "
            << cutSettings.minVolumeFraction << " of their hex); "
            << rep.nQualityMerged << " skewness repairs; "
            << nPreSplit << " cells pre-split at feature edges, "
            << rep.nPhantomJoined << " halves joined again; "
            << rep.nKilled << " extreme slivers (< " << cutSettings.killVolumeFraction
            << ") handed to the solid; "
            << rep.nDegenerateAbsorbed << " degenerate cells (< " << cutSettings.degenerateVolumeFraction
            << ") absorbed (" << rep.nDegenerateInverted
            << " with no host that keeps the face pyramids); skewed faces avoided: "
            << sumAll(rep.nSkewRefused) << " rejoins refused, "
            << sumAll(rep.nSkewRejected) << " sliver hosts rejected, "
            << sumAll(rep.nSkewRepairBelowLimit) << " repairs below the limit; "
            << rep.nMergeDeferred << " merges deferred, "
            << sumAll(rep.nMergeOutOfReach) << " hosts out of reach, "
            << sumAll(rep.nCornerClusters) << " corner-wedge clusters"
            << "  (" << timer.timeIncrement() << " s)" << nl;
        // (coalesceCoplanarFaces ran inside the budgeted block above)
        const label nAspectSplits = skipStage("refine")
            ? 0 : sumAll(dcCartCutCells::refineLongCells(m, myProc, maxAspectRatio));
        if (nAspectSplits) { Info<< "Cut cells: split " << nAspectSplits << " long corner cells for aspect ratio" << nl; }

        const label nGraded = skipStage("refine") ? 0 : sumAll(dcCartCutCells::gradeBesideSmallCells(m, myProc));
        if (nGraded) { Info<< "Cut cells: split " << nGraded << " large cells beside cells too small for them" << nl; }

        if (getenv("DCCART_REFINE_THIN_BOXES") != nullptr)
        {
            boolList protectedPatch(patchTypes.size(),false);
            forAll(patchTypes,pi)
            {
                const word& type=patchTypes[pi];
                protectedPatch[pi]=type.find("cyclic")==0 || type.find("processor")==0
                    || type.find("mapped")==0 || type.find("AMI")!=std::string::npos;
            }
            dcCartCutCells::refineThinAxisBoxes(m,protectedPatch,myProc);
            budgetCheck("thin-axis-box-experiment");
        }
        if (!skipStage("stitch")) { dcCartCutCells::stitchOpenCellEdges(m); }
        if (!skipStage("relabel"))
        {
            // after the merges: a sliver joined into its host hands the host a
            // face on another box's plane under the sliver's patch
            const label nRelabel = sumAll(dcCartCutCells::relabelCoplanarPatches(m, surfaces, cutSettings));
            if (nRelabel)
            {
                Info<< "Cut cells: " << nRelabel << " boundary faces renamed after the facet they lie in" << nl;
            }
        }
        dbgDumpCells(m, "08preghost");
        // Both sides of a baffle on one rank, again after the merges: the
        // ownership pass kept every baffle off the slab interfaces, but a
        // sliver merged into a host across an interface keeps the host's
        // rank, and a baffle face of the sliver can then face the other
        // rank's cell (Alibaba-sige: 12 single-face processorCyclic pairs on
        // perforated blanking panels).
        if (Pstream::parRun())
        {
            label nRefused = 0;
            const label nMovedCells = dcCartCutCells::gatherBaffleCells(m, nRefused);
            if (nMovedCells || nRefused)
            {
                Info<< "Ownership: " << nMovedCells << " cells moved so that no baffle"
                    << " face lies between two ranks, " << nRefused
                    << " refused (their new rank does not hold them whole)" << nl;
            }
        }
        if (!skipStage("dropghost"))
        {
            // (one rank: there is no halo, and nothing is dropped)
            std::vector<label> procOf(procPatch.begin(), procPatch.end());
            dcCartCutCells::dropGhostCells(m, myProc, procOf);
        }
        dbgDumpCells(m, "09dropghost");
        const label nCoalesced = sumAll(rep.nCoalesced);
        const label nTagCoalesced = sumAll(rep.nTagCoalesced);
        Info<< "Cut cells: joined " << nCoalesced << " coplanar face pieces"
            << " (" << nTagCoalesced << " of them a sliver absorbed across a"
            << " patch / zone difference)"
            << "  (" << timer.timeIncrement() << " s)" << nl;

        if (!skipStage("strip"))
        {
            // Stair-step left-overs: where an earlier pass cut the leaf
            // column at a sheet's rim, the half left under the sheet's bounds
            // never became eligible in the sheet pass and keeps the builder's
            // stair-step tags — axis-aligned steps that lie in no sheet plane,
            // a saw on the rim of the region the sheets enclose. They are
            // untagged before the wall split (see stripOffPlaneStairTags
            // there); this second pass catches a tag a merge carried onto a
            // face off the plane, so the faceZone keeps only faces in a
            // sheet plane. Faces of an axis-aligned sheet are in its plane and
            // stay.
            const label nStripped = stripOffPlaneStairTags(m, nullptr);
            if (nStripped)
            {
                Info<< "Cut cells: baffles — dropped " << nStripped
                    << " stair-step faces that lie in no sheet plane"
                    << "  (" << timer.timeIncrement() << " s)" << nl;
            }
        }
        dbgDumpCells(m, "10strip");

        // Terminal repair for the thin wall slivers the strict merge rounds
        // strand -- a wall-adjacent cell below wallSliverVolumeFraction of its
        // hex whose own wall face is closer to its centre than
        // minWallCellDistance. Merged away from the wall when a host can lift
        // it clear, handed to the solid when none can. Runs here, after the
        // halo is gone and before the late stitch, so the open-cell and
        // micro-face repairs still see whatever it leaves behind.
        if (!skipStage("thinwall"))
        {
            // the patches of type plain wall, from the FINAL list: baffle and
            // processor patches are appended after plainWallPatch was built, so
            // that one is too short to index by the ids the mesh now carries
            boolList wallPatchNow(patchTypes.size(), false);
            forAll(patchTypes, pi)
            {
                wallPatchNow[pi] = (patchTypes[pi] == "wall");
            }
            dcCartCutCells::repairThinWallCells
            (
                m, cutSettings, procPatch, wallPatchNow, rep
            );
            timer.timeIncrement();
        }
        dbgDumpCells(m, "11thinwall");

        // Stitch once more, last: the strip pass and the coincident-point
        // merge that follow the first stitch both rewrite face loops, and a
        // hanging vertex reintroduced there is exactly the "open cell"
        // (Sigma Sf != 0) checkMesh fails on -- Shinaide's five were a point
        // on the ACU-front seam x=5.0407381 sitting on an edge 5.0347333 ->
        // 5.0764 that one face of the cell did not carry.
        if (!skipStage("stitch"))
        {
            const label nLateStitch = sumAll(dcCartCutCells::stitchOpenCellEdges(m));
            if (nLateStitch)
            {
                Info<< "Cut mesh: stitched " << nLateStitch
                    << " faces with a hanging vertex after the strip pass" << nl;
            }
            dbgDumpCells(m, "12stitch");
            // The mirror repair, for the corner a clip left in a face the
            // cell's other faces do not carry (see the definition): last for
            // the same reason, because every pass above rewrites face loops.
            const label nDropped = sumAll
            (
                dcCartCutCells::dropHangingFaceCorners(m, procPatch)
            );
            if (nDropped)
            {
                Info<< "Cut mesh: dropped " << nDropped
                    << " hanging face corner(s) whose cell did not close"
                    << nl;
            }
            dbgDumpCells(m, "13dropcorner");
            // And the micro face that corner was holding up: a surface that
            // dips a fraction of a millimetre below a plane it otherwise lies
            // in leaves one, and checkMesh scores it in the tens because it
            // sits at the far corner of its cell. Runs here, not before the
            // merges, because the corner above has to go first -- until it
            // does, the micro piece is part of a face too big to see. Only
            // cells ALREADY over the skewness limit are touched, so a mesh
            // that passes keeps every point it had.
            if (!skipStage("microcollapse"))
            {
                const label nMicro = sumAll
                (
                    dcCartCutCells::collapseMicroFacePoints
                    (
                        m, cutSettings, procPatch
                    )
                );
                if (nMicro)
                {
                    Info<< "Cut mesh: moved " << nMicro
                        << " cut point(s) off a sub-cell surface dip" << nl;
                }
            }
            dbgDumpCells(m, "14micro");
        }

        // ---- the flow-through baffle pairs, written here -----------------
        // splitBaffleZones turns every remaining internal face of a cyclic
        // baffle zone into the master/slave pair createBaffles used to build
        // from the same faceZone: same patch names, same order (slave copies
        // are appended in ascending master order, so face k of one patch is
        // the partner of face k of the other, which is what a cyclic pair
        // requires), same types. It runs here -- after dropGhostCells and the
        // strip pass -- so the only faces it can see are ones whose TWO cells
        // this rank owns. A pair the slab split separated is already a
        // processor face and is routed below instead; that is precisely the
        // case createBaffles cannot handle ("cannot turn processor faces into
        // cyclic faces") and the reason the pipeline needed a redistributePar
        // pass first.
        // LAST, after every pass that rewrites a face loop: the late stitch
        // inserts vertices and the hanging-corner / micro-face repairs trim
        // them, and any of those applied to one side of a pair but not the
        // other would leave master[k] and slave[k] no longer the same polygon
        // -- which is what a cyclic pair is, and what coincidentFullMatch then
        // refuses to match. createBaffles had this for free by being a
        // separate utility run on the finished mesh.
        if (splitBaffles && !skipStage("bafflesplit"))
        {
            label nTurned = 0;
            labelList undecided;
            const label nSplit = sumAll
            (
                dcCartCutCells::splitBaffleZones
                (
                    m, cycMasters, cycSlaves, surfaces, &nTurned, &undecided
                )
            );
            nTurned = sumAll(nTurned);
            warnUndecided(undecided, "baffle split");
            // The rank-boundary faces below decide their side the same way;
            // those the geometry cannot tell fall back to the rank order.
            labelList undecidedRouted(surfaces.faceZoneNames().size(), 0);
            // decomposePar splits a cyclic pair across ranks into
            // procBoundary<me>to<nbr>through<zone>_{master,slave} of type
            // processorCyclic, each referring back to the local (possibly
            // empty) cyclic patch. Which rank holds the master side is the
            // same geometric question splitBaffleZones answers
            // (baffleFaceSide): this rank's face points out of its own cell,
            // so it is the master when that face points the master's way.
            // Both ranks see the same polygon from opposite sides and reach
            // opposite answers without exchanging anything, and the answer
            // does not depend on the rank numbering, so a rank boundary
            // through a panel cannot hand one half of it the other
            // orientation. (It used to be "the lower rank keeps the master
            // side", which was right only where the face already happened to
            // point the master's way.) Only a face standing across its sheet,
            // where the geometry cannot tell, falls back to the rank order.
            // toPolyMesh orders both sides by the face centre, the same key
            // that already matches plain processor faces.
            std::vector<label> rankOfPatch(patchNames.size(), -1);
            for (label s = 0; s < nProcs; ++s)
            {
                if (procPatch[s] >= 0 && procPatch[s] < label(rankOfPatch.size()))
                {
                    rankOfPatch[procPatch[s]] = s;
                }
            }
            // One rank can hold the master side of some faces of a zone and
            // the slave side of others (a zone of two panels facing opposite
            // ways, both cut by the same rank boundary), so a processorCyclic
            // patch is keyed by (neighbour, zone, which side the LOWER rank
            // holds) -- the same key on both ranks. The patches are created
            // in that key's order, not in face order: polyBoundaryMesh
            // exchanges processor geometry through PstreamBuffers, which
            // reads the patches to one neighbour back in patch order, so the
            // two ranks must list them in the same order.
            using procCyclicKey = std::tuple<label, label, bool>;
            std::vector<std::pair<label, procCyclicKey>> cyclicFaces;
            label nRouted = 0, nProcCyclic = 0;
            for (label f = 0; f < label(m.faces.size()); ++f)
            {
                if (!m.faceAlive[f] || m.neighbour[f] >= 0) { continue; }
                const label z = m.zone[f];
                if (z < 0 || z >= cycMasters.size() || cycMasters[z] < 0) { continue; }
                const label p = m.patch[f];
                if (p < 0 || p >= label(rankOfPatch.size())) { continue; }
                const label nbr = rankOfPatch[p];
                if (nbr < 0 || nbr == myProc) { continue; }
                const label side = dcCartCutCells::baffleFaceSide(m, f, surfaces);
                const bool masterHere = side ? (side > 0) : (myProc < nbr);
                if (!side && z < undecidedRouted.size()) { ++undecidedRouted[z]; }
                // A wall pair needs no coupling: whichever side this rank
                // holds is just a wall face of that side's patch.
                if (patchTypes[cycMasters[z]] != "cyclic")
                {
                    m.patch[f] = masterHere ? cycMasters[z] : cycSlaves[z];
                    ++nRouted;
                    continue;
                }
                const bool lowerHoldsMaster =
                    (myProc < nbr) ? masterHere : !masterHere;
                cyclicFaces.emplace_back
                (
                    f, procCyclicKey(nbr, z, lowerHoldsMaster)
                );
            }
            std::map<procCyclicKey, label> procCyclicOf;
            for (const auto& fk : cyclicFaces) { procCyclicOf.emplace(fk.second, -1); }
            for (auto& kp : procCyclicOf)
            {
                const label nbr = std::get<0>(kp.first);
                const label z = std::get<1>(kp.first);
                const bool masterHere =
                    (myProc < nbr) ? std::get<2>(kp.first) : !std::get<2>(kp.first);
                const word refer = surfaces.faceZoneNames()[z]
                    + (masterHere ? "_master" : "_slave");
                kp.second = patchNames.size();
                patchNames.append
                (
                    word
                    (
                        "procBoundary" + Foam::name(myProc) + "to"
                      + Foam::name(nbr) + "through" + refer
                    )
                );
                patchTypes.append(word("processorCyclic"));
                patchNeighbours.append(word::null);
                patchGroups.append(word::null);
                ++nProcCyclic;
            }
            for (const auto& fk : cyclicFaces)
            {
                m.patch[fk.first] = procCyclicOf[fk.second];
                ++nRouted;
            }
            if (nTurned)
            {
                Info<< "Cut mesh: baffles — " << nTurned
                    << " face(s) turned to the master side their sheet's"
                    << " normal gives" << nl;
            }
            // (over all ranks: a face and its partner count once each, and
            // so does each rank's half of a processorCyclic pair)
            warnUndecided(undecidedRouted, "rank-boundary baffle faces");
            nRouted = sumAll(nRouted);
            nProcCyclic = sumAll(nProcCyclic);
            if (nSplit || nRouted)
            {
                // (name() returns a string, which v2412 writes quoted, so the
                // counts go in as labels)
                Info<< "Cut mesh: baffles — wrote " << nSplit
                    << " master/slave pair(s) directly";
                if (nRouted)
                {
                    Info<< ", " << nRouted << " face(s) across a rank boundary"
                        << " into " << nProcCyclic
                        << " processorCyclic patch(es)";
                }
                Info<< nl;
            }
        }


        // The last word on quality: measure the mesh the way checkMesh will
        // and repair what it would refuse (see repairSkewFaces). Runs here,
        // after the baffle pairs exist, because a wall baffle's pieces only
        // become faces of ONE cell -- joinable -- once they are split. A
        // no-op when nothing is over the limit, so a mesh checkMesh already
        // passes is written bit-identically.
        dbgDumpCells(m, "15bafflesplit");
        if (!skipStage("skewrepair"))
        {
            // checkMesh measures a coupled patch's faces against the cell
            // centre on the other side (polyMeshTools::faceSkewness swaps
            // them), not against a mirror image, so they are neither scored
            // nor repaired here: one rank cannot see the other side.
            boolList coupledPatch(patchTypes.size(), false);
            forAll(patchTypes, pi)
            {
                const word& pt = patchTypes[pi];
                coupledPatch[pi] =
                    (
                       pt == "cyclic"
                    || pt == "cyclicAMI"
                    || pt == "processor"
                    || pt == "processorCyclic"
                    );
            }
            const label nSkewFixed = dcCartCutCells::repairSkewFaces
            (
                m, cutSettings, procPatch, coupledPatch
            );
            dbgDumpCells(m, "16skewrepair");
            // the repair's own line is already out; this is its cost, and a
            // mesh checkMesh would have passed reports neither
            const scalar skewSeconds = timer.timeIncrement();
            if (nSkewFixed)
            {
                Info<< "  (" << skewSeconds << " s)" << nl;
            }
        }
        if (!skipStage("weightrepair"))
        {
            const label nWeight = sumAll(dcCartCutCells::repairLowWeightFaces(m, cutSettings));
            dbgDumpCells(m, "17weightrepair");
            Info<< "Cut mesh: interpolation-weight repair -- united " << nWeight
                << " cell pair(s) across a face below 0.05  (" << timer.timeIncrement() << " s)" << nl;
        }


        const char* exactZeroPairValue=getenv("DCCART_EXACT_ZERO_BOUNDARY_PAIRS");
        if(exactZeroPairValue && std::strcmp(exactZeroPairValue,"1")==0)
        {
            boolList plainWall(patchTypes.size(),false),protectedPatch(patchTypes.size(),true);
            forAll(patchTypes,pi)
            {
                plainWall[pi]=patchTypes[pi]=="wall";
                protectedPatch[pi]=patchTypes[pi]!="wall"&&patchTypes[pi]!="patch";
            }
            const char* untouchedValue=getenv("DCCART_EXACT_ZERO_UNTOUCHED_PROCESSOR");
            const wordList* originalTypes=untouchedValue&&std::strcmp(untouchedValue,"1")==0
                ? &patchTypes : nullptr;
            dcCartCutCells::removeExactZeroBoundaryPairs(m,plainWall,protectedPatch,originalTypes);
            budgetCheck("exact-zero-boundary-pair-repair");
        }

        if (getenv("DCCART_REDUNDANT_EDGE_POINTS") != nullptr)
        {
            boolList protectedPatch(patchTypes.size(),false);
            forAll(patchTypes,pi)
            {
                const word& type=patchTypes[pi];
                protectedPatch[pi]=type.find("cyclic")==0
                 || type.find("processor")==0 || type.find("mapped")==0
                 || type.find("AMI")!=std::string::npos;
            }
            dcCartCutCells::removeRedundantEdgePoints(m,protectedPatch);
            budgetCheck("redundant-edge-point-experiment");
        }

        if (getenv("DCCART_TRANSITION_ROOF_AUDIT") != nullptr)
        {
            boolList protectedPatch(patchTypes.size(),false);
            forAll(patchTypes,pi)
            {
                const word& type=patchTypes[pi];
                protectedPatch[pi]=type.find("cyclic")==0
                 || type.find("processor")==0 || type.find("mapped")==0
                 || type.find("AMI")!=std::string::npos;
            }
            dcCartCutCells::auditRegularTransitionRoofs(m,protectedPatch);
            budgetCheck("transition-roof-readonly-audit");
        }

        // Copy only real ordinary processor-patch producer relations. Neither
        // patch names nor geometric matches infer a remote-rank identity.
        m.processorPatchPeers.assign(patchTypes.size(),-1);
        forAll(procPatch,peer)
        {
            const label patch=procPatch[peer];
            if(peer==myProc||peer<0||peer>=nProcs||patch<0||patch>=patchTypes.size()
                ||patchTypes[patch]!="processor")continue;
            label& current=m.processorPatchPeers[patch];
            if(current==-1)current=peer;
            else current=-2; // repeated/ambiguous producer slots remain unknown
        }
        // Copy the real splitBaffleZones producer relation before source capture.
        // Neither names nor coordinate coincidence establishes a paired role.
        m.physicalWallPatchPairs.assign(wallMasters.size(),{{-1,-1}});
        const auto actualWallPair=[&](label master,label slave)
        {
            return master>=0&&slave>=0&&master!=slave
                &&master<patchTypes.size()&&slave<patchTypes.size()
                &&patchTypes[master]=="wall"&&patchTypes[slave]=="wall";
        };
        forAll(wallMasters,zone)
        {
            const label wm=wallMasters[zone],ws=zone<wallSlaves.size()?wallSlaves[zone]:-1;
            const label cm=zone<cycMasters.size()?cycMasters[zone]:-1;
            const label cs=zone<cycSlaves.size()?cycSlaves[zone]:-1;
            // splitBaffleZones also produces real wall pairs through the cyc
            // arrays when the runner explicitly declares this zone a wall.
            // A true cyclic/processorCyclic pair never receives wall authority.
            const bool declaredWall=splitBaffles&&zone<surfaces.faceZoneNames().size()
                &&wallPairZones.found(surfaces.faceZoneNames()[zone]);
            const bool wallCandidate=wm>=0||ws>=0;
            const bool declaredCycCandidate=declaredWall&&(cm>=0||cs>=0);
            if(wallCandidate&&declaredCycCandidate)continue; // ambiguous producers stay unknown
            if(wallCandidate&&actualWallPair(wm,ws))m.physicalWallPatchPairs[zone]={{wm,ws}};
            else if(declaredCycCandidate&&actualWallPair(cm,cs))m.physicalWallPatchPairs[zone]={{cm,cs}};
        }
        const char* canonicalSkippedStages=getenv("DCCART_SKIP");
        if (getenv("DCCART_CONFORMING_CONES") != nullptr && normalSolidCarveCompleted
            && (!canonicalSkippedStages || !*canonicalSkippedStages))
        {
            // Reconstruct only known ordinary physical wall polygons on the
            // same frozen canonical OF-FC fan, BEFORE current source capture.
            const auto canonicalRoles=dcCartPostCarveCanonicalWallFan::roles
                (surfaces,*identitySource,patchTypes,wallMasters,wallSlaves,
                 cycMasters,cycSlaves,cutSettings.workAvailable);
            // Preserve the actual positive ordinary carved-wall producer role
            // independently of whether canonical fan construction is needed.
            m.physicalPlainWallRoles=canonicalRoles;
            m.physicalCompositeBCRoles=dcCartPostCarveCanonicalWallFan::roles
                (surfaces,*identitySource,patchTypes,wallMasters,wallSlaves,cycMasters,cycSlaves,
                 cutSettings.workAvailable,true);
            // A scalar append-patch role is valid only for a uniquely bound
            // original region of this actual declared wall-pair producer.
            // Multi-region zones instead keep per-native-source-face lineage.
            dcCartPostCarveCanonicalWallFan::bridgeDeclaredWallPairRoles
                (surfaces,m.physicalCompositeBCRoles,patchTypes,m.physicalWallPatchPairs,m.physicalCompositeBCRoles);
            m.physicalCompositeSourceBCRoles.clear();m.physicalCompositeSourceBCRejected.clear();
            for(const auto& item:m.physicalBaffleRegionBirths)
            {
                const label f=item.first,region=item.second.region;
                // Every recorded birth defaults to a frozen source rejection;
                // only all positive actual-region and wall-pair bindings clear it.
                if(f>=0&&std::size_t(f)<m.faces.size())m.physicalCompositeSourceBCRejected.insert(f);
                if(!dcCartPostCarveCanonicalWallFan::baffleRegionBirthCurrent(m,f)||region<0)continue;
                if(std::size_t(region)>=m.physicalCompositeBCRoles.size()
                    ||!m.physicalCompositeBCRoles[region].known||f<0||std::size_t(f)>=m.patch.size()
                    ||m.neighbour[f]!=-1||m.zone[f]<0||std::size_t(m.zone[f])>=m.physicalWallPatchPairs.size())continue;
                const auto pair=m.physicalWallPatchPairs[m.zone[f]];
                if(pair[0]<0||pair[1]<0||pair[0]==pair[1]||(m.patch[f]!=pair[0]&&m.patch[f]!=pair[1]))continue;
                // Exact current creator lineage, with strict original metadata.
                // This remains only BC qualification; collectCompleteOrigins,
                // exact per-source coverage/VM/Stock/peer are unchanged.
                const auto inserted=m.physicalCompositeSourceBCRoles.emplace(f,m.physicalCompositeBCRoles[region]);
                if(inserted.second)m.physicalCompositeSourceBCRejected.erase(f);
            }
            const char* inventoryValue=std::getenv("DCCART_CANONICAL_FAN_INVENTORY");
            const bool inventoryRequested=inventoryValue&&std::string(inventoryValue)=="1";
            auto canonicalFan=dcCartPostCarveCanonicalWallFan::apply
                (m,canonicalRoles,myProc,cutSettings.workAvailable,inventoryRequested);
            Pout<<"Upstream canonical plain-wall FC fan: rank "<<myProc
                <<", candidates "<<canonicalFan.candidates<<", owners "<<canonicalFan.parents
                <<", refused owners "<<canonicalFan.refused<<", new points/faces "
                <<canonicalFan.newPoints<<'/'<<canonicalFan.newFaces
                <<", published "<<canonicalFan.published<<", reason "<<canonicalFan.reason
                <<", separate OF-centre correction "<<canonicalFan.maximumFoamCentreChange
                <<"; source snapshot and stock quality remain mandatory"<<endl;
            if(inventoryRequested)
            {
                // Diagnostic only, immediately after the actual fan publication.
                // Never reconstruct an original ring from later triangles.
                std::FILE* inventoryFile=nullptr;bool inventoryIo=false;
                bool inventoryComplete=false,inventoryOverflow=false,inventoryClock=false;
                std::size_t inventoryWritten=0,inventoryBytes=0;
                std::string inventoryPath;
                try
                {
                    const std::string privateRoot="/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/server-component-finalization-private-20261008/";
                    const std::string localCase=runTime.path().c_str();
                    const bool privateCase=localCase.compare(0,privateRoot.size(),privateRoot)==0
                        &&localCase.find("/../")==std::string::npos&&localCase.find("/./")==std::string::npos
                        &&(localCase.size()<3||localCase.compare(localCase.size()-3,3,"/..")!=0)
                        &&(localCase.size()<2||localCase.compare(localCase.size()-2,2,"/.")!=0);
                    if(privateCase&&cutSettings.workAvailable())
                    {
                        inventoryPath=localCase+"/canonical-wall-fan-inventory-rank"+std::to_string(myProc)+".jsonl";
                        const int descriptor=::open(inventoryPath.c_str(),O_WRONLY|O_CREAT|O_EXCL,0600);
                        if(descriptor>=0)
                        {
                            inventoryFile=::fdopen(descriptor,"wb");
                            if(!inventoryFile){::close(descriptor);inventoryIo=true;}
                        }
                        else inventoryIo=true;
                    }
                    else inventoryClock=!cutSettings.workAvailable();
                    const auto jsonString=[](const std::string& value)
                    {
                        std::string escaped;escaped.push_back('"');
                        const char* hex="0123456789abcdef";
                        for(unsigned char c:value)
                        {
                            if(c=='"'||c=='\\'){escaped.push_back('\\');escaped.push_back(char(c));}
                            else if(c<0x20){escaped+="\\u00";escaped.push_back(hex[c>>4]);escaped.push_back(hex[c&15]);}
                            else escaped.push_back(char(c));
                        }
                        escaped.push_back('"');return escaped;
                    };
                    const auto emit=[&](const std::string& line)
                    {
                        if(!inventoryFile||line.size()>65536||inventoryBytes+line.size()+1>8*1024*1024)
                        {inventoryOverflow=true;return false;}
                        if(std::fwrite(line.data(),1,line.size(),inventoryFile)!=line.size()
                            ||std::fputc('\n',inventoryFile)==EOF){inventoryIo=true;return false;}
                        inventoryBytes+=line.size()+1;return true;
                    };
                    if(inventoryFile)
                    {
                        const auto& observed=canonicalFan.inventory;
                        std::ostringstream header;
                        header<<"{\"schema\":\"native-canonical-wall-fan-inventory/v1\",\"kind\":\"header\",\"rank\":"<<myProc
                            <<",\"stage\":\"post-carve-canonical-fan-before-source-snapshot\",\"published\":"<<(canonicalFan.published?"true":"false")
                            <<",\"beforePointCount\":"<<observed.beforePoints<<",\"beforeFaceCount\":"<<observed.beforeFaces
                            <<",\"beforeCellCount\":"<<observed.beforeCells<<",\"afterPointCount\":"<<m.points.size()
                            <<",\"afterFaceCount\":"<<m.faces.size()<<",\"afterCellCount\":"<<m.nCells
                            <<",\"observedExtractionEpoch\":"<<observed.extractionEpoch
                            <<",\"observedPreparedBirthGenerationBefore\":\""<<observed.generationBefore
                            <<"\",\"observedPreparedBirthGenerationAfter\":\""<<m.preparedBirth.generation
                            <<"\",\"expectedRecords\":"<<canonicalFan.records.size()
                            <<",\"nativeRepresentationEquivalent\":false,\"sourcePermission\":false,\"publicationPermission\":false}";
                        bool good=emit(header.str())&&canonicalFan.published&&observed.complete
                            &&observed.records.size()==canonicalFan.records.size();
                        inventoryOverflow=inventoryOverflow||observed.overflow;
                        inventoryClock=inventoryClock||observed.clock;
                        for(std::size_t i=0;good&&i<canonicalFan.records.size();++i)
                        {
                            if(!cutSettings.workAvailable()){inventoryClock=true;good=false;break;}
                            const auto& r=canonicalFan.records[i];const auto& original=observed.records[i];
                            if(original.originalRing.size()<3||original.originalRing.size()>64
                                ||original.originalRing.size()!=original.originalPointBits.size()
                                ||r.actualFaces.size()!=original.originalRing.size()
                                ||original.hub<0||std::size_t(original.hub)>=m.points.size()
                                ||dcCartPostCarveCanonicalWallFan::bits(m.points[original.hub])!=original.hubBits
                                ||r.role.entity.size()>1024||r.role.region.size()>1024)
                            {good=false;break;}
                            for(std::size_t j=0;good&&j<original.originalRing.size();++j)
                            {
                                const label p=original.originalRing[j],f=r.actualFaces[j];
                                if(p<0||std::size_t(p)>=m.points.size()
                                    ||dcCartPostCarveCanonicalWallFan::bits(m.points[p])!=original.originalPointBits[j]
                                    ||f<0||std::size_t(f)>=m.faces.size()||!m.faceAlive[f]
                                    ||m.owner[f]!=r.parent||m.neighbour[f]!=-1||m.patch[f]!=r.patch||m.zone[f]!=r.zone
                                    ||m.faces[f]!=std::vector<label>{p,original.originalRing[(j+1)%original.originalRing.size()],original.hub})good=false;
                            }
                            if(!good)break;
                            std::ostringstream row;
                            row<<"{\"kind\":\"fan\",\"ordinal\":"<<i<<",\"originalNativeFace\":"<<r.original
                                <<",\"parent\":"<<r.parent<<",\"patch\":"<<r.patch<<",\"zone\":"<<r.zone
                                <<",\"sourceBindingGranularity\":\"patch-region-facet-inventory\",\"sourceFacetUid\":null"
                                <<",\"roleKnown\":"<<(r.role.known?"true":"false")<<",\"sourceEntityUid\":"<<jsonString(r.role.entity)
                                <<",\"sourceRegionUid\":"<<jsonString(r.role.region)<<",\"sourceRegionSha256\":"<<jsonString(r.role.sha)
                                <<",\"operation\":"<<jsonString(r.role.operation)<<",\"hub\":{\"id\":"<<original.hub<<",\"bits\":[";
                            for(unsigned k=0;k<3;++k){if(k)row<<',';row<<'"'<<original.hubBits[k]<<'"';}row<<"]},\"originalRing\":[";
                            for(std::size_t j=0;j<original.originalRing.size();++j){if(j)row<<',';row<<original.originalRing[j];}
                            row<<"],\"originalPoints\":[";
                            for(std::size_t j=0;j<original.originalRing.size();++j)
                            {
                                if(j)row<<',';row<<"{\"id\":"<<original.originalRing[j]<<",\"bits\":[";
                                for(unsigned k=0;k<3;++k){if(k)row<<',';row<<'"'<<original.originalPointBits[j][k]<<'"';}row<<"]}";
                            }
                            row<<"],\"actualFaces\":[";
                            for(std::size_t j=0;j<r.actualFaces.size();++j)
                            {
                                if(j)row<<',';const label f=r.actualFaces[j];
                                row<<"{\"native\":"<<f<<",\"owner\":"<<m.owner[f]<<",\"neighbour\":"<<m.neighbour[f]
                                    <<",\"patch\":"<<m.patch[f]<<",\"zone\":"<<m.zone[f]<<",\"ring\":[";
                                for(std::size_t k=0;k<m.faces[f].size();++k){if(k)row<<',';row<<m.faces[f][k];}row<<"]}";
                            }
                            row<<"],\"recordComplete\":true,\"sourcePermission\":false}";
                            if(!emit(row.str())){good=false;break;}++inventoryWritten;
                        }
                        inventoryComplete=good&&inventoryWritten==canonicalFan.records.size()
                            &&!inventoryOverflow&&!inventoryClock&&!observed.error;
                        std::ostringstream footer;
                        footer<<"{\"kind\":\"footer\",\"complete\":"<<(inventoryComplete?"true":"false")
                            <<",\"writtenRecords\":"<<inventoryWritten<<",\"captureError\":"<<(observed.error?"true":"false")
                            <<",\"overflow\":"<<(inventoryOverflow?"true":"false")<<",\"clock\":"<<(inventoryClock?"true":"false")
                            <<",\"sourcePermission\":false,\"publicationPermission\":false}";
                        if(!emit(footer.str()))inventoryComplete=false;
                        if(std::fflush(inventoryFile)!=0)inventoryIo=true;
                        if(std::fclose(inventoryFile)!=0)inventoryIo=true;inventoryFile=nullptr;
                        if(inventoryIo)inventoryComplete=false;
                    }
                }
                catch(...)
                {
                    inventoryComplete=false;inventoryIo=true;
                    if(inventoryFile){std::fclose(inventoryFile);inventoryFile=nullptr;}
                }
                try{Pout<<"Canonical fan inventory: rank "<<myProc<<", path "<<inventoryPath.c_str()
                    <<", complete "<<inventoryComplete<<", records "<<inventoryWritten
                    <<", overflow/clock/io "<<inventoryOverflow<<'/'<<inventoryClock<<'/'<<inventoryIo
                    <<"; observation only, native representation/source permission unknown"<<endl;}catch(...){}
            }
            // Own the actual production origin after the optional observer is
            // finished. This move neither reads observer JSON nor grants a role.
            if(canonicalFan.completed&&canonicalFan.published)
                m.canonicalPhysicalFanOrigins=std::move(canonicalFan.records);
            const auto intrinsicCleanup=dcCartPostFanIntrinsicChainCleanup::apply
                (m,canonicalRoles,patchTypes,myProc,cutSettings.workAvailable);
            Pout<<"Upstream fixed-hub intrinsic chain cleanup: rank "<<myProc
                <<", tested/refused "<<intrinsicCleanup.tested<<'/'<<intrinsicCleanup.refused
                <<", published cohorts/chains "<<intrinsicCleanup.cohorts<<'/'<<intrinsicCleanup.chains
                <<", unreferenced chain-point IDs/retired faces "<<intrinsicCleanup.removedPointIds<<'/'<<intrinsicCleanup.retiredFaces
                <<", short polygon tried/published "<<intrinsicCleanup.shortTried<<'/'<<intrinsicCleanup.shortPublished
                <<", completed/published "<<intrinsicCleanup.completed<<'/'<<intrinsicCleanup.published
                <<", reason "<<intrinsicCleanup.reason
                <<"; geometry producer only, current Source and normal strict quality remain mandatory"<<endl;
            budgetCheck("upstream-canonical-plain-wall-fan");
        }

        const char* requestedWValue=getenv("DCCART_CROSS_PARENT_WEIGHT_UNIONS");
        const label requestedW=requestedWValue && std::string(requestedWValue)=="1";
        const label minRequestedW=returnReduce(requestedW,minOp<label>());
        const label maxRequestedW=returnReduce(requestedW,maxOp<label>());
        if (minRequestedW!=maxRequestedW)
            FatalErrorInFunction << "cross-parent W enable differs across ranks; no mesh written"
                                 << exit(FatalError);
        // Agree before optional upstream work receives its finite-phase reserve.
        const label requestedPointRepair=dcCartPointRepairBudget::enabled
            (std::getenv("DCCART_SHARED_POINT_REPAIR"));
        const label minimumPointRepair=returnReduce(requestedPointRepair,minOp<label>());
        const label maximumPointRepair=returnReduce(requestedPointRepair,maxOp<label>());
        if (minimumPointRepair!=maximumPointRepair)
        {
            FatalErrorInFunction
                << "DCCART_SHARED_POINT_REPAIR must agree on every rank; no mesh written"
                << exit(FatalError);
        }
        dcCartCutCells::weightUnionAuthority weightAuthority;
        dcCartCutCells::completeCavityAuthority cavityAuthority;
        if (maxRequestedW)
        {
            // This remains the actual normal product clock during the
            // intervening old cone stage; it is never a fixed launcher grant.
            std::function<scalar()> remainingW=[&]() -> scalar
            {
                return dcCartPointRepairBudget::upstreamAllowance
                    (timeLimit,timer.elapsedTime(),maximumPointRepair!=0);
            };
            // The current normal-carved live cells are one real fluid domain,
            // before an immutable origin is captured. This is the same source
            // declaration/identity contract used below; origAlive is not material.
            const auto rawNecessary=dcCartPointRepairSourceContract::readLocal(meshDict);
            const auto rawMaterial=dcCartWeightUnionSourceContract::readLocal(meshDict);
            List<string> rawRows(Pstream::nProcs());
            rawRows[myProc]=rawNecessary.canonical+"\n"+rawMaterial.canonical;
            if(Pstream::parRun()){Pstream::gatherList(rawRows);Pstream::scatterList(rawRows);}
            const char* rawSkipped=getenv("DCCART_SKIP");
            bool homogeneousRaw=normalSolidCarveCompleted&&(!rawSkipped||!*rawSkipped)
                &&rawNecessary.valid&&rawNecessary.eligible&&rawMaterial.valid&&rawMaterial.eligible
                &&dcCartPointRepairSourceContract::exactRowsAgree(rawRows);
            if(homogeneousRaw)
            {
                const dictionary& absence=meshDict.subDict("dcdtuniPointRepairSourceRoleContract");
                const ITstream* raw=dcCartPointRepairSourceContract::primitive(absence,"rawSha256");
                const ITstream* roles=dcCartPointRepairSourceContract::primitive(absence,"rolesDigest");
                homogeneousRaw=raw&&roles
                    &&std::string((*raw)[0].stringToken())==rawMaterial.rawSha256
                    &&std::string((*roles)[0].stringToken())==rawMaterial.rolesDigest;
            }
            if(homogeneousRaw)
            {
                const dcCartRawUnionStockMetrics::Available currentSourceWork=[&]()
                {const scalar left=remainingW();return std::isfinite(left)&&left>0;};
                const auto keys=dcCartRawQualityClusterUnion::sourceKeys
                    (surfaces,*identitySource,patchNames,patchTypes,wallMasters,wallSlaves,
                     cycMasters,cycSlaves,domainOffset,currentSourceWork);
                dcCartCutCells::mergeRawQualityClusters
                    (m,patchTypes,keys,myProc,homogeneousRaw,remainingW);
            }
            // Snapshot setup is separately bounded and charged to the same
            // whole clock. The existing helper's four-second local cap remains.
            auto snapshotClock=std::make_shared<clockTime>();
            std::function<scalar()> remainingSnapshot=[remainingW,snapshotClock]() -> scalar
            {
                return std::max(scalar(0),std::min
                    (remainingW(),scalar(4)-snapshotClock->elapsedTime()));
            };
            // Unknown/skipped post-carve paths refuse, while every rank still
            // enters readAuthority's one fixed collective below.
            const char* skippedStages=getenv("DCCART_SKIP");
            const bool completedNormalFluidOnlyPath=normalSolidCarveCompleted
                && (!skippedStages || !*skippedStages);
            auto snapshot=dcCartPostCarveFluidOwnershipPreview::capture
                (m,meshDict,patchNames,patchTypes,surfaces.faceZoneNames(),
                 myProc,completedNormalFluidOnlyPath,remainingSnapshot);
            Pout<<"Normal W ownership snapshot: rank "<<myProc
                <<", completed normal path "<<completedNormalFluidOnlyPath
                <<", immutable snapshot complete "<<snapshot->complete
                <<", pre-cone cells/faces/points "<<m.nCells<<'/'<<m.faces.size()<<'/'<<m.points.size()
                <<", setup wall "<<snapshotClock->elapsedTime()<<" s; no quality acceptance implied"<<endl;
            auto ownership=dcCartPostCarveFluidOwnershipPreview::verifier(snapshot);
            std::function<dcCartCutCells::weightUnionOrigin(label)> sourceOrigin=
                [snapshot](label parent)
                { return dcCartPostCarveFluidOwnershipPreview::origin(snapshot,parent); };
            weightAuthority=dcCartWeightUnionSourceContract::readAuthority
                (meshDict,ownership,remainingW,sourceOrigin);
            cavityAuthority=dcCartCompleteCavityAuthority::read
                (meshDict,snapshot,weightAuthority,remainingW);
            Pout<<"Normal W material authority: rank "<<myProc
                <<", material gate "<<weightAuthority.globallyNoMaterialNoBulk
                <<"; individual complete origin and installed quality guards required"<<endl;
        }
        if (getenv("DCCART_CONFORMING_CONES") != nullptr)
        {
            dcCartCutCells::decomposeConformingCones
            (
                m,patchNames,patchTypes,patchNeighbours,myProc,
                runTime,surfaces.faceZoneNames(),patchGroups,weightAuthority,cavityAuthority,
                earliestPhysicalPointRepair,cutSettings.workAvailable
            );
            budgetCheck("conforming-mixed-cone-experiment");
        }

        // The census: the three numbers that separate a mesh the steady RAS
        // solve converges on from one it does not, printed on every run so a
        // regression shows up at mesh time instead of three days later at
        // solve time. Measured on this project --
        //   converges:  Base604  min wall dist 4.1e-5, min vol 6.8e-5 of its
        //               hex, min face area 1.0e-7, 0 tetrahedra
        //               DAML 09-11  min vol 2.4e-8 m3, min face area 2.1e-7,
        //               0 tetrahedra, eps field max 434, Ux -> 1.3e-3
        //   diverges:   DAML now   min wall dist 1.3e-4, min vol 1.4e-8 of its
        //               hex, min face area 5.8e-9, 39 tetrahedra, eps field
        //               max 3.9e11, k -> 1e-8, Ux oscillating at 0.35-0.5
        // NOTE min wall distance does NOT separate them: Base604 converges
        // with a wall face 4.1e-5 m from its own cell centre. It is reported
        // because epsilonWallFunction's eps = 2*k*nu/y^2 is quadratic in it
        // and it is the quantity to look at first when eps runs away, not
        // because a small value is by itself a defect -- so it warns and never
        // fails. The counts that do separate them are the tetrahedra and the
        // minimum volume fraction; checkMesh reports the first, this reports
        // the second.
        {
            boolList wallPatchNow(patchTypes.size(), false);
            forAll(patchTypes, pi)
            {
                wallPatchNow[pi] = (patchTypes[pi] == "wall");
            }
            dcCartCutCells::meshQualityCensus
            (
                m, procPatch, wallPatchNow,
                max(surfaces.firstLayerFloor(), scalar(1e-3)), rep
            );
        }
        Info<< "Cut mesh: quality floor — min wall distance "
            << rep.minWallDistance << " m (baffle faces "
            << rep.minBaffleDistance << " m, open boundary "
            << rep.minOpenDistance << " m), min cell volume "
            << rep.minVolumeFractionSeen << " of its hex, min face area "
            << rep.minFaceAreaSeen << " m2" << endl;
        // Thin cells are what the solver trips on: a slab cell's thickness
        // t = V / A_max is the y its wall function divides by and the
        // aspect its gradients see. Counted below 1 mm (the DAML sheet that
        // broke the steady solve was 0.5 mm) and below the first-layer floor
        // in force, attributed to the boundary patch each one touches. The
        // per-patch counts are gathered by NAME: past the physical prefix
        // every rank's patch list is its own (processor / processorCyclic
        // patches), so an index-wise combine is not even the same length.
        {
            List<Tuple2<word, label>> mine;
            forAll(rep.thinCellsPerPatch, pi)
            {
                if (rep.thinCellsPerPatch[pi] > 0 && pi < patchNames.size())
                {
                    mine.append(Tuple2<word, label>(patchNames[pi], rep.thinCellsPerPatch[pi]));
                }
            }
            List<List<Tuple2<word, label>>> allRanks(Pstream::nProcs());
            allRanks[Pstream::myProcNo()] = mine;
            Pstream::gatherList(allRanks);
            Info<< "Cut mesh: thin cells (t = V/Amax) — min t "
                << rep.minThickness << " m; " << rep.nThinCells1mm
                << " below 1 mm; " << rep.nThinCells << " below the "
                << rep.thinFloor << " m floor";
            if (rep.nThinCells && Pstream::master())
            {
                Info<< " (" << rep.nThinAtBaffle << " carry a baffle face, "
                    << rep.nThinInterior << " touch no boundary)";
                std::map<word, label> byName;
                for (const auto& ranked : allRanks)
                {
                    for (const auto& nc : ranked) { byName[nc.first()] += nc.second(); }
                }
                std::vector<std::pair<word, label>> order(byName.begin(), byName.end());
                std::stable_sort
                (
                    order.begin(), order.end(),
                    [](const std::pair<word, label>& a, const std::pair<word, label>& b)
                    {
                        return a.second > b.second;
                    }
                );
                label shown = 0;
                for (const auto& nc : order)
                {
                    if (shown >= 8) { break; }
                    Info<< (shown ? ", " : ": ") << nc.first.c_str() << " " << nc.second;
                    ++shown;
                }
            }
            Info<< endl;
        }
        if (getenv("DCCART_STRICT_WALLDIST") != nullptr
         && cutSettings.minWallCellDistance > 0
         && rep.minWallDistance < cutSettings.minWallCellDistance)
        {
            WarningInFunction
                << "min wall distance " << rep.minWallDistance << " m is below"
                << " the " << cutSettings.minWallCellDistance << " m floor."
                << " Not by itself a defect (Base604 converges at 4.1e-5), but"
                << " epsilonWallFunction's eps = 2*k*nu/y^2 is quadratic in it,"
                << " so this is the first thing to look at when eps runs away."
                << endl;
        }

        {
            // every face gets a tet decomposition checkMesh accepts
            boolList processorPatch(patchTypes.size(), false);
            forAll(patchTypes, pi) { processorPatch[pi] = patchTypes[pi].find("processor") == 0; }
            const label nTetSplit = returnReduce(dcCartCutCells::repairFaceTets(m, processorPatch), sumOp<label>());
            Info<< "Cut mesh: " << nTetSplit << " faces split for a valid tet decomposition"
                << " (non-convex, or no base vertex)" << nl;
        }
        surfaces.reportRegionQueryAudit();

        std::function<scalar()> remainingPointRepairSeconds;
        dcCartFiniteAdmissionObservation::JobRecord pointRepairAdmission
            (std::getenv("DCCART_FINITE_ADMISSION_OBSERVE"));
        if (maximumPointRepair)
        {
            // -timeLimit is the runner's remaining whole-job budget. The
            // prototype also caps its own total stage time. This reserve is
            // a refusal margin; the runner watchdog and full checkMesh remain
            // authoritative for the complete pipeline.
            remainingPointRepairSeconds=[&]() -> scalar
            {
                const auto elapsed=timer.elapsedTime(); // original single job-clock sample
                const auto allowed=dcCartPointRepairBudget::allowance(timeLimit,elapsed);
                if(pointRepairAdmission.sample(timeLimit,elapsed,allowed))
                    Pout<<pointRepairAdmission.json(Pstream::myProcNo())<<nl;
                return allowed;
            };
        }
        // Always use this run's dcCartMeshDict, never the optional
        // snappy/geometry identitySource fallback. The enable gate above
        // makes this fixed gather/scatter sequence common to every rank.
        const bool singletonSourceRoleEligible=maximumPointRepair
            ? dcCartPointRepairSourceContract::readEligible(meshDict) : false;
        meshPtr = dcCartCutCells::toPolyMesh
        (
            m, runTime, polyMesh::defaultRegion,
            patchNames, patchTypes, surfaces.faceZoneNames(),
            patchNeighbours, patchGroups,false,nullptr,remainingPointRepairSeconds,
            singletonSourceRoleEligible,
            recordSourceRegions ? &sourcePatchAddressMap : nullptr
        );
        if (getenv("DCCART_CONE_LITERAL_UNIONS") || maximumPointRepair)
        { budgetCheck("conforming-final-installed-writer"); }
        const label nCellsAll = sumAll(meshPtr->nCells());
        Info<< "Cut mesh: cells " << nCellsAll << " over " << nProcs << " rank(s); this rank " << meshPtr->nCells()
            << ", points " << meshPtr->nPoints()
            << ", faces " << meshPtr->nFaces()
            << " (internal " << meshPtr->nInternalFaces() << ")"
            << "  (" << timer.timeIncrement() << " s)" << nl;
        forAll(meshPtr->faceZones(), z)
        {
            Info<< "  faceZone " << meshPtr->faceZones()[z].name() << ": "
                << meshPtr->faceZones()[z].size() << " faces" << nl;
        }
    }

    // OpenFOAM can report Mesh OK for a globally empty polyMesh. Never write
    // that as a generated fluid mesh. Local empty partitions remain legal.
    const label finalGlobalCells = returnReduce(meshPtr->nCells(), sumOp<label>());
    if (finalGlobalCells <= 0)
    {
        FatalErrorInFunction
            << "No fluid cells remain in the final mesh. Check locationInMesh "
            << "and the input solid/domain geometry; an empty mesh cannot be solved."
            << exit(FatalError);
    }

    if (!args.found("overwrite"))
    {
        ++runTime;
    }
    meshPtr->setInstance(runTime.constant());
    if (!returnReduce(meshPtr->write(), andOp<bool>()))
    {
        FatalErrorInFunction
            << "Failed to write the generated mesh on every rank"
            << exit(FatalError);
    }

    if (identitySource->found("dcdtuniSourceRegions", keyType::LITERAL))
    {
        // Metadata only: native surface/region addresses and the actual written
        // boundary table are joined here, after every topology mutation.
        writeDcCartSourceRegions
        (
            runTime, *meshPtr, surfaces,
            identitySource->subDict("dcdtuniSourceRegions"), patchNames,
            wallMasters, wallSlaves, cycMasters, cycSlaves, sourcePatchAddressMap
        );
    }

    if (Pstream::parRun())
    {
        // createBaffles can split cyclic baffles locally, but cannot turn
        // processor faces into cyclic faces. Redistribution is needed only
        // when a zoned face actually crosses a processor interface. Count
        // the final, written mesh; both processor copies contribute so zero
        // is an unambiguous indication that the current partition is ready.
        label processorZoneFaceCopies = 0;
        const polyMesh& mesh = *meshPtr;
        // Per-zone tally, so a nonzero count says WHICH sheet a rank boundary
        // still cuts (see the cutCol nudge above) instead of only how many.
        labelList copiesByZone(mesh.faceZones().size(), 0);
        forAll(mesh.faceZones(), zi)
        {
            for (const label facei : mesh.faceZones()[zi])
            {
                if (facei < mesh.nInternalFaces()) { continue; }
                const label patchi = mesh.boundaryMesh().whichPatch(facei);
                // A processorCyclic face IS the split pair, one side per rank:
                // decomposePar makes the same thing when it divides a cyclic.
                // Only a zone face on a PLAIN processor patch still has both
                // its cells to bring together, which is what migrate.
                if (isA<processorPolyPatch>(mesh.boundaryMesh()[patchi])
                    && !isA<processorCyclicPolyPatch>(mesh.boundaryMesh()[patchi]))
                {
                    ++processorZoneFaceCopies;
                    ++copiesByZone[zi];
                }
            }
        }
        reduce(processorZoneFaceCopies, sumOp<label>());
        Pstream::listCombineGather(copiesByZone, plusEqOp<label>());
        Pstream::listCombineScatter(copiesByZone);
        if (Pstream::master() && processorZoneFaceCopies)
        {
            forAll(copiesByZone, zi)
            {
                if (copiesByZone[zi])
                {
                    Info<< "Processor faceZone copies: " << copiesByZone[zi]
                        << " in " << mesh.faceZones()[zi].name() << nl;
                }
            }
        }
        if (Pstream::master())
        {
            OFstream info
            (
                runTime.globalPath()/runTime.constant()/"dcCartPartitionInfo.json"
            );
            info<< "{\n  \"nProcs\": " << Pstream::nProcs()
                << ",\n  \"processorZoneFaceCopies\": "
                << processorZoneFaceCopies
                << ",\n  \"bafflesSplitByMesher\": "
                << (splitBaffles ? "true" : "false")
                << ",\n  \"globalLeaves\": " << nLeaves << "\n}\n";
            info.flush();
            if (!info.good())
            {
                FatalErrorInFunction
                    << "Failed to write dcCartPartitionInfo.json"
                    << exit(FatalError);
            }
        }
        Info<< "Processor faceZone copies: " << processorZoneFaceCopies
            << (processorZoneFaceCopies ? " (baffles need redistribution)" : " (baffles are local)")
            << nl;
    }

    // Self-calibration for the memory guard: every run measures its own peak
    // (VmHWM per rank, summed), next to what the law predicted for it.
    {
        scalar peakSum = std::max(0.0, peakRssBytes());
        scalar peakMax = peakSum;
        reduce(peakSum, sumOp<scalar>());
        reduce(peakMax, maxOp<scalar>());
        const label nRanksMem = Pstream::parRun() ? Pstream::nProcs() : 1;
        Info<< "Memory: peak RSS sum " << peakSum/1e9 << " GB (largest rank "
            << peakMax/1e9 << " GB) for " << nLeaves << " octree leaves on "
            << nRanksMem << " rank(s); guard estimate "
            << memEstimateBytes(nLeaves, nRanksMem)/1e9 << " GB" << nl;
    }

    dcCartFlushLegacyJournalObservation();
    Info<< "Written to " << meshPtr->instance()/meshPtr->meshDir()
        << "  (" << timer.timeIncrement() << " s)" << nl
        << "Total " << timer.elapsedTime() << " s" << nl;

    Info<< "\nEnd\n" << endl;
    return 0;
}
