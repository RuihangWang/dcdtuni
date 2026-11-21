# Phase 1: Liquid Cooling Solver Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a steady-state liquid cooling network solver as `src/backend/liquidcooling/`, capable of computing pressure, flow rate, and temperature for D2C data center cooling systems.

**Architecture:** PyTorch-based hydraulic component models (`nn.Module`) connected via a NetworkX directed graph. Levenberg-Marquardt solver with sparse Jacobian finds steady-state pressure/flow. Thermal models compute chip temperatures and liquid/air heat split. Pydantic v2 schemas validate input JSON. All code written from scratch, referencing DiffLiquidTwin for physics equations only.

**Tech Stack:** Python 3.10+, PyTorch (float64), NetworkX, Pydantic v2, loguru, unittest

**Design Spec:** `AGENTS/specs/2026-04-09-liquid-cooling-integration-design.md`

**Reference Code (read-only, do NOT import):**
- `Ref_code/DiffLiquidTwin/model/component/` — physics equations for pipe, pump, cold plate, valve, HX, quick connect
- `Ref_code/DiffLiquidTwin/manager/hydraulic_fast.py` — sparse LM solver algorithm
- `Ref_code/extracted/dctwin/models/computing/d2c_server.py` — chip thermal model, hybrid cooling eta

---

## File Map

### New Files to Create

| File | Responsibility |
|------|---------------|
| `src/backend/liquidcooling/__init__.py` | Module docstring, version |
| `src/backend/liquidcooling/components/__init__.py` | Export all component classes |
| `src/backend/liquidcooling/components/pipe.py` | `PressureDrivenPipe` — Darcy-Weisbach with elevation |
| `src/backend/liquidcooling/components/pump.py` | `PumpWithSpeed` — affinity-law pump |
| `src/backend/liquidcooling/components/cold_plate.py` | `ColdPlate` — quadratic dP-Q |
| `src/backend/liquidcooling/components/valve.py` | `ControlValveKv` — Kv flow coefficient |
| `src/backend/liquidcooling/components/heat_exchanger.py` | `HeatExchanger` — quadratic dP-Q |
| `src/backend/liquidcooling/components/quick_connect.py` | `QuickConnector` — quadratic loss |
| `src/backend/liquidcooling/devices/__init__.py` | Export device classes |
| `src/backend/liquidcooling/devices/manifold.py` | `Manifold` — parallel branch flow distribution |
| `src/backend/liquidcooling/devices/cdu.py` | `CDU` — black-box + component-level composite |
| `src/backend/liquidcooling/solver/__init__.py` | Export solve functions |
| `src/backend/liquidcooling/solver/steady.py` | `solve_steady()` — LM with sparse Jacobian |
| `src/backend/liquidcooling/thermal/__init__.py` | Export thermal model functions |
| `src/backend/liquidcooling/thermal/chip_model.py` | `compute_chip_max_temp()` |
| `src/backend/liquidcooling/thermal/hybrid_cooling.py` | `compute_heat_capture_ratio()` |
| `src/backend/liquidcooling/topology.py` | `build_network()` — cfd_export JSON to NetworkX graph |
| `src/backend/liquidcooling/factory.py` | `create_component()` — spec dict to nn.Module |
| `src/backend/liquidcooling/results.py` | `LiquidResult`, `ServerResult`, `CDUResult` dataclasses |
| `src/backend/liquidcooling/schema.py` | Pydantic v2 models for liquidCooling JSON |
| `tests/test_liquid/__init__.py` | Test package |
| `tests/test_liquid/test_components.py` | Unit tests for all 6 hydraulic components |
| `tests/test_liquid/test_solver.py` | Tests for steady-state solver |
| `tests/test_liquid/test_thermal.py` | Tests for chip temp and eta models |
| `tests/test_liquid/test_topology.py` | Tests for auto topology generation |
| `tests/test_liquid/test_schema.py` | Tests for Pydantic schema validation |
| `tests/test_liquid/test_integration.py` | End-to-end test with reference data |

### Files to Modify

| File | Change |
|------|--------|
| `pyproject.toml` | Add `torch >= 2.2.0` and `networkx >= 3.0` to dependencies |

---

## Task 1: Project Setup

**Files:**
- Modify: `pyproject.toml`
- Create: `src/backend/liquidcooling/__init__.py`
- Create: `src/backend/liquidcooling/components/__init__.py`
- Create: `src/backend/liquidcooling/devices/__init__.py`
- Create: `src/backend/liquidcooling/solver/__init__.py`
- Create: `src/backend/liquidcooling/thermal/__init__.py`
- Create: `tests/test_liquid/__init__.py`

- [ ] **Step 1: Add torch and networkx to pyproject.toml**

In `pyproject.toml`, add under `[tool.poetry.dependencies]`:

```toml
torch = ">=2.2.0"
networkx = ">=3.0"
```

- [ ] **Step 2: Create module directory structure**

```bash
mkdir -p src/backend/liquidcooling/components
mkdir -p src/backend/liquidcooling/devices
mkdir -p src/backend/liquidcooling/solver
mkdir -p src/backend/liquidcooling/thermal
mkdir -p tests/test_liquid
```

- [ ] **Step 3: Write __init__.py files**

`src/backend/liquidcooling/__init__.py`:
```python
"""
Liquid cooling network simulation module.

Steady-state hydraulic solver for data center liquid cooling systems
including pipes, pumps, cold plates, control valves, heat exchangers,
and CDU (Coolant Distribution Unit) assemblies.
"""
```

`src/backend/liquidcooling/components/__init__.py`:
```python
"""Hydraulic component models (PyTorch nn.Module)."""
```

`src/backend/liquidcooling/devices/__init__.py`:
```python
"""Device-level assemblies (CDU, Manifold)."""
```

`src/backend/liquidcooling/solver/__init__.py`:
```python
"""Hydraulic network solvers."""
```

`src/backend/liquidcooling/thermal/__init__.py`:
```python
"""Thermal models for chip temperature and heat load distribution."""
```

`tests/test_liquid/__init__.py`:
```python
```

- [ ] **Step 4: Verify import works**

```bash
conda run -n dcdtuni python -c "import dcdtuni.liquid; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/backend/liquidcooling/ tests/test_liquid/
git commit -m "feat(liquid): scaffold liquid cooling module structure"
```

---

## Task 2: Pipe Component

**Files:**
- Create: `src/backend/liquidcooling/components/pipe.py`
- Create: `tests/test_liquid/test_components.py`

**Physics:** Darcy-Weisbach pipe flow with gravitational elevation:
```
ΔP_eff = (P_in - P_out) × 1000 + ρ g Δz   [Pa]
f = 64/Re (laminar) or 0.3164 Re^-0.25 (turbulent, Blasius)
v = √(2|ΔP_eff| / (ρ f L/D))
Q = sign(ΔP_eff) × A × v × 60000   [LPM]
```

- [ ] **Step 1: Write failing test**

`tests/test_liquid/test_components.py`:
```python
"""Unit tests for liquid cooling hydraulic components."""
import unittest
import torch

class TestPressureDrivenPipe(unittest.TestCase):
    """Test Darcy-Weisbach pipe model."""

    def test_forward_positive_pressure_drop(self):
        """Positive dP should produce positive flow."""
        from dcdtuni.liquid.components.pipe import PressureDrivenPipe
        pipe = PressureDrivenPipe(
            diameter=0.025, length=2.0, roughness=1e-5,
            viscosity=0.000653, density=992.2,
        )
        P_in = torch.tensor(120.0, dtype=torch.float64)   # kPa
        P_out = torch.tensor(100.0, dtype=torch.float64)  # kPa
        Q = pipe(P_in, P_out)
        self.assertGreater(Q.item(), 0.0, "Flow should be positive for positive dP")

    def test_forward_negative_pressure_drop(self):
        """Negative dP should produce negative flow (reverse)."""
        from dcdtuni.liquid.components.pipe import PressureDrivenPipe
        pipe = PressureDrivenPipe(
            diameter=0.025, length=2.0, roughness=1e-5,
            viscosity=0.000653, density=992.2,
        )
        P_in = torch.tensor(100.0, dtype=torch.float64)
        P_out = torch.tensor(120.0, dtype=torch.float64)
        Q = pipe(P_in, P_out)
        self.assertLess(Q.item(), 0.0, "Flow should be negative for negative dP")

    def test_forward_zero_pressure_drop(self):
        """Zero dP should produce near-zero flow."""
        from dcdtuni.liquid.components.pipe import PressureDrivenPipe
        pipe = PressureDrivenPipe(
            diameter=0.025, length=2.0, roughness=1e-5,
            viscosity=0.000653, density=992.2,
        )
        P_in = torch.tensor(100.0, dtype=torch.float64)
        P_out = torch.tensor(100.0, dtype=torch.float64)
        Q = pipe(P_in, P_out)
        self.assertAlmostEqual(Q.item(), 0.0, places=3)

    def test_elevation_effect(self):
        """Upward elevation should reduce effective dP."""
        from dcdtuni.liquid.components.pipe import PressureDrivenPipe
        pipe_flat = PressureDrivenPipe(
            diameter=0.025, length=2.0, roughness=1e-5,
            viscosity=0.000653, density=992.2, z_in=0.0, z_out=0.0,
        )
        pipe_up = PressureDrivenPipe(
            diameter=0.025, length=2.0, roughness=1e-5,
            viscosity=0.000653, density=992.2, z_in=0.0, z_out=2.0,
        )
        P_in = torch.tensor(120.0, dtype=torch.float64)
        P_out = torch.tensor(100.0, dtype=torch.float64)
        Q_flat = pipe_flat(P_in, P_out).item()
        Q_up = pipe_up(P_in, P_out).item()
        self.assertGreater(Q_flat, Q_up, "Upward pipe should have less flow")

    def test_differentiable(self):
        """Gradient should flow through forward pass."""
        from dcdtuni.liquid.components.pipe import PressureDrivenPipe
        pipe = PressureDrivenPipe(
            diameter=0.025, length=2.0, roughness=1e-5,
            viscosity=0.000653, density=992.2,
        )
        P_in = torch.tensor(120.0, dtype=torch.float64, requires_grad=True)
        P_out = torch.tensor(100.0, dtype=torch.float64, requires_grad=True)
        Q = pipe(P_in, P_out)
        Q.backward()
        self.assertIsNotNone(P_in.grad)
        self.assertIsNotNone(P_out.grad)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_components.py::TestPressureDrivenPipe -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'dcdtuni.liquid.components.pipe'`

- [ ] **Step 3: Implement PressureDrivenPipe**

`src/backend/liquidcooling/components/pipe.py`:
```python
"""Darcy-Weisbach pipe flow model with gravitational elevation."""
import torch
from torch import nn


class PressureDrivenPipe(nn.Module):
    """Pressure-driven pipe flow using Darcy-Weisbach equation.

    Args:
        diameter: Pipe inner diameter [m].
        length: Pipe length [m].
        roughness: Surface roughness [m] (unused in Blasius, reserved for Colebrook).
        viscosity: Dynamic viscosity [Pa·s].
        density: Fluid density [kg/m³].
        z_in: Inlet elevation [m].
        z_out: Outlet elevation [m].
    """

    def __init__(
        self,
        diameter: float,
        length: float,
        roughness: float,
        viscosity: float,
        density: float,
        z_in: float = 0.0,
        z_out: float = 0.0,
    ) -> None:
        super().__init__()
        self.d = diameter
        self.L = length
        self.e = roughness
        self.mu = viscosity
        self.rho = density
        self.z_in = z_in
        self.z_out = z_out
        self.gravity = 9.81

    @staticmethod
    def friction_factor(Re: torch.Tensor) -> torch.Tensor:
        """Compute Darcy friction factor (laminar/turbulent regime).

        Laminar (Re < 2000): f = 64 / Re
        Turbulent (Re >= 2000): f = 0.3164 * Re^(-0.25)  (Blasius)
        """
        f_lam = 64.0 / Re
        f_turb = 0.3164 * Re ** (-0.25)
        return torch.where(Re < 2000, f_lam, f_turb)

    def forward(self, pressure_in: torch.Tensor, pressure_out: torch.Tensor) -> torch.Tensor:
        """Compute volumetric flow rate given inlet/outlet pressures.

        Args:
            pressure_in: Inlet pressure [kPa].
            pressure_out: Outlet pressure [kPa].

        Returns:
            Volumetric flow rate [LPM]. Positive = flow from in to out.
        """
        delta_pressure = pressure_in - pressure_out  # kPa
        delta_z = self.z_in - self.z_out  # m
        delta_pressure_eff = (delta_pressure * 1000.0) + self.rho * self.gravity * delta_z  # Pa

        sign = torch.sign(delta_pressure_eff)
        delta_pressure_abs = torch.abs(delta_pressure_eff).clamp(min=1e-6)

        area_cross = 3.14159265 * (self.d / 2.0) ** 2
        L_over_D = self.L / self.d

        # Iterative friction factor: one update from initial guess
        f_guess = 0.04
        v = torch.sqrt((2.0 * delta_pressure_abs) / (self.rho * f_guess * L_over_D))
        Re = self.rho * v * self.d / self.mu
        f = self.friction_factor(Re)
        v = torch.sqrt((2.0 * delta_pressure_abs) / (self.rho * f * L_over_D))

        volume_flowrate = sign * area_cross * v
        return volume_flowrate * 60000.0  # m³/s → LPM
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_components.py::TestPressureDrivenPipe -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/components/pipe.py tests/test_liquid/test_components.py
git commit -m "feat(liquid): add PressureDrivenPipe component with Darcy-Weisbach model"
```

---

## Task 3: Pump Component

**Files:**
- Create: `src/backend/liquidcooling/components/pump.py`
- Modify: `tests/test_liquid/test_components.py`

**Physics:** Affinity laws — `H(Q,N) = (N/N₀)²(H₀ - aQ²)`, inverse solve for Q given ΔP.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_liquid/test_components.py`:
```python
class TestPumpWithSpeed(unittest.TestCase):
    """Test affinity-law pump model."""

    def test_forward_produces_flow(self):
        """Pump should produce flow against back-pressure."""
        from dcdtuni.liquid.components.pump import PumpWithSpeed
        pump = PumpWithSpeed(head_ref=46.77, quad_coef=63454.0, rpm_ref=3000, rho=992.2)
        P_in = torch.tensor(0.0, dtype=torch.float64)    # kPa suction
        P_out = torch.tensor(100.0, dtype=torch.float64)  # kPa discharge
        Q = pump(P_in, P_out)
        self.assertGreater(Q.item(), 0.0, "Pump should produce positive flow")

    def test_speed_scaling(self):
        """Reducing speed should reduce flow."""
        from dcdtuni.liquid.components.pump import PumpWithSpeed
        pump_fast = PumpWithSpeed(head_ref=46.77, quad_coef=63454.0, rpm_ref=3000, rho=992.2)
        pump_slow = PumpWithSpeed(head_ref=46.77, quad_coef=63454.0, rpm_ref=3000, rho=992.2)
        pump_slow.adjust_speed(1500)
        P_in = torch.tensor(0.0, dtype=torch.float64)
        P_out = torch.tensor(50.0, dtype=torch.float64)
        Q_fast = pump_fast(P_in, P_out).item()
        Q_slow = pump_slow(P_in, P_out).item()
        self.assertGreater(Q_fast, Q_slow, "Slower pump should produce less flow")

    def test_zero_back_pressure_max_flow(self):
        """Zero back-pressure should give maximum flow."""
        from dcdtuni.liquid.components.pump import PumpWithSpeed
        pump = PumpWithSpeed(head_ref=46.77, quad_coef=63454.0, rpm_ref=3000, rho=992.2)
        P_in = torch.tensor(0.0, dtype=torch.float64)
        P_out = torch.tensor(0.0, dtype=torch.float64)
        Q = pump(P_in, P_out)
        self.assertGreater(Q.item(), 0.0)

    def test_differentiable(self):
        """Gradient should flow through forward pass."""
        from dcdtuni.liquid.components.pump import PumpWithSpeed
        pump = PumpWithSpeed(head_ref=46.77, quad_coef=63454.0, rpm_ref=3000, rho=992.2)
        P_in = torch.tensor(0.0, dtype=torch.float64, requires_grad=True)
        P_out = torch.tensor(50.0, dtype=torch.float64, requires_grad=True)
        Q = pump(P_in, P_out)
        Q.backward()
        self.assertIsNotNone(P_in.grad)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_components.py::TestPumpWithSpeed -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement PumpWithSpeed**

`src/backend/liquidcooling/components/pump.py`:
```python
"""Speed-dependent pump model using affinity laws."""
import torch
from torch import nn


class PumpWithSpeed(nn.Module):
    """Pump with variable speed using affinity laws.

    Base curve at reference speed: H(Q) = H₀ - a·Q²
    Speed scaling: H(Q,N) = (N/N₀)² · (H₀ - a·Q²)

    Args:
        head_ref: Maximum head at reference speed [m].
        quad_coef: Quadratic coefficient a [s²/m⁵].
        rpm_ref: Reference speed [RPM].
        rho: Fluid density [kg/m³].
    """

    def __init__(
        self,
        head_ref: float,
        quad_coef: float,
        rpm_ref: float,
        rho: float = 997.0,
    ) -> None:
        super().__init__()
        self.head_ref = head_ref
        self.quad_coef = quad_coef
        self.rpm_ref = rpm_ref
        self.rpm_actual = rpm_ref
        self.rho = rho
        self.gravity = 9.81
        self._update_weights()

    def _update_weights(self) -> None:
        """Recompute affinity-law weights from current speed."""
        ratio = self.rpm_actual / self.rpm_ref
        self.weight_head = ratio ** 2
        self.weight_flowrate = ratio

    def adjust_speed(self, rpm_actual: float) -> None:
        """Set actual pump speed [RPM]."""
        self.rpm_actual = rpm_actual
        self._update_weights()

    def forward(self, pressure_in: torch.Tensor, pressure_out: torch.Tensor) -> torch.Tensor:
        """Compute volumetric flow rate given suction/discharge pressures.

        Args:
            pressure_in: Suction pressure [kPa].
            pressure_out: Discharge pressure [kPa].

        Returns:
            Volumetric flow rate [LPM].
        """
        delta_pressure = (pressure_out - pressure_in) * 1000.0  # Pa (pump lifts from in to out)
        head_rev = delta_pressure / (self.rho * self.gravity * self.weight_head)
        volume_rev_squared = (self.head_ref - head_rev) / self.quad_coef
        volume_rev = torch.sqrt(torch.clamp(volume_rev_squared, min=1e-12))
        volume_flowrate = volume_rev * self.weight_flowrate
        return volume_flowrate * 60000.0  # m³/s → LPM
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_components.py::TestPumpWithSpeed -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/components/pump.py tests/test_liquid/test_components.py
git commit -m "feat(liquid): add PumpWithSpeed component with affinity-law model"
```

---

## Task 4: Quadratic dP-Q Components (ColdPlate, HeatExchanger, QuickConnector)

These three share the same inverse-quadratic math: `ΔP = aQ² + bQ + c`. Implement all three in one task.

**Files:**
- Create: `src/backend/liquidcooling/components/cold_plate.py`
- Create: `src/backend/liquidcooling/components/heat_exchanger.py`
- Create: `src/backend/liquidcooling/components/quick_connect.py`
- Modify: `tests/test_liquid/test_components.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_liquid/test_components.py`:
```python
class TestColdPlate(unittest.TestCase):
    """Test quadratic dP-Q cold plate model."""

    def test_forward_positive_dp(self):
        from dcdtuni.liquid.components.cold_plate import ColdPlate
        cp = ColdPlate(coef_a=0.724, coef_b=0.991, coef_c=-0.155)
        P_in = torch.tensor(110.0, dtype=torch.float64)
        P_out = torch.tensor(100.0, dtype=torch.float64)
        Q = cp(P_in, P_out)
        self.assertGreater(Q.item(), 0.0)

    def test_forward_negative_dp_reverses_flow(self):
        from dcdtuni.liquid.components.cold_plate import ColdPlate
        cp = ColdPlate(coef_a=0.724, coef_b=0.991, coef_c=-0.155)
        P_in = torch.tensor(100.0, dtype=torch.float64)
        P_out = torch.tensor(110.0, dtype=torch.float64)
        Q = cp(P_in, P_out)
        self.assertLess(Q.item(), 0.0)

    def test_larger_dp_more_flow(self):
        from dcdtuni.liquid.components.cold_plate import ColdPlate
        cp = ColdPlate(coef_a=0.724, coef_b=0.991, coef_c=-0.155)
        Q_small = cp(torch.tensor(105.0, dtype=torch.float64), torch.tensor(100.0, dtype=torch.float64)).item()
        Q_large = cp(torch.tensor(120.0, dtype=torch.float64), torch.tensor(100.0, dtype=torch.float64)).item()
        self.assertGreater(Q_large, Q_small)

    def test_differentiable(self):
        from dcdtuni.liquid.components.cold_plate import ColdPlate
        cp = ColdPlate(coef_a=0.724, coef_b=0.991, coef_c=-0.155)
        P_in = torch.tensor(110.0, dtype=torch.float64, requires_grad=True)
        P_out = torch.tensor(100.0, dtype=torch.float64, requires_grad=True)
        Q = cp(P_in, P_out)
        Q.backward()
        self.assertIsNotNone(P_in.grad)


class TestHeatExchanger(unittest.TestCase):
    """Test quadratic dP-Q heat exchanger model."""

    def test_forward_positive_dp(self):
        from dcdtuni.liquid.components.heat_exchanger import HeatExchanger
        hx = HeatExchanger(coef_a=0.5, coef_b=0.3, coef_c=0.0)
        Q = hx(torch.tensor(110.0, dtype=torch.float64), torch.tensor(100.0, dtype=torch.float64))
        self.assertGreater(Q.item(), 0.0)

    def test_differentiable(self):
        from dcdtuni.liquid.components.heat_exchanger import HeatExchanger
        hx = HeatExchanger(coef_a=0.5, coef_b=0.3, coef_c=0.0)
        P_in = torch.tensor(110.0, dtype=torch.float64, requires_grad=True)
        P_out = torch.tensor(100.0, dtype=torch.float64, requires_grad=True)
        Q = hx(P_in, P_out)
        Q.backward()
        self.assertIsNotNone(P_in.grad)


class TestQuickConnector(unittest.TestCase):
    """Test quadratic dP-Q quick connector model."""

    def test_forward_positive_dp(self):
        from dcdtuni.liquid.components.quick_connect import QuickConnector
        qc = QuickConnector(coef_a=1.0, coef_b=0.5, coef_c=0.0)
        Q = qc(torch.tensor(110.0, dtype=torch.float64), torch.tensor(100.0, dtype=torch.float64))
        self.assertGreater(Q.item(), 0.0)

    def test_forward_reverse_flow(self):
        from dcdtuni.liquid.components.quick_connect import QuickConnector
        qc = QuickConnector(coef_a=1.0, coef_b=0.5, coef_c=0.0)
        Q = qc(torch.tensor(100.0, dtype=torch.float64), torch.tensor(110.0, dtype=torch.float64))
        self.assertLess(Q.item(), 0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_components.py -k "ColdPlate or HeatExchanger or QuickConnector" -v
```

Expected: FAIL

- [ ] **Step 3: Implement ColdPlate**

`src/backend/liquidcooling/components/cold_plate.py`:
```python
"""Cold plate model with quadratic pressure-drop curve."""
import torch
from torch import nn


class ColdPlate(nn.Module):
    """Cold plate with quadratic dP-Q characteristic.

    Pressure drop: ΔP = a·Q² + b·Q + c
    Inverse solve: given ΔP, find Q via quadratic formula.

    Args:
        coef_a: Quadratic coefficient.
        coef_b: Linear coefficient.
        coef_c: Constant offset.
    """

    def __init__(self, coef_a: float = 1.0, coef_b: float = 1.0, coef_c: float = 0.0) -> None:
        super().__init__()
        self.a = coef_a
        self.b = coef_b
        self.c = coef_c

    def _inverse_quadratic(self, y: torch.Tensor) -> torch.Tensor:
        """Solve a·x² + b·x + (c - y) = 0 for positive x."""
        disc = self.b ** 2 - 4.0 * self.a * (self.c - y)
        disc = torch.clamp(disc, min=1e-12)
        sqrt_disc = torch.sqrt(disc)
        x1 = (-self.b + sqrt_disc) / (2.0 * self.a)
        x2 = (-self.b - sqrt_disc) / (2.0 * self.a)
        x1_valid = x1 > 0
        x2_valid = x2 > 0
        x = torch.where(
            x1_valid & ~x2_valid, x1,
            torch.where(
                x2_valid & ~x1_valid, x2,
                torch.where(
                    x1_valid & x2_valid, torch.min(x1, x2),
                    torch.full_like(x1, 0.0),
                ),
            ),
        )
        return x

    def forward(self, pressure_in: torch.Tensor, pressure_out: torch.Tensor) -> torch.Tensor:
        """Compute flow rate from pressure difference.

        Args:
            pressure_in: Inlet pressure [kPa].
            pressure_out: Outlet pressure [kPa].

        Returns:
            Volumetric flow rate. Sign matches pressure difference direction.
        """
        delta_pressure = pressure_in - pressure_out
        return self._inverse_quadratic(torch.abs(delta_pressure)) * torch.sign(delta_pressure)
```

- [ ] **Step 4: Implement HeatExchanger**

`src/backend/liquidcooling/components/heat_exchanger.py`:
```python
"""Heat exchanger model with quadratic pressure-drop curve."""
import torch
from torch import nn


class HeatExchanger(nn.Module):
    """Heat exchanger with quadratic dP-Q characteristic.

    Pressure drop: ΔP = a·Q² + b·Q + c
    Inverse solve: given ΔP, find Q via quadratic formula.

    Args:
        coef_a: Quadratic coefficient.
        coef_b: Linear coefficient.
        coef_c: Constant offset.
    """

    def __init__(self, coef_a: float = 1.0, coef_b: float = 1.0, coef_c: float = 0.0) -> None:
        super().__init__()
        self.a = coef_a
        self.b = coef_b
        self.c = coef_c

    def _inverse_quadratic(self, y: torch.Tensor) -> torch.Tensor:
        """Solve a·x² + b·x + (c - y) = 0 for positive x."""
        disc = self.b ** 2 - 4.0 * self.a * (self.c - y)
        disc = torch.clamp(disc, min=1e-12)
        sqrt_disc = torch.sqrt(disc)
        x1 = (-self.b + sqrt_disc) / (2.0 * self.a)
        x2 = (-self.b - sqrt_disc) / (2.0 * self.a)
        x1_valid = x1 > 0
        x2_valid = x2 > 0
        x = torch.where(
            x1_valid & ~x2_valid, x1,
            torch.where(
                x2_valid & ~x1_valid, x2,
                torch.where(
                    x1_valid & x2_valid, torch.min(x1, x2),
                    torch.full_like(x1, 0.0),
                ),
            ),
        )
        return x

    def forward(self, pressure_in: torch.Tensor, pressure_out: torch.Tensor) -> torch.Tensor:
        """Compute flow rate from pressure difference.

        Args:
            pressure_in: Inlet pressure [kPa].
            pressure_out: Outlet pressure [kPa].

        Returns:
            Volumetric flow rate. Sign matches pressure difference direction.
        """
        delta_pressure = pressure_in - pressure_out
        return self._inverse_quadratic(torch.abs(delta_pressure)) * torch.sign(delta_pressure)
```

- [ ] **Step 5: Implement QuickConnector**

`src/backend/liquidcooling/components/quick_connect.py`:
```python
"""Quick connector model with quadratic pressure-loss curve."""
import torch
from torch import nn


class QuickConnector(nn.Module):
    """Quick connector with quadratic dP-Q characteristic.

    Pressure drop: ΔP = a·Q² + b·Q + c

    Args:
        coef_a: Quadratic coefficient.
        coef_b: Linear coefficient.
        coef_c: Constant offset.
    """

    def __init__(self, coef_a: float = 1.0, coef_b: float = 1.0, coef_c: float = 0.0) -> None:
        super().__init__()
        self.a = coef_a
        self.b = coef_b
        self.c = coef_c

    def _inverse_quadratic(self, y: torch.Tensor) -> torch.Tensor:
        """Solve a·x² + b·x + (c - y) = 0 for positive x."""
        disc = self.b ** 2 - 4.0 * self.a * (self.c - y)
        disc = torch.clamp(disc, min=1e-12)
        sqrt_disc = torch.sqrt(disc)
        x1 = (-self.b + sqrt_disc) / (2.0 * self.a)
        x2 = (-self.b - sqrt_disc) / (2.0 * self.a)
        x1_valid = x1 > 0
        x2_valid = x2 > 0
        x = torch.where(
            x1_valid & ~x2_valid, x1,
            torch.where(
                x2_valid & ~x1_valid, x2,
                torch.where(
                    x1_valid & x2_valid, torch.min(x1, x2),
                    torch.full_like(x1, 0.0),
                ),
            ),
        )
        return x

    def forward(self, pressure_in: torch.Tensor, pressure_out: torch.Tensor) -> torch.Tensor:
        """Compute flow rate from pressure difference.

        Args:
            pressure_in: Inlet pressure [kPa].
            pressure_out: Outlet pressure [kPa].

        Returns:
            Volumetric flow rate. Sign matches pressure difference direction.
        """
        delta_pressure = pressure_in - pressure_out
        return self._inverse_quadratic(torch.abs(delta_pressure)) * torch.sign(delta_pressure)
```

- [ ] **Step 6: Run all tests**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_components.py -v
```

Expected: All 15 tests pass (5 pipe + 4 pump + 4 cold plate + 2 HX)

- [ ] **Step 7: Commit**

```bash
git add src/backend/liquidcooling/components/cold_plate.py src/backend/liquidcooling/components/heat_exchanger.py src/backend/liquidcooling/components/quick_connect.py tests/test_liquid/test_components.py
git commit -m "feat(liquid): add ColdPlate, HeatExchanger, QuickConnector components"
```

---

## Task 5: Control Valve Component

**Files:**
- Create: `src/backend/liquidcooling/components/valve.py`
- Modify: `tests/test_liquid/test_components.py`

**Physics:** `Q = Kv(u) × √(ΔP/SG)` with three valve characteristics.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_liquid/test_components.py`:
```python
class TestControlValveKv(unittest.TestCase):
    """Test Kv-based control valve model."""

    def test_linear_full_open(self):
        from dcdtuni.liquid.components.valve import ControlValveKv
        valve = ControlValveKv(Kv_max=947.0, characteristic="linear", opening=1.0)
        Q = valve(torch.tensor(110.0, dtype=torch.float64), torch.tensor(100.0, dtype=torch.float64))
        self.assertGreater(Q.item(), 0.0)

    def test_half_open_less_flow(self):
        from dcdtuni.liquid.components.valve import ControlValveKv
        v_full = ControlValveKv(Kv_max=947.0, characteristic="linear", opening=1.0)
        v_half = ControlValveKv(Kv_max=947.0, characteristic="linear", opening=0.5)
        P_in = torch.tensor(110.0, dtype=torch.float64)
        P_out = torch.tensor(100.0, dtype=torch.float64)
        Q_full = v_full(P_in, P_out).item()
        Q_half = v_half(P_in, P_out).item()
        self.assertGreater(Q_full, Q_half)

    def test_equal_percentage_characteristic(self):
        from dcdtuni.liquid.components.valve import ControlValveKv
        valve = ControlValveKv(Kv_max=947.0, characteristic="eqperc", R=50.0, opening=0.5)
        Q = valve(torch.tensor(110.0, dtype=torch.float64), torch.tensor(100.0, dtype=torch.float64))
        self.assertGreater(Q.item(), 0.0)

    def test_quick_opening_characteristic(self):
        from dcdtuni.liquid.components.valve import ControlValveKv
        valve = ControlValveKv(Kv_max=947.0, characteristic="quick", opening=0.5)
        Q = valve(torch.tensor(110.0, dtype=torch.float64), torch.tensor(100.0, dtype=torch.float64))
        self.assertGreater(Q.item(), 0.0)

    def test_set_opening(self):
        from dcdtuni.liquid.components.valve import ControlValveKv
        valve = ControlValveKv(Kv_max=947.0, characteristic="linear", opening=1.0)
        P_in = torch.tensor(110.0, dtype=torch.float64)
        P_out = torch.tensor(100.0, dtype=torch.float64)
        Q_before = valve(P_in, P_out).item()
        valve.set_opening(0.3)
        Q_after = valve(P_in, P_out).item()
        self.assertGreater(Q_before, Q_after)

    def test_reverse_flow(self):
        from dcdtuni.liquid.components.valve import ControlValveKv
        valve = ControlValveKv(Kv_max=947.0, characteristic="linear", opening=1.0)
        Q = valve(torch.tensor(100.0, dtype=torch.float64), torch.tensor(110.0, dtype=torch.float64))
        self.assertLess(Q.item(), 0.0)
```

- [ ] **Step 2: Run to verify failure**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_components.py::TestControlValveKv -v
```

- [ ] **Step 3: Implement ControlValveKv**

`src/backend/liquidcooling/components/valve.py`:
```python
"""Control valve model with Kv flow coefficient and multiple characteristics."""
import math
import torch
from torch import nn


class ControlValveKv(nn.Module):
    """Control valve with opening-dependent Kv characteristic.

    Flow equation: Q = Kv(u) × √(ΔP_bar / SG)  [m³/h]
    Three characteristics: linear, equal-percentage, quick-opening.

    Args:
        Kv_max: Maximum flow coefficient [m³/h at 1 bar, SG=1].
        characteristic: "linear", "eqperc", or "quick".
        R: Rangeability for equal-percentage (default 50).
        opening: Initial valve opening [0, 1].
        SG: Specific gravity (default 1.0 for water).
    """

    def __init__(
        self,
        Kv_max: float,
        characteristic: str = "linear",
        R: float = 50.0,
        opening: float = 1.0,
        SG: float = 1.0,
    ) -> None:
        super().__init__()
        self.Kv_max = float(Kv_max)
        self.characteristic = characteristic
        self.R = float(R)
        self.SG = float(SG)
        self.eps = 1e-8
        self.register_buffer("u", torch.tensor(float(opening), dtype=torch.float64))

    @torch.no_grad()
    def set_opening(self, u: float) -> None:
        """Set valve opening position [0, 1]."""
        self.u.fill_(max(0.0, min(1.0, float(u))))

    def _Kv_of_u(self, u_tensor: torch.Tensor) -> torch.Tensor:
        """Compute effective Kv at given opening."""
        u = torch.clamp(u_tensor, self.eps, 1.0)
        if self.characteristic == "linear":
            g = u
        elif self.characteristic == "eqperc":
            g = (self.R ** u - 1.0) / (self.R - 1.0)
        elif self.characteristic == "quick":
            g = torch.sin(0.5 * math.pi * u)
        else:
            raise ValueError(f"Unknown valve characteristic: {self.characteristic}")
        Kv = self.Kv_max * g
        return torch.clamp(Kv, min=self.eps)

    def forward(self, pressure_in: torch.Tensor, pressure_out: torch.Tensor) -> torch.Tensor:
        """Compute flow rate given inlet/outlet pressures.

        Args:
            pressure_in: Inlet pressure [kPa].
            pressure_out: Outlet pressure [kPa].

        Returns:
            Volumetric flow rate [LPM]. Positive = flow from in to out.
        """
        dp = pressure_in - pressure_out  # kPa
        sign = torch.sign(dp)
        dp_bar = torch.abs(dp) / 100.0  # kPa → bar
        Kv = self._Kv_of_u(self.u)
        sqrt_arg = torch.clamp(dp_bar / self.SG, min=0.0)
        Q_h = Kv * torch.sqrt(sqrt_arg)  # m³/h
        Q_lpm = sign * (Q_h * 1000.0 / 60.0)  # → LPM
        return Q_lpm
```

- [ ] **Step 4: Run tests**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_components.py::TestControlValveKv -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/components/valve.py tests/test_liquid/test_components.py
git commit -m "feat(liquid): add ControlValveKv component with 3 characteristics"
```

---

## Task 6: Components __init__ Exports

**Files:**
- Modify: `src/backend/liquidcooling/components/__init__.py`

- [ ] **Step 1: Update exports**

`src/backend/liquidcooling/components/__init__.py`:
```python
"""Hydraulic component models (PyTorch nn.Module).

All components share the interface:
    forward(pressure_in: Tensor, pressure_out: Tensor) -> Tensor
    Input: pressures in kPa. Output: volumetric flow rate in LPM.
"""
from dcdtuni.liquid.components.pipe import PressureDrivenPipe
from dcdtuni.liquid.components.pump import PumpWithSpeed
from dcdtuni.liquid.components.cold_plate import ColdPlate
from dcdtuni.liquid.components.valve import ControlValveKv
from dcdtuni.liquid.components.heat_exchanger import HeatExchanger
from dcdtuni.liquid.components.quick_connect import QuickConnector

__all__ = [
    "PressureDrivenPipe",
    "PumpWithSpeed",
    "ColdPlate",
    "ControlValveKv",
    "HeatExchanger",
    "QuickConnector",
]
```

- [ ] **Step 2: Verify imports work**

```bash
conda run -n dcdtuni python -c "from dcdtuni.liquid.components import PressureDrivenPipe, PumpWithSpeed, ColdPlate, ControlValveKv, HeatExchanger, QuickConnector; print('All components imported OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/components/__init__.py
git commit -m "feat(liquid): export all hydraulic components from components package"
```

---

## Task 7: Steady-State Solver

**Files:**
- Create: `src/backend/liquidcooling/solver/steady.py`
- Create: `tests/test_liquid/test_solver.py`

This is the core solver — Levenberg-Marquardt with sparse Jacobian. Reference: `Ref_code/DiffLiquidTwin/manager/hydraulic_fast.py`.

- [ ] **Step 1: Write failing test**

`tests/test_liquid/test_solver.py`:
```python
"""Tests for steady-state hydraulic network solver."""
import unittest
from dataclasses import dataclass

import networkx as nx
import torch
from torch import nn


@dataclass
class ComponentRecord:
    """Binds a component module to a graph edge."""
    name: str
    start: str
    end: str
    component: nn.Module


class TestSolveSteady(unittest.TestCase):
    """Test LM solver on a simple 3-node network."""

    def _build_simple_network(self):
        """Build: FixedP(200kPa) --pipe--> junction --pipe--> FixedP(100kPa)."""
        from dcdtuni.liquid.components.pipe import PressureDrivenPipe
        from dcdtuni.liquid.solver.steady import solve_steady

        G = nx.DiGraph()
        G.add_node("inlet", pressure_fixed=200.0)
        G.add_node("junction")
        G.add_node("outlet", pressure_fixed=100.0)
        G.add_edge("inlet", "junction")
        G.add_edge("junction", "outlet")

        pipe1 = PressureDrivenPipe(diameter=0.025, length=2.0, roughness=1e-5, viscosity=0.000653, density=992.2)
        pipe2 = PressureDrivenPipe(diameter=0.025, length=2.0, roughness=1e-5, viscosity=0.000653, density=992.2)

        components = [
            ComponentRecord("pipe1", "inlet", "junction", pipe1),
            ComponentRecord("pipe2", "junction", "outlet", pipe2),
        ]
        return G, components, solve_steady

    def test_converges(self):
        """Solver should converge for simple network."""
        G, components, solve_steady = self._build_simple_network()
        node_pressures, comp_flows = solve_steady(G, components)
        self.assertIn("junction", node_pressures)
        self.assertIn("pipe1", comp_flows)
        self.assertIn("pipe2", comp_flows)

    def test_mass_conservation(self):
        """Flow in should equal flow out at junction."""
        G, components, solve_steady = self._build_simple_network()
        node_pressures, comp_flows = solve_steady(G, components)
        Q1 = comp_flows["pipe1"]
        Q2 = comp_flows["pipe2"]
        self.assertAlmostEqual(Q1, Q2, places=4, msg="Mass must be conserved at junction")

    def test_junction_pressure_between_boundaries(self):
        """Junction pressure should be between inlet and outlet."""
        G, components, solve_steady = self._build_simple_network()
        node_pressures, comp_flows = solve_steady(G, components)
        P_junc = node_pressures["junction"]
        self.assertGreater(P_junc, 100.0)
        self.assertLess(P_junc, 200.0)

    def test_symmetric_pipes_equal_pressure(self):
        """Symmetric pipes should split pressure equally."""
        G, components, solve_steady = self._build_simple_network()
        node_pressures, comp_flows = solve_steady(G, components)
        P_junc = node_pressures["junction"]
        self.assertAlmostEqual(P_junc, 150.0, places=0, msg="Symmetric pipes → midpoint pressure")

    def test_parallel_branches(self):
        """Two parallel pipes should double the flow."""
        from dcdtuni.liquid.components.pipe import PressureDrivenPipe
        from dcdtuni.liquid.solver.steady import solve_steady

        G = nx.DiGraph()
        G.add_node("in", pressure_fixed=200.0)
        G.add_node("out", pressure_fixed=100.0)
        G.add_edge("in", "out", key="a")
        G.add_edge("in", "out", key="b")

        pipe_a = PressureDrivenPipe(diameter=0.025, length=2.0, roughness=1e-5, viscosity=0.000653, density=992.2)
        pipe_b = PressureDrivenPipe(diameter=0.025, length=2.0, roughness=1e-5, viscosity=0.000653, density=992.2)

        components = [
            ComponentRecord("pipe_a", "in", "out", pipe_a),
            ComponentRecord("pipe_b", "in", "out", pipe_b),
        ]
        node_pressures, comp_flows = solve_steady(G, components)
        self.assertAlmostEqual(comp_flows["pipe_a"], comp_flows["pipe_b"], places=4)

    def test_pump_in_network(self):
        """Network with pump should converge."""
        from dcdtuni.liquid.components.pipe import PressureDrivenPipe
        from dcdtuni.liquid.components.pump import PumpWithSpeed
        from dcdtuni.liquid.solver.steady import solve_steady

        G = nx.DiGraph()
        G.add_node("pump_in", pressure_fixed=0.0)
        G.add_node("pump_out")
        G.add_node("load_out", pressure_fixed=0.0)
        G.add_edge("pump_in", "pump_out")
        G.add_edge("pump_out", "load_out")

        pump = PumpWithSpeed(head_ref=46.77, quad_coef=63454.0, rpm_ref=3000, rho=992.2)
        pipe = PressureDrivenPipe(diameter=0.025, length=5.0, roughness=1e-5, viscosity=0.000653, density=992.2)

        components = [
            ComponentRecord("pump", "pump_in", "pump_out", pump),
            ComponentRecord("pipe", "pump_out", "load_out", pipe),
        ]
        node_pressures, comp_flows = solve_steady(G, components)
        self.assertGreater(node_pressures["pump_out"], 0.0, "Pump should raise pressure")
        self.assertGreater(comp_flows["pump"], 0.0, "There should be positive flow")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_solver.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement solve_steady**

`src/backend/liquidcooling/solver/steady.py`:
```python
"""Steady-state hydraulic network solver using Levenberg-Marquardt.

Solves for node pressures such that mass is conserved at every free node:
    r_k = sum(inflows) - sum(outflows) = 0  for each free node k

Uses sparse incidence matrix B and per-edge derivatives for efficient
Jacobian assembly: J = B^T @ diag(dq/dDP) @ B
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import networkx as nx
import torch
from loguru import logger
from torch import nn


@dataclass
class ComponentRecord:
    """Binds a hydraulic component to a directed graph edge."""
    name: str
    start: str
    end: str
    component: nn.Module


def _flow_and_grad(
    comp: nn.Module,
    P_in: torch.Tensor,
    P_out: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """Evaluate component flow and gradient dQ/d(DP).

    Uses local autograd to compute dQ/dP_in and dQ/dP_out,
    then derives dQ/d(DP) = dQ/dP_in - dQ/dP_out.
    """
    Pin = P_in.detach().clone().requires_grad_(True)
    Pout = P_out.detach().clone().requires_grad_(True)
    q = comp(Pin, Pout)
    dq_dPin = torch.autograd.grad(q, Pin, retain_graph=True, allow_unused=True)[0]
    dq_dPout = torch.autograd.grad(q, Pout, allow_unused=True)[0]
    if dq_dPin is None:
        dq_dPin = torch.zeros_like(Pin)
    if dq_dPout is None:
        dq_dPout = torch.zeros_like(Pout)
    dq_dDP = dq_dPin - dq_dPout
    return q.detach(), dq_dDP.detach()


def _build_incidence_matrix(
    components: List[ComponentRecord],
    var_nodes: List[str],
    fixed_nodes: List[str],
    dtype: torch.dtype,
) -> torch.Tensor:
    """Build sparse edge-node incidence matrix B.

    B[e, n] = +1 if n is start of edge e (among variable nodes)
    B[e, n] = -1 if n is end of edge e (among variable nodes)
    """
    var_idx = {n: i for i, n in enumerate(var_nodes)}
    rows, cols, vals = [], [], []
    for e_idx, rec in enumerate(components):
        if rec.start in var_idx:
            rows.append(e_idx)
            cols.append(var_idx[rec.start])
            vals.append(1.0)
        if rec.end in var_idx:
            rows.append(e_idx)
            cols.append(var_idx[rec.end])
            vals.append(-1.0)
    n_edges = len(components)
    n_vars = len(var_nodes)
    if not rows:
        return torch.sparse_coo_tensor(
            indices=torch.zeros(2, 0, dtype=torch.long),
            values=torch.zeros(0, dtype=dtype),
            size=(n_edges, n_vars),
        ).coalesce()
    indices = torch.tensor([rows, cols], dtype=torch.long)
    values = torch.tensor(vals, dtype=dtype)
    B = torch.sparse_coo_tensor(indices, values, size=(n_edges, n_vars)).coalesce()
    return B


def solve_steady(
    G: nx.DiGraph,
    components: List[ComponentRecord],
    tol: float = 1e-8,
    max_iter: int = 200,
    lam_init: float = 1e-6,
    lam_up: float = 10.0,
    lam_down: float = 0.3,
    verbose: bool = False,
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Solve steady-state hydraulic network.

    Args:
        G: Directed graph with node attributes. Nodes with 'pressure_fixed'
            attribute are boundary conditions.
        components: List of ComponentRecord binding nn.Module to graph edges.
        tol: Convergence tolerance on residual L2 norm.
        max_iter: Maximum LM iterations.
        lam_init: Initial LM damping parameter.
        lam_up: Damping increase factor on rejected step.
        lam_down: Damping decrease factor on accepted step.
        verbose: Print iteration info.

    Returns:
        (node_pressures, comp_flows): Dicts mapping node/component names to values.
    """
    dtype = torch.float64

    # --- Partition nodes into fixed and variable ---
    all_nodes = list(G.nodes)
    fixed_nodes: List[str] = []
    fixed_values: Dict[str, float] = {}

    for n, attr in G.nodes(data=True):
        pf = attr.get("pressure_fixed")
        if pf is not None:
            fixed_nodes.append(n)
            if isinstance(pf, (int, float)):
                fixed_values[n] = float(pf)
            else:
                fixed_values[n] = float(attr.get("P", 0.0))

    var_nodes = [n for n in all_nodes if n not in fixed_nodes]

    # If no fixed nodes, anchor first node to 0
    if not fixed_nodes and var_nodes:
        anchor = var_nodes.pop(0)
        fixed_nodes.append(anchor)
        fixed_values[anchor] = 0.0

    if not var_nodes:
        # All nodes fixed — just evaluate flows
        P_full = {n: torch.tensor(fixed_values[n], dtype=dtype) for n in fixed_nodes}
        comp_flows = {}
        for rec in components:
            q = rec.component(P_full[rec.start], P_full[rec.end]).detach().item()
            comp_flows[rec.name] = float(q)
        return dict(fixed_values), comp_flows

    var_idx = {n: i for i, n in enumerate(var_nodes)}
    n_vars = len(var_nodes)

    # --- Build sparse incidence matrix ---
    B = _build_incidence_matrix(components, var_nodes, fixed_nodes, dtype)

    # --- Initialize pressures ---
    P_var = 100.0 * torch.rand(n_vars, dtype=dtype)
    lam = lam_init

    def _assemble_P_full(P_v: torch.Tensor) -> Dict[str, torch.Tensor]:
        pf: Dict[str, torch.Tensor] = {}
        for n, v in fixed_values.items():
            pf[n] = torch.tensor(v, dtype=dtype)
        for n in var_nodes:
            pf[n] = P_v[var_idx[n]]
        return pf

    def _eval_all(P_full: Dict[str, torch.Tensor]):
        q_list = []
        dq_list = []
        for rec in components:
            q_e, dq_e = _flow_and_grad(rec.component, P_full[rec.start], P_full[rec.end])
            q_list.append(q_e)
            dq_list.append(dq_e)
        return torch.stack(q_list), torch.stack(dq_list)

    def _residual(P_v: torch.Tensor):
        P_full = _assemble_P_full(P_v)
        q, dq = _eval_all(P_full)
        r = torch.sparse.mm(B.transpose(0, 1), q.unsqueeze(1)).squeeze(1)
        return r, q, dq

    # --- LM iteration ---
    for it in range(max_iter):
        r, q, dq_dDP = _residual(P_var)
        norm = float(torch.linalg.norm(r).item())

        if verbose and it % 50 == 0:
            logger.info(f"Iter {it}, residual norm = {norm:.4e}, lambda = {lam:.2e}")

        if norm < tol:
            if verbose:
                logger.info(f"Converged at iter {it}, residual = {norm:.4e}")
            break

        # Jacobian: J = B^T @ diag(dq/dDP) @ B
        Bco = B.coalesce()
        Br, Bc = Bco.indices()
        Bv = Bco.values().clone()
        Bv_scaled = Bv * dq_dDP[Br]
        DB = torch.sparse_coo_tensor(
            indices=torch.stack([Br, Bc]),
            values=Bv_scaled,
            size=Bco.size(),
            dtype=dtype,
        ).coalesce()
        J = torch.sparse.mm(B.transpose(0, 1).coalesce(), DB).to_dense()

        # Solve (J + λI)δ = -r
        Jd = J + lam * torch.eye(n_vars, dtype=dtype)
        try:
            delta = torch.linalg.solve(Jd, -r)
        except RuntimeError:
            delta = torch.linalg.lstsq(Jd, (-r).unsqueeze(1)).solution.squeeze(1)

        # Step acceptance
        new_P = P_var + delta
        r_try, _, _ = _residual(new_P)
        norm_try = float(torch.linalg.norm(r_try).item())

        if norm_try < norm:
            P_var = new_P.detach()
            lam = max(lam * lam_down, 1e-12)
        else:
            lam = min(lam * lam_up, 1e12)
    else:
        logger.warning(f"Solver did not converge in {max_iter} iterations (residual = {norm:.4e})")

    # --- Extract solution ---
    node_pressures: Dict[str, float] = {}
    for n in fixed_nodes:
        node_pressures[n] = fixed_values[n]
    for n in var_nodes:
        node_pressures[n] = float(P_var[var_idx[n]].item())

    P_full_final = _assemble_P_full(P_var.detach())
    comp_flows: Dict[str, float] = {}
    for rec in components:
        q_e = rec.component(P_full_final[rec.start], P_full_final[rec.end]).detach().item()
        comp_flows[rec.name] = float(q_e)

    return node_pressures, comp_flows
```

- [ ] **Step 4: Run tests**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_solver.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/solver/steady.py tests/test_liquid/test_solver.py
git commit -m "feat(liquid): add Levenberg-Marquardt steady-state hydraulic solver"
```

---

## Task 8: Thermal Models

**Files:**
- Create: `src/backend/liquidcooling/thermal/chip_model.py`
- Create: `src/backend/liquidcooling/thermal/hybrid_cooling.py`
- Create: `tests/test_liquid/test_thermal.py`

- [ ] **Step 1: Write failing tests**

`tests/test_liquid/test_thermal.py`:
```python
"""Tests for thermal models (chip temperature, hybrid cooling eta)."""
import unittest


class TestChipMaxTemp(unittest.TestCase):

    def test_basic_prediction(self):
        """Chip temp should be above water inlet temp."""
        from dcdtuni.liquid.thermal.chip_model import compute_chip_max_temp
        T = compute_chip_max_temp(
            water_inlet_temp_C=35.0,
            chip_tdp_W=350.0,
            chip_area_m2=0.004,
            chip_thickness_m=0.001,
            thermal_conductivity_W_mK=148.0,
            water_flow_rate_LPM=2.5,
            fluid_density_kg_m3=992.2,
            fluid_cp_J_kgK=4180.0,
        )
        self.assertGreater(T, 35.0)
        self.assertLess(T, 150.0, "Chip temp should be physically reasonable")

    def test_more_flow_lower_temp(self):
        """Higher flow rate should reduce chip temperature."""
        from dcdtuni.liquid.thermal.chip_model import compute_chip_max_temp
        kwargs = dict(
            water_inlet_temp_C=35.0, chip_tdp_W=350.0, chip_area_m2=0.004,
            chip_thickness_m=0.001, thermal_conductivity_W_mK=148.0,
            fluid_density_kg_m3=992.2, fluid_cp_J_kgK=4180.0,
        )
        T_low_flow = compute_chip_max_temp(water_flow_rate_LPM=1.0, **kwargs)
        T_high_flow = compute_chip_max_temp(water_flow_rate_LPM=5.0, **kwargs)
        self.assertGreater(T_low_flow, T_high_flow)


class TestHeatCaptureRatio(unittest.TestCase):

    def test_eta_between_0_and_1(self):
        """Heat capture ratio should be between 0 and 1."""
        from dcdtuni.liquid.thermal.hybrid_cooling import compute_heat_capture_ratio
        eta = compute_heat_capture_ratio(
            water_flow_rate_LPM=2.5,
            air_flow_rate_m3s=0.05,
            chip_area_m2=0.004,
            pipe_diameter_m=0.008,
            fluid_density_kg_m3=992.2,
            fluid_cp_J_kgK=4180.0,
            fluid_viscosity_Pa_s=0.000653,
            fluid_conductivity_W_mK=0.632,
        )
        self.assertGreater(eta, 0.0)
        self.assertLess(eta, 1.0)

    def test_more_water_flow_higher_eta(self):
        """More water flow should capture more heat."""
        from dcdtuni.liquid.thermal.hybrid_cooling import compute_heat_capture_ratio
        kwargs = dict(
            air_flow_rate_m3s=0.05, chip_area_m2=0.004, pipe_diameter_m=0.008,
            fluid_density_kg_m3=992.2, fluid_cp_J_kgK=4180.0,
            fluid_viscosity_Pa_s=0.000653, fluid_conductivity_W_mK=0.632,
        )
        eta_low = compute_heat_capture_ratio(water_flow_rate_LPM=0.5, **kwargs)
        eta_high = compute_heat_capture_ratio(water_flow_rate_LPM=5.0, **kwargs)
        self.assertGreater(eta_high, eta_low)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_thermal.py -v
```

- [ ] **Step 3: Implement chip_model.py**

`src/backend/liquidcooling/thermal/chip_model.py`:
```python
"""Maximum chip temperature prediction for direct-to-chip liquid cooling.

Reference: dctwin D2CServerModel (ChipThermoModel).
T_chip = T_water_in + P/S × (1/h_w + L/k_s + S/(ṁ·Cp))
"""
import math


def compute_chip_max_temp(
    water_inlet_temp_C: float,
    chip_tdp_W: float,
    chip_area_m2: float,
    chip_thickness_m: float,
    thermal_conductivity_W_mK: float,
    water_flow_rate_LPM: float,
    fluid_density_kg_m3: float,
    fluid_cp_J_kgK: float,
    pipe_diameter_m: float = 0.008,
    fluid_viscosity_Pa_s: float = 0.000653,
    fluid_conductivity_W_mK: float = 0.632,
) -> float:
    """Compute maximum chip temperature.

    Args:
        water_inlet_temp_C: Cold plate inlet water temperature [degC].
        chip_tdp_W: Chip thermal design power [W].
        chip_area_m2: Chip heat transfer area [m2].
        chip_thickness_m: Chip (silicon) thickness [m].
        thermal_conductivity_W_mK: Chip thermal conductivity [W/(m*K)].
        water_flow_rate_LPM: Water flow through cold plate [LPM].
        fluid_density_kg_m3: Coolant density [kg/m3].
        fluid_cp_J_kgK: Coolant specific heat [J/(kg*K)].
        pipe_diameter_m: Cold plate channel hydraulic diameter [m].
        fluid_viscosity_Pa_s: Coolant dynamic viscosity [Pa*s].
        fluid_conductivity_W_mK: Coolant thermal conductivity [W/(m*K)].

    Returns:
        Maximum chip temperature [degC].
    """
    # Mass flow rate
    m_dot = fluid_density_kg_m3 * water_flow_rate_LPM / 60000.0  # kg/s

    # Water-side heat transfer coefficient (Dittus-Boelter)
    A_pipe = math.pi * (pipe_diameter_m / 2.0) ** 2
    v_water = (water_flow_rate_LPM / 60000.0) / max(A_pipe, 1e-12)  # m/s
    Re_w = fluid_density_kg_m3 * v_water * pipe_diameter_m / max(fluid_viscosity_Pa_s, 1e-12)
    Pr_w = fluid_viscosity_Pa_s * fluid_cp_J_kgK / max(fluid_conductivity_W_mK, 1e-12)
    Nu_w = 0.023 * max(Re_w, 1.0) ** 0.8 * max(Pr_w, 0.1) ** (1.0 / 3.0)
    h_w = Nu_w * fluid_conductivity_W_mK / pipe_diameter_m  # W/(m2*K)

    # Thermal resistance components
    R_convection = 1.0 / max(h_w, 1e-6)                                    # convection
    R_conduction = chip_thickness_m / max(thermal_conductivity_W_mK, 1e-6)  # chip conduction
    R_capacity = chip_area_m2 / max(m_dot * fluid_cp_J_kgK, 1e-6)          # coolant capacity

    # Chip temperature
    P_over_S = chip_tdp_W / max(chip_area_m2, 1e-12)
    T_chip = water_inlet_temp_C + P_over_S * (R_convection + R_conduction + R_capacity)
    return T_chip
```

- [ ] **Step 4: Implement hybrid_cooling.py**

`src/backend/liquidcooling/thermal/hybrid_cooling.py`:
```python
"""Hybrid cooling load distribution model.

Computes eta = fraction of heat captured by liquid cooling.
Reference: dctwin HybridCoolingLoadDistributionModel.

eta = N / (M + N)
  M = water-side thermal resistance
  N = air-side thermal resistance
"""
import math

# Air properties at ~25 degC
_RHO_AIR = 1.19       # kg/m3
_CP_AIR = 1006.0       # J/(kg*K)
_MU_AIR = 1.85e-5      # Pa*s
_K_AIR = 0.026         # W/(m*K)
_PR_AIR = _MU_AIR * _CP_AIR / _K_AIR


def compute_heat_capture_ratio(
    water_flow_rate_LPM: float,
    air_flow_rate_m3s: float,
    chip_area_m2: float,
    pipe_diameter_m: float = 0.008,
    fluid_density_kg_m3: float = 992.2,
    fluid_cp_J_kgK: float = 4180.0,
    fluid_viscosity_Pa_s: float = 0.000653,
    fluid_conductivity_W_mK: float = 0.632,
) -> float:
    """Compute liquid cooling heat capture ratio (eta).

    Args:
        water_flow_rate_LPM: Water flow rate [LPM].
        air_flow_rate_m3s: Server air flow rate [m3/s].
        chip_area_m2: Chip surface area [m2].
        pipe_diameter_m: Cold plate channel hydraulic diameter [m].
        fluid_density_kg_m3: Coolant density [kg/m3].
        fluid_cp_J_kgK: Coolant specific heat [J/(kg*K)].
        fluid_viscosity_Pa_s: Coolant dynamic viscosity [Pa*s].
        fluid_conductivity_W_mK: Coolant thermal conductivity [W/(m*K)].

    Returns:
        eta in (0, 1): fraction of chip heat captured by liquid cooling.
    """
    # Water-side: Dittus-Boelter (turbulent internal flow)
    A_pipe = math.pi * (pipe_diameter_m / 2.0) ** 2
    v_water = (water_flow_rate_LPM / 60000.0) / max(A_pipe, 1e-12)
    Re_w = fluid_density_kg_m3 * v_water * pipe_diameter_m / max(fluid_viscosity_Pa_s, 1e-12)
    Pr_w = fluid_viscosity_Pa_s * fluid_cp_J_kgK / max(fluid_conductivity_W_mK, 1e-12)
    Nu_w = 0.023 * max(Re_w, 1.0) ** 0.8 * max(Pr_w, 0.1) ** (1.0 / 3.0)
    h_w = Nu_w * fluid_conductivity_W_mK / pipe_diameter_m

    # Air-side: flat plate correlation
    L_chip = math.sqrt(chip_area_m2)  # characteristic length
    v_air = air_flow_rate_m3s / max(chip_area_m2, 1e-12)
    Re_a = _RHO_AIR * v_air * L_chip / _MU_AIR
    Nu_a = 0.664 * max(Re_a, 1.0) ** 0.6 * _PR_AIR ** (1.0 / 3.0)
    h_a = Nu_a * _K_AIR / L_chip

    # Thermal resistances
    M = 1.0 / max(h_w * chip_area_m2, 1e-12)  # water-side [K/W]
    N = 1.0 / max(h_a * chip_area_m2, 1e-12)  # air-side [K/W]

    # eta = N / (M + N) — higher air resistance means more heat goes to water
    eta = N / (M + N)
    return max(0.0, min(1.0, eta))
```

- [ ] **Step 5: Run tests**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_thermal.py -v
```

Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/backend/liquidcooling/thermal/chip_model.py src/backend/liquidcooling/thermal/hybrid_cooling.py tests/test_liquid/test_thermal.py
git commit -m "feat(liquid): add chip temperature and hybrid cooling thermal models"
```

---

## Task 9: Pydantic Schema for liquidCooling

**Files:**
- Create: `src/backend/liquidcooling/schema.py`
- Create: `tests/test_liquid/test_schema.py`

- [ ] **Step 1: Write failing test**

`tests/test_liquid/test_schema.py`:
```python
"""Tests for liquid cooling Pydantic schema validation."""
import unittest
import json


class TestLiquidCoolingSchema(unittest.TestCase):

    def test_minimal_valid(self):
        from dcdtuni.liquid.schema import LiquidCoolingConfig
        cfg = LiquidCoolingConfig(enabled=True)
        self.assertTrue(cfg.enabled)

    def test_full_config(self):
        from dcdtuni.liquid.schema import LiquidCoolingConfig
        data = {
            "enabled": True,
            "fluid": {"type": "water", "rho": 992.2, "mu": 0.000653, "cp": 4180, "k": 0.632},
            "cdus": {
                "CDU_1": {
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 0}, "size": {"W": 0.8, "D": 0.6, "H": 1.8}},
                    "supplyTemperature": 35.0,
                    "totalFlowRate": 60.0,
                    "servedRacks": ["Rack_1"],
                }
            },
            "specifications": {
                "coldPlateSpec": [{"name": "cp_default", "coefA": 0.724, "coefB": 0.991, "coefC": -0.155}],
                "pipeSpec": [{"name": "pipe_25mm", "diameter": 0.025, "roughness": 1e-5}],
            },
            "piping": {"autoGenerated": True, "mainPipeSpec": "pipe_25mm"},
        }
        cfg = LiquidCoolingConfig(**data)
        self.assertEqual(cfg.cdus["CDU_1"].supplyTemperature, 35.0)
        self.assertEqual(len(cfg.specifications.coldPlateSpec), 1)

    def test_server_liquid_cooling(self):
        from dcdtuni.liquid.schema import ServerLiquidCooling
        data = {
            "enabled": True,
            "coldPlates": [
                {"id": "cp_cpu", "label": "CPU", "specName": "cp_default", "chipTDP": 350,
                 "thermalModel": {"chipArea": 0.004, "chipThickness": 0.001, "thermalConductivity": 148},
                 "position": {"x": 0.25, "y": 0.5}},
            ],
            "routing": {"type": "series", "connections": []},
        }
        slc = ServerLiquidCooling(**data)
        self.assertEqual(len(slc.coldPlates), 1)
        self.assertEqual(slc.routing.type, "series")

    def test_backward_compatible_none(self):
        """Missing liquidCooling should be handled gracefully."""
        from dcdtuni.liquid.schema import LiquidCoolingConfig
        cfg = LiquidCoolingConfig(enabled=False)
        self.assertFalse(cfg.enabled)
        self.assertEqual(len(cfg.cdus), 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_schema.py -v
```

- [ ] **Step 3: Implement schema.py**

`src/backend/liquidcooling/schema.py`:
```python
"""Pydantic v2 schemas for liquid cooling configuration in cfd_export.json."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _LiquidBaseModel(BaseModel):
    """Base model with forward-compatible extra fields."""
    model_config = ConfigDict(extra="allow")


# --- Geometry helpers ---

class Point3D(_LiquidBaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class Size3D(_LiquidBaseModel):
    W: float = 0.0
    D: float = 0.0
    H: float = 0.0


class CDUGeometry(_LiquidBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Size3D = Field(default_factory=Size3D)


# --- Fluid ---

class FluidConfig(_LiquidBaseModel):
    type: str = "water"
    rho: float = 992.2
    mu: float = 0.000653
    cp: float = 4180.0
    k: float = 0.632


# --- Specifications ---

class PumpSpec(_LiquidBaseModel):
    name: str
    headRef: float
    quadCoef: float
    rpmRef: float = 3000.0


class PipeSpec(_LiquidBaseModel):
    name: str
    diameter: float
    roughness: float = 1e-5
    length: float = 2.0


class ValveSpec(_LiquidBaseModel):
    name: str
    kvMax: float
    characteristic: str = "eqperc"
    r: float = 50.0
    opening: float = 1.0


class ColdPlateSpec(_LiquidBaseModel):
    name: str
    coefA: float = 1.0
    coefB: float = 1.0
    coefC: float = 0.0


class HxSpec(_LiquidBaseModel):
    name: str
    coefA: float = 1.0
    coefB: float = 1.0
    coefC: float = 0.0


class Specifications(_LiquidBaseModel):
    pumpSpec: List[PumpSpec] = Field(default_factory=list)
    pipeSpec: List[PipeSpec] = Field(default_factory=list)
    valveSpec: List[ValveSpec] = Field(default_factory=list)
    coldPlateSpec: List[ColdPlateSpec] = Field(default_factory=list)
    hxSpec: List[HxSpec] = Field(default_factory=list)


# --- CDU ---

class CDUAdvancedPump(_LiquidBaseModel):
    specName: str
    rpm: float = 3000.0


class CDUAdvancedHx(_LiquidBaseModel):
    specName: str


class CDUAdvancedValve(_LiquidBaseModel):
    specName: str
    opening: float = 1.0


class CDUAdvanced(_LiquidBaseModel):
    pump: Optional[CDUAdvancedPump] = None
    heatExchanger: Optional[CDUAdvancedHx] = None
    valve: Optional[CDUAdvancedValve] = None


class CDUConfig(_LiquidBaseModel):
    geometry: CDUGeometry = Field(default_factory=CDUGeometry)
    supplyTemperature: float = 35.0
    totalFlowRate: float = 60.0
    advanced: Optional[CDUAdvanced] = None
    servedRacks: List[str] = Field(default_factory=list)


# --- Piping ---

class PipingConfig(_LiquidBaseModel):
    autoGenerated: bool = True
    mainPipeSpec: str = "pipe_25mm"
    branchPipeSpec: Optional[str] = None
    chains: List = Field(default_factory=list)


# --- Server-level liquid cooling ---

class Position2D(_LiquidBaseModel):
    x: float = 0.5
    y: float = 0.5


class ThermalModelConfig(_LiquidBaseModel):
    chipArea: float = 0.004
    chipThickness: float = 0.001
    thermalConductivity: float = 148.0


class ColdPlateConfig(_LiquidBaseModel):
    id: str
    label: str = ""
    specName: str = "cp_default"
    chipTDP: float = 350.0
    thermalModel: ThermalModelConfig = Field(default_factory=ThermalModelConfig)
    position: Position2D = Field(default_factory=Position2D)


class RoutingConnection(_LiquidBaseModel):
    fromPort: str = Field(alias="from")
    toPort: str = Field(alias="to")

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class RoutingConfig(_LiquidBaseModel):
    type: Literal["series", "parallel", "custom"] = "series"
    connections: List[RoutingConnection] = Field(default_factory=list)


class ServerLiquidCooling(_LiquidBaseModel):
    enabled: bool = False
    coldPlates: List[ColdPlateConfig] = Field(default_factory=list)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)


# --- Top-level ---

class LiquidCoolingConfig(_LiquidBaseModel):
    """Top-level liquidCooling section of cfd_export.json."""
    enabled: bool = False
    fluid: FluidConfig = Field(default_factory=FluidConfig)
    cdus: Dict[str, CDUConfig] = Field(default_factory=dict)
    specifications: Specifications = Field(default_factory=Specifications)
    piping: PipingConfig = Field(default_factory=PipingConfig)
```

- [ ] **Step 4: Run tests**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_schema.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/schema.py tests/test_liquid/test_schema.py
git commit -m "feat(liquid): add Pydantic v2 schemas for liquidCooling config"
```

---

## Task 10: Component Factory

**Files:**
- Create: `src/backend/liquidcooling/factory.py`

- [ ] **Step 1: Implement factory.py**

`src/backend/liquidcooling/factory.py`:
```python
"""Factory: create component nn.Module instances from specification dicts."""
from __future__ import annotations

from torch import nn

from dcdtuni.liquid.components import (
    ColdPlate,
    ControlValveKv,
    HeatExchanger,
    PressureDrivenPipe,
    PumpWithSpeed,
    QuickConnector,
)
from dcdtuni.liquid.schema import (
    ColdPlateSpec,
    HxSpec,
    PipeSpec,
    PumpSpec,
    Specifications,
    ValveSpec,
)


def _find_spec(specs: list, name: str):
    """Find specification by name in a list."""
    for s in specs:
        if s.name == name:
            return s
    raise KeyError(f"Specification '{name}' not found")


def create_pipe(spec: PipeSpec, viscosity: float, density: float, length: float | None = None) -> PressureDrivenPipe:
    """Create a pipe component from spec."""
    return PressureDrivenPipe(
        diameter=spec.diameter,
        length=length if length is not None else spec.length,
        roughness=spec.roughness,
        viscosity=viscosity,
        density=density,
    )


def create_pump(spec: PumpSpec, rho: float) -> PumpWithSpeed:
    """Create a pump component from spec."""
    return PumpWithSpeed(
        head_ref=spec.headRef,
        quad_coef=spec.quadCoef,
        rpm_ref=spec.rpmRef,
        rho=rho,
    )


def create_cold_plate(spec: ColdPlateSpec) -> ColdPlate:
    """Create a cold plate component from spec."""
    return ColdPlate(coef_a=spec.coefA, coef_b=spec.coefB, coef_c=spec.coefC)


def create_valve(spec: ValveSpec) -> ControlValveKv:
    """Create a control valve component from spec."""
    return ControlValveKv(
        Kv_max=spec.kvMax,
        characteristic=spec.characteristic,
        R=spec.r,
        opening=spec.opening,
    )


def create_heat_exchanger(spec: HxSpec) -> HeatExchanger:
    """Create a heat exchanger component from spec."""
    return HeatExchanger(coef_a=spec.coefA, coef_b=spec.coefB, coef_c=spec.coefC)


def create_component_by_name(
    comp_type: str,
    spec_name: str,
    specifications: Specifications,
    fluid_viscosity: float,
    fluid_density: float,
    pipe_length: float | None = None,
) -> nn.Module:
    """Create a component nn.Module by type name and spec name.

    Args:
        comp_type: One of "pipe", "pump", "valve", "cold_plate", "heat_exchanger", "quick_connect".
        spec_name: Name of the specification in the specifications library.
        specifications: The specifications library from liquidCooling config.
        fluid_viscosity: Fluid dynamic viscosity [Pa*s].
        fluid_density: Fluid density [kg/m3].
        pipe_length: Override pipe length [m] (used for auto-generated pipes).

    Returns:
        Instantiated nn.Module component.
    """
    if comp_type == "pipe":
        spec = _find_spec(specifications.pipeSpec, spec_name)
        return create_pipe(spec, fluid_viscosity, fluid_density, pipe_length)
    elif comp_type == "pump":
        spec = _find_spec(specifications.pumpSpec, spec_name)
        return create_pump(spec, fluid_density)
    elif comp_type == "valve":
        spec = _find_spec(specifications.valveSpec, spec_name)
        return create_valve(spec)
    elif comp_type == "cold_plate":
        spec = _find_spec(specifications.coldPlateSpec, spec_name)
        return create_cold_plate(spec)
    elif comp_type == "heat_exchanger":
        spec = _find_spec(specifications.hxSpec, spec_name)
        return create_heat_exchanger(spec)
    elif comp_type == "quick_connect":
        # Quick connectors use cold plate specs (same quadratic model)
        spec = _find_spec(specifications.coldPlateSpec, spec_name)
        return QuickConnector(coef_a=spec.coefA, coef_b=spec.coefB, coef_c=spec.coefC)
    else:
        raise ValueError(f"Unknown component type: {comp_type}")
```

- [ ] **Step 2: Quick smoke test**

```bash
conda run -n dcdtuni python -c "
from dcdtuni.liquid.schema import Specifications, PipeSpec, ColdPlateSpec
from dcdtuni.liquid.factory import create_component_by_name
specs = Specifications(
    pipeSpec=[PipeSpec(name='p1', diameter=0.025, roughness=1e-5)],
    coldPlateSpec=[ColdPlateSpec(name='cp1', coefA=0.7, coefB=1.0, coefC=0.0)],
)
pipe = create_component_by_name('pipe', 'p1', specs, 0.000653, 992.2)
cp = create_component_by_name('cold_plate', 'cp1', specs, 0.000653, 992.2)
print(f'Pipe: {pipe.__class__.__name__}, ColdPlate: {cp.__class__.__name__}')
"
```

Expected: `Pipe: PressureDrivenPipe, ColdPlate: ColdPlate`

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/factory.py
git commit -m "feat(liquid): add component factory for spec-to-module instantiation"
```

---

## Task 11: Topology Builder

**Files:**
- Create: `src/backend/liquidcooling/topology.py`
- Create: `tests/test_liquid/test_topology.py`

- [ ] **Step 1: Write failing test**

`tests/test_liquid/test_topology.py`:
```python
"""Tests for automatic topology generation from cfd_export.json."""
import unittest


class TestBuildNetwork(unittest.TestCase):

    def _make_config(self):
        """Minimal config: 1 CDU, 1 rack, 1 server with 1 cold plate (series)."""
        from dcdtuni.liquid.schema import (
            LiquidCoolingConfig, FluidConfig, CDUConfig, Specifications,
            PipeSpec, ColdPlateSpec, PipingConfig,
        )
        return LiquidCoolingConfig(
            enabled=True,
            fluid=FluidConfig(type="water", rho=992.2, mu=0.000653, cp=4180.0, k=0.632),
            cdus={"CDU_1": CDUConfig(supplyTemperature=35.0, totalFlowRate=60.0, servedRacks=["Rack_1"])},
            specifications=Specifications(
                pipeSpec=[PipeSpec(name="pipe_25mm", diameter=0.025, roughness=1e-5, length=2.0)],
                coldPlateSpec=[ColdPlateSpec(name="cp_default", coefA=0.724, coefB=0.991, coefC=-0.155)],
            ),
            piping=PipingConfig(autoGenerated=True, mainPipeSpec="pipe_25mm"),
        )

    def _make_racks_data(self):
        """Minimal rack with one liquid-cooled server."""
        return {
            "Rack_1": {
                "servers": [
                    {
                        "id": "Rack_1_s01",
                        "type": "fanServer",
                        "physics": {"volumeFlowRate": 0.05, "power": 700},
                        "liquidCooling": {
                            "enabled": True,
                            "coldPlates": [
                                {"id": "cp_cpu", "label": "CPU", "specName": "cp_default", "chipTDP": 350,
                                 "thermalModel": {"chipArea": 0.004, "chipThickness": 0.001, "thermalConductivity": 148},
                                 "position": {"x": 0.25, "y": 0.5}},
                            ],
                            "routing": {"type": "series", "connections": []},
                        },
                    },
                ],
            },
        }

    def test_builds_graph(self):
        from dcdtuni.liquid.topology import build_network
        cfg = self._make_config()
        racks = self._make_racks_data()
        G, components = build_network(cfg, racks)
        self.assertGreater(len(G.nodes), 0)
        self.assertGreater(len(components), 0)

    def test_has_fixed_pressure_nodes(self):
        from dcdtuni.liquid.topology import build_network
        cfg = self._make_config()
        racks = self._make_racks_data()
        G, components = build_network(cfg, racks)
        fixed = [n for n, a in G.nodes(data=True) if a.get("pressure_fixed") is not None]
        self.assertGreater(len(fixed), 0, "Should have at least one fixed-pressure node")

    def test_solvable(self):
        """Generated network should be solvable."""
        from dcdtuni.liquid.topology import build_network
        from dcdtuni.liquid.solver.steady import solve_steady
        cfg = self._make_config()
        racks = self._make_racks_data()
        G, components = build_network(cfg, racks)
        node_pressures, comp_flows = solve_steady(G, components)
        self.assertGreater(len(node_pressures), 0)
        # All flows should be finite
        for name, q in comp_flows.items():
            self.assertTrue(-1e6 < q < 1e6, f"Flow {name}={q} not finite")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_topology.py -v
```

- [ ] **Step 3: Implement topology.py**

`src/backend/liquidcooling/topology.py`:
```python
"""Build hydraulic network graph from liquidCooling config.

Converts cfd_export.json liquidCooling section + rack server data
into a NetworkX DiGraph + list of ComponentRecord for the solver.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import networkx as nx

from dcdtuni.liquid.factory import create_component_by_name
from dcdtuni.liquid.schema import (
    CDUConfig,
    LiquidCoolingConfig,
    ServerLiquidCooling,
)
from dcdtuni.liquid.solver.steady import ComponentRecord


def build_network(
    liquid_cfg: LiquidCoolingConfig,
    racks_data: Dict[str, Any],
) -> Tuple[nx.DiGraph, List[ComponentRecord]]:
    """Build hydraulic network from liquid cooling config.

    Args:
        liquid_cfg: Parsed LiquidCoolingConfig from cfd_export.json.
        racks_data: Dict of rack data from cfd_export.json (keyed by rack name).
            Each rack has "servers" list, each server may have "liquidCooling".

    Returns:
        (G, components): Directed graph and list of ComponentRecord.
    """
    G = nx.DiGraph()
    components: List[ComponentRecord] = []
    fluid = liquid_cfg.fluid
    specs = liquid_cfg.specifications

    for cdu_name, cdu_cfg in liquid_cfg.cdus.items():
        _add_cdu_subgraph(G, components, cdu_name, cdu_cfg, liquid_cfg, racks_data, fluid, specs)

    return G, components


def _add_cdu_subgraph(
    G: nx.DiGraph,
    components: List[ComponentRecord],
    cdu_name: str,
    cdu_cfg: CDUConfig,
    liquid_cfg: LiquidCoolingConfig,
    racks_data: Dict[str, Any],
    fluid,
    specs,
) -> None:
    """Add CDU + served racks to the network graph."""
    supply_node = f"{cdu_name}_supply"
    return_node = f"{cdu_name}_return"

    # Black-box CDU: fixed pressure at supply, fixed pressure at return (0 kPa gauge)
    # Supply pressure estimated from total flow rate to provide enough head
    G.add_node(supply_node, pressure_fixed=200.0)  # Approximate supply pressure
    G.add_node(return_node, pressure_fixed=0.0)     # Return at atmospheric

    pipe_spec_name = liquid_cfg.piping.mainPipeSpec

    for rack_name in cdu_cfg.servedRacks:
        rack_data = racks_data.get(rack_name)
        if rack_data is None:
            continue

        manifold_in = f"{rack_name}_manifold_in"
        manifold_out = f"{rack_name}_manifold_out"
        G.add_node(manifold_in)
        G.add_node(manifold_out)

        # Supply pipe: CDU → Rack manifold in
        supply_pipe = create_component_by_name(
            "pipe", pipe_spec_name, specs, fluid.mu, fluid.rho, pipe_length=5.0,
        )
        supply_pipe_name = f"{cdu_name}_{rack_name}_supply_pipe"
        G.add_edge(supply_node, manifold_in)
        components.append(ComponentRecord(supply_pipe_name, supply_node, manifold_in, supply_pipe))

        # Return pipe: Rack manifold out → CDU
        return_pipe = create_component_by_name(
            "pipe", pipe_spec_name, specs, fluid.mu, fluid.rho, pipe_length=5.0,
        )
        return_pipe_name = f"{cdu_name}_{rack_name}_return_pipe"
        G.add_edge(manifold_out, return_node)
        components.append(ComponentRecord(return_pipe_name, manifold_out, return_node, return_pipe))

        # Add server cold plates
        servers = rack_data.get("servers", [])
        for server in servers:
            lc = server.get("liquidCooling")
            if lc is None or not lc.get("enabled", False):
                continue

            server_id = server["id"]
            slc = ServerLiquidCooling(**lc)
            _add_server_coldplates(
                G, components, server_id, slc, manifold_in, manifold_out, specs, fluid,
            )


def _add_server_coldplates(
    G: nx.DiGraph,
    components: List[ComponentRecord],
    server_id: str,
    slc: ServerLiquidCooling,
    manifold_in: str,
    manifold_out: str,
    specs,
    fluid,
) -> None:
    """Add cold plate edges for a single server."""
    cold_plates = slc.coldPlates
    if not cold_plates:
        return

    routing_type = slc.routing.type

    if routing_type == "series":
        # Chain: manifold_in → cp[0] → cp[1] → ... → manifold_out
        prev_node = manifold_in
        for i, cp_cfg in enumerate(cold_plates):
            cp_in = f"{server_id}_{cp_cfg.id}_in"
            cp_out = f"{server_id}_{cp_cfg.id}_out"
            G.add_node(cp_in)
            G.add_node(cp_out)

            # Pipe from previous node to cold plate inlet
            pipe = create_component_by_name("pipe", "pipe_25mm", specs, fluid.mu, fluid.rho, pipe_length=0.5)
            pipe_name = f"{server_id}_{cp_cfg.id}_inlet_pipe"
            G.add_edge(prev_node, cp_in)
            components.append(ComponentRecord(pipe_name, prev_node, cp_in, pipe))

            # Cold plate itself
            cp_comp = create_component_by_name("cold_plate", cp_cfg.specName, specs, fluid.mu, fluid.rho)
            cp_name = f"{server_id}_{cp_cfg.id}"
            G.add_edge(cp_in, cp_out)
            components.append(ComponentRecord(cp_name, cp_in, cp_out, cp_comp))

            prev_node = cp_out

        # Final pipe to manifold out
        pipe = create_component_by_name("pipe", "pipe_25mm", specs, fluid.mu, fluid.rho, pipe_length=0.5)
        pipe_name = f"{server_id}_outlet_pipe"
        G.add_edge(prev_node, manifold_out)
        components.append(ComponentRecord(pipe_name, prev_node, manifold_out, pipe))

    elif routing_type == "parallel":
        # Each cold plate gets its own branch: manifold_in → cp → manifold_out
        for cp_cfg in cold_plates:
            cp_in = f"{server_id}_{cp_cfg.id}_in"
            cp_out = f"{server_id}_{cp_cfg.id}_out"
            G.add_node(cp_in)
            G.add_node(cp_out)

            # Inlet pipe
            pipe_in = create_component_by_name("pipe", "pipe_25mm", specs, fluid.mu, fluid.rho, pipe_length=0.5)
            G.add_edge(manifold_in, cp_in)
            components.append(ComponentRecord(f"{server_id}_{cp_cfg.id}_inlet_pipe", manifold_in, cp_in, pipe_in))

            # Cold plate
            cp_comp = create_component_by_name("cold_plate", cp_cfg.specName, specs, fluid.mu, fluid.rho)
            G.add_edge(cp_in, cp_out)
            components.append(ComponentRecord(f"{server_id}_{cp_cfg.id}", cp_in, cp_out, cp_comp))

            # Outlet pipe
            pipe_out = create_component_by_name("pipe", "pipe_25mm", specs, fluid.mu, fluid.rho, pipe_length=0.5)
            G.add_edge(cp_out, manifold_out)
            components.append(ComponentRecord(f"{server_id}_{cp_cfg.id}_outlet_pipe", cp_out, manifold_out, pipe_out))

    elif routing_type == "custom":
        # Build from explicit connections — implementation for Phase 5
        # For now, fall back to series
        _add_server_coldplates(
            G, components, server_id,
            ServerLiquidCooling(enabled=True, coldPlates=cold_plates,
                                routing=type(slc.routing)(type="series")),
            manifold_in, manifold_out, specs, fluid,
        )
```

- [ ] **Step 4: Run tests**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_topology.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/backend/liquidcooling/topology.py tests/test_liquid/test_topology.py
git commit -m "feat(liquid): add auto topology builder from liquidCooling config"
```

---

## Task 12: Results Data Structures

**Files:**
- Create: `src/backend/liquidcooling/results.py`

- [ ] **Step 1: Implement results.py**

`src/backend/liquidcooling/results.py`:
```python
"""Result data structures for liquid cooling simulation."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict
import json
from pathlib import Path


@dataclass
class ServerLiquidResult:
    """Liquid cooling results for a single server."""
    server_id: str
    liquid_flow_rate_LPM: float
    cold_plate_inlet_temp_C: float
    cold_plate_outlet_temp_C: float
    chip_max_temp_C: float
    liquid_heat_capture_ratio: float
    residual_heat_to_air_W: float
    liquid_heat_removed_W: float
    server_total_power_W: float


@dataclass
class CDUResult:
    """Aggregated results for a CDU."""
    cdu_id: str
    supply_temp_C: float
    return_temp_C: float
    total_flow_LPM: float
    pump_power_W: float = 0.0


@dataclass
class LiquidResult:
    """Complete liquid cooling simulation results."""
    node_pressures: Dict[str, float] = field(default_factory=dict)
    comp_flows: Dict[str, float] = field(default_factory=dict)
    servers: Dict[str, ServerLiquidResult] = field(default_factory=dict)
    cdus: Dict[str, CDUResult] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "nodes": self.node_pressures,
            "edges": self.comp_flows,
            "servers": {k: asdict(v) for k, v in self.servers.items()},
            "cdus": {k: asdict(v) for k, v in self.cdus.items()},
        }

    def save(self, path: Path) -> None:
        """Save results to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> LiquidResult:
        """Load results from JSON file."""
        with open(path) as f:
            data = json.load(f)
        result = cls()
        result.node_pressures = data.get("nodes", {})
        result.comp_flows = data.get("edges", {})
        for k, v in data.get("servers", {}).items():
            result.servers[k] = ServerLiquidResult(**v)
        for k, v in data.get("cdus", {}).items():
            result.cdus[k] = CDUResult(**v)
        return result
```

- [ ] **Step 2: Quick smoke test**

```bash
conda run -n dcdtuni python -c "
from dcdtuni.liquid.results import LiquidResult, ServerLiquidResult, CDUResult
r = LiquidResult()
r.servers['s1'] = ServerLiquidResult('s1', 2.5, 35.0, 42.0, 68.5, 0.85, 105.0, 595.0, 700.0)
r.cdus['CDU_1'] = CDUResult('CDU_1', 35.0, 41.2, 60.0)
d = r.to_dict()
print(f'Servers: {len(d[\"servers\"])}, CDUs: {len(d[\"cdus\"])}')
"
```

Expected: `Servers: 1, CDUs: 1`

- [ ] **Step 3: Commit**

```bash
git add src/backend/liquidcooling/results.py
git commit -m "feat(liquid): add result data structures with JSON serialization"
```

---

## Task 13: Integration Test with Reference Data

**Files:**
- Create: `tests/test_liquid/test_integration.py`

This test validates the full pipeline: config → topology → solver → thermal → results.

- [ ] **Step 1: Write integration test**

`tests/test_liquid/test_integration.py`:
```python
"""End-to-end integration test: config → topology → solver → thermal → results."""
import unittest


class TestFullPipeline(unittest.TestCase):
    """Test complete liquid cooling simulation pipeline."""

    def test_single_rack_single_server(self):
        """1 CDU, 1 rack, 1 server with 1 cold plate."""
        from dcdtuni.liquid.schema import (
            LiquidCoolingConfig, FluidConfig, CDUConfig, Specifications,
            PipeSpec, ColdPlateSpec, PipingConfig,
        )
        from dcdtuni.liquid.topology import build_network
        from dcdtuni.liquid.solver.steady import solve_steady
        from dcdtuni.liquid.thermal.chip_model import compute_chip_max_temp
        from dcdtuni.liquid.thermal.hybrid_cooling import compute_heat_capture_ratio
        from dcdtuni.liquid.results import LiquidResult, ServerLiquidResult, CDUResult

        # --- Config ---
        liquid_cfg = LiquidCoolingConfig(
            enabled=True,
            fluid=FluidConfig(rho=992.2, mu=0.000653, cp=4180.0, k=0.632),
            cdus={"CDU_1": CDUConfig(supplyTemperature=35.0, totalFlowRate=60.0, servedRacks=["Rack_1"])},
            specifications=Specifications(
                pipeSpec=[PipeSpec(name="pipe_25mm", diameter=0.025, roughness=1e-5, length=2.0)],
                coldPlateSpec=[ColdPlateSpec(name="cp_default", coefA=0.724, coefB=0.991, coefC=-0.155)],
            ),
            piping=PipingConfig(autoGenerated=True, mainPipeSpec="pipe_25mm"),
        )

        racks_data = {
            "Rack_1": {
                "servers": [{
                    "id": "Rack_1_s01",
                    "type": "fanServer",
                    "physics": {"volumeFlowRate": 0.05, "power": 700},
                    "liquidCooling": {
                        "enabled": True,
                        "coldPlates": [{
                            "id": "cp_cpu", "label": "CPU", "specName": "cp_default",
                            "chipTDP": 350,
                            "thermalModel": {"chipArea": 0.004, "chipThickness": 0.001, "thermalConductivity": 148},
                            "position": {"x": 0.25, "y": 0.5},
                        }],
                        "routing": {"type": "series", "connections": []},
                    },
                }],
            },
        }

        # --- Build network ---
        G, components = build_network(liquid_cfg, racks_data)
        self.assertGreater(len(G.nodes), 0)
        self.assertGreater(len(components), 0)

        # --- Solve ---
        node_pressures, comp_flows = solve_steady(G, components, verbose=False)
        self.assertGreater(len(node_pressures), 0)

        # All pressures finite
        for n, p in node_pressures.items():
            self.assertTrue(-1e6 < p < 1e6, f"Pressure at {n} = {p} not finite")

        # --- Thermal ---
        # Get cold plate flow rate (find the cold plate component)
        cp_flow = None
        for name, q in comp_flows.items():
            if "cp_cpu" in name and "pipe" not in name:
                cp_flow = abs(q)
                break
        self.assertIsNotNone(cp_flow, "Should find cold plate flow")
        self.assertGreater(cp_flow, 0.0, "Cold plate should have flow")

        eta = compute_heat_capture_ratio(
            water_flow_rate_LPM=cp_flow,
            air_flow_rate_m3s=0.05,
            chip_area_m2=0.004,
            fluid_density_kg_m3=992.2,
            fluid_cp_J_kgK=4180.0,
            fluid_viscosity_Pa_s=0.000653,
            fluid_conductivity_W_mK=0.632,
        )
        self.assertGreater(eta, 0.0)
        self.assertLess(eta, 1.0)

        T_chip = compute_chip_max_temp(
            water_inlet_temp_C=35.0,
            chip_tdp_W=350.0,
            chip_area_m2=0.004,
            chip_thickness_m=0.001,
            thermal_conductivity_W_mK=148.0,
            water_flow_rate_LPM=cp_flow,
            fluid_density_kg_m3=992.2,
            fluid_cp_J_kgK=4180.0,
        )
        self.assertGreater(T_chip, 35.0)
        self.assertLess(T_chip, 150.0)

        # --- Results ---
        liquid_heat = eta * 350.0
        residual = 700.0 - liquid_heat
        m_dot = 992.2 * cp_flow / 60000.0
        T_out = 35.0 + liquid_heat / (m_dot * 4180.0)

        result = LiquidResult(
            node_pressures=node_pressures,
            comp_flows=comp_flows,
        )
        result.servers["Rack_1_s01"] = ServerLiquidResult(
            server_id="Rack_1_s01",
            liquid_flow_rate_LPM=cp_flow,
            cold_plate_inlet_temp_C=35.0,
            cold_plate_outlet_temp_C=T_out,
            chip_max_temp_C=T_chip,
            liquid_heat_capture_ratio=eta,
            residual_heat_to_air_W=residual,
            liquid_heat_removed_W=liquid_heat,
            server_total_power_W=700.0,
        )
        result.cdus["CDU_1"] = CDUResult("CDU_1", 35.0, T_out, 60.0)

        # Verify JSON round-trip
        d = result.to_dict()
        self.assertIn("Rack_1_s01", d["servers"])
        self.assertIn("CDU_1", d["cdus"])

    def test_two_racks_two_servers_each(self):
        """2 CDUs serving 2 racks, each with 2 servers — more complex topology."""
        from dcdtuni.liquid.schema import (
            LiquidCoolingConfig, FluidConfig, CDUConfig, Specifications,
            PipeSpec, ColdPlateSpec, PipingConfig,
        )
        from dcdtuni.liquid.topology import build_network
        from dcdtuni.liquid.solver.steady import solve_steady

        liquid_cfg = LiquidCoolingConfig(
            enabled=True,
            fluid=FluidConfig(rho=992.2, mu=0.000653, cp=4180.0, k=0.632),
            cdus={"CDU_1": CDUConfig(supplyTemperature=35.0, totalFlowRate=60.0, servedRacks=["Rack_1", "Rack_2"])},
            specifications=Specifications(
                pipeSpec=[PipeSpec(name="pipe_25mm", diameter=0.025, roughness=1e-5, length=2.0)],
                coldPlateSpec=[ColdPlateSpec(name="cp_default", coefA=0.724, coefB=0.991, coefC=-0.155)],
            ),
            piping=PipingConfig(autoGenerated=True, mainPipeSpec="pipe_25mm"),
        )

        def _make_server(rack, idx):
            return {
                "id": f"{rack}_s{idx:02d}", "type": "fanServer",
                "physics": {"volumeFlowRate": 0.05, "power": 700},
                "liquidCooling": {
                    "enabled": True,
                    "coldPlates": [
                        {"id": "cp_cpu", "label": "CPU", "specName": "cp_default", "chipTDP": 350,
                         "thermalModel": {"chipArea": 0.004, "chipThickness": 0.001, "thermalConductivity": 148},
                         "position": {"x": 0.25, "y": 0.5}},
                        {"id": "cp_gpu", "label": "GPU", "specName": "cp_default", "chipTDP": 350,
                         "thermalModel": {"chipArea": 0.006, "chipThickness": 0.001, "thermalConductivity": 148},
                         "position": {"x": 0.65, "y": 0.5}},
                    ],
                    "routing": {"type": "series", "connections": []},
                },
            }

        racks_data = {
            "Rack_1": {"servers": [_make_server("Rack_1", 1), _make_server("Rack_1", 2)]},
            "Rack_2": {"servers": [_make_server("Rack_2", 1), _make_server("Rack_2", 2)]},
        }

        G, components = build_network(liquid_cfg, racks_data)
        node_pressures, comp_flows = solve_steady(G, components, verbose=False)

        # Should have pressures for all nodes
        self.assertGreater(len(node_pressures), 4)
        # Should have flows for all components
        self.assertGreater(len(comp_flows), 8)
        # All pressures finite
        for n, p in node_pressures.items():
            self.assertTrue(-1e6 < p < 1e6, f"Pressure at {n} = {p}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run integration tests**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/test_integration.py -v
```

Expected: 2 passed

- [ ] **Step 3: Run ALL liquid tests to verify nothing is broken**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/ -v
```

Expected: All tests pass (components + solver + thermal + schema + topology + integration)

- [ ] **Step 4: Commit**

```bash
git add tests/test_liquid/test_integration.py
git commit -m "test(liquid): add end-to-end integration tests for full pipeline"
```

---

## Task 14: Module Exports and Final Cleanup

**Files:**
- Modify: `src/backend/liquidcooling/__init__.py`
- Modify: `src/backend/liquidcooling/solver/__init__.py`
- Modify: `src/backend/liquidcooling/thermal/__init__.py`
- Modify: `src/backend/liquidcooling/devices/__init__.py`

- [ ] **Step 1: Update all __init__.py exports**

`src/backend/liquidcooling/__init__.py`:
```python
"""
Liquid cooling network simulation module.

Steady-state hydraulic solver for data center liquid cooling systems
including pipes, pumps, cold plates, control valves, heat exchangers,
and CDU (Coolant Distribution Unit) assemblies.
"""
from dcdtuni.liquid.solver.steady import solve_steady, ComponentRecord
from dcdtuni.liquid.topology import build_network
from dcdtuni.liquid.schema import LiquidCoolingConfig
from dcdtuni.liquid.results import LiquidResult

__all__ = ["solve_steady", "ComponentRecord", "build_network", "LiquidCoolingConfig", "LiquidResult"]
```

`src/backend/liquidcooling/solver/__init__.py`:
```python
"""Hydraulic network solvers."""
from dcdtuni.liquid.solver.steady import solve_steady, ComponentRecord

__all__ = ["solve_steady", "ComponentRecord"]
```

`src/backend/liquidcooling/thermal/__init__.py`:
```python
"""Thermal models for chip temperature and heat load distribution."""
from dcdtuni.liquid.thermal.chip_model import compute_chip_max_temp
from dcdtuni.liquid.thermal.hybrid_cooling import compute_heat_capture_ratio

__all__ = ["compute_chip_max_temp", "compute_heat_capture_ratio"]
```

`src/backend/liquidcooling/devices/__init__.py`:
```python
"""Device-level assemblies (CDU, Manifold)."""
```

- [ ] **Step 2: Verify top-level import**

```bash
conda run -n dcdtuni python -c "
from dcdtuni.liquid import solve_steady, build_network, LiquidCoolingConfig, LiquidResult
from dcdtuni.liquid.components import PressureDrivenPipe, PumpWithSpeed, ColdPlate, ControlValveKv
from dcdtuni.liquid.thermal import compute_chip_max_temp, compute_heat_capture_ratio
print('All imports OK')
"
```

- [ ] **Step 3: Run full test suite one final time**

```bash
conda run -n dcdtuni python -m pytest tests/test_liquid/ -v --tb=short
```

Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add src/backend/liquidcooling/
git commit -m "feat(liquid): finalize module exports for Phase 1 liquid solver"
```

---

## Summary

| Task | Description | Files Created | Tests |
|------|-------------|---------------|-------|
| 1 | Project setup | 7 __init__.py + pyproject.toml | 0 |
| 2 | Pipe component | pipe.py | 5 |
| 3 | Pump component | pump.py | 4 |
| 4 | ColdPlate + HX + QuickConnect | 3 files | 8 |
| 5 | Control valve | valve.py | 6 |
| 6 | Component exports | __init__.py | 0 |
| 7 | LM steady-state solver | steady.py | 6 |
| 8 | Thermal models | chip_model.py, hybrid_cooling.py | 4 |
| 9 | Pydantic schema | schema.py | 4 |
| 10 | Component factory | factory.py | 0 (smoke test) |
| 11 | Topology builder | topology.py | 3 |
| 12 | Results dataclasses | results.py | 0 (smoke test) |
| 13 | Integration test | test_integration.py | 2 |
| 14 | Module exports | 4 __init__.py | 0 |
| **Total** | | **~25 files** | **~42 tests** |
