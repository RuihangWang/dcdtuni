"""backend.aircooling.zonal: conservation, convergence and the implicit-adjoint gradient."""
import pytest

torch = pytest.importorskip("torch")

from backend.aircooling.zonal import ZonalNetwork  # noqa: E402


def _room(mixing=0.0):
    # 0 cold, 1 hot, 2 high, 3 return node, 4 supply node; one server cold->hot, one unit 3->4
    return ZonalNetwork(
        node_names=("cold", "hot", "high", "ret", "sup"), height_m=(0.5, 1.0, 2.5, 1.0, 1.0),
        passive_pairs=((0, 1), (1, 2), (0, 2), (1, 3), (4, 0)), directed=(False, False, False, True, True),
        server_pairs=((0, 1),), server_flow_m3s=(1.0,), server_power_kw=(10.0,), unit_pairs=((3, 4),),
        mixing_m3s_per_m2=mixing)


AREAS = torch.tensor([0.05, 2.0, 2.0, 0.5, 0.5], dtype=torch.float64)


@pytest.mark.parametrize("mixing", [0.0, 0.01])
def test_mass_and_energy_close(mixing):
    net = _room(mixing)
    c = torch.tensor([18.0, 1.2], dtype=torch.float64)
    state, info = net.solve(c, AREAS)
    T = net.temperatures(state, c, AREAS)
    assert float(T["mass_residual_m3s"][1:].abs().max()) < 1e-9
    assert float(T["unit_coil_kw"].sum()) == pytest.approx(10.0, rel=1e-8)   # all server heat leaves at the coil
    rise = 10.0e3 / (net.rho * net.cp * 1.0)
    assert float(T["server_inlet_c"][0]) >= 18.0 - 1e-9
    assert float(T["unit_return_c"][0]) == pytest.approx(18.0 + 10.0e3 / (net.rho * net.cp * 1.2), rel=1e-6)
    assert rise > 0 and info["residual_inf"] < 1e-10


def test_implicit_gradient_matches_finite_differences():
    net = _room(0.01)
    c0 = torch.tensor([18.0, 1.2], dtype=torch.float64)

    def qoi(c, a):
        s = net.differentiable_state(c, a)
        return net.temperatures(s, c, a)["server_inlet_c"][0]

    c = c0.clone().requires_grad_(True)
    a = AREAS.clone().requires_grad_(True)
    gc, ga = torch.autograd.grad(qoi(c, a), (c, a))
    h = 1e-6
    for i in range(2):
        e = torch.zeros(2, dtype=torch.float64)
        e[i] = h
        fd = (qoi(c0 + e, AREAS) - qoi(c0 - e, AREAS)) / (2 * h)
        assert float(gc[i]) == pytest.approx(float(fd), rel=1e-5, abs=1e-8)
    for i in range(len(AREAS)):
        e = torch.zeros(len(AREAS), dtype=torch.float64)
        e[i] = h
        fd = (qoi(c0, AREAS + e) - qoi(c0, AREAS - e)) / (2 * h)
        assert float(ga[i]) == pytest.approx(float(fd), rel=1e-4, abs=1e-6)


def test_recirculation_rises_when_the_unit_flow_drops():
    net = _room(0.01)

    def inlet(flow):
        c = torch.tensor([18.0, flow], dtype=torch.float64)
        return float(net.temperatures(net.solve(c, AREAS)[0], c, AREAS)["server_inlet_c"][0])

    assert inlet(0.6) > inlet(1.5)


def test_invalid_networks_are_rejected():
    with pytest.raises(ValueError, match="connected"):
        ZonalNetwork(("a", "b", "c", "d"), (0, 0, 0, 0), ((0, 1),), (False,), (), (), (), ((2, 3),))
    with pytest.raises(ValueError, match="own supply node"):
        ZonalNetwork(("a", "b", "c"), (0, 0, 0), ((0, 1), (1, 2)), (False, False), (), (), (), ((0, 2), (1, 2)))
