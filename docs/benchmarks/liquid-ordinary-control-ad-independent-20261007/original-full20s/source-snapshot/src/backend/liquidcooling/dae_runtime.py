"""Version-3 DAE adapter for the existing run lifecycle and result consumers.

JSON checkpoints replay accepted inputs through the same solver. They never
claim to serialize an autograd tape; research uses DAEExperiment tensors.
"""
from __future__ import annotations

import copy
import math
import uuid
import numpy as np
import torch
from backend.liquidcooling.dae_experiment import DAEExperiment
from backend.liquidcooling.liquidrun_dae_schema import load_dae_input
from backend.liquidcooling.postprocess.summaries import build_rack_summaries, summaries_json
from backend.liquidcooling.runtime import _canonical_hash, MAX_SAMPLES, MAX_NODES, MAX_EDGES


def admit_dae_runtime_input(raw):
    """Shared v3 resource gate for production sessions and tensor research runs."""
    # Check before model validators expand trajectories or allocate a graph.
    count=raw.get('time',{}).get('N_t')
    if not isinstance(count,int) or isinstance(count,bool) or not 1<=count<=MAX_SAMPLES:
        raise ValueError(f'DAE N_t must be an integer in 1..{MAX_SAMPLES}')
    if len(raw.get('nodes',[]))>MAX_NODES or len(raw.get('edges',[]))>MAX_EDGES:
        raise ValueError('DAE graph exceeds the runtime size limit')
    if raw.get('manufactured_benchmark') is not None:
        from backend.liquidcooling.manufactured_benchmark import MAX_BENCHMARK_BYTES
        import json
        if len(json.dumps(raw['manufactured_benchmark'],allow_nan=False).encode())>MAX_BENCHMARK_BYTES:
            raise ValueError('manufactured benchmark byte cap exceeded')
    definition=load_dae_input(raw)
    if definition.equation_graph.size>4000:
        raise ValueError('current dense DAE runtime supports at most 4000 unknowns; reduce the mock discretization')
    return definition


class DAESimulation:
    def __init__(self, raw, run_id=None):
        self.definition=admit_dae_runtime_input(raw)
        # Process-wide PyTorch policy belongs at the execution boundary, never
        # in the equation graph or Newton/adjoint algorithm.
        torch.set_num_threads(self.definition.manifest.execution.max_threads)
        self._liquidrun=self.definition.manifest.model_dump(by_alias=True,mode='json',exclude_none=True)
        self.run_id=run_id or uuid.uuid4().hex
        self.config_hash=_canonical_hash(self._liquidrun,[])
        self.experiment=DAEExperiment(self.definition)
        self._graph=self.definition.equation_graph.graph
        self._pending=1.
        self._executed_cache=None

    @property
    def done(self):return self.experiment.done

    @property
    def metadata(self):
        g=self._graph
        cp=[] if g.cp_indices is None else [g.edge_str_ids[i] for i in g.cp_indices.edge_ids.tolist()]
        hx=[] if g.hx_indices is None else [g.edge_str_ids[i] for i in g.hx_indices.edge_ids.tolist()]
        return {'solver':'differentiable_newton_dae','analysis':self._liquidrun['analysis'],
            'object_bindings':copy.deepcopy(self._liquidrun['object_bindings']),
            'node_ids':list(g.node_ids),'edge_ids':list(g.edge_str_ids),'cp_edge_ids':cp,'hx_edge_ids':hx,
            'pump_edge_ids':self.experiment.pump_edge_ids,
            'pump_rpm_control_edge_ids':self.experiment.pump_rpm_control_edge_ids,
            'valve_edge_ids':self.experiment.valve_edge_ids,
            'total_sample_count':self.definition.sample_count,'dt_s':self.definition.dt,
            'config_hash':self.config_hash,'parameter_provenance':copy.deepcopy(self.definition.provenance),
            'pending_heat_multiplier':self._pending,
            'pending_pump_rpm_ratios':self.experiment.pending_pump_rpm_ratios,
            'pending_valve_openings':self.experiment.pending_valve_openings,
            'pending_primary_inlet_K':self.experiment.pending_primary_inlet_K,
            'initialization_report':copy.deepcopy(self.experiment.initializer),
            'execution':{**self._liquidrun['execution'],'actual_threads':torch.get_num_threads()},
            'limits':{'valve_topology':'fixed_active_set','time_integrator':'backward_euler',
                'primary_hydraulics':'prescribed_flow','cold_plate_coolant':'quasi_steady_NTU'}}

    def _frame(self, frame):
        frame=copy.deepcopy(frame)
        frame.update(run_id=self.run_id,chip_temp_K=frame.pop('solid_temperature_K'),
            edge_outlet_temp_K=frame.pop('edge_outlet_K'),
            controls={'pump_rpm_ratios':copy.deepcopy(frame['pump_rpm_ratios']),
                      'valve_openings':copy.deepcopy(frame['valve_openings']),
                      'primary_inlet_K':copy.deepcopy(frame['primary_inlet_K'])},
            cp_edge_ids=self.metadata['cp_edge_ids'],cell_positions=self._graph.cell_position.tolist())
        return frame

    @property
    def frames(self):return [self._frame(frame) for frame in self.experiment.frames]

    @property
    def latest_frame(self):return self._frame(self.experiment.latest_frame)

    def step(self):return self._frame(self.experiment.step())

    def set_inputs(self,*,heat_multiplier=None,pump_rpm_ratios=None,valve_openings=None,primary_inlet_K=None):
        """Validate one JSON command, then queue it at the next step boundary."""
        if heat_multiplier is None and pump_rpm_ratios is None and valve_openings is None and primary_inlet_K is None:
            raise ValueError('at least one heat, pump, valve or supply-temperature input is required')
        def real_number(value):
            if isinstance(value,bool) or not isinstance(value,(int,float)):
                return False
            try:return math.isfinite(float(value))
            except (OverflowError,TypeError,ValueError):return False
        if heat_multiplier is not None:
            if not real_number(heat_multiplier) or heat_multiplier<0:
                raise ValueError('heat multiplier must be a finite nonnegative number')
        for name,commands in (('pump_rpm_ratios',pump_rpm_ratios),('valve_openings',valve_openings),('primary_inlet_K',primary_inlet_K)):
            if commands is None:continue
            if not isinstance(commands,dict):
                raise ValueError(f'{name} must be an edge-ID object')
            for edge_id,value in commands.items():
                if not isinstance(edge_id,str) or not real_number(value):
                    raise ValueError(f'{name} must map edge IDs to finite numbers')
        self.experiment.set_inputs(heat_multiplier=heat_multiplier,
            pump_rpm_ratios=pump_rpm_ratios,valve_openings=valve_openings,
            primary_inlet_K=primary_inlet_K)
        if heat_multiplier is not None:self._pending=float(heat_multiplier)

    def set_heat_multiplier(self,value):
        self.set_inputs(heat_multiplier=value)

    def equilibrium(self,heat_multiplier=None):
        return self._frame(self.experiment.equilibrium(heat_multiplier))

    def checkpoint(self):
        return {'format':'liquid-dae-replay-checkpoint','version':3,'run_id':self.run_id,
            'config_hash':self.config_hash,'liquidrun':copy.deepcopy(self._liquidrun),'controllers':[],
            'seq':self.experiment.seq,'heat_multipliers':self.experiment.accepted_heat_multipliers,
            'pump_rpm_ratios':self.experiment.accepted_pump_rpm_ratios,
            'valve_openings':self.experiment.accepted_valve_openings,
            'primary_inlet_K':self.experiment.accepted_primary_inlet_K,
            'pending_heat_multiplier':self._pending,'applied_inputs':self.experiment.applied_inputs,
            'pending_pump_rpm_ratios':self.experiment.pending_pump_rpm_ratios,
            'pending_valve_openings':self.experiment.pending_valve_openings,
            'pending_primary_inlet_K':self.experiment.pending_primary_inlet_K,
            'restore_method':'replay accepted inputs; recompute roots and differentiable trajectory'}

    def executed_manifest(self):
        from backend.liquidcooling.accepted_run_input import build_accepted_manifest
        if self._executed_cache is None or self._executed_cache[0]!=self.experiment.seq:
            manifest=build_accepted_manifest(self.definition,self.experiment.accepted_heat_multipliers,
                pump_rpm_ratios=self.experiment.accepted_pump_rpm_ratios,
                valve_openings=self.experiment.accepted_valve_openings,
                primary_inlet_K=self.experiment.accepted_primary_inlet_K)
            self._executed_cache=(self.experiment.seq,manifest)
        return copy.deepcopy(self._executed_cache[1])

    @classmethod
    def from_checkpoint(cls,raw,checkpoint):
        version=checkpoint.get('version')
        if checkpoint.get('format')!='liquid-dae-replay-checkpoint' or isinstance(version,bool) or version not in (1,2,3):
            raise ValueError('unsupported DAE checkpoint')
        if version<3 and ('primary_inlet_K' in checkpoint or 'pending_primary_inlet_K' in checkpoint):
            raise ValueError('legacy checkpoint cannot contain supply-temperature fields')
        if checkpoint.get('controllers')!=[] or _canonical_hash(checkpoint['liquidrun'],[])!=checkpoint.get('config_hash'):
            raise ValueError('checkpoint config hash does not match its manifest')
        # Validate original input and compare the canonical form BEFORE replay.
        current=load_dae_input(raw).manifest.model_dump(by_alias=True,mode='json',exclude_none=True)
        if _canonical_hash(current,[])!=checkpoint['config_hash']:
            raise ValueError('checkpoint hash does not match requested liquidrun')
        seq=checkpoint.get('seq');scales=checkpoint.get('heat_multipliers')
        if isinstance(seq,bool) or not isinstance(seq,int) or not 0<=seq<current['time']['N_t']:
            raise ValueError('checkpoint seq is outside the configured horizon')
        if not isinstance(scales,list) or len(scales)!=seq+1 or scales[0]!=1.:
            raise ValueError('checkpoint heat multipliers do not match accepted samples')
        for scale in scales+[checkpoint.get('pending_heat_multiplier')]:
            try:finite=isinstance(scale,(int,float)) and math.isfinite(float(scale))
            except (OverflowError,TypeError,ValueError):finite=False
            if isinstance(scale,bool) or not finite or scale<0:
                raise ValueError('checkpoint heat multiplier must be finite and nonnegative')
        events=checkpoint.get('applied_inputs')
        if not isinstance(events,list):
            raise ValueError('checkpoint applied_inputs must be a list')
        by_seq={}
        for event in events:
            if not isinstance(event,dict) or isinstance(event.get('seq'),bool) or not isinstance(event.get('seq'),int):
                raise ValueError('checkpoint accepted events require an integer seq')
            if event['seq'] in by_seq or not 1<=event['seq']<=seq:
                raise ValueError('checkpoint accepted event seq is duplicate or out of range')
            by_seq[event['seq']]=event
        simulation=cls(current,checkpoint['run_id'])
        for index,scale in enumerate(scales[1:],start=1):
            event=by_seq.get(index,{})
            if version==1 and ('pump_rpm_ratios' in event or 'valve_openings' in event):
                raise ValueError('legacy checkpoint cannot contain actuator events')
            if version<3 and 'primary_inlet_K' in event:
                raise ValueError('legacy checkpoint cannot contain supply-temperature events')
            simulation.set_inputs(heat_multiplier=scale,
                pump_rpm_ratios=event.get('pump_rpm_ratios'),
                valve_openings=event.get('valve_openings'),
                primary_inlet_K=event.get('primary_inlet_K'))
            simulation.step()
        if simulation.experiment.applied_inputs!=checkpoint.get('applied_inputs'):
            raise ValueError('checkpoint event record disagrees with accepted inputs')
        if version>=2:
            if (simulation.experiment.accepted_pump_rpm_ratios!=checkpoint.get('pump_rpm_ratios') or
                simulation.experiment.accepted_valve_openings!=checkpoint.get('valve_openings')):
                raise ValueError('checkpoint actuator traces disagree with accepted inputs')
            pending_pumps=checkpoint.get('pending_pump_rpm_ratios')
            pending_valves=checkpoint.get('pending_valve_openings')
            if not isinstance(pending_pumps,dict) or not isinstance(pending_valves,dict):
                raise ValueError('checkpoint pending actuators must be edge-ID objects')
        else:
            pending_pumps={};pending_valves={}
        if version>=3:
            if simulation.experiment.accepted_primary_inlet_K!=checkpoint.get('primary_inlet_K'):
                raise ValueError('checkpoint supply-temperature trace disagrees with accepted inputs')
            pending_inlets=checkpoint.get('pending_primary_inlet_K')
            if not isinstance(pending_inlets,dict):
                raise ValueError('checkpoint pending supply temperatures must be an edge-ID object')
        else:
            pending_inlets={}
        if not simulation.done:
            simulation.set_inputs(heat_multiplier=checkpoint['pending_heat_multiplier'],
                pump_rpm_ratios=pending_pumps,valve_openings=pending_valves,
                primary_inlet_K=pending_inlets)
        elif checkpoint['pending_heat_multiplier']!=scales[-1] or pending_pumps or pending_valves or pending_inlets:
            raise ValueError('completed checkpoint cannot have a pending input')
        return simulation

    def result(self):
        frames=self.frames;g=self._graph
        def series(key):return np.asarray([frame[key] for frame in frames],dtype=float).T.tolist()
        cp_ids=self.metadata['cp_edge_ids'];edge_index={name:i for i,name in enumerate(g.edge_str_ids)}
        edge_out=np.asarray(series('edge_outlet_temp_K'))
        flow=np.asarray(series('edge_flow_kg_s'));node_T=np.asarray(series('node_temperature_K'))
        chip=np.asarray(series('chip_temp_K'));pressure=np.asarray(series('node_pressure_Pa'))
        water=np.asarray([edge_out[edge_index[cp]] for cp in cp_ids])
        details=[]
        for local,cp_id in enumerate(cp_ids):
            edge=edge_index[cp_id];forward=flow[edge]>=0
            up=int(g.edge_from[edge]);down=int(g.edge_to[edge])
            details.append({'edge_id':cp_id,'chip_temp_K':chip[local].tolist(),
                'water_inlet_temp_K':np.where(forward,node_T[up],node_T[down]).tolist(),
                'water_outlet_temp_K':water[local].tolist(),
                'inlet_pressure_Pa':np.where(forward,pressure[up],pressure[down]).tolist(),
                'outlet_pressure_Pa':np.where(forward,pressure[down],pressure[up]).tolist()})
        cell_ids=[] if g.cell_edge_id is None else [g.edge_str_ids[int(g.pipe_indices.edge_ids[i])] for i in g.cell_edge_id.tolist()]
        return {'run_id':self.run_id,'config_hash':self.config_hash,'solver':'differentiable_newton_dae',
            'status':'completed' if self.done else 'partial','completed':self.done,'converged':True,
            # Accepted-state Newton convergence is not completion of the requested analysis.
            'analysis':copy.deepcopy(self._liquidrun['analysis']),
            'expected_duration_s':float((self.definition.sample_count-1)*self.definition.dt),
            'final_loss':max(f['newton']['scaled_residual_inf'] for f in frames),
            'epochs':sum(f['newton']['iterations'] for f in frames),
            'node_ids':list(g.node_ids),'edge_ids':list(g.edge_str_ids),'hx_edge_ids':self.metadata['hx_edge_ids'],
            **{key:series(key) for key in ('node_pressure_Pa','node_temperature_K','edge_flow_kg_s',
                'chip_temp_K','edge_outlet_temp_K','primary_return_K','secondary_supply_K')},
            'cell_temp_K':series('cell_temperature_K'),'water_outlet_temp_K':water.tolist(),
            'time_s':[f['time_s'] for f in frames],'cp_details':details,
            'controls':[f['controls'] for f in frames],'diagnostics':[f['diagnostics'] for f in frames],
            'applied_inputs':self.experiment.applied_inputs,'heat_multipliers':[f['heat_multiplier'] for f in frames],
            'pump_rpm_ratios':self.experiment.accepted_pump_rpm_ratios,
            'valve_openings':self.experiment.accepted_valve_openings,
            'primary_inlet_K':self.experiment.accepted_primary_inlet_K,
            'parameter_provenance':copy.deepcopy(self.definition.provenance),
            'object_bindings':copy.deepcopy(self._liquidrun['object_bindings']),
            'fluid':copy.deepcopy(self._liquidrun['fluid']),'cell_edge_ids':cell_ids,
            'cell_positions':g.cell_position.tolist(),
            'rack_summaries':summaries_json(build_rack_summaries(g,{'m':flow},water,chip,node_T))}
