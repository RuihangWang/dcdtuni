from pathlib import Path
import json,os,time,re,shutil,hashlib,datetime
from urllib.parse import quote
r=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration")
scratch=r/".codex-tmp/pimple-time-boundaries"
dest=scratch/"write-now-control";dest.mkdir(exist_ok=True)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
exe=r/"src/backend/aircooling/third_parties/foam/platforms/linux64GccDPInt32Opt/bin/buoyantBoussinesqPimpleFoam_new"
simple=exe.with_name("buoyantBoussinesqSimpleFoam_new")
assert sha(exe)=="b49ea3b9140e2943d0712bf021c0a9c54d40d17be64c297c4938e753903d4e3b"
assert sha(simple)=="7932473e304c68aba242e1c9947eeb0586c1aebea911b6e567e19e3d85316257"
os.environ["AIR_API_PORT"]="8392"
h=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air/.codex-tmp/api_case.py")
ns={"__file__":str(h.resolve())};exec(h.read_text().split("mode=sys.argv")[0],ns);api=ns["api"]
project=json.loads((r/".codex-tmp/steady-lifecycle-api/SteadyLifecycle_MPI2_channel.cfdproj").read_text())
project["transient"]["durationS"]=.5;project["transient"]["gridS"]=.001
project["runDefaults"]["simulationConfig"].update({
    "endTimeSeconds":.5,"deltaT":.001,"maxDeltaT":.001,"writeIntervalSeconds":.1,
    "adjustTimeStep":True,"startFrom":"startTime"})
proj=dest/"input.cfdproj";proj.write_text(json.dumps(project,ensure_ascii=False,indent=2)+"\n")
export=api("/api/browser/export-case",{"projPath":str(proj)},timeout=180)
(dest/"export.json").write_text(json.dumps(export,indent=2)+"\n")
assert export.get("success")
close=api("/api/browser/close",{},timeout=40)
(dest/"browser-close.json").write_text(json.dumps(close,indent=2)+"\n")
case=r/".codex-tmp/cases/SteadyLifecycle_MPI2";m=case/"model/Steady_lifecycle_MPI2.cfdrun"
(dest/"input.cfdrun").write_bytes(m.read_bytes())
before=set((case/"log").iterdir())
reply=api("/api/run-mesh",{"cfdrunJson":m.read_text(),"cfdrunFilename":m.name,"solveOnly":True},timeout=90)
(dest/"job.json").write_text(json.dumps(reply,indent=2)+"\n")
job=reply["jobId"]
run=None
for _ in range(600):
    added=set((case/"log").iterdir())-before
    if len(added)==1:
        run=added.pop()
        log=run/"log.mpi_solver"
        control=run/"system/controlDict"
        if log.exists() and control.exists():
            raw=log.read_text(errors="replace")
            times=re.findall(r"^Time = ([0-9.eE+-]+)$",raw,re.M)
            # Only the actual PIMPLE phase, after a genuine transport solve.
            if "buoyantBoussinesqPimpleFoam_new -parallel" in raw and times and "Solving for T" in raw:
                # The installed normal NFS watcher has a five-second skew.
                # Wait for its real mtime separation; never alter timestamps.
                if time.time_ns()-control.stat().st_mtime_ns>6_000_000_000:
                    break
    time.sleep(.025)
else:
    raise RuntimeError("PIMPLE did not enter a genuine transport solve within 15 seconds")
status=api("/api/job-status?job_id="+job,timeout=30)
assert not status.get("done"),"Native run completed before the stop request"
original=control.read_text()
assert re.search(r"stopAt\s+endTime;",original)
changed=re.sub(r"(stopAt\s+)endTime;",r"\1writeNow;",original,count=1)
(dest/"controlDict-before").write_text(original)
(dest/"controlDict-request").write_text(changed)
(dest/"native-at-request.log").write_text(raw)
pre=control.stat()
utc=datetime.datetime.now(datetime.timezone.utc).isoformat()
temporary=control.with_name("controlDict.write-now-owned.tmp")
with temporary.open("w") as f:
    f.write(changed);f.flush();os.fsync(f.fileno())
temporary.chmod(pre.st_mode & 0o777)
os.replace(temporary,control)
post=control.stat()
request={"utc":utc,"runPath":str(run),"casePath":str(case),"jobId":job,
         "lastObservedPhysicalTimeS":float(times[-1]),
         "beforeMtimeNs":pre.st_mtime_ns,"afterMtimeNs":post.st_mtime_ns,
         "writeMethod":"owned same-directory atomic replacement; normal filesystem mtime",
         "statusBeforeRequest":status,"pimpleSha256Before":sha(exe),"simpleSha256Before":sha(simple)}
(dest/"stop-request.json").write_text(json.dumps(request,indent=2)+"\n")
print("stop requested",job,"at observed t",times[-1],flush=True)
for _ in range(180):
    status=api("/api/job-status?job_id="+job,timeout=30)
    if status.get("done"):break
    time.sleep(.5)
else:
    raise RuntimeError("Normal writeNow request did not finish within 90 seconds")
(dest/"status.json").write_text(json.dumps(status,indent=2)+"\n")
raw=api("/api/log-stream?job_id="+job,timeout=90)
(dest/"job.sse.log").write_text(raw if isinstance(raw,str) else json.dumps(raw,indent=2))
(dest/"controlDict-final").write_bytes(control.read_bytes())
output=dest/"run";output.mkdir(exist_ok=True)
for p in run.iterdir():
    if p.is_file() and (p.suffix==".json" or p.name.startswith("log.") or p.name.endswith(".log")):
        shutil.copy2(p,output/p.name)
shutil.copytree(run/"system",output/"system",dirs_exist_ok=True)
shutil.copytree(run/"steadyStart",output/"steadyStart",dirs_exist_ok=True)
for partition in sorted(run.glob("processor[0-9]*")):
    for instant in partition.iterdir():
        try:value=float(instant.name)
        except ValueError:continue
        if instant.is_dir() and value>=0:
            shutil.copytree(instant,output/partition.name/instant.name,dirs_exist_ok=True)
response=api("/api/read-file?path="+quote(str(run/"convergence.json"),safe=""),timeout=30)
(dest/"api-convergence-response.json").write_text(json.dumps(response,indent=2)+"\n")
verdict=json.loads(response["content"])
final={"apiStatus":status,"apiVerdict":verdict["verdict"],
       "transient":verdict["transient"],"pimpleSha256After":sha(exe),"simpleSha256After":sha(simple)}
(dest/"actual-receipt.json").write_text(json.dumps(final,indent=2)+"\n")
print(json.dumps(final),flush=True)
assert status["returncode"]==0, "Normal requested stop must exit without killing MPI"
assert verdict["verdict"]=="TRANSIENT_INCOMPLETE"
assert 0<verdict["transient"]["final_time"]<.5
assert final["pimpleSha256After"]==request["pimpleSha256Before"]
