"""Numerical DAE experiment host: accepted steps, input changes and equilibrium.

Consumes a validated DAE input definition. No geometry/renderer/HTTP concerns.
Changing an input affects the NEXT step, never historical samples. Equilibrium
is a separate analysis and does not introduce a fictitious transient state jump.
Tensor histories retain differentiability; presentation snapshots are copies.
"""
import copy
import math
from dataclasses import asdict
from numbers import Real
import torch
from backend.liquidcooling.solver.dae_newton import (
    solve_steady,backward_euler_step,consistent_initialize,
)


class DAEExperiment:
    def __init__(self,definition,*,record_frames=True):
        # Training/optimization may keep every state on its execution device.
        # The interactive host opts into presentation snapshots at step edges.
        self.record_frames=record_frames
        self.definition=definition
        self.equation_graph=definition.equation_graph
        self.seq=0
        self._heat=self.equation_graph.graph.bc_P.new_tensor(1.)
        self._pending=self._heat
        graph=self.equation_graph.graph
        self._pump_ids=tuple(graph.edge_str_ids[i] for i in graph.pump_indices.edge_ids.tolist()) if graph.pump_indices is not None else ()
        self._valve_ids=tuple(graph.edge_str_ids[i] for i in graph.valve_indices.edge_ids.tolist()) if graph.valve_indices is not None else ()
        self._pump_local={edge_id:i for i,edge_id in enumerate(self._pump_ids)}
        self._valve_local={edge_id:i for i,edge_id in enumerate(self._valve_ids)}
        self._hx_ids=tuple(graph.edge_str_ids[i] for i in graph.hx_indices.edge_ids.tolist()) if graph.hx_indices is not None else ()
        self._hx_local={edge_id:i for i,edge_id in enumerate(self._hx_ids)}
        self._active_pumps={}
        self._active_valves={}
        self._queued_pumps={}
        self._queued_valves={}
        self._active_inlets={}
        self._queued_inlets={}
        self._applied=[]
        self._heat_history=[1.]
        theta=definition.parameters(0,heat_multiplier=self._heat)
        guess=self.equation_graph.initial_guess(theta)
        # A mass-consistent hydraulic guess prevents arbitrary initial edge
        # orientations from making an internal pressure datum look like an
        # external fluid source. This is only an initializer: the accepted
        # state and every subsequent step solve the complete DAE residual.
        from backend.liquidcooling.graphcompiling.time_slice import time_slice
        from backend.liquidcooling.solver.differentiable_hydraulic import hydraulic_step
        with torch.no_grad():
            graph=self.equation_graph.graph
            try:
                pressure,flow=hydraulic_step(time_slice(graph,0))
            except RuntimeError as exc:
                # The optional GGA predictor has a narrower convergence domain
                # than the full Newton root (e.g. linear component test loops).
                # Keep its failure visible; only the full DAE can accept a state.
                self.initializer={'method':'raw_guess','predictor_error':str(exc)}
            else:
                guess[self.equation_graph.state_layout['pressure_free']]=pressure[~graph.bc_mask]
                guess[self.equation_graph.state_layout['flow']]=flow
                self.initializer={'method':'hydraulic_guess_then_full_dae_newton'}
        options=definition.newton_options
        if definition.mode=='steady' or definition.initialization=='steady':
            self._state,report=solve_steady(self.equation_graph.problem,guess,theta,options=options,
                conserved=(self.equation_graph.steady_neutral_storage_mask,guess))
            derivative=torch.zeros_like(self._state)
        elif definition.initialization=='consistent':
            self._state,derivative,report=consistent_initialize(self.equation_graph.problem,guess,theta,options=options)
        else:
            raise ValueError('unknown DAE initialization')
        self._theta=theta
        self._states=[self._state]
        self._parameters=[theta]
        first=self._frame(self._state,derivative,theta,0.,0,report,self._heat)
        self._frames=[] if first is None else [first]

    @property
    def tensor_state(self):return self._state.clone()

    def tensor_series(self,field):
        return torch.stack([self.equation_graph.unpack(y,p)[field] for y,p in zip(self._states,self._parameters)],dim=1)

    @property
    def frames(self):return copy.deepcopy(self._frames)

    @property
    def latest_frame(self):
        if not self._frames:raise ValueError('tensor-only experiment has no presentation snapshots')
        return copy.deepcopy(self._frames[-1])

    @property
    def accepted_heat_multipliers(self):return list(self._heat_history)

    @property
    def pump_edge_ids(self):return list(self._pump_ids)

    @property
    def pump_rpm_control_edge_ids(self):
        held=self.equation_graph._pump_flow.detach().cpu().tolist()
        return [edge_id for edge_id,is_held in zip(self._pump_ids,held) if not is_held]

    @property
    def valve_edge_ids(self):return list(self._valve_ids)

    def _accepted_components(self,field,ids):
        part=self.equation_graph.parameter_layout[field]
        return [dict(zip(ids,theta[part].detach().cpu().tolist())) for theta in self._parameters]

    @property
    def accepted_pump_rpm_ratios(self):
        return self._accepted_components('pump_rpm_ratio',self._pump_ids)

    @property
    def accepted_valve_openings(self):
        return self._accepted_components('valve_opening',self._valve_ids)

    @property
    def pending_pump_rpm_ratios(self):
        return {edge_id:float(value.detach()) for edge_id,value in self._queued_pumps.items()}

    @property
    def pending_valve_openings(self):
        return {edge_id:float(value.detach()) for edge_id,value in self._queued_valves.items()}

    @property
    def hx_edge_ids(self):return list(self._hx_ids)

    @property
    def accepted_primary_inlet_K(self):
        return self._accepted_components('primary_inlet_K',self._hx_ids)

    @property
    def pending_primary_inlet_K(self):
        return {edge_id:float(value.detach()) for edge_id,value in self._queued_inlets.items()}

    @property
    def applied_inputs(self):return copy.deepcopy(self._applied)

    @property
    def done(self):return self.definition.mode=='steady' or self.seq>=self.definition.sample_count-1

    def _scale(self,value,*,name='heat multiplier',positive=False,maximum=None):
        reference=self._heat
        if isinstance(value,bool) or (not isinstance(value,(Real,torch.Tensor))):
            raise ValueError(f'{name} must be a finite real scalar')
        if isinstance(value,torch.Tensor) and value.device!=reference.device:
            raise ValueError(f'{name} tensor must share the graph device')
        if isinstance(value,torch.Tensor) and (value.dtype==torch.bool or value.is_complex()):
            raise ValueError(f'{name} must be a finite real scalar')
        if isinstance(value,torch.Tensor):
            scale=value.to(dtype=reference.dtype)
        else:
            try:scalar=float(value)
            except (OverflowError,TypeError,ValueError) as error:
                raise ValueError(f'{name} must be a finite real scalar') from error
            if not math.isfinite(scalar):
                raise ValueError(f'{name} must be a finite real scalar')
            scale=reference.new_tensor(scalar)
        if scale.ndim!=0 or not bool(torch.isfinite(scale)):
            raise ValueError(f'{name} must be a finite {"positive" if positive else "nonnegative"} scalar')
        out_of_range=bool(scale<=0) if positive else bool(scale<0)
        if out_of_range:
            raise ValueError(f'{name} must be a finite {"positive" if positive else "nonnegative"} scalar')
        if maximum is not None and bool(scale>maximum):
            raise ValueError(f'{name} must not exceed {maximum}')
        return scale

    def set_heat_multiplier(self,value):
        self.set_inputs(heat_multiplier=value)

    def _checked_commands(self,commands,local,*,name,positive=False,maximum=None):
        if commands is None:return {}
        if not isinstance(commands,dict):
            raise TypeError(f'{name} must be an edge-ID mapping')
        checked={}
        for edge_id,value in commands.items():
            if not isinstance(edge_id,str) or edge_id not in local:
                raise ValueError(f'{name} edge ID {edge_id!r} is not a matching compiled actuator')
            if name=='pump_rpm_ratios' and bool(self.equation_graph._pump_flow[local[edge_id]]):
                raise ValueError(f'{name} {edge_id!r} controls a flow-held pump; edit its flow setpoint track instead')
            if name=='valve_openings' and bool(self.equation_graph._closed_valves[local[edge_id]]):
                raise ValueError(f'{name} {edge_id!r} changes the fixed valve active set')
            checked[edge_id]=self._scale(value,name=f'{name}.{edge_id}',positive=positive,maximum=maximum)
        return checked

    def set_inputs(self,*,heat_multiplier=None,pump_rpm_ratios=None,valve_openings=None,primary_inlet_K=None):
        """Queue a whole validated command for the next accepted step only."""
        if self.done:raise ValueError('experiment has no future step; create a new run or query equilibrium')
        heat=self._pending if heat_multiplier is None else self._scale(heat_multiplier)
        pumps=self._checked_commands(pump_rpm_ratios,self._pump_local,name='pump_rpm_ratios')
        valves=self._checked_commands(valve_openings,self._valve_local,name='valve_openings',positive=True,maximum=1.)
        # Supply temperature is a Kelvin boundary: strictly positive, no upper
        # cap here (validate_parameters remains the physical backstop).
        inlets=self._checked_commands(primary_inlet_K,self._hx_local,name='primary_inlet_K',positive=True)
        if heat_multiplier is None and not pumps and not valves and not inlets:
            raise ValueError('at least one heat, pump, valve or supply-temperature input is required')
        # Nothing is mutated until all fields pass validation.
        self._pending=heat
        self._queued_pumps={**self._queued_pumps,**pumps}
        self._queued_valves={**self._queued_valves,**valves}
        self._queued_inlets={**self._queued_inlets,**inlets}

    def _theta_at(self,index,heat,pumps,valves,inlets):
        theta=self.definition.parameters(index,heat_multiplier=heat)
        if not pumps and not valves and not inlets:return theta
        indices=[];values=[]
        for field,local,commands in (
            ('pump_rpm_ratio',self._pump_local,pumps),
            ('valve_opening',self._valve_local,valves),
            ('primary_inlet_K',self._hx_local,inlets),
        ):
            if not commands:continue
            start=self.equation_graph.parameter_layout[field].start
            indices.extend(start+local[edge_id] for edge_id in commands)
            values.extend(commands.values())
        positions=torch.tensor(indices,dtype=torch.long,device=theta.device)
        updated=theta.index_copy(0,positions,torch.stack(values))
        self.equation_graph.validate_parameters(updated)
        return updated

    def step(self):
        if self.done:raise ValueError('experiment has no future step')
        seq=self.seq+1;time=seq*self.definition.dt
        pumps={**self._active_pumps,**self._queued_pumps}
        valves={**self._active_valves,**self._queued_valves}
        inlets={**self._active_inlets,**self._queued_inlets}
        theta=self._theta_at(seq,self._pending,pumps,valves,inlets)
        next_state,report=backward_euler_step(self.equation_graph.problem,self._state,theta,
            time=time,dt=self.definition.dt,options=self.definition.newton_options)
        derivative=torch.where(self.equation_graph.differential_mask,
            (next_state-self._state)/self.definition.dt,torch.zeros_like(next_state))
        frame=self._frame(next_state,derivative,theta,time,seq,report,self._pending)
        # Commit only after the nonlinear solve and physical diagnostics succeed.
        accepted={'seq':seq,'time_s':time}
        if float(self._pending.detach())!=float(self._heat.detach()):
            accepted['heat_multiplier']=float(self._pending.detach())
        if self._queued_pumps:
            accepted['pump_rpm_ratios']={edge_id:float(value.detach()) for edge_id,value in self._queued_pumps.items()}
        if self._queued_valves:
            accepted['valve_openings']={edge_id:float(value.detach()) for edge_id,value in self._queued_valves.items()}
        if self._queued_inlets:
            accepted['primary_inlet_K']={edge_id:float(value.detach()) for edge_id,value in self._queued_inlets.items()}
        if len(accepted)>2:self._applied.append(accepted)
        self._active_pumps=pumps;self._active_valves=valves;self._active_inlets=inlets
        self._queued_pumps={};self._queued_valves={};self._queued_inlets={}
        self._heat=self._pending;self._theta=theta;self._state=next_state;self.seq=seq
        self._states.append(next_state);self._parameters.append(theta)
        self._heat_history.append(float(self._heat.detach()))
        if frame is not None:self._frames.append(frame)
        return copy.deepcopy(frame)

    def equilibrium(self,heat_multiplier=None):
        # A query previews the pending controls against the CURRENT accepted
        # sample's scheduled boundaries. It is not a time step or a mutation
        # of the accepted trajectory.
        multiplier=self._pending if heat_multiplier is None else self._scale(heat_multiplier)
        pumps={**self._active_pumps,**self._queued_pumps}
        valves={**self._active_valves,**self._queued_valves}
        inlets={**self._active_inlets,**self._queued_inlets}
        theta=self._theta_at(self.seq,multiplier,pumps,valves,inlets)
        # Equilibrium freezes the boundary at its current value. A sampled
        # v4 HX setpoint rate belongs to the preceding transient interval;
        # retaining it would invent storage in a steady query.
        rate=self.equation_graph.parameter_layout['hx_T_set_rate']
        theta=torch.cat((theta[:rate.start],torch.zeros_like(theta[rate]),theta[rate.stop:]))
        state,report=solve_steady(self.equation_graph.problem,self._state,theta,
            time=self.seq*self.definition.dt,options=self.definition.newton_options,
            conserved=(self.equation_graph.steady_neutral_storage_mask,self._state))
        frame=self._frame(state,torch.zeros_like(state),theta,self.seq*self.definition.dt,
                          self.seq,report,multiplier,presentation=True)
        frame['kind']='equilibrium'
        frame['evaluated_at_s']=frame.pop('time_s')
        frame['input_basis']='current_accepted_sample_with_pending_controls'
        return frame

    def _frame(self,y,ydot,theta,time,seq,report,multiplier,presentation=False):
        fields=self.equation_graph.unpack(y,theta)
        diagnostics=self.equation_graph.diagnostics(y.new_tensor(time),y,ydot,theta)
        if not all(bool(torch.isfinite(value).all()) for value in diagnostics.values()):
            raise RuntimeError('non-finite DAE physical diagnostics')
        heat_scale=1+abs(float(diagnostics['heat_input_W'].detach()))
        balance_tolerance=max(1e-7,256*torch.finfo(y.dtype).eps)
        if abs(float(diagnostics['energy_balance_W'].detach()))>balance_tolerance*heat_scale:
            raise RuntimeError('DAE step violates global thermal energy balance')
        if not self.record_frames and not presentation:return None
        mapping={'node_pressure_Pa':'pressure','edge_flow_kg_s':'flow','node_temperature_K':'node_temperature',
                 'cell_temperature_K':'water_temperature','solid_temperature_K':'solid_temperature',
                 'primary_return_K':'primary_return_temperature','secondary_supply_K':'hx_secondary_temperature',
                 'edge_outlet_K':'edge_outlet_temperature'}
        layout=self.equation_graph.parameter_layout
        pumps=dict(zip(self._pump_ids,theta[layout['pump_rpm_ratio']].detach().tolist()))
        valves=dict(zip(self._valve_ids,theta[layout['valve_opening']].detach().tolist()))
        inlets=dict(zip(self._hx_ids,theta[layout['primary_inlet_K']].detach().tolist()))
        return {'kind':'frame','seq':seq,'time_s':float(time),
                'heat_multiplier':float(multiplier.detach()),
                'pump_rpm_ratios':pumps,'valve_openings':valves,
                'primary_inlet_K':inlets,
                **{name:fields[key].detach().tolist() for name,key in mapping.items()},
                'diagnostics':{name:float(value.detach()) for name,value in diagnostics.items()},
                'newton':asdict(report)}
