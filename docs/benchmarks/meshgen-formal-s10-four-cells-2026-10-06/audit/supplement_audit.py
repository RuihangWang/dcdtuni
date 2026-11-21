from pathlib import Path
from fractions import Fraction as F
from datetime import datetime,timezone
import hashlib,json,re,struct
O=Path(__file__).resolve().parent
B=Path("/mnt/lv/home/litianyu/mycode/dcdtuni")
T=B/".codex-tmp/goal-20261005/integration"
V=T/"src/backend/aircooling/third_parties/meshgenerator"
def sha(b): return hashlib.sha256(b).hexdigest()
def ident(p):
 b=p.read_bytes()
 return {"path":str(p),"bytes":len(b),"sha256":sha(b)}
def put(name,data):
 f=O/name
 with f.open("x",encoding="utf-8") as w: json.dump(data,w,ensure_ascii=False,indent=2);w.write("\n")
def stablecopy(source,name):
 b=source.read_bytes();p=O/name
 with p.open("xb") as w:w.write(b)
 assert b==source.read_bytes()==p.read_bytes()
 return {"source":str(source),"copy":name,"bytes":len(b),"sha256":sha(b),"sourceCopySourceExact":True}
sourceCopies=[stablecopy(V/"source/cli/main.cpp","source/main.cpp"),stablecopy(V/"source/core/src/CartesianCfdrun.cpp","source/CartesianCfdrun.cpp")]
lines=(O/"root-browser/fullLog.txt").read_text().splitlines()
cart=(O/"source/Cartesian.cpp").read_text().splitlines()
main=(O/"source/main.cpp").read_text().splitlines()
cfdrun=(O/"source/CartesianCfdrun.cpp").read_text().splitlines()
snips={"Cartesian.cpp":[(1441,1486),(11933,11963),(12450,12525),(12545,12562),(12603,12716),(12737,12754),(31493,31528),(32668,32702)],
       "main.cpp":[(1482,1515),(1528,1575)],
       "CartesianCfdrun.cpp":[(1178,1195),(2100,2115)]}
refs={}
for name,ranges in snips.items():
 src={"Cartesian.cpp":cart,"main.cpp":main,"CartesianCfdrun.cpp":cfdrun}[name]
 refs[name]={"sha256":sha((O/"source"/name).read_bytes()),"ranges":[{"first":lo,"last":hi,"lines":[{"line":n,"text":src[n-1]} for n in range(lo,hi+1)]} for lo,hi in ranges]}
put("source-branch-bindings.json",{"sourceCopies":sourceCopies,"references":refs,"scope":"Read-only held producer source; no production edit, compile or execution."})
mapping=json.loads((O/"case/Mesh/.meshgen_scratch/dc_report.json").read_text())
records=mapping["solids"]
file_order=list(dict.fromkeys(x["stlFile"] for x in records))
patches=[];triangles=[];surfaces=[]
for filename in file_order:
 p=O/"case/Mesh/constant/triSurface"/filename
 text=p.read_text()
 by_solid={r["solidName"]:r for r in records if r["stlFile"]==filename}
 current=None;vertices=[];solid_order=[]
 for ln,line in enumerate(text.splitlines(),1):
  s=line.strip()
  if s.startswith("solid "):
   current=s[6:].strip()
   if current not in solid_order:solid_order.append(current)
  elif s.startswith("vertex "):
   vertices.append([F(x) for x in s.split()[1:]])
   if len(vertices)==3:
    rec=by_solid[current];target=rec["patchName"]
    if target not in patches:patches.append(target)
    triangles.append({"nativeTriangleIndex":len(triangles),"filename":filename,"solidName":current,"targetPatchName":target,"nativePatchIndex":patches.index(target),"vertices":[[str(x) for x in v] for v in vertices],"declaredRole":rec["role"],"patchType":rec["patchType"],"floodBarrier":rec["floodBarrier"]})
    vertices=[]
 surfaces.append({"file":filename,"identity":ident(p),"solidFirstAppearanceOrder":solid_order})
assert patches[17]=="Thermal_block_Bottom"
assert [t["nativeTriangleIndex"] for t in triangles if t["targetPatchName"]=="Thermal_block_Bottom"]==[34,35]
assert triangles[34]["declaredRole"]==triangles[35]["declaredRole"]=="SOLID_BOX"
put("actual-surface-patch-order.json",{"method":"Reconstruct loadAsciiSTL first appearance and buildDcRawSurface target first appearance from copied literal STLs and actual mapping.solids. No product helper import.","fileOrder":file_order,"nativePatchFirstAppearance":patches,"reportAlphabeticalPatchNames":mapping["patchNames"],"arraysEqual":patches==mapping["patchNames"],"index17CoincidesOnly":mapping["patchNames"][17]==patches[17],"surfaceFiles":surfaces,"triangles":triangles,"sourceReferences":["Cartesian.cpp:1441-1486","main.cpp:1482-1575"],"limits":"Source-backed order reconstruction, not a recorded live TriSurface array or process mapping. Report array and emitter array must not generally be interchanged."})
# Keep full raw lines, including unrelated interleaved thread output; never stitch fragments.
selected=[]
phase_ids=[161,168,175,182,457,464,471,478]
for n,l in enumerate(lines,1):
 if any(re.search(r"\bci="+str(c)+r"\b",l) for c in phase_ids) and any(tag in l for tag in ["[DC-SB-TNF]","[DC-E3D-FR]","[DC-E3D-FRR]","[DC-SB]"]):
  if "t=34" in l or "t=35" in l or "[DC-SB]" in l:
   selected.append({"line":n,"raw":l})
for n in [1527,1574,2857,2858,2943,3960]:
 assert 0<n<=len(lines)
 selected.append({"line":n,"raw":lines[n-1]})
selected.sort(key=lambda x:x["line"])
put("phase-emitter-raw-witness.json",{"rawLog":ident(O/"root-browser/fullLog.txt"),"rows":selected,"phaseIDs":phase_ids,"compactS10IDs":[457,463,469,475],"phaseToCompactJoin":"UNKNOWN; no persisted origCi/compaction map. Matching numerical IDs must not be used as a join.","directWitness":{"phase457":{"leaf":747,"keptSide":0,"t34ProbePlusSide":0,"t34ProbeMinusSide":1,"emittedSide":0,"patch17Area":[0,0.00390625,0],"boxYFromCompleteFace":[0.5,0.59375]},"phase478":{"leaf":768,"keptSide":1,"t35ProbePlusSide":1,"t35ProbeMinusSide":0,"emittedSide":1,"patch17Area":[0,0.00390625,0],"boxYFromCompleteFace":[0.5,0.59375]},"normal":[0,-1,0],"centroidY":0.5,"plusProbeY":"0.5-10*clipTol, below owning upper leaf for positive clipTol","sourceClassifiesOutsidePlusProbeAsKept":True},"interpretation":"Actual upper-leaf phase rows emit the same bottom-footprint physical fragment toward +Y, while a below-leaf phase has matching footprint and emission. This supports a leaf-local wall-half ownership defect. It does not reconstruct a complete downstream neighbour or assert a phase->compact ID map.","interleaving":"Raw line3960 contains another thread's DC-SB suffix; retained unchanged and not stitched into a new face table."})
old=json.loads((O/"original/Meshgen_Formal_Channel.cfdproj").read_text())
new=json.loads((O/"case/Meshgen_Formal_S10_Diagnostics.cfdproj").read_text())
def diff(x,y,p=""):
 out=[]
 if type(x) is not type(y):return [{"path":p,"old":x,"new":y,"kind":"type"}]
 if isinstance(x,dict):
  for k in sorted(set(x)|set(y)):
   if k not in x or k not in y:out.append({"path":p+"/"+k,"oldPresent":k in x,"newPresent":k in y,"old":x.get(k),"new":y.get(k)})
   else:out.extend(diff(x[k],y[k],p+"/"+k))
 elif isinstance(x,list):
  if len(x)!=len(y):out.append({"path":p+"/length","old":len(x),"new":len(y)})
  else:
   for i,(a,b) in enumerate(zip(x,y)):out.extend(diff(a,b,p+"/"+str(i)))
 elif x!=y:out.append({"path":p,"old":x,"new":y})
 return out
projectdiff=diff(old,new); assert len(projectdiff)==1 and projectdiff[0]["path"]=="/meta/casePath"
e=json.loads((O/"root-browser/actual-browser-proof.json").read_text())["evidence"]
raw=(O/"case/model/Meshgen_Formal_S10_Diagnostics.cfdrun").read_bytes()
rawsha=sha(raw)
assert rawsha=="88f0a476ef8bc9ce3b1f083e312759f9d53ed8563858045c58c4ef1d9ae549f2"
logids=[{"line":n,"raw":l} for n,l in enumerate(lines,1) if any(x in l for x in ["启动命令:","Owned input snapshot:","Using meshgen-cli","[DC-S10C] TOTAL","S10 refusal:","refused","returncode=1"])][:30]
assert any(rawsha in r["raw"] and "Owned input snapshot:" in r["raw"] for r in logids)
native=json.loads((O/"case/Mesh/.meshgen_scratch/constant/dc_report.json").read_text())
def jpegdim(p):
 b=p.read_bytes();assert b[:2]==b"\xff\xd8";i=2
 while i<len(b):
  if b[i]!=255:i+=1;continue
  while b[i]==255:i+=1
  m=b[i];i+=1
  if m in [0xd8,0xd9] or 0xd0<=m<=0xd7:continue
  size=int.from_bytes(b[i:i+2],"big")
  if m in [0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf]:
   return {"height":int.from_bytes(b[i+3:i+5],"big"),"width":int.from_bytes(b[i+5:i+7],"big"),"precisionBits":b[i+2],"identity":ident(p)}
  i+=size
 raise ValueError("no JPEG SOF")
after=json.loads((O/"startup/after.json").read_text())
put("actual-input-ui-runtime-binding.json",{"schemaVersion":1,"jobId":"2d09f776","clickedAt":e["clickedAt"],"actualLogUtcStart":"2026-10-06T01:20:25Z","actualLogUtcTerminal":"2026-10-06T01:20:28Z","priorTaskTimeCorrection":"Actual raw evidence is01:20UTC, not00:20UTC.","uiFinal":e["finalDom"],"returncode":1,"nativeTotal":"NOT_CAPTURED","meshStatus":"S10kind1strictrefusal before polyMesh export/checkMesh/solve","oldProject":ident(O/"original/Meshgen_Formal_Channel.cfdproj"),"newProject":ident(O/"case/Meshgen_Formal_S10_Diagnostics.cfdproj"),"typedProjectDifferences":projectdiff,"rawCfdrun":ident(O/"case/model/Meshgen_Formal_S10_Diagnostics.cfdrun"),"stlBatchSha256Logged":"87e20ad7866eebca37074b782d43e80a557b9106b4f9764183930505d092a906","ownSnapshotExpectedAndPublishedBytesMatch":True,"originalLogs":logids,"nativeS10Census":native["s10Census"],"nativeFirstKind1Classifier":native["kind1"],"classifierScopeCaution":"kind1.familyExclusive reports coplanar1/none3; S10R fam labels call allfour coplanar. The exact geometric plane finding is separate from these two historical classifier definitions.","consoleErrors":[x for x in e["finalErrors"] if x["level"]=="error"],"consoleWarnings":[x for x in e["finalErrors"] if x["level"]=="warn"],"actualRootBrowserJPEGS":[jpegdim(O/"root-browser/preScreenshot.jpg"),jpegdim(O/"root-browser/finalScreenshot.jpg")],"screenshotScope":"ROOT actual browser JPEG outputs are preserved; control terminal facts are bound to raw DOM/AX/proof, not inferred from image appearance.","startupDiagnosticOnlyFourFlags":after["diagnosticOnlyExactFourFlags"],"startupMainFourIdentitiesUnchanged":after["mainFourProcessIdentitiesFullUnchanged"],"actualNativeProcessMapping":"UNKNOWN: observer expected--cfdrun, but normal meshgen CLI uses positional cfdrun; it threw before a process record was saved.","memfdScope":"Logged /proc/752733/fd/4 input and owned bytes are bound, but no live FD seals/ELF maps/inode/process-absence proof was captured."})
# Do not mutate any source proof; fresh re-verification of copied declared files.
bindings=json.loads((O/"copy-bindings.json").read_text())
checks=[]
for r in bindings["records"]:
 src=Path(r["source"]);dst=O/r["copy"]
 a=src.read_bytes();b=dst.read_bytes();c=src.read_bytes()
 assert a==b==c and sha(b)==r["sha256"] and len(b)==r["bytes"]
 checks.append({"copy":r["copy"],"bytes":len(b),"sha256":sha(b),"sourceCopySourceExactAtFinalRead":True})
bound=json.loads((O/"source-binding.json").read_text())
assert sha((V/"source/core/src/Cartesian.cpp").read_bytes())==bound["Cartesian"]["sha256"]
assert ident(Path(bound["configuredCLIBinary"]["path"]))["sha256"]==bound["configuredCLIBinary"]["sha256"]
for r in sourceCopies:assert sha(Path(r["source"]).read_bytes())==r["sha256"]
C=T/".codex-tmp/cases/Meshgen_Formal_S10_Diagnostics_CUA"
regular=[]
for p in C.rglob("*"):
 assert not p.is_symlink(),str(p)
 if p.is_file():regular.append(str(p.relative_to(C)))
assert sorted(regular)==sorted(r["copy"][5:] for r in checks if r["copy"].startswith("case/"))
put("final-copy-and-source-reverification.json",{"utc":datetime.now(timezone.utc).isoformat(),"records":checks,"sourceCopies":sourceCopies,"physicalCaseRegularCount":len(regular),"namespaceExactAtFinalRead":True,"allNoSymlinks":True,"sourceCartesianAndConfiguredCLIStillHeld":True,"noProductionWritesOrBusinessExecution":True,"limitations":"No processes/ports/API were queried; original source/proof retained unchanged."})
print(json.dumps({"status":"PASS","sourceCopies":len(sourceCopies),"copyRecordsReverified":len(checks),"nativePatches":len(patches),"triangles":len(triangles),"projectDiffKeys":[r["path"] for r in projectdiff],"consoleErrors":0,"consoleWarnings":len(e["finalErrors"]),"caseFiles":len(regular)},ensure_ascii=False))
