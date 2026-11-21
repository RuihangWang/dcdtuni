import math, torch, pytest

torch.set_default_dtype(torch.float64)

class TestNTUEffectiveness:
    def test_high_ntu_saturation(self):
        UA, m, cp = 10000.0, 0.01, 4182.0
        T_s, T_in = 350.0, 308.15
        NTU = UA / (abs(m) * cp + 1e-10)
        eff = 1.0 - math.exp(-NTU)
        T_out = T_in + eff * (T_s - T_in)
        assert T_out == pytest.approx(T_s, abs=0.1)

    def test_low_ntu_matches_linear(self):
        UA, m, cp = 1.0, 1.0, 4182.0
        T_s, T_in = 350.0, 308.15
        NTU = UA / (m * cp)
        eff = 1.0 - math.exp(-NTU)
        T_ntu = T_in + eff * (T_s - T_in)
        T_lin = T_in + UA * (T_s - T_in) / (m * cp)
        assert T_ntu == pytest.approx(T_lin, rel=0.01)

    def test_moderate_ntu_differs(self):
        UA, m, cp = 150.0, 0.03, 4182.0
        T_s, T_in = 350.0, 308.15
        NTU = UA / (m * cp)
        eff = 1.0 - math.exp(-NTU)
        T_ntu = T_in + eff * (T_s - T_in)
        T_lin = T_in + UA * (T_s - T_in) / (m * cp)
        assert T_lin > T_ntu + 0.5
