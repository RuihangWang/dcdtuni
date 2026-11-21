from pathlib import Path
import subprocess,os,time,json,hashlib
b=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/air")
d=b/".codex-tmp/build-installed-writer"
os.sched_setaffinity(0,set(range(8,16)))
env={**os.environ,"PATH":"/mnt/lv/home/litianyu/miniconda3/condabin:"+os.environ["PATH"],
 "CUDA_VISIBLE_DEVICES":"","WM_NCOMPPROCS":"2","TMPDIR":str(d/"tmp"),
 "TMP":str(d/"tmp"),"TEMP":str(d/"tmp"),"XDG_CACHE_HOME":str(d/"cache"),
 "CCACHE_DIR":str(d/"cache/ccache"),"OPENBLAS_NUM_THREADS":"1","OMP_NUM_THREADS":"1"}
cmd=["bash","src/backend/aircooling/third_parties/dcCartMesh/deploy.sh","--cart-only","--no-test"]
start=time.monotonic()
with (d/"build-attempt1.log").open("wb") as log:
 p=subprocess.run(cmd,cwd=b,env=env,stdout=log,stderr=subprocess.STDOUT)
receipt={"returncode":p.returncode,"wallSeconds":time.monotonic()-start,
 "command":cmd,"cpuAffinity":sorted(os.sched_getaffinity(0)),
 "CUDA_VISIBLE_DEVICES":"","WM_NCOMPPROCS":2,"TMPDIR":str(d/"tmp"),
 "XDG_CACHE_HOME":str(d/"cache"),"nativeCaseExecuted":False,"serviceRestarted":False}
(d/"build-attempt1.json").write_text(json.dumps(receipt,indent=2)+"\n")
print(json.dumps(receipt),flush=True)
print("\n".join((d/"build-attempt1.log").read_text(errors="replace").splitlines()[-35:]),flush=True)
raise SystemExit(p.returncode)
