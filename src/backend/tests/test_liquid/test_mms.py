"""Method of Manufactured Solutions verification of the liquid-cooling thermal solve.

These tests answer "are we solving the equations right?" by measuring the
observed order of accuracy against an exact solution.  The formal order of the
scheme (implicit BDF-1 in time, first-order upwind in space) is 1 in both
variables, so 1 is the number every test below expects.

See ``backend/liquidcooling/verification/mms.py`` for the derivation and the
literature it follows (Roache 2002; Salari & Knupp SAND2000-1444; Oberkampf &
Roy 2010).
"""
from __future__ import annotations

import math

import pytest
import torch

from backend.liquidcooling.verification import (
    build_mms_graph,
    observed_order,
    run_mms,
    steady_case,
    temporal_case,
    transient_case,
)

# The observed order approaches the formal order from below as the grid is
# refined, so the finest pair is the one held to a tight tolerance.
ORDER_TOL_FINEST = 0.10
ORDER_TOL_MEAN = 0.15


# ---------------------------------------------------------------------------
# The manufactured solution itself
# ---------------------------------------------------------------------------

class TestManufacturedSolution:
    """The solution and its source must be mutually consistent before use."""

    def test_source_matches_the_operator(self):
        """S must equal dT/dt + u dT/dx for the manufactured T, to round-off.

        Checked by finite differences of T rather than by re-deriving the
        analytic form, so an algebra slip in either expression is caught.
        """
        c = transient_case()
        h = 1e-6
        for x in (0.13, 0.77, 1.41, 1.93):
            for t in (0.0, 0.21, 0.6):
                dTdt = (c.T(x, t + h) - c.T(x, t - h)) / (2 * h)
                dTdx = (c.T(x + h, t) - c.T(x - h, t)) / (2 * h)
                assert c.S(x, t) == pytest.approx(dTdt + c.u * dTdx, abs=1e-5)

    def test_steady_source_matches_the_operator(self):
        c = steady_case()
        h = 1e-6
        for x in (0.13, 0.77, 1.41, 1.93):
            dTdx = (c.T(x + h, 0.0) - c.T(x - h, 0.0)) / (2 * h)
            assert c.S(x, 0.0) == pytest.approx(c.u * dTdx, abs=1e-5)

    def test_the_two_operators_are_balanced(self):
        """Neither operator may be weak enough to hide a bug inside it.

        If the unsteady term is much smaller than the advective one, a combined
        refinement study reports the advective order and a broken time
        integrator survives it — so the amplitudes are held to within a factor
        of two of each other.  Amplitudes are taken over the whole domain: at
        any single point either derivative can pass through zero.
        """
        c = transient_case()
        k = 2 * math.pi / c.L
        unsteady = c.amp / c.tau          # max |dT/dt| at t = 0
        advective = c.u * c.amp * k       # max |u dT/dx| at t = 0
        assert unsteady > 1.0, "time derivative is too weak to verify"
        assert advective > 1.0, "advection is too weak to verify"
        ratio = max(unsteady, advective) / min(unsteady, advective)
        assert ratio < 2.0, (
            f"unsteady {unsteady:.3f} K/s vs advective {advective:.3f} K/s "
            f"differ by {ratio:.2f}x — the weaker operator is under-verified"
        )

    def test_the_two_terms_do_not_cancel(self):
        """A solution whose two terms cancel lets a solver that drops BOTH win."""
        c = transient_case()
        h = 1e-6
        worst = min(
            abs(
                (c.T(x, t + h) - c.T(x, t - h)) / (2 * h)
                + c.u * (c.T(x + h, t) - c.T(x - h, t)) / (2 * h)
            )
            / max(
                abs((c.T(x, t + h) - c.T(x, t - h)) / (2 * h)),
                abs(c.u * (c.T(x + h, t) - c.T(x - h, t)) / (2 * h)),
                1e-12,
            )
            for x in (0.11, 0.53, 0.97, 1.33, 1.79)
            for t in (0.0, 0.3, 0.6)
        )
        assert worst > 0.1, f"the two terms nearly cancel somewhere (ratio {worst:.4f})"

    def test_inlet_value_is_not_trivial(self):
        """phi != 0, so a wrong inlet boundary condition cannot pass unseen."""
        c = transient_case()
        assert abs(c.T(0.0, 0.0) - c.T0) > 1.0


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

class TestMMSGraph:
    def test_cell_centres_match_the_compiler_convention(self):
        """Cells are centred at (c + 0.5)*dx, as graphcompiling/compiler.py does.

        The whole error measurement hangs on sampling the exact solution where
        the code believes its unknowns live.
        """
        c = transient_case()
        n = 8
        g = build_mms_graph(c, n, 4)
        dx = c.L / n
        for i in range(n):
            assert g.cell_position[i, 0].item() == pytest.approx((i + 0.5) * dx)

    def test_source_is_capacitance_weighted(self):
        """cell_heat_W carries watts, i.e. rho*cp*V*S, not S."""
        c = transient_case()
        n, steps = 8, 4
        g = build_mms_graph(c, n, steps)
        dx, dt = c.L / n, c.t_end / steps
        cell_vol = c.area * dx
        expected = c.rho * c.cp * cell_vol * c.S(0.5 * dx, dt)
        assert g.cell_heat_W[0, 1].item() == pytest.approx(expected, rel=1e-12)

    def test_initial_and_inlet_fields_are_exact(self):
        c = transient_case()
        n, steps = 8, 4
        g = build_mms_graph(c, n, steps)
        dx, dt = c.L / n, c.t_end / steps
        for i in range(n):
            assert g.cell_T_init[i].item() == pytest.approx(c.T((i + 0.5) * dx, 0.0))
        for k in range(steps + 1):
            assert g.bc_T_series[0, k].item() == pytest.approx(c.T(0.0, k * dt))

    def test_zero_source_leaves_ordinary_runs_untouched(self):
        """The hooks are optional: a graph without them must stay as it was.

        This is the guarantee that MMS support did not change any real case.
        """
        from backend.liquidcooling.graphcompiling.compiled_graph import CompiledGraph
        import dataclasses

        defaults = {
            f.name: f.default
            for f in dataclasses.fields(CompiledGraph)
            if f.name in ("cell_heat_W", "cell_T_init", "bc_T_series")
        }
        assert defaults == {
            "cell_heat_W": None,
            "cell_T_init": None,
            "bc_T_series": None,
        }


# ---------------------------------------------------------------------------
# Order of accuracy — the direct (production) solve path
# ---------------------------------------------------------------------------

class TestCombinedOrderOfAccuracy:
    """dx and dt refined together; expected observed order 1."""

    @pytest.fixture(scope="class")
    def study(self):
        # Five levels: with both operators balanced the combined study
        # approaches order 1 from below more slowly than either isolation,
        # because the leading error now carries a dx term, a dt term and their
        # cross term.  Four levels stop at ~0.93; five reach ~0.96.
        return observed_order(
            transient_case(), n_cells_0=20, n_steps_0=20, n_levels=5, refine=2
        )

    def test_error_decreases_monotonically(self, study):
        assert study.monotone, f"error did not fall on every refinement:\n{study.table()}"

    def test_observed_order_approaches_one(self, study):
        assert study.finest_order == pytest.approx(1.0, abs=ORDER_TOL_FINEST), (
            f"finest observed order {study.finest_order:.4f} is not 1:\n{study.table()}"
        )

    def test_mean_order_is_first_order(self, study):
        assert study.mean_order == pytest.approx(1.0, abs=ORDER_TOL_MEAN), study.table()

    def test_order_improves_with_refinement(self, study):
        """Asymptotic convergence: each order estimate is closer to 1 than the last."""
        gaps = [abs(o - 1.0) for o in study.orders]
        assert all(b < a for a, b in zip(gaps, gaps[1:])), study.table()


class TestSteadyOrderOfAccuracy:
    """dt fixed, marched to steady state; isolates the spatial operator."""

    @pytest.fixture(scope="class")
    def case(self):
        # 12 s is 4.8 residence times at L/u = 2.5 s.
        return steady_case(t_end=12.0)

    @pytest.fixture(scope="class")
    def study(self, case):
        return observed_order(
            case, n_cells_0=20, n_steps_0=300, n_levels=4, refine=2, refine_time=False
        )

    def test_march_actually_reached_steady_state(self, case):
        """An unconverged march would contaminate the spatial order."""
        from backend.liquidcooling.solver.hybrid import _solve_thermal_direct
        from backend.liquidcooling.solver.model import LiquidODILModel

        g = build_mms_graph(case, 40, 300)
        model = LiquidODILModel(g)
        with torch.no_grad():
            model.state["m"].data.fill_(case.m_dot)
        _solve_thermal_direct(model, g)
        T_w = model.state["T_w"].data
        drift = (T_w[:, -1] - T_w[:, -2]).abs().max().item()
        assert drift < 1e-9, f"still transient at the final step: drift {drift:.3e} K"

    def test_error_decreases_monotonically(self, study):
        assert study.monotone, study.table()

    def test_observed_order_is_one(self, study):
        assert study.finest_order == pytest.approx(1.0, abs=ORDER_TOL_FINEST), study.table()

    def test_every_level_is_first_order(self, study):
        for o in study.orders:
            assert o == pytest.approx(1.0, abs=ORDER_TOL_FINEST), study.table()


class TestTemporalOrderOfAccuracy:
    """u = 0, dx fixed, dt refined; isolates the time integrator.

    Refining dt in the FULL case cannot do this — the spatial error stays put as
    a floor that flattens the measured order.  Removing advection entirely turns
    each cell into an independent backward-Euler ODE, so what is measured is the
    time integrator and nothing else.
    """

    @pytest.fixture(scope="class")
    def study(self):
        return observed_order(
            temporal_case(),
            n_cells_0=40,
            n_steps_0=20,
            n_levels=4,
            refine=2,
            refine_space=False,
        )

    def test_the_spatial_operator_really_is_absent(self):
        """With u = 0, refining dx alone must change nothing at all.

        This is the control: if refining dx moved the error, advection would
        still be active and the study would not be a temporal isolation.
        """
        control = observed_order(
            temporal_case(),
            n_cells_0=40,
            n_steps_0=20,
            n_levels=3,
            refine=2,
            refine_time=False,
        )
        for o in control.orders:
            assert abs(o) < 1e-6, f"refining dx changed the error: {control.table()}"

    def test_error_decreases_monotonically(self, study):
        assert study.monotone, study.table()

    def test_observed_order_is_one(self, study):
        assert study.finest_order == pytest.approx(1.0, abs=ORDER_TOL_FINEST), study.table()

    def test_every_level_is_first_order(self, study):
        for o in study.orders:
            assert o == pytest.approx(1.0, abs=ORDER_TOL_MEAN), study.table()


# ---------------------------------------------------------------------------
# Order of accuracy — the residual (Adam-ODIL) solve path
# ---------------------------------------------------------------------------

def _mms_residual(case, n: int) -> torch.Tensor:
    """Plug the exact solution into ``PipeBatchHandler.compute_residuals``.

    Returned normalised by the cell capacitance, so the units are K/s and the
    numbers are directly comparable to the source term.
    """
    from backend.liquidcooling.solver.model import LiquidODILModel

    g = build_mms_graph(case, n, n)
    model = LiquidODILModel(g)
    dx, dt = case.L / n, case.t_end / n
    with torch.no_grad():
        model.state["m"].data.fill_(case.m_dot)
    T_w = torch.tensor(
        [[case.T((i + 0.5) * dx, k * dt) for k in range(n + 1)] for i in range(n)],
        dtype=torch.float64,
    )
    T_all = torch.tensor(
        [
            [case.T(0.0, k * dt) for k in range(n + 1)],
            [case.T(case.L, k * dt) for k in range(n + 1)],
        ],
        dtype=torch.float64,
    )
    state = {
        "m": model.state["m"].data,
        "T_w": T_w,
        "T_all_nodes": T_all,
        "P": model.state["P"].data,
    }
    res = model.handlers["pipe"].compute_residuals(
        state, g.pipe_indices, dt=dt, bdf_order=1
    )
    capacitance = case.rho * case.cp * case.area * dx
    return res["energy"] / capacitance


class TestResidualConsistency:
    """The second solve path must discretise the same equation, consistently.

    ``hybrid_solve`` marches the balance directly; ``adam_odil`` minimises the
    residual form of it.  Both need the manufactured source, and both are
    verified — a solver is only as trustworthy as its least-checked path.
    """

    @pytest.fixture(scope="class")
    def residuals(self):
        c = transient_case()
        return c, {n: _mms_residual(c, n) for n in (20, 40, 80, 160)}

    def test_initial_condition_residual_is_exactly_zero(self, residuals):
        """With the per-cell IC hook, the exact initial field satisfies the IC."""
        c, _ = residuals
        from backend.liquidcooling.solver.model import LiquidODILModel

        n = 20
        g = build_mms_graph(c, n, n)
        model = LiquidODILModel(g)
        dx, dt = c.L / n, c.t_end / n
        T_w = torch.tensor(
            [[c.T((i + 0.5) * dx, k * dt) for k in range(n + 1)] for i in range(n)],
            dtype=torch.float64,
        )
        state = {
            "m": model.state["m"].data,
            "T_w": T_w,
            "T_all_nodes": torch.tensor(
                [
                    [c.T(0.0, k * dt) for k in range(n + 1)],
                    [c.T(c.L, k * dt) for k in range(n + 1)],
                ],
                dtype=torch.float64,
            ),
            "P": model.state["P"].data,
        }
        res = model.handlers["pipe"].compute_residuals(
            state, g.pipe_indices, dt=dt, bdf_order=1
        )
        assert res["ic"].abs().max().item() < 1e-9

    def test_interior_truncation_error_is_first_order(self, residuals):
        """Away from the inlet, the residual falls as O(h)."""
        _, res = residuals
        ns = sorted(res)
        norms = [res[n][1:].abs().max().item() for n in ns]
        orders = [
            math.log(a / b) / math.log(2) for a, b in zip(norms, norms[1:])
        ]
        assert orders[-1] == pytest.approx(1.0, abs=ORDER_TOL_FINEST), (
            f"interior residual orders {orders}, norms {norms}"
        )

    def test_inlet_cell_carries_the_known_boundary_artifact(self, residuals):
        """The first cell's truncation error is O(1), and that is expected.

        With cell-centred first-order upwind and a Dirichlet inlet, the inlet
        face value is exact while the outlet face is upwinded, so the two
        O(dx/2 * dT/dx) flux errors that cancel in the interior do not cancel
        in the first cell.  The residual there tends to u/2 * dT/dx|_(x=0).

        The scheme is still globally first order — a boundary truncation error
        one order below the interior, acting over a single cell, preserves the
        global order (Gustafsson, Math. Comp. 29, 1975).  The solution-error
        studies above measure exactly that, and this test pins the mechanism so
        the plateau is never mistaken for a regression.
        """
        c, res = residuals
        predicted = 0.5 * c.u * c.amp * (2 * math.pi / c.L) * math.cos(c.phi)
        finest = res[max(res)][0].abs().max().item()
        assert finest == pytest.approx(predicted, rel=0.05), (
            f"inlet-cell residual {finest:.4f} does not match the predicted "
            f"boundary artifact {predicted:.4f}"
        )

    def test_smooth_operators_do_not_pollute_the_measurement(self, residuals):
        """sigmoid(m*1000) and smooth_abs(m, 1e-6) must be negligible here.

        Both are differentiable stand-ins used so the ODIL path stays smooth.
        If either were active at the mass flows used, it would show up as an
        error floor and hide a genuine order loss.
        """
        from backend.liquidcooling.solver.bdf import smooth_abs

        c, _ = residuals
        m = torch.tensor([c.m_dot], dtype=torch.float64)
        assert (1.0 - torch.sigmoid(m * 1000.0)).abs().max().item() < 1e-15
        rel = abs(smooth_abs(m).item() - abs(c.m_dot)) / abs(c.m_dot)
        assert rel < 1e-9


# ---------------------------------------------------------------------------
# Source sampling
# ---------------------------------------------------------------------------

class TestSourceSampling:
    """Pointwise vs cell-averaged source must not change the observed order.

    Sampling S at the cell centre instead of averaging it over the cell is an
    O(dx^2) difference — one order below the discretisation error — so it can
    change the error slightly but not the order.  Measured, not assumed.
    """

    def test_cell_averaged_source_gives_the_same_order(self):
        c = transient_case()

        def run_cell_averaged(case, n_cells, n_steps):
            from backend.liquidcooling.solver.hybrid import _solve_thermal_direct
            from backend.liquidcooling.solver.model import LiquidODILModel

            g = build_mms_graph(case, n_cells, n_steps)
            dx, dt = case.L / n_cells, case.t_end / n_steps
            k = 2 * math.pi / case.L
            for ci in range(n_cells):
                xl, xr = ci * dx, (ci + 1) * dx
                i_cos = (math.sin(k * xr + case.phi) - math.sin(k * xl + case.phi)) / k
                i_sin = (-math.cos(k * xr + case.phi) + math.cos(k * xl + case.phi)) / k
                for nn in range(n_steps + 1):
                    decay = math.exp(-(nn * dt) / case.tau)
                    s_bar = decay * case.amp * (
                        case.u * k * i_cos - (1.0 / case.tau) * i_sin
                    ) / dx
                    g.cell_heat_W[ci, nn] = (
                        case.rho * case.cp * case.area * dx * s_bar
                    )
            model = LiquidODILModel(g)
            with torch.no_grad():
                model.state["m"].data.fill_(case.m_dot)
            _solve_thermal_direct(model, g)
            t_num = model.state["T_w"].data[:, -1].cpu()
            t_exact = torch.tensor(
                [case.T((i + 0.5) * dx, case.t_end) for i in range(n_cells)],
                dtype=t_num.dtype,
            )
            err = t_num - t_exact
            return {
                "n_cells": n_cells,
                "n_steps": n_steps,
                "dx": dx,
                "dt": dt,
                "l2": float(err.pow(2).mean().sqrt()),
                "linf": float(err.abs().max()),
            }

        pointwise = observed_order(c, 20, 20, 4, 2)
        averaged = observed_order(c, 20, 20, 4, 2, runner=run_cell_averaged)
        assert averaged.finest_order == pytest.approx(
            pointwise.finest_order, abs=0.02
        ), f"pointwise {pointwise.orders} vs averaged {averaged.orders}"


# ---------------------------------------------------------------------------
# Guards on the harness itself
# ---------------------------------------------------------------------------

class TestHarnessCatchesBrokenSolvers:
    """An MMS study that cannot fail proves nothing.

    Each case here perturbs the physics and asserts the study notices, which is
    the red half of the red-green cycle for a verification harness.
    """

    def test_wrong_velocity_destroys_the_order(self):
        """Solve with a mass flow the source was not built for."""
        c = transient_case()

        def run_wrong_u(case, n_cells, n_steps):
            from backend.liquidcooling.solver.hybrid import _solve_thermal_direct
            from backend.liquidcooling.solver.model import LiquidODILModel

            g = build_mms_graph(case, n_cells, n_steps)
            model = LiquidODILModel(g)
            with torch.no_grad():
                model.state["m"].data.fill_(case.m_dot * 1.5)  # <-- the bug
            _solve_thermal_direct(model, g)
            dx = case.L / n_cells
            t_num = model.state["T_w"].data[:, -1].cpu()
            t_exact = torch.tensor(
                [case.T((i + 0.5) * dx, case.t_end) for i in range(n_cells)],
                dtype=t_num.dtype,
            )
            err = t_num - t_exact
            return {
                "n_cells": n_cells,
                "n_steps": n_steps,
                "dx": dx,
                "dt": case.t_end / n_steps,
                "l2": float(err.pow(2).mean().sqrt()),
                "linf": float(err.abs().max()),
            }

        study = observed_order(c, 20, 20, 4, 2, runner=run_wrong_u)
        assert abs(study.finest_order - 1.0) > ORDER_TOL_FINEST, (
            f"a 50% velocity error still scored order {study.finest_order:.3f} — "
            f"the harness is not sensitive enough:\n{study.table()}"
        )

    def test_missing_source_destroys_the_order(self):
        """Drop the manufactured source; the solution can no longer be reached."""
        c = transient_case()

        def run_no_source(case, n_cells, n_steps):
            from backend.liquidcooling.solver.hybrid import _solve_thermal_direct
            from backend.liquidcooling.solver.model import LiquidODILModel

            g = build_mms_graph(case, n_cells, n_steps)
            g.cell_heat_W.zero_()  # <-- the bug
            model = LiquidODILModel(g)
            with torch.no_grad():
                model.state["m"].data.fill_(case.m_dot)
            _solve_thermal_direct(model, g)
            dx = case.L / n_cells
            t_num = model.state["T_w"].data[:, -1].cpu()
            t_exact = torch.tensor(
                [case.T((i + 0.5) * dx, case.t_end) for i in range(n_cells)],
                dtype=t_num.dtype,
            )
            err = t_num - t_exact
            return {
                "n_cells": n_cells,
                "n_steps": n_steps,
                "dx": dx,
                "dt": case.t_end / n_steps,
                "l2": float(err.pow(2).mean().sqrt()),
                "linf": float(err.abs().max()),
            }

        study = observed_order(c, 20, 20, 4, 2, runner=run_no_source)
        assert abs(study.finest_order - 1.0) > ORDER_TOL_FINEST, (
            f"dropping the source still scored order {study.finest_order:.3f}:\n"
            f"{study.table()}"
        )

    def test_wrong_inlet_boundary_condition_destroys_the_order(self):
        """phi != 0 exists so this fails; if it passes, the phase is degenerate."""
        c = transient_case()

        def run_wrong_inlet(case, n_cells, n_steps):
            from backend.liquidcooling.solver.hybrid import _solve_thermal_direct
            from backend.liquidcooling.solver.model import LiquidODILModel

            g = build_mms_graph(case, n_cells, n_steps)
            g.bc_T_series[0, :] = case.T0  # <-- the bug: mean instead of exact
            model = LiquidODILModel(g)
            with torch.no_grad():
                model.state["m"].data.fill_(case.m_dot)
            _solve_thermal_direct(model, g)
            dx = case.L / n_cells
            t_num = model.state["T_w"].data[:, -1].cpu()
            t_exact = torch.tensor(
                [case.T((i + 0.5) * dx, case.t_end) for i in range(n_cells)],
                dtype=t_num.dtype,
            )
            err = t_num - t_exact
            return {
                "n_cells": n_cells,
                "n_steps": n_steps,
                "dx": dx,
                "dt": case.t_end / n_steps,
                "l2": float(err.pow(2).mean().sqrt()),
                "linf": float(err.abs().max()),
            }

        study = observed_order(c, 20, 20, 4, 2, runner=run_wrong_inlet)
        assert abs(study.finest_order - 1.0) > ORDER_TOL_FINEST, (
            f"a wrong inlet BC still scored order {study.finest_order:.3f}:\n"
            f"{study.table()}"
        )
