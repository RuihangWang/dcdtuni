"""Routing wrapper that makes the acausal DAE residual torch.compile-safe.

Evidence base (all measured, torch 2.5.1+cu121, CPU float64):
``docs/benchmarks/dae-baseline-d5388f8a-20260928/README.md`` — probes R1–R5d.

torch 2.5.1 incompatibility class:
  * ``torch.func.jvp`` OUTSIDE a ``torch.compile``'d callable → dynamo silently
    falls back to eager (R2-G: 12/15 frames skipped, instant "bit-exact");
  * ``torch.compile`` OUTSIDE ``torch.func.jvp`` → compiles, but returns WRONG
    tangents (R2-F: diff 32.7 on the CP+HX fixture);
  * a compiled forward's AOT backward executed under a functorch transform
    (``jacobian(vectorize=True)``) or inside a nested backward → hard crash
    ``Cannot access data pointer of Tensor that doesn't have storage``
    (R3/R4/R5a).

Routing rules v3 (validated bit-exact including the crash scenarios, R5c/R5d):
  1. functorch transform active              → eager
  2. grad mode AND any tensor input requires_grad (a graph WILL be built that
     some transform/nested backward could later execute) → eager
  3. otherwise (no_grad or grad-free inputs: Newton merit/norm value calls,
     rollout inference, diagnostics)          → compiled

Measured note: ``torch.autograd.functional.jacobian(vectorize=True)`` calls the
residual forward in plain grad mode (its vmap wraps only the backward batching
phase), so the dense Jacobian path is caught by rule 2, not rule 1; direct
``torch.func.jvp`` matvecs are caught by rule 1.

Routing is counted and exposed (``routing_report``); a compile failure degrades
EXPLICITLY (error log once + latch + fallback counter) and never masquerades as
a compiled result. Newton's value calls in ``dae_newton.py`` run under
``torch.no_grad`` so rule 3 captures the hot value path (~12× warm CPU speedup
measured on the small fixture; GPU numbers come with the S3/S4 episodes).
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Callable, Optional

import torch
from torch import Tensor

from .dae_newton import DAEProblem

logger = logging.getLogger(__name__)

try:  # functorch level detection (private but stable across torch 2.x)
    from torch._C import _functorch as _fc

    def under_functorch_transform() -> bool:
        try:
            return _fc.maybe_current_level() is not None
        except Exception:  # pragma: no cover - defensive
            return False
except ImportError:  # pragma: no cover
    def under_functorch_transform() -> bool:
        return False


def _default_compile_factory(residual: Callable, *, mode: str, dynamic: bool) -> Callable:
    return torch.compile(residual, mode=mode, dynamic=dynamic)


class RoutedCompiledResidual:
    """Residual callable implementing routing rules v3 with exposed counters."""

    def __init__(self, residual: Callable, *, mode: str = "default",
                 dynamic: bool = False, compile_factory: Optional[Callable] = None):
        self._eager = residual
        factory = compile_factory or _default_compile_factory
        self._compiled = factory(residual, mode=mode, dynamic=dynamic)
        self._broken = False
        self._failure_logged = False
        self.n_compiled = 0
        self.n_eager_transform = 0
        self.n_eager_grad = 0
        self.n_eager_fallback = 0

    def __call__(self, *args):
        if under_functorch_transform():
            self.n_eager_transform += 1
            return self._eager(*args)
        if torch.is_grad_enabled() and any(
            isinstance(a, Tensor) and a.requires_grad for a in args
        ):
            self.n_eager_grad += 1
            return self._eager(*args)
        if self._broken:
            self.n_eager_fallback += 1
            return self._eager(*args)
        try:
            result = self._compiled(*args)
        except Exception as exc:
            self._broken = True
            self.n_eager_fallback += 1
            if not self._failure_logged:
                self._failure_logged = True
                logger.error(
                    "compiled DAE residual failed (%r); degrading to eager "
                    "explicitly — this call and all later ones are eager", exc,
                )
            return self._eager(*args)
        self.n_compiled += 1
        return result

    def routing_report(self) -> dict:
        return {
            "n_compiled": self.n_compiled,
            "n_eager_transform": self.n_eager_transform,
            "n_eager_grad": self.n_eager_grad,
            "n_eager_fallback": self.n_eager_fallback,
            "broken_latch": self._broken,
        }


def compiled_problem(problem: DAEProblem, *, mode: str = "default",
                     dynamic: bool = False) -> "tuple[DAEProblem, RoutedCompiledResidual]":
    """Opt-in compiled variant of a DAEProblem; the input problem is untouched.

    Returns ``(routed_problem, router)``. The router exposes ``routing_report()``
    for evidence receipts; any performance claim must quote the routing counts
    alongside timings (compiled numbers without routing evidence are invalid).
    """
    router = RoutedCompiledResidual(problem.residual, mode=mode, dynamic=dynamic)
    return dataclasses.replace(problem, residual=router), router
