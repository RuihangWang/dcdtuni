"""Native airflow time boundaries from the original physical schedule corners."""
import math
from numbers import Real


def normalize_time_events(events, *, end_time=None):
    """Validate and sort physical timestamps without changing schedule data."""
    times = set()
    for event in events or ():
        if isinstance(event, bool) or not isinstance(event, Real):
            raise ValueError("transient event times must be finite numbers")
        event = float(event)
        if not math.isfinite(event) or event < 0:
            raise ValueError("transient event times must be finite and nonnegative")
        if end_time is not None and event > end_time:
            raise ValueError("transient event runs beyond the end time")
        times.add(event)
    return sorted(times)


def time_events_from_registry(registry):
    """Union step times and ramp corners BEFORE the OpenFOAM table ε transform."""
    events = []
    if registry:
        for _, spec in registry.items():
            for rows in (getattr(spec, "timeVarying", None) or {}).values():
                for row in rows:
                    events.append(row["t"] if isinstance(row, dict)
                                  else row.t if hasattr(row, "t") else row[0])
    return normalize_time_events(events)
