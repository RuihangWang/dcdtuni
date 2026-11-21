from pathlib import Path
import os,sys,subprocess,time,json,hashlib
Q=Path(__file__).resolve().parent
label=sys.argv[1];cmd=sys.argv[2:]
env=dict(os.environ)
env.update(TMPDIR=str(Q/"tmp"),TMP=str(Q/"tmp"),TEMP=str(Q/"tmp"),CUDA_VISIBLE_DEVICES="",OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",PYTHONDONTWRITEBYTECODE="1",NODE_COMPILE_CACHE=str(Q/"tmp/node-compile-cache"),npm_config_cache=str(Q/"tmp/npm-cache"),OV_PRIVATE_TREE=str(Q/"after"))
start=time.monotonic();p=subprocess.run(cmd,cwd=Q,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
f=Q/"logs"/(label+".stdout.txt");f.write_bytes(p.stdout)
o={"command":cmd,"returncode":p.returncode,"elapsedSeconds":time.monotonic()-start,"stdout":{"path":str(f.relative_to(Q)),"bytes":len(p.stdout),"sha256":hashlib.sha256(p.stdout).hexdigest()},"scope":"Own copied private source; no production mutation, Kit, converter, solver, API, browser"}
(Q/"logs"/(label+".json")).write_text(json.dumps(o,indent=2)+"\n")
print(json.dumps(o));print(p.stdout.decode(errors="replace"))
sys.exit(p.returncode)
