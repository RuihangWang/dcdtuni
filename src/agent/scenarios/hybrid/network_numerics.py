"""Same zonal operator, exact zero-throughflow leaf elimination and robust startup.

The legacy residual remains the acceptance test. Negative-tail sqrt(softplus)
is evaluated through its log to avoid 0*infinity AD; the asymptotic branch at
-35 differs from exact arithmetic by less than double precision. This is a
numerical engine, not a different heat source/flow or new physical conductivity.
"""
from __future__ import annotations
import hashlib,time,math
from pathlib import Path
import torch
from torch.nn import functional as fn
VERSION="zonal-same-residual-leaf-elimination/v2"
SOURCE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def stable_residual(net,u,controls,areas):
    n=len(net.node_names);ne=len(net.passive_pairs);nu=len(net.unit_pairs)
    a,b,B,supply,free=net._operators(u);p=torch.cat((u.new_zeros(1),u[:n-1]));theta=u.new_zeros(n)
    theta=theta.index_copy(0,supply,(controls[:nu]-net.reference_c)/net.temperature_scale_k).index_copy(0,free,u[n-1:]);h=u.new_tensor(net.height_m)
    dp=p[a[:ne]]-p[b[:ne]]+net.rho*net.gravity_m_s2*net.buoyancy_beta_per_k*net.temperature_scale_k*.5*(theta[a[:ne]]+theta[b[:ne]])*(h[b[:ne]]-h[a[:ne]])
    coefficient=net.discharge_coefficient*areas*math.sqrt(2/net.rho);pe=net.pressure_smoothing_pa;x=dp/pe
    safe=x.clamp_min(-35.);logsoft=torch.where(x<-35.,x,fn.softplus(safe).log())
    forward=coefficient*math.sqrt(pe)*(.5*logsoft).exp();signed=coefficient*dp/(dp.square()+pe**2).pow(.25)
    directed=torch.tensor(net.directed);passive=torch.where(directed,forward,signed)
    fixed=torch.cat((u.new_tensor(net.server_flow_m3s),controls[nu:]*u.new_tensor(net.unit_rated_flow_m3s)));q=torch.cat((passive,fixed))
    qp=.5*(torch.sqrt(passive.square()+net.thermal_flow_smoothing_m3s**2)+passive);qm=qp-passive
    ep=torch.where(directed,passive*theta[a[:ne]],qp*theta[a[:ne]]-qm*theta[b[:ne]])
    e=torch.cat((ep,fixed*theta[a[ne:]]));heat=u.new_zeros(n).index_add(0,torch.tensor([b for a,b in net.server_pairs]),u.new_tensor(net.server_power_kw)*1000/(net.rho*net.cp*net.temperature_scale_k))
    return torch.cat(((B@q)[1:],(B@e-heat)[free]))

class Reduction:
    def __init__(self,net):
        n=len(net.node_names);self.net=net;self.n=n;self.leaf=4;self.neighbor=3
        touches=[i for i,p in enumerate(net.passive_pairs) if self.leaf in p]
        if len(touches)!=1 or net.directed[touches[0]] or net.passive_pairs[touches[0]]!=(3,4):raise ValueError("exact source undirected no-source leaf prerequisite")
        if any(self.leaf in p for p in net.server_pairs+net.unit_pairs):raise ValueError("leaf has a forced machine flow")
        _,_,_,supply,free=net._operators(torch.zeros(net.state_size,dtype=torch.float64));self.free=free;self.supply=supply
        self.tleaf=n-1+int((free==self.leaf).nonzero()[0]);self.tneighbor=n-1+int((free==self.neighbor).nonzero()[0]);self.pleaf=self.leaf-1;self.pneighbor=self.neighbor-1
        removed={self.pleaf,self.tleaf};self.kept=torch.tensor([i for i in range(net.state_size) if i not in removed]);self.prows=torch.tensor([i for i in range(n-1) if i!=self.pleaf]);self.pkept=torch.tensor([i for i in range(n-1) if i!=self.pleaf])
        self.factor=net.rho*net.gravity_m_s2*net.buoyancy_beta_per_k*net.temperature_scale_k*(net.height_m[self.leaf]-net.height_m[self.neighbor])
    def rebuild(self,y):
        u=y.new_zeros(self.net.state_size).index_copy(0,self.kept,y)
        return u.index_copy(0,torch.tensor([self.pleaf,self.tleaf]),torch.stack((u[self.pneighbor]+self.factor*u[self.tneighbor],u[self.tneighbor])))
    def residual(self,y,c,a):return stable_residual(self.net,self.rebuild(y),c,a)[self.kept]


def _newton(fun,x,*,tolerance,max_iterations=80):
    history=[]
    for i in range(max_iterations):
        r=fun(x);norm=float(r.detach().abs().max());history.append(norm)
        if norm<tolerance:return x.detach(),history
        J=torch.autograd.functional.jacobian(fun,x,vectorize=True)
        row=J.abs().amax(1).clamp_min(1e-16)
        step=torch.linalg.solve(J/row[:,None],-r/row)
        linear=float((J@step+r).detach().abs().max())
        if not torch.isfinite(step).all() or linear>max(1e-8,1e-7*norm):raise RuntimeError("scaled Newton actual linear solve inaccurate")
        scale=1.;accepted=False
        for j in range(50):
            candidate=(x+scale*step).detach();after=float(fun(candidate).detach().abs().max())
            if math.isfinite(after) and after<norm:x=candidate;accepted=True;break
            scale*=.5
        if not accepted:raise RuntimeError("same-residual Newton line search failed")
    raise RuntimeError("same-residual Newton maximum iterations")


def solve_state(net,controls,areas):
    start=time.perf_counter();red=Reduction(net);n=red.n;u=controls.new_zeros(net.state_size)
    seed=(controls[:4]@controls[4:]/controls[4:].sum()-net.reference_c)/net.temperature_scale_k
    u[n-1:]=seed;h=u.new_tensor(net.height_m);u[:n-1]=net.rho*net.gravity_m_s2*net.buoyancy_beta_per_k*net.temperature_scale_k*seed*(h[1:]-h[0]);y=u[red.kept]
    # First pressure closure at a fixed real supply-temperature seed; no HF data.
    def pressure(p):
        v=u.index_copy(0,red.pkept,p);v=red.rebuild(v[red.kept]);return stable_residual(net,v,controls,areas)[red.prows]
    p,pressure_history=_newton(pressure,u[red.pkept],tolerance=1e-10)
    u=u.index_copy(0,red.pkept,p);u=red.rebuild(u[red.kept])
    # Linear enthalpy initial solve at those fixed flows (thermal buoyancy is
    # restored in the following coupled Newton, never removed from the operator).
    _,theta,passive,mass,thermal,free,supply=net.fields(u,controls,areas);a,b,B,_,_=net._operators(u);ne=len(net.passive_pairs)
    fixed=torch.cat((u.new_tensor(net.server_flow_m3s),controls[4:]*u.new_tensor(net.unit_rated_flow_m3s)));q=torch.cat((passive,fixed));qp=.5*(torch.sqrt(passive.square()+net.thermal_flow_smoothing_m3s**2)+passive);qm=qp-passive
    E=u.new_zeros((len(q),n));edges=torch.arange(len(q));E[edges,a]=q
    for i in range(ne):
        if not net.directed[i]:E[i,a[i]]=qp[i];E[i,b[i]]=-qm[i]
    K=B@E;unknown=[int(k) for k in free if int(k)!=red.leaf];mapping=u.new_zeros((n,len(unknown)))
    for j,k in enumerate(unknown):mapping[k,j]=1.
    mapping[red.leaf,unknown.index(red.neighbor)]=1.
    boundary=u.new_zeros(n).index_copy(0,supply,(controls[:4]-net.reference_c)/net.temperature_scale_k)
    heat=u.new_zeros(n).index_add(0,torch.tensor([b for a,b in net.server_pairs]),u.new_tensor(net.server_power_kw)*1000/(net.rho*net.cp*net.temperature_scale_k))
    rows=torch.tensor(unknown);t=torch.linalg.solve((K@mapping)[rows],heat[rows]-(K@boundary)[rows]);theta=boundary+mapping@t
    u=u.index_copy(0,torch.arange(n-1,net.state_size),theta[free]);u=red.rebuild(u[red.kept])
    y,history=_newton(lambda y:red.residual(y,controls,areas),u[red.kept],tolerance=1e-10)
    full=red.rebuild(y);original=net.residual(full,controls,areas);difference=stable_residual(net,full,controls,areas)-original
    if float(original.abs().max())>1e-10 or float(difference.abs().max())>1e-11:raise RuntimeError("original full residual acceptance failed")
    return full.detach(),{"engine":VERSION,"exact_leaf_reconstruction":True,"pressure_history":pressure_history,"coupled_history":history,"original_full_residual_inf":float(original.abs().max()),"stable_vs_original_max_error":float(difference.abs().max()),"wall_seconds":time.perf_counter()-start}

class ImplicitState(torch.autograd.Function):
    @staticmethod
    def forward(ctx,controls,areas,net):
        u,_=solve_state(net,controls.detach(),areas.detach());ctx.net=net;ctx.save_for_backward(u,controls,areas);return u
    @staticmethod
    def backward(ctx,goal):
        u,c,a=ctx.saved_tensors;net=ctx.net;red=Reduction(net)
        with torch.enable_grad():
            y=u[red.kept].detach().requires_grad_(True);c=c.detach().requires_grad_(True);a=a.detach().requires_grad_(True)
            rebuilt=red.rebuild(y);rhs=torch.autograd.grad(rebuilt,y,grad_outputs=goal,retain_graph=True)[0]
            J=torch.autograd.functional.jacobian(lambda v:red.residual(v,c,a),y,vectorize=True);adj=torch.linalg.solve(J.T,rhs)
            residual=red.residual(y,c,a);dc,da=torch.autograd.grad(residual,(c,a),grad_outputs=-adj)
        return dc,da,None
