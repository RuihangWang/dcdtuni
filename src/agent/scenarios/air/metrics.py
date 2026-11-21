"""Reported-only air metrics with explicit thermal/airflow normalization.

RCI uses equally sampled inlet-face means and is not clipped at zero.
RTI uses airflow-weighted AHU/IT temperature differences under energy closure.
SHI is the original common-reference index, generalized with explicit rack
flow weights; for differing ACU setpoints the caller must declare its reference.
RHI inferred as 1-SHI assumes closure; rhi_from_returns measures it separately.
Sources: Herrlin ASHRAE 2005/2010 and Sharma/Bash/Patel AIAA-2002-3091.
"""
from __future__ import annotations
import math
import numpy as np


def _vector(x,name):
    if type(x).__module__.split('.')[0]=='torch':x=x.detach().cpu().numpy()
    a=np.asarray(x,dtype=float)
    if a.ndim!=1 or a.size==0 or not np.isfinite(a).all():raise ValueError(f'{name} needs a nonempty finite vector')
    return a


def _positive(x,name):
    if not math.isfinite(float(x)) or float(x)<=0:raise ValueError(f'{name} must be finite and positive')


def flow_weighted_mean(temperatures_c,flows):
    t=_vector(temperatures_c,'temperature');f=_vector(flows,'positive flow weights')
    if t.shape!=f.shape or np.any(f<0) or f.sum()<=0:raise ValueError('matching nonnegative flows with positive total are required')
    return float(np.dot(t,f)/f.sum())


def rci_hi(t_in,t_max_rec,t_max_all):
    t=_vector(t_in,'server inlet temperatures')
    _positive(t_max_all-t_max_rec,'allowable minus recommended upper temperature')
    return float(100*(1-np.maximum(t-t_max_rec,0).sum()/((t_max_all-t_max_rec)*t.size)))


def rci_lo(t_in,t_min_rec,t_min_all):
    t=_vector(t_in,'server inlet temperatures')
    _positive(t_min_rec-t_min_all,'recommended minus allowable lower temperature')
    return float(100*(1-np.maximum(t_min_rec-t,0).sum()/((t_min_rec-t_min_all)*t.size)))


def rti_flow(it_flow_m3s,acu_flow_m3s):
    _positive(it_flow_m3s,'total IT flow');_positive(acu_flow_m3s,'total ACU flow')
    return float(100*it_flow_m3s/acu_flow_m3s)


def rti_temperature(t_return_mix_c,t_supply_mix_c,dt_it_k):
    _positive(dt_it_k,'flow-weighted IT temperature rise')
    if not math.isfinite(t_return_mix_c) or not math.isfinite(t_supply_mix_c):raise ValueError('mixed temperatures must be finite')
    return float(100*(t_return_mix_c-t_supply_mix_c)/dt_it_k)


def rti_from_fields(t_in,t_ex,it_flows,t_supply,t_return,acu_flows):
    tin=_vector(t_in,'inlet');tex=_vector(t_ex,'exhaust');vf=_vector(it_flows,'IT flows')
    if tin.shape!=tex.shape or tin.shape!=vf.shape:raise ValueError('one inlet/exhaust/flow per server')
    supply=flow_weighted_mean(t_supply,acu_flows);ret=flow_weighted_mean(t_return,acu_flows)
    rise=flow_weighted_mean(tex-tin,vf)
    rtif=rti_flow(vf.sum(),_vector(acu_flows,'ACU flows').sum())
    rtit=rti_temperature(ret,supply,rise)
    return dict(rti_flow_percent=rtif,rti_temperature_percent=rtit,
                closure_difference_percentage_points=rtit-rtif,
                t_supply_mix_c=supply,t_return_mix_c=ret,dt_it_flow_weighted_k=rise)


def _rack_terms(t_in,t_ex,t_supply_c,server_flows=None):
    tin=_vector(t_in,'inlet');tex=_vector(t_ex,'exhaust')
    if tin.shape!=tex.shape:raise ValueError('matching server inlet/exhaust vectors are required')
    if np.any(tex<tin):raise ValueError('server exhaust must not be colder than inlet for this heated-IT metric')
    ref=np.asarray(t_supply_c,dtype=float)
    if not np.isfinite(ref).all() or (ref.ndim!=0 and ref.shape!=tin.shape):
        raise ValueError('supply reference must be one declared scalar or one per server')
    weights=np.ones_like(tin) if server_flows is None else _vector(server_flows,'server flows')
    if weights.shape!=tin.shape or np.any(weights<=0):raise ValueError('positive matching server flow weights are required')
    den=float(np.dot(weights,tex-ref));_positive(den,'exhaust enthalpy relative to supply')
    return tin,tex,ref,weights,den


def shi(t_in_rack,t_ex_rack,t_supply_c,server_flows=None):
    tin,tex,ref,w,den=_rack_terms(t_in_rack,t_ex_rack,t_supply_c,server_flows)
    return float(np.dot(w,tin-ref)/den)


def rhi(t_in_rack,t_ex_rack,t_supply_c,server_flows=None):
    """Inferred RHI complement under room heat closure, not an independent check."""
    return 1.0-shi(t_in_rack,t_ex_rack,t_supply_c,server_flows)


def rhi_from_returns(t_ex_rack,server_flows,t_acu_return_c,acu_flows,t_supply_ref_c):
    tex=_vector(t_ex_rack,'exhaust');rf=_vector(server_flows,'server flows')
    tra=_vector(t_acu_return_c,'ACU return');af=_vector(acu_flows,'ACU flows')
    if tex.shape!=rf.shape or tra.shape!=af.shape or np.any(rf<=0) or np.any(af<=0):raise ValueError('positive matching rack/ACU flows are required')
    if not np.isscalar(t_supply_ref_c) or not math.isfinite(float(t_supply_ref_c)):
        raise ValueError('measured RHI needs one explicitly declared common supply reference')
    den=float(np.dot(rf,tex-t_supply_ref_c));_positive(den,'rack exhaust enthalpy')
    return float(np.dot(af,tra-t_supply_ref_c)/den)
