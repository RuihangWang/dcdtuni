"""Strict liquidrun v3/v4: traceable inputs to the common Newton DAE equations.

v4 (cfdproj v5 timelines, transient design §3) adds four endpoint forms — a
held HX secondary supply temperature, a flow-held pump, a pressure-boundary
series and a cold-plate coolant temperature rise — every keyframed input as a
list[N_t], no events, and a `transient` provenance block.

V1/V2 parsing is unchanged. V3 does not accept Adam settings or the old
infinite-reservoir T_secondary_K field. Existing V2 field models and graph
reference checks are reused via an internal, resolved validation view; this
view is never the public manifest or a second project source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import json
from pathlib import Path
from typing import Annotated, Literal, TYPE_CHECKING

from pydantic import Field, model_validator

from backend.liquidcooling.liquidrun_schema import (
    StrictModel, Positive, Nonnegative, PositiveInt, Identifier, Vector3,
    SourceProject, Units, Execution, Fluid, Time, Node, EdgeBase, PartialParams,
    PipeEdge, PumpEdge, ValveEdge, ColdPlateEdge, Rack, Event, InitialConditions,
    ObjectBinding, Controller, LiquidrunV2, parse_liquidrun,
    Number, PumpParams, ColdPlateParams,
)
from backend.liquidcooling.parameters import ParameterPack, resolve_parameter_pack, _digest
from backend.liquidcooling.manufactured_benchmark import ManufacturedBenchmark, validate_contract, bind

if TYPE_CHECKING:
    from torch import Tensor
    from backend.liquidcooling.solver.equation_graph import EquationGraph
    from backend.liquidcooling.solver.dae_newton import NewtonOptions


HX_MODELS=('counterflow-ntu','mixed-volumes')


class TwoVolumeHXParams(PartialParams):
    dp_a: Nonnegative | None = None
    dp_b: Nonnegative | None = None
    UA_W_per_K: Positive | None = None
    # How UA is read (owner decision 2026-10-21). 'counterflow-ntu': duty
    # eps*C_min*(T_secondary_in - T_primary_in) with the counterflow
    # effectiveness, both fluid volumes keeping their storage (the steady
    # state is the counterflow epsilon-NTU exchanger). 'mixed-volumes': UA
    # between two perfectly mixed volumes. Absent = 'mixed-volumes', so a
    # saved manifest replays exactly as it ran; editors write the field.
    hxModel: Literal['counterflow-ntu','mixed-volumes'] | None = None


class TwoVolumeHXEdge(EdgeBase):
    type: Literal['heat_exchanger']
    params: TwoVolumeHXParams


DAEEdge = Annotated[PipeEdge | PumpEdge | ValveEdge | ColdPlateEdge | TwoVolumeHXEdge,
                    Field(discriminator='type')]


class DAEParameterPack(ParameterPack):
    @model_validator(mode='after')
    def no_infinite_reservoir(self):
        for profile_id,profile in self.profiles.items():
            if profile.type=='heat_exchanger' and 'T_secondary_K' in profile.params:
                raise ValueError(f'profile {profile_id}: T_secondary_K is not a two-volume DAE parameter; use primary_circuits')
        return self


class Analysis(StrictModel):
    mode: Literal['steady','transient']
    initialization: Literal['steady','consistent']

    @model_validator(mode='after')
    def steady_initialization(self):
        if self.mode=='steady' and self.initialization!='steady':
            raise ValueError('steady analysis requires steady initialization')
        return self


class DAEExecution(StrictModel):
    backend: Literal['pytorch']
    device: Annotated[str,Field(pattern=r'^(cpu|cuda(?::[0-9]+)?)$')]
    dtype: Literal['float32','float64']
    max_threads: Annotated[PositiveInt,Field(le=256)]


# The dense Newton/adjoint path builds the whole n x n Jacobian; above this
# many unknowns a network takes the sparse path (solver/dae_sparse.py).
DENSE_UNKNOWNS_LIMIT = 4000


class NewtonConfig(StrictModel):
    max_iterations: PositiveInt
    atol: Positive
    rtol: Nonnegative
    max_backtracks: PositiveInt
    # Omitted (every saved project): dense up to DENSE_UNKNOWNS_LIMIT
    # unknowns, sparse above. 'sparse' factors a coloured-JVP Jacobian (the
    # same factorisation solves the adjoint); gmres/pgmres/block are research
    # paths with their own receipts.
    linear_solver: Literal['dense','sparse','gmres','pgmres','block'] | None = None

    def to_options(self, unknowns: int | None = None):
        from backend.liquidcooling.solver.dae_newton import NewtonOptions
        values = self.model_dump(exclude_none=True)
        if 'linear_solver' not in values:
            large = unknowns is not None and unknowns > DENSE_UNKNOWNS_LIMIT
            values['linear_solver'] = 'sparse' if large else 'dense'
        return NewtonOptions(**values)


class PrimaryCircuitBinding(StrictModel):
    edge_id: Identifier
    flow_kg_s: Nonnegative | Annotated[list[Nonnegative],Field(min_length=1)]
    inlet_K: Positive | Annotated[list[Positive],Field(min_length=1)]
    cp_J_kgK: Positive
    rho_kg_m3: Positive
    primary_volume_m3: Positive
    secondary_volume_m3: Positive
    source: Literal['mock','vendor','calibrated']
    source_reference: Identifier

    def value_at(self,name:str,index:int) -> float:
        value=getattr(self,name)
        return value[index] if isinstance(value,list) else value


# Share the established, version-independent topology contract. Only the HX
# thermal law differs: the temporary T_secondary_K value exists because the
# legacy CompiledGraph compiler still requires its unused reservoir column.
_COMMON_GRAPH_FIELDS = (
    'format','source','projectName','source_project','units','execution','gravity',
    'fluid','time','nodes','edges','racks','events','initial_conditions',
    'controllers','object_bindings',
)


def _v2_graph_view(manifest: 'LiquidrunDAEV3') -> dict:
    raw=manifest.model_dump(by_alias=True,mode='json',exclude_none=True)
    resolved=resolve_parameter_pack(raw)
    view={key:deepcopy(resolved[key]) for key in _COMMON_GRAPH_FIELDS}
    view.update(version='2.0',schemaVersion=2)
    # Reuse only v2 graph validation. Its execution policy must not constrain
    # v3: the public manifest below owns the actual device/dtype/thread budget.
    view['execution']={'backend':'pytorch','device':'cpu','dtype':'float64','max_threads':1}
    bindings={entry.edge_id:entry for entry in manifest.primary_circuits}
    # v4 endpoint forms are not v2 fields: strip them, and fill the v2-required
    # value a v4 form replaces with an unused placeholder. The tensors are
    # attached to the compiled graph afterwards. A no-op on v3 input.
    for node in view['nodes']:
        bc=node.get('bc')
        if bc and isinstance(bc.get('P_Pa'),list):
            bc['P_Pa']=bc['P_Pa'][0]
    for edge in view['edges']:
        params=edge['params']
        if edge['type']=='pump':
            params.pop('mode',None)
            if params.pop('flow_set_kg_s',None) is not None:
                params.setdefault('rpm_ratio',1.)
        elif edge['type']=='cold_plate':
            params.pop('heat_mode',None)
            if params.pop('dT_set_K',None) is not None:
                params.setdefault('Q_W',0.)
        elif edge['type']=='heat_exchanger':
            params.pop('mode',None)
            params.pop('T_set_K',None)
            params.pop('hxModel',None)
            edge['params']['T_secondary_K']=bindings[edge['id']].value_at('inlet_K',0)
    return view


def _sample_schedule(value,count:int) -> list[float]:
    return list(value) if isinstance(value,list) else [value]*count


class LiquidrunDAEV3(StrictModel):
    format: Literal['liquidrun']
    schemaVersion: Literal[3]
    version: Literal['3.0']
    equation_system: Literal['incompressible_thermal_dae']
    source: Identifier
    projectName: Identifier
    source_project: SourceProject
    units: Units
    execution: DAEExecution
    gravity: Vector3
    fluid: Fluid
    time: Time
    nodes: Annotated[list[Node],Field(min_length=1)]
    edges: Annotated[list[DAEEdge],Field(min_length=1)]
    racks: list[Rack]
    events: list[Event]
    initial_conditions: InitialConditions
    object_bindings: dict[Identifier,ObjectBinding]
    parameter_pack: DAEParameterPack | None = None
    # PI loops solved inside the DAE (the integral is a differential state, the
    # actuator a function of the state); see _dae_controller_contract.
    controllers: list[Controller] = Field(default_factory=list)
    analysis: Analysis
    newton: NewtonConfig
    primary_circuits: list[PrimaryCircuitBinding]

    @model_validator(mode='after')
    def graph_and_primary_contract(self):
        return _graph_and_primary_contract(self)


def _dae_controller_contract(self,view):
    """What a DAE controller adds to the v2 contract (targets, actuator types,
    uniqueness, event conflicts and the sample grid are checked there).

    The DAE solves a continuous PI law with back-calculation anti-windup:
    u = clip(bias + kp*e + I, output_min, output_max),
    dI/dt = ki*e + (u - u_raw)/T_t, e = direction*(setpoint - measurement),
    integrated by the same backward-Euler step as the plant, so the loop has
    no sample lag. A derivative term, a slew limit and a slower sampling are
    not modelled and are refused rather than silently dropped.
    """
    edges={edge['id']:edge for edge in view['edges']}
    for control in self.controllers:
        pid=control.pid
        name=f'controller {control.id}'
        if pid.kd!=0 or pid.derivative_tau_s!=0:
            raise ValueError(f'{name}: the DAE implements PI control; kd and derivative_tau_s must be 0')
        if pid.rate_limit_per_s is not None:
            raise ValueError(f'{name}: the DAE does not model an actuator slew limit; omit rate_limit_per_s')
        if pid.ki<=0:
            raise ValueError(f'{name}: the DAE controller needs ki > 0 (its integral is the state that removes the offset)')
        if abs(control.sample_period_s-self.time.dt_s)>1e-9*max(1.,self.time.dt_s):
            raise ValueError(f'{name}: the DAE integrates the loop every step; sample_period_s must equal time.dt_s')
        edge=edges[control.actuator.edge_id]
        field=control.actuator.field
        if field=='opening' and pid.output_min<=0:
            raise ValueError(f'{name}: a valve controller needs output_min > 0 (the DAE keeps a fixed valve active set)')
        declared=next(e for e in self.edges if e.id==edge['id'])
        if edge['type']=='pump' and getattr(declared.params,'mode',None)=='flow':
            raise ValueError(f'{name}: pump {edge["id"]} holds a flow set point; its speed is not an actuator')
        scheduled=edge['params'].get(field)
        if field=='opening' and any(value<=0 for value in _sample_schedule(scheduled,self.time.N_t)):
            raise ValueError(f'{name}: valve {edge["id"]} is closed; a controlled valve must start open')
        if isinstance(scheduled,list) and len(set(scheduled))>1:
            raise ValueError(f'{name}: {edge["id"]}.{field} has a time schedule; a controlled actuator takes its value from the loop')


def _graph_and_primary_contract(self):
        hx_ids={edge.id for edge in self.edges if edge.type=='heat_exchanger'}
        bound=[entry.edge_id for entry in self.primary_circuits]
        if len(set(bound))!=len(bound):
            raise ValueError('primary circuit edge_id bindings must be unique')
        if set(bound)!=hx_ids:
            raise ValueError('primary_circuits must cover every heat-exchanger HX edge exactly once and no other edge')
        for entry in self.primary_circuits:
            for name in ('flow_kg_s','inlet_K'):
                value=getattr(entry,name)
                if isinstance(value,list) and len(value)!=self.time.N_t:
                    raise ValueError(f'primary circuit {entry.edge_id}.{name} series must contain exactly N_t samples')
        if self.analysis.mode=='steady' and (self.time.N_t!=1 or self.events):
            raise ValueError('steady analysis requires N_t=1 and no scheduled events')
        view=_v2_graph_view(self)
        LiquidrunV2.model_validate(view)
        _dae_controller_contract(self,view)
        for edge in view['edges']:
            if edge['type']!='valve':
                continue
            opening=_sample_schedule(edge['params']['opening'],self.time.N_t)
            for event in self.events:
                if event.target==edge['id'] and event.field=='opening':
                    step=round(event.time_s/self.time.dt_s)
                    opening[step:]=[event.value]*(self.time.N_t-step)
            closed=[value<=0 for value in opening]
            if any(value!=closed[0] for value in closed):
                raise ValueError('DAE v3 requires a fixed valve active set; opening/closing needs a new topology initialization')
        return self


class PumpParamsV4(PumpParams):
    mode: Literal['curve','flow'] | None = None
    flow_set_kg_s: Nonnegative | Annotated[list[Nonnegative],Field(min_length=1)] | None = None


class ColdPlateParamsV4(ColdPlateParams):
    heat_mode: Literal['power','temperature_rise'] | None = None
    dT_set_K: Nonnegative | Annotated[list[Nonnegative],Field(min_length=1)] | None = None


class TwoVolumeHXParamsV4(TwoVolumeHXParams):
    mode: Literal['physics','setpoint'] | None = None
    T_set_K: Positive | Annotated[list[Positive],Field(min_length=1)] | None = None


class PumpEdgeV4(EdgeBase):
    type: Literal['pump']
    params: PumpParamsV4


class ColdPlateEdgeV4(EdgeBase):
    type: Literal['cold_plate']
    params: ColdPlateParamsV4


class TwoVolumeHXEdgeV4(EdgeBase):
    type: Literal['heat_exchanger']
    params: TwoVolumeHXParamsV4


DAEEdgeV4 = Annotated[PipeEdge | PumpEdgeV4 | ValveEdge | ColdPlateEdgeV4 | TwoVolumeHXEdgeV4,
                      Field(discriminator='type')]


class NodeBCV4(StrictModel):
    P_Pa: Number | Annotated[list[Number],Field(min_length=1)]
    T_K: Positive | None = None


class NodeV4(Node):
    bc: NodeBCV4 | None = None


class TransientTrack(StrictModel):
    modelUid: Annotated[str,Field(min_length=1)]
    param: Annotated[str,Field(min_length=1)]
    target: str


class TransientStartUniform(StrictModel):
    airT_C: float | None = None
    waterT_C: float | None = None
    solidT_C: float | None = None


class TransientStart(StrictModel):
    kind: Literal['steadyConverged','uniform']
    uniform: TransientStartUniform | None = None


class TransientProvenance(StrictModel):
    """Which project timeline this run was sampled from (cfdproj v5)."""
    durationS: Positive
    start: TransientStart
    tracks: list[TransientTrack]


class LiquidrunDAEV4(LiquidrunDAEV3):
    schemaVersion: Literal[4]
    version: Literal['4.0']
    nodes: Annotated[list[NodeV4],Field(min_length=1)]
    edges: Annotated[list[DAEEdgeV4],Field(min_length=1)]
    # Every keyframed input is a list[N_t]; events exist only to read v3.
    events: Annotated[list[Event],Field(max_length=0)]
    transient: TransientProvenance
    manufactured_benchmark: ManufacturedBenchmark | None = None

    @model_validator(mode='after')
    def graph_and_primary_contract(self):
        count=self.time.N_t
        def length(owner,name,value):
            if isinstance(value,list) and len(value)!=count:
                raise ValueError(f'{owner}.{name} series must contain exactly N_t samples')
        for node in self.nodes:
            if node.bc is not None:
                length(node.id,'P_Pa',node.bc.P_Pa)
        for edge in self.edges:
            params=edge.params
            if edge.type=='pump':
                held=(params.mode or 'curve')=='flow'
                if held!=(params.flow_set_kg_s is not None):
                    raise ValueError(f'pump {edge.id}: mode "flow" needs flow_set_kg_s, and only a flow-held pump carries one')
                if held and params.rpm_ratio is not None:
                    raise ValueError(f'pump {edge.id}: a flow-held pump has no rpm_ratio input')
                length(edge.id,'flow_set_kg_s',params.flow_set_kg_s)
            elif edge.type=='cold_plate':
                rise=(params.heat_mode or 'power')=='temperature_rise'
                if rise!=(params.dT_set_K is not None):
                    raise ValueError(f'cold plate {edge.id}: heat_mode "temperature_rise" needs dT_set_K, and only such a plate carries one')
                if rise and params.Q_W is not None:
                    raise ValueError(f'cold plate {edge.id}: a temperature-rise plate derives its heat; Q_W is not an input')
                length(edge.id,'dT_set_K',params.dT_set_K)
            elif edge.type=='heat_exchanger':
                held=(params.mode or 'physics')=='setpoint'
                if held!=(params.T_set_K is not None):
                    raise ValueError(f'heat exchanger {edge.id}: mode "setpoint" needs T_set_K, and only a held exchanger carries one')
                length(edge.id,'T_set_K',params.T_set_K)
        result=_graph_and_primary_contract(self)
        if self.manufactured_benchmark is not None:
            validate_contract(self.manufactured_benchmark,self,_v2_graph_view(self))
        return result


def is_dae_manifest(raw) -> bool:
    """A DAE .liquidrun (v3 or v4), by its declared version."""
    return isinstance(raw,dict) and (raw.get('version') in ('3.0','4.0') or raw.get('schemaVersion') in (3,4))


def parse_dae_manifest(raw) -> LiquidrunDAEV3:
    """Validate a DAE manifest as the version it declares (v4 is a v3 subclass)."""
    model=LiquidrunDAEV4 if isinstance(raw,dict) and raw.get('schemaVersion')==4 else LiquidrunDAEV3
    return model.model_validate(raw)


def _attach_hx_models(graph,manifest:LiquidrunDAEV3,*,device) -> dict[str,str]:
    """Per-HX model flags in compiled HX order; returns {edge_id: model}."""
    import torch
    edges={edge.id:edge for edge in manifest.edges}
    hx=[] if graph.hx_indices is None else [graph.edge_str_ids[i] for i in graph.hx_indices.edge_ids.tolist()]
    models={edge_id:(edges[edge_id].params.hxModel or 'mixed-volumes') for edge_id in hx}
    flags=[models[edge_id]=='counterflow-ntu' for edge_id in hx]
    if any(flags):
        graph.hx_counterflow_mode=torch.tensor(flags,dtype=torch.bool,device=device)
    return models


def _attach_endpoint_forms(graph,manifest:LiquidrunDAEV4,*,dtype,device) -> None:
    """Put the v4 forms on the compiled graph, in its component order."""
    import torch
    count=manifest.time.N_t
    edges={edge.id:edge for edge in manifest.edges}
    def ordered(kind):
        indices=getattr(graph,kind+'_indices')
        return [] if indices is None else [edges[graph.edge_str_ids[i]] for i in indices.edge_ids.tolist()]
    def attach(kind,mode_name,series_name,held,value):
        members=ordered(kind)
        flags=[held(edge.params) for edge in members]
        if not any(flags):
            return
        setattr(graph,mode_name,torch.tensor(flags,dtype=torch.bool,device=device))
        rows=[_sample_schedule(value(edge.params),count) if flag else [0.]*count for edge,flag in zip(members,flags)]
        setattr(graph,series_name,torch.tensor(rows,dtype=dtype,device=device).reshape(len(members),count))
    attach('pump','pump_flow_mode','pump_flow_set',lambda p:(p.mode or 'curve')=='flow',lambda p:p.flow_set_kg_s)
    attach('cp','cp_rise_mode','cp_dT_set',lambda p:(p.heat_mode or 'power')=='temperature_rise',lambda p:p.dT_set_K)
    attach('hx','hx_setpoint_mode','hx_T_set',lambda p:(p.mode or 'physics')=='setpoint',lambda p:p.T_set_K)
    varying={node.id:node.bc.P_Pa for node in manifest.nodes if node.bc is not None and isinstance(node.bc.P_Pa,list)}
    if varying:
        series=graph.bc_P.detach().clone().unsqueeze(1).repeat(1,count)
        for index,name in enumerate(graph.node_ids):
            if name in varying:
                series[index]=torch.tensor(varying[name],dtype=series.dtype,device=series.device)
        graph.bc_P_series=series


@dataclass
class LoadedDAEInput:
    manifest: LiquidrunDAEV3
    equation_graph: EquationGraph
    provenance: dict
    _primary_order: tuple[PrimaryCircuitBinding,...]
    _primary_inlet: Tensor
    _primary_flow: Tensor
    _manufactured_sources: dict | None = None

    @property
    def mode(self):return self.manifest.analysis.mode

    @property
    def initialization(self):return self.manifest.analysis.initialization

    @property
    def dt(self):return self.manifest.time.dt_s

    @property
    def sample_count(self):return self.manifest.time.N_t

    @property
    def newton_options(self) -> NewtonOptions:return self.manifest.newton.to_options(self.equation_graph.size)

    def parameters(self,index:int,heat_multiplier=1.0,*,pump_rpm_ratio=None,valve_opening=None) -> Tensor:
        """Scheduled theta plus differentiable, full-vector actuator overrides.

        Online edge-ID commands are resolved to component order by the host.
        The numerical parameter boundary accepts tensors in that fixed order;
        no source schedule or compiled graph is mutated.
        """
        import torch
        if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<self.sample_count:
            raise ValueError('parameter sample index must lie within N_t')
        if isinstance(heat_multiplier,bool) or (isinstance(heat_multiplier,torch.Tensor) and heat_multiplier.dtype==torch.bool):
            raise ValueError('heat_multiplier must be a real scalar')
        graph=self.equation_graph.graph
        reference=graph.bc_P
        if isinstance(heat_multiplier,torch.Tensor) and (heat_multiplier.device!=reference.device or heat_multiplier.is_complex()):
            raise ValueError('heat_multiplier must be a real scalar on the graph device')
        scale=torch.as_tensor(heat_multiplier,dtype=reference.dtype,device=reference.device)
        if scale.ndim!=0 or not bool(torch.isfinite(scale)) or not bool(scale>=0):
            raise ValueError('heat_multiplier must be a finite nonnegative scalar')
        heat=graph.cp_params.Q_W[:,index] if graph.cp_params is not None else scale.new_empty(0)
        primary_inlet=self._primary_inlet[:,index]
        primary_flow=self._primary_flow[:,index]
        if isinstance(pump_rpm_ratio,torch.Tensor) and pump_rpm_ratio.device!=reference.device:
            raise ValueError('pump_rpm_ratio tensor must share the graph device')
        if isinstance(valve_opening,torch.Tensor) and valve_opening.device!=reference.device:
            raise ValueError('valve_opening tensor must share the graph device')
        # A heat multiplier scales a prescribed coolant temperature rise too,
        # so it scales every plate's heat whichever form sets it.
        rise=graph.cp_dT_set[:,index]*scale if graph.cp_dT_set is not None else None
        theta=self.equation_graph.parameters(index,heat_W=heat*scale,
            pump_rpm_ratio=pump_rpm_ratio,primary_inlet_K=primary_inlet,
            primary_flow_kg_s=primary_flow,cp_dT_set=rise)
        if valve_opening is not None:
            if isinstance(valve_opening,torch.Tensor) and (valve_opening.dtype==torch.bool or valve_opening.is_complex()):
                raise ValueError('valve_opening must contain real values')
            value=torch.as_tensor(valve_opening,dtype=reference.dtype,device=reference.device)
            part=self.equation_graph.parameter_layout['valve_opening']
            count=part.stop-part.start
            if value.ndim==0 and count==1:
                value=value.reshape(1)
            if value.shape!=(count,):
                raise ValueError(f'valve_opening must be a [{count}] tensor')
            theta=torch.cat((theta[:part.start],value,theta[part.stop:]))
            self.equation_graph.validate_parameters(theta)
        if self._manufactured_sources is not None:
            for name,series in self._manufactured_sources.items():
                part=self.equation_graph.parameter_layout[name]
                theta=torch.cat((theta[:part.start],series[:,index],theta[part.stop:]))
            self.equation_graph.validate_parameters(theta)
        return theta


def load_dae_input(raw: dict | LiquidrunDAEV3) -> LoadedDAEInput:
    """Validate and compile v3 without solving or touching a live session.

    No implicit v1/v2 migration, file access, controller execution, or defaults
    for the facility-primary circuit occur here.
    """
    import torch
    from backend.liquidcooling.graphcompiling.compiler import compile_graph
    from backend.liquidcooling.solver.equation_graph import EquationGraph,PrimaryCircuit
    # Round-trip typed input rather than trusting a caller's model_copy(update=)
    # or mutation to have passed Pydantic validation.
    public=raw.model_dump(by_alias=True,mode='json',exclude_none=True) if isinstance(raw,LiquidrunDAEV3) else deepcopy(raw)
    model=LiquidrunDAEV4 if isinstance(public,dict) and public.get('schemaVersion')==4 else LiquidrunDAEV3
    manifest=model.model_validate(public)
    view=_v2_graph_view(manifest)
    # Compile base schedules, then apply each event at its validated integer
    # sample. Legacy int(time/dt) can floor 0.3/0.1 to 2 rather than 3.
    view['events']=[]
    # Topology is compiled once on the host, then ALL tensor state/indices and
    # coefficients move together. No transfer or NumPy conversion in F.
    execution=manifest.execution
    device=torch.device(execution.device)
    dtype=getattr(torch,execution.dtype)
    graph=compile_graph(parse_liquidrun(view),device='cpu').to(device,dtype=dtype)
    graph.solver_config=manifest.newton.to_options()
    if isinstance(manifest,LiquidrunDAEV4):
        _attach_endpoint_forms(graph,manifest,dtype=dtype,device=device)
    _attach_hx_models(graph,manifest,device=device)
    edge_indices={name:index for index,name in enumerate(graph.edge_str_ids)}
    for event in manifest.events:
        kind,field={'Q_W':('cp','Q_W'),'rpm_ratio':('pump','rpm_ratio'),'opening':('valve','opening')}[event.field]
        indices=getattr(graph,kind+'_indices').edge_ids.tolist()
        local=indices.index(edge_indices[event.target])
        start=round(event.time_s/manifest.time.dt_s)
        schedule=getattr(getattr(graph,kind+'_params'),field)
        schedule[local,start:]=event.value
    by_id={entry.edge_id:entry for entry in manifest.primary_circuits}
    hx_ids=[] if graph.hx_indices is None else [graph.edge_str_ids[index] for index in graph.hx_indices.edge_ids.tolist()]
    ordered=tuple(by_id[edge_id] for edge_id in hx_ids)
    def vector(name,index=None):
        return torch.tensor([entry.value_at(name,index) if index is not None else getattr(entry,name) for entry in ordered],dtype=dtype,device=device)
    source='; '.join(f'{entry.edge_id}: {entry.source}: {entry.source_reference}' for entry in ordered) or 'no heat exchanger in this manifest'
    primary=PrimaryCircuit(flow_kg_s=vector('flow_kg_s',0),inlet_K=vector('inlet_K',0),
        cp_J_kgK=vector('cp_J_kgK'),rho_kg_m3=vector('rho_kg_m3'),primary_volume_m3=vector('primary_volume_m3'),
        secondary_volume_m3=vector('secondary_volume_m3'),source=source)
    benchmark=getattr(manifest,'manufactured_benchmark',None)
    equation=EquationGraph(graph,primary,manufactured=benchmark is not None,controllers=manifest.controllers)
    sources=bind(equation,benchmark) if benchmark is not None else None
    original=manifest.model_dump(by_alias=True,mode='json',exclude_none=True)
    resolved=resolve_parameter_pack(original)
    provenance={'manifest_sha256':_digest(original),'source_project':manifest.source_project.model_dump(exclude_none=True),
        'parameter_pack':resolved.get('parameter_provenance'),
        'primary_circuits':[entry.model_dump(mode='json') for entry in ordered],
        'legacy_hx_adapter':'T_secondary_K is an internal compiler placeholder derived from explicit primary inlet at sample 0; EquationGraph does not use it'}
    if isinstance(manifest,LiquidrunDAEV4):
        provenance['transient']=manifest.transient.model_dump(mode='json',exclude_none=True)
    # Versioned constitutive laws: same manifest hash, different physics must
    # be distinguishable in every result (report D1/D5/S1, 2026-10-21).
    provenance['solver_laws']={name:deepcopy(equation.provenance[name]) for name in ('pump_law','friction_law','hydrostatics','hx_models','tee_law','controller_law') if name in equation.provenance}
    def schedule(name):
        values=[_sample_schedule(getattr(entry,name),manifest.time.N_t) for entry in ordered]
        return torch.tensor(values,dtype=dtype,device=device).reshape(len(ordered),manifest.time.N_t)
    return LoadedDAEInput(manifest,equation,provenance,ordered,schedule('inlet_K'),schedule('flow_kg_s'),sources)


def load_dae_file(path:str|Path) -> LoadedDAEInput:
    with Path(path).open(encoding='utf-8') as stream:
        return load_dae_input(json.load(stream))


def dae_json_schema(version:int=3) -> dict:
    model={3:LiquidrunDAEV3,4:LiquidrunDAEV4}[version]
    schema=model.model_json_schema(by_alias=True)
    schema.update({'$schema':'https://json-schema.org/draft/2020-12/schema',
        '$id':f'https://dcdtuni.local/schemas/liquidrun-v{version}.schema.json',
        'description':'Strict acausal incompressible/thermal DAE input. Python adds profile completeness, graph/HX references, exact series/grid lengths and fixed valve active-set validation.'})
    return schema


if __name__=='__main__':
    for version,name in ((3,'liquidrun_dae.schema.json'),(4,'liquidrun_dae_v4.schema.json')):
        Path(__file__).with_name(name).write_text(json.dumps(dae_json_schema(version),indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
