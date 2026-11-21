from pathlib import Path
import json,subprocess,os,time
V=Path(__file__).resolve().parent;P=V.parent/"complete-cavity-authority-20261006"
start=time.monotonic();deadline=start+30
env=dict(os.environ,CUDA_VISIBLE_DEVICES="",OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",TMPDIR=str(V/"tmp"),WM_PROJECT_DIR="/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni",FOAM_ETC="/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/etc")
j=json.loads((P/"actual-of-link-first.json").read_text());cmd=j["command"][:]
cmd=[str(V/"actual_of_cap_harness.cpp") if x==str(P/"actual_of_harness.cpp") else str(V/"tmp/cap_harness") if x==str(P/"tmp/actual_of_harness") else x for x in cmd]
records=[]
def run(name,command):
    remaining=deadline-time.monotonic()
    if remaining<=0:raise TimeoutError("authorized 30s cap")
    t=time.monotonic();r=subprocess.run(command,env=env,capture_output=True,text=True,timeout=remaining)
    (V/(name+".log")).write_text(r.stdout+r.stderr)
    row=dict(command=command,exitCode=r.returncode,elapsed=time.monotonic()-t)
    (V/(name+".json")).write_text(json.dumps(row,indent=2)+"\n");records.append(row)
    print(name,r.returncode,row["elapsed"],r.stdout,r.stderr[-500:],flush=True);return r.returncode==0
ok=run("one-authorized-actualOF-harness-compile",cmd)
if ok:
    for case in ["early_empty","early_oversized"]:
        ok=run(case,["taskset","-c","8-15",str(V/"tmp/cap_harness"),str(P/"true.dict"),case]) and ok
(V/"tests-summary.json").write_text(json.dumps(dict(allPass=ok,elapsed=time.monotonic()-start,deadlineSeconds=30,newCases=2,compilerCalls=1,repeatedExistingTests=0,noBusiness=True,records=records),indent=2)+"\n")
