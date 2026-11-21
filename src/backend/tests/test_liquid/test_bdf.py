"""Tests for BDF time-derivative utilities and smooth_abs."""
import math
import pytest
import torch
from torch import Tensor

from backend.liquidcooling.solver.bdf import smooth_abs, bdf_diff


# ---------------------------------------------------------------------------
# smooth_abs
# ---------------------------------------------------------------------------

class TestSmoothAbs:
    def test_positive(self):
        x = torch.tensor(3.0)
        result = smooth_abs(x)
        # sqrt(9 + eps^2) ≈ 3.0
        assert abs(result.item() - 3.0) < 1e-4

    def test_negative(self):
        x = torch.tensor(-4.0)
        result = smooth_abs(x)
        # sqrt(16 + eps^2) ≈ 4.0
        assert abs(result.item() - 4.0) < 1e-4

    def test_near_zero(self):
        x = torch.tensor(0.0)
        result = smooth_abs(x)
        # sqrt(0 + eps^2) = eps = 1e-6 by default
        assert abs(result.item() - 1e-6) < 1e-10

    def test_gradient_at_zero(self):
        """smooth_abs is differentiable at zero (no nan/inf gradient)."""
        x = torch.tensor(0.0, requires_grad=True)
        y = smooth_abs(x)
        y.backward()
        grad = x.grad
        assert grad is not None
        assert not torch.isnan(grad), "gradient should not be nan at zero"
        assert not torch.isinf(grad), "gradient should not be inf at zero"

    def test_custom_eps(self):
        x = torch.tensor(0.0)
        result = smooth_abs(x, eps=1e-3)
        assert abs(result.item() - 1e-3) < 1e-8

    def test_batch(self):
        x = torch.tensor([-2.0, 0.0, 3.0])
        result = smooth_abs(x)
        assert result.shape == (3,)
        assert abs(result[0].item() - 2.0) < 1e-4
        assert abs(result[2].item() - 3.0) < 1e-4


# ---------------------------------------------------------------------------
# bdf_diff
# ---------------------------------------------------------------------------

def _make_linear(N_t: int, dt: float, batch: tuple = ()):
    """x[..., t] = t*dt  →  exact derivative = 1.0 everywhere."""
    t = torch.arange(N_t, dtype=torch.float64) * dt
    shape = batch + (N_t,)
    x = t.expand(shape).clone()
    return x

def _make_quadratic(N_t: int, dt: float, batch: tuple = ()):
    """x[..., t] = (t*dt)^2  →  exact derivative = 2*t*dt."""
    t = torch.arange(N_t, dtype=torch.float64) * dt
    shape = batch + (N_t,)
    x = (t * t).expand(shape).clone()
    return x


class TestBdfDiff:
    # --- output length ---

    def test_output_length_bdf1(self):
        x = _make_linear(10, 0.1)
        dx = bdf_diff(x, dt=0.1, order=1)
        assert dx.shape[-1] == 9, "BDF1 should give N_t - 1 output steps"

    def test_output_length_bdf2(self):
        x = _make_linear(10, 0.1)
        dx = bdf_diff(x, dt=0.1, order=2)
        assert dx.shape[-1] == 9, "BDF2 should give N_t - 1 output steps"

    def test_output_length_bdf3(self):
        x = _make_linear(10, 0.1)
        dx = bdf_diff(x, dt=0.1, order=3)
        assert dx.shape[-1] == 9, "BDF3 should give N_t - 1 output steps"

    def test_output_length_consistent_all_orders(self):
        """All orders must return the same output length for the same input."""
        x = _make_linear(7, 0.05)
        lengths = [bdf_diff(x, dt=0.05, order=k).shape[-1] for k in (1, 2, 3)]
        assert lengths[0] == lengths[1] == lengths[2]

    # --- BDF1 linear (exact) ---

    def test_bdf1_linear_exact(self):
        """BDF1 is exact for linear x: derivative should be 1.0 everywhere."""
        dt = 0.1
        x = _make_linear(20, dt)
        dx = bdf_diff(x, dt=dt, order=1)
        error = (dx - 1.0).abs().max().item()
        assert error < 1e-10, f"BDF1 linear error too large: {error}"

    # --- BDF2 linear (exact) ---

    def test_bdf2_linear_exact(self):
        """BDF2 is also exact for linear x."""
        dt = 0.1
        x = _make_linear(20, dt)
        dx = bdf_diff(x, dt=dt, order=2)
        error = (dx - 1.0).abs().max().item()
        assert error < 1e-10, f"BDF2 linear error too large: {error}"

    # --- BDF1 quadratic: O(dt) error ---

    def test_bdf1_quadratic_order1_error(self):
        """BDF1 on x=t^2 should have O(dt) truncation error (not near-exact)."""
        dt = 0.01
        N_t = 50
        x = _make_quadratic(N_t, dt)
        dx = bdf_diff(x, dt=dt, order=1)
        # exact derivative at t_k = k*dt is 2*k*dt (for k=1..N_t-1)
        t_mid = torch.arange(1, N_t, dtype=torch.float64) * dt
        exact = 2.0 * t_mid
        error = (dx - exact).abs().max().item()
        # BDF1 is O(dt) accurate: error ~ dt ~ 0.01, not < 1e-6
        assert error < 0.1, f"BDF1 quadratic error too large: {error}"
        # And NOT near-exact (truncation error exists)
        assert error > 1e-8, "BDF1 should NOT be near-exact on quadratic (expected O(dt) error)"

    # --- BDF2 quadratic: near-exact ---

    def test_bdf2_quadratic_near_exact(self):
        """BDF2 is exact for quadratic polynomials (second-order method)."""
        dt = 0.01
        N_t = 50
        x = _make_quadratic(N_t, dt)
        dx = bdf_diff(x, dt=dt, order=2)
        t_mid = torch.arange(1, N_t, dtype=torch.float64) * dt
        exact = 2.0 * t_mid
        # First step (index 0) uses BDF1 fallback → allow larger error there
        error_main = (dx[1:] - exact[1:]).abs().max().item()
        assert error_main < 1e-8, f"BDF2 quadratic error (main steps) too large: {error_main}"

    # --- BDF3 quadratic ---

    def test_bdf3_quadratic(self):
        """BDF3 on quadratic should also be near-exact (order >= 2)."""
        dt = 0.01
        N_t = 50
        x = _make_quadratic(N_t, dt)
        dx = bdf_diff(x, dt=dt, order=3)
        t_mid = torch.arange(1, N_t, dtype=torch.float64) * dt
        exact = 2.0 * t_mid
        # Steps with full BDF3 stencil: index 2..N_t-2
        error_main = (dx[2:] - exact[2:]).abs().max().item()
        assert error_main < 1e-8, f"BDF3 quadratic error (main steps) too large: {error_main}"

    # --- batch dimension ---

    def test_batch_dimension_2d(self):
        """bdf_diff should work with batch dimensions [..., N_t]."""
        dt = 0.1
        x = _make_linear(15, dt, batch=(4,))
        dx = bdf_diff(x, dt=dt, order=2)
        assert dx.shape == (4, 14), f"Expected (4,14), got {dx.shape}"
        error = (dx - 1.0).abs().max().item()
        assert error < 1e-10

    def test_batch_dimension_3d(self):
        """bdf_diff should work with 3D batch [B, C, N_t]."""
        dt = 0.05
        x = _make_linear(12, dt, batch=(3, 5))
        dx = bdf_diff(x, dt=dt, order=1)
        assert dx.shape == (3, 5, 11), f"Expected (3,5,11), got {dx.shape}"

    # --- edge cases ---

    def test_minimum_length_bdf1(self):
        """BDF1 requires at least 2 time steps."""
        x = torch.tensor([1.0, 2.0])
        dx = bdf_diff(x, dt=1.0, order=1)
        assert dx.shape[-1] == 1

    def test_minimum_length_bdf2(self):
        """BDF2 with only 2 steps should fall back to BDF1 (output length 1)."""
        x = torch.tensor([1.0, 3.0])
        dx = bdf_diff(x, dt=1.0, order=2)
        assert dx.shape[-1] == 1
        # BDF1 fallback: (3-1)/1 = 2
        assert abs(dx[0].item() - 2.0) < 1e-10

    def test_invalid_order_raises(self):
        """Order outside {1,2,3} should raise ValueError."""
        x = torch.ones(10)
        with pytest.raises((ValueError, NotImplementedError)):
            bdf_diff(x, dt=0.1, order=0)
        with pytest.raises((ValueError, NotImplementedError)):
            bdf_diff(x, dt=0.1, order=4)
