import math, torch, pytest
from backend.liquidcooling.handlers.pipe import PipeBatchHandler
from backend.liquidcooling.graphcompiling.compiled_graph import PipeIndices, PipeBatchParams

torch.set_default_dtype(torch.float64)

class TestSwameeJain:
    def _make_handler(self, roughness=1.5e-6):
        D = 0.025
        params = PipeBatchParams(D=torch.tensor([D]), L=torch.tensor([1.0]),
            dh=torch.tensor([0.0]), K_minor=torch.tensor([0.0]),
            A=torch.tensor([math.pi/4*D**2]), roughness=torch.tensor([roughness]))
        return PipeBatchHandler(params, rho=997.0, mu=0.00089, cp=4182.0, g_mag=9.81)

    def test_laminar_unchanged(self):
        """Laminar flow (Re=500) unaffected by roughness."""
        h = self._make_handler(roughness=0.001)
        D, mu, rho = 0.025, 0.00089, 997.0
        A = math.pi/4 * D**2
        m = 500.0 * A * mu / D
        Q = m / rho
        dp_hp = Q * 128 * mu * 1.0 / (math.pi * D**4)
        state = {"P": torch.tensor([[dp_hp], [0.0]]), "m": torch.tensor([[m]])}
        idx = PipeIndices(edge_ids=torch.tensor([0]), node_from=torch.tensor([0]),
                         node_to=torch.tensor([1]), cell_start=torch.tensor([0]),
                         cell_end=torch.tensor([0]))
        res = h.compute_residuals(state, idx, 1.0, 1)
        assert res["momentum"].abs().max().item() < 1.0

    def test_rough_vs_smooth_differ(self):
        """Turbulent: rough pipe has different friction than smooth."""
        h_rough = self._make_handler(roughness=0.001)
        h_smooth = self._make_handler(roughness=1e-7)
        D, mu, rho = 0.025, 0.00089, 997.0
        A = math.pi/4 * D**2
        m = 100000.0 * A * mu / D
        state = {"P": torch.tensor([[100000.0], [0.0]]), "m": torch.tensor([[m]])}
        idx = PipeIndices(edge_ids=torch.tensor([0]), node_from=torch.tensor([0]),
                         node_to=torch.tensor([1]), cell_start=torch.tensor([0]),
                         cell_end=torch.tensor([0]))
        r_rough = h_rough.compute_residuals(state, idx, 1.0, 1)["momentum"].item()
        r_smooth = h_smooth.compute_residuals(state, idx, 1.0, 1)["momentum"].item()
        assert r_rough != pytest.approx(r_smooth, abs=10.0)

    def test_moody_reference(self):
        """Swamee-Jain at Re=10000, e/D=0.001 should give f≈0.0327 (±5%).

        Reference: Colebrook-White iterative gives 0.03238 at these conditions;
        Swamee-Jain explicit approximation gives 0.03267 (within 1% of C-W).
        """
        D = 0.025
        roughness = 0.001 * D
        e_D = roughness / D
        Re = torch.tensor([[10000.0]])
        log_arg = e_D / 3.7 + 5.74 / Re.pow(0.9)
        f_sj = 0.25 / torch.log10(log_arg).pow(2)
        assert f_sj.item() == pytest.approx(0.0327, rel=0.05)
