"""One same-host monotonic clock for mesh preparation and strict acceptance.

This is execution metadata, never a cfdrun field or a geometry permission.
A shorter project budget is measured from the original admission, not from
the moment a later stage reads the configuration.

SOFT TARGET (owner decision 2026-10-20): the 5-minute mesh time is a
development target, not a product limit. The budget only measures: a mesh
that runs past it is logged once as a warning and runs to completion. Nothing
raises, no alarm is armed and no process is terminated because of it.
"""
import logging
import math
import signal
import threading
import time
from contextlib import contextmanager

DEFAULT_MESH_TIME_LIMIT_S = 300.0

logger = logging.getLogger(__name__)

class MeshTimeoutError(RuntimeError):
    """Kept for callers that still catch it; the soft budget never raises it."""

def mesh_time_limit_seconds(config, default=DEFAULT_MESH_TIME_LIMIT_S):
    raw = config.get("meshTimeLimitSeconds", default)
    try:
        value = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value) or value <= 0:
        value = default
    return min(value, DEFAULT_MESH_TIME_LIMIT_S)

class MeshBudget:
    def __init__(self, *, started=None, deadline=None):
        now = time.monotonic()
        if (started is None) != (deadline is None):
            raise ValueError("mesh start and deadline must be supplied together")
        if started is None:
            started, deadline = now, now + DEFAULT_MESH_TIME_LIMIT_S
        if (type(started) not in (int, float) or type(deadline) not in (int, float)
                or not math.isfinite(started) or not math.isfinite(deadline)
                or started < 0 or started > now or deadline <= started
                or deadline > started + DEFAULT_MESH_TIME_LIMIT_S):
            raise ValueError("invalid same-host monotonic mesh budget")
        self.started = float(started)
        self.deadline = float(deadline)
        self.active = True
        self._configured_limit = self.deadline - self.started
        self._frontend_elapsed = 0.0
        self._deadline_changed = None
        self._overrun_logged = False

    @property
    def limit(self):
        return self.deadline - self.started

    def remaining(self):
        return self.deadline - time.monotonic() if self.active else None

    def elapsed(self):
        return time.monotonic() - self.started

    def check(self, step):
        """Soft: log the first overrun of the target, never stop the mesh."""
        remaining = self.remaining()
        if remaining is not None and remaining <= 0 and not self._overrun_logged:
            self._overrun_logged = True
            logger.warning(
                f"Mesh time {self.elapsed():.0f}s passed the {self.limit:.6g}s development "
                f"target before '{step}' (soft target: the mesh runs to completion)")

    def charge_preparation(self, elapsed):
        """Charge a browser duration without importing its unrelated clock origin."""
        if not self.active:
            raise RuntimeError("a completed mesh budget cannot be rearmed")
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("meshPreparationElapsedSeconds must be a finite non-negative number")
        # A repeated receipt can tighten, never deduct the same duration twice.
        self._frontend_elapsed = max(self._frontend_elapsed, float(elapsed))
        self._tighten_deadline()
        self.check("frontend preparation")
        return self

    def _tighten_deadline(self):
        candidate = self.started + self._configured_limit - self._frontend_elapsed
        if candidate < self.deadline:
            self.deadline = candidate
            if self._deadline_changed is not None:
                self._deadline_changed(self.deadline)

    def tighten(self, config):
        if not self.active:
            raise RuntimeError("a completed mesh budget cannot be rearmed")
        self._configured_limit = min(self._configured_limit, mesh_time_limit_seconds(config))
        self._tighten_deadline()
        self.check("mesh configuration")
        return self

    def tighten_manifest(self, manifest):
        if isinstance(manifest, dict):
            for block in (manifest, manifest.get("meshGeometry")):
                if isinstance(block, dict) and isinstance(block.get("meshConfig"), dict):
                    self.tighten(block["meshConfig"])
        self.check("mesh input configuration")
        return self

    def cli_args(self):
        self.check("CLI handoff")
        return ["--mesh-started-monotonic", format(self.started, ".17g"),
                "--mesh-deadline-monotonic", format(self.deadline, ".17g")]

    def watch(self, callback):
        # soft target: the CLI watchdog is never armed
        self._deadline_changed = None
        if callback is not None:
            callback(None)

    def finish(self):
        self.check("mesh completion")
        took = self.elapsed()
        if took > self.limit:
            logger.warning(f"Mesh pipeline took {took:.0f}s (development target {self.limit:.6g}s)")
        else:
            logger.info(f"Mesh pipeline took {took:.0f}s (development target {self.limit:.6g}s)")
        self.active = False
        if self._deadline_changed is not None:
            self._deadline_changed(None)

@contextmanager
def mesh_deadline_watchdog(budget, *, cleanup):
    """CLI-only POSIX watchdog, including blocked stdin and Python preparation.

    The supervisor remains responsible for cleanup escalation. This context
    never signals a server thread or an unrelated process.
    """
    if budget is None:
        yield
        return
    if (not hasattr(signal, "SIGALRM")
            or threading.current_thread() is not threading.main_thread()):
        raise RuntimeError("mesh deadline watchdog requires a POSIX main thread")
    prior_handler = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL)[0] != 0:
        raise RuntimeError("mesh CLI already has an active alarm")
    def arm(deadline):
        if deadline is None:
            signal.setitimer(signal.ITIMER_REAL, 0)
        else:
            budget.check("mesh watchdog")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                budget.check("mesh watchdog")
            signal.setitimer(signal.ITIMER_REAL, remaining)
    def expired(_signum, _frame):
        try:
            budget.check("mesh CLI watchdog")
        except MeshTimeoutError:
            cleanup()
            raise
        arm(budget.deadline if budget.active else None)
    signal.signal(signal.SIGALRM, expired)
    try:
        budget.watch(arm)
        yield
    finally:
        budget.watch(None)
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prior_handler)
