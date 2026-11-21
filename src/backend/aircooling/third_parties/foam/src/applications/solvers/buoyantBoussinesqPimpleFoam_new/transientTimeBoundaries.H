/* Physical timeline limits for the repo-owned static PIMPLE solver. */
#ifndef dcTransientTimeBoundaries_H
#define dcTransientTimeBoundaries_H

#include "Time.H"
#include "scalarList.H"
#include <cmath>
#include <limits>

namespace Foam
{

// Only a few representational ULPs, never a physical timestep tolerance.
inline scalar dcTimeRoundoff(const scalar a, const scalar b)
{
    return 16*std::numeric_limits<scalar>::epsilon()
        *max(mag(a), mag(b));
}

inline bool dcAtTransientEnd(const Time& runTime)
{
    return runTime.stopAt() == Time::saEndTime
        && mag(runTime.value() - runTime.endTime().value())
            <= dcTimeRoundoff(runTime.value(), runTime.endTime().value());
}

inline void dcLimitTransientDeltaT(Time& runTime)
{
    const scalar now = runTime.value();
    const scalar dt = runTime.deltaTValue();
    const scalar end = runTime.endTime().value();
    if (!std::isfinite(now) || !std::isfinite(dt) || dt <= 0
        || !std::isfinite(end))
    {
        FatalErrorInFunction
            << "Transient time and endTime must be finite, and deltaT positive."
            << exit(FatalError);
    }

    // Time changes endTime to the current time once a writeNow/noWriteNow
    // request has taken its genuine final step. Never revive such a stop.
    scalar target = end;
    const scalarList events = runTime.controlDict().getOrDefault<scalarList>
    (
        "dcTransientEvents", scalarList()
    );
    scalar previous = -VGREAT;
    for (const scalar event : events)
    {
        if (!std::isfinite(event) || event < 0 || event <= previous)
        {
            FatalErrorInFunction
                << "dcTransientEvents must be finite, nonnegative and strictly increasing."
                << exit(FatalError);
        }
        previous = event;
        if (event > now + dcTimeRoundoff(now, event))
        {
            target = min(target, event);
        }
    }

    const scalar remaining = target - now;
    if (remaining > dcTimeRoundoff(now, target) && dt > remaining)
    {
        // Time::adjustDeltaT aligns writes with a bounded fivefold decrease.
        // This final physical cap must not be increased again by that hook.
        runTime.setDeltaT(remaining, false);
        Info<< "DcTransient: deltaT limited to " << remaining
            << " at boundary " << target << nl;
    }
}

inline bool dcRunTransient(Time& runTime)
{
    // Read a runtime-modified endTime/stopAt before Time::run's half-dt test.
    // This uses OpenFOAM's normal MPI file-watch synchronization.
    runTime.readModifiedObjects();
    dcLimitTransientDeltaT(runTime);
    return runTime.run();
}

}
#endif
