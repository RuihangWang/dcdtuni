#!/usr/bin/env python3
"""Controlled CPU test cohorts. No server, real Kit, GPU, or normal mutation."""
import argparse, hashlib, json, os, pathlib, subprocess, sys, time
O=pathlib.Path(__file__).resolve().parent
P='/mnt/lv/home/litianyu/miniconda3/envs/dcdtuni/bin/python'
parser=argparse.ArgumentParser();parser.add_argument('version',choices=('baseline','current','candidate'));parser.add_argument('--startup-only',action='store_true');parser.add_argument('--lifecycle-only',action='store_true');parser.add_argument('--label');args=parser.parse_args()
root=O/args.version; tests=root/'src/frontend/omniverse/cfd_demo/tests'
files=['test_ov_startup_lifecycle.py'] if args.startup_only else ['test_ov_startup_lifecycle.py','test_display_frame_order.py','test_render_command_routing.py','test_render_output_observations.py']
if not args.startup_only and args.version!='baseline':files+=['test_owned_output_layout.py']
if args.version=='candidate' and not args.startup_only:files+=['test_dataset_scene_race.py']
if args.lifecycle_only:files=['test_display_frame_order.py','test_render_command_routing.py','test_dataset_scene_race.py']
label=args.label or args.version+('-startup' if args.startup_only else '')
assert label.replace('-','').isalnum(), 'label must be a bounded simple name'
assert len(label)<=64
logs=O/'logs';logs.mkdir(exist_ok=True)
assert not (logs/(label+'.status.json')).exists(), 'immutable run already recorded'
runtime=root/'.codex-tmp'/label;runtime.mkdir(parents=True,exist_ok=True)
argv=['taskset','-c','36',P,'-m','pytest','-q','-o','addopts=','-o','cache_dir='+str(runtime/'cache'),'--basetemp',str(runtime/'pytest-temp'),'--junitxml',str(logs/(label+'.xml'))]+[str(tests/x) for x in files]
env=dict(os.environ,CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1',PYTHONPYCACHEPREFIX=str(runtime/'pycache'),TMPDIR=str(runtime/'tmp'))
pathlib.Path(env['TMPDIR']).mkdir()
start=time.monotonic()
with (logs/(label+'.stdout')).open('wb') as out,(logs/(label+'.stderr')).open('wb') as err:
 process=subprocess.Popen(argv,cwd=root,env=env,stdout=out,stderr=err)
 rc=process.wait(timeout=120)
record={'argv':argv,'cwd':str(root),'elapsedSeconds':time.monotonic()-start,'returncode':rc,'pid':process.pid,'env':{k:env[k] for k in ('CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','PYTHONDONTWRITEBYTECODE','PYTHONPYCACHEPREFIX','TMPDIR')},'servicesStarted':False,'realKitStarted':False,'ownFakeChildrenOnly':True}
(logs/(label+'.status.json')).write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record));print((logs/(label+'.stdout')).read_text()[-2200:]);sys.exit(0 if rc in (0,1) else rc)
