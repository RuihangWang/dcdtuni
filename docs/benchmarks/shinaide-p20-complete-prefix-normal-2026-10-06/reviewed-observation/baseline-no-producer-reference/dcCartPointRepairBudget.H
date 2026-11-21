// Optional native repair reserves time for the remaining product pipeline.
#ifndef dcCartPointRepairBudget_H
#define dcCartPointRepairBudget_H

#include <algorithm>
#include <cmath>
#include <cstring>

namespace Foam
{
namespace dcCartPointRepairBudget
{
inline bool enabled(const char* flag)
{
    return flag && std::strcmp(flag, "1") == 0;
}

inline double allowance(double runLimitSeconds, double runElapsedSeconds)
{
    constexpr double stageCap = 20;
    constexpr double downstreamReserve = 60;
    if (!std::isfinite(runLimitSeconds) || runLimitSeconds < 0
     || !std::isfinite(runElapsedSeconds) || runElapsedSeconds < 0)
    {
        return 0;
    }
    if (runLimitSeconds == 0) { return stageCap; }
    return std::min(stageCap,
        std::max(0.0, runLimitSeconds-runElapsedSeconds-downstreamReserve));
}
}
}
#endif
