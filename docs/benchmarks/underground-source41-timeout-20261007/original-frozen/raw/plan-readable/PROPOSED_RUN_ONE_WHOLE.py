"""Prepared exact PRIVATE one-whole runner. Execution requires ROOT authorization.
Default has no action; --preflight-only is read-only. No normal source/service/Git operation.
"""
from pathlib import Path
import argparse,json,hashlib,os,subprocess,time,signal,datetime,re,traceback,shutil,shlex
F=Path(__file__).resolve().parent
P=json.loads((F/'PLAN.json').read_text());B=json.loads((F/'build-source-input-binding.json').read_text());G=json.loads((F/'guards.json').read_text())
OWNER=Path(P['ownerRoot']);R=Path(P['runtimeRoot']);C=Path(P['caseRoot']);M=C/'Mesh';OF=Path('/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni');APP=Path(B['pair']['APP']['path']);LIB=Path(B['pair']['LIB']['path'])
def info(p):
 p=Path(p);h=hashlib.sha256()
 with p.open('rb') as s:
  for b in iter(lambda:s.read(1<<20),b''):h.update(b)
 return {'bytes':p.stat().st_size,'sha256':h.hexdigest()}
def exact(p,v):return info(p)=={k:v[k] for k in ['bytes','sha256']}
def write(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as s:s.write(json.dumps(v,indent=2,sort_keys=True)+'\n')
def role(pid):
 p=Path('/proc')/str(pid);x=(p/'stat').read_text().rsplit(')',1)[1].split()
 return {'pid':pid,'start':x[19],'uid':int(next(l.split()[1] for l in (p/'status').read_text().splitlines() if l.startswith('Uid:'))),'cwd':os.readlink(p/'cwd'),'argv':(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')}
def verify_manifest(path,want):
 path=Path(path);assert info(path)['sha256']==want
 j=json.loads(path.read_text());assert isinstance(j['payload'],list)
 for row in j['payload']:
  rel=Path(row['path']);assert not rel.is_absolute() and '..' not in rel.parts
  q=path.parent/rel;assert q.is_file() and not q.is_symlink() and exact(q,row),str(q)
 return len(j['payload'])
def strict_guard_read():
 errors=[]
 for p,v in G['normalA39'].items():
  if not exact(p,v):errors.append('normalA39:'+p)
 for rel,want in G['main29'].items():
  q=Path(G['mainRoot'])/rel;have=info(q)['sha256'] if q.is_file() else None
  if have!=want:errors.append('main29:'+rel)
 for name,v in G['normalPlatforms'].items():
  q=Path(v['path'])
  if q.exists()!=v['exists'] or q.is_symlink()!=v['symlink'] or (os.readlink(q) if q.is_symlink() else None)!=v['link'] or str(q.resolve())!=v['resolved']:errors.append('platform-link:'+name)
  for rel,m in v['pairFiles'].items():
   if not exact(q/rel,m):errors.append('platform-pair:'+name+':'+rel)
 roles=[]
 for want in G['main8390Kit8301Roles']:
  have=role(want['pid']);small={k:have[k] for k in ['pid','start','cwd','argv']};roles.append(small)
  if small!=want:errors.append('role:'+str(want['pid']))
 if git_head_sha(G['normalIntegrationSource']['root'])!=G['normalIntegrationSource']['requiredIntegrationHead']:errors.append('I-reviewed-checkpoint-HEAD-not-held')
 for rel,want in G['normalIntegrationSource']['plannedAfterSha256'].items():
  q=Path(G['normalIntegrationSource']['root'])/rel
  if not q.is_file() or info(q)['sha256']!=want:errors.append('I-reviewed-source-not-held:'+rel)
 for row in B['actualSource41']:
  if not exact(row['actualBuildInput'],row):errors.append('source41:'+row['relativePath'])
 for name in ['APP','LIB']:
  if not exact(B['pair'][name]['path'],B['pair'][name]):errors.append('owned-pair:'+name)
 return {'held':not errors,'errors':errors,'roles':roles,'normalKitMemoryHeldClaimed':False,'normalSourceMutationPerformed':False}
def guards():
 try:return strict_guard_read()
 except Exception as error:return {"held":False,"errors":["guard-read-error:"+repr(error)],"normalSourceMutationPerformed":False,"normalKitMemoryHeldClaimed":False}
def preflight(manifest_sha,cpu):
 assert cpu=='16-23' and P['CPU']=='16-23' and P['MPI']==8
 assert P['wholeDeadlineSeconds']==300 and P['nativeCLISeconds']==299 and P['nativeRemainingReserveSeconds']==60 and P['finiteCapSeconds']==20 and P['admissionRequiresGreaterThan']==3
 count=verify_manifest(F/'MANIFEST.json',manifest_sha)
 for key in ['sourcePlanManifest','pairedBuildManifest']:
  x=B[key];verify_manifest(x['path'],x['sha256'])
 assert B['sourcePlanManifest']['sha256']==P['sourceManifestSha'] and B['pairedBuildManifest']['sha256']==P['pairedBuildManifestSha']
 own=Path(B['actualOwnedBuildRoot']);assert own.resolve().is_relative_to(OWNER.resolve()) and not (own/'platforms').is_symlink()
 for row in B['actualOwnedBuildFiles']:assert exact(own/row['path'],row)
 for row in B['actualSource41']:assert exact(row['actualBuildInput'],row)
 for name in ['APP','LIB']:
  q=Path(B['pair'][name]['path']);assert q.resolve().is_relative_to(own.resolve()) and q.is_file() and not q.is_symlink() and exact(q,B['pair'][name])
 assert os.access(APP,os.X_OK)
 for table in [B['compilerDependencies'],B['toolchain'],B['pair']['APP']['resolvedLddDependencies'],B['pair']['LIB']['resolvedLddDependencies']]:
  for path,v in table.items():assert exact(path,v),path
 assert (OF/'src').is_symlink() and (OF/'src').resolve()==(OF/'include/OpenFOAM-2412/src').resolve() and (OF/'src/meshTools/lnInclude').is_dir()
 inputs=json.loads(Path(B['inputBindingsSource']).read_text());assert len(inputs)==P['inputCount']==55
 for rel,v in inputs.items():assert exact(Path(B['inputFrozenRoot'])/rel,v)
 flags=json.loads((F/'exact-flags34.json').read_text());assert flags==json.loads(Path(B['flagsSource']).read_text());assert len(flags['combinedExact'])==34 and flags['combinedExact']==dict(flags['previous33Exact'],DCCART_FINITE_CLOSED_COMPONENT_PREFIX='1') and flags['previous33Exact']==dict(flags['baselineExact'],DCCART_FINITE_ADMISSION_OBSERVE='1') and flags['combinedExact']['DCCART_POINT_STAR_FULL_CAPTURE']=='0'
 assert not R.exists() and not R.is_symlink() and C.resolve().is_relative_to(R.resolve()) and R.resolve().is_relative_to(OWNER.resolve())
 artifact=verify_actual_input_artifact()
 before=guards();assert before['held'],before
 return {'manifestPayloadCount':count,'source41':41,'inputFiles':len(inputs),'inputArtifact':artifact,'guards':before,'readOnlyPreflight':True,'nativeExecuted':False}
def git_head_sha(repo):
 repo=Path(repo).resolve();base=Path(G['mainRoot']).resolve();assert repo.is_relative_to(base)
 dot=repo/'.git';git_dir=Path(dot.read_text().split(':',1)[1].strip()) if dot.is_file() else dot
 git_dir=git_dir.resolve();assert git_dir.is_relative_to(base)
 head=(git_dir/'HEAD').read_text().strip()
 if not head.startswith('ref: '):assert re.fullmatch(r'[0-9a-f]{40}',head);return head
 ref=head[5:];common=(git_dir/(git_dir/'commondir').read_text().strip()).resolve() if (git_dir/'commondir').is_file() else git_dir
 assert common.is_relative_to(base);loose=(common/ref).resolve();assert loose.is_relative_to(common)
 if loose.is_file():value=loose.read_text().strip()
 else:
  rows=[line.split() for line in (common/'packed-refs').read_text().splitlines() if line and not line.startswith(('#','^'))]
  matches=[row[0] for row in rows if len(row)==2 and row[1]==ref];assert len(matches)==1;value=matches[0]
 assert re.fullmatch(r'[0-9a-f]{40}',value);return value
def verify_actual_input_artifact():
 x=B['actualInputArtifact'];path=Path(x['manifestPath'])
 assert info(path)['sha256']==x['manifestSha256']==P['actualInputArtifactManifestSha']
 j=json.loads(path.read_text());assert len(j['files'])==87
 for name,v in j['files'].items():
  rel=Path(name);assert not rel.is_absolute() and '..' not in rel.parts
  q=path.parent/rel;assert q.is_file() and not q.is_symlink() and exact(q,v),name
 assert exact(x['stageArchivePath'],x['stageArchive']) and exact(x['membersPath'],x['members']) and exact(x['originalInputArchivePath'],x['originalInputArchive'])
 members=json.loads(Path(x['membersPath']).read_text());rows=json.loads(Path(B['inputBindingsSource']).read_text())
 assert len(members)==len(rows)==P['inputCount']==x['stageFileCount']==55
 assert rows=={r['path']:{k:r[k] for k in ['bytes','sha256']} for r in members}
 assert len([r for r in members if r['path'].endswith('.stl')])==x['stageStlCount']==44
 assert len([r for r in members if r['path'].startswith('Mesh/system/')])==x['systemDictionaryCount']==8
 root=Path(B['inputFrozenRoot']);raw=(root/'model/transport.cfdrun').read_bytes()
 assert hashlib.sha256(raw).hexdigest()==x['rawManifestSha256']==P['actualInputRawManifestSha']
 whole=json.loads(Path(x['wholeReceiptPath']).read_text());stage=json.loads(Path(x['stageReceiptPath']).read_text());contracts=json.loads(Path(x['authoritiesPath']).read_text());inventory=json.loads(Path(x['inventoryPath']).read_text())
 assert whole['ok'] and whole['exportCalls']==1 and whole['all13PhysicalStlBytesUnchanged'] and whole['projectUnchanged'] and whole['liquidGraphUnchanged'] and whole['sourceCaseUnchanged'] and len(whole['files'])==x['originalStlCount']==13
 assert stage['ok'] and stage['sourceAdmission']['inventoryOk'] and stage['sourceAdmission']['eligible'] and stage['actualCaseLockReleased'] and stage['actualExecutionIdentityDuringStage']==x['rawManifestSha256']
 assert stage['sourceAdmission']['contracts']==contracts and set(contracts)=={'singleton','weight'}
 assert all(v['eligible'] is True and v['reasons']==[] and v['rawSha256']==x['rawManifestSha256'] for v in contracts.values())
 assert inventory['verifiedOriginal']['rawSha256']==x['rawManifestSha256'] and inventory['dictionarySHA256']==info(root/'Mesh/system/snappyHexMeshDict')['sha256']
 assert info(x['sourceCasePath'])['sha256']==x['sourceCaseSha256']
 project=json.loads(Path(x['sourceCasePath']).read_text());run=json.loads(raw)
 patches=project['constructions']['floors']['m_52']['geometry']['faces']['top']['patches'];uids=[project['uid']+'/m_52/top/remainder-sheet']+[project['uid']+'/m_52/top/patch/'+v['id'] for v in patches]
 floor=[v for v in run['geometryExport']['sourceSurfaces'].values() if v['sourceObjectUid']=='m_52']
 assert len(patches)==30 and len(floor)==len(set(uids))==31 and sorted(v['sourceFaceUid'] for v in floor)==sorted(uids) and all(v['representation']=='zeroThicknessSheet' for v in floor)
 assert info(root/'Mesh/constant/triSurface/merged_liquid_transport.stl')['sha256']=='c20977276c98e70e64e3636f6e668738316e9c01d1b0aaeed901c0c70088354e'
 assert stage['carrierFacetCount']==248540 and len(stage['actualLoadedCarrierSolids'])==6 and stage['wholeCarrierUnchanged'] and len(inventory['files'])==44
 text=(root/'Mesh/system/dcCartMeshDict').read_text()
 for section,v in [('dcdtuniPointRepairSourceRoleContract',contracts['singleton']),('dcdtuniWeightUnionSourceContract',contracts['weight'])]:
  match=re.search(r'\b'+section+r'\s*\{([^}]+)\}',text);assert match
  body=match.group(1);assert re.search(r'\beligible\s+true\s*;',body) and ('rawSha256 "'+x['rawManifestSha256']+'";') in body and ('rolesDigest "'+v['rolesDigest']+'";') in body and re.search(r'\breasons\s*\(\s*\)\s*;',body)
 return {'artifactSHA':x['manifestSha256'],'rawManifestSHA':x['rawManifestSha256'],'originalSTLs':13,'stageFiles':55,'stageSTLs':44,'systemDictionaries':8,'actualInventoryEligible':True,'floorSourceBytesAppliedAtPreparation':G['normalIntegrationSource']['promotionConfirmedAtPreparation'],'floorGitCheckpoint':G['normalIntegrationSource']['requiredIntegrationHead']}
def environment():
 e={k:v for k,v in os.environ.items() if not k.startswith(('DCCART_','CART_')) and k not in ['LD_LIBRARY_PATH','LD_PRELOAD','LD_AUDIT','LD_DEBUG','LD_DEBUG_OUTPUT','PYTHONPATH','BASH_ENV','ENV']}
 e.update(PATH=str(OF/'bin')+':/usr/bin:/bin',LD_LIBRARY_PATH=str(LIB.parent)+':'+str(OF/'lib/sys-mpich')+':'+str(OF/'lib'),WM_PROJECT_DIR=str(OF),FOAM_LIBBIN=str(OF/'lib'),FOAM_APPBIN=str(OF/'bin'),FOAM_ETC=str(OF/'etc'),FOAM_MPI='sys-mpich',FOAM_SIGFPE='false',CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1',TMPDIR=str(R/'tmp'),TMP=str(R/'tmp'),TEMP=str(R/'tmp'),XDG_CACHE_HOME=str(R/'cache'))
 flags=json.loads((F/'exact-flags34.json').read_text());e.update(flags['combinedExact'])
 assert {k:v for k,v in e.items() if k.startswith(('DCCART_','CART_'))}==flags['combinedExact'] and all(k not in e for k in flags['heldAbsent'])
 return e
def observe(root,ledger):
 todo=[root];seen=set();rss=0
 while todo and len(seen)<256:
  pid=todo.pop()
  if pid in seen:continue
  seen.add(pid);q=Path('/proc')/str(pid)
  try:
   r=role(pid)
   if r['uid']!=os.getuid():continue
   todo.extend(map(int,(q/'task'/str(pid)/'children').read_text().split()));rss+=int(next((l.split()[1] for l in (q/'status').read_text().splitlines() if l.startswith('VmRSS:')),'0'))
   if Path(os.readlink(q/'exe')).resolve()!=APP.resolve():continue
   env=dict(x.split(b'=',1) for x in (q/'environ').read_bytes().split(b'\0') if b'=' in x);rank=int(env[b'PMI_RANK']);assert 0<=rank<8
   lines=(q/'maps').read_text().splitlines();paths=sorted(set(l.split()[-1] for l in lines if 'libdcCartMesh.so' in l));cpu=sorted(os.sched_getaffinity(pid))
   ledger[str(rank)]={**r,'rank':rank,'exe':str(APP),'libraryPaths':paths,'privateMapped':paths==[str(LIB)],'CPUs':cpu}
  except (OSError,ValueError,KeyError):pass
 return rss,bool(todo)
def terminate_owned(proc,ticks):
 if proc is None or proc.poll() is not None:return
 assert role(proc.pid)['start']==ticks and os.getpgid(proc.pid)==proc.pid
 os.killpg(proc.pid,signal.SIGTERM)
 try:proc.wait(timeout=5)
 except subprocess.TimeoutExpired:
  if proc.poll() is None:
   assert role(proc.pid)['start']==ticks and os.getpgid(proc.pid)==proc.pid;os.killpg(proc.pid,signal.SIGKILL);proc.wait()
def step(name,argv,env,deadline,monitor=False):
 start=time.monotonic();v={'argv':argv,'cwd':str(M),'startUTC':datetime.datetime.now(datetime.timezone.utc).isoformat(),'remainingAtStart':deadline-start,'shared300sDeadline':True,'exitCode':None,'timedOut':False};ledger={};peak=0;capped=False;proc=None;ticks=None
 write(R/'logs'/(name+'-started.json'),v)
 try:
  if deadline<=start:v.update(notStarted=True,timedOut=True);return v
  with (R/'logs'/(name+'.stdout')).open('xb') as out,(R/'logs'/(name+'.stderr')).open('xb') as err:
   proc=subprocess.Popen(argv,cwd=M,env=env,stdout=out,stderr=err,start_new_session=True);ticks=role(proc.pid)['start'];write(R/'logs'/(name+'-process.json'),{'pid':proc.pid,'startTicks':ticks,'PGID':os.getpgid(proc.pid)})
   while proc.poll() is None:
    if monitor:
     rss,cap=observe(proc.pid,ledger);peak=max(peak,rss);capped|=cap
    remaining=deadline-time.monotonic()
    if remaining<=0:v['timedOut']=True;terminate_owned(proc,ticks);break
    try:proc.wait(timeout=min(.25,remaining))
    except subprocess.TimeoutExpired:pass
   v['exitCode']=proc.returncode
 except BaseException as error:
  v['collectorException']=repr(error);terminate_owned(proc,ticks);raise
 finally:
  v.update(elapsedSeconds=time.monotonic()-start,endUTC=datetime.datetime.now(datetime.timezone.utc).isoformat(),rankIdentities=ledger,peakOwnedTreeRSSKiB=peak,procObserverCapped=capped,hardWallGuaranteeClaimed=False,cleanupMayFinishAfterDeadline=True)
  write(R/'logs'/(name+'-status.json'),v)
 return v
def wrapper(name,leaf,args):
 # Only known owned paths and fixed argv enter this shell wrapper.
 p=R/('rank-'+name+'.sh');log=shlex.quote(str(R/'logs'))
 text='#!/bin/bash\nset -e\nrank=$PMI_RANK\ntest -n "$rank"\ncase "$rank" in 0|1|2|3|4|5|6|7) ;; *) exit 93 ;; esac\n'
 text+='exec taskset -c "$((16+rank))" '+shlex.quote(str(leaf))+' '+shlex.join(args)+' > '+log+'/'+name+'-rank-${rank}.stdout 2> '+log+'/'+name+'-rank-${rank}.stderr\n'
 with p.open('x') as stream:stream.write(text)
 p.chmod(0o755);return p
def input_check():
 rows=json.loads(Path(B['inputBindingsSource']).read_text());table={rel:info(C/rel) for rel in rows}
 return table,all(table[rel]=={k:v[k] for k in ['bytes','sha256']} for rel,v in rows.items())
def raw_logs(name):
 files=[R/'logs'/(name+'.stdout'),R/'logs'/(name+'.stderr')]
 files += [R/'logs'/(name+'-rank-'+str(rank)+'.'+kind) for rank in range(8) for kind in ['stdout','stderr']]
 return {str(p.relative_to(R)):p.read_text(errors='replace') for p in files if p.is_file()}
def main():
 args=argparse.ArgumentParser();mode=args.add_mutually_exclusive_group(required=True);mode.add_argument('--execute-authorized',action='store_true');mode.add_argument('--preflight-only',action='store_true');args.add_argument('--approved-run-plan-manifest-sha',required=True);args.add_argument('--approved-cpu',required=True);a=args.parse_args()
 pre=preflight(a.approved_run_plan_manifest_sha,a.approved_cpu)
 if a.preflight_only:print(json.dumps(pre,sort_keys=True));return
 assert a.execute_authorized
 for sub in ['logs','tmp','cache']:(R/sub).mkdir(parents=True,exist_ok=False)
 write(R/'authorization.json',{'runPlanManifestSHA':a.approved_run_plan_manifest_sha,'CPU':a.approved_cpu,'onceNative':1,'strictMaximum':1,'normalUIAcceptanceClaimed':False,'explicitExecuteAuthorizedCLI':True});write(R/'guards-before.json',pre['guards'])
 start=time.monotonic();deadline=start+300;s={'strictAccepted':False,'normalPromotionAllowed':False,'native':None,'checker':None,'nativeWholeInvocations':0,'checkerInvocations':0}
 try:
  rows=json.loads(Path(B['inputBindingsSource']).read_text())
  for rel,v in rows.items():
   src=Path(B['inputFrozenRoot'])/rel;dst=C/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst);assert not dst.is_symlink() and dst.stat().st_ino!=src.stat().st_ino and exact(dst,v)
  for rank in range(8):(M/('processor'+str(rank))).mkdir()
  table,held=input_check();write(R/'input-before.json',table);assert held
  native_wrapper=wrapper('native',APP,['-overwrite','-timeLimit','299','-parallel']);check_wrapper=wrapper('checkMesh',OF/'bin/checkMesh',['-allTopology','-allGeometry','-parallel']);env=environment();write(R/'environment-whitelist.json',{k:v for k,v in env.items() if k.startswith(('DCCART_','CART_','FOAM_','WM_')) or k in ['PATH','LD_LIBRARY_PATH','CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','TMPDIR','TMP','TEMP','XDG_CACHE_HOME']})
  native=step('native',['taskset','-c',a.approved_cpu,str(OF/'bin/mpirun'),'-np','8',str(native_wrapper)],env,deadline,True);s['native']=native;s['nativeWholeInvocations']=0 if native.get('notStarted') else 1
  table,held=input_check();write(R/'input-after-native.json',table)
  outputs={}
  for rank in range(8):
   for name in ['points','faces','owner','neighbour','boundary','cellZones','faceZones','pointZones']:
    q=M/('processor'+str(rank))/'constant/polyMesh'/name
    if q.is_file() and not q.is_symlink() and q.stat().st_size>0:outputs[str(q.relative_to(C))]={'bytes':q.stat().st_size,'mtimeNs':q.stat().st_mtime_ns}
  write(R/'mesh-output-before-check.json',outputs);ranks=native['rankIdentities'];loads=not native['procObserverCapped'] and len(ranks)==8 and all(ranks[str(rank)]['privateMapped'] and ranks[str(rank)]['CPUs']==[16+rank] for rank in range(8));part=M/'constant/dcCartPartitionInfo.json';pi=json.loads(part.read_text()) if part.is_file() else None;baffles=isinstance(pi,dict) and pi.get('bafflesSplitByMesher') is True and type(pi.get('nProcs')) is int and pi['nProcs']==8
  post_native_guards=guards();write(R/'guards-after-native-before-checker.json',post_native_guards)
  qualified=native['exitCode']==0 and not native['timedOut'] and held and len(outputs)==64 and loads and baffles and post_native_guards['held']
  s.update(nativeQualified=qualified,inputsHeld=held,actual8RankPrivateLoad=loads,nativeSplitBafflesComplete=baffles);write(R/'native-qualification.json',{k:s[k] for k in ['nativeQualified','inputsHeld','actual8RankPrivateLoad','nativeSplitBafflesComplete']})
  if qualified and time.monotonic()<deadline:
   checker=step('checkMesh',['taskset','-c',a.approved_cpu,str(OF/'bin/mpirun'),'-np','8',str(check_wrapper)],env,deadline);s['checker']=checker;s['checkerInvocations']=0 if checker.get('notStarted') else 1;text='\n'.join(raw_logs('checkMesh').values());ok=bool(re.search(r'(?m)^\s*Mesh OK\.\s*$',text));failed=re.findall(r'(?m)^\s*(?:\*+\s*)?Failed\s+\d+\s+mesh checks.*$',text)
   s.update(literalMeshOK=ok,literalFailedLines=failed,preliminaryStrictStockAccepted=checker['exitCode']==0 and not checker['timedOut'] and ok and not failed and time.monotonic()<=deadline)
  else:s.update(checkerNotStartedReason='native-unqualified-or-no-original-budget',preliminaryStrictStockAccepted=False)
  s['qualityPipelineElapsedSeconds']=time.monotonic()-start
  # Hash collection is evidence, cannot extend quality execution or invent a passing result.
  after={rel:{'bytes':(C/rel).stat().st_size,'mtimeNs':(C/rel).stat().st_mtime_ns} for rel in outputs};s['geometryMetadataHeldDuringChecker']=after==outputs
  write(R/'raw-mesh64-binding.json',{rel:info(C/rel) for rel in outputs});write(R/'full-strict-sets-binding.json',{str(q.relative_to(C)):info(q) for rank in range(8) for q in sorted((M/('processor'+str(rank))/'constant/polyMesh/sets').glob('*')) if q.is_file()})
  raw=raw_logs('native');lines={path:[line for line in txt.splitlines() if any(key in line for key in ['dctwin-finite-','dcCart finite point repair:','Point repair','lineage','coupled','guard failures','dwProposed','Total ='])] for path,txt in raw.items()};write(R/'native-admission-and-guard-lines-raw.json',lines)
  records=[];decode_errors=[]
  for path,txt in raw.items():
   for num,line in enumerate(txt.splitlines(),1):
    if 'dctwin-finite-' not in line:continue
    try:
     pos=line.find('"{');raw=line[pos:] if pos>=0 else line[line.find('{'):]
     record=json.loads(raw)
     if isinstance(record,str):record=json.loads(record)
     if not isinstance(record,dict) or not str(record.get('schema','')).startswith('dctwin-finite-'):raise ValueError('unexpected observer object')
     records.append({'path':path,'line':num,'record':record,'raw':line})
    except (ValueError,TypeError) as error:decode_errors.append({'path':path,'line':num,'error':repr(error),'raw':line})
  write(R/'finite-admission-decoded.json',{'records':records,'decodeErrors':decode_errors,'rawAlwaysAuthoritative':True,'censusNotSearch':True,'proposalNotCommitOrQuality':True,'firstLe3NotFinalGuard':True});s['sourcePhysicalCoupledPassingAuditPendingROOT']=True;s['strictAccepted']=False
 except BaseException:
  s['collectorException']=traceback.format_exc();(R/'collector-exception.txt').write_text(s['collectorException']);raise
 finally:
  after=guards();write(R/'guards-after.json',after);s['normalSourceRolesHeld']=after['held'];s['collectionFinishedElapsedSeconds']=time.monotonic()-start;write(R/'summary.json',s);assert after['held'],after
if __name__=='__main__':main()
