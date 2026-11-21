"""Method of Manufactured Solutions for the liquid-cooling thermal transport.

References
----------
[1] P. J. Roache, "Code Verification by the Method of Manufactured Solutions",
    Journal of Fluids Engineering 124(1), 4-10, 2002.
[2] K. Salari and P. Knupp, "Code Verification by the Method of Manufactured
    Solutions", Sandia report SAND2000-1444, 2000.
[3] W. L. Oberkampf and C. J. Roy, "Verification and Validation in Scientific
    Computing", Cambridge University Press, 2010, chapter 6.
[4] C. J. Roy, "Review of code and solution verification procedures for
    computational simulation", J. Comput. Phys. 205(1), 131-156, 2005.

What is being verified
----------------------
The fluid energy transport along a pipe.  Both solve paths implement the same
balance per cell; ``solver/hybrid.py::_solve_thermal_direct`` marches it as

    C_cell*(T_i^{n+1} - T_i^n)/dt = |m|*cp*(T_{i-1}^{n+1} - T_i^{n+1}) + Q_i^{n+1}

with ``C_cell = rho*cp*A*dx``.  Dividing by ``rho*cp*A*dx`` and writing
``u = m/(rho*A)``, ``S = Q/(rho*cp*A*dx)``:

    (T_i^{n+1} - T_i^n)/dt + u*(T_i^{n+1} - T_{i-1}^{n+1})/dx = S_i^{n+1}

which is the advection equation ``dT/dt + u dT/dx = S`` discretised with
implicit BDF-1 in time and first-order upwind in space.  The formal order of
accuracy is therefore **1 in space and 1 in time**, and that is the number MMS
must reproduce.  A scheme that scores lower has a bug; one that scores much
higher usually means the manufactured solution is degenerate (see [2] §3).

The manufactured solution
-------------------------
The governing equation has no source term of its own, so MMS supplies one:
choose ``T_ms`` first, substitute it into the operator, and whatever is left
over is the source ``S`` that makes ``T_ms`` the exact solution.

    T_ms(x, t) = T0 + A_amp*sin(2*pi*x/L + phi)*exp(-t/tau)
    S(x, t)    = exp(-t/tau)*A_amp*[ u*(2*pi/L)*cos(2*pi*x/L + phi)
                                     - (1/tau)*sin(2*pi*x/L + phi) ]

Requirements from [2] §3.2 that this choice meets:

  * smooth and infinitely differentiable, so the truncation error is clean;
  * non-trivial in **both** x and t, so neither operator can be silently wrong;
  * the two terms cannot cancel — one is a sine, the other a cosine of the same
    argument, so their sum vanishes only on a measure-zero set;
  * bounded and physically plausible (a few K about a realistic coolant
    temperature), so round-off never competes with discretisation error.

``phi`` is deliberately non-zero.  With ``phi = 0`` the inlet value would be
exactly ``T0`` for all time, and a wrong inlet boundary condition could pass
unnoticed.

``tau`` is set to ``1/(u*k) = L/(2*pi*u)`` so that the unsteady amplitude
``amp/tau`` equals the advective amplitude ``u*amp*k``.  With the two terms
unbalanced the weaker one contributes so little error that a combined
refinement study silently reports the stronger one's order, and a bug in the
weaker operator survives.

Three studies, following [3] section 6.4 — verify the terms together AND in
isolation:

  1. **combined** — refine dx and dt together; measures the global order.
  2. **steady** (``steady_case``) — freeze time, refine dx; isolates space.
  3. **temporal** (``temporal_case``, ``u = 0``) — refine dt; isolates time.

Refining only one variable in the full case does not isolate anything: the
other's error stays put as a floor that flattens the measured order.

The source is sampled **pointwise at the cell centre** rather than averaged over
the cell.  For a first-order scheme the difference between the two is O(dx^2),
one order below the discretisation error, so it cannot change the observed
order; ``test_mms.py`` measures this rather than assuming it.

A known, expected feature of the residual field: with cell-centred first-order
upwind and a Dirichlet inlet, the FIRST cell's truncation error is O(1) rather
than O(dx), because the exact inlet face value leaves nothing to cancel the
upwinded outlet face error.  The scheme is still globally first order — a
boundary truncation error one order below the interior, acting over a single
cell, preserves the global order (Gustafsson, Math. Comp. 29, 1975) — and the
solution-error studies here measure exactly that.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import torch

from backend.liquidcooling.graphcompiling.compiled_graph import (
    CompiledGraph,
    PipeBatchParams,
    PipeIndices,
)


# ---------------------------------------------------------------------------
# The manufactured solution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MMSCase:
    """A manufactured solution for ``dT/dt + u dT/dx = S`` on a single pipe.

    ``steady`` drops the time dependence (``exp(-t/tau)`` becomes 1 and the
    ``dT/dt`` contribution to the source disappears), which isolates the spatial
    operator.  Everything else is shared, so the two variants exercise the same
    code path with the same geometry.
    """

    L: float = 2.0            # pipe length [m]
    D: float = 0.02           # pipe inner diameter [m]
    u: float = 0.8            # bulk velocity [m/s]
    T0: float = 308.15        # mean temperature [K]
    amp: float = 5.0          # temperature amplitude [K]
    phi: float = 0.7          # phase [rad] — non-zero on purpose
    # Decay time constant [s].  Chosen as 1/(u*k) = L/(2*pi*u) so that the
    # unsteady term amp/tau and the advective term u*amp*k have EQUAL amplitude
    # — otherwise the weaker operator contributes so little error that the
    # study silently measures the stronger one's order alone.
    tau: float = 2.0 / (2.0 * math.pi * 0.8)
    t_end: float = 0.6        # final time [s]
    rho: float = 998.2        # fluid density [kg/m^3]
    cp: float = 4182.0        # fluid specific heat [J/(kg K)]
    mu: float = 1.0e-3        # dynamic viscosity [Pa s]
    steady: bool = False

    # -- geometry ---------------------------------------------------------
    @property
    def area(self) -> float:
        """Pipe cross-sectional area [m^2]."""
        return math.pi * (self.D / 2.0) ** 2

    @property
    def m_dot(self) -> float:
        """Mass flow that realises the bulk velocity ``u`` [kg/s]."""
        return self.rho * self.area * self.u

    # -- the solution and its source --------------------------------------
    def _decay(self, t: float) -> float:
        return 1.0 if self.steady else math.exp(-t / self.tau)

    def T(self, x: float, t: float) -> float:
        """Exact temperature [K] at arc length ``x`` and time ``t``."""
        k = 2.0 * math.pi / self.L
        return self.T0 + self.amp * math.sin(k * x + self.phi) * self._decay(t)

    def S(self, x: float, t: float) -> float:
        """Source term [K/s] that makes :meth:`T` the exact solution."""
        k = 2.0 * math.pi / self.L
        decay = self._decay(t)
        advect = self.u * k * math.cos(k * x + self.phi)
        if self.steady:
            return self.amp * advect
        unsteady = -(1.0 / self.tau) * math.sin(k * x + self.phi)
        return decay * self.amp * (advect + unsteady)

    def cell_heat_W(self, x: float, t: float, cell_volume: float) -> float:
        """The source expressed as the code wants it: watts in one cell.

        ``S`` is a temperature rate [K/s]; the discrete balance carries watts,
        so multiply by the cell's thermal capacitance ``rho*cp*V``.
        """
        return self.rho * self.cp * cell_volume * self.S(x, t)


def transient_case(**kw) -> MMSCase:
    """The full space-and-time manufactured solution."""
    return MMSCase(steady=False, **kw)


def steady_case(**kw) -> MMSCase:
    """The steady variant, which isolates the spatial operator."""
    return MMSCase(steady=True, **kw)


def temporal_case(**kw) -> MMSCase:
    """The zero-velocity variant, which isolates the temporal operator.

    With ``u = 0`` the spatial operator vanishes and every cell becomes an
    independent ODE ``dT/dt = S(x, t)`` marched by backward Euler, so refining
    ``dt`` measures the time integrator alone.  Refining ``dt`` in the full case
    cannot do this: the spatial error stays put and becomes a floor that
    flattens the measured order.  Oberkampf & Roy [3] section 6.4 recommends
    verifying each term in isolation as well as together, and this is the
    isolation for the time term.
    """
    kw.setdefault("u", 0.0)
    return MMSCase(steady=False, **kw)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_mms_graph(case: MMSCase, n_cells: int, n_steps: int) -> CompiledGraph:
    """Build a single-pipe :class:`CompiledGraph` carrying the MMS case.

    ``n_steps`` is the number of time *steps*; the graph holds ``n_steps + 1``
    time levels so that level 0 is the initial condition.

    The graph is built directly rather than through the config compiler so the
    mass flow, the initial field and the inlet boundary values are exactly the
    manufactured ones — MMS verifies one operator at a time, and folding in a
    hydraulic solve would blur which operator an error came from.
    """
    if n_cells < 1:
        raise ValueError("n_cells must be >= 1")
    if n_steps < 1:
        raise ValueError("n_steps must be >= 1")

    N_t = n_steps + 1
    dt = case.t_end / n_steps
    dx = case.L / n_cells
    area = case.area
    cell_vol = area * dx
    dtype = torch.float64

    # --- cells: centres at (c + 0.5)*dx, matching graphcompiling/compiler.py
    cell_x = [(c + 0.5) * dx for c in range(n_cells)]
    cell_position = torch.zeros(n_cells, 3, dtype=dtype)
    for c, x in enumerate(cell_x):
        cell_position[c, 0] = x

    # --- the manufactured source, in watts, at the NEW time level of each step
    # The scheme is implicit, so the step that produces level n uses the source
    # at level n.  Level 0 is never used by the march; it is filled for shape.
    cell_heat_W = torch.zeros(n_cells, N_t, dtype=dtype)
    for c, x in enumerate(cell_x):
        for n in range(N_t):
            cell_heat_W[c, n] = case.cell_heat_W(x, n * dt, cell_vol)

    # --- initial field and inlet history, both exact
    cell_T_init = torch.tensor(
        [case.T(x, 0.0) for x in cell_x], dtype=dtype
    )
    bc_T_series = torch.zeros(2, N_t, dtype=dtype)
    for n in range(N_t):
        bc_T_series[0, n] = case.T(0.0, n * dt)   # inlet node, x = 0

    node_position = torch.zeros(2, 3, dtype=dtype)
    node_position[1, 0] = case.L

    return CompiledGraph(
        N_nodes=2,
        N_edges=1,
        N_cells=n_cells,
        N_cp=0,
        N_t=N_t,
        node_ids=["inlet", "outlet"],
        node_position=node_position,
        bc_mask=torch.tensor([True, False]),
        bc_P=torch.tensor([101325.0, 0.0], dtype=dtype),
        bc_T=torch.tensor([case.T(0.0, 0.0), 0.0], dtype=dtype),
        bc_T_mask=torch.tensor([True, False]),
        junction_mask=torch.tensor([False, False]),
        N_junctions=0,
        max_junction_depth=0,
        junction_indices=torch.zeros(0, dtype=torch.long),
        node_to_junction=torch.tensor([-1, -1], dtype=torch.long),
        edge_str_ids=["mms_pipe"],
        edge_from=torch.tensor([0], dtype=torch.long),
        edge_to=torch.tensor([1], dtype=torch.long),
        edge_type=torch.tensor([0], dtype=torch.long),
        incidence=torch.tensor([[1.0], [-1.0]], dtype=dtype),
        pipe_indices=PipeIndices(
            edge_ids=torch.tensor([0], dtype=torch.long),
            node_from=torch.tensor([0], dtype=torch.long),
            node_to=torch.tensor([1], dtype=torch.long),
            cell_start=torch.tensor([0], dtype=torch.long),
            cell_end=torch.tensor([n_cells], dtype=torch.long),
        ),
        cp_indices=None,
        valve_indices=None,
        pump_indices=None,
        hx_indices=None,
        pipe_params=PipeBatchParams(
            D=torch.tensor([case.D], dtype=dtype),
            L=torch.tensor([case.L], dtype=dtype),
            dh=torch.tensor([case.D], dtype=dtype),
            K_minor=torch.tensor([0.0], dtype=dtype),
            A=torch.tensor([area], dtype=dtype),
            roughness=torch.tensor([1.5e-6], dtype=dtype),
        ),
        cp_params=None,
        valve_params=None,
        pump_params=None,
        hx_params=None,
        N_hx=0,
        cell_edge_id=torch.zeros(n_cells, dtype=torch.long),
        cell_ds=torch.full((n_cells,), dx, dtype=dtype),
        cell_volume=torch.full((n_cells,), cell_vol, dtype=dtype),
        cell_dh=torch.full((n_cells,), case.D, dtype=dtype),
        cell_position=cell_position,
        node_in_edges=[[], [0]],
        node_out_edges=[[0], []],
        dt=dt,
        bdf_order=1,
        T_fluid_init=case.T0,
        T_solid_init=case.T0,
        P_init=101325.0,
        solver_config={"max_iter": 200, "tol": 1e-12},
        rack_meta=[],
        fluid_rho=case.rho,
        fluid_cp=case.cp,
        fluid_mu=case.mu,
        g_mag=9.81,
        cell_heat_W=cell_heat_W,
        cell_T_init=cell_T_init,
        bc_T_series=bc_T_series,
    )


# ---------------------------------------------------------------------------
# Running one refinement level
# ---------------------------------------------------------------------------

def run_mms(case: MMSCase, n_cells: int, n_steps: int) -> dict:
    """Solve one MMS refinement level and return its discrete error norms.

    The mass flow is imposed, not solved for: MMS here targets the **thermal**
    operator, and letting the Newton hydraulics pick ``m`` would mix a second
    discretisation into the same error number.

    Returns a dict with ``l2`` (RMS error over cells at the final time),
    ``linf``, ``dx``, ``dt``, and the numerical/exact fields.
    """
    from backend.liquidcooling.solver.hybrid import _solve_thermal_direct
    from backend.liquidcooling.solver.model import LiquidODILModel

    graph = build_mms_graph(case, n_cells, n_steps)
    model = LiquidODILModel(graph)

    # Impose the exact, constant mass flow on every time level.
    with torch.no_grad():
        model.state["m"].data.fill_(case.m_dot)

    _solve_thermal_direct(model, graph)

    T_num = model.state["T_w"].data[:, -1].cpu()
    dx = case.L / n_cells
    dt = case.t_end / n_steps
    T_exact = torch.tensor(
        [case.T((c + 0.5) * dx, case.t_end) for c in range(n_cells)],
        dtype=T_num.dtype,
    )

    err = T_num - T_exact
    return {
        "n_cells": n_cells,
        "n_steps": n_steps,
        "dx": dx,
        "dt": dt,
        "l2": float(err.pow(2).mean().sqrt()),
        "linf": float(err.abs().max()),
        "T_num": T_num,
        "T_exact": T_exact,
    }


# ---------------------------------------------------------------------------
# Observed order of accuracy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ObservedOrder:
    """The outcome of a grid-refinement study."""

    levels: list[dict]
    orders: list[float]

    @property
    def mean_order(self) -> float:
        return sum(self.orders) / len(self.orders) if self.orders else float("nan")

    @property
    def finest_order(self) -> float:
        return self.orders[-1] if self.orders else float("nan")

    @property
    def monotone(self) -> bool:
        """True when every refinement reduced the error."""
        return all(
            b["l2"] < a["l2"] for a, b in zip(self.levels, self.levels[1:])
        )

    def table(self) -> str:
        """A human-readable refinement table."""
        rows = [
            f"{'cells':>7} {'steps':>7} {'dx':>10} {'dt':>10} "
            f"{'L2 err':>12} {'Linf err':>12} {'order':>7}"
        ]
        for i, lv in enumerate(self.levels):
            order = "" if i == 0 else f"{self.orders[i - 1]:7.3f}"
            rows.append(
                f"{lv['n_cells']:7d} {lv['n_steps']:7d} {lv['dx']:10.3e} "
                f"{lv['dt']:10.3e} {lv['l2']:12.5e} {lv['linf']:12.5e} {order:>7}"
            )
        return "\n".join(rows)


def observed_order(
    case: MMSCase,
    n_cells_0: int = 20,
    n_steps_0: int = 20,
    n_levels: int = 4,
    refine: int = 2,
    refine_time: bool = True,
    refine_space: bool = True,
    runner: Callable[[MMSCase, int, int], dict] | None = None,
) -> ObservedOrder:
    """Run a grid-refinement sequence and compute the observed order.

    ``dx`` and ``dt`` are refined **together** by the same factor by default.
    The scheme is first order in both, so refining only one leaves the other's
    error as a floor that flattens the measured order — a classic way to make a
    correct code look broken, discussed in [3] §6.4 and [4] §3.

    Set ``refine_time=False`` to hold ``dt`` fixed, which is what the steady
    variant wants; set ``refine_space=False`` to hold ``dx`` fixed, which is
    what the ``u = 0`` temporal variant wants.  Holding the wrong one fixed
    measures nothing: with ``u = 0`` there is no spatial operator, so refining
    ``dx`` alone leaves the error untouched and reports an order of exactly 0.
    """
    if n_levels < 2:
        raise ValueError("need at least 2 levels to measure an order")
    if not (refine_time or refine_space):
        raise ValueError("refining neither dx nor dt cannot measure an order")
    run = runner or run_mms

    levels: list[dict] = []
    n_cells, n_steps = n_cells_0, n_steps_0
    for _ in range(n_levels):
        levels.append(run(case, n_cells, n_steps))
        if refine_space:
            n_cells *= refine
        if refine_time:
            n_steps *= refine

    orders = [
        math.log(a["l2"] / b["l2"]) / math.log(refine)
        for a, b in zip(levels, levels[1:])
    ]
    return ObservedOrder(levels=levels, orders=orders)
