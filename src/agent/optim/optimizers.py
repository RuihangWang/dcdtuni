"""Four native optimizers behind one interface: ``optimize(oracle, space, budget, seed, logger, method=...)``.

Extracted from the frozen ``training/e5_native_optimizers_v2.py``, where they
were bound to the controlled-2D task (two box-bounded variables and a lid
direction). Here a :class:`Space` is any box of continuous variables plus a
finite set of discrete choices, and the oracle is any callable::

    oracle(point: Point, fidelity: "HF" | "LF", with_gradient: bool) -> {
        "I0": reward to MAXIMISE (finite, also for a failed evaluation),
        "status": "success" | anything else,
        "I3": d reward / d point.values (physical units; only when asked),
        "costs": {"cpu_seconds": ..., ...},
    }

- ``heuristic``: the box midpoint once per choice.
- ``cma-es``: rank-mu CMA-ES (Hansen 2016, https://arxiv.org/abs/1604.00772)
  with positive recombination weights, rank-one and rank-mu updates and
  cumulative step-size adaptation, on a sigmoid latent transform of the box
  (no clipping); one independent stream per choice, equal attempts.
- ``mfbo``: Kennedy--O'Hagan autoregressive co-kriging
  (https://doi.org/10.1093/biomet/87.1.1) with a cost-aware acquisition: HF
  expected improvement times the fractional HF posterior-variance reduction of
  a query, divided by the median own measured CPU cost of that fidelity; one
  HF query every fourth attempt. A registered adaptation, not BOCA and not
  random search.
- ``adjoint-descent``: projected ascent of the HF reward along the oracle's
  own gradient with Armijo backtracking; a failed reply or a missing gradient
  is kept and never turned into a step.

Every reply is kept in ``history`` (failures included); the incumbent is the
best HF reply actually seen, never an LF one. With 2 variables and 2 choices
the proposals are the frozen module's, value for value (see the tests).
``logger`` (a :class:`agent.records.RunLogger`, optional) receives one
``metrics`` record per reply.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

VERSION = "agent-native-optimizers/v1 (from E5-native-controlled2d-optimizers/v2)"
METHODS = ("heuristic", "cma-es", "mfbo", "adjoint-descent")
Oracle = Callable[["Point", str, bool], Mapping[str, Any]]


def _expit(value):
    from scipy.special import expit
    return expit(value)


@dataclass(frozen=True)
class Point:
    """One design: physical values of the continuous variables and the index of the discrete choice."""
    values: Tuple[float, ...]
    choice: int
    space: "Space" = field(repr=False, compare=False)

    @property
    def label(self) -> Any:
        return self.space.choices[self.choice]

    def as_dict(self) -> Dict[str, Any]:
        return {"values": dict(zip(self.space.names, self.values)), "choice": self.label}


@dataclass(frozen=True)
class Space:
    """A box ``bounds = ((low, high), ...)`` of named continuous variables and discrete ``choices``."""
    bounds: Tuple[Tuple[float, float], ...]
    names: Tuple[str, ...] = ()
    choices: Tuple[Any, ...] = (0,)

    def __post_init__(self):
        bounds = tuple((float(low), float(high)) for low, high in self.bounds)
        if not bounds or any(not (math.isfinite(low) and math.isfinite(high) and high > low) for low, high in bounds):
            raise ValueError("bounds must be a nonempty sequence of finite increasing (low, high) pairs")
        names = tuple(self.names) or tuple(f"x{i}" for i in range(len(bounds)))
        if len(names) != len(bounds) or len(set(names)) != len(names):
            raise ValueError("one distinct name per continuous variable")
        choices = tuple(self.choices)
        if not choices or len(set(map(repr, choices))) != len(choices):
            raise ValueError("at least one distinct discrete choice is required")
        object.__setattr__(self, "bounds", bounds)
        object.__setattr__(self, "names", names)
        object.__setattr__(self, "choices", choices)

    @property
    def dim(self) -> int:
        return len(self.bounds)

    @property
    def lower(self) -> np.ndarray:
        return np.asarray(self.bounds, dtype=float)[:, 0]

    @property
    def width(self) -> np.ndarray:
        return np.diff(np.asarray(self.bounds, dtype=float), axis=1)[:, 0]

    def to_physical(self, unit) -> np.ndarray:
        x = np.asarray(unit, dtype=float)
        if x.shape != (self.dim,) or not np.isfinite(x).all() or (x < 0).any() or (x > 1).any():
            raise ValueError("unit bounds violated")
        return self.lower + self.width * x

    def to_unit(self, values) -> np.ndarray:
        return (np.asarray(values, dtype=float) - self.lower) / self.width

    def point(self, unit, choice: int) -> Point:
        if not 0 <= int(choice) < len(self.choices):
            raise ValueError(f"choice index {choice} outside {len(self.choices)} choices")
        return Point(tuple(self.to_physical(unit).tolist()), int(choice), self)

    def identity(self) -> Dict[str, Any]:
        return {"bounds": [list(b) for b in self.bounds], "names": list(self.names),
                "choices": [repr(c) for c in self.choices]}


class RankMuCMA:
    """Canonical positive-weight rank-mu CMA on the sigmoid latent of the unit box."""

    def __init__(self, seed, dim=2, population=6, sigma=.7):
        self.rng = np.random.default_rng(seed)
        self.n = dim
        self.lam = population
        self.mu = population // 2
        weights = np.log(self.mu + .5) - np.log(np.arange(1, self.mu + 1))
        self.w = weights / weights.sum()
        self.mueff = 1 / np.dot(self.w, self.w)
        self.cc = (4 + self.mueff / dim) / (dim + 4 + 2 * self.mueff / dim)
        self.cs = (self.mueff + 2) / (dim + self.mueff + 5)
        self.c1 = 2 / ((dim + 1.3) ** 2 + self.mueff)
        self.cmu = min(1 - self.c1, 2 * (self.mueff - 2 + 1 / self.mueff) / ((dim + 2) ** 2 + self.mueff))
        self.damps = 1 + 2 * max(0, math.sqrt((self.mueff - 1) / (dim + 1)) - 1) + self.cs
        self.chi = math.sqrt(dim) * (1 - 1 / (4 * dim) + 1 / (21 * dim * dim))
        self.m = np.zeros(dim)
        self.sigma = sigma
        self.C = np.eye(dim)
        self.pc = np.zeros(dim)
        self.ps = np.zeros(dim)
        self.generation = 0

    def ask(self):
        eig, B = np.linalg.eigh((self.C + self.C.T) / 2)
        if eig.min() <= 0:
            raise RuntimeError("CMA covariance not positive definite")
        self.invsqrt = (B * (1 / np.sqrt(eig))) @ B.T
        z = self.rng.normal(size=(self.lam, self.n))
        self.y = z @ (B * np.sqrt(eig)).T
        self.latent = self.m + self.sigma * self.y
        return _expit(self.latent)

    def tell(self, rewards):
        rewards = np.asarray(rewards)
        if rewards.shape != (self.lam,) or not np.isfinite(rewards).all():
            raise ValueError("complete finite CMA population required")
        selected = np.argsort(-rewards, kind="stable")[:self.mu]
        ys = self.y[selected]
        yw = self.w @ ys
        self.m = self.w @ self.latent[selected]
        self.ps = (1 - self.cs) * self.ps + math.sqrt(self.cs * (2 - self.cs) * self.mueff) * (self.invsqrt @ yw)
        self.generation += 1
        normalizer = math.sqrt(1 - (1 - self.cs) ** (2 * self.generation))
        hsig = float(np.linalg.norm(self.ps) / normalizer / self.chi < 1.4 + 2 / (self.n + 1))
        self.pc = (1 - self.cc) * self.pc + hsig * math.sqrt(self.cc * (2 - self.cc) * self.mueff) * yw
        self.C = ((1 - self.c1 - self.cmu) * self.C
                  + self.c1 * (np.outer(self.pc, self.pc) + (1 - hsig) * self.cc * (2 - self.cc) * self.C)
                  + self.cmu * np.einsum("i,ij,ik->jk", self.w, ys, ys))
        self.sigma *= math.exp(self.cs / self.damps * (np.linalg.norm(self.ps) / self.chi - 1))
        self.sigma = float(np.clip(self.sigma, 1e-6, 10.))

    def state(self):
        return {"generation": self.generation, "mean_latent": self.m.tolist(), "sigma": self.sigma,
                "covariance": self.C.tolist(), "step_path": self.ps.tolist(), "covariance_path": self.pc.tolist()}


class AutoregressiveGP:
    """Joint k_LL=kL, k_LH=rho*kL, k_HH=rho^2*kL+kDelta over rows [x..., choice, fidelity]."""

    def __init__(self, length=.3, rho=1., delta=.25, noise=1e-8, *, dim=2):
        self.length = length
        self.rho = rho
        self.delta = delta
        self.noise = noise
        self.dim = dim

    def kernel(self, a, b):
        a = np.asarray(a)
        b = np.asarray(b)
        d = self.dim
        base = (np.exp(-.5 * np.sum(((a[:, None, :d] - b[None, :, :d]) / self.length) ** 2, axis=2))
                * (a[:, None, d] == b[None, :, d]))
        fa = a[:, None, d + 1]
        fb = b[None, :, d + 1]
        return base * ((1 - fa + self.rho * fa) * (1 - fb + self.rho * fb) + self.delta * fa * fb)

    def fit(self, X, y):
        from scipy.linalg import cho_factor, cho_solve
        self.X = np.asarray(X)
        self.y = np.asarray(y)
        matrix = self.kernel(self.X, self.X) + self.noise * np.eye(len(self.X))
        self.factor = cho_factor(matrix, lower=True, check_finite=True)
        self.alpha = cho_solve(self.factor, self.y)
        return self

    def predict(self, X):
        from scipy.linalg import cho_solve
        X = np.asarray(X)
        cross = self.kernel(X, self.X)
        mean = cross @ self.alpha
        variance = np.diag(self.kernel(X, X)) - np.einsum("ij,ji->i", cross, cho_solve(self.factor, cross.T))
        return mean, np.maximum(variance, 1e-12)

    def posterior_cross_diag(self, A, B):
        from scipy.linalg import cho_solve
        A = np.asarray(A)
        B = np.asarray(B)
        prior = np.diag(self.kernel(A, B))
        left = self.kernel(A, self.X)
        right = self.kernel(self.X, B)
        return prior - np.einsum("ij,ji->i", left, cho_solve(self.factor, right))


class MFBO:
    """Cost-aware multi-fidelity acquisition over a scrambled Sobol pool plus named anchor points."""

    def __init__(self, seed, *, dim=2, choices=2, cost_key="cpu_seconds", hf_every=4):
        from scipy.stats import qmc
        self.dim = dim
        self.choices = choices
        self.cost_key = cost_key
        self.hf_every = hf_every
        self.gp = AutoregressiveGP(dim=dim)
        pool = qmc.Sobol(d=dim, scramble=True, seed=seed).random_base2(8)
        anchors = [[.5] * dim]
        for axis in range(dim):
            for end in (0., 1.):
                row = [.5] * dim
                row[axis] = end
                anchors.append(row)
        pool = np.r_[pool, anchors]
        self.pool = np.concatenate([np.c_[pool, np.full(len(pool), c)] for c in range(choices)])
        self.anchors = [(c, "HF") for c in range(choices)] + [(c, "LF") for c in range(choices)]
        self.X: List[List[float]] = []
        self.y: List[float] = []
        self.cpu: Dict[int, List[float]] = {0: [], 1: []}
        self.acquisition_records: List[Dict[str, Any]] = []

    def observe(self, x, choice, fidelity, reward, cpu):
        f = int(fidelity == "HF")
        self.X.append([*np.asarray(x).tolist(), choice, f])
        self.y.append(float(reward))
        if cpu > 0:
            self.cpu[f].append(float(cpu))

    def ask(self):
        # Guaranteed paired anchor observations first, then native joint-GP acquisition.
        if len(self.X) < len(self.anchors):
            choice, fid = self.anchors[len(self.X)]
            return np.full(self.dim, .5), choice, fid, {"initial_paired_anchor": True}
        from scipy.special import ndtr
        d = self.dim
        gp = self.gp.fit(self.X, self.y)
        high = np.c_[self.pool, np.ones(len(self.pool))]
        mean, var = gp.predict(high)
        std = np.sqrt(var)
        incumbent = max(y for row, y in zip(self.X, self.y) if row[d + 1] == 1)
        improvement = mean - incumbent
        z = improvement / std
        ei = improvement * ndtr(z) + std * np.exp(-z * z / 2) / math.sqrt(2 * math.pi)
        observed = np.asarray(self.X)
        candidates = []
        force_HF = (len(self.X) + 1) % self.hf_every == 0
        # Own paid observations only; a calibration is never imported as free oracle truth.
        costs = {f: float(np.median(self.cpu[f])) if self.cpu[f] else 1. for f in (0, 1)}
        for f in ((1,) if force_HF else (0, 1)):
            query = np.c_[self.pool, np.full(len(self.pool), f)]
            _, qvar = gp.predict(query)
            cov = gp.posterior_cross_diag(high, query)
            reduction = np.clip(cov * cov / (qvar + gp.noise) / var, 0., 1.)
            score = ei * reduction / costs[f]
            duplicate = np.any(np.all(np.isclose(query[:, None, :], observed[None, :, :], atol=1e-13, rtol=0.),
                                      axis=2), axis=1)
            score[duplicate] = -np.inf
            i = int(np.argmax(score))
            candidates.append((float(score[i]), i, f, float(ei[i]), float(reduction[i])))
        score, i, f, ei_value, reduction = max(candidates, key=lambda v: (v[0], v[2], -v[1]))
        if not math.isfinite(score):
            raise RuntimeError("MFBO candidate acquisition exhausted")
        record = {"HF_expected_improvement": ei_value, "HF_fractional_variance_reduction": reduction,
                  "cost_estimate_CPU_seconds": costs[f], "acquisition_score": score, "forced_HF_cadence": force_HF}
        self.acquisition_records.append(record)
        return self.pool[i, :d].copy(), int(self.pool[i, d]), "HF" if f else "LF", record


class AdjointArm:
    """Projected ascent of the HF reward along its native gradient, with Armijo backtracking."""

    def __init__(self, width, *, initial_step=.25, armijo=1e-4):
        self.width = np.asarray(width, dtype=float)
        self.initial_step = initial_step
        self.armijo = armijo
        self.current = np.full(len(self.width), .5)
        self.anchor = None
        self.anchor_reward = None
        self.anchor_grad = None
        self.step = initial_step
        self.pending = None

    def ask(self):
        return self.current.copy() if self.pending is None else self.pending.copy()

    def tell(self, x, result):
        grad = result.get("I3")
        if result.get("status") != "success" or grad is None:
            # A failed solve/adjoint retains its reward/cost and cannot synthesize a step.
            self.pending = None
            return {"accepted": False, "reason": "native HF gradient unavailable"}
        grad = np.asarray(grad, dtype=float) * self.width
        if grad.shape != self.width.shape or not np.isfinite(grad).all():
            raise ValueError("invalid native reward I3")
        reward = float(result["I0"])
        accepted = self.anchor is None
        if self.anchor is not None:
            threshold = self.anchor_reward + self.armijo * float(self.anchor_grad @ (x - self.anchor))
            accepted = reward >= threshold
        if accepted:
            self.current = x.copy()
            self.anchor = x.copy()
            self.anchor_reward = reward
            self.anchor_grad = grad.copy()
            self.step = self.initial_step
        else:
            self.step *= .5
        direction = self.anchor_grad / max(np.linalg.norm(self.anchor_grad), 1.)
        self.pending = np.clip(self.anchor + self.step * direction, 0., 1.)
        # Do not silently buy repetitive evaluations at a stationary projected point.
        stationary = bool(np.max(np.abs(self.pending - self.anchor)) < 1e-10 or self.step < 1e-8)
        return {"accepted": accepted, "step_unit_coordinates": self.step, "stationary": stationary,
                "native_I3_sign": "reward ascent"}


def _cap(budget) -> int:
    cap = getattr(budget, "max_attempts", budget)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ValueError(f"budget must be a positive attempt count (or have max_attempts), got {budget!r}")
    return cap


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def optimize(oracle: Oracle, space: Space, budget, seed: int, logger=None, *, method: str = "cma-es",
             on_reply: Optional[Callable[[Dict[str, Any], Optional[Dict[str, Any]]], None]] = None,
             population: int = 6, sigma: float = .7, cost_key: str = "cpu_seconds") -> Dict[str, Any]:
    """Run ``method`` on ``oracle`` over ``space`` for at most ``budget`` attempts.

    ``budget`` is an attempt count (or an object with ``max_attempts``); a
    budget object that also charges cost should be enforced by the oracle
    itself (e.g. :func:`agent.tools.fidelity.evaluate` raises
    ``BudgetExhausted``), which ends the run with the paid history intact via
    ``on_reply``/``logger``.
    """
    if method not in METHODS:
        raise ValueError(f"unknown optimizer method {method!r}; choose from {METHODS}")
    cap = _cap(budget)
    n = len(space.choices)
    history: List[Dict[str, Any]] = []
    best: Optional[Dict[str, Any]] = None

    def evaluate(x, choice, fidelity, with_gradient=False, metadata=None):
        nonlocal best
        point = space.point(x, choice)
        result = oracle(point, fidelity, with_gradient)
        reward = float(result["I0"])
        if not math.isfinite(reward):
            raise ValueError("the oracle must return a finite reward, also for a failed evaluation")
        record = {"attempt": len(history) + 1, "design": point.as_dict(), "fidelity": fidelity,
                  "gradient_requested": with_gradient, "status": result.get("status"), "I0": reward,
                  "I1": result.get("I1"), "I3": result.get("I3"), "costs": dict(result.get("costs") or {}),
                  "optimizer_metadata": metadata or {}}
        if fidelity == "HF" and (best is None or reward > best["I0"]):
            best = {"design": record["design"], "I0": reward, "status": record["status"],
                    "attempt": record["attempt"]}
        history.append(record)
        if logger is not None:
            logger.metrics(step=record["attempt"], values=_jsonable({**record, "method": method}))
        if on_reply:
            on_reply(record, best)
        return result

    middle = np.full(space.dim, .5)
    if method == "heuristic":
        for choice in range(min(n, cap)):
            evaluate(middle, choice, "HF", metadata={"rule": "neutral box-midpoint proposal; choice enumeration"})
    elif method == "cma-es":
        arms = [RankMuCMA(seed + 10007 * c, dim=space.dim, population=population, sigma=sigma) for c in range(n)]
        pending: List[list] = [[] for _ in range(n)]
        rewards: List[list] = [[] for _ in range(n)]
        while len(history) < cap:
            choice = len(history) % n
            if not pending[choice]:
                pending[choice] = list(arms[choice].ask())
                rewards[choice] = []
            x = pending[choice].pop(0)
            result = evaluate(x, choice, "HF", metadata={"choice_stream": choice,
                                                         "generation": arms[choice].generation})
            rewards[choice].append(float(result["I0"]))
            if not pending[choice]:
                arms[choice].tell(rewards[choice])
    elif method == "mfbo":
        state = MFBO(seed, dim=space.dim, choices=n, cost_key=cost_key)
        while len(history) < cap:
            x, choice, fidelity, metadata = state.ask()
            result = evaluate(x, choice, fidelity, metadata=metadata)
            state.observe(x, choice, fidelity, result["I0"], (result.get("costs") or {}).get(cost_key, 0.))
    else:
        arms = [AdjointArm(space.width) for _ in range(n)]
        finished: set = set()
        while len(history) < cap and len(finished) < n:
            choice = len(history) % n
            while choice in finished:
                choice = (choice + 1) % n
            x = arms[choice].ask()
            result = evaluate(x, choice, "HF", True, metadata={"choice_stream": choice, "native_target_adjoint": True})
            state = arms[choice].tell(x, result)
            if state.get("stationary"):
                finished.add(choice)
    summary = {"format": VERSION, "method": method, "seed": seed, "space": space.identity(),
               "attempts": len(history), "HF_attempts": sum(r["fidelity"] == "HF" for r in history),
               "LF_attempts": sum(r["fidelity"] == "LF" for r in history), "history": history,
               "incumbent_HF": best, "stop": "stationary_or_heuristic_terminal" if len(history) < cap else "attempt_cap"}
    if logger is not None:
        logger.event("optimizer_finished", method=method, attempts=summary["attempts"],
                     incumbent_HF=_jsonable(best), stop=summary["stop"])
    return summary
