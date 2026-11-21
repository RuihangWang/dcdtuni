#!/usr/bin/env python3
"""OBS-only paired closure decoder. Never grants quality/source permission."""
from pathlib import Path
import sys,json,math,struct,hashlib
MAX_BYTES=64*1024*1024
class Refusal(ValueError):pass
def need(v,m):
 if not v:raise Refusal(m)
def load(p):
 p=Path(p);need(p.is_file() and not p.is_symlink(),"not-regular-owned-capture")
 need(p.stat().st_size<=MAX_BYTES,"capture-byte-cap")
 return json.loads(p.read_text()),{"path":str(p),"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
def ids(items,key="native"):
 need(isinstance(items,list),"list-missing")
 d={}
 for e in items:
  k=e.get(key);need(type(k) is int and k>=0 and k not in d,"duplicate-or-invalid-"+key);d[k]=e
 return d
def scalar_bits(o,path="root"):
 if isinstance(o,dict):
  if "value" in o and "bits" in o:
   v=o["value"];b=o["bits"]
   need(type(v) in (int,float) and math.isfinite(v),"nonfinite:"+path)
   need(isinstance(b,str) and len(b)==16,"bits-shape:"+path)
   try:raw=bytes.fromhex(b)
   except ValueError:raise Refusal("bits-hex:"+path)
   need(struct.pack(">d",float(v))==raw,"decimal-bits-mismatch:"+path)
  for k,v in o.items():scalar_bits(v,path+"."+k)
 elif isinstance(o,list):
  for i,v in enumerate(o):scalar_bits(v,path+"["+str(i)+"]")
def fingerprint(o):
 need(isinstance(o,dict),"source-fingerprint-missing")
 for k in ["raw","roles","material","producer","boundaryMembership"]:
  s=o.get(k);need(isinstance(s,str) and len(s)==64 and all(c in "0123456789abcdef" for c in s),"source-digest:"+k)
 need(isinstance(o.get("materialKey"),str) and o["materialKey"],"material-key-missing")
 return o
def installed(v,label,expected):
 need(v.get("cachedFieldsOnly") is True and v.get("numericRecomputed") is False,"installed-caches-or-recompute:"+label)
 for k in ["epoch","rank","dependencyOrdinal","source"]:need(v.get(k)==expected[k],"paired-"+k+":"+label)
 c=ids(v.get("cells"));f=ids(v.get("faces"));p=ids(v.get("pointBindings"))
 need(0<len(c)<=4096 and 0<len(f)<=32768 and len(p)<=32768,"installed-local-cap:"+label)
 compactC={e["compact"]:n for n,e in c.items() if not e.get("removed")}
 compactF={e["compact"]:n for n,e in f.items() if not e.get("removed")}
 need(len(compactC)==sum(not e.get("removed") for e in c.values()),"compact-cell-not-unique")
 need(len(compactF)==sum(not e.get("removed") for e in f.values()),"compact-face-not-unique")
 for n,e in c.items():
  if e.get("removed"):need(e.get("compact")==-1,"removed-cell-binding");continue
  need(type(e.get("compact")) is int and 0<=e["compact"]<v["actualCellCount"],"installed-cell-range")
  own=sorted((z["compact"],i) for i,z in f.items() if not z.get("removed") and z["owner"]==e["compact"])
  nei=sorted((z["compact"],i) for i,z in f.items() if not z.get("removed") and z["neighbour"]==e["compact"])
  order=[i for _,i in own+nei]
  need(order and e.get("writerFaceAccumulationOrder")==order,"writer-owner-then-neighbour-order:"+str(n))
 for n,e in f.items():
  if e.get("removed"):need(e.get("compact")==-1,"removed-face-binding");continue
  need(type(e.get("compact")) is int and 0<=e["compact"]<v["actualFaceCount"],"installed-face-range")
  need(e.get("determinantSelected")==bool(e.get("internal") or e.get("coupled")),"actual-role-dmask")
  need(isinstance(e.get("ring"),list) and 3<=len(e["ring"])<=32 and len(e.get("orderedWorldCoordinates",[]))==len(e["ring"]),"installed-literal-ring")
  need(type(e.get("owner")) is int and 0<=e["owner"]<v["actualCellCount"],"installed-global-owner-range")
 for n,e in p.items():
  need(type(e.get("aliasRepresentative")) is int and e["aliasRepresentative"]>=0,"alias-handle")
  need(type(e.get("compact")) is int and -1<=e["compact"]<v["actualPointCount"],"point-binding-range")
  need(("world" in e)==(e["compact"]>=0),"point-binding-world-presence")
 return c,f,p,compactC
def decode(rawPath,finalPath):
 raw,rs=load(rawPath);final,fs=load(finalPath)
 for name,d in [("raw",raw),("final",final)]:
  need(d.get("captureComplete") is True,"capture-not-complete:"+name+":"+str(d.get("refusal")))
  need(d.get("observationOnly") is True and d.get("qualityPermission") is False,"not-observation-only:"+name)
  scalar_bits(d,name)
 need(raw.get("allJournalEntriesCaptured") is True and raw.get("finalInstalledRequired") is True,"raw-incomplete-journal-or-pair")
 need(final.get("rawPacketPath")==str(Path(rawPath)),"raw-final-path-binding")
 view=raw["selectedView"];journal=raw["journal"];fingerprint(view["source"])
 expected={"epoch":view["epoch"],"rank":view["rank"],"source":view["source"],"dependencyOrdinal":raw["dependencyOrdinal"]}
 need(expected["epoch"]>0 and type(expected["rank"]) is int and 0<=expected["rank"]<8,"capture-epoch-rank")
 for k in ["epoch","rank","source"]:need(journal.get(k)==view[k],"journal-view-"+k)
 need(journal.get("complete") is True,"journal-not-complete")
 veC=ids(view["cells"]);veF=ids(view["faces"]);entries=ids(journal["entries"],"index")
 need(0<len(entries)<=4096,"journal-entry-cap")
 need(raw.get("dependencyEntry") in entries,"dependency-entry-missing")
 allOrigins={o["nativeParent"]:o for o in raw["normalOriginCatalog"]}
 need(len(allOrigins)==len(raw["normalOriginCatalog"])<=4096,"normal-origin-uniqueness")
 producer=raw["normalProducer"];need(all(producer.get(k) is True for k in ["complete","completedFluidOnlyPath","boundSameFreshSource"]),"source-reference-not-complete")
 need(producer.get("referenceRank")==expected["rank"],"source-reference-rank")
 pp=ids(producer["parents"]);pf=ids(producer["faces"],"handle");ps=ids(producer["points"])
 for n,o in allOrigins.items():
  need(o.get("rank")==expected["rank"] and n in pp and pp[n].get("valid") is True,"normal-origin-parent-rank")
  need(pp[n]["identity"]==[o["rank"],n,o["level"],o["leaf"],o["piece"]],"normal-origin-identity")
  facets=o["sourceFacets"];need([x[0] for x in facets]==pp[n]["facets"],"normal-origin-facets")
  for h,patch,zone in facets:
   need(h in pf and pf[h]["patch"]==patch and pf[h]["zone"]==zone,"normal-source-facet-role")
   need(all(x in ps for x in pf[h]["ring"]),"normal-source-point-missing")
 rawC=ids(raw["currentRaw"]["cells"]);rawF=ids(raw["currentRaw"]["faces"])
 early=installed(raw["earliestInstalled"],"earliest",expected);late=installed(final["finalInstalled"],"final",expected)
 need(set(early[0])==set(late[0]) and set(early[1])==set(late[1]) and set(early[2])==set(late[2]),"request-native-sets-changed")
 metrics=raw["currentRaw"]["metricFaces"]
 for n in metrics:
  need(n in rawF and n in late[1],"metric-face-missing")
  f=late[1][n]
  if f.get("removed"):continue
  need(f["owner"] in late[3] and (not f["internal"] or f["neighbour"] in late[3]),"metric-endpoint-complete-shape-missing")
 intersection=[]
 members={k:set(view[k]) for k in ["core","outside","secondHalo"]}
 for i,e in entries.items():
  need(e.get("prepared") is True and e.get("complete") is True,"journal-entry-not-complete")
  phases=[]
  for side in ["before","after"]:
   z=e[side];need(z.get("complete") is True,"journal-side-incomplete")
   zc=ids(z["cells"]);zf=ids(z["faces"])
   need(all(n in zf for c in zc.values() for n in c["rawFaces"]),"journal-shape-incomplete")
   phases.append({"side":side,"core":z["core"],"coreIntersectView":{k:sorted(set(z["core"])&v) for k,v in members.items()},"allCapturedCellsIntersectView":{k:sorted(set(zc)&v) for k,v in members.items()},"viewFacesOverlap":sorted(set(zf)&set(veF))})
  b=ids(e["before"]["faces"]);a=ids(e["after"]["faces"])
  changed=[n for n in set(b)|set(a) if b.get(n)!=a.get(n)]
  intersection.append({"index":i,"keep":e["keep"],"drop":e["drop"],"phases":phases,"changedCapturedFaces":sorted(changed),"changedFacesIntersectView":sorted(set(changed)&set(veF))})
 return {"schema":"dcdtuni.paired-prefix-structural-audit/v1","observationOnly":True,"qualityPermission":False,"completePairedStructuralDecode":True,"raw":rs,"final":fs,"identity":expected,"dependency":{k:raw[k] for k in ["dependencyOrdinal","dependencySite","dependencyEntry","dependencyKind","dependencySide","legacyMembership","viewMembership"]},"counts":{"viewCore":len(view["core"]),"viewOutside":len(view["outside"]),"viewSecondHalo":len(view["secondHalo"]),"journalEntries":len(entries),"rawCells":len(rawC),"rawFaces":len(rawF),"requestedCells":len(early[0]),"requestedFaces":len(early[1]),"requestedPoints":len(early[2]),"finalRemovedCells":sum(x.get("removed") is True for x in late[0].values()),"finalRemovedFaces":sum(x.get("removed") is True for x in late[1].values())},"intersections":intersection,"qualification":"Structural/source/bit-format/writer-order decode only; not numeric replay, not a source/quality permission, no production admission."}
if __name__=="__main__":
 try:
  result=decode(sys.argv[1],sys.argv[2]);print(json.dumps(result,indent=2));sys.exit(0)
 except Exception as e:
  print(json.dumps({"observationOnly":True,"qualityPermission":False,"completePairedStructuralDecode":False,"refusal":type(e).__name__+":"+str(e)},indent=2));sys.exit(1)
