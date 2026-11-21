from pathlib import Path
T=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration/.codex-tmp/shinaide-ring-full-mms-private-20261006")
p=T/"after/src/backend/liquidcooling/manufactured_benchmark.py"
p.write_text((T/"module-content.py").read_text())
def edit(rel,old,new,count=1):
 p=T/"after"/rel;s=p.read_text();assert s.count(old)==count,(rel,s.count(old),old[:100]);p.write_text(s.replace(old,new,count))
S="src/backend/liquidcooling/liquidrun_dae_schema.py"
edit(S,"from backend.liquidcooling.parameters import ParameterPack, resolve_parameter_pack, _digest","from backend.liquidcooling.parameters import ParameterPack, resolve_parameter_pack, _digest\nfrom backend.liquidcooling.manufactured_benchmark import ManufacturedBenchmark, validate_contract, bind")
edit(S,"    transient: TransientProvenance\n","    transient: TransientProvenance\n    manufactured_benchmark: ManufacturedBenchmark | None = None\n")
edit(S,"        return _graph_and_primary_contract(self)\n\n\ndef is_dae_manifest","        result=_graph_and_primary_contract(self)\n        if self.manufactured_benchmark is not None:\n            validate_contract(self.manufactured_benchmark,self,_v2_graph_view(self))\n        return result\n\n\ndef is_dae_manifest")
edit(S,"    _primary_flow: Tensor\n","    _primary_flow: Tensor\n    _manufactured_sources: dict | None = None\n")
edit(S,"        return theta\n\n\n","        if self._manufactured_sources is not None:\n            for name,series in self._manufactured_sources.items():\n                part=self.equation_graph.parameter_layout[name]\n                theta=torch.cat((theta[:part.start],series[:,index],theta[part.stop:]))\n            self.equation_graph.validate_parameters(theta)\n        return theta\n\n\n")
edit(S,"    equation=EquationGraph(graph,primary)","    benchmark=getattr(manifest,'manufactured_benchmark',None)\n    equation=EquationGraph(graph,primary,manufactured=benchmark is not None)\n    sources=bind(equation,benchmark) if benchmark is not None else None")
edit(S,"schedule('inlet_K'),schedule('flow_kg_s'))","schedule('inlet_K'),schedule('flow_kg_s'),sources)")
E="src/backend/liquidcooling/solver/equation_graph.py"
edit(E,"def __init__(self, graph: CompiledGraph, primary: PrimaryCircuit, *, device=None,dtype=None):","def __init__(self, graph: CompiledGraph, primary: PrimaryCircuit, *, device=None,dtype=None,manufactured=False):")
edit(E,"        self.device=torch.device(graph.bc_P.device if device is None else device)","        self._manufactured=manufactured\n        self.device=torch.device(graph.bc_P.device if device is None else device)")
edit(E,"        return fields\n","        if self._manufactured:\n            for name,count in (('mms_momentum_Pa',g.N_edges),('mms_water_W',g.N_cells),('mms_solid_W',g.N_cp),('mms_hx_secondary_W',g.N_hx),('mms_hx_primary_W',g.N_hx)):\n                fields[name]=g.bc_P.new_zeros(count)\n        return fields\n")
edit(E,"        return torch.cat((mass,momentum,node_residual,water_residual,solid_residual,secondary_residual,primary_residual))","        if self._manufactured:\n            momentum=momentum-p['mms_momentum_Pa']\n            water_residual=water_residual-p['mms_water_W']\n            solid_residual=solid_residual-p['mms_solid_W']\n            secondary_residual=secondary_residual-p['mms_hx_secondary_W']\n            primary_residual=primary_residual-p['mms_hx_primary_W']\n        return torch.cat((mass,momentum,node_residual,water_residual,solid_residual,secondary_residual,primary_residual))")
edit(E,"        return {'heat_input_W':source,'primary_rejection_W':rejection,'storage_rate_W':storage,","        extra={}\n        if self._manufactured:\n            external=sum(p[name].sum() for name in ('mms_water_W','mms_solid_W','mms_hx_secondary_W','mms_hx_primary_W'))\n            extra={'physical_heat_input_W':source,'manufactured_heat_W':external,'manufactured_momentum_inf_Pa':norm(p['mms_momentum_Pa'])}\n            source=source+external\n        return {**extra,'heat_input_W':source,'primary_rejection_W':rejection,'storage_rate_W':storage,")
# Preserve the original default state path; additional sign gate only research.
edit(E,"    def validate_state(self,y:Tensor,theta:Tensor) -> None:\n","    def validate_state(self,y:Tensor,theta:Tensor) -> None:\n        if self._manufactured and hasattr(self,'manufactured_signs'):\n            if not bool((y[self.state_layout['flow']]*self.manufactured_signs>1e-8).all()):\n                raise ValueError('manufactured flow crossed declared sign regime')\n")
D="src/backend/liquidcooling/dae_experiment.py"
p=T/"after"/D;s=p.read_text();start=s.index("        guess=self.equation_graph.initial_guess(theta)");end=s.index("        self._theta=theta",start)
old=s[start:end]
new="""        benchmark=getattr(definition.manifest,'manufactured_benchmark',None)
        if benchmark is not None:
            from backend.liquidcooling.manufactured_benchmark import initial_vectors
            from backend.liquidcooling.solver.dae_newton import NewtonReport
            self._state,derivative=initial_vectors(self.equation_graph,benchmark,theta)
            self.equation_graph.problem.validate(self._state)
            self.equation_graph.validate_parameters(theta)
            self.equation_graph.validate_state(self._state,theta)
            residual=self.equation_graph.residual(theta.new_zeros(()),self._state,derivative,theta)
            norm=float((residual/self.equation_graph.residual_scale).abs().max())
            if not math.isfinite(norm) or norm>definition.newton_options.atol:
                raise ValueError('manufactured initial state/derivative fails original continuous residual atol')
            report=NewtonReport(0,norm,0)
            self.initializer={'method':'declared_manufactured_state','source_sha256':benchmark.source_sha256}
        else:
"""+''.join('    '+l+'\n' for l in old.splitlines())
p.write_text(s[:start]+new+s[end:])
R="src/backend/liquidcooling/dae_runtime.py"
edit(R,"    definition=load_dae_input(raw)","    if raw.get('manufactured_benchmark') is not None:\n        from backend.liquidcooling.manufactured_benchmark import MAX_BENCHMARK_BYTES\n        import json\n        if len(json.dumps(raw['manufactured_benchmark'],allow_nan=False).encode())>MAX_BENCHMARK_BYTES:\n            raise ValueError('manufactured benchmark byte cap exceeded')\n    definition=load_dae_input(raw)")
F="src/frontend/react/src/schema/liquidrunDAE.ts"
edit(F,"  execution?:{backend:","  manufactured_benchmark?:import('./manufacturedBenchmark').ManufacturedBenchmark\n  execution?:{backend:")
F="src/frontend/react/src/liquid/liquidExperiment.ts"
edit(F,"(timeline||v4Forms)?'v4':'v3'","(timeline||v4Forms||experiment.dae.manufactured_benchmark)?'v4':'v3'")
edit(F,"        dae={...dae,analysis:{...dae.analysis,initialization:uniform?'consistent':'steady'}}","        dae={...dae,analysis:{...dae.analysis,initialization:dae.manufactured_benchmark?'consistent':uniform?'consistent':'steady'}}")
S="src/frontend/shared/js/cfdproj_schema.js"
edit(S,"['execution'])) {","['execution','manufactured_benchmark'])) {")
# Detailed generated strict JSON schema is authoritative at actual React v4 compile.
edit(S,"        if (d.equation_system !== 'incompressible_thermal_dae')","        if (d.manufactured_benchmark !== undefined) {\n          if (!object(d.manufactured_benchmark) || d.manufactured_benchmark.format !== 'dcdtuni-manufactured-benchmark' || d.manufactured_benchmark.version !== 1) err('dae.manufactured_benchmark','expected explicit versioned research benchmark');\n          if (d.analysis.mode !== 'transient' || d.analysis.initialization !== 'consistent') err('dae.manufactured_benchmark','requires transient/consistent analysis');\n        }\n        if (d.equation_system !== 'incompressible_thermal_dae')")
print("private edits complete")
