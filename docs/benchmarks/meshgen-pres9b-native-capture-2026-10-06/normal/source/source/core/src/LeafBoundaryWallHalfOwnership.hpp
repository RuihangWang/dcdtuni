#pragma once
#include <cmath>
#include <vector>

// Private preview: only leaf-boundary wall-fragment ownership.
// No geometry is snapped, reoriented or removed by this numeric policy.
namespace meshgen_leaf_ownership {
struct HalfOwnership {
    bool boundaryTile = false;
    bool plus = true;
    bool minus = true;
    bool allows(int half) const {
        return half == 0 ? plus : (half == 1 ? minus : false);
    }
    bool allowsEdge(double sign) const {
        if (!boundaryTile) return true;  // preserve all legacy non-boundary paths
        if (sign == 1.0) return plus;     // existing edge sign means p+
        if (sign == -1.0) return minus;   // existing edge sign means p-
        return false;                    // no invented direction for a boundary tile
    }
};
template<class Point>
inline double component(const Point& p, int axis) {
    return axis == 0 ? p.x : (axis == 1 ? p.y : p.z);
}
template<class Point>
inline bool finitePoint(const Point& p) {
    return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z);
}
template<class Point>
inline bool inLeaf(const Point& p, const Point& lo, const Point& hi, double clipTol) {
    for (int axis = 0; axis < 3; ++axis)
        if (component(p, axis) < component(lo, axis) - clipTol ||
            component(p, axis) > component(hi, axis) + clipTol)
            return false;
    return true;
}
template<class Point>
inline HalfOwnership boundaryHalfOwnership(
    const Point& lo, const Point& hi,
    const Point& original0, const Point& original1, const Point& original2,
    const std::vector<Point>& fragment, const Point& probePlus,
    const Point& probeMinus, double clipTol, double areaNormSquared) {
    HalfOwnership result;
    // Unsupported or degenerate geometry follows the original path; this policy
    // does not grant a new repair and leaves existing strict checks untouched.
    if (!(clipTol > 0) || !std::isfinite(clipTol) ||
        !(areaNormSquared > 0) || !std::isfinite(areaNormSquared) ||
        fragment.size() < 3 || !finitePoint(lo) || !finitePoint(hi) ||
        !finitePoint(original0) || !finitePoint(original1) ||
        !finitePoint(original2) || !finitePoint(probePlus) || !finitePoint(probeMinus))
        return result;
    for (int axis = 0; axis < 3; ++axis)
        if (!(component(lo, axis) < component(hi, axis))) return result;
    for (const auto& p : fragment) if (!finitePoint(p)) return result;
    // Both the source triangle and the actual fragment must be on the same
    // existing boundary plane. The current clipTol is reused, not enlarged.
    for (int axis = 0; axis < 3 && !result.boundaryTile; ++axis) {
        for (int upper = 0; upper < 2 && !result.boundaryTile; ++upper) {
            const double plane = component(upper ? hi : lo, axis);
            if (std::abs(component(original0, axis) - plane) > clipTol ||
                std::abs(component(original1, axis) - plane) > clipTol ||
                std::abs(component(original2, axis) - plane) > clipTol)
                continue;
            bool onPlane = true;
            for (const auto& p : fragment) {
                if (std::abs(component(p, axis) - plane) > clipTol) {
                    onPlane = false;
                    break;
                }
            }
            if (onPlane) result.boundaryTile = true;
        }
    }
    if (result.boundaryTile) {
        result.plus = inLeaf(probePlus, lo, hi, clipTol);
        result.minus = inLeaf(probeMinus, lo, hi, clipTol);
    }
    return result;
}
}  // namespace meshgen_leaf_ownership
