from pathlib import Path
import os,json,copy,time,hashlib,shutil
r=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration")
scratch=r/".codex-tmp/pimple-time-boundaries"
os.environ["AIR_API_PORT"]="8392"
h=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/api_case.py")
ns={"__file__":str(h.resolve())};exec(h.read_text().split("mode=sys.argv")[0],ns)
api=ns["api"]
base=json.loads((r/".codex-tmp/steady-lifecycle-api/SteadyLifecycle_MPI2_channel.cfdproj").read_text())
cases=[
    ("dt-over-end",.005,.1,1,True,False),
    ("nondivisible",.026,.01,.01,False,False),
    ("breakpoint",.011,.004,.004,False,True),
]
case=r/".codex-tmp/cases/SteadyLifecycle_MPI2"
manifest=case/"model/Steady_lifecycle_MPI2.cfdrun"
for name,end,dt,maxdt,adjust,step in cases:
    dest=scratch/("after-"+name);dest.mkdir(exist_ok=True)
    project=copy.deepcopy(base)
    project["transient"]["durationS"]=end;project["transient"]["gridS"]=.001
    sc=project["runDefaults"]["simulationConfig"]
    sc.update({"endTimeSeconds":end,"deltaT":dt,"maxDeltaT":maxdt,
               "writeIntervalSeconds":.001,"adjustTimeStep":adjust,"startFrom":"startTime"})
    if step:
        project["transient"]["tracks"][0]["keyframes"]=[
            {"t":0,"value":22},{"t":.003,"value":24,"rampS":0}]
    proj=dest/"input.cfdproj";proj.write_text(json.dumps(project,ensure_ascii=False,indent=2)+"\n")
    export=api("/api/browser/export-case",{"projPath":str(proj)},timeout=180)
    (dest/"export.json").write_text(json.dumps(export,indent=2)+"\n")
    if not export.get("success"):raise RuntimeError("React export failed")
    (dest/"input.cfdrun").write_bytes(manifest.read_bytes())
    close=api("/api/browser/close",{},timeout=40)
    (dest/"browser-close.json").write_text(json.dumps(close,indent=2)+"\n")
    before=set((case/"log").iterdir())
    reply=api("/api/run-mesh",{"cfdrunJson":manifest.read_text(),
                             "cfdrunFilename":manifest.name,"solveOnly":True},timeout=90)
    (dest/"job.json").write_text(json.dumps(reply,indent=2)+"\n")
    job=reply["jobId"]
    for iteration in range(120):
        status=api("/api/job-status?job_id="+job,timeout=30)
        if status.get("done"):break
        time.sleep(.5)
    else:raise RuntimeError("Native API time-boundary run did not finish within a minute")
    (dest/"status.json").write_text(json.dumps(status,indent=2)+"\n")
    raw=api("/api/log-stream?job_id="+job,timeout=60)
    (dest/"job.sse.log").write_text(raw if isinstance(raw,str) else json.dumps(raw,indent=2))
    added=set((case/"log").iterdir())-before
    if len(added)!=1:raise RuntimeError("Ambiguous native run directory")
    run=added.pop()
    (dest/"run-location.json").write_text(json.dumps({"runPath":str(run)},indent=2)+"\n")
    output=dest/"run";output.mkdir(exist_ok=True)
    for p in run.iterdir():
        if p.is_file() and (p.suffix==".json" or p.name.startswith("log.") or p.name.endswith(".log")):
            shutil.copy2(p,output/p.name)
    shutil.copytree(run/"system",output/"system",dirs_exist_ok=True)
    for partition in sorted(run.glob("processor[0-9]*")):
        for instant in partition.iterdir():
            try:numeric=float(instant.name)
            except ValueError:continue
            if instant.is_dir() and numeric>=0:
                shutil.copytree(instant,output/partition.name/instant.name,dirs_exist_ok=True)
    print(name,job,status,"run",run.name,flush=True)
    if status.get("returncode")!=0:raise RuntimeError("Native run failed; preserved artifacts")
