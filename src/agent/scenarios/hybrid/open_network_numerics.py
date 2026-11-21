"""General full-network initialization: variable active ACUs, no sealed-roof leaf."""
import time
import torch
from .network_numerics import stable_residual,_newton
VERSION="open-zonal-full-state-numerics/v2-mass-consistent-initialization"
def pressure_consistent_state(net,state,controls,areas):
    n=len(net.node_names)
    def mass(pressure):
        return stable_residual(net,torch.cat((pressure,state[n-1:])),controls,areas)[:n-1]
    pressure,history=_newton(mass,state[:n-1],tolerance=1e-10)
    result=torch.cat((pressure,state[n-1:]))
    if float(stable_residual(net,result,controls,areas)[:n-1].abs().max())>1e-10:
        raise RuntimeError("post-enthalpy pressure closure failed")
    return result,history
def solve_state(net,controls,areas):
    n=len(net.node_names);nu=len(net.unit_pairs)
    if controls.shape!=(2*nu,) or (controls[nu:]<=0).any():
        raise ValueError("only positive active branch controls enter the internal equations")
    start=time.perf_counter();state=controls.new_zeros(net.state_size)
    seed=(controls[:nu]@controls[nu:]/controls[nu:].sum()-net.reference_c)/net.temperature_scale_k
    state[n-1:]=seed;h=state.new_tensor(net.height_m)
    state[:n-1]=net.rho*net.gravity_m_s2*net.buoyancy_beta_per_k*net.temperature_scale_k*seed*(h[1:]-h[0])
    def mass(pressure):
        u=torch.cat((pressure,state[n-1:]))
        return stable_residual(net,u,controls,areas)[:n-1]
    pressure,phistory=_newton(mass,state[:n-1],tolerance=1e-10)
    state=torch.cat((pressure,state[n-1:]))
    _,theta,passive,_,_,free,supply=net.fields(state,controls,areas)
    a,b,B,_,_=net._operators(state);ne=len(net.passive_pairs)
    fixed=torch.cat((state.new_tensor(net.server_flow_m3s),controls[nu:]*state.new_tensor(net.unit_rated_flow_m3s)))
    q=torch.cat((passive,fixed))
    qp=.5*(torch.sqrt(passive.square()+net.thermal_flow_smoothing_m3s**2)+passive);qm=qp-passive
    E=state.new_zeros((len(q),n));E[torch.arange(len(q)),a]=q
    for i in range(ne):
        if not net.directed[i]:E[i,a[i]]=qp[i];E[i,b[i]]=-qm[i]
    K=B@E;boundary=state.new_zeros(n).index_copy(0,supply,(controls[:nu]-net.reference_c)/net.temperature_scale_k)
    heat=state.new_zeros(n).index_add(0,torch.tensor([j for _,j in net.server_pairs]),state.new_tensor(net.server_power_kw)*1000/(net.rho*net.cp*net.temperature_scale_k))
    thermal=torch.linalg.solve(K[free][:,free],heat[free]-(K@boundary)[free])
    state=torch.cat((pressure,thermal))
    # Enthalpy initialization changes buoyancy. Re-close pressure at that
    # temperature before taking the coupled step; otherwise a no-source upper
    # node can temporarily have only incoming flows and a singular (p,T) block.
    state,post_thermal_pressure_history=pressure_consistent_state(net,state,controls,areas)
    state,history=_newton(lambda u:stable_residual(net,u,controls,areas),state,tolerance=1e-10)
    original=net.residual(state,controls,areas)
    mismatch=stable_residual(net,state,controls,areas)-original
    if float(original.abs().max())>1e-10 or float(mismatch.abs().max())>1e-11:
        raise RuntimeError("full original OPEN operator did not meet unchanged residual gate")
    return state,{"engine":VERSION,"pressure_history":phistory,"coupled_history":history,"post_enthalpy_pressure_history":post_thermal_pressure_history,
        "original_full_residual_inf":float(original.abs().max()),
        "stable_vs_original_max_error":float(mismatch.abs().max()),"wall_seconds":time.perf_counter()-start}
class ImplicitState(torch.autograd.Function):
    @staticmethod
    def forward(ctx,controls,areas,net):
        state,_=solve_state(net,controls.detach(),areas.detach())
        ctx.net=net;ctx.save_for_backward(state,controls,areas);return state
    @staticmethod
    def backward(ctx,goal):
        state,c,a=ctx.saved_tensors;net=ctx.net
        with torch.enable_grad():
            u=state.detach().requires_grad_(True);c=c.detach().requires_grad_(True);a=a.detach().requires_grad_(True)
            J=torch.autograd.functional.jacobian(lambda v:stable_residual(net,v,c,a),u,vectorize=True)
            adj=torch.linalg.solve(J.T,goal)
            dc,da=torch.autograd.grad(stable_residual(net,u,c,a),(c,a),grad_outputs=-adj)
        return dc,da,None
