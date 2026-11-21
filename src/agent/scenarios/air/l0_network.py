"""Conservative differentiable pressure/enthalpy network for air-v1 L0.

Independent lumped model, not CFD/EnergyPlus and not an HF residual interface.
Pressure-flow and nodal conservation follow the airflow-network framework:
https://bigladdersoftware.com/epx/docs/24-2/engineering-reference/airflownetwork-model.html
Effective passage areas/discharge coefficients require declared geometry and
HF calibration. Directed port links preserve imposed machine flow orientation;
room links are bidirectional. Supply temperature is post-fan. Fan heat belongs
in the shared coil/plant energy model, never a second room heat source.
"""
from __future__ import annotations
from dataclasses import dataclass
import math,time
import torch
from torch.nn import functional as fn

VERSION="air-pressure-enthalpy-network/v1"
@dataclass(frozen=True)
class AirNetwork:
    node_names:tuple[str,...]
    height_m:tuple[float,...]
    passive_pairs:tuple[tuple[int,int],...]
    directed:tuple[bool,...]
    server_pairs:tuple[tuple[int,int],...]
    server_flow_m3s:tuple[float,...]
    server_power_kw:tuple[float,...]
    unit_pairs:tuple[tuple[int,int],...] # return -> post-fan supply
    unit_rated_flow_m3s:tuple[float,...]
    rho:float=1.19
    cp:float=1005.
    discharge_coefficient:float=.62
    pressure_smoothing_pa:float=.01
    thermal_flow_smoothing_m3s:float=1e-8
    buoyancy_beta_per_k:float=.00333
    gravity_m_s2:float=9.81
    reference_c:float=22.
    temperature_scale_k:float=20.
    def __post_init__(self):
        n=len(self.node_names)
        if n<3 or len(set(self.node_names))!=n or len(self.height_m)!=n:raise ValueError("named node/height contract")
        if len(self.passive_pairs)!=len(self.directed):raise ValueError("passive link direction contract")
        if len(self.server_pairs)!=len(self.server_flow_m3s) or len(self.server_pairs)!=len(self.server_power_kw):raise ValueError("fixed server inventory")
        if len(self.unit_pairs)!=len(self.unit_rated_flow_m3s) or not self.unit_pairs:raise ValueError("fixed ACU inventory")
        for pair in self.passive_pairs+self.server_pairs+self.unit_pairs:
            if len(pair)!=2 or pair[0]==pair[1] or min(pair)<0 or max(pair)>=n:raise ValueError("valid distinct link endpoints")
        for values in (self.server_flow_m3s,self.server_power_kw,self.unit_rated_flow_m3s):
            if any(not math.isfinite(x) or x<=0 for x in values):raise ValueError("strict positive inventory values")
        for value in (self.rho,self.cp,self.discharge_coefficient,self.pressure_smoothing_pa,self.thermal_flow_smoothing_m3s,self.temperature_scale_k):
            if not math.isfinite(value) or value<=0:raise ValueError("positive physical/smoothing constants")
        if len(set(b for a,b in self.unit_pairs))!=len(self.unit_pairs):raise ValueError("one supplied temperature per distinct unit supply node")
        adjacency=[set() for _ in range(n)]
        for a,b in self.passive_pairs+self.server_pairs+self.unit_pairs:adjacency[a].add(b);adjacency[b].add(a)
        reached={0};pending=[0]
        while pending:
            for j in adjacency[pending.pop()]-reached:reached.add(j);pending.append(j)
        if len(reached)!=n:raise ValueError("one connected network with a single pressure gauge required")
    @property
    def state_size(self):return 2*len(self.node_names)-1-len(self.unit_pairs)
    def _operators(self,like):
        n=len(self.node_names);pairs=self.passive_pairs+self.server_pairs+self.unit_pairs
        a=torch.tensor([p[0] for p in pairs],dtype=torch.long,device=like.device)
        b=torch.tensor([p[1] for p in pairs],dtype=torch.long,device=like.device)
        B=like.new_zeros((n,len(pairs)));col=torch.arange(len(pairs),device=like.device)
        B[a,col]=1;B[b,col]=-1
        supply=[p[1] for p in self.unit_pairs]
        free=[i for i in range(n) if i not in supply]
        return a,b,B,torch.tensor(supply,device=like.device),torch.tensor(free,device=like.device)
    def fields(self,state,controls,areas):
        n=len(self.node_names);nu=len(self.unit_pairs);ne=len(self.passive_pairs)
        a,b,B,supply,free=self._operators(state)
        pressure=torch.cat((state.new_zeros(1),state[:n-1]))
        theta=state.new_zeros(n)
        theta=theta.index_copy(0,supply,(controls[:nu]-self.reference_c)/self.temperature_scale_k)
        theta=theta.index_copy(0,free,state[n-1:])
        heights=state.new_tensor(self.height_m)
        dp=pressure[a[:ne]]-pressure[b[:ne]]+self.rho*self.gravity_m_s2*self.buoyancy_beta_per_k*self.temperature_scale_k*.5*(theta[a[:ne]]+theta[b[:ne]])*(heights[b[:ne]]-heights[a[:ne]])
        coef=self.discharge_coefficient*areas*math.sqrt(2/self.rho)
        pe=self.pressure_smoothing_pa
        signed=coef*dp/(dp.square()+pe**2).pow(.25)
        forward=coef*(pe*fn.softplus(dp/pe)).sqrt()
        directed=torch.tensor(self.directed,device=state.device)
        passive=torch.where(directed,forward,signed)
        fixed=torch.cat((state.new_tensor(self.server_flow_m3s),controls[nu:]*state.new_tensor(self.unit_rated_flow_m3s)))
        q=torch.cat((passive,fixed))
        qplus=.5*(torch.sqrt(passive.square()+self.thermal_flow_smoothing_m3s**2)+passive)
        qminus=qplus-passive
        ef_passive=torch.where(directed,passive*theta[a[:ne]],qplus*theta[a[:ne]]-qminus*theta[b[:ne]])
        ef=torch.cat((ef_passive,fixed*theta[a[ne:]]))
        mass=B@q
        heat=state.new_zeros(n)
        outlet=torch.tensor([p[1] for p in self.server_pairs],device=state.device)
        heat=heat.index_add(0,outlet,state.new_tensor(self.server_power_kw)*1000/(self.rho*self.cp*self.temperature_scale_k))
        thermal=B@ef-heat
        return pressure,theta,passive,mass,thermal,free,supply
    def residual(self,state,controls,areas):
        _,_,_,mass,thermal,free,_=self.fields(state,controls,areas)
        return torch.cat((mass[1:],thermal[free]))
    def solve(self,controls,areas,*,tolerance=1e-10,max_iterations=60):
        if controls.ndim!=1 or len(controls)!=2*len(self.unit_pairs):raise ValueError("supply temperatures then flow fractions")
        if areas.ndim!=1 or len(areas)!=len(self.passive_pairs) or not torch.isfinite(areas).all() or (areas<=0).any():raise ValueError("positive effective passage areas")
        if controls.dtype!=torch.float64 or areas.dtype!=torch.float64 or controls.device.type!="cpu":raise ValueError("float64 CPU physical evaluator")
        if not torch.isfinite(controls).all() or (controls[len(self.unit_pairs):]<=0).any():raise ValueError("finite temperatures/positive machine fractions")
        begun=time.perf_counter();history=[]
        state=controls.new_zeros(self.state_size);state[len(self.node_names)-1:]=.5
        with torch.enable_grad():
            for iteration in range(max_iterations):
                r=self.residual(state,controls,areas);norm=float(r.detach().abs().max());history.append(norm)
                if norm<tolerance:break
                J=torch.autograd.functional.jacobian(lambda u:self.residual(u,controls,areas),state,vectorize=True)
                step=torch.linalg.solve(J,-r)
                scale=1.;accepted=False
                for trial in range(30):
                    candidate=(state+scale*step).detach()
                    nextnorm=float(self.residual(candidate,controls,areas).detach().abs().max())
                    if math.isfinite(nextnorm) and nextnorm<norm:
                        state=candidate;accepted=True;break
                    scale*=.5
                if not accepted:raise RuntimeError("network Newton line-search failed; retain physical failure")
            else:raise RuntimeError("network iteration limit; no derivative accepted")
        if not torch.isfinite(state).all() or history[-1]>=tolerance:raise RuntimeError("invalid converged network")
        return state.detach(),{"iterations":len(history)-1,"residual_inf":history[-1],"history":history,"wall_seconds":time.perf_counter()-begun}
    def differentiable_state(self,controls,areas):
        return _ImplicitNetwork.apply(controls,areas,self)
    def temperatures(self,state,controls,areas):
        p,t,q,m,e,free,supply=self.fields(state,controls,areas);T=self.reference_c+self.temperature_scale_k*t
        ids=lambda xs:torch.tensor(xs,dtype=torch.long,device=state.device)
        inlet=ids([a for a,b in self.server_pairs]);outlet=ids([b for a,b in self.server_pairs]);returns=ids([a for a,b in self.unit_pairs])
        return {"server_inlet_c":T[inlet],"server_exhaust_c":T[outlet],"unit_return_c":T[returns],"unit_supply_c":T[supply],"pressure_pa":p,"passive_flow_m3s":q,
                "mass_residual_m3s":m,"thermal_residual_kw":e*self.rho*self.cp*self.temperature_scale_k/1000,
                "air_coil_removed_kw":-e[supply]*self.rho*self.cp*self.temperature_scale_k/1000,
                "field_I2_available":False,"calibration_status":"unfitted lumped passage model"}
class _ImplicitNetwork(torch.autograd.Function):
    @staticmethod
    def forward(ctx,controls,areas,network):
        state,receipt=network.solve(controls.detach(),areas.detach())
        ctx.network=network;ctx.save_for_backward(state,controls,areas)
        return state
    @staticmethod
    def backward(ctx,state_gradient):
        state,controls,areas=ctx.saved_tensors;net=ctx.network
        with torch.enable_grad():
            u=state.detach().requires_grad_(True);c=controls.detach().requires_grad_(True);a=areas.detach().requires_grad_(True)
            J=torch.autograd.functional.jacobian(lambda x:net.residual(x,c,a),u,vectorize=True)
            adjoint=torch.linalg.solve(J.T,state_gradient)
            residual=net.residual(u,c,a)
            dc,da=torch.autograd.grad(residual,(c,a),grad_outputs=-adjoint)
        return dc,da,None
