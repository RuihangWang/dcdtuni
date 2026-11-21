#!/usr/bin/env python3
"""Own one Kit/Xvfb pair for bounded acceptance or an explicit service.

Without --execute this prints a CPU-only plan: no nvidia-smi, Xvfb, socket
connection, Kit or GPU imports. Execution requires one chosen UUID/index.
Bounded runs require an absolute deadline; --service runs until its private
stop.request file or a supervisor signal, preserving the same GPU/CPU guards.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import time


def select_gpu(inventory, gpu_uuid, gpu_index):
    rows=[]
    for row in csv.reader(inventory.splitlines()):
        if not row:continue
        if len(row)!=3:raise ValueError('Unexpected GPU inventory columns')
        rows.append({'index':int(row[0].strip()),'uuid':row[1].strip(),'pci_bus_id':row[2].strip()})
    matches=[r for r in rows if r['uuid']==gpu_uuid and r['index']==gpu_index]
    if len(matches)!=1 or sum(r['uuid']==gpu_uuid for r in rows)!=1:
        raise ValueError('Fresh inventory does not uniquely match the approved physical GPU UUID/index')
    return matches[0]


def cpu_budget(args):
    """Use an explicit small set, never os.cpu_count() or an implicit all-CPU mask."""
    one=getattr(args,'cpu',None);many=getattr(args,'cpus',None)
    if one is not None and many is not None:raise ValueError('Choose either --cpu or --cpus, not both')
    if many is not None:
        if not isinstance(many,str) or not many or len(many)>128:raise ValueError('CPU list must be explicit comma-separated indices')
        tokens=[part.strip() for part in many.split(',')]
        if not all(re.fullmatch(r'0|[1-9][0-9]*',part) for part in tokens):raise ValueError('CPU list must contain nonnegative integer indices only')
        cpus=[int(part) for part in tokens]
        if len(cpus)!=len(set(cpus)):raise ValueError('Duplicate CPU indices are ambiguous')
    else:
        cpu=0 if one is None else one
        if type(cpu) is not int or cpu<0:raise ValueError('Legacy --cpu must be one nonnegative integer')
        cpus=[cpu]
    if not 1<=len(cpus)<=8:raise ValueError('This acceptance harness admits at most eight explicitly selected CPUs')
    numeric=getattr(args,'numeric_threads',None)
    if numeric is None:numeric=min(2,len(cpus))
    if type(numeric) is not int or not 1<=numeric<=len(cpus):raise ValueError('Numeric threads must be positive and fit the selected CPU budget')
    return {'affinity':sorted(cpus),'numeric_threads':numeric,'tasking_workers':max(2,len(cpus))}


def build_plan(args, now=None):
    now=time.time() if now is None else now
    if not re.fullmatch(r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',args.gpu_uuid):
        raise ValueError('Exactly one full physical GPU UUID is required')
    if args.gpu_index<0:raise ValueError('Invalid physical GPU index')
    budget=cpu_budget(args)
    service=bool(getattr(args,'service',False))
    if service:
        if args.deadline_epoch is not None:raise ValueError('Service mode cannot have an episode deadline')
        if args.stream_debug:raise ValueError('Service mode cannot log StreamSDK scene traffic')
    elif (not isinstance(args.deadline_epoch,(int,float)) or not math.isfinite(args.deadline_epoch)
          or not 12<args.deadline_epoch-now<=480):
        raise ValueError('Absolute episode deadline must be 12–480 seconds in the future')
    ports=(args.signal_port,args.http_port,args.media_port)
    if len(set(ports))!=len(ports) or not all(1024<=p<=65535 for p in ports):raise ValueError('Distinct unprivileged signal/HTTP/media ports are required')
    run=args.run_dir.resolve();ext=args.extension_dir.resolve();launcher=args.kit_launcher.resolve()
    icd_path=Path(args.vulkan_icd).resolve(strict=True)
    icd_bytes=icd_path.read_bytes()
    try:icd=json.loads(icd_bytes)
    except (ValueError,UnicodeError) as error:raise ValueError('A valid single NVIDIA ICD manifest is required') from error
    library=icd.get('ICD',{}).get('library_path','') if isinstance(icd,dict) else ''
    if not isinstance(library,str) or Path(library).name!='libGLX_nvidia.so.0':
        raise ValueError('The selected manifest must identify the known NVIDIA Vulkan library')
    env={'CUDA_VISIBLE_DEVICES':args.gpu_uuid,'CUDA_DEVICE_ORDER':'PCI_BUS_ID',
        'VK_DRIVER_FILES':str(icd_path),'VK_ICD_FILENAMES':str(icd_path),
        'OMP_NUM_THREADS':str(budget['numeric_threads']),'OPENBLAS_NUM_THREADS':str(budget['numeric_threads']),
        'MKL_NUM_THREADS':str(budget['numeric_threads']),'NUMEXPR_NUM_THREADS':str(budget['numeric_threads']),
        'DC_CFD_AI':'0','DCDTUNI_RENDER_TEXTURE_CACHE':str(run/'textures'),
        'DC_CFD_SCENE_TREE':str(run/'scene-tree.json'),'DC_CFD_DATASET_INFO':str(run/'dataset.json'),
        'DC_CFD_KIT_LOG':str(run/'kit.stdout.log'),'DC_CFD_VTU':'','DC_CFD_JSON':'','DC_CFD_GEO':'','DC_CFD_RANGES':'',
        'XDG_CACHE_HOME':str(run/'cache'),'XDG_CONFIG_HOME':str(run/'config'),'XDG_DATA_HOME':str(run/'data'),
        'TMPDIR':str(run/'tmp')}
    reuse=getattr(args,'reuse_runtime_cache',None)
    if reuse is not None:
        reuse=Path(reuse).resolve()
        for name in ('cache','config','data'):
            if not (reuse/name).is_dir():raise ValueError('Reuse requires an existing private cache/config/data directory')
        env.update(XDG_CACHE_HOME=str(reuse/'cache'),XDG_CONFIG_HOME=str(reuse/'config'),XDG_DATA_HOME=str(reuse/'data'))
    argv=[str(launcher),'--no-window','--ext-folder',str(ext),'--enable','dc.cfd_bridge',
        f'--/renderer/activeGpu={args.gpu_index}','--/renderer/multiGpu/enabled=false','--/renderer/multiGpu/maxGpuCount=1',
        '--/persistent/exts/omni.cae.viz/defaultDeviceForAuto=cuda:0',
        '--/persistent/exts/omni.cae.viz/defaultBoundingBoxDevice=cuda:0',
        '--/exts/omni.cae.viz/defaultDeviceForAuto=cuda:0','--/exts/omni.cae.viz/defaultBoundingBoxDevice=cuda:0',
        '--/exts/omni.warp.core/devices=cuda:0',
        f'--/exts/omni.kit.livestream.app/primaryStream/signalPort={args.signal_port}',
        f'--/exts/omni.kit.livestream.app/primaryStream/streamPort={args.media_port}',
        f'--/exts/omni.services.transport.server.http/port={args.http_port}',
        '--/app/window/hideUi=false','--/persistent/app/viewport/Viewport/Viewport0/hud/visible=false',
        '--/app/settings/persistent=false',f'--/log/file={run}/kit.internal.log',
        '--/log/enableStandardStreamOutput=true','--/log/outputStreamLevel=info',
        # At least two workers avoid the observed single-worker wait starvation;
        # all workers still inherit the small, explicitly selected CPU mask.
        f"--/plugins/carb.tasking.plugin/threadCount={budget['tasking_workers']}",
        f"--/plugins/omni.tbb.globalcontrol/maxThreadCount={budget['numeric_threads']}"]
    if args.stream_debug:
        argv.append('--/log/channels/omni.kit.livestream.streamsdk=info')
    return {'kit_argv':argv,'environment':env,'run_dir':str(run),'stop_file':str(run/'stop.request'),
            'vulkan_icd':{'path':str(icd_path),'sha256':hashlib.sha256(icd_bytes).hexdigest(),'library_path':library},
            'deadline_epoch':args.deadline_epoch,'service':service,
            'cpu_affinity':budget['affinity'],'cpu_budget':budget,'nice':10,
            'gpu_uuid':args.gpu_uuid,'gpu_index':args.gpu_index,'signal_port':args.signal_port,
            'expected_pci_bus_id':getattr(args,'gpu_pci',None),
            'media_port':args.media_port,'http_port':args.http_port,'cleanup_reserve_s':10,
            'scope':'single Kit process group and private Xvfb; no AI worker, training or scene auto-load'}


def _available(port, kind):
    # Bind/read-only availability check; close immediately. Never stop a holder.
    with socket.socket(socket.AF_INET,kind) as sock:
        try:sock.bind(('0.0.0.0',port))
        except OSError as error:raise RuntimeError(f'Port {port} is already in use; no existing process will be touched') from error


def _stop_owned(process, name, receipts, deadline):
    if process is None:return
    receipt={'name':name,'pid':process.pid,'pgid':process.pid}
    # Every child is created with start_new_session=True, so this process group
    # belongs solely to this harness. No port-based or name-based termination.
    try:
        os.killpg(process.pid,signal.SIGTERM)
        receipt['term_sent']=True
    except ProcessLookupError:receipt['term_sent']=False
    wait=max(.1,min(3.,deadline-time.time()-2))
    try:receipt['returncode']=process.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        try:os.killpg(process.pid,signal.SIGKILL);receipt['kill_sent']=True
        except ProcessLookupError:receipt['kill_sent']=False
        try:receipt['returncode']=process.wait(timeout=max(.1,min(1.,deadline-time.time())))
        except subprocess.TimeoutExpired:receipt['wait_timed_out']=True
    receipts.append(receipt)


def _exited_without_reaping(process):
    # Keep a terminated child as our waitable zombie until cleanup, preventing
    # its PID/process-group ID from being reused between observation and killpg.
    return os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is not None


def apply_cpu_affinity(plan):
    selected=set(plan['cpu_affinity'])
    if not selected.issubset(os.sched_getaffinity(0)):
        raise ValueError('Selected CPUs are not available to this supervisor; refusing to widen inherited limits')
    os.sched_setaffinity(0,selected)
    if os.sched_getaffinity(0)!=selected:raise RuntimeError('The requested CPU affinity was not applied exactly')


def spawn_owned(plan, argv, **kwargs):
    # Xvfb and Kit share this path. Verify the inherited mask immediately before
    # each spawn; start_new_session changes process ownership, not CPU affinity.
    if os.sched_getaffinity(0)!=set(plan['cpu_affinity']):
        raise RuntimeError('Supervisor CPU affinity drifted before spawning a child')
    return subprocess.Popen(argv,start_new_session=True,**kwargs)


def execute(plan):
    run=Path(plan['run_dir']);run.mkdir(mode=0o700,parents=False,exist_ok=False)
    deadline=plan['deadline_epoch'] if plan['deadline_epoch'] is not None else float('inf')
    for name in ('textures','cache','config','data','tmp'):(run/name).mkdir(mode=0o700)
    state={'status':'preflight','plan':plan,'supervisor_pid':os.getpid(),'started_epoch':time.time(),'cleanup':[]}
    def persist():
        state['updated_epoch']=time.time()
        temp=run/'state.next.json';temp.write_text(json.dumps(state,indent=2)+'\n');temp.replace(run/'state.json')
    interrupted=False
    def stop(_signal,_frame):
        nonlocal interrupted
        interrupted=True
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,stop)
    xvfb=kit=None;logs=[]
    try:
        persist()
        apply_cpu_affinity(plan)
        os.nice(max(0,plan['nice']-os.getpriority(os.PRIO_PROCESS,0)))
        state['effective_cpu_affinity']=sorted(os.sched_getaffinity(0))
        state['effective_nice']=os.getpriority(os.PRIO_PROCESS,0)
        argv=plan['kit_argv'];launcher=Path(argv[0]);ext=Path(argv[argv.index('--ext-folder')+1])
        if not launcher.is_file() or not os.access(launcher,os.X_OK):raise ValueError('Kit application launcher is not executable')
        if not (ext/'dc.cfd_bridge/config/extension.toml').is_file():raise ValueError('Worktree bridge extension is missing')
        for port in (plan['signal_port'],plan['http_port']):_available(port,socket.SOCK_STREAM)
        _available(plan['media_port'],socket.SOCK_DGRAM)
        # This is the first GPU-related command, reachable only with --execute.
        inventory=subprocess.check_output(['nvidia-smi','-i',plan['gpu_uuid'],'--query-gpu=index,uuid,pci.bus_id','--format=csv,noheader'],text=True,timeout=5)
        state['selected_gpu']=select_gpu(inventory,plan['gpu_uuid'],plan['gpu_index'])
        if plan.get('expected_pci_bus_id') and state['selected_gpu']['pci_bus_id'].lower()!=plan['expected_pci_bus_id'].lower():
            raise ValueError('Selected physical GPU PCI address changed from the approved card')
        active=subprocess.check_output(['nvidia-smi','-i',plan['gpu_uuid'],'--query-compute-apps=gpu_uuid,pid,process_name','--format=csv,noheader'],text=True,timeout=5)
        state['selected_gpu_compute_apps_before']=active.strip()
        if any(row and row[0].strip()==plan['gpu_uuid'] for row in csv.reader(active.splitlines())):
            raise RuntimeError('Approved GPU still has a compute process; wait for numeric phase cleanup, do not share it')
        state['source_hashes']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [launcher,ext/'dc.cfd_bridge/config/extension.toml',ext/'dc.cfd_bridge/dc/cfd_bridge/extension.py',ext/'dc.cfd_bridge/dc/cfd_bridge/render_scene.py']}
        if time.time()>deadline-12:raise TimeoutError('No startup time remains inside the shared episode deadline')
        xvfb_bin=shutil.which('Xvfb')
        if not xvfb_bin:raise RuntimeError('Xvfb is unavailable')
        read_fd,write_fd=os.pipe()
        xvfb_log=open(run/'xvfb.log','wb');logs.append(xvfb_log)
        xvfb_argv=[xvfb_bin,'-displayfd',str(write_fd),'-screen','0','1600x900x24','-nolisten','tcp']
        try:
            xvfb=spawn_owned(plan,xvfb_argv,pass_fds=(write_fd,),stdout=xvfb_log,stderr=subprocess.STDOUT)
        finally:os.close(write_fd)
        state['xvfb']={'pid':xvfb.pid,'pgid':xvfb.pid,'argv':xvfb_argv};persist()
        try:
            ready,_,_=select.select([read_fd],[],[],min(5.,max(.1,deadline-time.time()-10)))
            if not ready:raise TimeoutError('Private Xvfb did not allocate a display')
            display=os.read(read_fd,64).decode().strip()
        finally:os.close(read_fd)
        if not display.isdecimal():raise RuntimeError('Xvfb returned an invalid display number')
        state['display']=':'+display
        env=os.environ.copy();env.update(plan['environment']);env['DISPLAY']=state['display']
        for key in ('VK_ADD_DRIVER_FILES','VK_LOADER_DRIVERS_SELECT','VK_LOADER_DRIVERS_DISABLE'):env.pop(key,None)
        # Remove inherited scene-selection/AI settings and use no demo assets.
        for key in ('DC_CFD_AI_GPUS','DC_CFD_AI_PYTHON','DC_CFD_RACK_USD','DC_CFD_GB300_COUNT'):env.pop(key,None)
        kit_log=open(run/'kit.stdout.log','wb');logs.append(kit_log)
        kit=spawn_owned(plan,argv,cwd=launcher.parent,env=env,stdout=kit_log,stderr=subprocess.STDOUT)
        state['kit']={'pid':kit.pid,'pgid':kit.pid,'argv':argv};state['status']='running';persist()
        print(json.dumps({'run_dir':str(run),'kit_pid':kit.pid,'display':state['display'],'deadline_epoch':plan['deadline_epoch']}),flush=True)
        while time.time()<deadline-plan['cleanup_reserve_s']:
            if interrupted:state['stop_reason']='supervisor_signal';break
            if Path(plan['stop_file']).exists():state['stop_reason']='private_stop_file';break
            if _exited_without_reaping(kit):state['stop_reason']='kit_exited';break
            if _exited_without_reaping(xvfb):state['stop_reason']='private_xvfb_exited';break
            time.sleep(.25)
        else:state['stop_reason']='shared_episode_deadline'
        state['status']='stopping';persist()
    except BaseException as error:
        state['status']='failed';state['error']={'type':type(error).__name__,'message':str(error)};persist()
        print(json.dumps(state['error']),flush=True)
    finally:
        _stop_owned(kit,'kit',state['cleanup'],deadline)
        _stop_owned(xvfb,'private_xvfb',state['cleanup'],deadline)
        for stream in logs:stream.close()
        state['completed_epoch']=time.time();state['elapsed_s']=state['completed_epoch']-state['started_epoch']
        if state['status']!='failed':state['status']='stopped'
        persist();print(json.dumps({'status':state['status'],'stop_reason':state.get('stop_reason'),'state':str(run/'state.json')}),flush=True)
    return 1 if state['status']=='failed' else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kit-launcher',type=Path,required=True)
    parser.add_argument('--extension-dir',type=Path,required=True)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--gpu-uuid',required=True)
    parser.add_argument('--gpu-index',type=int,required=True)
    parser.add_argument('--gpu-pci')
    parser.add_argument('--vulkan-icd',type=Path,default=Path('/etc/vulkan/icd.d/nvidia_icd.json'))
    parser.add_argument('--stream-debug',action='store_true',help='Opt in to StreamSDK info diagnostics during a future authorized run')
    lifetime=parser.add_mutually_exclusive_group(required=True)
    lifetime.add_argument('--deadline-epoch',type=float)
    lifetime.add_argument('--service',action='store_true',help='Run until private stop.request or supervisor signal')
    cpu_args=parser.add_mutually_exclusive_group()
    cpu_args.add_argument('--cpu',type=int,help='Legacy single CPU index (defaults to 0 when no CPU selection is provided)')
    cpu_args.add_argument('--cpus',help='Explicit comma-separated small CPU set, e.g. 0,1,2,3; at most eight, never all')
    parser.add_argument('--numeric-threads',type=int,help='OpenMP/BLAS/TBB budget, defaults to min(2, selected CPU count)')
    parser.add_argument('--signal-port',type=int,default=8301)
    parser.add_argument('--http-port',type=int,default=8378)
    parser.add_argument('--media-port',type=int,default=47998)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--reuse-runtime-cache',type=Path)
    args=parser.parse_args();plan=build_plan(args)
    if not args.execute:print(json.dumps(plan,indent=2));return 0
    return execute(plan)


if __name__=='__main__':raise SystemExit(main())
