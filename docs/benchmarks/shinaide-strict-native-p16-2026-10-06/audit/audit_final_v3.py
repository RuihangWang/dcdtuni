from pathlib import Path
import json, hashlib, re, struct, shutil, datetime, gc
O=Path(__file__).resolve().parent
T=O.parent.parent
A=T.parent/"air"
C=O/"case"
P=O/"root-browser"
BUILD=T/".codex-tmp/bounded-legacy-journal-normal-build-20261006"
PREVIEW=T/".codex-tmp/bounded-legacy-journal-implementation-20261006"
D=A/"src/backend/aircooling/third_parties/dcCartMesh/src/mesh/dcCartMesh/dcCartCutCells"
def sha(p):
    h=hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
    return h.hexdigest()
def ident(p):
    p=Path(p);return {"path":str(p),"bytes":p.stat().st_size,"sha256":sha(p)}
def put(n,d):
    p=O/n;content=json.dumps(d,ensure_ascii=False,indent=2)+"\n"
    if p.exists():assert p.read_text()==content,str(p)
    else:p.write_text(content)
def labelset(p):
    b=p.read_bytes()
    # The count/list is AFTER the actual FoamFile header. Header "format binary"
    # does not imply binary labelSet body; these written sets have ASCII labels.
    hdr=re.search(rb"\bFoamFile\s*\{[^}]*\}",b,re.S)
    assert hdr, str(p)
    m=re.search(rb"\bclass\s+(cellSet|faceSet|pointSet)\s*;",hdr.group())
    assert m,str(p)
    start=re.search(rb"(?:^|\n)\s*(\d+)\s*\(",b[hdr.end():])
    assert start,str(p)
    n=int(start.group(1));at=hdr.end()+start.end();end=b.find(b")",at)
    assert end>=at,str(p)
    body=b[at:end]
    assert re.fullmatch(rb"\s*(?:\d+\s*)*",body),str(p)
    values=[int(x) for x in re.findall(rb"\d+",body)]
    assert len(values)==n and len(set(values))==n,str(p)
    return values,{"class":m.group(1).decode(),"headerFormat":re.search(rb"\bformat\s+(\w+)\s*;",hdr.group()).group(1).decode(),
        "bodyEncoding":"ASCII label list","emptyInline0List":n==0 and b[at-2:end+1]==b"0()",
        "headerEndByte":hdr.end(),"bodyStartByte":at,"bodyEndByte":end,"count":n}
def diff(a,b,path="$"):
    if type(a) is not type(b):return [{"path":path,"old":a,"new":b,"change":"type"}]
    if isinstance(a,dict):
        r=[]
        for k in list(a)+[x for x in b if x not in a]:
            if k not in a:r.append({"path":path+"."+k,"new":b[k],"change":"added"})
            elif k not in b:r.append({"path":path+"."+k,"old":a[k],"change":"removed"})
            else:r.extend(diff(a[k],b[k],path+"."+k))
        return r
    if isinstance(a,list):
        if len(a)!=len(b):return [{"path":path,"oldLength":len(a),"newLength":len(b),"change":"length"}]
        return [d for i,(x,y) in enumerate(zip(a,b)) for d in diff(x,y,path+"["+str(i)+"]")]
    return [] if a==b else [{"path":path,"old":a,"new":b,"change":"value"}]
failure={"D":{},"W":{},"N":{}}
for rank in range(8):
    for metric,name in [("D","underdeterminedCells"),("W","lowWeightFaces"),("N","nonOrthoFaces")]:
        p=C/f"Mesh/processor{rank}/constant/polyMesh/sets/{name}"
        labels,fmt=labelset(p)
        failure[metric][str(rank)]={**ident(p),"format":fmt,"labels":labels,"count":len(labels)}
tot={k:sum(v["count"] for v in per.values()) for k,per in failure.items()}
assert tot=={"D":831,"W":82,"N":742},tot
put("final-failure-set-audit.json",{"schemaVersion":1,"scope":"Final P16 actual per-rank labelSets; compact IDs remain rank-local; no P15/P16 label migration claim","totals":tot,"ranks":failure})
put("audit-reader-correction.json",{"scope":"Own read-only audit tooling, not native or runner failure",
 "originalObservedFailure":"AssertionError: case/Mesh/processor0/constant/polyMesh/sets/lowWeightFaces",
 "originalCause":"Original count/list parser required whitespace between count and opening parenthesis; actual empty set is 0().",
 "correctedRule":"Find count and parenthesis after FoamFile header, accept optional whitespace including 0(), strictly validate ASCII label body/count/uniqueness.",
 "emptySetIdentity":ident(C/"Mesh/processor0/constant/polyMesh/sets/lowWeightFaces"),
 "otherOwnReadOnlyFailure":"Attempt to read nonexistent dcCartCompleteCavityCurrent.H; actual held file is dcCartCompleteCavityLocalOracle.H. No native input or source was changed."})
e=json.loads((P/"actual-browser-proof.json").read_text())["evidence"]
log=(P/"finalLog.txt").read_text()
raw=(C/"Mesh/log.mpi_solver").read_text()
lines=log.splitlines()
qualityNeedles=["Cell determinant (wellposedness)","Cells with small determinant","Face interpolation weight","low interpolation weights","Face volume ratio","Mesh non-orthogonality Max","non-orthogonality >","Failed 2 mesh checks.","returncode=1","Cell volumes OK","cells:"]
qualityLines=[{"line":i+1,"text":s} for i,s in enumerate(lines) if any(n.lower() in s.lower() for n in qualityNeedles)]
assert "Failed 2 mesh checks." in log and "returncode=1" in log
native=re.findall(r"(?m)^Total ([0-9.]+) s$",raw);assert native==["166.071"],native
assert re.search(r"(?m)^End\s*$",raw)
terminalUTC="2026-10-06T01:02:15Z"
fromiso=lambda s:datetime.datetime.fromisoformat(s.replace("Z","+00:00"))
wall=(fromiso(terminalUTC)-fromiso(e["clickedAt"])).total_seconds()
buttons=e["finalDom"]["buttons"]
assert next(x for x in buttons if x["id"]=="mesh-run-btn")["disabled"] is False
assert next(x for x in buttons if x["id"]=="tab-btn-solver")["disabled"] is True
assert not any("abort" in x["id"].lower() for x in buttons)
assert e["finalErrors"]==[]
imgs=[]
for name in ["preScreenshot.jpg","finalScreenshot.jpg"]:
    # JPEG SOF marker parser: dimensions only, no rendering or browser access.
    b=(P/name).read_bytes();assert b[:2]==b"\xff\xd8"
    i=2;shape=None
    while i<len(b):
        if b[i]!=255:i+=1;continue
        while b[i]==255:i+=1
        marker=b[i];i+=1
        if marker in [0xd8,0xd9] or 0xd0<=marker<=0xd7:continue
        length=int.from_bytes(b[i:i+2],"big")
        if marker in [0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf]:
            shape={"height":int.from_bytes(b[i+3:i+5],"big"),"width":int.from_bytes(b[i+5:i+7],"big"),"components":b[i+7]};break
        i+=length
    assert shape and shape["width"]==1280 and shape["height"]==720
    imgs.append({**ident(P/name),**shape,"scope":"ROOT actual CUA browser screenshot JPEG; separate from product canvas screenshot API"})
put("actual-final-quality-and-ui.json",{"jobId":"11e5fb58","returncode":1,"finalCheckRan":True,"failedMeshChecks":2,
 "cells":5906774,"D":{"count":831,"minimumPrinted":2.4094e-7},"W":{"count":82,"minimumPrinted":.0140854},
 "R":{"count":0,"check":"OK","minimumPrinted":.0105268},
 "N":{"warningFaces":742,"maximumPrintedDegrees":86.9077,"stockHardCheck":"OK"},
 "nativeTotalSeconds":166.071,"nativeLog":ident(C/"Mesh/log.mpi_solver"),
 "actualFinalLog":ident(P/"finalLog.txt"),"actualQualityLines":qualityLines,
 "time":{"actualUIClickedAt":e["clickedAt"],"actualFinalLogUTC":terminalUTC,"clickedToFinalPrintedTimestampSeconds":wall,
  "roundedStartLogToFinalSeconds":194,"finishedCapturedAt":e["finishedCapturedAt"],
  "caution":"Observation capture time is not terminal time; copy/hash/audit time is not native Total."},
 "actualFinalButtons":buttons,"abortAbsentFromCapturedButtons":True,"solveRunButtonNotMounted":"No claim that an unmounted solve-run button was disabled",
 "actualConsoleWarningsErrors":e["finalErrors"],"images":imgs,"scope":"Entire mesh still fails strict acceptance. No solver was enabled or run by this audit; no arbitrary-case timing guarantee.",
 "comparisonToP15":"Same printed headline counts only; topology/label migration and journal causal attribution not independently inferred."})
old=json.loads((P/"Shinaide_TypedAdmissionP15.cfdproj").read_text())
new=json.loads((P/"Shinaide_LegacyJournalP16.cfdproj").read_text())
changes=diff(old,new)
assert len(changes)==1 and changes[0]["path"]=="$.meta.casePath",changes
physical=C/"Shinaide_LegacyJournalP16.cfdproj"
assert sha(physical)==sha(P/"Shinaide_LegacyJournalP16.cfdproj")
model=list((C/"model").glob("*.cfdrun"));assert len(model)==1
inputSHA=sha(model[0]);assert inputSHA=="a074393a4d67f20a39d9f439640b8e72130001521df5a4cf96a66bc92063f73e"
assert inputSHA in log
batch="88987445c6c7775377c443101dcd18a57fd7aff2f9952187ed77322fff3b5194";assert batch in log
helper=json.loads((P/"P16_LegacyJournal_BrowserProof_Only.cfdproj").read_text())
put("project-input-semantic-diff.json",{"oldP15":ident(P/"Shinaide_TypedAdmissionP15.cfdproj"),"newP16":ident(physical),
 "typedJSONDiffs":changes,"onlyMetaCasePathDifferent":True,
 "actualPublishedCFDRUN":ident(model[0]),"matchesActualCLIExpectedAndOwnedSnapshotLogSHA":True,
 "actualSTLBatchSHAInCLIAndSnapshotLog":batch,"STLBatchScope":"Log binding only here; no independent imported helper or physical batch recomputation.",
 "proofHelper":ident(P/"P16_LegacyJournal_BrowserProof_Only.cfdproj"),"proofHelperIsNotPhysicalInput":True,
 "proofHelperContainsDescription":bool(helper.get("meta",{}).get("description"))})
observ=json.loads((P/"actual-native-observation.json").read_text())
ranks=[r for r in observ["processes"] if Path(r["exe"]).name=="dcCartMesh"]
assert len(observ["processes"])==10 and len(ranks)==8
app="c2013d92578935d496b4b9c3b3f2277418cd8da7671f11061ff255d207a4098d"
lib="2c18c05f3da83e9df7dbb85e240561c0a4592da039858ede88fa4aecf5612feb"
for r in ranks:
    assert r["exeIdentity"]["sha256"]==app
    assert any(x["sha256"]==lib for x in r["mappedNativeLibs"])
    assert len(r["cpuAllowed"])==1 and re.search(r"(?:\\t|\s)(1[6-9]|2[0-3])$",r["cpuAllowed"][0])
assert sorted(int(re.search(r"(\d+)$",r["cpuAllowed"][0]).group(1)) for r in ranks)==list(range(16,24))
put("actual-native-binding-audit.json",{"observer":ident(P/"actual-native-observation.json"),
 "observedUTC":observ["utc"],"selectedProcesses":10,"actualNativeRanks":8,
 "appSHA256":app,"librarySHA256":lib,"allEightExeAndMappedLibraryExact":True,"rankCPUAssignments":[r["cpuAllowed"] for r in ranks],"eightRanksAggregateExactlyCPU16to23":True,
 "scope":"10 selected processes as original observer filter specifies, not a full 11-process or final absence claim.",
 "ranks":ranks,"firstObservation":ident(P/"first-native-observation.json")})
writer=[]
for rank in range(8):
    path=C/f"Mesh/processor{rank}/conforming-writer-final-rank{rank}.json"
    with path.open() as f:w=json.load(f)
    assert w["rank"]==rank and w["failures"]==0 and w["status"]=="local-pass-awaiting-global"
    writer.append({**ident(path),"rank":rank,"protectedFaceRecords":len(w["faces"]),"failures":w["failures"],
        "status":w["status"],"baselineImmutable":w["baselineImmutable"],"remoteQualityRecomputed":w["remoteQualityRecomputed"]})
    del w;gc.collect()
assert sum(x["protectedFaceRecords"] for x in writer)==296686
put("writer-final-reported-audit.json",{"records":writer,"totalProtectedFaceRecords":296686,
 "allEightReportedFailuresZero":True,"scope":"Independently read all eight actual final JSON statuses/counts. Does not independently compare every earliest/final geometry value, remote quality, owner remapping or certify full mesh quality.",
 "nativeEndedNormally":True,"nativeEndStillDoesNotMeanStrictMeshAccepted":True})
dest=O/"typed-native-records";dest.mkdir()
for src in sorted((C/"Mesh").glob("conforming-literal-unions-rank*.jsonl")):
    out=dest/src.name;shutil.copyfile(src,out);assert sha(src)==sha(out)
sourceDir=O/"held-source";sourceDir.mkdir()
sources=[]
for name,expected in [("dcCartCompleteCavityLocalOracle.H","be7595c19f89aa857ea54ebe16552c05dce447648305d0f64adede8b048ec326"),
                      ("dcCartCutCells.C","df5a4c5e6a86243f942cd71f12cc1dc69f4b1b3e74c5d6ecea54e0669a83d774")]:
    path=D/name;assert sha(path)==expected;shutil.copyfile(path,sourceDir/name);assert sha(path)==sha(sourceDir/name)
    sources.append({"source":ident(path),"copy":ident(sourceDir/name)})
for name in ["native-after.json","source-applied.json","normal-build-result.json"]:
    shutil.copyfile(BUILD/name,sourceDir/name)
natives=json.loads((BUILD/"native-after.json").read_text())
nativeholds=[]
for key in ["dcCartMesh","libdcCartMesh.so"]:
    q=Path(natives[key]["path"]);assert sha(q)==natives[key]["sha256"]
    nativeholds.append(ident(q))
manifests=[]
for dirname,expected in [
("bounded-legacy-journal-implementation-20261006","cdc7cb985a261300a568e182fe97aeacfcb63723e24322e34ab4bc0324790724"),
("bounded-legacy-journal-independent-review-20261006","3dd0db6c3012a3a0c15123b7025fc5b214b59efd3726aa94fa58de828e252ae8"),
("bounded-legacy-journal-normal-build-20261006","b3bd93992420652aac2bfe628acf4f45b456a9e50d52e876f2f6be855b37b5bd")]:
    q=T/".codex-tmp"/dirname/"manifest.json";assert sha(q)==expected;manifests.append(ident(q))
put("source-and-causal-bindings.json",{"sources":sources,"nativeHeld":nativeholds,"consumedManifests":manifests,
 "currentSourceLines":{"legacyFirstGate":"dcCartCompleteCavityLocalOracle.H:963",
 "journalIdentityCountCapacityAndFingerprint":"dcCartCompleteCavityLocalOracle.H:887-894",
 "disjointBoundedDependencyLoops":"dcCartCompleteCavityLocalOracle.H:895-942",
 "laterGlobalActivePointEquality":"dcCartCompleteCavityLocalOracle.H:989-990",
 "laterAllNativePointDoubleBits":"dcCartCompleteCavityLocalOracle.H:991-994",
 "laterWPacketOverlap":"dcCartCompleteCavityLocalOracle.H:996-1004",
 "laterCellAliveAndRingRoles":"dcCartCompleteCavityLocalOracle.H:1006-1039"},
 "actualFirstRefusal":"legacy for all10 captured views; these later original gates were not reached for those views.",
 "notProved":["Journal completeness/count/resource/overlap subreason","Later global active-point equality on actual P16 captured views","Existence of an actual quality-positive disjoint current candidate"],
 "preservation":"No source/binary change, business command, API/browser/test/build or service action performed."})
# Recheck the frozen copy bindings; do not re-read or hash the live large Mesh.
bound=json.loads((O/"case-copy-manifest.json").read_text())
assert len(bound["files"])==372
for rec in bound["files"]:
    path=Path(rec["copy"]);assert path.stat().st_size==rec["bytes"] and sha(path)==rec["sha256"],rec["relative"]
rootbound=json.loads((O/"root-browser-copy-bindings.json").read_text())
for rec in rootbound["files"]:
    path=Path(rec.get("copy",rec.get("target","")))
    assert path.is_file(),rec
    expected=rec.get("copySHA256",rec.get("sha256"))
    assert sha(path)==expected,rec
put("final-copy-hash-verification.json",{"caseCopyFiles":372,"caseCopyBytes":bound["totalBytes"],
 "caseCopyManifest":ident(O/"case-copy-manifest.json"),"allCaseCopySHAAndBytesReverified":True,
 "rootBrowserFiles":rootbound["fileCount"],"rootBrowserBytes":rootbound["totalBytes"],
 "allRootBrowserCopiesReverified":True,"scope":"Fresh rehash of owned frozen copies; original before/copy/after/source-namespace checks remain in original copy manifest. No second live whole-case scan."})
print(json.dumps({"sets":tot,"nativeTotal":166.071,"UIclickedToFinalPrintedSeconds":wall,
 "capturedViews":10,"currentPreparedCommitted":0,"allCapturedFirstRefusal":"legacy",
 "protectedFinalReportedRecords":296686,"rootBrowserFiles":rootbound["fileCount"],"sourceAndELFHold":True}))
