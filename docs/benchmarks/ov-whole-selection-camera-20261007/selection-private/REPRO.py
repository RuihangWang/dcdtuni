"""Fresh owned CPU reproduction; native Kit/GPU/browser/services are never used."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

F = Path(__file__).resolve().parent
B = Path("/mnt/lv/home/litianyu/mycode/dcdtuni")
I = B / ".codex-tmp/goal-20261005/integration"
p = argparse.ArgumentParser()
p.add_argument("--output", required=True)
p.add_argument("--cpu", type=int, choices=[38], default=38)
p.add_argument("--native-only", action="store_true")
p.add_argument("--build", action="store_true")
a = p.parse_args()
O = Path(a.output).resolve()
assert O.is_relative_to(B / ".codex-tmp") and not O.exists(), "exclusive project-owned output required"
manifest = json.loads((F / "MANIFEST.json").read_text())
for item in manifest["files"]:
    raw = (F / item["path"]).read_bytes()
    assert len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"], item["path"]
O.mkdir()
for name in ("before", "candidate"):
    shutil.copytree(F / name, O / name)
for name in ("REPLAY_NATIVE.py", "REPLAY_AXIS_CPU.py"):
    shutil.copyfile(F / name, O / name)
for name in ("logs", "cache", "tmp"):
    (O / name).mkdir()
env = os.environ.copy()
env.update(CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", PXR_WORK_THREAD_LIMIT="1", XDG_CACHE_HOME=str(O / "cache"), TMPDIR=str(O / "tmp"))
statuses = []

def run(name, argv, expected=0, cwd=None):
    started = time.monotonic()
    value = subprocess.run(["taskset", "-c", str(a.cpu), *argv], cwd=cwd or O, env=env, capture_output=True, timeout=120)
    (O / "logs" / (name + ".stdout")).write_bytes(value.stdout)
    (O / "logs" / (name + ".stderr")).write_bytes(value.stderr)
    receipt = {"name": name, "argv": argv, "expectedRc": expected, "rc": value.returncode, "elapsed": time.monotonic() - started, "stdoutSha256": hashlib.sha256(value.stdout).hexdigest(), "stderrSha256": hashlib.sha256(value.stderr).hexdigest()}
    (O / "logs" / (name + ".status.json")).write_text(json.dumps(receipt, indent=2) + "\n")
    statuses.append(receipt)
    assert value.returncode == expected, name
    if expected == 1:
        assert b"PRODUCT RED" in value.stderr or b"expected 'UPS_Rack'" in value.stdout, "unexpected tool failure is not baseline PRODUCT RED"
    print(json.dumps(receipt), flush=True)

for version in ("before", "candidate"):
    for case in ("shift", "clear", "programmatic14"):
        run("native-" + version + "-" + case, [sys.executable, str(O / "REPLAY_NATIVE.py"), version, case], 1 if version == "before" else 0)
run("native-unit", [sys.executable, str(O / "candidate/src/frontend/omniverse/cfd_demo/tests/test_source_selection.py"), "-v"])
run("axis", [sys.executable, str(O / "REPLAY_AXIS_CPU.py"), "--out", str(O / "axis-output")])
if not a.native_only:
    R = O / "workspace"
    for item in json.loads((F / "DEPENDENCIES.json").read_text())["files"]:
        source = I / item["path"]
        raw = source.read_bytes()
        assert len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"], "held dependency drift: " + item["path"]
        target = R / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    for item in json.loads((F / "apply-map.json").read_text())["targets"]:
        if not item["relativeTarget"].startswith("src/frontend/omniverse/"):
            target = R / item["relativeTarget"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(F / item["candidate"], target)
    react = R / "src/frontend/react"
    (react / "node_modules").symlink_to(I / "src/frontend/react/node_modules", target_is_directory=True)
    (R / "src/backend/liquidcooling/data").symlink_to(I / "src/backend/liquidcooling/data", target_is_directory=True)
    config = R / "vitest.selection.config.ts"
    config.write_text("export default " + json.dumps({"root": str(react), "cacheDir": str(O / "cache/vite"), "test": {"environment": "jsdom", "pool": "forks", "poolOptions": {"forks": {"singleFork": True}}}}) + "\n")
    argv = ["/usr/bin/node", str(react / "node_modules/vitest/vitest.mjs"), "run", "--config", str(config), "--cache=false", "--maxWorkers=1", "--minWorkers=1"]
    rels = ["src/frontend/shared/schema/ov-edit-event.schema.json", "src/frontend/react/src/renderer/ovEditBridge.ts"]
    for rel in rels:
        shutil.copyfile(F / "before" / rel, R / rel)
    try:
        run("mounted-baseline-red", [*argv, "test/ovWholeSelection.test.tsx", "-t", "mounted native Shift|mounted native empty"], 1, react)
    finally:
        for rel in rels:
            shutil.copyfile(F / "candidate" / rel, R / rel)
    run("frontend-green", [*argv, "test/ovEditBridge.test.ts", "test/ovWholeSelection.test.tsx"], cwd=react)
    if a.build:
        run("types", ["/usr/bin/node", str(react / "node_modules/typescript/bin/tsc"), "--noEmit", "--incremental", "false"], cwd=react)
        run("vite", ["/usr/bin/node", str(react / "node_modules/vite/bin/vite.js"), "build"], cwd=react)
(O / "SUMMARY.json").write_text(json.dumps({"scope": "CPU controlled/source only; no streamed pixel or actual hit acceptance", "statuses": statuses}, indent=2) + "\n")
