"""CPU contract tests for strict 1D sensible-enthalpy and UID identity."""
from dataclasses import replace
import numpy as np
import pytest
import torch
from agent.scenarios.hybrid.thermal_stream import (
    SI_UNITS, UIDField, server_exhaust_temperature,
    server_exhaust_temperature_by_uid, sensible_heat_W,
)


def test_scalar_and_explicit_1d_calorimetry_preserve_signed_heat():
    tin=np.array([291.15,293.15,295.15])
    q=np.array([900.,0.,-300.]);flow=np.array([.151,.301,.151])
    tout=server_exhaust_temperature(tin,q,flow)
    assert tout.shape==(3,)
    np.testing.assert_allclose(sensible_heat_W(tin,tout,flow),q,rtol=0,atol=1e-10)
    assert server_exhaust_temperature(293.15,0.,.151)==293.15
    assert server_exhaust_temperature(293.15,900.,.151)==pytest.approx(293.15+900./(1.19*1005*.151))


def test_only_true_scalars_broadcast_and_lists_are_explicit_1d():
    tout=server_exhaust_temperature(293.15,[900.,1800.],.151)
    np.testing.assert_allclose(tout,293.15+np.array([900.,1800.])/(1.19*1005*.151))
    with pytest.raises(ValueError,match="same length"):
        server_exhaust_temperature(np.array([293.15]),np.array([900.,1800.]),.151)


@pytest.mark.parametrize("name",["T_in_K","Q_air_W","flow_m3s","rho_kg_m3","cp_J_kgK"])
def test_column_vectors_never_expand_to_n_by_n(name):
    args={"T_in_K":np.array([293.15,294.15]),"Q_air_W":np.array([900.,1800.]),
          "flow_m3s":np.array([.151,.301]),"rho_kg_m3":1.19,"cp_J_kgK":1005.}
    args[name]=np.ones((2,1))
    with pytest.raises(ValueError,match="2D broadcasting"):
        server_exhaust_temperature(**args)


@pytest.mark.parametrize("bad",[float("nan"),float("inf"),-float("inf")])
@pytest.mark.parametrize("name",["T_in_K","Q_air_W","flow_m3s","rho_kg_m3","cp_J_kgK"])
def test_nonfinite_input_is_rejected_in_every_channel(name,bad):
    args={"T_in_K":293.15,"Q_air_W":900.,"flow_m3s":.151,"rho_kg_m3":1.19,"cp_J_kgK":1005.}
    args[name]=bad
    with pytest.raises(ValueError,match="finite"):
        server_exhaust_temperature(**args)


@pytest.mark.parametrize("name",["T_in_K","flow_m3s","rho_kg_m3","cp_J_kgK"])
@pytest.mark.parametrize("bad",[0.,-1.])
def test_physical_positive_quantities_cannot_be_nonpositive(name,bad):
    args={"T_in_K":293.15,"Q_air_W":900.,"flow_m3s":.151,"rho_kg_m3":1.19,"cp_J_kgK":1005.}
    args[name]=bad
    with pytest.raises(ValueError,match="positive"):
        server_exhaust_temperature(**args)


@pytest.mark.parametrize("field,bad",[("temperature","C"),("heat","kW"),("volume_flow","L/s"),
                                       ("density","g/cm3"),("specific_heat","kJ/(kg*K)")])
def test_non_si_units_require_explicit_conversion(field,bad):
    with pytest.raises(ValueError,match="SI units"):
        server_exhaust_temperature(293.15,900.,.151,units=replace(SI_UNITS,**{field:bad}))


@pytest.mark.parametrize("bad",[True,"900",complex(900,0),np.array([],dtype=float)])
def test_invalid_numeric_values_are_not_silently_cast(bad):
    with pytest.raises(ValueError):
        server_exhaust_temperature(293.15,bad,.151)


def test_capacity_and_output_overflow_or_unphysical_cooling_are_rejected():
    with pytest.raises(ValueError,match="heat_capacity_rate"):
        server_exhaust_temperature(293.15,900.,.151,rho_kg_m3=1e300,cp_J_kgK=1e300)
    with pytest.raises(ValueError,match="T_out_K"):
        server_exhaust_temperature(293.15,1e300,1e-300)
    with pytest.raises(ValueError,match="positive"):
        server_exhaust_temperature(293.15,-1e9,.151)
    with pytest.raises(ValueError,match="finite"):
        sensible_heat_W(293.15,np.inf,.151)


def test_sensible_heat_rejects_2d_output_or_different_vector_lengths():
    with pytest.raises(ValueError,match="2D broadcasting"):
        sensible_heat_W(np.array([293.15,294.15]),np.ones((2,1)),.151)
    with pytest.raises(ValueError,match="same length"):
        sensible_heat_W(np.array([293.15,294.15]),np.array([296.15,297.15,298.15]),.151)


def test_torch_retains_dtype_device_and_all_physical_input_derivatives():
    initial=[np.array([293.15,294.15]),np.array([900.,1800.]),
             np.array([.151,.301]),np.array([1.19,1.20]),np.array([1005.,1006.])]
    tensors=[torch.tensor(v,dtype=torch.float64,requires_grad=True) for v in initial]
    tin,q,flow,rho,cp=tensors
    tout=server_exhaust_temperature(tin,q,flow,rho_kg_m3=rho,cp_J_kgK=cp)
    assert tout.dtype==torch.float64 and tout.device.type=="cpu"
    gradients=torch.autograd.grad(tout.sum(),tensors)
    steps=[1e-4,1e-3,1e-6,1e-5,1e-2]
    for i,(gradient,step) in enumerate(zip(gradients,steps)):
        fd=np.empty(2)
        for j in range(2):
            plus=[v.copy() for v in initial];minus=[v.copy() for v in initial]
            plus[i][j]+=step;minus[i][j]-=step
            evaluate=lambda a:server_exhaust_temperature(a[0],a[1],a[2],rho_kg_m3=a[3],cp_J_kgK=a[4]).sum()
            fd[j]=(evaluate(plus)-evaluate(minus))/(2*step)
        np.testing.assert_allclose(gradient.detach().numpy(),fd,rtol=2e-8,atol=2e-9)
    reconstructed=sensible_heat_W(tin,tout,flow,rho_kg_m3=rho,cp_J_kgK=cp)
    torch.testing.assert_close(reconstructed,q,rtol=0,atol=1e-10)


def test_torch_scalar_broadcast_keeps_gradient_and_does_not_coerce_mixed_dtype():
    q=torch.tensor([900.,1800.],dtype=torch.float64,requires_grad=True)
    flow=torch.tensor(.151,dtype=torch.float64,requires_grad=True)
    out=server_exhaust_temperature(293.15,q,flow)
    out.sum().backward()
    assert flow.grad.item()==pytest.approx(-2700./(1.19*1005*.151**2))
    with pytest.raises(ValueError,match="dtype"):
        server_exhaust_temperature(torch.tensor(293.15,dtype=torch.float32),q,flow)
    with pytest.raises(ValueError,match="floating"):
        server_exhaust_temperature(293.15,torch.tensor([900,1800]),.151)


@pytest.mark.parametrize("bad",[torch.tensor([[900.],[1800.]],dtype=torch.float64),
                               torch.tensor([float("nan")],dtype=torch.float64)])
def test_torch_shape_and_finiteness_are_checked_without_numpy_detach(bad):
    with pytest.raises(ValueError):
        server_exhaust_temperature(293.15,bad,.151)


def test_uid_batch_preserves_exact_order_and_autograd():
    order=("rack-b/device-2","rack-a/device-1")
    q=torch.tensor([1800.,900.],dtype=torch.float64,requires_grad=True)
    flow=torch.tensor([.301,.151],dtype=torch.float64,requires_grad=True)
    out=server_exhaust_temperature_by_uid(order,293.15,UIDField(order,q),UIDField(order,flow))
    assert out.order==order
    out.values.sum().backward()
    torch.testing.assert_close(q.grad,1./(1.19*1005*flow),rtol=1e-14,atol=0)
    with pytest.raises(ValueError,match="UID order"):
        server_exhaust_temperature_by_uid(order,293.15,UIDField(order[::-1],q),UIDField(order,flow))
    with pytest.raises(ValueError,match="explicit UIDField"):
        server_exhaust_temperature_by_uid(order,293.15,q,UIDField(order,flow))
    with pytest.raises(ValueError,match="shared scalar or explicit UIDField"):
        server_exhaust_temperature_by_uid(order,np.array([293.15,294.15]),UIDField(order,q),UIDField(order,flow))


@pytest.mark.parametrize("order,values",[
    (["a","b"],[1.,2.]),(("a","a"),[1.,2.]),((),[]),
    ((" a",),[1.]),(("a","b"),[1.]),(("a","b"),[[1.],[2.]]),
])
def test_invalid_uid_identity_or_shape_fails(order,values):
    with pytest.raises(ValueError):
        UIDField(order,values)


def test_uid_fields_are_revalidated_after_external_mutation():
    order=("a","b");q=np.array([900.,1800.]);field=UIDField(order,q)
    q[0]=np.nan
    with pytest.raises(ValueError,match="finite"):
        server_exhaust_temperature_by_uid(order,293.15,field,UIDField(order,[.151,.301]))
