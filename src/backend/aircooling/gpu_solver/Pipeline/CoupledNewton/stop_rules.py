"""Engineering stopping rules for ``solve_newton(stop_rule=...)`` (data-centre line, owner 2026-11-06).

The certified solver stops on ``max|r| < tol``; a design loop does not need that. These rules play the role of
OpenFOAM's ``residualControl`` and of dctwin's return-temperature plateau test, and are chosen by the user:

* ``ResidualControl({"U": 1e-3, "p_rgh": 1e-3, "T": 1e-4, ...})`` -- every listed field's residual 2-norm has
  fallen below ``threshold`` times its value at the first evaluation (U is the joint norm of the velocity
  components); fields not listed are not tested.
* ``QuantityStagnation(q, tol)`` -- the engineering quantities ``q(x)`` (a vector, e.g. ACU return and server
  intake temperatures from ``Post_process.dc_qoi``) moved by less than ``tol`` (their units) between consecutive
  ACCEPTED iterates, ``window`` times in a row (an evaluation whose quantities are bitwise those of the previous one
  -- the same iterate after a refused / rejected step -- is not counted again).
* ``HeatClosure(heat, declared, rel)`` -- the air-side heat ``heat(x)`` (W, e.g. the ACU heat removed) is within
  ``rel`` of the ``declared`` IT heat (registration section 4c).
* ``AllOf(...)`` / ``AnyOf(...)`` combine rules; ``min_iterations`` keeps a rule silent before that iterate.

Each rule records what it measured in ``self.last`` so the caller can log it.
"""

from __future__ import annotations

import torch

__all__ = ["ResidualControl", "QuantityStagnation", "HeatClosure", "DctwinPlateau", "AllOf", "AnyOf",
           "parse_residual_control", "field_slices"]


def field_slices(unknowns, n_cell: int) -> dict[str, slice]:
    """Row ranges of each unknown in the field-blocked state ``[u | v | w | p_rgh | T | ...]``; the velocity
    components are joined under ``"U"`` (their descriptor name)."""
    out, start = {}, 0
    for name, width, *_ in unknowns:
        out[str(name)] = slice(start, start + int(width) * n_cell)
        start += int(width) * n_cell
    return out


def parse_residual_control(text: str) -> dict[str, float]:
    """``"U=1e-3,p_rgh=1e-3,T=1e-4"`` -> dict."""
    out = {}
    for item in filter(None, (s.strip() for s in text.split(","))):
        k, v = item.split("=")
        out[k.strip()] = float(v)
    return out


class ResidualControl:
    def __init__(self, thresholds: dict[str, float], slices: dict[str, slice], min_iterations: int = 1):
        unknown = sorted(set(thresholds) - set(slices))
        if unknown:
            raise ValueError(f"residual control: unknown fields {unknown}; the case has {sorted(slices)}")
        self.thresholds, self.slices, self.min_iterations = dict(thresholds), slices, int(min_iterations)
        self.initial = None
        self.last = {}

    def norms(self, residual: torch.Tensor) -> dict[str, float]:
        r = residual.reshape(-1)
        return {f: float(r[self.slices[f]].norm()) for f in self.thresholds}

    def __call__(self, x, residual, it) -> bool:
        n = self.norms(residual)
        if self.initial is None:
            self.initial = {f: max(v, 1e-300) for f, v in n.items()}
        rel = {f: n[f] / self.initial[f] for f in n}
        self.last = {"relative": rel}
        return it >= self.min_iterations and all(rel[f] <= self.thresholds[f] for f in rel)


class QuantityStagnation:
    def __init__(self, quantities, tol: float, window: int = 2, min_iterations: int = 1, span: bool = False):
        self.q, self.tol, self.window, self.min_iterations = quantities, float(tol), int(window), int(min_iterations)
        # ``span`` (opt-in, registration section 4d): also |q_n - q_{n-window}| < tol over the window of accepted
        # iterates, so that a slow drift of many sub-tol steps is not a plateau.  False: every rule before.
        self.span = bool(span)
        self.recent = []
        self.prev = None
        self.count = 0
        self.last = {}

    def _verdict(self, it) -> bool:
        ok = it >= self.min_iterations and self.count >= self.window
        if self.span:
            spanned = (float((self.recent[-1] - self.recent[0]).abs().max())
                       if len(self.recent) > self.window else float("inf"))
            self.last["span_change"] = spanned
            ok = ok and spanned < self.tol
        return ok

    def __call__(self, x, residual, it) -> bool:
        with torch.no_grad():
            q = self.q(x).detach().reshape(-1).double().cpu()
        if self.prev is not None and torch.equal(q, self.prev):
            # the same iterate again (a refused or rejected step): no new accepted step to count
            return self._verdict(it)
        if self.prev is None:
            change = float("inf")
        else:
            change = float((q - self.prev).abs().max())
        self.prev = q
        self.count = self.count + 1 if change < self.tol else 0
        self.last = {"max_change": change, "count": self.count}
        if q.numel() <= 4:
            self.last["q"] = [float(v) for v in q]
        self.recent = (self.recent + [q])[-(self.window + 1):]
        return self._verdict(it)


class DctwinPlateau:
    """dctwin's own convergence verdict for the OpenFOAM reference (``log/<run>/convergence.json``: "every return-air
    temperature changed then plateaued (within 0.5 K over the last 100 iterations), and the heat the ACUs remove
    closed on the declared heat; residuals are diagnostics"): over the last ``window`` ACCEPTED iterates every return
    temperature's peak-to-peak is <= ``tol`` (K) and the air-side heat is within ``heat_rel`` of ``declared`` at every
    one of them.  ``returns(x)`` gives the return temperatures, ``heat(x)`` the ACU heat removed (W).  An evaluation
    whose quantities are bitwise those of the previous one (a rejected step) is not counted again."""

    def __init__(self, returns, heat, declared: float, tol: float = 0.5, window: int = 100, heat_rel: float = 0.01):
        if not declared > 0.0:
            raise ValueError(f"dctwin plateau: the declared heat must be positive, not {declared}")
        self.returns, self.heat, self.declared = returns, heat, float(declared)
        self.tol, self.window, self.heat_rel = float(tol), int(window), float(heat_rel)
        self.hist = []                       # (return temperatures, relative heat error) of accepted iterates
        self.last = {}

    def __call__(self, x, residual, it) -> bool:
        with torch.no_grad():
            q = self.returns(x).detach().reshape(-1).double().cpu()
            e = float(self.heat(x)) / self.declared - 1.0
        if not (self.hist and torch.equal(q, self.hist[-1][0])):
            self.hist = (self.hist + [(q, e)])[-self.window:]
        if len(self.hist) < self.window:
            self.last = {"window": f"{len(self.hist)}/{self.window}", "heat_rel": e}
            return False
        Q = torch.stack([h[0] for h in self.hist])
        p2p = float((Q.max(0).values - Q.min(0).values).max())
        worst = max(abs(h[1]) for h in self.hist)
        self.last = {"peak_to_peak_K": p2p, "window_max_heat_rel": worst, "heat_rel": e}
        return p2p <= self.tol and worst <= self.heat_rel


class HeatClosure:
    def __init__(self, heat, declared: float, rel: float, min_iterations: int = 1):
        if not declared > 0.0:
            raise ValueError(f"heat closure: the declared heat must be positive, not {declared}")
        self.heat, self.declared, self.rel, self.min_iterations = heat, float(declared), float(rel), int(min_iterations)
        self.last = {}

    def __call__(self, x, residual, it) -> bool:
        with torch.no_grad():
            h = float(self.heat(x))
        err = h / self.declared - 1.0
        self.last = {"heat_W": h, "relative_error": err}
        return it >= self.min_iterations and abs(err) <= self.rel


class MeritGuard:
    """Registration section 4f (opt-in): the steady merit -- RMS over the fields of ||F_f|| / ||F_f(reference)|| --
    is below ``level`` and has fallen by at least ``drop`` x over the last ``window`` accepted evaluations (an
    evaluation with bitwise the same residual as the previous one is not counted).  The reference is the first residual
    seen, or the one given to ``set_reference`` (the cold start, when a driver hands over mid-rung)."""

    def __init__(self, slices: dict, window: int = 10, level: float = 1e-2, drop: float = 2.0):
        self.slices, self.window, self.level, self.drop = slices, int(window), float(level), float(drop)
        self.reference = None
        self.recent = []
        self.last = {}

    def set_reference(self, residual):
        r = residual.reshape(-1)
        self.reference = torch.stack([r[sl].norm() for sl in self.slices.values()]).clamp_min(1e-300)

    def __call__(self, x, residual, it) -> bool:
        r = residual.reshape(-1)
        if self.reference is None:
            self.set_reference(r)
        rel = torch.stack([r[sl].norm() for sl in self.slices.values()]) / self.reference
        merit = float(torch.sqrt((rel * rel).mean()))
        if not self.recent or merit != self.recent[-1]:
            self.recent = (self.recent + [merit])[-(self.window + 1):]
        fallen = (self.recent[0] / max(merit, 1e-300)) if len(self.recent) > self.window else 0.0
        self.last = {"merit": merit, "fallen": fallen}
        return merit < self.level and fallen >= self.drop


class AllOf:
    def __init__(self, *rules):
        self.rules = [r for r in rules if r is not None]
        self.last = {}

    def __call__(self, x, residual, it) -> bool:
        verdicts = [rule(x, residual, it) for rule in self.rules]      # every rule observes every iterate
        self.last = {type(r).__name__: r.last for r in self.rules}
        return bool(self.rules) and all(verdicts)


class AnyOf(AllOf):
    def __call__(self, x, residual, it) -> bool:
        verdicts = [rule(x, residual, it) for rule in self.rules]
        self.last = {type(r).__name__: r.last for r in self.rules}
        return any(verdicts)
