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
from backend.liquidcooling.handlers.valve import effective_valve_kv
from backend.liquidcooling.solver.newton_hydraulic import (
    _pipe_dp_friction, pump_headloss, quadratic_headloss, valve_headloss,
)

if TYPE_CHECKING:
    from backend.liquidcooling.solver.dae_newton import DAEProblem


@dataclass(frozen=True)
class PrimaryCircuit:
    """Explicit per-HX boundary/properties; `source` must identify their origin.

    All fields are vectors [N_hx], in the same order as graph.hx_indices.
    Both volumes must be positive. UA is the graph's hx_params.UA; it is the
    conductance between two perfectly mixed volumes, not a counterflow NTU fit.
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


class EquationGraph:
    """Adapter exposing one F(t,y,ydot,theta) for steady and transient Newton."""

    def __init__(self, graph: CompiledGraph, primary: PrimaryCircuit, *, device=None,dtype=None):
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

        self.state_layout, self.size = _layout({
            'pressure_free':len(self._free), 'flow':graph.N_edges,
            'node_temperature':graph.N_nodes, 'water_temperature':graph.N_cells,
            'solid_temperature':graph.N_cp, 'hx_secondary_temperature':graph.N_hx,
            'primary_return_temperature':graph.N_hx,
        })
        self.differential_mask = torch.zeros(self.size,dtype=torch.bool,device=self.device)
        for name in ('water_temperature','solid_temperature','hx_secondary_temperature','primary_return_temperature'):
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
                             'cp_Csolid','primary_inlet_K','primary_cp','primary_rho','primary_volume','secondary_volume')
        self._nonnegative_names=('heat_W','pump_rpm_ratio','valve_Kv','valve_opening','primary_flow_kg_s','cp_UA','hx_UA',
                                 'pump_flow_set','cp_dT_set')
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
        self.provenance = {'primary_circuit':primary.source,'hx_model':'two_mixed_fluid_volumes',
                           'cold_plate_fluid_model':'quasisteady_NTU_no_coolant_storage',
                           'dry_node_policy':'numerical_temperature_gauge_unobservable'}

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
            (values['hx_T_set'][self._hx_setpoint]>0).all()))
        if bool(checks.all()):return
        messages=('theta must contain finite parameters',
                  'positive physical parameters required: '+', '.join(self._positive_names),
                  'nonnegative physical parameters required: '+', '.join(self._nonnegative_names),
                  'valve_opening must lie in [0,1]',
                  'valve active-set changes require a new EquationGraph',
                  'a held HX secondary supply temperature must be positive (K)')
        failures=checks.detach().cpu().tolist()
        raise ValueError('; '.join(message for passed,message in zip(failures,messages) if not passed))

    def validate_state(self,y:Tensor,theta:Tensor) -> None:
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
        if bool(checks.all()):return
        flags=checks.detach().cpu().tolist()
        if not flags[0]:raise ValueError('accepted state must contain finite values')
        raise ValueError('an external pressure-boundary source requires an inlet temperature; a dry-node gauge is not a reservoir model')

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
        # With zero external mass at free nodes, every dangling tree is exactly
        # stagnant. Keep its hydraulic equations and storage, suppress only the
        # mathematically zero advective transport (including numerical residue).
        dead=[False]*self.graph.N_edges
        leaves = deque(n for n,edges in enumerate(adjacency) if not self._host_bc[n] and len(edges) == 1)
        while leaves:
            node = leaves.popleft()
            if self._host_bc[node] or len(adjacency[node]) != 1:
                continue
            edge = next(iter(adjacency[node]))
            dead[edge] = True
            a,b=self._host_from[edge],self._host_to[edge]
            adjacency[a].discard(edge)
            adjacency[b].discard(edge)
            other = b if a == node else a
            if not self._host_bc[other] and len(adjacency[other]) == 1:
                leaves.append(other)
        return torch.tensor(dead,dtype=torch.bool,device=self.device)

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
        return torch.cat((values['pressure_bc'][self._free],flow,node,
                          values['initial_water_temperature'],values['initial_solid_temperature'],
                          secondary,values['primary_inlet_K']))

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
        # Physics: UA exchange between the two mixed volumes. Set point: the
        # secondary is held at T_set and the duty is whatever the secondary
        # energy balance needs (ideal control), C_s·dT_set/dt included.
        exchange = torch.where(self._hx_setpoint,inflow-secondary_capacity*p['hx_T_set_rate'],p['hx_UA']*(secondary-primary))
        secondary_residual = torch.where(self._hx_setpoint,self._node_gauge_conductance*(secondary-p['hx_T_set']),
                                         secondary_capacity*d['hx_secondary_temperature']-inflow+exchange)
        primary_residual = primary_capacity*d['primary_return_temperature']-p['primary_flow_kg_s']*p['primary_cp']*(p['primary_inlet_K']-primary)-exchange
        return torch.cat((mass,momentum,node_residual,water_residual,solid_residual,secondary_residual,primary_residual))

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
        return {'heat_input_W':source,'primary_rejection_W':rejection,'storage_rate_W':storage,
                'boundary_exchange_W':boundary,'energy_balance_W':source+boundary-storage-rejection,
                'mass_residual_kg_s':norm(residual[self.state_layout['pressure_free']]),
                'momentum_residual_Pa':norm(residual[self.state_layout['flow']]),
                'scaled_residual':norm(residual/self.residual_scale)}
