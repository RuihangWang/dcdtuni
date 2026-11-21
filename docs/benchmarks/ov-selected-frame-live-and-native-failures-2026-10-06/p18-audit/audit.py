import pathlib,json,hashlib,re,datetime,os
B=pathlib.Path('/mnt/lv/home/litianyu/mycode/dcdtuni')
A=B/'.codex-tmp/goal-20261005/air';IT=B/'.codex-tmp/goal-20261005/integration'
T=A/'.codex-tmp/p18-legacy-journal-readonly-audit-20261006'
OLD=IT/'.codex-tmp/p17-legacy-journal-readonly-audit-20261006'
BUILD=A/'.codex-tmp/p18-legacy-journal-scope-normal-native-build-20261006'
def sha(b):return hashlib.sha256(b).hexdigest()
def write(name,data):
 with (T/name).open('x') as f:f.write(json.dumps(data,indent=2,sort_keys=True)+'\n')
def parse_set(p):
 b=p.read_bytes();s=b.decode('utf8')
 s=re.sub(r'/\*[\s\S]*?\*/','',s);s=re.sub(r'//[^\n]*','',s)
 matches=list(re.finditer(r'(?<![\w.])(\d+)\s*\(\s*([0-9\s]*)\s*\)',s))
 assert len(matches)==1,(str(p),len(matches))
 m=matches[0];n=int(m.group(1));ids=list(map(int,m.group(2).split()))
 assert len(ids)==n and len(set(ids))==n
 return {'declaredCount':n,'parsedCount':len(ids),'uniqueCount':len(set(ids)),'ids':ids,'bytes':len(b),'sha256':sha(b)}
sets=[]
for r in range(8):
 for name in ['underdeterminedCells','lowWeightFaces']:
  new=T/f'raw/Mesh/processor{r}/constant/polyMesh/sets/{name}'
  old=OLD/f'raw/sets/rank{r}-{name}'
  d=parse_set(new);d.update(rank=r,kind=name,P17=str(old),P17SHA256=sha(old.read_bytes()),byteExactP17=new.read_bytes()==old.read_bytes())
  sets.append(d)
assert all(x['byteExactP17'] for x in sets)
totals={name:sum(x['declaredCount'] for x in sets if x['kind']==name) for name in ['underdeterminedCells','lowWeightFaces']}
tele=json.loads((T/'telemetry-analysis.json').read_text())
oldCompare=[];phaseNames=['init','prepare','before','finish','after','closure','census','repair','current']
phaseCounts=[]
for rank,x in enumerate(tele):
 old=[json.loads(l) for l in (OLD/f'raw/legacy-journal-observation-rank{rank}.jsonl').read_text().splitlines()]
 firstOld=[r for r in old if r.get('kind')=='first-invalidating']
 if not firstOld:firstOld=[r for r in old if r.get('completeKnown') and r.get('preComplete') and not r.get('postComplete')]
 oldCompare.append({'rank':rank,'P17SHA256':sha((OLD/f'raw/legacy-journal-observation-rank{rank}.jsonl').read_bytes()),'P17FirstInvalidating':firstOld[0] if firstOld else None,'P18NoInvalidation':not x['trueToFalseInvalidations'],'P18FirstRefusal':x['firstRefusal']})
 s=x['summary']
 for phase,name in enumerate(phaseNames):
  phaseCounts.append({'rank':rank,'phase':phase,'name':name,'attempt':s['attempt'][phase],'pass':s['pass'][phase],'fail':s['fail'][phase],'dependencyReasonCount':s['counts'][phase*16+14]})
unionParity=[]
for r in range(8):
 new=T/f'raw/Mesh/conforming-literal-unions-rank{r}.jsonl';old=OLD/f'raw/conforming-literal-unions-rank{r}.jsonl'
 rows=[json.loads(l) for l in new.read_text().splitlines()]
 unionParity.append({'rank':r,'rows':len(rows),'byRound':{str(k):sum(x.get('round')==k for x in rows) for k in [0,1,2]},'P17SHA256':sha(old.read_bytes()),'P18SHA256':sha(new.read_bytes()),'byteExactP17':new.read_bytes()==old.read_bytes()})
H=T/'source/src/mesh/dcCartMesh/dcCartCutCells/dcCartCompleteCavityLocalOracle.H'
C=T/'source/src/mesh/dcCartMesh/dcCartCutCells/dcCartCutCells.C'
Core=T/'source/src/mesh/dcCartMesh/dcCartCutCells/dcCartCompleteCavityOriginCore.H'
excerpts=[]
for p,ranges in [(H,[(321,387),(903,969),(972,990)]),(C,[(14820,14861),(14992,15031)]),(Core,[(15,18),(92,104)])]:
 lines=p.read_text().splitlines()
 for lo,hi in ranges:excerpts.append({'source':str(p.relative_to(T)),'sha256':sha(p.read_bytes()),'first':lo,'last':hi,'lines':[{'line':i+1,'text':lines[i]} for i in range(lo-1,hi)]})
before=json.loads((T/'source-before.json').read_text());after=[]
for x in before:
 p=pathlib.Path(x['path']);b=p.read_bytes();after.append({'path':str(p),'bytes':len(b),'sha256':sha(b),'held':sha(b)==x['sha256']})
assert all(x['held'] for x in after)
prev=json.loads((BUILD/'after.json').read_text())['runtime6']
runtime=[]
for old in prev:
 p=pathlib.Path('/proc')/str(old['pid'])
 if not p.exists():runtime.append({'pid':old['pid'],'present':False});continue
 raw=(p/'stat').read_text();parts=raw[raw.rfind(')')+2:].split()
 status=dict(l.split(':',1) for l in (p/'status').read_text().splitlines() if ':' in l)
 rec={'pid':old['pid'],'ppid':int(parts[1]),'startTicks':parts[19],'argv':(p/'cmdline').read_bytes().decode().strip('\0').split('\0'),'cwd':os.readlink(p/'cwd'),'exe':os.readlink(p/'exe'),'cpu':status['Cpus_allowed_list'].strip(),'uid':status['Uid'].split()}
 rec['sameSelectedIdentity']=all(rec[k]==old[k] for k in ['pid','ppid','startTicks','argv','cwd','exe','cpu','uid'])
 runtime.append(rec)
write('runtime6-now.json',runtime);write('source-after.json',after)
write('source-excerpts.json',excerpts)
write('failure-sets-and-parity.json',{'totals':totals,'sets':sets,'all16ByteExactP17':True})
write('p17-p18-telemetry-comparison.json',{'ranks':oldCompare,'phaseNames':phaseNames,'phaseCounts':phaseCounts,'scopeFixEliminatedP17Mutation':all(not x['trueToFalseInvalidations'] for x in tele),'noDroppedOrSaturated':all(x['summary']['dropped']==0 and x['summary']['saturated']==0 for x in tele)})
write('union-jsonl-parity.json',unionParity)
log=(T/'raw/Mesh/log.mpi_solver').read_text()
nativeTotal=float(re.search(r'^Total\s+([0-9.]+)\s+s$',log,re.M).group(1))
cells=int(re.search(r'Cut mesh: cells (\d+) over 8 rank',log).group(1))
write('receipt.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'jobId':'f29ead6c','scope':'read-only raw telemetry/source/sets review; no tests/native/API/CLI/UI/service/Git/product edits','strictResult':{'FailedChecks':2,'D':totals['underdeterminedCells'],'W':totals['lowWeightFaces'],'cells':cells,'FailedChecksSource':'ROOT actual React terminal statement; independently parsed exact all-rank D/W sets; log.mpi_solver contains native mesher, not final checker output'},'nativeMesherSeconds':nativeTotal,'rootClickUTC':'2026-10-06T02:24:46.850Z','rootTerminalSecondPrecisionUTC':['2026-10-06T02:28:03Z','2026-10-06T02:28:04Z'],'timingScope':'No independently captured precise pipeline latency; native Total is separate','capturedViews':sum(x['header']['captured'] for x in tele),'firstRefusalRank1':tele[1]['firstRefusal'],'firstRefusalRank2':tele[2]['firstRefusal'],'allJournalComplete':all(x['summary']['complete'] for x in tele),'noFirstInvalidation':all(not x['trueToFalseInvalidations'] for x in tele),'legacyClosurePassedByRank':{str(x['rank']):x['summary']['pass'][5] for x in tele},'source33Held':all(x['held'] for x in after),'runtime6SelectedIdentitiesHeld':all(x.get('sameSelectedIdentity',False) for x in runtime),'lineageBacktraceLimit':'Overlap record has current core.front plus type/equal handles, not legacy entry index/keep/drop or full captured View/LegacySnapshot; cannot identify exact modifying commit or classify unchanged halo overlap from this output.'})
print(json.dumps({'totals':totals,'nativeSeconds':nativeTotal,'cells':cells,'source33Held':all(x['held'] for x in after),'runtime6Held':all(x.get('sameSelectedIdentity',False) for x in runtime),'unionAll8Parity':all(x['byteExactP17'] for x in unionParity),'phaseCountsActive':[x for x in phaseCounts if x['attempt']]},indent=2))
