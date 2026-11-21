import importlib.util
import json
from pathlib import Path
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/resolve_vulkan_gpu.py"
spec = importlib.util.spec_from_file_location("vulkan_gpu_selection", SCRIPT)
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)
UUID = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"

def block(index, device_uuid=UUID, vendor="0x10de"):
    return f"GPU{index}:\n vendorID = {vendor}\n deviceUUID = {device_uuid}\n"

def test_uuid_mapping_handles_different_vulkan_order():
    assert selection.device_index(block(0, OTHER)+block(3), "GPU-"+UUID) == 3

@pytest.mark.parametrize("summary", [block(0, OTHER),block(1)+block(2),block(1,vendor="0x8086")])
def test_missing_duplicate_or_non_nvidia_match_is_rejected(summary):
    with pytest.raises(ValueError):
        selection.device_index(summary,"GPU-"+UUID)

def test_one_process_local_manifest_is_required(tmp_path):
    manifest=tmp_path/"nvidia.json"
    manifest.write_text(json.dumps({"ICD":{"library_path":"libGLX_nvidia.so.0"}}))
    assert selection.select_icd({"DC_CFD_VULKAN_ICD":str(manifest)}) == str(manifest.resolve())
    with pytest.raises(ValueError):
        selection.select_icd({"VK_DRIVER_FILES":str(manifest)+":"+str(manifest)})
    manifest.write_text(json.dumps({"ICD":{"library_path":"libmesa.so"}}))
    with pytest.raises(ValueError):
        selection.select_icd({"DC_CFD_VULKAN_ICD":str(manifest)})


def test_physical_selection_propagates_one_uuid_and_driver_to_bounded_runner(tmp_path):
    manifest=tmp_path/"nvidia.json"
    manifest.write_text(json.dumps({"ICD":{"library_path":"libGLX_nvidia.so.0"}}))
    environment={"DC_CFD_VULKAN_ICD":str(manifest)}
    calls=[]
    def run(command, *, env, timeout):
        from subprocess import CompletedProcess
        calls.append((command,dict(env),timeout))
        if command[0]=="nvidia-smi":
            return CompletedProcess(command,0,f"0, GPU-{UUID}, 00000000:01:00.0\n","")
        return CompletedProcess(command,0,block(3),"")
    assert selection.resolve_gpu(0,environment,run)==("0","GPU-"+UUID,"00000000:01:00.0",3,str(manifest))
    assert len(calls)==2 and all(call[2]==20 for call in calls)
    assert calls[1][1]["VK_DRIVER_FILES"]==calls[1][1]["VK_ICD_FILENAMES"]==str(manifest)


@pytest.mark.parametrize("response", [
    f"1, GPU-{UUID}, 00000000:01:00.0\n",
    f"0, GPU-{UUID}, 00000000:01:00.0\n0, GPU-{UUID}, 00000000:01:00.0\n"])
def test_wrong_physical_index_or_multiple_cards_fail_before_vulkan(response):
    from subprocess import CompletedProcess
    def run(command, *, env, timeout):
        assert command[0]=="nvidia-smi"
        return CompletedProcess(command,0,response,"")
    with pytest.raises(ValueError):
        selection.resolve_gpu(0,{},run)


def test_active_gpu_override_must_match_uuid_not_physical_ordinal(tmp_path):
    from subprocess import CompletedProcess
    manifest=tmp_path/"nvidia.json"
    manifest.write_text(json.dumps({"ICD":{"library_path":"libGLX_nvidia.so.0"}}))
    def run(command, *, env, timeout):
        return CompletedProcess(command,0,
            f"0, GPU-{UUID}, 00000000:01:00.0\n" if command[0]=="nvidia-smi" else block(3),"")
    with pytest.raises(ValueError,match="override"):
        selection.resolve_gpu(0,{"DC_CFD_VULKAN_ICD":str(manifest),"DC_CFD_ACTIVE_GPU":"0"},run)
