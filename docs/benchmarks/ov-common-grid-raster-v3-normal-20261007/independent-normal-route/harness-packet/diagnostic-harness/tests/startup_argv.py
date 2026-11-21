from pathlib import Path
import os,sys,subprocess,json,hashlib,time
D=Path(__file__).resolve().parents[1];B=D.parents[1];rel='src/frontend/omniverse/cfd_demo/scripts/start_demo.sh';script=D/'runtime'/rel
source=D/'dependency/ROOT-launch-environment-whitelist.json'
assert hashlib.sha256(source.read_bytes()).hexdigest()=='2ff1209b9da78de9dba4ecc09f79d910630b503b55f61ef61a882f2e5d9c66ff'
flags={k:v for k,v in json.loads(source.read_text()).items()if k.startswith(('DCCART_','CART_'))};assert len(flags)==32
build=D/'runtime/private-recorder';build.mkdir(exist_ok=True)
wrapper=build/'omni.cae_streaming.kit.sh';rec=build/'record.py'
rec.write_text("import os,sys,json\nfrom pathlib import Path\nPath(os.environ['PRIVATE_RECORD']).write_text(json.dumps({'argv':sys.argv[1:],'flags':{k:v for k,v in os.environ.items()if k.startswith(('DCCART_','CART_'))},'resource':{k:os.environ.get(k)for k in ['DC_CFD_GPU','CUDA_VISIBLE_DEVICES','DISPLAY','DC_CFD_SIGNAL_PORT','DC_CFD_STREAM_PORT','DC_CFD_RESOLVED_ACTIVE_GPU']}},indent=2)+'\\n')\n")
wrapper.write_text('#!/bin/bash\nexec '+sys.executable+' '+str(rec)+' "$@"\n');wrapper.chmod(0o755)
icd=build/'fixture-icd.json';icd.write_text('{}');vtk=build/'vtk-site';vtk.mkdir(exist_ok=True)
results=[]
for label,value,phase in [('before','bounded-display-v1','before'),('off',None,'after'),('wrong','1','after'),('wrong-suffix','bounded-display-v1 ','after'),('on','bounded-display-v1','after')]:
 script.write_bytes((D/phase/rel).read_bytes());runtime=D/f'runtime/.codex-tmp/argv-{label}';runtime.mkdir(parents=True,exist_ok=True);(runtime/'supervisor.pid').write_text(str(os.getpid()))
 output=D/f'logs/startup-{label}-argv.json';env=dict(os.environ,**flags,DC_CFD_KIT_BUILD=str(build),DC_CFD_RUNTIME_DIR=str(runtime),DC_CFD_SUPERVISOR_PID=str(os.getpid()),DC_CFD_GPU='0',DC_CFD_RESOLVED_GPU='0',DC_CFD_RESOLVED_UUID='GPU-private-cpu-recorder',DC_CFD_RESOLVED_PCI='0000:fixture',DC_CFD_RESOLVED_ACTIVE_GPU='0',DC_CFD_RESOLVED_ICD=str(icd),DC_CFD_SIGNAL_PORT='8302',DC_CFD_STREAM_PORT='48000',DC_CFD_DISPLAY=':99',DC_CFD_VTK_SITE=str(vtk),PRIVATE_RECORD=str(output))
 env.pop('DC_CFD_BOUNDED_DISPLAY_READBACK',None)
 if value is not None:env['DC_CFD_BOUNDED_DISPLAY_READBACK']=value
 cmd=['bash',str(script),'--supervised-child'];t=time.monotonic();p=subprocess.run(cmd,cwd=D,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
 for k,v in [('stdout',p.stdout),('stderr',p.stderr)]: (D/f'logs/startup-{label}.{k}').write_bytes(v)
 results.append({'label':label,'optIn':value,'sourceSha256':hashlib.sha256(script.read_bytes()).hexdigest(),'command':cmd,'returncode':p.returncode,'elapsed':time.monotonic()-t,'affinity':sorted(os.sched_getaffinity(0))});assert p.returncode==0,(label,p.stderr.decode());assert output.exists()
script.write_bytes((D/'after'/rel).read_bytes())
base=json.loads((D/'logs/startup-before-argv.json').read_text());arg='--/exts/dc.cfd_bridge/boundedDisplayReadback/enabled=true'
# Only runtime-specific log/portable roots differ among five owned directories.
def normalized(v):return [a.replace('argv-before','argv-PHASE').replace('argv-off','argv-PHASE').replace('argv-wrong-suffix','argv-PHASE').replace('argv-wrong','argv-PHASE').replace('argv-on','argv-PHASE')for a in v]
for row in results[1:]:
 v=json.loads((D/f"logs/startup-{row['label']}-argv.json").read_text());assert v['flags']==flags and v['resource']==base['resource'];expected=normalized(base['argv'])+([arg]if row['label']=='on'else []);assert normalized(v['argv'])==expected;assert sum(a==arg for a in v['argv'])==(1 if row['label']=='on'else 0)
 for a in ['--/app/renderer/resolution/width=1600','--/app/renderer/resolution/height=900','--/app/window/width=1600','--/app/window/height=900','--/renderer/activeGpu=0','--/renderer/multiGpu/enabled=false','--/exts/omni.kit.livestream.app/primaryStream/signalPort=8302','--/exts/omni.kit.livestream.app/primaryStream/streamPort=48000']:assert a in v['argv']
(D/'startup-argv-results.json').write_text(json.dumps({'cases':results,'whitelistSha256':hashlib.sha256(source.read_bytes()).hexdigest(),'flagCount':len(flags),'rawArgvPreserved':True,'comparison':'only owned runtime directory phase-name normalized; all raw argv saved','scope':'actual full start_demo supervised-child shell with owned CPU argv recorder, fake resolved UUID/ICD; no Kit/GPU/supervisor/service/browser launched'},indent=2)+'\n')
print('5 actual startup shell argv cases PASS; original 32 DCCART flags, GPU/ports/resolution args held; no Kit executed')
