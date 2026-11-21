"""Stall rules of a Newton attempt that should give up early (deployment mode;
design 1.4).

A learned / transferred start, a fallback that restarts from one, and a certification attempt from a head's u0 are
attempts whose failure is cheaper to detect than to wait out.  In a deployment smoke test the first fallback
(a ramp from the head's u0 at the rung below) ran 20 Newton steps of 17.5 s each -- every GMRES at its 1230-iteration
cap keeping 0.83-0.97 of its right-hand side, ||r||2 flat at 1.17e-4, the preconditioner rebuilt ("unconverged") at
every step -- until the 400-s cap killed the run; and one validation's certification spent 69-86 s per 16^3 instance
on eight such steps.  Two rules, kept per rung (a row with ``it`` = 0 starts one):

* FAILED SOLVES: ``failed_solves`` consecutive Newton steps whose linear solve missed its requested tolerance AND kept
  a relative residual above ``FAILED_REL`` (1e-2, i.e. not even two digits: a near miss of a tight forcing term, such
  as 2e-8 against 1e-8 on a first step, is not a failure);
* NO PROGRESS: ||r||2 above ``drop`` times its value ``window`` steps earlier (with the defaults: not halved in four
  steps; a Newton iteration inside its basin gains far more, and every certified rung of the ladder on
  DHC64 does).

``StallWatch.check(row)`` returns the reason (a string) or None; what an abort means is the caller's business (the
steady entry's ``LearnedInitMonitor`` raises ``LearnedInitAbort``; ``state_train.newton_solve`` ends a certification
attempt).  Pure Python on ``solve_newton``'s history rows (``it``, ``l2``, ``inner_rel``, ``inner_rtol``,
``inner_iterations``); no tensor is touched.
"""
from __future__ import annotations

import math

STALL_DEFAULT = "4,0.5,2"
FAILED_REL = 1e-2


def parse_stall(spec):
    """``"WINDOW,DROP,FAILED"`` -> ``{"window", "drop", "failed_solves"}``; ``None`` / ``"off"`` -> None.  A WINDOW
    or FAILED of 0 switches that rule off (not both)."""
    if spec is None:
        return None
    text = str(spec).strip()
    if text.lower() in ("", "off", "none"):
        return None
    parts = [piece.strip() for piece in text.split(",") if piece.strip()]
    if len(parts) != 3:
        raise ValueError(f"stall rule {spec!r}: give WINDOW,DROP,FAILED (e.g. {STALL_DEFAULT}) or 'off'")
    try:
        window, drop, failed = int(parts[0]), float(parts[1]), int(parts[2])
    except ValueError as error:
        raise ValueError(f"stall rule {spec!r}: give WINDOW,DROP,FAILED (e.g. {STALL_DEFAULT}) or 'off'") from error
    if window < 0 or failed < 0 or not (0.0 < drop < 1.0) or (window == 0 and failed == 0):
        raise ValueError(f"stall rule {spec!r}: need WINDOW >= 0, 0 < DROP < 1, FAILED >= 0, not both 0")
    return {"window": window, "drop": drop, "failed_solves": failed}


def linear_solve_failed(row, floor: float = FAILED_REL) -> bool:
    """The step's linear solve missed its requested tolerance and kept a relative residual above ``floor`` (a row
    without the linear-solve keys, or without iterations, is not a failure)."""
    try:
        rel, rtol = float(row["inner_rel"]), float(row["inner_rtol"])
        iterations = int(row.get("inner_iterations") or 0)
    except (KeyError, TypeError, ValueError):
        return False
    if iterations <= 0:
        return False
    if not math.isfinite(rel):
        return True
    return rel > rtol and rel > floor


class StallWatch:
    """The two stall rules over the rows of one attempt, rung by rung (see the module docstring)."""

    def __init__(self, window: int = 4, drop: float = 0.5, failed_solves: int = 2):
        self.window, self.drop, self.failed_solves = int(window), float(drop), int(failed_solves)
        self.l2, self.failed = [], 0

    @classmethod
    def from_spec(cls, spec):
        """A watch from ``parse_stall``'s spelling (None for None / "off")."""
        rule = parse_stall(spec)
        return None if rule is None else cls(**rule)

    def reset(self) -> None:
        self.l2, self.failed = [], 0

    def check(self, row):
        """Feed one history row; the reason this rung stalled, or None."""
        it = int(row.get("it", 0))
        try:
            l2 = float(row.get("l2", float("nan")))
        except (TypeError, ValueError):
            l2 = float("nan")
        if it == 0 or not self.l2:
            self.reset()
            if it == 0:
                self.l2.append(l2)
                return None
        self.l2.append(l2)
        if not math.isfinite(l2):
            return f"non-finite ||r||2 at step {it}"
        if self.failed_solves:
            self.failed = self.failed + 1 if linear_solve_failed(row) else 0
            if self.failed >= self.failed_solves:
                return (f"{self.failed} consecutive linear solves failed by step {it} (relative residual "
                        f"{float(row['inner_rel']):.3g} > {float(row['inner_rtol']):.3g} after "
                        f"{int(row['inner_iterations'])} iterations)")
        if self.window and len(self.l2) > self.window:
            before = self.l2[-1 - self.window]
            ratio = l2 / before if before > 0 else float("inf")
            if not ratio <= self.drop:
                return (f"no progress: ||r||2 at step {it} is {ratio:.3g}x its value {self.window} steps earlier "
                        f"(> {self.drop:g})")
        return None
