"""Opt-in fixed-grid thermal trajectory AD/FD. Default mode is JSON-only."""
from __future__ import annotations
import argparse, hashlib, json, math, os, subprocess, sys, time
from pathlib import Path
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]
B=REPO.parents[2]
NAMES=("original","executed","checkpoint","ledger")
TIMEOUT=180

def digest(value):
    return hashlib.sha256(value).hexdigest()

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False)

def owned(path):
    value=Path(path).resolve()
    if not value.is_relative_to(B):
        raise ValueError("all paths must stay inside dcdtuni")
    return value

def grid(raw):
    if not isinstance(raw,dict) or raw.get("schemaVersion")!=4:
        raise ValueError("normal UI v4 input required")
    clock=raw.get("time",{});n=clock.get("N_t");dt=clock.get("dt_s")
    if isinstance(n,bool) or not isinstance(n,int) or not 2<=n<=256:
        raise ValueError("complete 2..256 samples required; never shorten")
    if isinstance(dt,bool) or not isinstance(dt,(float,int)) or not math.isfinite(dt) or dt<=0:
        raise ValueError("finite positive dt required")
    if raw.get("analysis")!={"mode":"transient","initialization":"steady"}:
        raise ValueError("transient with steady initialization required")
    e=raw.get("execution",{})
    if e.get("device")!="cpu" or e.get("dtype")!="float64":
        raise ValueError("input must already request CPU/float64")
    threads=e.get("max_threads")
    if isinstance(threads,bool) or not isinstance(threads,int) or not 1<=threads<=2:
        raise ValueError("input must already request <=2 CPU threads")
    if raw.get("controllers")!=[] or raw.get("events")!=[]:
        raise ValueError("v4 empty events and no controllers required")
    return n,dt

def preflight(bundle):
    original,executed,cp,ledger=(bundle[x] for x in NAMES)
    n,dt=grid(original)
    if grid(executed)!=(n,dt):
        raise ValueError("executed grid differs; no prefix-only probe")
    if not isinstance(cp,dict) or cp.get("format")!="liquid-dae-replay-checkpoint" or cp.get("version")!=3 or cp.get("seq")!=n-1 or cp.get("controllers")!=[]:
        raise ValueError("completed version3 checkpoint required")
    if not isinstance(ledger,dict) or ledger.get("format")!="liquid-accepted-inputs/v1" or ledger.get("run_id")!=cp.get("run_id"):
        raise ValueError("actual accepted-input run identity required")
    if ledger.get("accepted_sample_count")!=n or ledger.get("original_sample_count")!=n or ledger.get("complete_original_horizon") is not True or ledger.get("accepted_time_s")!=(n-1)*dt:
        raise ValueError("complete accepted horizon required")
    events=cp.get("applied_inputs")
    if not isinstance(events,list):
        raise ValueError("explicit accepted online ledger required")
    seen=set();allowed={"seq","time_s","heat_multiplier","pump_rpm_ratios","valve_openings","primary_inlet_K"}
    for event in events:
        if not isinstance(event,dict) or set(event)-allowed:
            raise ValueError("unknown ledger field")
        seq=event.get("seq")
        if isinstance(seq,bool) or not isinstance(seq,int) or not 1<=seq<n or seq in seen:
            raise ValueError("duplicate/out-of-range accepted seq")
        if event.get("time_s")!=seq*dt or not set(event)-{"seq","time_s"}:
            raise ValueError("actual accepted sample boundary required")
        seen.add(seq)
    return {"N_t":n,"dt_s":dt,"end_s":(n-1)*dt,"run_id":cp["run_id"],"online_events":len(events)}

def inputs(args):
    bundle={};identities={}
    for name in NAMES:
        path=owned(getattr(args,name));data=path.read_bytes()
        bundle[name]=json.loads(data)
        identities[name]={"path":str(path),"bytes":len(data),"sha256":digest(data)}
    return bundle,identities,preflight(bundle)

def source_guard():
    rows=json.loads((HERE/"source-identity.json").read_text())["sources"]
    for row in rows:
        if digest((REPO/row["repoRelative"]).read_bytes())!=row["sha256"]:
            raise ValueError("source changed: "+row["repoRelative"])
    return rows

def worker(bundle,identities,clock):
    # No numerical import occurs before explicit --execute.
    sys.path.insert(0,str(REPO/"src"))
    import torch
    from backend.liquidcooling.dae_runtime import admit_dae_runtime_input
    from backend.liquidcooling.accepted_run_input import build_accepted_manifest
    from backend.liquidcooling.dae_experiment import DAEExperiment
    from backend.liquidcooling.runtime import _canonical_hash
    torch.set_num_threads(bundle["original"]["execution"]["max_threads"])
    torch.set_num_interop_threads(1)
    start=time.monotonic();deadline=start+175
    def gate():
        if time.monotonic()>=deadline:
            raise TimeoutError("inter-step deadline reached")
    original=admit_dae_runtime_input(bundle["original"])
    executed=admit_dae_runtime_input(bundle["executed"])
    cp,ledger=bundle["checkpoint"],bundle["ledger"]
    om=original.manifest.model_dump(by_alias=True,mode="json",exclude_none=True)
    em=executed.manifest.model_dump(by_alias=True,mode="json",exclude_none=True)
    oh,eh=_canonical_hash(om,[]),_canonical_hash(em,[])
    if oh!=cp["config_hash"] or oh!=ledger["original_config_hash"] or eh!=ledger["executed_config_hash"] or canonical(cp["liquidrun"])!=canonical(om):
        raise ValueError("actual producer config-hash/manifest binding differs")
    reconstruction=build_accepted_manifest(original,cp["heat_multipliers"],pump_rpm_ratios=cp["pump_rpm_ratios"],valve_openings=cp["valve_openings"],primary_inlet_K=cp["primary_inlet_K"])
    if canonical(reconstruction)!=canonical(em):
        raise ValueError("executed input differs from production accepted-manifest reconstruction")
    eq=executed.equation_graph
    if bool(eq._cp_rise.any()) or bool(eq._hx_setpoint.any()):
        raise ValueError("first gate covers power cold plates and physical HXs only")
    heat=eq.parameter_layout["heat_W"];inlet=eq.parameter_layout["primary_inlet_K"]
    theta=[executed.parameters(i).detach().clone() for i in range(clock["N_t"])]
    mask=torch.zeros(eq.parameter_count,dtype=torch.bool);mask[heat]=True;mask[inlet]=True
    if any(not torch.equal(x[~mask],theta[0][~mask]) for x in theta):
        raise ValueError("changing hydraulics/properties require separate qualification")
    for part in (heat,inlet):
        if not any(not torch.equal(x[part],theta[0][part]) for x in theta[1:]):
            raise ValueError("missing realized post-t0 thermal waveform")
    runs=[]
    def trajectory(alpha,beta,label):
        gate();definition=admit_dae_runtime_input(bundle["executed"])
        graph=definition.equation_graph;producer=definition.parameters;base=producer(0).detach().clone()
        def parameters(index,heat_multiplier=1.,**kwargs):
            value=producer(index,heat_multiplier=heat_multiplier,**kwargs)
            if index==0:
                return value
            changed=value.clone()
            changed[heat]=value[heat]+(alpha-1)*(value[heat]-base[heat])
            changed[inlet]=value[inlet]+(beta-1)*(value[inlet]-base[inlet])
            graph.validate_parameters(changed)
            return changed
        definition.parameters=parameters
        experiment=DAEExperiment(definition,record_frames=False)
        states=[experiment.tensor_state]
        while not experiment.done:
            gate();experiment.step();states.append(experiment.tensor_state)
        stack=torch.stack(states)
        if stack.shape[0]!=clock["N_t"] or not bool(torch.isfinite(stack.detach()).all()):
            raise ValueError("state dimension/finiteness failed")
        flow=stack[:,graph.state_layout["flow"]].detach()
        if not bool((torch.sign(flow)==torch.sign(flow[0])).all()):
            raise ValueError("flow sign regime changed")
        objective=experiment.tensor_series("solid_temperature")[:,-1].mean()
        if not bool(torch.isfinite(objective.detach())):
            raise ValueError("nonfinite objective")
        runs.append({"label":label,"samples":int(stack.shape[0]),"state_size":int(stack.shape[1]),"objective_K":float(objective.detach()),"max_flow_change":float((flow-flow[0]).abs().max())})
        return objective
    a=torch.tensor(1.,dtype=torch.float64,requires_grad=True)
    b=torch.tensor(1.,dtype=torch.float64,requires_grad=True)
    objective=trajectory(a,b,"AD exact accepted waveform")
    gradients=torch.autograd.grad(objective,(a,b));epsilon=1e-4
    fd=[(trajectory(1+epsilon,1.,"heat+h")-trajectory(1-epsilon,1.,"heat-h"))/(2*epsilon),(trajectory(1.,1+epsilon,"inlet+h")-trajectory(1.,1-epsilon,"inlet-h"))/(2*epsilon)]
    directions={}
    for name,ad,center in zip(("post_t0_heat_amplitude","post_t0_primary_inlet_amplitude"),gradients,fd):
        x,y=float(ad.detach()),float(center.detach());error=abs(x-y)/max(abs(x),abs(y),1e-10)
        directions[name]={"AD":x,"centered_FD":y,"relative_error":error,"passed":math.isfinite(x) and math.isfinite(y) and abs(x)>1e-8 and error<1e-4}
    assert len(runs)==5
    for identity in identities.values():
        if digest(Path(identity["path"]).read_bytes())!=identity["sha256"]:
            raise ValueError("input changed during probe")
    return {"executed":True,"passed":all(x["passed"] for x in directions.values()),"clock":clock,"input_identities":identities,"original_config_hash":oh,"executed_config_hash":eh,"production_accepted_manifest_exact":True,"online_applied_inputs":cp["applied_inputs"],"objective":"mean all cold-plate solid temperatures at actual final sample","epsilon":epsilon,"directions":directions,"runs":runs,"elapsed_seconds":time.monotonic()-start,"scope":"first-order local accepted thermal waveform amplitudes; fixed graph and static hydraulic/property regime, not JSON/UI/event-time or second order"}

def main():
    from owned_worker import hold_worker_sentinel, run_owned
    ap=argparse.ArgumentParser(description=__doc__)
    for name in NAMES:
        ap.add_argument("--"+name,type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--execute",action="store_true")
    ap.add_argument("--worker",action="store_true",help=argparse.SUPPRESS)
    ap.add_argument("--parent-pid",type=int,help=argparse.SUPPRESS)
    args=ap.parse_args();out=owned(args.output_dir)
    if not out.is_relative_to(HERE):
        ap.error("output must remain in isolated probe directory")
    if args.worker:
        if not args.execute or args.parent_pid is None:
            ap.error("worker requires explicit execute and parent identity")
        fd=hold_worker_sentinel(out,args.parent_pid)
        try:
            bundle,identities,clock=inputs(args);sources=source_guard()
            result=worker(bundle,identities,clock);result["sources"]=sources
            (out/"result.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
            print(json.dumps({"passed":result["passed"],"result":str(out/"result.json")}))
            raise SystemExit(0 if result["passed"] else 1)
        finally:
            os.close(fd)
    # An execute output must be fresh: no reused sentinel or worker identity.
    out.mkdir(parents=True,exist_ok=not args.execute)
    bundle,identities,clock=inputs(args);sources=source_guard()
    if not args.execute:
        result={"executed":False,"scope":"JSON-only production accepted-input preflight; no numerical imports",
                "input_identities":identities,"clock":clock,"sources":sources,"trajectory_cap":5,
                "worker_deadline_seconds":180,"originalInputProvenance":"production normalized input.liquidrun; not a captured browser Blob"}
        (out/"plan.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
        print(json.dumps({"executed":False,"plan":str(out/"plan.json")}))
        return
    command=[sys.executable,str(Path(__file__).resolve())]
    for name in NAMES:
        command+=["--"+name,str(owned(getattr(args,name)))]
    command+=["--output-dir",str(out),"--execute","--worker","--parent-pid",str(os.getpid())]
    env=dict(os.environ);env.update(CUDA_VISIBLE_DEVICES="",OMP_NUM_THREADS="2",OPENBLAS_NUM_THREADS="1",
                                   TMPDIR=str(out),TMP=str(out),TEMP=str(out),PYTHONDONTWRITEBYTECODE="1")
    terminal=run_owned(command,HERE,env,out)
    terminal["input_identities"]=identities
    (out/"worker-terminal.json").write_text(json.dumps(terminal,indent=2)+"\n")
    print(json.dumps(terminal))
    raise SystemExit(0 if terminal["passed"] else 1)

if __name__=="__main__":
    main()
