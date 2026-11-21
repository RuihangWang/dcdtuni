"""Hard constraints, bounded I0 scalarization, exact selection rank and I1/I3.

Recommended/allowable inlet envelopes use ASHRAE TC9.9 5th-edition reference
card tables 2.1/2.2. H1 is recommended 18--22C, allowable 15--25C. The 45C
exhaust/ACU-return defaults are task engineering assumptions, not ASHRAE limits.
Exact selection/reporting is lexicographic; a weighted real scalar reward is
explicitly scalarized, not falsely labeled strict lexicographic ordering.
"""
from __future__ import annotations
from dataclasses import dataclass,field
from typing import Mapping,Any
import math
import numpy as np
from .energy import array_namespace,as_like,vector,finite,_like,_positive,_nonnegative

ASHRAE_SOURCE='https://www.ashrae.org/file%20library/technical%20resources/bookstore/supplemental%20files/therm-gdlns-5th-r-e-refcard.pdf'

@dataclass(frozen=True)
class ThermalLimits:
    t_in_max_c: float=27.0
    t_in_min_c: float=18.0
    t_in_allow_max_c: float=32.0
    t_in_allow_min_c: float=15.0
    t_ex_max_c: float=45.0
    t_ra_max_c: float=45.0
    def __post_init__(self):
        for name,value in vars(self).items():
            if not math.isfinite(value):raise ValueError(f'{name} must be finite')
        if not self.t_in_allow_min_c<self.t_in_min_c<self.t_in_max_c<self.t_in_allow_max_c:
            raise ValueError('allowable inlet bounds must strictly enclose recommended bounds')

ASHRAE_CLASSES={'A1':ThermalLimits(),'H1':ThermalLimits(t_in_max_c=22.0,t_in_allow_max_c=25.0)}
BASE_KEYS=('inlet_hi_k','inlet_lo_k','exhaust_hi_k','return_hi_k','acu_capacity_frac')


def margins(t_in,t_ex,t_ra,acu_coil_kw,acu_cap_kw,lim=ThermalLimits(),*,energy_closure=None,closure_limit=0.01):
    xp=array_namespace(t_in,t_ex,t_ra,acu_coil_kw,acu_cap_kw)
    like=_like((t_in,t_ex,t_ra,acu_coil_kw,acu_cap_kw),xp)
    tin=vector(t_in,'server inlet temperatures',xp,like)
    tex=vector(t_ex,'server exhaust temperatures',xp,like)
    tra=vector(t_ra,'ACU return temperatures',xp,like)
    load=vector(acu_coil_kw,'ACU coil loads',xp,like)
    cap=vector(acu_cap_kw,'ACU coil capacities',xp,like)
    if tin.shape!=tex.shape or tra.shape!=load.shape or tra.shape!=cap.shape:
        raise ValueError('server pairs and ACU temperature/load/capacity vectors must match separately')
    if bool(xp.any(cap<=0)) or bool(xp.any(load<0)):
        raise ValueError('positive ACU capacities and nonnegative coil loads are required')
    m=dict(inlet_hi_k=tin-lim.t_in_max_c,inlet_lo_k=lim.t_in_min_c-tin,
           exhaust_hi_k=tex-lim.t_ex_max_c,return_hi_k=tra-lim.t_ra_max_c,
           acu_capacity_frac=load/cap-1.0)
    if energy_closure is not None:
        _nonnegative(closure_limit,'closure limit')
        closure=as_like(energy_closure,like,xp);finite(closure,'energy closure',xp)
        m['energy_closure_frac']=xp.abs(closure)-closure_limit
    return m


def violations(m:Mapping[str,Any],xp=None,smooth_beta=None):
    if not m:raise ValueError('constraint margins must not be empty')
    xp=xp or array_namespace(*m.values())
    if smooth_beta is not None:_positive(smooth_beta,'softplus beta')
    out={}
    like=_like(tuple(m.values()),xp)
    for k,v in m.items():
        v=as_like(v,like,xp);finite(v,k,xp)
        relu=xp.clip(v,0,None)
        out[k]=relu if smooth_beta is None else relu+xp.log1p(xp.exp(-smooth_beta*xp.abs(v)))/smooth_beta
    return out


def _validated_viol(viol,tol=0.0,require_complete=False):
    _nonnegative(tol,'feasibility tolerance')
    if not viol or (require_complete and not set(BASE_KEYS)<=set(viol)):
        raise ValueError('all declared thermal/capacity constraints are required')
    xp=array_namespace(*viol.values())
    like=_like(tuple(viol.values()),xp)
    for name,v in viol.items():
        v=as_like(v,like,xp)
        finite(v,name,xp)
        count = v.numel() if xp is not np else v.size
        if count == 0:
            raise ValueError('constraint arrays must not be empty')
        if bool(xp.any(v<0)):raise ValueError('violation values must be nonnegative')
    return xp


def is_feasible(viol,tol=0.0):
    xp=_validated_viol(viol,tol)
    like=_like(tuple(viol.values()),xp)
    return all(float(as_like(v,like,xp).max())<=tol for v in viol.values())


def exhaust_stat(t_ex,mode='mean',tau=1.0):
    xp=array_namespace(t_ex);like=_like((t_ex,),xp)
    t=vector(t_ex,'exhaust temperature',xp,like)
    if mode=='mean':return xp.sum(t)/t.shape[0]
    if mode=='max':return t.max()
    if mode=='softmax':
        _positive(tau,'exhaust softmax tau');m=t.max()
        return m+tau*xp.log(xp.sum(xp.exp((t-m)/tau)))
    raise ValueError('unknown exhaust statistic')


_DEFAULT_SCALES=(('inlet_hi_k',1.0),('inlet_lo_k',1.0),('exhaust_hi_k',1.0),
                 ('return_hi_k',1.0),('acu_capacity_frac',0.1),('energy_closure_frac',0.01))
@dataclass(frozen=True)
class ScoreWeights:
    pue_scale:float=0.1
    lam_exhaust:float=0.1
    t_ref_c:float=35.0
    t_scale_k:float=10.0
    infeasible_offset:float=100.0
    invalid_offset:float=200.0
    scales:tuple=field(default_factory=lambda:_DEFAULT_SCALES)
    penalty_mu:float=10.0
    def __post_init__(self):
        if isinstance(self.scales,Mapping):object.__setattr__(self,'scales',tuple(sorted(self.scales.items())))
        for n in ('pue_scale','t_scale_k','penalty_mu'):_positive(getattr(self,n),n)
        _nonnegative(self.lam_exhaust,'exhaust weight')
        if not all(math.isfinite(x) for x in (self.t_ref_c,self.infeasible_offset,self.invalid_offset)) or self.infeasible_offset<=1 or self.invalid_offset<=self.infeasible_offset:
            raise ValueError('finite reference and separated infeasible/invalid reward offsets are required')
        for k,v in self.scales:_positive(v,f'{k} violation scale')
        if len(dict(self.scales))!=len(self.scales):raise ValueError('duplicate violation scales')


def _penalty(viol,w,xp):
    scale=dict(w.scales);value=0.0
    like=_like(tuple(viol.values()),xp)
    for k,v in viol.items():
        if k not in scale:raise ValueError(f'no normalization scale for constraint {k}')
        value=value+xp.sum(as_like(v,like,xp))/scale[k]
    return value


def scalarized_loss(pue,t_ex,w=ScoreWeights(),exhaust_mode='mean'):
    xp=array_namespace(pue,t_ex);like=_like((pue,t_ex),xp)
    pu=as_like(pue,like,xp);finite(pu,'design PUE',xp)
    if pu.ndim!=0 or bool(pu<1):raise ValueError('design PUE must be a scalar >=1')
    return (pu-1.0)/w.pue_scale+w.lam_exhaust*(exhaust_stat(t_ex,exhaust_mode)-w.t_ref_c)/w.t_scale_k


def score(pue,t_ex,viol,w=ScoreWeights(),exhaust_mode='mean',tol=0.0,*,evaluation_valid=True):
    """I0: bounded weighted feasible reward; any feasible reward exceeds infeasible.

    Among feasible candidates this preserves the declared weighted trade-off.
    Use rank_key for exact PUE-first lexicographic selection, not this scalar.
    """
    if not evaluation_valid:return -w.invalid_offset
    xp=_validated_viol(viol,tol,require_complete=True)
    loss=float(scalarized_loss(pue,t_ex,w,exhaust_mode))
    if is_feasible(viol,tol):return -2.0/math.pi*math.atan(loss)
    return -w.infeasible_offset-float(_penalty(viol,w,xp))


def rank_key(pue,t_ex,viol,w=ScoreWeights(),exhaust_mode='mean',tol=0.0,*,evaluation_valid=True):
    """Exact selection tuple; compare with max(): validity, feasibility, PUE, exhaust."""
    if not evaluation_valid:return (0,0,-math.inf,-math.inf,-math.inf)
    xp=_validated_viol(viol,tol,require_complete=True)
    scalarized_loss(pue,t_ex,w,exhaust_mode)
    if is_feasible(viol,tol):return (1,1,0.0,-float(pue),-float(exhaust_stat(t_ex,exhaust_mode)))
    return (1,0,-float(_penalty(viol,w,xp)),-float(pue),-float(exhaust_stat(t_ex,exhaust_mode)))


def smooth_objective(pue,t_ex,smooth_viol,w=ScoreWeights(),exhaust_mode='mean'):
    """I3 proxy, distinct from hard bounded I0; use the same proxy in LAX terms."""
    xp=_validated_viol(smooth_viol,require_complete=True)
    return -scalarized_loss(pue,t_ex,w,exhaust_mode)-w.penalty_mu*_penalty(smooth_viol,w,xp)


def i1_vector(energy,viol,t_ex):
    _validated_viol(viol,require_complete=True)
    keys=('chiller_kw','chw_pump_kw','rejection_kw','fan_kw','other_kw')
    if not set(keys)<=set(energy):raise ValueError('I1 requires the declared power components')
    xp=array_namespace(*(energy[k] for k in keys))
    like=_like(tuple(energy[k] for k in keys),xp)
    for k in keys:
        term=as_like(energy[k],like,xp);finite(term,k,xp)
        if bool(xp.any(term<0)):raise ValueError('I1 power components must be nonnegative')
    out={f'power.{k}':energy[k] for k in keys}
    out.update({f'viol.{k}':v for k,v in viol.items()});out['exhaust_c']=t_ex
    return out
