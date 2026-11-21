"""Resolve a physical NVIDIA UUID to the selected Vulkan driver's device index.

CUDA and Vulkan enumerate separately. Restrict this process to one existing
NVIDIA ICD so duplicate system manifests cannot duplicate each physical GPU.
No system driver files are changed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid


def select_icd(environment):
    override = (environment.get("DC_CFD_VULKAN_ICD") or
                environment.get("VK_DRIVER_FILES") or environment.get("VK_ICD_FILENAMES"))
    candidates = [override] if override else [
        "/etc/vulkan/icd.d/nvidia_icd.json", "/usr/share/vulkan/icd.d/nvidia_icd.json"]
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.is_file():
            if override:
                raise ValueError("Vulkan override must name one existing NVIDIA ICD manifest")
            continue
        data = json.loads(path.read_text())
        if "nvidia" not in data.get("ICD", {}).get("library_path", "").lower():
            raise ValueError("Selected Vulkan ICD is not an NVIDIA driver")
        resolved = str(path.resolve())
        if any(c in resolved for c in "\t\n"):
            raise ValueError("Vulkan ICD path may not contain tabs or newlines")
        return resolved
    raise ValueError("No NVIDIA Vulkan ICD manifest found; set DC_CFD_VULKAN_ICD")


def device_index(summary, physical_uuid):
    wanted = uuid.UUID(physical_uuid.removeprefix("GPU-")).hex
    matches = []
    for match in re.finditer(r"^GPU(\d+):\s*\n(.*?)(?=^GPU\d+:|\Z)", summary, re.M | re.S):
        block = match.group(2)
        found = re.search(r"^\s*deviceUUID\s*=\s*(\S+)", block, re.M)
        if not found or uuid.UUID(found.group(1)).hex != wanted:
            continue
        vendor = re.search(r"^\s*vendorID\s*=\s*(\S+)", block, re.M)
        if not vendor or int(vendor.group(1), 0) != 0x10DE:
            raise ValueError("Requested UUID is not an NVIDIA Vulkan device")
        matches.append(int(match.group(1)))
    if len(matches) != 1:
        raise ValueError(f"Requested UUID must match exactly one Vulkan device; found {len(matches)}")
    return matches[0]


def resolve_gpu(physical_index, environment, run):
    """Select the requested physical card; run owns bounded subprocess cleanup."""
    requested = str(physical_index)
    if not requested.isdigit():
        raise ValueError("GPU must be a physical index")
    result = run(["nvidia-smi", "-i", requested,
                  "--query-gpu=index,uuid,pci.bus_id", "--format=csv,noheader,nounits"],
                 env=environment, timeout=20)
    lines = result.stdout.strip().splitlines()
    if len(lines) != 1:
        raise ValueError("Expected exactly one physical GPU")
    fields = [field.strip() for field in lines[0].split(",")]
    if len(fields) != 3 or fields[0] != requested or not fields[1].startswith("GPU-"):
        raise ValueError("Requested physical index does not match NVIDIA response")
    uuid.UUID(fields[1].removeprefix("GPU-"))
    icd = select_icd(environment)
    selected = dict(environment, VK_DRIVER_FILES=icd, VK_ICD_FILENAMES=icd)
    summary = run(["vulkaninfo", "--summary"], env=selected, timeout=20)
    active = device_index(summary.stdout, fields[1])
    override = environment.get("DC_CFD_ACTIVE_GPU")
    if override and override != str(active):
        raise ValueError("activeGpu override does not match requested physical UUID")
    return fields[0], fields[1], fields[2], active, icd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uuid", required=True)
    args = parser.parse_args()
    icd = select_icd(os.environ)
    environment = dict(os.environ, VK_DRIVER_FILES=icd, VK_ICD_FILENAMES=icd)
    result = subprocess.run(["vulkaninfo", "--summary"], env=environment,
                            text=True, capture_output=True, timeout=20, check=True)
    print(f"{device_index(result.stdout, args.uuid)}\t{icd}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"[start_demo] Vulkan GPU selection failed: {error}", file=sys.stderr)
        sys.exit(2)
