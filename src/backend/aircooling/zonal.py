"""Differentiable zonal pressure–enthalpy airflow network (a low-fidelity room model).

Nodes are well-mixed air zones; links are passive openings (orifice law, with
the stack-effect pressure from the Boussinesq density difference), machines
with an imposed volume flow (server fans: inlet zone -> outlet zone, heat added
at the outlet; cooling units: return zone -> a supply node whose temperature is
imposed). Unknowns are the node pressures (node 0 is the gauge) and the
temperatures of the nodes without an imposed temperature. The residual is the
volume balance of every node but the gauge and the enthalpy balance of every
free node; Newton with a backtracking line search solves it, and the solution
is differentiable through an implicit adjoint (one transposed Jacobian solve).

The structure follows the airflow-network model of EnergyPlus
(https://bigladdersoftware.com/epx/docs/24-2/engineering-reference/airflownetwork-model.html).
Passage areas and discharge coefficients are declared, not calibrated; the
model is not CFD and provides no flow field.

Ported from the frozen research branch (``src/agent/scenario_air/l0_network.py``
at 7065ce31) with the same equations.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import torch
from torch.nn import functional as fn

VERSION = "zonal-pressure-enthalpy-network/v1"


@dataclass(frozen=True)
class ZonalNetwork:
    node_names: tuple[str, ...]
    height_m: tuple[float, ...]
    passive_pairs: tuple[tuple[int, int], ...]
    directed: tuple[bool, ...]                 # True: flow only from pair[0] to pair[1]
    server_pairs: tuple[tuple[int, int], ...]  # inlet node -> outlet node
    server_flow_m3s: tuple[float, ...]
    server_power_kw: tuple[float, ...]         # heat added at the outlet node
    unit_pairs: tuple[tuple[int, int], ...]    # return node -> supply node (imposed temperature)
    rho: float = 1.18
    cp: float = 1006.0
    discharge_coefficient: float = 0.62
    pressure_smoothing_pa: float = 0.01
    thermal_flow_smoothing_m3s: float = 1e-8
    buoyancy_beta_per_k: float = 0.00333
    gravity_m_s2: float = 9.81
    reference_c: float = 22.0
    temperature_scale_k: float = 20.0
    # Exchange (mixing) flow through every two-way opening, per m2 of effective area: turbulent and
    # counter-current exchange the single orifice law does not carry. It moves heat, not mass, and keeps
    # the temperature of a zone with no net through-flow determined. 0 reproduces the pure orifice model.
    mixing_m3s_per_m2: float = 0.0

    def __post_init__(self):
        n = len(self.node_names)
        if n < 3 or len(set(self.node_names)) != n or len(self.height_m) != n:
            raise ValueError("named nodes with one height each")
        if len(self.passive_pairs) != len(self.directed):
            raise ValueError("one direction flag per passive link")
        if not (len(self.server_pairs) == len(self.server_flow_m3s) == len(self.server_power_kw)):
            raise ValueError("one flow and one power per server")
        if not self.unit_pairs:
            raise ValueError("at least one cooling unit")
        for a, b in self.passive_pairs + self.server_pairs + self.unit_pairs:
            if a == b or min(a, b) < 0 or max(a, b) >= n:
                raise ValueError("links join two distinct existing nodes")
        if any(not math.isfinite(x) or x <= 0 for x in self.server_flow_m3s):
            raise ValueError("server flows must be positive")
        if any(not math.isfinite(x) or x < 0 for x in self.server_power_kw):
            raise ValueError("server heat must be nonnegative")
        for v in (self.rho, self.cp, self.discharge_coefficient, self.pressure_smoothing_pa,
                  self.thermal_flow_smoothing_m3s, self.temperature_scale_k):
            if not math.isfinite(v) or v <= 0:
                raise ValueError("physical and smoothing constants must be positive")
        if not math.isfinite(self.mixing_m3s_per_m2) or self.mixing_m3s_per_m2 < 0:
            raise ValueError("mixing flow per area must be nonnegative")
        supplies = [b for _, b in self.unit_pairs]
        if len(set(supplies)) != len(supplies):
            raise ValueError("each cooling unit needs its own supply node")
        adjacency = [set() for _ in range(n)]
        for a, b in self.passive_pairs + self.server_pairs + self.unit_pairs:
            adjacency[a].add(b)
            adjacency[b].add(a)
        seen, todo = {0}, [0]
        while todo:
            for j in adjacency[todo.pop()] - seen:
                seen.add(j)
                todo.append(j)
        if len(seen) != n:
            raise ValueError("the network must be connected (one pressure gauge)")

    # ------------------------------------------------------------ layout
    @property
    def n_nodes(self) -> int:
        return len(self.node_names)

    @property
    def n_units(self) -> int:
        return len(self.unit_pairs)

    @property
    def state_size(self) -> int:
        return 2 * self.n_nodes - 1 - self.n_units

    def _operators(self, like):
        n = self.n_nodes
        pairs = self.passive_pairs + self.server_pairs + self.unit_pairs
        a = torch.tensor([p[0] for p in pairs], dtype=torch.long, device=like.device)
        b = torch.tensor([p[1] for p in pairs], dtype=torch.long, device=like.device)
        B = like.new_zeros((n, len(pairs)))
        col = torch.arange(len(pairs), device=like.device)
        B[a, col] = 1.0
        B[b, col] = -1.0
        supply = [p[1] for p in self.unit_pairs]
        free = [i for i in range(n) if i not in supply]
        return a, b, B, torch.tensor(supply, device=like.device), torch.tensor(free, device=like.device)

    # ------------------------------------------------------------ equations
    def fields(self, state, controls, areas):
        """controls = (unit supply temperatures degC, unit flows m3/s); areas = passive effective areas m2."""
        n, nu, ne = self.n_nodes, self.n_units, len(self.passive_pairs)
        a, b, B, supply, free = self._operators(state)
        pressure = torch.cat((state.new_zeros(1), state[:n - 1]))
        theta = state.new_zeros(n)
        theta = theta.index_copy(0, supply, (controls[:nu] - self.reference_c) / self.temperature_scale_k)
        theta = theta.index_copy(0, free, state[n - 1:])
        h = state.new_tensor(self.height_m)
        ia, ib = a[:ne], b[:ne]
        dp = pressure[ia] - pressure[ib] + self.rho * self.gravity_m_s2 * self.buoyancy_beta_per_k \
            * self.temperature_scale_k * 0.5 * (theta[ia] + theta[ib]) * (h[ib] - h[ia])
        coef = self.discharge_coefficient * areas * math.sqrt(2.0 / self.rho)
        pe = self.pressure_smoothing_pa
        signed = coef * dp / (dp.square() + pe ** 2).pow(0.25)
        # one-way law: sqrt of a softplus-smoothed positive part; the small offset keeps the derivative
        # finite where the softplus underflows, and a 1e-4 fraction of the two-way law keeps it nonzero
        # for reverse pressure (numerical regularization, 0.01 % reverse flow)
        delta = 1e-9 * pe
        forward = coef * ((pe * fn.softplus(dp / pe) + delta).sqrt() - math.sqrt(delta)) + 1e-4 * signed
        directed = torch.tensor(self.directed, device=state.device)
        passive = torch.where(directed, forward, signed)
        fixed = torch.cat((state.new_tensor(self.server_flow_m3s), controls[nu:]))
        q = torch.cat((passive, fixed))
        qplus = 0.5 * (torch.sqrt(passive.square() + self.thermal_flow_smoothing_m3s ** 2) + passive)
        qminus = qplus - passive
        ef_passive = torch.where(directed, passive * theta[ia], qplus * theta[ia] - qminus * theta[ib])
        if self.mixing_m3s_per_m2 > 0:
            mix = torch.where(directed, torch.zeros_like(areas), self.mixing_m3s_per_m2 * areas)
            ef_passive = ef_passive + mix * (theta[ia] - theta[ib])
        ef = torch.cat((ef_passive, fixed * theta[a[ne:]]))
        mass = B @ q
        heat = state.new_zeros(n)
        if self.server_pairs:
            outlet = torch.tensor([p[1] for p in self.server_pairs], device=state.device)
            heat = heat.index_add(0, outlet, state.new_tensor(self.server_power_kw) * 1000.0
                                  / (self.rho * self.cp * self.temperature_scale_k))
        thermal = B @ ef - heat
        return pressure, theta, passive, mass, thermal, free, supply

    def residual(self, state, controls, areas):
        _, _, _, mass, thermal, free, _ = self.fields(state, controls, areas)
        return torch.cat((mass[1:], thermal[free]))

    def _check_inputs(self, controls, areas):
        if controls.ndim != 1 or len(controls) != 2 * self.n_units:
            raise ValueError("controls: unit supply temperatures, then unit flows")
        if areas.ndim != 1 or len(areas) != len(self.passive_pairs):
            raise ValueError("one effective area per passive link")
        if controls.dtype != torch.float64 or areas.dtype != torch.float64:
            raise ValueError("float64 inputs")
        if not torch.isfinite(controls).all() or (controls[self.n_units:] <= 0).any():
            raise ValueError("finite temperatures and positive unit flows")
        if not torch.isfinite(areas).all() or (areas <= 0).any():
            raise ValueError("positive effective areas")

    def solve(self, controls, areas, *, tolerance: float = 1e-10, max_iterations: int = 100):
        """Damped Newton on the residual; pseudo-transient continuation if Newton does not converge.

        Newton: a full step is backtracked until the squared residual satisfies the Armijo condition, with
        a Levenberg-Marquardt step as the fallback when no Newton step is accepted. If that does not reach
        the tolerance, the solve restarts with pseudo-transient continuation (switched evolution
        relaxation: (J + I/tau) du = -r, tau grows as the residual falls), which follows a pseudo-time
        path to the steady state and hands over to Newton near it. Converged when the max-norm of the
        residual is below ``tolerance``; raises otherwise. Temperatures start at the mean supply
        temperature, pressures at zero.
        """
        self._check_inputs(controls, areas)
        t0 = time.perf_counter()
        start = self.initial_state(controls, areas)
        res = lambda u: self.residual(u, controls, areas)  # noqa: E731
        with torch.enable_grad():
            state, info = self._newton(res, start, tolerance, max_iterations)
            if state is None:
                state, info = self._pseudo_transient(res, start, tolerance, 4 * max_iterations)
        if state is None or not torch.isfinite(state).all():
            raise RuntimeError(f"zonal network: no converged state ({info['reason']})")
        info["wall_s"] = time.perf_counter() - t0
        return state.detach(), info

    INITIAL_PICARD_ITERATIONS = 60

    def initial_state(self, controls, areas):
        """Pressures of the isothermal network at the mean supply temperature; that temperature everywhere.

        The pressures solve the network's mass balance with the orifice law of every passive link (stack
        term at the uniform temperature included, one-way links treated as two-way), by Picard iteration:
        each pass solves the linear network q_i = G_i dp_i with G_i = Cd A_i sqrt(2/rho) / (dp_i^2 +
        pe^2)^(1/4) taken at the previous pass's pressures (damped by the geometric mean of the old and new
        conductance), starting from G_i at 1 Pa. Starting from zero pressure, Newton can stall where an
        enclosure must pressurize to push its imposed fan flow out through small leaks; from the 1 Pa
        linear network alone it crawled (pseudo-transient iteration limit) when a floor plenum had to reach
        about 140 Pa through its tiles (perimeter families at high flow, 2026-10-21). The Picard pressures
        satisfy the isothermal mass balance, so Newton starts next to the coupled solution."""
        n, nu, ne = self.n_nodes, self.n_units, len(self.passive_pairs)
        g = (self.discharge_coefficient * areas * math.sqrt(2.0 / self.rho)).detach()
        theta = float(((controls[:nu] - self.reference_c) / self.temperature_scale_k).mean())
        ia = torch.tensor([p[0] for p in self.passive_pairs], dtype=torch.long)
        ib = torch.tensor([p[1] for p in self.passive_pairs], dtype=torch.long)
        h = controls.new_tensor(self.height_m)
        stack = self.rho * self.gravity_m_s2 * self.buoyancy_beta_per_k * self.temperature_scale_k * theta * (h[ib] - h[ia])
        src = controls.new_zeros(n)
        fixed = list(zip(self.server_pairs, self.server_flow_m3s)) + \
            list(zip(self.unit_pairs, controls[nu:].detach().tolist()))
        for (a, b), q in fixed:
            src[a] -= q
            src[b] += q
        state = controls.new_zeros(self.state_size)
        G, p_best = g.clone(), None
        pe = self.pressure_smoothing_pa
        for _ in range(self.INITIAL_PICARD_ITERATIONS + 1):
            L = controls.new_zeros((n, n))
            L.index_put_((ia, ia), G, accumulate=True)
            L.index_put_((ib, ib), G, accumulate=True)
            L.index_put_((ia, ib), -G, accumulate=True)
            L.index_put_((ib, ia), -G, accumulate=True)
            rhs = src.clone()
            rhs.index_add_(0, ia, -G * stack)
            rhs.index_add_(0, ib, G * stack)
            try:
                p = torch.linalg.solve(L[1:, 1:], rhs[1:])
            except RuntimeError:
                break
            if not torch.isfinite(p).all():
                break
            p_best = p
            pf = torch.cat((p.new_zeros(1), p))
            dp = pf[ia] - pf[ib] + stack
            G_new = g / (dp.square() + pe ** 2).pow(0.25)
            change = float(((G_new / G).log().abs()).max())
            G = (G * G_new).sqrt()
            if change < 1e-6:
                break
        if p_best is not None:
            state[:n - 1] = p_best
        state[n - 1:] = theta
        return state.detach()

    @staticmethod
    def _jacobian(res, state):
        return torch.autograd.functional.jacobian(res, state, vectorize=True).detach()

    def _newton(self, res, state, tolerance, max_iterations):
        lm_steps = 0
        for it in range(max_iterations):
            r = res(state).detach()
            norm = float(r.abs().max())
            if norm < tolerance:
                return state, {"method": "newton", "iterations": it, "residual_inf": norm,
                               "levenberg_marquardt_steps": lm_steps}
            f0 = float(r @ r)
            J = self._jacobian(res, state)
            accepted = False
            try:
                step = torch.linalg.solve(J, -r)
                ok = bool(torch.isfinite(step).all())
            except RuntimeError:
                ok = False
            if ok:
                scale = 1.0
                for _ in range(40):
                    cand = state + scale * step
                    r2 = res(cand).detach()
                    f = float(r2 @ r2)
                    if math.isfinite(f) and f <= (1.0 - 1e-4 * scale) * f0:
                        state, accepted = cand, True
                        break
                    scale *= 0.5
            if not accepted:
                JtJ, g = J.T @ J, J.T @ r
                base = float(JtJ.diagonal().max())
                for lam in (1e-8, 1e-6, 1e-4, 1e-2, 1.0, 1e2):
                    step = torch.linalg.solve(JtJ + lam * base * torch.eye(len(state), dtype=J.dtype), -g)
                    cand = state + step
                    r2 = res(cand).detach()
                    f = float(r2 @ r2)
                    if math.isfinite(f) and f < f0:
                        state, accepted = cand, True
                        lm_steps += 1
                        break
            if not accepted:
                return None, {"reason": "newton: no step reduces the residual"}
        return None, {"reason": "newton: iteration limit"}

    def _pseudo_transient(self, res, state, tolerance, max_iterations):
        r = res(state).detach()
        norm = float(r.abs().max())
        J = self._jacobian(res, state)
        tau = 1.0 / max(float(J.diagonal().abs().max()), 1e-12)
        eye = torch.eye(len(state), dtype=J.dtype)
        for it in range(max_iterations):
            if norm < tolerance:
                return state, {"method": "pseudo-transient", "iterations": it, "residual_inf": norm,
                               "levenberg_marquardt_steps": 0}
            step = torch.linalg.solve(J + eye / tau, -r)
            cand = state + step
            r2 = res(cand).detach()
            n2 = float(r2.abs().max())
            if not math.isfinite(n2):
                tau *= 0.25
                continue
            tau = min(tau * max(0.1, min(10.0, norm / max(n2, 1e-300))), 1e12)
            state, r, norm = cand, r2, n2
            J = self._jacobian(res, state)
        return None, {"reason": "pseudo-transient: iteration limit"}

    def differentiable_state(self, controls, areas):
        return _Implicit.apply(controls, areas, self)

    def temperatures(self, state, controls, areas) -> dict:
        p, t, q, m, e, free, supply = self.fields(state, controls, areas)
        T = self.reference_c + self.temperature_scale_k * t
        ids = lambda xs: torch.tensor(xs, dtype=torch.long, device=state.device)  # noqa: E731
        kw = self.rho * self.cp * self.temperature_scale_k / 1000.0
        return {
            "node_c": T,
            "server_inlet_c": T[ids([a for a, _ in self.server_pairs])] if self.server_pairs else T[:0],
            "unit_return_c": T[ids([a for a, _ in self.unit_pairs])],
            "unit_supply_c": T[supply],
            "pressure_pa": p, "passive_flow_m3s": q, "mass_residual_m3s": m,
            "thermal_residual_kw": e * kw,
            # heat removed at each supply node: inflow enthalpy minus outflow enthalpy at the imposed temperature
            "unit_coil_kw": -e[supply] * kw,
        }


class _Implicit(torch.autograd.Function):
    @staticmethod
    def forward(ctx, controls, areas, network):
        state, _ = network.solve(controls.detach(), areas.detach())
        ctx.network = network
        ctx.save_for_backward(state, controls, areas)
        return state

    @staticmethod
    def backward(ctx, grad_state):
        state, controls, areas = ctx.saved_tensors
        net = ctx.network
        with torch.enable_grad():
            u = state.detach().requires_grad_(True)
            c = controls.detach().requires_grad_(True)
            a = areas.detach().requires_grad_(True)
            J = torch.autograd.functional.jacobian(lambda x: net.residual(x, c, a), u, vectorize=True)
            adjoint = torch.linalg.solve(J.T, grad_state)
            r = net.residual(u, c, a)
            dc, da = torch.autograd.grad(r, (c, a), grad_outputs=-adjoint)
        return dc, da, None
