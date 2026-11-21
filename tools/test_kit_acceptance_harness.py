"""CPU-only checks of the bounded single-GPU harness plan; never launch Kit."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

path=Path(__file__).with_name('kit_acceptance_harness.py')
spec=importlib.util.spec_from_file_location('kit_acceptance_harness',path)
harness=importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def arguments(tmp_path):
    icd=tmp_path/'nvidia_icd.json'
    icd.write_text('{"file_format_version":"1.0.1","ICD":{"library_path":"libGLX_nvidia.so.0","api_version":"1.4.312"}}')
    return SimpleNamespace(kit_launcher=tmp_path/'main/build/omni.cae_streaming.kit.sh',
        extension_dir=tmp_path/'task/kit/extensions',run_dir=tmp_path/'run',
        vulkan_icd=icd,stream_debug=False,
        gpu_uuid='GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',gpu_index=2,
        signal_port=8301,http_port=8378,media_port=47998,cpu=0,deadline_epoch=1480.)


def test_plan_pins_one_uuid_and_vulkan_index_and_private_artifacts(tmp_path):
    plan=harness.build_plan(arguments(tmp_path),now=1000.)
    env=plan['environment']
    assert env['CUDA_VISIBLE_DEVICES']=='GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    assert env['DC_CFD_AI']=='0'
    assert env['OMP_NUM_THREADS']==env['MKL_NUM_THREADS']=='1'
    args=plan['kit_argv']
    assert args[0]==str(tmp_path/'main/build/omni.cae_streaming.kit.sh')
    assert args[args.index('--ext-folder')+1]==str(tmp_path/'task/kit/extensions')
    assert '--/renderer/activeGpu=2' in args
    assert '--/renderer/multiGpu/enabled=false' in args
    assert '--/renderer/multiGpu/maxGpuCount=1' in args
    assert '--/persistent/exts/omni.cae.viz/defaultDeviceForAuto=cuda:0' in args
    assert '--/exts/omni.warp.core/devices=cuda:0' in args
    assert env['DCDTUNI_RENDER_TEXTURE_CACHE']==str(tmp_path/'run/textures')
    assert plan['stop_file']==str(tmp_path/'run/stop.request')
    assert plan['deadline_epoch']==1480.
    assert all('X99' not in part for part in args)


def test_plan_avoids_duplicate_icd_discovery_without_changing_system_files(tmp_path):
    args=arguments(tmp_path)
    original=args.vulkan_icd.read_bytes()
    plan=harness.build_plan(args,now=1000.)
    assert plan['environment']['VK_DRIVER_FILES']==str(args.vulkan_icd)
    assert plan['environment']['VK_ICD_FILENAMES']==str(args.vulkan_icd)
    assert plan['vulkan_icd']['library_path']=='libGLX_nvidia.so.0'
    assert args.vulkan_icd.read_bytes()==original
    assert plan['cpu_affinity']==[0]
    assert '--/plugins/carb.tasking.plugin/threadCount=2' in plan['kit_argv']
    assert plan['environment']['OMP_NUM_THREADS']==plan['environment']['MKL_NUM_THREADS']==plan['environment']['OPENBLAS_NUM_THREADS']=='1'


def test_streamsdk_diagnostics_are_opt_in_for_a_future_authorized_run(tmp_path):
    args=arguments(tmp_path)
    quiet=harness.build_plan(args,now=1000.)
    assert not any('channels/omni.kit.livestream.streamsdk=' in x for x in quiet['kit_argv'])
    args.stream_debug=True
    diagnostic=harness.build_plan(args,now=1000.)
    assert '--/log/channels/omni.kit.livestream.streamsdk=info' in diagnostic['kit_argv']


def test_service_plan_keeps_one_gpu_isolation_and_repo_owned_artifacts_without_episode_timeout(tmp_path):
    args=arguments(tmp_path)
    args.service=True
    args.deadline_epoch=None
    plan=harness.build_plan(args,now=1000.)
    assert plan['service'] is True
    assert plan['deadline_epoch'] is None
    assert plan['environment']['CUDA_VISIBLE_DEVICES']==args.gpu_uuid
    assert plan['environment']['DC_CFD_AI']=='0'
    assert plan['environment']['DC_CFD_KIT_LOG']==str(tmp_path/'run/kit.stdout.log')
    assert plan['stop_file']==str(tmp_path/'run/stop.request')
    assert '--/log/outputStreamLevel=info' in plan['kit_argv']
    assert not any('channels/omni.kit.livestream.webrtc.plugin=' in x for x in plan['kit_argv'])


@pytest.mark.parametrize('service,deadline,debug',[
    (True,1480.,False),(True,None,True),(False,None,False),
])
def test_service_and_bounded_modes_require_unambiguous_lifetime(tmp_path,service,deadline,debug):
    args=arguments(tmp_path)
    args.service=service
    args.deadline_epoch=deadline
    args.stream_debug=debug
    with pytest.raises(ValueError):harness.build_plan(args,now=1000.)


def test_only_one_nvidia_driver_manifest_is_admitted(tmp_path):
    args=arguments(tmp_path)
    args.vulkan_icd.write_text('{"ICD":{"library_path":"libvulkan_intel.so"}}')
    with pytest.raises(ValueError,match='NVIDIA'):harness.build_plan(args,now=1000.)


@pytest.mark.parametrize('change',[{'gpu_uuid':'GPU-a,GPU-b'},{'gpu_index':-1},{'cpu':-1},{'cpu':True},
    {'deadline_epoch':999.},{'deadline_epoch':1481.},{'http_port':8301}])
def test_refuses_ambiguous_card_resource_scope_or_deadline(tmp_path,change):
    args=arguments(tmp_path)
    for name,value in change.items():setattr(args,name,value)
    with pytest.raises(ValueError):harness.build_plan(args,now=1000.)


def test_fresh_gpu_inventory_requires_matching_unique_uuid_and_index():
    inventory='0, GPU-00000000-1111-2222-3333-444444444444, 00000000:17:00.0\n2, GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee, 00000000:65:00.0\n'
    record=harness.select_gpu(inventory,'GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',2)
    assert record['pci_bus_id']=='00000000:65:00.0'
    with pytest.raises(ValueError):harness.select_gpu(inventory,record['uuid'],0)
    with pytest.raises(ValueError):harness.select_gpu(inventory+inventory,record['uuid'],2)


def test_explicit_small_cpu_list_sets_bounded_worker_and_numeric_budgets(tmp_path):
    args=arguments(tmp_path)
    args.cpu=None;args.cpus='3, 1,2,0'
    plan=harness.build_plan(args,now=1000.)
    assert plan['cpu_affinity']==[0,1,2,3]
    assert '--/plugins/carb.tasking.plugin/threadCount=4' in plan['kit_argv']
    assert '--/plugins/omni.tbb.globalcontrol/maxThreadCount=2' in plan['kit_argv']
    for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        assert plan['environment'][key]=='2'
    assert plan['nice']==10
    args.numeric_threads=3
    configured=harness.build_plan(args,now=1000.)
    assert configured['environment']['OMP_NUM_THREADS']=='3'
    assert '--/plugins/omni.tbb.globalcontrol/maxThreadCount=3' in configured['kit_argv']


def test_legacy_single_cpu_and_default_never_expand_to_all_machine_cpus(tmp_path):
    args=arguments(tmp_path)
    args.cpu=None
    default=harness.build_plan(args,now=1000.)
    assert default['cpu_affinity']==[0]
    assert default['environment']['OMP_NUM_THREADS']=='1'
    args.cpu=3
    legacy=harness.build_plan(args,now=1000.)
    assert legacy['cpu_affinity']==[3]
    assert legacy['environment']['OMP_NUM_THREADS']=='1'
    assert '--/plugins/carb.tasking.plugin/threadCount=2' in legacy['kit_argv']


@pytest.mark.parametrize('cpus',['','all','0,,1','0-3','0,0','-1,0','1.5','0,1,2,3,4,5,6,7,8',42])
def test_refuses_invalid_ambiguous_or_machine_wide_cpu_selection(tmp_path,cpus):
    args=arguments(tmp_path);args.cpu=None;args.cpus=cpus
    with pytest.raises(ValueError):harness.build_plan(args,now=1000.)


@pytest.mark.parametrize('threads',[0,-1,5,True,1.5])
def test_numeric_budget_must_fit_the_explicit_cpu_budget(tmp_path,threads):
    args=arguments(tmp_path);args.cpu=None;args.cpus='0,1,2,3';args.numeric_threads=threads
    with pytest.raises(ValueError):harness.build_plan(args,now=1000.)


def test_refuses_conflicting_legacy_and_cpu_list_arguments(tmp_path):
    args=arguments(tmp_path);args.cpus='0,1'
    with pytest.raises(ValueError):harness.build_plan(args,now=1000.)


@pytest.mark.parametrize('count',[1,4])
def test_cpu_child_inherits_only_the_verified_supervisor_affinity(count):
    original=os.sched_getaffinity(0)
    if len(original)<count:pytest.skip('CPU-only test supervisor has fewer permitted CPUs than this case')
    selected=set(sorted(original)[:count])
    plan={'cpu_affinity':sorted(selected)}
    try:
        harness.apply_cpu_affinity(plan)
        process=harness.spawn_owned(plan,[sys.executable,'-c','import os,json;print(json.dumps(sorted(os.sched_getaffinity(0))))'],stdout=subprocess.PIPE,text=True)
        output,_=process.communicate(timeout=5)
        assert process.returncode==0
        assert json.loads(output)==sorted(selected)
    finally:os.sched_setaffinity(0,original)


def test_unavailable_cpus_are_rejected_before_any_child_launch():
    original=os.sched_getaffinity(0)
    unavailable=max(original)+10000
    with pytest.raises(ValueError,match='available'):
        harness.apply_cpu_affinity({'cpu_affinity':[unavailable]})
    assert os.sched_getaffinity(0)==original
