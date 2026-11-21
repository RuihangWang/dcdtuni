from pathlib import Path
import json,hashlib,re,sys,time
I=Path("/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/goal-20261005/integration")
T=Path(__file__).resolve().parent
sys.path.insert(0,str(I/"src"))
from backend.aircooling.runner.point_repair_source_contract import collect_actual_inventory,digest
from backend.aircooling.runner.cfdrun_schema import CfdrunV2
from backend.aircooling.runner.cfdrun_reader import _materialise_boundary_bindings,_materialise_porous_bindings
from backend.aircooling.runner.bridge_bc import BridgeBCRegistry
start=time.monotonic();records=[]
for tag,case in [("P19","Shinaide_DependencyBindingP19_CUA"),("P20","Shinaide_CompletePrefixP20_CUA")]:
 D=I/".codex-tmp/cases"/case
 rawpath=next((D/"model").glob("*.cfdrun"));rawbytes=rawpath.read_bytes();raw=json.loads(rawbytes);sha=hashlib.sha256(rawbytes).hexdigest()
 # Exact normal preflight payload schema, reconstructed from actual pinned
 # original inputs. This read-only audit never grants or renders authority.
 orig={k:dict(path=v["path"],contentHash=v["contentHash"]) for k,v in raw["geometryExport"]["stlFiles"].items()}
 proof=dict(version=1,rawSha256=sha,verifiedFiles=orig)
 snappy=(D/"Mesh/system/snappyHexMeshDict").read_bytes()
 inv=collect_actual_inventory(raw,tri_surface_dir=D/"Mesh/constant/triSurface",snappy_bytes=snappy,wall_pair_zones={"Raised_Floor","merged_wall_baffle"},verified_original_receipt=proof)
 registry=BridgeBCRegistry();model=CfdrunV2.model_validate(raw)
 _materialise_boundary_bindings(model,registry);_materialise_porous_bindings(model,registry)
 payload=dict(files=inv["files"],regions=sorted(inv["regions"],key=lambda x:(x.get("patchName",""),x.get("file",""))),registry={n:s.model_dump(mode="json") for n,s in registry.items()},refinementOnly=inv.get("refinementOnly",{}),sourceSurfaceMetadata=inv.get("sourceSurfaceMetadata",{}),original=proof,dictionarySHA256=inv["dictionarySHA256"])
 roles=digest(payload)
 emitted=re.search(r'rolesDigest "([0-9a-f]{64})"',(D/"Mesh/system/dcCartMeshDict").read_text()).group(1)
 assert roles==emitted,(tag,roles,emitted)
 (T/(tag+"-roles-preimage.json")).write_text(json.dumps(payload,indent=2,sort_keys=True,allow_nan=False)+"\n")
 (T/(tag+"-actual-inventory.json")).write_text(json.dumps(inv,indent=2,sort_keys=True,allow_nan=False)+"\n")
 records.append(dict(tag=tag,rawPath=str(rawpath),rawSHA256=sha,reproducedRolesDigest=roles,emittedRolesDigest=emitted,formulaMatch=True,preimage=payload))
def diffs(a,b,p=""):
 if type(a)!=type(b):return [dict(path=p,before=a,after=b)]
 if isinstance(a,dict):
  r=[]
  for k in sorted(set(a)|set(b)):r.extend(diffs(a.get(k),b.get(k),p+"/"+k))
  return r
 if isinstance(a,list):
  if len(a)!=len(b):return [dict(path=p,before=a,after=b)]
  r=[]
  for i,(x,y) in enumerate(zip(a,b)):r.extend(diffs(x,y,p+"/"+str(i)))
  return r
 return [] if a==b else [dict(path=p,before=a,after=b)]
changes=diffs(records[0]["preimage"],records[1]["preimage"])
report=dict(scope="Read-only production Python inventory/typed BC producer replay; no authority or mesh action",seconds=time.monotonic()-start,formula="sha256(json.dumps(files, sorted regions, full typed registry, refinementOnly, sourceSurfaceMetadata, original verifiedRaw+files proof, dictionarySHA256; sort_keys=True,separators=(',',':'),allow_nan=False))",records=[{k:v for k,v in r.items() if k!="preimage"} for r in records],actualFormulaDifferences=changes,onlyPinnedRawSHAChanged=changes==[dict(path="/original/rawSha256",before=records[0]["rawSHA256"],after=records[1]["rawSHA256"])],qualityPermission=False)
(T/"result.json").write_text(json.dumps(report,indent=2)+"\n")
print(json.dumps(report,indent=2))
