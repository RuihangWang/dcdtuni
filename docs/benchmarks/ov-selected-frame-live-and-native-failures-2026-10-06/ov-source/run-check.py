import os,sys,subprocess,json,time,hashlib
from pathlib import Path
T=Path(__file__).resolve().parents[2]
O=Path(__file__).resolve().parent
name=sys.argv[1];cmd=sys.argv[2:]
env=os.environ.copy();env.update(TMPDIR=str(O/"owned-tmp"),TEMP=str(O/"owned-tmp"),TMP=str(O/"owned-tmp"),PYTHONDONTWRITEBYTECODE="1",CUDA_VISIBLE_DEVICES="",OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1",npm_config_cache=str(O/"npm-cache"))
cwd=T/"src/frontend/react" if cmd[0]=="npm" else T
start=time.monotonic()
with (O/(name+".stdout.txt")).open("wb") as out:
 r=subprocess.run(cmd,cwd=cwd,env=env,stdout=out,stderr=subprocess.STDOUT)
(O/(name+".json")).write_text(json.dumps({"command":cmd,"cwd":str(cwd),"returncode":r.returncode,"elapsedSeconds":time.monotonic()-start},indent=2)+"\n")
print(name,r.returncode)
sys.exit(r.returncode)
