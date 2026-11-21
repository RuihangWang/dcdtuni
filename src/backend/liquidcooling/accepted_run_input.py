"""Export only the accepted DAE input prefix as a self-contained v3/v4 manifest.

This is a derived run artifact, not a replacement .cfdproj or checkpoint. Live
heat commands are already accepted by the caller; pending inputs are absent
from this API. Plan events are materialized in the compiled graph, then copied
into edge-level schedules. Original equipment profiles remain intact as source
records, with explicit edge overrides taking precedence during replay.
"""
from __future__ import annotations

from copy import deepcopy
import math
from numbers import Real

from backend.liquidcooling.liquidrun_dae_schema import LoadedDAEInput
from backend.liquidcooling.parameters import resolve_parameter_pack


def build_accepted_manifest(definition:LoadedDAEInput,heat_multipliers:list[float],*,
                            pump_rpm_ratios:list[dict[str,float]]|None=None,
                            valve_openings:list[dict[str,float]]|None=None,
                            primary_inlet_K:list[dict[str,float]]|None=None) -> dict:
    """Build a validated accepted prefix without modifying loaded inputs.

    The first accepted frame is initialized at multiplier 1. A prefix includes
    that frame and cannot exceed the original N_t. Cold-plate scheduled heat
    is multiplied. Optional per-sample edge-ID actuator traces replace the
    compiled pump/valve schedules precisely; an omitted trace preserves the
    original scheduled controls. Pending commands are never included.

    Tensor-to-JSON transfer is intentional at this artifact boundary; it is
    outside the differentiable residual and does not execute a numerical solve.
    """
    if not isinstance(definition,LoadedDAEInput):
        raise TypeError('definition must be a LoadedDAEInput')
    if not isinstance(heat_multipliers,list):
        raise TypeError('heat_multipliers must be a list of accepted sample values')
    count=len(heat_multipliers)
    if not 1<=count<=definition.sample_count:
        raise ValueError('accepted prefix sample count must lie between 1 and original N_t')
    scales=[]
    for value in heat_multipliers:
        if isinstance(value,bool) or not isinstance(value,Real):
            raise ValueError('heat_multipliers must contain finite nonnegative real values')
        try:
            scalar=float(value)
        except (OverflowError,ValueError) as error:
            raise ValueError('heat_multipliers must contain finite nonnegative values') from error
        if not math.isfinite(scalar) or scalar<0:
            raise ValueError('heat_multipliers must contain finite nonnegative values')
        scales.append(scalar)
    if scales[0]!=1.0:
        raise ValueError('the first accepted heat multiplier must equal 1')

    output=definition.manifest.model_dump(by_alias=True,mode='json',exclude_none=True)
    resolved=resolve_parameter_pack(output)
    original_edges={edge['id']:edge for edge in resolved['edges']}
    graph=definition.equation_graph.graph
    edge_outputs={edge['id']:edge for edge in output['edges']}

    def checked_trace(name,trace,group,*,maximum=None,positive=False):
        if trace is None:return None
        indices=getattr(graph,group+'_indices')
        ids=[] if indices is None else [graph.edge_str_ids[i] for i in indices.edge_ids.tolist()]
        if not isinstance(trace,list) or len(trace)!=count:
            raise ValueError(f'{name} must have exactly one edge-ID map per accepted sample')
        checked=[]
        for sample in trace:
            if not isinstance(sample,dict) or set(sample)!=set(ids):
                raise ValueError(f'{name} must include every compiled {group} edge ID exactly once')
            values={}
            for edge_id,value in sample.items():
                if isinstance(value,bool) or not isinstance(value,Real):
                    raise ValueError(f'{name}.{edge_id} must be finite and nonnegative')
                try:scalar=float(value)
                except (OverflowError,TypeError,ValueError) as error:
                    raise ValueError(f'{name}.{edge_id} must be finite and nonnegative') from error
                if not math.isfinite(scalar) or scalar<0 or (positive and scalar<=0) or (maximum is not None and scalar>maximum):
                    raise ValueError(f'{name}.{edge_id} lies outside its allowed range')
                values[edge_id]=scalar
            checked.append(values)
        return checked

    pumps=checked_trace('pump_rpm_ratios',pump_rpm_ratios,'pump')
    valves=checked_trace('valve_openings',valve_openings,'valve',maximum=1.)
    inlets=checked_trace('primary_inlet_K',primary_inlet_K,'hx',positive=True)

    def held(mode_name):
        """Edge IDs whose v4 form replaces this field (flow-held pumps have no
        rpm_ratio, temperature-rise plates no Q_W)."""
        mask=getattr(graph,mode_name,None)
        group={'pump_flow_mode':'pump','cp_rise_mode':'cp'}[mode_name]
        indices=getattr(graph,group+'_indices')
        if mask is None or indices is None:
            return set()
        return {graph.edge_str_ids[i] for i,flag in zip(indices.edge_ids.tolist(),mask.tolist()) if flag}

    def materialize(group:str,field:str,multipliers=None,accepted=None,skip=frozenset(),source=None):
        indices=getattr(graph,group+'_indices')
        params=getattr(graph,group+'_params')
        if indices is None:
            return
        schedule=getattr(params,field) if source is None else source
        if schedule.ndim==1:
            schedule=schedule[:,None].expand(-1,definition.sample_count)
        elif schedule.ndim==2 and schedule.shape[1]==1:
            schedule=schedule.expand(-1,definition.sample_count)
        if schedule.ndim!=2 or schedule.shape[1]<count or schedule.shape[0]!=indices.edge_ids.numel():
            raise ValueError(f'compiled {group}.{field} does not cover the accepted prefix')
        prefix=schedule[:,:count]
        if multipliers is not None:
            prefix=prefix*prefix.new_tensor(multipliers)[None,:]
        # One bulk read per component type, no mutation of the source tensor.
        rows=prefix.detach().cpu().tolist()
        for global_index,values in zip(indices.edge_ids.detach().cpu().tolist(),rows):
            edge_id=graph.edge_str_ids[global_index]
            if edge_id in skip:
                continue
            if accepted is not None:
                values=[sample[edge_id] for sample in accepted]
            edge_outputs[edge_id]['params'][field]=values

    rising,flowing=held('cp_rise_mode'),held('pump_flow_mode')
    materialize('cp','Q_W',scales,skip=rising)
    materialize('pump','rpm_ratio',accepted=pumps,skip=flowing)
    materialize('valve','opening',accepted=valves)
    if graph.cp_dT_set is not None:
        # The accepted heat multiplier scaled the rise (LoadedDAEInput.parameters).
        materialize('cp','dT_set_K',scales,skip=set(edge_outputs)-rising,source=graph.cp_dT_set)
    for edge in output['edges']:
        for field in ('flow_set_kg_s','T_set_K'):
            if isinstance(edge['params'].get(field),list):
                edge['params'][field]=edge['params'][field][:count]
    for node in output['nodes']:
        bc=node.get('bc')
        if bc and isinstance(bc.get('P_Pa'),list):
            bc['P_Pa']=bc['P_Pa'][:count]
    for edge in output['edges']:
        if edge['type']!='pipe':
            continue
        source=original_edges[edge['id']]['params'].get('heat_source_W_per_m')
        if isinstance(source,list):
            # [n_cells] is spatial constants; [n_cells][N_t] is time-dependent.
            # Mixed constant/time-dependent cells are supported by the compiler.
            edge['params']['heat_source_W_per_m']=[
                deepcopy(value[:count]) if isinstance(value,list) else value
                for value in source
            ]
    for circuit in output['primary_circuits']:
        for field in ('flow_kg_s','inlet_K'):
            if isinstance(circuit[field],list):
                circuit[field]=circuit[field][:count]
        if inlets is not None:
            # The accepted supply-temperature trace replaces the planned
            # schedule wholesale (same semantics as pump/valve traces above);
            # circuits align to HX edges by edge_id, every sample must carry
            # this circuit's edge (checked_trace enforced set equality).
            edge_id=circuit.get('edge_id',circuit.get('edgeId'))
            if edge_id is None:
                raise ValueError('primary_circuits entry lacks an edge id for the accepted inlet trace')
            circuit['inlet_K']=[sample[edge_id] for sample in inlets]
    benchmark=output.get('manufactured_benchmark')
    if benchmark is not None:
        # This derived artifact covers accepted samples only. Preserve the
        # supplied source values and their layout; do not regenerate forcing.
        from backend.liquidcooling.manufactured_benchmark import canonical_digest
        benchmark['times_s']=benchmark['times_s'][:count]
        for field in benchmark['sources'].values():
            field['values']=[row[:count] for row in field['values']]
        benchmark.pop('source_sha256')
        benchmark['source_sha256']=canonical_digest(benchmark)
    output['time']['N_t']=count
    output['events']=[]
    if not output['source'].endswith(':accepted-trace'):
        output['source']+=':accepted-trace'

    # The public v3 checker ensures no invalid prefix, stale event, unknown
    # field, malformed profile override or non-finite product is exported.
    checked=type(definition.manifest).model_validate(output)
    return checked.model_dump(by_alias=True,mode='json',exclude_none=True)
