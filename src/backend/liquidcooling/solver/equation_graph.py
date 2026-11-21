"""Compiled liquid networks as a continuous, non-causal index-one DAE.

`from`/`to` define signed coordinates, not a causal execution order. Every
continuous coefficient used by the residual is packed into explicit `theta`.
Storage is pipe coolant, cold-plate solid, and two mixed HX fluid volumes.
The facility-primary circuit is a specified-flow boundary, not a second solved
hydraulic network. Its return temperature is a solved storage state.

The adapter follows the graph device and float32/float64 dtype. The residual
uses batched device tensors without host scalar extraction; admission and
accepted-state checks are explicit callbacks outside Newton/Jacobian kernels.
It supports a fixed valve active set. Open-edge
components need pressure references; a floating component is rejected. A
structurally blind pipe retains its coolant storage but has exactly zero
advection. Dry zero-volume junction temperatures have a numerical gauge and
are explicitly marked unobservable; no physical junction heat capacity is
invented. Gradients are piecewise within a flow/valve regime.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from typing import TYPE_CHECKING

import torch
from torch import Tensor

from backend.liquidcooling.graphcompiling.compiled_graph import CompiledGraph
from backend.liquidcooling.graphcompiling.compiler import TEE_LAW
from backend.liquidcooling.handlers.valve import effective_valve_kv
from backend.liquidcooling.solver.newton_hydraulic import (
    FRICTION_LAW, HYDROSTATICS, PUMP_LAW, _pipe_dp_friction, pump_headloss, quadratic_headloss, valve_headloss,
)

# Registered in provenance.solver_laws when a run has a controller.
CONTROLLER_LAW = 'pi_backward_euler_backcalc_antiwindup/v1'


if TYPE_CHECKING:
    from backend.liquidcooling.solver.dae_newton import DAEProblem


@dataclass(frozen=True)
class PrimaryCircuit:
    """Explicit per-HX boundary/properties; `source` must identify their origin.

    All fields are vectors [N_hx], in the same order as graph.hx_indices.
    Both volumes must be positive. UA is the graph's hx_params.UA, read per HX
    by its hxModel: the conductance between two perfectly mixed volumes
    ('mixed-volumes', the default of a manifest without the field) or the
    counterflow epsilon-NTU UA ('counterflow-ntu').
    Positive primary flow goes from inlet_K to the solved primary return.
    """
    flow_kg_s: Tensor
    inlet_K: Tensor
    cp_J_kgK: Tensor
    rho_kg_m3: Tensor
    primary_volume_m3: Tensor
    secondary_volume_m3: Tensor
    source: str

    def to(self,device=None,dtype=None) -> 'PrimaryCircuit':
        """Move fluid properties/boundaries together without detaching gradients."""
        values={}
        for name in ('flow_kg_s','inlet_K','cp_J_kgK','rho_kg_m3','primary_volume_m3','secondary_volume_m3'):
            value=torch.as_tensor(getattr(self,name))
            if value.dtype==torch.bool or value.is_complex():
                raise ValueError(f'{name} must contain real numbers')
            values[name]=value.to(device=device,dtype=dtype)
        return PrimaryCircuit(**values,source=self.source)


def _vector(value, count: int, name: str, *, device, dtype) -> Tensor:
    if isinstance(value, Tensor) and (value.dtype == torch.bool or value.is_complex()):
        raise ValueError(f'{name} must contain real numbers, not booleans/complex values')
    out = torch.as_tensor(value,dtype=dtype,device=device)
    if out.ndim == 0 and count == 1:
        out = out.reshape(1)
    if out.shape != (count,):
        raise ValueError(f'{name} must be a [{count}] tensor')
    return out


def _layout(sizes: dict[str, int]) -> tuple[dict[str, slice], int]:
    result, end = {}, 0
    for name, size in sizes.items():
        result[name] = slice(end, end + size)
        end += size
    return result, end


def stagnant_edges_by_blocks(n_nodes:int,edge_from:list[int],edge_to:list[int],open_edges:list[bool],
                             datum_nodes:list[int],drivers:list[bool]) -> list[bool]:
    """Edges whose flow is exactly zero in EVERY state (report S2/D6).

    Augment the open-edge graph with one ground node joined to every pressure
    datum, then split it into biconnected blocks. A block can carry flow only
    if it is not a bridge and contains a driver (a pump, or a ground edge,
    i.e. a path between pressure data). Proof sketch: a bridge separates a
    side without ground, whose free-node mass balance forces zero net flow;
    a driverless block exchanges zero net mass with the rest through its cut
    vertices, and Tellegen's theorem with strictly passive monotone laws
    (pipe, plate, HX, valve) and closed elevation potentials (compiler check)
    makes every flow in it zero. This generalizes dangling-tree pruning to
    stagnant loops, e.g. parallel plates behind a closed return valve.
    """
    ground=n_nodes
    real=len(edge_from)
    adjacency:list[list[tuple[int,int]]]=[[] for _ in range(n_nodes+1)]
    for edge,(a,b,is_open) in enumerate(zip(edge_from,edge_to,open_edges)):
        if is_open:
            adjacency[a].append((b,edge));adjacency[b].append((a,edge))
    for k,node in enumerate(datum_nodes):
        adjacency[node].append((ground,real+k));adjacency[ground].append((node,real+k))
    disc=[-1]*(n_nodes+1);low=[0]*(n_nodes+1);clock=0
    stack_edges:list[int]=[];blocks:list[list[int]]=[]
    for start in range(n_nodes+1):
        if disc[start]!=-1 or not adjacency[start]:continue
        disc[start]=low[start]=clock;clock+=1
        frames=[(start,-1,iter(adjacency[start]))]
        while frames:
            node,parent_edge,neighbours=frames[-1]
            descended=False
            for other,edge in neighbours:
                if edge==parent_edge:continue
                if disc[other]==-1:
                    stack_edges.append(edge)
                    disc[other]=low[other]=clock;clock+=1
                    frames.append((other,edge,iter(adjacency[other])));descended=True;break
                if disc[other]<disc[node]:
                    stack_edges.append(edge);low[node]=min(low[node],disc[other])
            if descended:continue
            frames.pop()
            if frames:
                parent=frames[-1][0]
                low[parent]=min(low[parent],low[node])
                if low[node]>=disc[parent]:
                    block=[]
                    while True:
                        edge=stack_edges.pop();block.append(edge)
                        if edge==parent_edge:break
                    blocks.append(block)
    stagnant=[False]*real
    for block in blocks:
        driven=len(block)>1 and any(edge>=real or drivers[edge] for edge in block)
        if not driven:
            for edge in block:
                if edge<real:stagnant[edge]=True
    return stagnant


class EquationGraph:
    """Adapter exposing one F(t,y,ydot,theta) for steady and transient Newton."""

    def __init__(self, graph: CompiledGraph, primary: PrimaryCircuit, *, device=None,dtype=None,manufactured=False,
                 controllers=()):
        self._manufactured=manufactured
        self.device=torch.device(graph.bc_P.device if device is None else device)
        self.dtype=graph.bc_P.dtype if dtype is None else dtype
        if self.dtype not in (torch.float32,torch.float64):
            raise ValueError('EquationGraph dtype must be float32 or float64')
        if not isinstance(primary.source, str) or not primary.source.strip():
            raise ValueError('primary circuit requires a nonempty parameter source label')
        # Topological analysis is a one-time host operation, never repeated in
        # the residual. Transfer whole index arrays, not per-edge CUDA scalars.
        self._host_from=graph.edge_from.detach().cpu().tolist()
        self._host_to=graph.edge_to.detach().cpu().tolist()
        self._host_bc=graph.bc_mask.detach().cpu().tolist()
        host_bc_T=graph.bc_T_mask.detach().cpu().tolist()
        host_spans=[] if graph.pipe_indices is None else list(zip(
            graph.pipe_indices.edge_ids.detach().cpu().tolist(),
            graph.pipe_indices.cell_start.detach().cpu().tolist(),
            graph.pipe_indices.cell_end.detach().cpu().tolist()))
        graph=graph.to(self.device,self.dtype)
        primary=primary.to(self.device,self.dtype)
        self.graph, self.primary = graph, primary
        self._from = graph.edge_from.detach().clone()
        self._to = graph.edge_to.detach().clone()
        self._bc_mask = graph.bc_mask.detach().clone()
        self._bc_T_mask = graph.bc_T_mask.detach().clone()
        self._free=torch.tensor([i for i,value in enumerate(self._host_bc) if not value],device=self.device,dtype=torch.long)
        self._boundary_nodes=torch.tensor([i for i,(pressure,temperature) in enumerate(zip(self._host_bc,host_bc_T)) if pressure or temperature],device=self.device,dtype=torch.long)
        self._ids = {
            name: (getattr(graph, name + '_indices').edge_ids.detach().clone()
                   if getattr(graph, name + '_indices') is not None else torch.empty(0,dtype=torch.long,device=self.device))
            for name in ('pipe','cp','pump','valve','hx')
        }
        self._characteristic = (graph.valve_params.characteristic.detach().clone()
                                if graph.valve_params is not None else torch.empty(0,dtype=torch.long,device=self.device))
        # .liquidrun v4 endpoint forms, fixed for the run (like the valve active
        # set): which pumps hold a flow, plates a coolant temperature rise, HXs a
        # secondary supply set point. Absent = the v3 physics for every element.
        def mode(name,count):
            value=getattr(graph,name,None)
            if value is None:
                return torch.zeros(count,dtype=torch.bool,device=self.device)
            if not isinstance(value,Tensor) or value.dtype!=torch.bool or value.shape!=(count,):
                raise ValueError(f'{name} must be a [{count}] boolean tensor')
            return value.detach().to(self.device).clone()
        self._pump_flow=mode('pump_flow_mode',len(self._ids['pump']))
        self._cp_rise=mode('cp_rise_mode',len(self._ids['cp']))
        self._hx_setpoint=mode('hx_setpoint_mode',len(self._ids['hx']))
        # hxModel 'counterflow-ntu' (owner decision 2026-10-21); False = the
        # two-mixed-volume conductance every manifest without the field ran.
        self._hx_counterflow=mode('hx_counterflow_mode',len(self._ids['hx']))
        self._any_counterflow=bool(self._hx_counterflow.any())
        # Elevation rise of non-pipe edges from node heights (report D5); pipes
        # keep their route rise in the pipe_dh parameter. Constant geometry.
        component_dh=getattr(graph,'component_dh',None)
        self._component_dh=(torch.zeros(graph.N_edges,dtype=self.dtype,device=self.device) if component_dh is None
                            else component_dh.detach().to(device=self.device,dtype=self.dtype).clone())
        self._has_component_dh=bool((self._component_dh!=0).any())
        self._flow_edges=torch.zeros(graph.N_edges,dtype=torch.bool,device=self.device)
        self._flow_edges[self._ids['pump']]=self._pump_flow
        # The current compiler stores PIPE-LOCAL indices in cell_edge_id,
        # despite its generic name. Derive global ownership from pipe spans.
        cells,first,last=[-1]*graph.N_cells,[False]*graph.N_cells,[False]*graph.N_cells
        pipe_first,pipe_last=[],[]
        for edge,begin,end in host_spans:
            if not 0<=begin<end<=graph.N_cells:
                raise ValueError('pipe storage requires a valid nonempty cell span')
            if any(value!=-1 for value in cells[begin:end]):
                raise ValueError('pipe storage spans overlap')
            cells[begin:end]=[edge]*(end-begin)
            first[begin],last[end-1]=True,True
            pipe_first.append(begin);pipe_last.append(end-1)
        if any(value<0 for value in cells):
            raise ValueError('pipe storage spans do not cover all cells')
        self._cells=torch.tensor(cells,dtype=torch.long,device=self.device)
        self._first=torch.tensor(first,dtype=torch.bool,device=self.device)
        self._last=torch.tensor(last,dtype=torch.bool,device=self.device)
        self._pipe_first=torch.tensor(pipe_first,dtype=torch.long,device=self.device)
        self._pipe_last=torch.tensor(pipe_last,dtype=torch.long,device=self.device)
        cell_index = torch.arange(graph.N_cells,device=self.device)
        self._previous = (cell_index-1).clamp(min=0)
        self._next = (cell_index+1).clamp(max=max(0,graph.N_cells-1))

        self._compile_controllers(controllers)

        self.state_layout, self.size = _layout({
            'pressure_free':len(self._free), 'flow':graph.N_edges,
            'node_temperature':graph.N_nodes, 'water_temperature':graph.N_cells,
            'solid_temperature':graph.N_cp, 'hx_secondary_temperature':graph.N_hx,
            'primary_return_temperature':graph.N_hx,
            'controller_integral':self.controller_count,
        })
        self.differential_mask = torch.zeros(self.size,dtype=torch.bool,device=self.device)
        for name in ('water_temperature','solid_temperature','hx_secondary_temperature','primary_return_temperature',
                     'controller_integral'):
            self.differential_mask[self.state_layout[name]] = True
        # A held secondary supply temperature is algebraic (T_s = T_set); its
        # storage enters through the explicit set-point rate instead.
        secondary_rows=torch.arange(self.state_layout['hx_secondary_temperature'].start,
                                    self.state_layout['hx_secondary_temperature'].stop,device=self.device)
        self.differential_mask[secondary_rows[self._hx_setpoint]] = False

        # Physical tensors remain attached until parameters() explicitly packs
        # them. The residual never reads this defaults map or original graph.
        self._defaults = self._parameter_fields(0)
        self.parameter_layout, self.parameter_count = _layout({name:value.numel() for name,value in self._defaults.items()})
        self._positive_names=('fluid_rho','fluid_cp','fluid_mu','pipe_D','pipe_L','pipe_area','cell_volume',
                             'cp_Csolid','primary_inlet_K','primary_cp','primary_rho','primary_volume','secondary_volume',
                             'controller_tracking_s')
        self._nonnegative_names=('heat_W','pump_rpm_ratio','valve_Kv','valve_opening','primary_flow_kg_s','cp_UA','hx_UA',
                                 'pump_flow_set','cp_dT_set','controller_kp','controller_ki','controller_output_min')
        def indices(names):
            return torch.tensor([i for name in names for i in range(self.parameter_layout[name].start,self.parameter_layout[name].stop)],dtype=torch.long,device=self.device)
        self._positive_indices=indices(self._positive_names)
        self._nonnegative_indices=indices(self._nonnegative_names)
        self._closed_valves = self._defaults['valve_opening'].detach() <= 0
        self._closed_edges = torch.zeros(graph.N_edges,dtype=torch.bool,device=self.device)
        self._closed_edges[self._ids['valve']] = self._closed_valves
        self._dead_edges = self._pressure_topology_and_blind_edges()
        self._thermal_active = ~(self._closed_edges | self._dead_edges)
        # State and residual blocks have identical order. These storage rows
        # become neutral at steady state when their prescribed cell heat is 0.
        # The steady solver may select the conserved reference temperature,
        # but must still verify the ORIGINAL residual (nonzero heat has no root).
        self.steady_neutral_storage_mask = torch.zeros(self.size,dtype=torch.bool,device=self.device)
        self.steady_neutral_storage_mask[self.state_layout['water_temperature']] = self._dead_edges[self._cells]
        # A stagnant plate exchanges nothing with coolant: unheated, its solid
        # keeps the conserved stored temperature; heated, no steady state
        # exists (check_steady_exists says which plates and valves).
        self.steady_neutral_storage_mask[self.state_layout['solid_temperature']] = self._dead_edges[self._ids['cp']]
        self.stagnant_mask = (self._closed_edges|self._dead_edges).detach().clone()
        self.stagnant_edges = self.stagnant_mask.cpu().tolist()
        self._node_gauge_conductance = 4000.0  # numerical W/K scaling only
        self.state_scale = torch.ones(self.size,dtype=self.dtype,device=self.device)
        self.residual_scale = torch.ones(self.size,dtype=self.dtype,device=self.device)
        self.state_scale[self.state_layout['pressure_free']] = 1e5
        # Rows in kg/s (a closed valve, a flow-held pump) are not Pa rows.
        self.residual_scale[self.state_layout['flow']] = torch.where(self._closed_edges|self._flow_edges,1.,1e5)
        for name in ('node_temperature','water_temperature','solid_temperature','hx_secondary_temperature','primary_return_temperature'):
            self.state_scale[self.state_layout[name]] = 300.
            self.residual_scale[self.state_layout[name]] = 1e4
        from backend.liquidcooling.solver.dae_newton import DAEProblem
        self.problem: DAEProblem = DAEProblem(
            residual=self.residual, differential_mask=self.differential_mask,
            state_scale=self.state_scale, residual_scale=self.residual_scale,
            name='compiled-liquid-two-sided-hx',
            parameter_validator=self.validate_parameters,state_validator=self.validate_state,
            linear_partition=self.state_layout['flow'].stop,
        )
        hx_names=[graph.edge_str_ids[i] for i in self._ids['hx'].detach().cpu().tolist()]
        counterflow=self._hx_counterflow.detach().cpu().tolist()
        hx_models={name:('counterflow-ntu' if flag else 'mixed-volumes') for name,flag in zip(hx_names,counterflow)}
        hx_summary=('two_mixed_fluid_volumes' if not any(counterflow) else
                    'counterflow_ntu_steady_duty_with_mixed_volume_departures' if all(counterflow) else 'per_edge_see_hx_models')
        self.provenance = {'primary_circuit':primary.source,'hx_model':hx_summary,'hx_models':hx_models,
                           'cold_plate_fluid_model':'quasisteady_NTU_no_coolant_storage',
                           'dry_node_policy':'numerical_temperature_gauge_unobservable',
                           'pump_law':PUMP_LAW,
                           'friction_law':FRICTION_LAW,
                           'hydrostatics':HYDROSTATICS,
                           'tee_law':TEE_LAW}
        if self.controller_count:
            self.provenance['controller_law']=CONTROLLER_LAW

    def _parameter_fields(self, time_index: int) -> dict[str,Tensor]:
        g = self.graph
        if isinstance(time_index,bool) or not isinstance(time_index,int) or not 0 <= time_index < g.N_t:
            raise ValueError('time_index must lie within the compiled horizon')
        def constant(value,count,name):
            return _vector(value,count,name,device=self.device,dtype=self.dtype)
        def sample(value,count,name,index=time_index):
            if value is None:
                return torch.zeros(count,dtype=self.dtype,device=self.device)
            if value.ndim == 2:
                column = 0 if value.shape[1] == 1 else index
                if column >= value.shape[1]:
                    raise ValueError(f'{name} schedule is shorter than the horizon')
                value = value[:,column]
            return constant(value,count,name)
        def rate(value,count,name):
            # Backward difference on the sample grid, consistent with the
            # backward-Euler step that consumes it; 0 at the first sample.
            if value is None or time_index == 0 or value.ndim < 2 or value.shape[1] == 1:
                return torch.zeros(count,dtype=self.dtype,device=self.device)
            return (sample(value,count,name)-sample(value,count,name,time_index-1))/g.dt
        fields = {
            'fluid_rho':constant(g.fluid_rho,1,'fluid_rho'),
            'fluid_cp':constant(g.fluid_cp,1,'fluid_cp'),
            'fluid_mu':constant(g.fluid_mu,1,'fluid_mu'),
            'gravity':constant(g.g_mag,1,'gravity'),
            'pressure_bc':sample(g.bc_P_series if g.bc_P_series is not None else g.bc_P,g.N_nodes,'pressure_bc'),
            'temperature_bc':sample(g.bc_T_series if g.bc_T_series is not None else g.bc_T,g.N_nodes,'temperature_bc'),
            'node_gauge_temperature':g.bc_P.new_ones(g.N_nodes)*g.T_fluid_init,
            'initial_water_temperature':constant(g.cell_T_init,g.N_cells,'cell_T_init') if g.cell_T_init is not None else g.bc_P.new_ones(g.N_cells)*g.T_fluid_init,
            'initial_solid_temperature':g.bc_P.new_ones(g.N_cp)*g.T_solid_init,
            'cell_volume':constant(g.cell_volume,g.N_cells,'cell_volume'),
            'cell_heat_W':sample(g.cell_heat_W,g.N_cells,'cell_heat_W'),
        }
        mapping = {
            'pipe':{'D':'pipe_D','L':'pipe_L','dh':'pipe_dh','A':'pipe_area','roughness':'pipe_roughness','K_minor':'pipe_minor'},
            'cp':{'dp_a':'cp_dp_a','dp_b':'cp_dp_b','UA':'cp_UA','C_solid':'cp_Csolid','Q_W':'heat_W'},
            'pump':{'H0':'pump_H0','a_coef':'pump_a','rpm_ratio':'pump_rpm_ratio'},
            'valve':{'Kv_max':'valve_Kv','opening':'valve_opening'},
            'hx':{'dp_a':'hx_dp_a','dp_b':'hx_dp_b','UA':'hx_UA'},
        }
        for kind,names in mapping.items():
            params = getattr(g,kind+'_params')
            count = len(self._ids[kind])
            for attr,name in names.items():
                fields[name] = sample(getattr(params,attr),count,name) if params is not None else g.bc_P.new_empty(0)
        for attr,name in (
            ('flow_kg_s','primary_flow_kg_s'),('inlet_K','primary_inlet_K'),
            ('cp_J_kgK','primary_cp'),('rho_kg_m3','primary_rho'),
            ('primary_volume_m3','primary_volume'),('secondary_volume_m3','secondary_volume'),
        ):
            fields[name] = constant(getattr(self.primary,attr),g.N_hx,name)
        # v4 endpoint forms; zeros where an element keeps the v3 physics.
        fields['pump_flow_set'] = sample(g.pump_flow_set,len(self._ids['pump']),'pump_flow_set')
        fields['cp_dT_set'] = sample(g.cp_dT_set,len(self._ids['cp']),'cp_dT_set')
        fields['hx_T_set'] = sample(g.hx_T_set,len(self._ids['hx']),'hx_T_set')
        fields['hx_T_set_rate'] = rate(g.hx_T_set,len(self._ids['hx']),'hx_T_set_rate')
        for name in ('setpoint','kp','ki','bias','output_min','output_max','tracking_s'):
            fields['controller_'+name] = constant(self._controller_constants[name],self.controller_count,'controller_'+name)
        if self._manufactured:
            for name,count in (('mms_momentum_Pa',g.N_edges),('mms_water_W',g.N_cells),('mms_solid_W',g.N_cp),('mms_hx_secondary_W',g.N_hx),('mms_hx_primary_W',g.N_hx)):
                fields[name]=g.bc_P.new_zeros(count)
        return fields

    def parameters(self, time_index=0, *, heat_W=None, pump_rpm_ratio=None,
                   primary_inlet_K=None, primary_flow_kg_s=None, pump_flow_set=None,
                   cp_dT_set=None, hx_T_set=None, hx_T_set_rate=None, pressure_bc=None) -> Tensor:
        """Pack all continuous inputs without detaching override tensors.

        Other coefficients are addressable through parameter_layout. The open/
        closed valve set is fixed for this adapter; changing it needs a new
        EquationGraph and a fresh pressure-reference check.
        """
        values = self._parameter_fields(time_index)
        for name,override in [('heat_W',heat_W),('pump_rpm_ratio',pump_rpm_ratio),
                              ('primary_inlet_K',primary_inlet_K),('primary_flow_kg_s',primary_flow_kg_s),
                              ('pump_flow_set',pump_flow_set),('cp_dT_set',cp_dT_set),('hx_T_set',hx_T_set),
                              ('hx_T_set_rate',hx_T_set_rate),('pressure_bc',pressure_bc)]:
            if override is not None:
                values[name] = _vector(override,values[name].numel(),name,device=self.device,dtype=self.dtype)
        theta=torch.cat(list(values.values()))
        self.validate_parameters(theta)
        return theta

    def validate_parameters(self,theta:Tensor) -> None:
        """Explicit admission boundary, never called from the tensor residual.

        All predicates are batched; valid input needs one host decision rather
        than one host read per parameter/component. Failure details synchronize
        only on the exceptional path.
        """
        values=self._theta(theta)
        checks=torch.stack((torch.isfinite(theta).all(),
            (theta[self._positive_indices]>0).all(),(theta[self._nonnegative_indices]>=0).all(),
            (values['valve_opening']<=1).all(),
            ((values['valve_opening']<=0)==self._closed_valves).all(),
            (values['hx_T_set'][self._hx_setpoint]>0).all(),
            (values['controller_output_min']<values['controller_output_max']).all(),
            ((values['controller_output_min']>0)|~self._ctl_valve).all(),
            ((values['controller_output_max']<=1)|~self._ctl_valve).all()))
        if bool(checks.all()):return
        messages=('theta must contain finite parameters',
                  'positive physical parameters required: '+', '.join(self._positive_names),
                  'nonnegative physical parameters required: '+', '.join(self._nonnegative_names),
                  'valve_opening must lie in [0,1]',
                  'valve active-set changes require a new EquationGraph',
                  'a held HX secondary supply temperature must be positive (K)',
                  'controller output_min must be below output_max',
                  'a valve controller needs output_min > 0 (fixed valve active set)',
                  'a valve controller output_max cannot exceed 1')
        failures=checks.detach().cpu().tolist()
        raise ValueError('; '.join(message for passed,message in zip(failures,messages) if not passed))

    def validate_state(self,y:Tensor,theta:Tensor) -> None:
        if self._manufactured and hasattr(self,'manufactured_signs'):
            if not bool((y[self.state_layout['flow']]*self.manufactured_signs>1e-8).all()):
                raise ValueError('manufactured flow crossed declared sign regime')
        """Accepted-root guard; a numerical dry-node gauge is not a reservoir."""
        state=self._state(y)
        self._theta(theta)
        actual=torch.where(self._thermal_active,state['flow'],torch.zeros_like(state['flow']))
        up=torch.where(actual>=0,self._from,self._to)
        down=torch.where(actual>=0,self._to,self._from)
        incoming=torch.zeros_like(state['node_temperature']).index_add(0,down,actual.abs())
        outgoing=torch.zeros_like(incoming).index_add(0,up,actual.abs())
        # A pressure datum can receive internal flow AND supply extra external
        # fluid. That net source still needs a declared reservoir temperature.
        # Ignore only accumulation roundoff, not an unmodelled physical source.
        flow_tolerance=1e-10+64*torch.finfo(y.dtype).eps*(incoming+outgoing)
        unspecified=self._bc_mask & ~self._bc_T_mask & (outgoing-incoming>flow_tolerance)
        checks=torch.stack((torch.isfinite(y).all(),~unspecified.any()))
        if not bool(checks.all()):
            flags=checks.detach().cpu().tolist()
            if not flags[0]:raise ValueError('accepted state must contain finite values')
            raise ValueError('an external pressure-boundary source requires an inlet temperature; a dry-node gauge is not a reservoir model')
        self._check_held_secondary_feasible(state,theta)

    def _check_held_secondary_feasible(self,state,theta) -> None:
        """Second law for a held secondary supply (report D4).

        Rejecting duty to the primary needs T_set >= primary inlet: no passive
        HX cools a stream below the coolant that cools it. The ideal set-point
        form would otherwise accept 21 kW flowing from 288 K into 293 K water
        without a message. Negative duty (the ideal controller heating the
        secondary, e.g. during a fast set-point rise) stays admitted: it is the
        documented ideal-control semantics of this form.
        """
        if not bool(self._hx_setpoint.any()):return
        duty,inlet,held,duty_tolerance,temperature_tolerance=self._held_secondary_duty(state,self._theta(theta))
        ids=self._ids['hx']
        bad=self._hx_setpoint & (duty>duty_tolerance) & (held<inlet-temperature_tolerance)
        if not bool(bad.any()):return
        rows=[]
        for local in torch.nonzero(bad).flatten().tolist():
            edge=self.graph.edge_str_ids[int(ids[local])]
            q,t_set,t_in=float(duty[local]),float(held[local]),float(inlet[local])
            rows.append(f"heat exchanger '{edge}': held secondary supply T_set={t_set:.2f} K is below the primary inlet "
                        f"{t_in:.2f} K while rejecting {q:.1f} W to the primary")
        raise ValueError('; '.join(rows)+'. A passive HX cannot cool the secondary below its coolant (second law): '
                         'raise T_set_K to at least the primary inlet temperature, or model a chiller explicitly.')

    def _held_secondary_duty(self,state,p):
        """Duty a held secondary sends to the primary [W] (negative = taken from it)."""
        _,weight,up,_,_,_,_,_=self._thermal_fields(state,p)
        ids=self._ids['hx']
        duty=(weight[ids]*p['fluid_cp']*(state['node_temperature'][up[ids]]-state['hx_secondary_temperature'])
              -p['fluid_rho']*p['fluid_cp']*p['secondary_volume']*p['hx_T_set_rate'])
        inlet,held=p['primary_inlet_K'],p['hx_T_set']
        duty_tolerance=1e-9*(1+duty.abs().max()) if duty.numel() else duty.new_zeros(())
        return duty,inlet,held,duty_tolerance,1e-9*(1+inlet.abs())

    def held_secondary_ideal_heating(self,y:Tensor,theta:Tensor) -> tuple[Tensor,Tensor]:
        """Per HX: is the held secondary being heated above the primary inlet?

        Such a duty flows from the primary into a secondary held hotter than
        the primary inlet, so the primary leaves colder than it came: no
        passive exchanger delivers it. The set-point form admits it as ideal
        control (fast set-point rises need it), and accepted states report it
        here instead of hiding it (report D4). Returns (flags, duty in W).
        """
        if not bool(self._hx_setpoint.any()):
            zeros=torch.zeros(len(self._ids['hx']),dtype=self.dtype,device=self.device)
            return zeros.bool(),zeros
        duty,inlet,held,duty_tolerance,temperature_tolerance=self._held_secondary_duty(self._state(y),self._theta(theta))
        return self._hx_setpoint & (duty<-duty_tolerance) & (held>inlet+temperature_tolerance),duty

    def coolant_temperatures(self,y:Tensor) -> list[tuple[str,Tensor]]:
        """Physical coolant temperatures of a state, labelled (gauges excluded):
        observable nodes, cells of flowing pipes, both HX volumes."""
        state=self._state(y)
        observable=self.observable_mask(y)[self.state_layout['node_temperature']]
        cells=self._thermal_active[self._cells]
        return [('node',torch.where(observable,state['node_temperature'],torch.full_like(state['node_temperature'],float('nan')))),
                ('pipe cell',torch.where(cells,state['water_temperature'],torch.full_like(state['water_temperature'],float('nan')))),
                ('HX secondary volume',state['hx_secondary_temperature']),
                ('HX primary return',state['primary_return_temperature'])]

    def _pressure_topology_and_blind_edges(self) -> Tensor:
        adjacency = [set() for _ in range(self.graph.N_nodes)]
        closed=self._closed_edges.detach().cpu().tolist()
        for edge,(a,b) in enumerate(zip(self._host_from,self._host_to)):
            if not closed[edge]:
                adjacency[a].add(edge)
                adjacency[b].add(edge)
        references=[i for i,value in enumerate(self._host_bc) if value]
        reached, pending = set(references),deque(references)
        while pending:
            node = pending.popleft()
            for edge in adjacency[node]:
                other=self._host_to[edge] if self._host_from[edge]==node else self._host_from[edge]
                if other not in reached:
                    reached.add(other)
                    pending.append(other)
        if len(reached) != self.graph.N_nodes:
            raise ValueError('every open-edge component requires a pressure reference; floating topology is unsupported')
        # Exact-zero-flow edges: dangling trees AND driverless/bridge blocks.
        # Keep their hydraulic equations and storage; suppress only the
        # mathematically zero advective transport (including numerical residue).
        drivers=[False]*self.graph.N_edges
        for edge in self._ids['pump'].detach().cpu().tolist():
            drivers[edge]=True
        dead=stagnant_edges_by_blocks(self.graph.N_nodes,self._host_from,self._host_to,[not value for value in closed],
                                      references,drivers)
        return torch.tensor(dead,dtype=torch.bool,device=self.device)

    def check_steady_exists(self,theta:Tensor) -> None:
        """Explain, before Newton, why a steady state cannot exist (report S2).

        Heat into a stagnant cold plate (or pipe cell) has nowhere to go: its
        temperature rises without bound, so F(y,0,theta)=0 has no root. The
        error names the plates, the closed valves that isolate them and the
        transient alternative instead of a generic line-search failure.
        """
        p=self._theta(theta)
        cp_ids=self._ids['cp']
        rise=self._cp_rise
        heated_cp=self._dead_edges[cp_ids]&~rise&(p['heat_W']>0)
        heated_cell=self._dead_edges[self._cells]&(p['cell_heat_W']!=0)
        if not bool(heated_cp.any() or heated_cell.any()):
            self._check_heat_sink(p)
            return
        names=self.graph.edge_str_ids
        plates=[(names[int(cp_ids[i])],float(p['heat_W'][i])) for i in torch.nonzero(heated_cp).flatten().tolist()]
        cells=sorted({names[int(self._cells[i])] for i in torch.nonzero(heated_cell).flatten().tolist()})
        # The stagnant region(s) holding them, and the closed valves at their border.
        dead=self._dead_edges.detach().cpu().tolist();closed=self._closed_edges.detach().cpu().tolist()
        parent=list(range(self.graph.N_nodes))
        def find(node):
            while parent[node]!=node:
                parent[node]=parent[parent[node]];node=parent[node]
            return node
        for edge,(a,b) in enumerate(zip(self._host_from,self._host_to)):
            if dead[edge]:parent[find(a)]=find(b)
        index={name:i for i,name in enumerate(names)}
        regions={find(self._host_from[index[name]]) for name,_ in plates}|{find(self._host_from[index[name]]) for name in cells}
        valves=sorted(names[edge] for edge,(a,b) in enumerate(zip(self._host_from,self._host_to))
                      if closed[edge] and (find(a) in regions or find(b) in regions))
        parts=[]
        if plates:parts.append('cold plate(s) '+', '.join(f"'{name}' ({heat:.6g} W)" for name,heat in plates))
        if cells:parts.append('heated pipe(s) '+', '.join(f"'{name}'" for name in cells))
        cause=(f"isolated by closed valve(s) {', '.join(repr(v) for v in valves)}" if valves else
               'no pump or pressure difference drives flow through them')
        raise ValueError('no steady state exists: '+' and '.join(parts)+
                         f' sit in a stagnant region with no coolant flow ({cause}), so their temperature rises '
                         'without bound. Open the valve(s) or set that heat to 0 for a steady analysis, or run a '
                         "transient with analysis.initialization='consistent' to follow the heat-up.")

    def _check_heat_sink(self,p) -> None:
        """Every flowing coolant network needs a heat sink for a unique steady state.

        A network (thermally active edges and their nodes) whose exchangers all
        have zero primary flow and no set point, and that holds no temperature
        boundary, keeps every joule it receives. Heated, its temperature grows
        without bound (no root); unheated, every uniform temperature is a root,
        and the dense Newton solve either fails on a singular Jacobian or
        returns an arbitrary level. Both are said by name before Newton.
        """
        active=self._thermal_active.detach().cpu().tolist()
        parent=list(range(self.graph.N_nodes))
        def find(node):
            while parent[node]!=node:
                parent[node]=parent[parent[node]];node=parent[node]
            return node
        for edge,(a,b) in enumerate(zip(self._host_from,self._host_to)):
            if active[edge]:parent[find(a)]=find(b)
        names=self.graph.edge_str_ids
        hx_ids=self._ids['hx'].detach().cpu().tolist()
        sink_hx=(self._hx_setpoint|(p['primary_flow_kg_s']>0)).detach().cpu().tolist()
        bc_T=self._bc_T_mask.detach().cpu().tolist()
        networks={}
        for edge,(a,b) in enumerate(zip(self._host_from,self._host_to)):
            if active[edge]:networks.setdefault(find(a),{'edges':[],'sink':False})['edges'].append(edge)
        for node,fixed in enumerate(bc_T):
            if fixed and find(node) in networks:networks[find(node)]['sink']=True
        for local,edge in enumerate(hx_ids):
            if active[edge] and sink_hx[local]:networks[find(self._host_from[edge])]['sink']=True
        cp_ids=self._ids['cp'].detach().cpu().tolist()
        plate_heat=torch.where(self._cp_rise,p['cp_dT_set'],p['heat_W']).detach().cpu().tolist()
        cell_heat=p['cell_heat_W'].detach().cpu().tolist();cells=self._cells.detach().cpu().tolist()
        for root,network in networks.items():
            if network['sink']:continue
            members=set(network['edges'])
            hx=[names[edge] for edge in hx_ids if edge in members]
            heat=sum(q for edge,q in zip(cp_ids,plate_heat) if edge in members and q!=0)
            heat+=sum(q for cell,q in zip(cells,cell_heat) if cell in members and q!=0)
            where=(f"the coolant network through heat exchanger(s) {', '.join(repr(h) for h in hx)}" if hx else
                   f"the coolant network through {names[network['edges'][0]]!r}")
            cause=('every exchanger it reaches has a primary flow of 0 kg/s and no held secondary supply'
                   if hx else 'it reaches no heat exchanger') + ', and it has no temperature boundary'
            if heat:
                raise ValueError(f'no steady state exists: {where} has no heat sink ({cause}), so the heat put '
                                 'into it raises its temperature without bound. Give an exchanger a primary '
                                 "flow, or run a transient with analysis.initialization='consistent' to follow "
                                 'the heat-up.')
            raise ValueError(f'the steady state is not unique: {where} has no heat sink ({cause}) and no heat '
                             'input, so every uniform temperature is a steady state. Give an exchanger a '
                             "primary flow, or run a transient with analysis.initialization='consistent' from "
                             'the stored temperatures.')

    def _theta(self,theta: Tensor) -> dict[str,Tensor]:
        if theta.shape != (self.parameter_count,):
            raise ValueError(f'theta must have shape [{self.parameter_count}]')
        if theta.device!=self.device or theta.dtype!=self.dtype:
            raise ValueError('theta device/dtype must match EquationGraph')
        return {name:theta[indices] for name,indices in self.parameter_layout.items()}

    def _state(self,y:Tensor) -> dict[str,Tensor]:
        if y.shape != (self.size,):
            raise ValueError(f'y must have shape [{self.size}]')
        if y.device!=self.device or y.dtype!=self.dtype:
            raise ValueError('state device/dtype must match EquationGraph')
        return {name:y[indices] for name,indices in self.state_layout.items()}

    def _pressure(self,state,theta):
        return theta['pressure_bc'].index_copy(0,self._free,state['pressure_free'])

    def _thermal_fields(self,state,theta):
        m,node = state['flow'],state['node_temperature']
        actual = torch.where(self._thermal_active,m,torch.zeros_like(m))
        weight = actual.abs()
        up = torch.where(actual >= 0,self._from,self._to)
        down = torch.where(actual >= 0,self._to,self._from)
        outlet = node[up]
        pipe_ids,cp_ids,hx_ids = self._ids['pipe'],self._ids['cp'],self._ids['hx']
        if len(pipe_ids):
            pipe_out = torch.where(actual[pipe_ids] >= 0,state['water_temperature'][self._pipe_last],state['water_temperature'][self._pipe_first])
            outlet = outlet.index_copy(0,pipe_ids,pipe_out)
        adv = weight[cp_ids]*theta['fluid_cp']
        safe = torch.where(adv > 0,adv,torch.ones_like(adv))
        effectiveness = torch.where(adv > 0,-torch.expm1(-theta['cp_UA']/safe),torch.zeros_like(adv))
        cp_heat = adv*effectiveness*(state['solid_temperature']-node[up[cp_ids]])
        cp_out = node[up[cp_ids]]+effectiveness*(state['solid_temperature']-node[up[cp_ids]])
        outlet = outlet.index_copy(0,cp_ids,cp_out)
        outlet = outlet.index_copy(0,hx_ids,state['hx_secondary_temperature'])
        incoming = torch.zeros_like(node).index_add(0,down,weight)
        observable = self._bc_T_mask | (incoming > 0)
        return actual,weight,up,down,outlet,cp_heat,incoming,observable

    def observable_mask(self,y:Tensor) -> Tensor:
        state = self._state(y)
        actual = torch.where(self._thermal_active,state['flow'],torch.zeros_like(state['flow']))
        down = torch.where(actual >= 0,self._to,self._from)
        incoming = torch.zeros_like(state['node_temperature']).index_add(0,down,actual.abs())
        result = torch.ones(self.size,dtype=torch.bool,device=self.device)
        result[self.state_layout['node_temperature']] = self._bc_T_mask | (incoming > 0)
        return result

    def unpack(self,y:Tensor,theta:Tensor|None=None) -> dict[str,Tensor]:
        state = self._state(y)
        state['observable_mask'] = self.observable_mask(y)
        state['node_temperature_observable'] = state['observable_mask'][self.state_layout['node_temperature']]
        if theta is not None:
            values = self._theta(theta)
            state['pressure'] = self._pressure(state,values)
            state['edge_outlet_temperature'] = self._thermal_fields(state,values)[4]
        return state

    def initial_guess(self,theta:Tensor) -> Tensor:
        values = self._theta(theta)
        flow=theta.new_full((self.graph.N_edges,),.01)
        flow = torch.where(self._thermal_active,flow,torch.zeros_like(flow))
        node = torch.where(self._bc_T_mask,values['temperature_bc'],values['node_gauge_temperature'])
        secondary = torch.where(self._hx_setpoint,values['hx_T_set'],node[self._to[self._ids['hx']]])
        # The integral starts where the actuator's own (scheduled) value is
        # reached at zero error, so a loop that starts on its set point starts
        # bumpless; the proportional term then acts on the initial error.
        integral=self._actuator_nominal(values)-values['controller_bias']
        return torch.cat((values['pressure_bc'][self._free],flow,node,
                          values['initial_water_temperature'],values['initial_solid_temperature'],
                          secondary,values['primary_inlet_K'],integral))

    def _plate_heat(self,weight:Tensor,p:dict[str,Tensor]) -> Tensor:
        """Heat into each cold-plate solid: prescribed power, or for a
        temperature-rise plate |m|·cp·dT_set (no flow, no heat)."""
        derived=weight[self._ids['cp']]*p['fluid_cp']*p['cp_dT_set']
        return torch.where(self._cp_rise,derived,p['heat_W'])

    def residual(self,t:Tensor,y:Tensor,ydot:Tensor,theta:Tensor) -> Tensor:
        """Continuous residual; time histories enter through explicit theta(t)."""
        del t  # no hidden time integrator: this is the continuous operator
        s,d,p = self._state(y),self._state(ydot),self._theta(theta)
        m = s['flow']
        pressure = self._pressure(s,p)
        rho,cp,mu,gravity = p['fluid_rho'],p['fluid_cp'],p['fluid_mu'],p['gravity']
        if self.controller_count:
            output,raw,error,_ = self._controller_signals(s,p,pressure)
            p = {**p,**self._actuated(p,output)}
            controller_residual = d['controller_integral']-(p['controller_ki']*error+(output-raw)/p['controller_tracking_s'])
        else:
            controller_residual = y.new_zeros(0)
        dp = torch.zeros_like(m)
        ids = self._ids['pipe']
        if len(ids):
            pipe_dp = _pipe_dp_friction(m[ids],p['pipe_D'],p['pipe_L'],p['pipe_area'],p['pipe_minor'],p['pipe_roughness'],rho,mu)
            dp = dp.index_copy(0,ids,pipe_dp+rho*gravity*p['pipe_dh'])
        for kind in ('cp','hx'):
            ids = self._ids[kind]
            losses,_ = quadratic_headloss(m[ids],p[kind+'_dp_a'],p[kind+'_dp_b'])
            dp = dp.index_copy(0,ids,losses)
        ids = self._ids['pump']
        losses,_ = pump_headloss(m[ids],p['pump_H0'],p['pump_a'],p['pump_rpm_ratio'],rho,gravity)
        dp = dp.index_copy(0,ids,losses)
        ids = self._ids['valve']
        kv = effective_valve_kv(p['valve_Kv'],p['valve_opening'],self._characteristic)
        losses,_ = valve_headloss(m[ids],kv,rho)
        dp = dp.index_copy(0,ids,losses)
        if self._has_component_dh:
            dp = dp+rho*gravity*self._component_dh
        held = torch.zeros_like(m).index_copy(0,self._ids['pump'],p['pump_flow_set'])
        momentum = torch.where(self._closed_edges,m,torch.where(self._flow_edges,m-held,dp-pressure[self._from]+pressure[self._to]))
        mass=torch.zeros_like(s['node_temperature']).index_add(0,self._from,m).index_add(0,self._to,-m)[self._free]

        actual,weight,up,down,outlet,cp_heat,incoming,_ = self._thermal_fields(s,p)
        received = torch.zeros_like(s['node_temperature']).index_add(0,down,weight*cp*outlet)
        mixed = incoming*cp*s['node_temperature']-received
        gauge = self._node_gauge_conductance*(s['node_temperature']-p['node_gauge_temperature'])
        fixed = self._node_gauge_conductance*(s['node_temperature']-p['temperature_bc'])
        node_residual = torch.where(self._bc_T_mask,fixed,torch.where(incoming > 0,mixed,gauge))

        cell_flow = actual[self._cells]
        water = s['water_temperature']
        upstream_forward = torch.where(self._first,s['node_temperature'][self._from[self._cells]],water[self._previous])
        upstream_reverse = torch.where(self._last,s['node_temperature'][self._to[self._cells]],water[self._next])
        upstream_temperature = torch.where(cell_flow >= 0,upstream_forward,upstream_reverse)
        water_residual = rho*cp*p['cell_volume']*d['water_temperature']-cell_flow.abs()*cp*(upstream_temperature-water)-p['cell_heat_W']
        solid_residual = p['cp_Csolid']*d['solid_temperature']-self._plate_heat(weight,p)+cp_heat

        ids = self._ids['hx']
        secondary,primary = s['hx_secondary_temperature'],s['primary_return_temperature']
        secondary_capacity = rho*cp*p['secondary_volume']
        primary_capacity = p['primary_rho']*p['primary_cp']*p['primary_volume']
        inflow = weight[ids]*cp*(s['node_temperature'][up[ids]]-secondary)
        # Physics: UA exchange between the two mixed volumes, or (hxModel
        # counterflow-ntu) the counterflow epsilon-NTU duty from the two inlet
        # temperatures plus UA on the volumes' departures from their steady
        # counterflow outlets. Set point: the secondary is held at T_set and the duty
        # is whatever the secondary energy balance needs (ideal control),
        # C_s·dT_set/dt included.
        physical = p['hx_UA']*(secondary-primary)
        if self._any_counterflow:
            duty = self._counterflow_duty(weight[ids]*cp,p['primary_flow_kg_s']*p['primary_cp'],p['hx_UA'],
                                          s['node_temperature'][up[ids]],p['primary_inlet_K'],secondary,primary)
            physical = torch.where(self._hx_counterflow,duty,physical)
        exchange = torch.where(self._hx_setpoint,inflow-secondary_capacity*p['hx_T_set_rate'],physical)
        secondary_residual = torch.where(self._hx_setpoint,self._node_gauge_conductance*(secondary-p['hx_T_set']),
                                         secondary_capacity*d['hx_secondary_temperature']-inflow+exchange)
        primary_residual = primary_capacity*d['primary_return_temperature']-p['primary_flow_kg_s']*p['primary_cp']*(p['primary_inlet_K']-primary)-exchange
        if self._manufactured:
            momentum=momentum-p['mms_momentum_Pa']
            water_residual=water_residual-p['mms_water_W']
            solid_residual=solid_residual-p['mms_solid_W']
            secondary_residual=secondary_residual-p['mms_hx_secondary_W']
            primary_residual=primary_residual-p['mms_hx_primary_W']
        return torch.cat((mass,momentum,node_residual,water_residual,solid_residual,secondary_residual,primary_residual,
                          controller_residual))

    # ------------------------------------------------------------ control ---
    def _compile_controllers(self,controllers) -> None:
        """Index maps of the PI loops (CONTROLLER_LAW); all empty without one."""
        g=self.graph
        node_index={name:i for i,name in enumerate(g.node_ids)}
        edge_index={name:i for i,name in enumerate(g.edge_str_ids)}
        local={kind:{int(e):i for i,e in enumerate(self._ids[kind].detach().cpu().tolist())} for kind in ('cp','pump','valve')}
        kinds={'node_temperature':[],'solid_temperature':[],'pressure_difference':[],'mass_flow':[]}
        pump_rows,pump_local,valve_rows,valve_local=[],[],[],[]
        constants={name:[] for name in ('setpoint','kp','ki','bias','output_min','output_max','tracking_s')}
        direction,ids=[],[]
        for row,control in enumerate(controllers or ()):
            control=control.model_dump() if hasattr(control,'model_dump') else dict(control)
            measurement,actuator,pid=control['measurement'],control['actuator'],control['pid']
            kind,target=measurement['kind'],measurement['target_id']
            if kind=='node_temperature':
                kinds[kind].append((row,node_index[target],0))
            elif kind=='pressure_difference':
                kinds[kind].append((row,node_index[target],node_index[measurement['reference_id']]))
            elif kind=='solid_temperature':
                kinds[kind].append((row,local['cp'][edge_index[target]],0))
            elif kind=='mass_flow':
                kinds[kind].append((row,edge_index[target],0))
            else:
                raise ValueError(f"controller {control['id']}: unknown measurement kind {kind!r}")
            edge=edge_index[actuator['edge_id']]
            if actuator['field']=='rpm_ratio':
                pump=local['pump'][edge]
                if bool(self._pump_flow[pump]):
                    raise ValueError(f"controller {control['id']}: pump {actuator['edge_id']} holds a flow set point")
                pump_rows.append(row);pump_local.append(pump)
            else:
                valve=local['valve'][edge]
                valve_rows.append(row);valve_local.append(valve)
            kp,ki=float(pid['kp']),float(pid['ki'])
            for name,value in (('setpoint',pid['setpoint']),('kp',kp),('ki',ki),('bias',pid['bias']),
                               ('output_min',pid['output_min']),('output_max',pid['output_max']),
                               ('tracking_s',max(kp/ki if ki>0 else 0.,float(g.dt)))):
                constants[name].append(float(value))
            direction.append(1. if pid.get('action','reverse')=='reverse' else -1.)
            ids.append(control['id'])
        def long(values):return torch.tensor(values,dtype=torch.long,device=self.device)
        self.controller_ids=tuple(ids)
        self.controller_count=len(ids)
        self._controller_constants=constants
        self._ctl_measure={kind:(long([r for r,_,_ in rows]),long([a for _,a,_ in rows]),long([b for _,_,b in rows]))
                           for kind,rows in kinds.items() if rows}
        self._ctl_direction=torch.tensor(direction,dtype=self.dtype,device=self.device)
        self._ctl_pump_rows,self._ctl_pump_local=long(pump_rows),long(pump_local)
        self._ctl_valve_rows,self._ctl_valve_local=long(valve_rows),long(valve_local)
        self._ctl_valve=torch.zeros(len(ids),dtype=torch.bool,device=self.device)
        self._ctl_valve[self._ctl_valve_rows]=True
        self.controlled_actuators={('pump',int(k)) for k in pump_local}|{('valve',int(k)) for k in valve_local}

    def _actuator_nominal(self,p) -> Tensor:
        """The actuators' own parameter values, one per controller."""
        nominal=torch.zeros(self.controller_count,dtype=self.dtype,device=self.device)
        nominal=nominal.index_copy(0,self._ctl_pump_rows,p['pump_rpm_ratio'][self._ctl_pump_local])
        return nominal.index_copy(0,self._ctl_valve_rows,p['valve_opening'][self._ctl_valve_local])

    def _controller_signals(self,s,p,pressure):
        """(applied output, unclipped output, error, measurement) per controller."""
        measured=torch.zeros(self.controller_count,dtype=self.dtype,device=self.device)
        for kind,(rows,a,b) in self._ctl_measure.items():
            value={'node_temperature':lambda:s['node_temperature'][a],
                   'solid_temperature':lambda:s['solid_temperature'][a],
                   'pressure_difference':lambda:pressure[a]-pressure[b],
                   'mass_flow':lambda:s['flow'][a]}[kind]()
            measured=measured.index_copy(0,rows,value)
        error=self._ctl_direction*(p['controller_setpoint']-measured)
        raw=p['controller_bias']+p['controller_kp']*error+s['controller_integral']
        output=torch.minimum(torch.maximum(raw,p['controller_output_min']),p['controller_output_max'])
        return output,raw,error,measured

    def _actuated(self,p,output) -> dict[str,Tensor]:
        """The pump speeds and valve openings with the controlled ones replaced."""
        return {'pump_rpm_ratio':p['pump_rpm_ratio'].index_copy(0,self._ctl_pump_local,output[self._ctl_pump_rows]),
                'valve_opening':p['valve_opening'].index_copy(0,self._ctl_valve_local,output[self._ctl_valve_rows])}

    def controller_report(self,y:Tensor,theta:Tensor) -> dict[str,dict[str,float]]:
        """Per controller: applied output, measurement, error, saturation."""
        if not self.controller_count:
            return {}
        s,p=self._state(y),self._theta(theta)
        output,raw,error,measured=self._controller_signals(s,p,self._pressure(s,p))
        rows=zip(self.controller_ids,output.detach().cpu().tolist(),raw.detach().cpu().tolist(),
                 error.detach().cpu().tolist(),measured.detach().cpu().tolist())
        return {name:{'output':u,'measurement':v,'error':e,'saturated':bool(u!=r)} for name,u,r,e,v in rows}

    def applied_actuators(self,y:Tensor,theta:Tensor) -> dict[str,Tensor]:
        """Pump speeds and valve openings the residual actually used at ``y``."""
        p=self._theta(theta)
        if not self.controller_count:
            return {'pump_rpm_ratio':p['pump_rpm_ratio'],'valve_opening':p['valve_opening']}
        s=self._state(y)
        output=self._controller_signals(s,p,self._pressure(s,p))[0]
        return self._actuated(p,output)

    @staticmethod
    def _counterflow_duty(secondary_capacity_rate,primary_capacity_rate,UA,secondary_inlet,primary_inlet,
                          secondary,primary):
        """Counterflow epsilon-NTU duty with hold-up exchange [W].

        With NTU=UA/C_min, C_r=C_min/C_max and x=NTU*(1-C_r),
        eps=(1-e^-x)/(1-C_r*e^-x) = NTU*phi/(NTU*phi+e^-x), phi=(1-e^-x)/x,
        which reaches NTU/(1+NTU) at C_r=1 without a 0/0.

        Q = eps*C_min*(T_s,in-T_p,in) + UA*[(T_s-T_s*)-(T_p-T_p*)], where
        T_s* = T_s,in-a*dT_in and T_p* = T_p,in+b*dT_in are the steady
        counterflow outlets (a = eps*C_min/C_s, b = eps*C_min/C_p). At any
        steady state with flow the volumes sit at those outlets, the second
        term is zero and the duty is exactly the counterflow exchanger. In a
        transient the volumes' departures from those outlets exchange through
        UA as two mixed volumes do. A side without flow reaches the other
        inlet (a or b = 1), so with a stagnant side (a pump trip, a primary
        flow of 0) the exchanger is exactly the mixed-volumes model,
        UA*(T_s-T_p): a stagnant hold-up keeps exchanging instead of freezing.
        The double torch.where keeps gradients finite at zero flow.
        """
        c_s,c_p=secondary_capacity_rate,primary_capacity_rate
        c_min=torch.minimum(c_s,c_p)
        c_max=torch.maximum(c_s,c_p)
        flowing=c_min>0
        one=torch.ones_like(c_min)
        c_min_safe=torch.where(flowing,c_min,one)
        c_max_safe=torch.where(flowing,c_max,one)
        ntu=UA/c_min_safe
        x=ntu*(1-c_min_safe/c_max_safe)
        small=x<1e-6
        x_safe=torch.where(small,torch.ones_like(x),x)
        phi=torch.where(small,1-x/2+x*x/6,-torch.expm1(-x_safe)/x_safe)
        eps=ntu*phi/(ntu*phi+torch.exp(-x))
        # Fraction of the inlet difference each side covers at steady state.
        # Without flow on a side it reaches the other inlet; with no flow at
        # all a+b=1 leaves UA*(T_s-T_p) between the two stagnant volumes.
        a=torch.where(flowing,eps*c_min_safe/torch.where(flowing,c_s,one),torch.where(c_s>0,torch.zeros_like(one),one))
        b=torch.where(flowing,eps*c_min_safe/torch.where(flowing,c_p,one),torch.where(c_s>0,one,torch.zeros_like(one)))
        inlet_difference=secondary_inlet-primary_inlet
        duty=torch.where(flowing,eps*c_min_safe*inlet_difference,torch.zeros_like(c_min))
        return duty+UA*((secondary-primary)-(1-a-b)*inlet_difference)

    def diagnostics(self,t,y,ydot,theta):
        """Thermal balance in W, not a scaled nonlinear-solver norm."""
        s,d,p=self._state(y),self._state(ydot),self._theta(theta)
        residual=self.residual(t,y,ydot,theta)
        _,weight,up,down,outlet,_,_,_=self._thermal_fields(s,p)
        outgoing=torch.zeros_like(s['node_temperature']).index_add(0,up,weight)
        received=torch.zeros_like(s['node_temperature']).index_add(0,down,weight*p['fluid_cp']*outlet)
        boundary=(outgoing*p['fluid_cp']*s['node_temperature']-received)[self._boundary_nodes].sum()
        storage=(p['fluid_rho']*p['fluid_cp']*p['cell_volume']*d['water_temperature']).sum()
        storage=storage+(p['cp_Csolid']*d['solid_temperature']).sum()
        secondary_rate=torch.where(self._hx_setpoint,p['hx_T_set_rate'],d['hx_secondary_temperature'])
        storage=storage+(p['fluid_rho']*p['fluid_cp']*p['secondary_volume']*secondary_rate).sum()
        storage=storage+(p['primary_rho']*p['primary_cp']*p['primary_volume']*d['primary_return_temperature']).sum()
        source=self._plate_heat(weight,p).sum()+p['cell_heat_W'].sum()
        rejection=(p['primary_flow_kg_s']*p['primary_cp']*(s['primary_return_temperature']-p['primary_inlet_K'])).sum()
        def norm(values):return values.abs().max() if values.numel() else y.new_zeros(())
        extra={}
        if self._manufactured:
            external=sum(p[name].sum() for name in ('mms_water_W','mms_solid_W','mms_hx_secondary_W','mms_hx_primary_W'))
            extra={'physical_heat_input_W':source,'manufactured_heat_W':external,'manufactured_momentum_inf_Pa':norm(p['mms_momentum_Pa'])}
            source=source+external
        return {**extra,'heat_input_W':source,'primary_rejection_W':rejection,'storage_rate_W':storage,
                'boundary_exchange_W':boundary,'energy_balance_W':source+boundary-storage-rejection,
                'mass_residual_kg_s':norm(residual[self.state_layout['pressure_free']]),
                'momentum_residual_Pa':norm(residual[self.state_layout['flow']]),
                'scaled_residual':norm(residual/self.residual_scale)}
