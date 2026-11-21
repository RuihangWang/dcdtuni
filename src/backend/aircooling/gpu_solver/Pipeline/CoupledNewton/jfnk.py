"""JFNK mode of the Newton loop: the exact Krylov operator, and a preconditioner rebuilt by policy.

Design: ``docs/research/2026-10-05-unified-learned-solver.md`` sections 3 and 4.  A Newton step needs the
EXACT Jacobian action; an assembled matrix is needed only to BUILD a preconditioner.  So in this mode the Krylov
operator is always the exact Jacobian at the current iterate -- the matrix assembled at that iterate, or a
Jacobian-vector product of the residual, which is the same operator -- and the preconditioner is built from an
assembled matrix and KEPT across Newton steps until a policy rebuilds it.  A step that keeps it assembles nothing:
no colouring probes and no AMG setup (about 7.4 s per linearisation at 3.5M unknowns).  A kept
preconditioner moves no root and no certificate -- the residual, the operator and the gate stay exact -- it costs
Krylov iterations only, and the policy bounds that cost.

``newton.solve_newton(krylov_operator=..., precond_policy=..., jvp=...)`` runs the mode; this module holds the
parts that are not the loop: the policy, the JVP construction and the product counter.  Nothing here is reached
by a default solve.

OPERATOR (``krylov_operator``, the entries' ``--operator``)

    "assembled"  the matrix assembled at this iterate.  Every Newton step assembles; the policy decides only
                 whether the preconditioner is rebuilt from that matrix (saves the AMG setup, not the probes).
    "jvp"        the JVP at this iterate on every step; a step assembles only to rebuild the preconditioner.
    "auto"       the assembled matrix on the steps that assemble anyway (rebuilds), the JVP on the others.

JVP (``jvp``, ``--jvp``).  Both are exact and differentiate the residual the line search and the gate read, with
the d_f coefficient the case is configured with -- the one ``assemble`` uses, so "auto" alternates between two
spellings of one operator (``--coefficient implicit`` makes that operator the true Jacobian of an MWI residual).

    "tape"     ``jacobian.linearise``: one residual tape per Newton step (a forward and a create_graph backward),
               then every product is one second-order pass over it -- which is exactly what one colouring probe
               of ``assemble`` is.  Measured cost of a probe: 4.3 ms against 5.7 ms for a residual evaluation at
               48^3 (``linearise``'s docstring), about 20 ms at 3.5M unknowns (LDC96: ~210 probes in a 4.2 s
               assembly).  The tape stays resident through the Krylov solve, where it replaces ``J``.
    "forward"  ``jacobian.linearise_forward``: forward-mode AD, one dual-number evaluation of the residual per
               product.  No tape and no first backward, but the primal is recomputed with every product: by
               reading 2-3x a residual evaluation each (primal plus tangent arithmetic), i.e. dearer per product
               than "tape" and lighter in memory -- the one measurement on record agrees: the ODIL JFNK trainer
               found a retained double-backward product 10.6 ms against 31.1 ms for ``torch.func.jvp`` on the
               same FV residual (``Pipeline/ODIL/trainer_jfnk.py``, ``_jv``).  It is checked ONCE per solver
               against "tape" on a seeded random direction (``FORWARD_CHECK_RTOL``); a residual whose forward pass
               does not support forward AD, or a mismatch, falls back to "tape" for the rest of the run and the
               reason is logged.

POLICY (``PrecondPolicy``, ``--precond-rebuild``): terms joined by "+"; the preconditioner is rebuilt at a Newton
step when ANY term fires:

    every      at every Newton step (today's behaviour, the default; combined with nothing)
    every:k    when the kept preconditioner has served k Newton steps
    growth:g   when the previous linear solve needed more than g times the iterations PER DECADE of the first
               solve after the last build, or missed its (forcing) tolerance
    rung       at the first Newton step of every ``solve_newton`` call -- a continuation rung in the steady entry,
               a time step in the transient entry.  Without it a preconditioner is carried across calls.
    ceiling:g  when a solve on a KEPT preconditioner needed more than g times the iterations per decade of an
               absolute reference the caller sets (``set_ceiling``; F3-X2: the classical cycle at rest of the same
               problem), and the first solve after its build was within that ceiling (otherwise a fresh build would
               not do better).  Never fires on the first solve after a build, and not at all without a reference.

and in any case (``REBUILD_REASONS``): at the first step ("first"), on the step after a refused line search that
ran on a kept preconditioner ("retry": the default path's stale-Jacobian retry, applied to the preconditioner), at a
PTC handover or cutback, and at the first PTC arming of a call ("ptc"; the shift needs an assembled diagonal).

WHY PER DECADE.  The transient Oseen rule (``timestepping.solve_linear_step``) compares raw iteration counts, which
is right at the fixed tolerance it solves to.  A Newton loop under Eisenstat-Walker forcing asks for 1e-8 at its
first step, 1e-1 in the middle and 1e-4..1e-8 at the end, so the raw count moves several-fold with the request
alone: a reference taken at a tight solve would never fire, one taken at a loose solve would fire on every
tightening.  The count per decade of relative-residual reduction actually achieved, ``its / log10(1/rel)``, is what
the request does not move (GMRES convergence is close to linear in the log of the residual); at a constant
tolerance (``--no-forcing``, the transient solve) the two rules coincide.

WHAT STAYS ALIVE.  The policy object holds the kept preconditioner between steps and between ``solve_newton``
calls, on purpose: the memory release of ``solve_newton`` drops everything else -- the previous ``J``
(the operator of a reuse step is the JVP), its closures and the previous tape -- before the next assembly or tape.
A preconditioner pins what it reads (a coupled AMG its hierarchy; ``BudgetedLearned`` / ``StagnationRescue`` the
block matrix their lazily built classical and rescue arms are built from, so those arms are built at most once per
kept preconditioner, from the same stale matrix).  ``release()`` drops it; the entries call it after the last solve.
"""
from __future__ import annotations

import logging
import math
import time

import torch

OPERATORS = ("assembled", "jvp", "auto")
JVP_KINDS = ("tape", "forward")
REBUILD_REASONS = ("first", "every", "growth", "unconverged", "rung", "retry", "handover", "cutback", "ptc",
                   "order1", "ceiling")       # order1: --order1-switch; ceiling: PrecondPolicy's ceiling:g
# Forward-mode JVP against the taped one at the one-time check, relative 2-norm.  Both differentiate the same
# function exactly; they differ by summation order only (measured nothing yet: 1e-10 leaves four orders above
# the ~1e-14 a reordering costs and sits far below any operator defect -- a missing d_f term reads >= 1e-2).
FORWARD_CHECK_RTOL = 1.0e-10
# When the taped reference does not fit on the device (SS-M3, track N section 9.6: LM1021), the forward product is
# checked against central differences of the residual along a direction scaled entry by entry with |x| (so no entry
# changes sign), at the relative step FD_CHECK_STEP and then a tenth of it (a switch inside the first step);
# truncation and round-off leave about 1e-10 there, so 1e-6 still reads any operator defect (>= 1e-2).
FD_CHECK_STEP = 6.0e-6
FD_CHECK_RTOL = 1.0e-6
# The floor on the decades a solve is credited with when it reduced its residual by almost nothing, so that its
# iterations per decade is large but finite.
_MIN_DECADES = 1.0e-2


def iterations_per_decade(iterations: int, relative: float):
    """``iterations / log10(1 / relative)``, or ``None`` when the solve measured nothing (no iterations, or an
    exact or non-finite final residual)."""
    iterations, relative = int(iterations), float(relative)
    if iterations <= 0 or not math.isfinite(relative) or relative <= 0.0:
        return None
    return iterations / max(-math.log10(relative), _MIN_DECADES)


class PrecondPolicy:
    """When the JFNK loop rebuilds its preconditioner, and the preconditioner it keeps until then.

    ``decide`` is a pure query: why the preconditioner must be rebuilt at the coming Newton step, or ``None`` to
    keep it.  The loop then reports what happened: ``built`` after a build, ``observe`` after every linear solve,
    ``request`` for a forced rebuild at the next step.  ``reset`` starts a new problem (a new continuation ladder:
    the steady entry calls it per ramp), ``release`` drops the kept preconditioner (memory) and keeps the rule's
    state, so the next step rebuilds.
    """

    def __init__(self, spec: str = "every"):
        terms = [term.strip() for term in str(spec).split("+")]
        every, growth, rung, ceiling, seen = None, None, False, None, set()
        for term in terms:
            name, colon, value = term.partition(":")
            if not name or name in seen:
                raise ValueError(f"--precond-rebuild {spec!r}: empty or repeated term {term!r}")
            seen.add(name)
            if name == "every":
                every = 1 if not colon else _positive_int(value, spec)
            elif name == "growth":
                growth = _growth_factor(value if colon else "", spec)
            elif name == "rung":
                if colon:
                    raise ValueError(f"--precond-rebuild {spec!r}: 'rung' takes no value")
                rung = True
            elif name == "ceiling":
                ceiling = _growth_factor(value if colon else "", spec, name)
            else:
                raise ValueError(f"--precond-rebuild {spec!r}: unknown term {term!r} (every, every:k, growth:g, "
                                 "rung, ceiling:g, joined by '+')")
        if every == 1 and len(terms) > 1:
            raise ValueError(f"--precond-rebuild {spec!r}: 'every' rebuilds at every Newton step already; it "
                             "combines with nothing (use every:k)")
        self.spec = "+".join(terms)
        self.every, self.growth, self.rung = every, growth, rung
        self.ceiling, self.ceiling_rate = ceiling, None     # ceiling:g and its absolute reference (set_ceiling)
        self.precondition, self.held = None, False
        self.pending, self.reference, self.age = None, None, 0
        self.builds = 0
        # Callables told of every observed solve, ``fn(rate, rebuilt=..., converged=...)`` (empty by default; the
        # steady entry's ``--camg-theta-refresh`` adds the network-output reuse's rule).  They read, never decide
        self.observers = []

    def __repr__(self) -> str:
        return f"PrecondPolicy({self.spec!r})"

    @property
    def rebuilds_every_step(self) -> bool:
        """``every``: the default path's rule (a preconditioner is never kept)."""
        return self.every == 1

    # ---------------------------------------------------------------------------------------------- the rule
    def decide(self, *, first_of_call: bool):
        """The reason to rebuild at the coming Newton step (``REBUILD_REASONS``), or ``None`` to keep.  Pure."""
        if not self.held:
            return "first"
        if self.pending is not None:
            return self.pending
        if self.rung and first_of_call:
            return "rung"
        if self.every is not None and self.age >= self.every:
            return "every"
        return None

    def built(self, precondition) -> None:
        """A preconditioner was just built (``None`` is a legitimate one: ``precond_shape="none"``)."""
        self.precondition, self.held = precondition, True
        self.pending, self.reference, self.age = None, None, 0
        self.builds += 1

    def observe(self, iterations: int, relative: float, converged: bool, *, rebuilt: bool):
        """Fold one linear solve into the rule; returns its iterations per decade (``None``: not measurable).

        The first measurable solve after a build is the reference.  ``growth:g`` fires on a later solve whose
        iterations per decade exceed ``g`` times the reference, and on any solve that missed its tolerance.
        ``ceiling:g`` fires on a solve on a kept preconditioner whose iterations per decade exceed ``g`` times the
        absolute reference of ``set_ceiling``, when the reference solve of that build was within the ceiling.
        """
        self.age += 1
        rate = iterations_per_decade(iterations, relative)
        if rebuilt or self.reference is None:
            self.reference = rate
        elif (self.growth is not None and rate is not None
              and rate > self.growth * self.reference):
            self.pending = self.pending or "growth"
        if (self.ceiling is not None and self.ceiling_rate is not None and not rebuilt and rate is not None
                and self.reference is not None and rate > self.ceiling * self.ceiling_rate >= self.reference):
            self.pending = self.pending or "ceiling"
        if self.growth is not None and not converged:
            self.pending = self.pending or "unconverged"
        for observer in self.observers:
            observer(rate, rebuilt=bool(rebuilt), converged=bool(converged))
        return rate

    def set_ceiling(self, rate) -> None:
        """The absolute reference of ``ceiling:g`` (iterations per decade; ``None`` disables the term)."""
        self.ceiling_rate = None if rate is None else float(rate)

    def request(self, reason: str) -> None:
        """Rebuild at the next Newton step, for ``reason`` (``REBUILD_REASONS``); first come, first named."""
        if reason not in REBUILD_REASONS:
            raise ValueError(f"unknown rebuild reason {reason!r}")
        if self.held and self.pending is None:
            self.pending = reason

    def release(self) -> None:
        """Drop the kept preconditioner; the next Newton step rebuilds ("first")."""
        self.precondition, self.held = None, False

    def reset(self) -> None:
        """A new problem: drop the preconditioner and everything the rule remembers about it."""
        self.release()
        self.pending, self.reference, self.age = None, None, 0


def _positive_int(value: str, spec: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise ValueError(f"--precond-rebuild {spec!r}: every:k needs an integer k >= 1, got {value!r}")
    return number


def _growth_factor(value: str, spec: str, name: str = "growth") -> float:
    try:
        number = float(value)
    except ValueError:
        number = float("nan")
    if not (math.isfinite(number) and number > 1.0):
        raise ValueError(f"--precond-rebuild {spec!r}: {name}:g needs a finite g > 1 (it multiplies an "
                         f"iteration count), got {value!r}")
    return number


# -------------------------------------------------------------------------------------------- the operator
def check_forward_jvp(solver, x: torch.Tensor, *, coefficient=None) -> dict:
    """Forward mode against the tape at ``x`` on one seeded random direction: ``{"ok", "gap", "reason", "check"}``.

    ``check`` is "tape"; when the taped reference runs out of device memory and the solver has its residual
    (``_operator_residual`` or ``residual``; ``coefficient`` None), it is "fd": ``_fd_check``'s central differences
    instead (SS-M3)."""
    generator = torch.Generator(device="cpu").manual_seed(20261005)
    direction = torch.randn(x.numel(), generator=generator, dtype=x.dtype).to(x.device)
    try:
        forward = solver.linearise_forward(x, coefficient=coefficient)(direction)
    except Exception as failure:          # an op of this residual without a forward-mode formula, typically
        return {"ok": False, "gap": float("nan"), "reason": f"{type(failure).__name__}: {failure}", "check": "tape"}
    out_of_memory = False
    try:
        taped = solver.linearise(x, coefficient=coefficient)
        reference = taped(direction)
        del taped
    except torch.cuda.OutOfMemoryError:
        if coefficient is not None or not (hasattr(solver, "_operator_residual") or hasattr(solver, "residual")):
            raise
        out_of_memory = True
    if out_of_memory:                     # outside the handler, so that the traceback's frames are released
        del forward
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return _fd_check(solver, x, direction)
    gap = float((forward - reference).norm() / max(float(reference.norm()), 1e-300))
    ok = math.isfinite(gap) and gap <= FORWARD_CHECK_RTOL
    return {"ok": ok, "gap": gap, "check": "tape",
            "reason": "" if ok else f"relative gap {gap:.3e} to the taped product exceeds {FORWARD_CHECK_RTOL:.0e}"}


def _fd_check(solver, x: torch.Tensor, direction: torch.Tensor) -> dict:
    """The forward product along w = direction * |x| against (F(x + h w) - F(x - h w)) / 2h, h = FD_CHECK_STEP and
    then h / 10, F the function both linearisations differentiate (``_operator_residual`` where the solver has it);
    ``ok`` when either relative gap is at most FD_CHECK_RTOL."""
    function = getattr(solver, "_operator_residual", None) or solver.residual
    point = x.detach().reshape(-1)
    w = direction.reshape(-1) * point.abs()
    jw = solver.linearise_forward(point)(w)
    norm = max(float(jw.norm()), 1e-300)
    gaps = []
    for h in (FD_CHECK_STEP, FD_CHECK_STEP / 10.0):
        with torch.no_grad():
            fd = (function(point + h * w) - function(point - h * w)) / (2.0 * h)
        gaps.append(float((fd.reshape(-1) - jw.reshape(-1)).norm()) / norm)
        if math.isfinite(gaps[-1]) and gaps[-1] <= FD_CHECK_RTOL:
            break
    gap = min(g for g in gaps if math.isfinite(g)) if any(math.isfinite(g) for g in gaps) else float("nan")
    ok = math.isfinite(gap) and gap <= FD_CHECK_RTOL
    return {"ok": ok, "gap": gap, "check": "fd", "fd_gaps": gaps,
            "reason": "" if ok else (f"the taped reference ran out of device memory and the relative gap {gap:.3e} "
                                     f"to central finite differences exceeds {FD_CHECK_RTOL:.0e}")}


def exact_jvp(solver, x: torch.Tensor, *, kind: str = "tape", coefficient=None):
    """``(product, kind_used)``: the exact Krylov operator ``d -> J(x) d`` of a JFNK step.

    ``kind`` "forward" is checked against "tape" at its first use on ``solver`` (the verdict is kept on the solver
    as ``_forward_jvp_verdict`` and logged once) and falls back to "tape" when it fails; ``kind_used`` says which
    one runs.  ``coefficient`` is ``linearise``'s: ``None`` keeps the case's configured d_f, as ``assemble`` does.
    """
    if kind not in JVP_KINDS:
        raise ValueError(f"unknown jvp {kind!r}; valid: {', '.join(JVP_KINDS)}")
    if kind == "forward":
        verdict = getattr(solver, "_forward_jvp_verdict", None)
        if verdict is None:
            verdict = check_forward_jvp(solver, x, coefficient=coefficient)
            solver._forward_jvp_verdict = verdict
            log = logging.getLogger(__name__)
            if verdict["ok"]:
                log.info("forward-mode JVP checked against the %s: relative gap %.3e",
                         "taped product" if verdict.get("check", "tape") == "tape" else "central differences",
                         verdict["gap"])
            else:
                log.warning("forward-mode JVP REFUSED (%s); the taped product is used for the rest of the run",
                            verdict["reason"])
        if verdict["ok"]:
            return solver.linearise_forward(x, coefficient=coefficient), "forward"
    return solver.linearise(x, coefficient=coefficient), "tape"


class CountedOperator:
    """A Krylov operator that counts its products and the seconds spent in them.

    ``synchronize`` (the CUDA device, or ``None``) makes each reading honest on a GPU: the clock is read after the
    device drained its queue, at the cost of one sync per product.  Without it the seconds are host time.
    """

    def __init__(self, apply, synchronize=None):
        self.apply, self.count, self.seconds = apply, 0, 0.0
        self._device = synchronize

    def _clock(self) -> float:
        if self._device is not None:
            torch.cuda.synchronize(self._device)
        return time.perf_counter()

    def __call__(self, vector: torch.Tensor) -> torch.Tensor:
        began = self._clock()
        out = self.apply(vector)
        self.seconds += self._clock() - began
        self.count += 1
        return out


# ------------------------------------------------------------------------------------------------ records
JFNK_COLUMNS = ("operator", "jvp_kind", "precond_rebuilt", "rebuild_reason", "precond_age", "its_per_decade",
                "jvp_products", "jvp_seconds", "jvp_setup_seconds")


def totals(rows) -> dict:
    """Run totals of the JFNK columns of Newton history rows (rows without them are not counted)."""
    rows = [row for row in rows if "precond_rebuilt" in row]
    reasons = {}
    for row in rows:
        if row["precond_rebuilt"]:
            reasons[row["rebuild_reason"]] = reasons.get(row["rebuild_reason"], 0) + 1
    return {"steps": len(rows),
            "assemblies": sum(1 for row in rows if row.get("reassembled")),
            "builds": sum(1 for row in rows if row["precond_rebuilt"]),
            "jvp_steps": sum(1 for row in rows if row["operator"] == "jvp"),
            "jvp_products": sum(int(row["jvp_products"]) for row in rows),
            "jvp_seconds": sum(float(row["jvp_seconds"]) for row in rows),
            "jvp_setup_seconds": sum(float(row["jvp_setup_seconds"]) for row in rows),
            "rebuild_reasons": reasons}


def describe(summary: dict) -> str:
    """One log line of ``totals``."""
    return ("%d Newton steps, %d assemblies, %d preconditioner builds (%s), %d steps on the JVP operator with "
            "%d products in %.2f s (+ %.2f s taping)" % (
                summary["steps"], summary["assemblies"], summary["builds"],
                ", ".join(f"{k} {v}" for k, v in summary["rebuild_reasons"].items()) or "none",
                summary["jvp_steps"], summary["jvp_products"], summary["jvp_seconds"],
                summary["jvp_setup_seconds"]))
