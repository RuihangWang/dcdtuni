#ifndef dcCartRetainedPlanarBaseStockTets_H
#define dcCartRetainedPlanarBaseStockTets_H

#include "point.H"
#include "tetPointRef.H"
#include "polyMeshTetDecomposition.H"
#include <cmath>
#include <cstddef>
#include <functional>
#include <vector>

namespace Foam
{
namespace dcCartRetainedPlanarBaseStockTets
{

// Geometry-only eligibility, with caller-supplied actual OF face/cell centres.
// This does not grant source, neighbour, ownership, or final quality authority.
// A shared face must use one common ring index for BOTH endpoint anchors.
inline bool accept
(
    const std::vector<point>& ownerOrientedRing,
    const point& actualFaceCentre,
    const point& actualOwnerCentre,
    const point* actualNeighbourCentre,
    const std::function<bool()>& geometryAvailable,
    label* commonAnchor = nullptr
)
{
    if (commonAnchor) *commonAnchor = -1;
    const std::size_t n = ownerOrientedRing.size();
    // Same ring bound as the existing exact planar-outline admission.
    if (n < 3 || n > 64) return false;
    auto available = [&]() { return !geometryAvailable || geometryAvailable(); };
    auto finite = [](const point& p)
    {
        return std::isfinite(p.x()) && std::isfinite(p.y())
            && std::isfinite(p.z());
    };
    if (!available() || !finite(actualFaceCentre) || !finite(actualOwnerCentre)
        || (actualNeighbourCentre && !finite(*actualNeighbourCentre))) return false;
    for (const point& p : ownerOrientedRing)
        if (!available() || !finite(p)) return false;

    const scalar tol = polyMeshTetDecomposition::minTetQuality;
    if (!std::isfinite(tol) || tol < 0) return false;
    for (std::size_t j = 0; j < n; ++j)
    {
        if (!available()) return false;
        const point& a = ownerOrientedRing[j];
        const point& b = ownerOrientedRing[(j + 1) % n];
        const scalar own = tetPointRef
            (a, b, actualFaceCentre, actualOwnerCentre).quality();
        if (!std::isfinite(own) || own > -tol) return false;
        if (actualNeighbourCentre)
        {
            if (!available()) return false;
            // Literal stock internal-neighbour operands: same forward edge.
            const scalar nei = tetPointRef
                (a, b, actualFaceCentre, *actualNeighbourCentre).quality();
            if (!std::isfinite(nei) || nei < tol) return false;
        }
    }
    for (std::size_t k = 0; k < n; ++k)
    {
        bool good = true;
        for (std::size_t j = 1; j + 1 < n; ++j)
        {
            if (!available()) return false;
            const point& base = ownerOrientedRing[k];
            const point& a = ownerOrientedRing[(k + j) % n];
            const point& b = ownerOrientedRing[(k + j + 1) % n];
            const scalar own = tetPointRef
                (actualOwnerCentre, base, a, b).quality();
            if (!std::isfinite(own) || own <= tol) { good = false; break; }
            if (actualNeighbourCentre)
            {
                if (!available()) return false;
                const scalar nei = tetPointRef
                    (*actualNeighbourCentre, base, b, a).quality();
                if (!std::isfinite(nei) || nei <= tol) { good = false; break; }
            }
        }
        if (good)
        {
            if (!available()) return false;
            if (commonAnchor) *commonAnchor = label(k);
            return true;
        }
    }
    return false;
}

}
}
#endif
