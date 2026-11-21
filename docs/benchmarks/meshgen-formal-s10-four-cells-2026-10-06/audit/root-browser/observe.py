from pathlib import Path
import os,json,time,hashlib,datetime
it=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration")
out=it/".codex-tmp/meshgen-formal-s10-root-observer-20261006/actual-process-observations.json"
cli=it/"src/backend/aircooling/third_parties/meshgenerator/platforms/linux-x86_64-gcc-release/bin/meshgen-cli"
seen={};deadline=time.monotonic()+45
while time.monotonic()<deadline:
 for z in Path("/proc").iterdir():
  if not z.name.isdigit() or int(z.name) in seen:continue
  try:
   argv=[a.decode() for a in z.joinpath("cmdline").read_bytes().split(b"\0")[:-1]]
   if not argv or not any("meshgen-cli" in a for a in argv):continue
   if str(cli) not in argv:continue
   status=z.joinpath("status").read_text();uid=next(l for l in status.splitlines() if l.startswith("Uid:")).split()[1:];assert uid==["1002"]*4
   st=z.joinpath("stat").read_text();tail=st[st.rfind(")")+2:].split();env=dict(v.split("=",1) for v in z.joinpath("environ").read_text().split("\0") if "=" in v)
   rec={"pid":int(z.name),"ppid":int(tail[1]),"startTicks":int(tail[19]),"utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"argv":argv,"exe":str(z.joinpath("exe").resolve()),"exeSHA256":hashlib.sha256(z.joinpath("exe").read_bytes()).hexdigest(),"uid":uid,"cpu":next(l for l in status.splitlines() if l.startswith("Cpus_allowed_list:")).split(":")[1].strip(),"cwd":str(z.joinpath("cwd").resolve()),"cuda":env.get("CUDA_VISIBLE_DEVICES"),"flags":{k:v for k,v in env.items() if k.startswith(("DCCART_","CART_"))},"maps":z.joinpath("maps").read_text()}
   idx=argv.index("--cfdrun");f=Path(argv[idx+1]);v=f.read_bytes();rec["actualCfdrun"]={"path":str(f),"bytes":len(v),"sha256":hashlib.sha256(v).hexdigest()}
   seen[int(z.name)]=rec;out.write_text(json.dumps({"scope":"ROOT read-only actual normal React CLI observer; no execution of native binary or business command","records":list(seen.values())},indent=2));print("OBSERVED",z.name,flush=True)
  except (FileNotFoundError,ProcessLookupError,PermissionError):pass
 time.sleep(.1)
if not out.exists():out.write_text(json.dumps({"records":[],"noMatchingProcessObserved":True}))
print("DONE",len(seen),flush=True)
