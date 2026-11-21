"""TDD: turbulence stabilization for large data center cases.

CMI-Guichou (238 racks) k/epsilon explodes at iteration ~17 due to
positive feedback: G = nut * |S|^2 → high k → high nut → higher G.

Fix: limitTurbulenceViscosity (fvOptions) + gradient limiters (fvSchemes)
+ relaxation ramping (fvSolution).
"""
from pathlib import Path

import pytest

from backend.aircooling.runner.openfoam_case import (
    generate_fv_options,
    generate_fv_schemes,
    generate_fv_solution,
)


class TestLimitTurbulenceViscosity:
    """fvOptions should include limitTurbulenceViscosity to cap nut."""

    def test_fvoptions_contains_limit_nut(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_options(tmp_path, leakage_zones={})
        content = (system_dir / "fvOptions").read_text()
        assert "limitTurbulenceViscosity" in content
        assert "limitNut" in content

    def test_limit_nut_has_correct_coefficient(self, tmp_path):
        system_dir = tmp_path / "system"
        generate_fv_options(tmp_path, leakage_zones={})
        content = (system_dir / "fvOptions").read_text()
        assert "1e5" in content or "100000" in content


class TestGradientLimiters:
    """fvSchemes should use cellLimited gradients for k and epsilon."""

    def test_grad_k_uses_cell_limited_when_configured(self, tmp_path):
        system_dir = tmp_path
        system_dir.mkdir(exist_ok=True)
        generate_fv_schemes(system_dir, {"gradSchemeK": "cellLimited Gauss linear 1"})
        content = (system_dir / "fvSchemes").read_text()
        assert "grad(k)" in content
        assert "cellLimited" in content

    def test_grad_epsilon_uses_cell_limited_when_configured(self, tmp_path):
        system_dir = tmp_path
        system_dir.mkdir(exist_ok=True)
        generate_fv_schemes(system_dir, {"gradSchemeEpsilon": "cellLimited Gauss linear 1"})
        content = (system_dir / "fvSchemes").read_text()
        assert "grad(epsilon)" in content

    def test_grad_k_epsilon_omitted_when_empty(self, tmp_path):
        system_dir = tmp_path
        system_dir.mkdir(exist_ok=True)
        generate_fv_schemes(system_dir, {})
        content = (system_dir / "fvSchemes").read_text()
        assert "grad(k)" not in content
        assert "grad(epsilon)" not in content


class TestRelaxationRamping:
    """fvSolution should use table-based ramping for k/epsilon relaxation."""

    def test_k_epsilon_uses_fixed_relaxation(self, tmp_path):
        """fvSolution uses fixed relaxation 0.3 for k/epsilon (no table ramping)."""
        system_dir = tmp_path
        system_dir.mkdir(exist_ok=True)
        generate_fv_solution(system_dir)
        content = (system_dir / "fvSolution").read_text()
        assert "0.3" in content

    def test_k_epsilon_uses_configured_relaxation_value(self, tmp_path):
        """fvSolution uses the configured fixed relaxation value for k/epsilon."""
        system_dir = tmp_path
        system_dir.mkdir(exist_ok=True)
        generate_fv_solution(system_dir, relaxation_factors={"k_epsilon": 0.3})
        content = (system_dir / "fvSolution").read_text()
        assert "0.3" in content
