#!/usr/bin/env python3
"""Prepare a Linux dcdtuni clone; dry-run is the default, never starts services."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import sys
from dataclasses import asdict, dataclass

KIT_URL = "https://github.com/NVIDIA-Omniverse/kit-cae.git"
KIT_TAG = "v2.0.1"
KIT_COMMIT = "b79bec14f509c30fbf2bc65520e6e2efced9f06f"
NODE_MINIMUM = (24, 12, 0)
REACT = "src/frontend/react"
CART = "src/backend/aircooling/third_parties/dcCartMesh"
FOAM = "src/backend/aircooling/third_parties/foam"
NATIVE_TREES = (CART, FOAM)
KIT = "src/backend/kit-cae"
# Kit's build-time caches live INSIDE the Kit checkout (git-ignored by both
# repositories), so every link the build makes stays inside it and can be
# relative: the whole directory moves, or the repository moves, intact.
KIT_CACHE = KIT + "/_cache"
LFS_INCLUDE = "src/frontend/shared/**"
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class Step:
    label: str
    argv: tuple[str, ...] = ()
    cwd: str = "."
    verify: str = ""


def owned(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise BootstrapError(f"Path leaves this checkout: {relative}")
    return path



def environment_prefix(root: Path, requested: str | None = None) -> Path:
    value = requested or os.environ.get("DCDTUNI_ENV_PREFIX") or ".codex-tmp/envs/dcdtuni"
    prefix = owned(root, value)
    if prefix.name != "dcdtuni":
        raise BootstrapError("The repository environment prefix must end in dcdtuni")
    return prefix.resolve()


def foam_context(root: Path, prefix: Path, env: dict[str, str]) -> dict:
    packages = list((prefix / "conda-meta").glob("openfoam-*.json"))
    if len(packages) != 1:
        raise BootstrapError("Exactly one installed OpenFOAM package record is required")
    package = json.loads(packages[0].read_text())
    if str(package.get("version")) != "2412":
        raise BootstrapError("Installed OpenFOAM must be version 2412")
    query = ("import json,os,ssl,sys; from pathlib import Path; "
             "data={k:os.environ.get(k) for k in ('WM_PROJECT_VERSION','WM_OPTIONS','WM_PRECISION_OPTION','FOAM_API')}; "
             "data.update(python_implementation=sys.implementation.name, python_version=list(sys.version_info[:2]), "
             "python_prefix=str(Path(sys.prefix).resolve()), python_ssl=ssl.OPENSSL_VERSION); print(json.dumps(data))")
    data = json.loads(command(root, ("conda", "run", "-p", str(prefix), "python", "-c", query), env=env))
    if data.get("python_implementation") != "cpython" or data.get("python_version") != [3, 10]:
        raise BootstrapError("The product environment must use CPython 3.10, not another Python implementation")
    if data.get("python_prefix") != str(prefix.resolve()):
        raise BootstrapError("Activated Python belongs to a different environment prefix")
    if not str(data.get("python_ssl", "")).startswith("OpenSSL "):
        raise BootstrapError("The product Python must use its CPython OpenSSL backend")
    if str(data.get("WM_PROJECT_VERSION", "")).lstrip("v") != "2412":
        raise BootstrapError("The activated OpenFOAM version differs from 2412")
    if data.get("WM_PRECISION_OPTION") != "DP" or not re.fullmatch(r"linux64\w*DPInt(?:32|64)Opt", str(data.get("WM_OPTIONS", ""))):
        raise BootstrapError("The activated OpenFOAM build must use double precision (WM_PRECISION_OPTION=DP)")
    if data.get("FOAM_API") not in (None, "2412", 2412):
        raise BootstrapError("The activated OpenFOAM header API differs from 2412")
    data["environment_prefix"] = str(prefix.resolve())
    data["openfoam_package_record_sha256"] = hashlib.sha256(packages[0].read_bytes()).hexdigest()
    return data


def source_snapshot(root: Path, relative: str) -> dict[str, str]:
    values = {}
    native_root = owned(root, relative)
    for path in sorted(native_root.rglob("*")):
        key = path.relative_to(root).as_posix()
        if any(part in ("platforms", "lnInclude") or part.startswith("linux64") for part in path.relative_to(native_root).parts):
            continue
        selected = path.suffix in (".C", ".H", ".cpp", ".hpp", ".cxx", ".h") or path.name in ("Allwmake", "env.sh", "deploy.sh") or path.parent.name == "Make" and path.name in ("files", "options")
        if selected and path.is_file():
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise BootstrapError(f"Build source is a borrowed symlink: {key}")
            values[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not values:
        raise BootstrapError("No native build sources were found")
    return values


def output_snapshot(root: Path, relative: str, options: str) -> dict:
    if relative == CART:
        names = ("bin/dcCartMesh", "lib/libdcCartMesh.so")
    else:
        names = ("bin/buoyantBoussinesqSimpleFoam_new", "bin/buoyantBoussinesqPimpleFoam_new", "bin/writeExplicitBC")
    rows = {}
    for name in names:
        path = owned(root, relative + "/platforms/" + options + "/" + name)
        if path.is_symlink() or not path.is_file() or name.startswith("bin/") and not os.access(path, os.X_OK):
            raise BootstrapError(f"Expected checkout-owned native output missing: {relative}/{name}")
        rows[path.relative_to(root).as_posix()] = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return rows


def check_build_binding(root: Path, context: dict) -> None:
    path = owned(root, ".codex-tmp/bootstrap-native-build.json")
    if not path.is_file() or path.is_symlink():
        raise BootstrapError("A native source/build receipt is missing; output presence cannot certify a fresh source build")
    receipt = json.loads(path.read_text())
    if receipt.get("schema") != "dcdtuni-native-build-v1" or receipt.get("checkout") != str(root.resolve()) or receipt.get("foam_context") != context:
        raise BootstrapError("Native receipt belongs to another checkout or OpenFOAM environment")
    for relative in NATIVE_TREES:
        group = receipt.get("groups", {}).get(relative, {})
        if group.get("source_sha256") != source_snapshot(root, relative) or group.get("outputs") != output_snapshot(root, relative, context["WM_OPTIONS"]):
            raise BootstrapError(f"Native source/output identity differs from its completed fresh build: {relative}")


def write_build_binding(root: Path, context: dict, groups: dict) -> None:
    if set(groups) != set(NATIVE_TREES):
        raise BootstrapError("Every native tree must complete before recording a build binding")
    path = owned(root, ".codex-tmp/bootstrap-native-build.json")
    temp = owned(root, ".codex-tmp/bootstrap-native-build.json.new")
    data = {"schema": "dcdtuni-native-build-v1", "checkout": str(root.resolve()),
            "foam_context": context, "groups": groups,
            "quality_solver_video_verified": False,
            "build_mode": "deploy --clean --no-test"}
    temp.write_text(json.dumps(data, indent=2) + "\n")
    temp.replace(path)


def kit_path_config(root: Path) -> tuple[Path, str]:
    config = owned(root, ".codex-tmp/bootstrap-cache/omniverse-config/omniverse.toml")
    # data_root holds the extension cache the Kit build links into (extscache)
    names = (("data_root", KIT_CACHE + "/omniverse-data"), ("cache_root", ".codex-tmp/bootstrap-cache/omniverse-cache"),
             ("logs_root", ".codex-tmp/bootstrap-cache/omniverse-logs"))
    body = "[paths]\n" + "".join(
        key + " = " + json.dumps(str(owned(root, name).resolve())) + "\n"
        for key, name in names)
    return config, body


def prepare_kit_paths(root: Path) -> Path:
    """Use Kit's supported global-path config without inheriting another runtime."""
    config, body = kit_path_config(root)
    if config.is_symlink() or config.exists() and (not config.is_file() or config.read_text() != body):
        raise BootstrapError("The checkout Omniverse path configuration differs; existing contents were preserved")
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.exists():
        config.write_text(body)
    return config.parent


def check_kit_paths(root: Path) -> None:
    config, body = kit_path_config(root)
    if config.is_symlink() or not config.is_file() or config.read_text() != body:
        raise BootstrapError("The checkout Omniverse path configuration is missing or differs from its owned defaults")


def _kit_symlinks(root: Path):
    """Every symlink in the Kit checkout (not its .git), without following links."""
    kit = owned(root, KIT)
    for directory, dirs, files in os.walk(kit, followlinks=False):
        if Path(directory) == kit and ".git" in dirs:
            dirs.remove(".git")
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink():
                yield path


def check_kit_links(root: Path) -> None:
    kit = owned(root, KIT).resolve()
    for path in _kit_symlinks(root):
        if os.path.isabs(os.readlink(path)):
            raise BootstrapError(f"Kit build has an absolute link ({path}); run the relativize step")
        if not path.resolve().is_relative_to(kit):
            raise BootstrapError("Kit build has links outside the Kit checkout; configure the in-checkout caches and rebuild")


def relativize_kit_links(root: Path) -> int:
    """Rewrite every absolute link in the Kit checkout as a relative one.

    Packman and Kit's extension precache write absolute links; with both
    caches inside the checkout each target is a path below it, so the link
    can name it relative to its own directory. A link leaving the checkout is
    refused (that build used a foreign cache). Returns the links rewritten."""
    kit = owned(root, KIT).resolve()
    count = 0
    for path in list(_kit_symlinks(root)):
        target = os.readlink(path)
        if not os.path.isabs(target):
            continue
        absolute = Path(os.path.normpath(target))
        if not absolute.is_relative_to(kit) or not absolute.resolve().is_relative_to(kit):
            raise BootstrapError(f"Kit link leaves the Kit checkout: {path} -> {target}")
        relative = os.path.relpath(absolute, path.parent.resolve())
        temp = path.with_name(path.name + ".relink-tmp")
        if temp.is_symlink() or temp.exists():
            temp.unlink()
        temp.symlink_to(relative)
        os.replace(temp, path)
        count += 1
    return count

def _stop_command_group(process: subprocess.Popen, grace: float = 5.0) -> None:
    """Stop only the session created for this command, including its descendants."""
    deadline = time.monotonic() + grace
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    # The direct process may exit before an inherited-pipe-free descendant.
    # Keep the same total grace, then close any remaining owned group.
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=grace)
    except subprocess.TimeoutExpired:
        raise BootstrapError("Unable to stop an owned command after its timeout") from None


def command(root: Path, argv: tuple[str, ...], cwd: str = ".", *, capture: bool = True,
            env: dict[str, str] | None = None, _timeout: float | None = None) -> bytes:
    timeout = _timeout if _timeout is not None else (1800 if not capture else 60)
    process = subprocess.Popen(argv, cwd=owned(root, cwd), env=env,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None,
                               start_new_session=True)
    try:
        stdout, _stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        _stop_command_group(process)
        if isinstance(error, KeyboardInterrupt):
            raise
        # Do not include command arguments, registry credentials or subprocess output.
        raise BootstrapError(f"Command timed out ({timeout:g}s): {argv[0]} {argv[1] if len(argv)>1 else ''}") from None
    if process.returncode:
        raise BootstrapError(f"Command failed ({process.returncode}): {argv[0]} {argv[1] if len(argv)>1 else ''}")
    return stdout or b""


def version(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)\s*", text)
    if not match:
        raise BootstrapError("Cannot parse tool version; a stable release is required")
    return tuple(map(int, match.groups()))


def check_node(node: str, npm: str) -> None:
    value = version(node)
    if value[0] != 24 or value < NODE_MINIMUM:
        raise BootstrapError("Node 24.12.0 or newer within Node 24 is required by the locked dependencies")
    if version(npm)[0] < 10:
        raise BootstrapError("npm 10 or newer is required by the NVIDIA streaming SDK")


def check_package_lock(root: Path) -> None:
    package = json.loads(owned(root, REACT + "/package.json").read_text())
    lock = json.loads(owned(root, REACT + "/package-lock.json").read_text())
    expected = {"node": "^24.12.0", "npm": ">=10.0.0"}
    if lock.get("lockfileVersion") != 3:
        raise BootstrapError("The tracked React package-lock must be lockfileVersion 3")
    lock_root = lock.get("packages", {}).get("")
    if not isinstance(lock_root, dict):
        raise BootstrapError("The React lock root is missing")
    for key in ("name", "version", "dependencies", "devDependencies", "engines"):
        if package.get(key) != lock_root.get(key):
            raise BootstrapError(f"React package and lock root disagree: {key}")
    if package.get("engines") != expected:
        raise BootstrapError("The React package engine policy does not match this bootstrap")


def check_native_sources(root: Path) -> int:
    count = 0
    for relative in (CART, FOAM):
        for entry in ("Allwmake", "env.sh", "deploy.sh"):
            if not owned(root, relative + "/" + entry).is_file():
                raise BootstrapError(f"Missing native build entry: {relative}/{entry}")
        platform = owned(root, relative + "/platforms")
        if platform.is_symlink():
            raise BootstrapError(f"Build output must belong to this checkout, not a borrowed symlink: {relative}/platforms")
        make_files = sorted(owned(root, relative).rglob("Make/files"))
        if not make_files:
            raise BootstrapError(f"Missing native Make/files: {relative}")
        for make_file in make_files:
            body = re.sub(r"/\*.*?\*/", "", make_file.read_text(), flags=re.S)
            for line in body.splitlines():
                item = line.strip()
                if item.endswith(".C"):
                    source = make_file.parent.parent / item
                    if not source.resolve().is_relative_to(root.resolve()) or not source.is_file():
                        raise BootstrapError(f"Missing native source named by {make_file.relative_to(root)}: {item}")
                    count += 1
    return count


def check_assets(root: Path) -> int:
    raw = command(root, ("git", "ls-tree", "-rz", "HEAD", "--", "src/frontend/shared"))
    count = 0
    for row in raw.split(b"\0"):
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        relative = raw_path.decode("utf-8")
        if Path(relative).suffix.lower() not in ASSET_SUFFIXES:
            continue
        if metadata.split()[0] != b"100644":
            raise BootstrapError(f"Default asset must be a regular tracked file: {relative}")
        blob = command(root, ("git", "show", "HEAD:" + relative))
        pointer = re.fullmatch(rb"version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([0-9]+)\n?", blob)
        path = owned(root, relative)
        if path.is_symlink() or not path.is_file():
            raise BootstrapError(f"Default image asset missing or linked outside its own file: {relative}")
        value = path.read_bytes()
        expected_sha = pointer[1].decode() if pointer else hashlib.sha256(blob).hexdigest()
        expected_size = int(pointer[2]) if pointer else len(blob)
        if len(value) != expected_size or hashlib.sha256(value).hexdigest() != expected_sha:
            raise BootstrapError(f"Unhydrated or changed default image asset: {relative}; run the targeted Git LFS pull")
        count += 1
    if not count:
        raise BootstrapError("No tracked default shared image assets were found")
    return count


def check_kit_source(root: Path, *, required: bool = False) -> bool:
    kit = owned(root, KIT)
    if not kit.exists():
        if required:
            raise BootstrapError("Public Kit-CAE is not installed; run bootstrap --install --ov")
        return False
    top = command(root, ("git", "rev-parse", "--show-toplevel"), KIT).decode().strip()
    if Path(top).resolve() != kit.resolve():
        raise BootstrapError("Kit path is populated but is not its own public Git checkout")
    head = command(root, ("git", "rev-parse", "HEAD"), KIT).decode().strip()
    if head != KIT_COMMIT:
        raise BootstrapError(f"Kit-CAE must be the pinned {KIT_TAG} commit; existing checkout was preserved")
    dirty = command(root, ("git", "status", "--porcelain", "--untracked-files=no"), KIT)
    if dirty.strip():
        raise BootstrapError("Kit-CAE has tracked local modifications; preserve/review it before using a clean pinned installation")
    return True


def prerequisites(root: Path, *, ov: bool) -> dict:
    if not sys.platform.startswith("linux"):
        raise BootstrapError("The product server bootstrap requires Linux; Windows can use the browser against that server")
    if sys.version_info < (3, 10):
        raise BootstrapError("Python 3.10 or newer is required for the bootstrap")
    tools = ["git", "git-lfs", "node", "npm", "conda", "bash", "g++", "ss", "fuser"]
    if ov:
        tools += ["nvidia-smi", "Xvfb", "vulkaninfo"]
    absent = [tool for tool in tools if not shutil.which(tool)]
    if absent:
        raise BootstrapError("Missing prerequisite tools: " + ", ".join(absent))
    node = command(root, ("node", "--version")).decode().strip()
    npm = command(root, ("npm", "--version")).decode().strip()
    check_node(node, npm)
    command(root, ("git", "lfs", "version"))
    command(root, ("conda", "config", "--show", "register_envs"))
    check_package_lock(root)
    sources = check_native_sources(root)
    if ov:
        check_kit_source(root)
    return {"node": node, "npm": npm, "native_translation_units": sources}


# Liquid cooling needs torch and networkx; the SCANO AI solve adds scipy, torch_geometric and torch_scatter.
AI_IMPORTS = "import torch, networkx, scipy, torch_geometric, torch_scatter"


def plan(root: Path, *, ov: bool, air_only: bool, prefix: Path | None = None) -> list[Step]:
    prefix = prefix or environment_prefix(root)
    steps = [
        Step("Configure local Git LFS filters and preserve existing hooks", ("git", "lfs", "install", "--local", "--skip-repo")),
        Step("Fetch only shared default display assets", ("git", "lfs", "pull", "--include=" + LFS_INCLUDE, "--exclude=")),
        Step("Verify default asset bytes against fixed HEAD", verify="assets"),
        Step("Create/update dcdtuni Python/OpenFOAM 2412 environment", ("bash", "setup_env.sh")),
    ]
    if not air_only:
        steps.append(Step("Require liquid solver and AI inference dependencies", (str(prefix / "bin/python"), "-c", AI_IMPORTS + "; print('Liquid and AI dependencies import successfully')")))
    steps += [
        Step("Install the exact React lock including build tools", ("npm", "ci", "--include=dev", "--include=optional"), REACT),
        Step("Build React production UI", ("npm", "run", "build"), REACT),
        Step("Bind activated OpenFOAM2412 DP build context", verify="foam_context"),
        Step("Capture mesher source identity before fresh build", verify="source_before:" + CART),
        Step("Fresh build repository meshers without CFD smoke cases", ("bash", "deploy.sh", "--clean", "--no-test"), CART),
        Step("Bind mesher source and APP/LIB output", verify="source_after:" + CART),
        Step("Capture solver source identity before fresh build", verify="source_before:" + FOAM),
        Step("Fresh build steady/transient solvers and writer", ("bash", "deploy.sh", "--clean", "--no-test"), FOAM),
        Step("Bind solver source and executable outputs", verify="source_after:" + FOAM),
        Step("Record the native fresh-build bindings", verify="write_build_binding"),
    ]
    if ov:
        if not (root / KIT).exists():
            steps.append(Step("Fetch pinned public Kit-CAE", ("git", "clone", "--branch", KIT_TAG, "--single-branch", "--depth", "1", KIT_URL, KIT)))
        steps += [
            Step("Configure checkout-owned Omniverse data/cache/log paths", verify="prepare_kit_paths"),
            Step("Verify exact clean public Kit source before build", verify="kit_source"),
            Step("Build official Kit release and extension cache", ("bash", "repo.sh", "build", "-r"), KIT),
            Step("Install official optional VTK delegates", ("bash", "repo.sh", "pip_download"), KIT),
            Step("Rewrite the build's absolute links as relative ones", verify="relativize_kit_links"),
            Step("Verify expected Kit streaming launcher", verify="kit_output"),
        ]
    return steps


def cache_environment(root: Path, prefix: Path | None = None) -> dict[str, str]:
    prefix = prefix or environment_prefix(root)
    env = os.environ.copy()
    cache = owned(root, ".codex-tmp/bootstrap-cache")
    env.update({"ENV_NAME": "dcdtuni", "DCDTUNI_ENV_PREFIX": str(prefix),
                "CONDA_REGISTER_ENVS": "false", "CONDA_ENVS_PATH": str(prefix.parent),
                "PYTHONDONTWRITEBYTECODE": "1", "XDG_CACHE_HOME": str(cache / "xdg-cache"),
                "XDG_DATA_HOME": str(cache / "xdg-data"), "XDG_CONFIG_HOME": str(cache / "xdg-config"),
                "OMNI_CONFIG_PATH": str(kit_path_config(root)[0].parent),
                "CUDA_CACHE_PATH": str(cache / "cuda"), "__GL_SHADER_DISK_CACHE_PATH": str(cache / "gl-shaders"),
                "PIP_CACHE_DIR": str(cache / "pip"),
                "npm_config_cache": str(cache / "npm"), "CONDA_PKGS_DIRS": str(cache / "conda-packages"),
                "PM_PACKAGES_ROOT": str(owned(root, KIT_CACHE + "/packman").resolve()), "TMPDIR": str(cache / "tmp")})
    for key in list(env):
        if key.startswith("DCCART_") or key in ("DC_CFD_ASSET_ROOT", "DC_CFD_KIT_BUILD", "NODE_ENV"):
            env.pop(key)
    return env


def verify(root: Path, name: str, *, prefix: Path | None = None, env: dict | None = None, state: dict | None = None) -> None:
    prefix = prefix or environment_prefix(root)
    env = env or cache_environment(root, prefix)
    state = state if state is not None else {}
    if name == "assets":
        check_assets(root)
    elif name == "foam_context":
        state["foam_context"] = foam_context(root, prefix, env)
    elif name.startswith("source_before:"):
        relative = name.partition(":")[2]
        state.setdefault("sources", {})[relative] = source_snapshot(root, relative)
    elif name.startswith("source_after:"):
        relative = name.partition(":")[2]
        values = source_snapshot(root, relative)
        if state.get("sources", {}).get(relative) != values:
            raise BootstrapError("Source changed during native compilation; no binding recorded")
        state.setdefault("groups", {})[relative] = {"source_sha256": values,
            "outputs": output_snapshot(root, relative, state["foam_context"]["WM_OPTIONS"])}
    elif name == "write_build_binding":
        write_build_binding(root, state["foam_context"], state["groups"])
    elif name == "prepare_kit_paths":
        prepare_kit_paths(root)
    elif name == "kit_source":
        check_kit_source(root, required=True)
    elif name == "relativize_kit_links":
        print(f"relinked {relativize_kit_links(root)} Kit links", flush=True)
    elif name == "kit_output":
        launcher = owned(root, KIT + "/_build/linux-x86_64/release/omni.cae_streaming.kit.sh")
        check_kit_links(root)
        if not launcher.is_file() or not os.access(launcher, os.X_OK):
            raise BootstrapError("The pinned Kit build did not produce the streaming launcher")
    else:
        raise BootstrapError(f"Unknown verification step: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print ordered actions only (default)")
    mode.add_argument("--preflight", action="store_true", help="Read-only check of an installed checkout; no GPU probe")
    mode.add_argument("--install", action="store_true", help="Execute asset/dependency/build actions; never start services")
    mode.add_argument("--prepare-ov-paths", action="store_true", help="Prepare only checkout-owned OV data/cache/log config and print its directory")
    parser.add_argument("--ov", action="store_true", help="Include clean public Kit-CAE dependency/build checks")
    parser.add_argument("--air-only", action="store_true", help="Allow setup's optional liquid-dependency fallback")
    parser.add_argument("--env-prefix", help="Environment location inside this checkout, ending in dcdtuni; default .codex-tmp/envs/dcdtuni")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if args.prepare_ov_paths:
            print(prepare_kit_paths(root))
            return 0
        prefix = environment_prefix(root, args.env_prefix)
        check_package_lock(root)
        check_native_sources(root)
        steps = plan(root, ov=args.ov, air_only=args.air_only, prefix=prefix)
        if not args.install and not args.preflight:
            print(json.dumps({"mode": "dry-run", "root": str(root), "steps": [asdict(s) for s in steps],
                              "creates_conda_environment": str(prefix),
                              "gpu_or_service_launch": False, "native_behavior_verified": False}, indent=2))
            return 0
        receipt = prerequisites(root, ov=args.ov)
        env = cache_environment(root, prefix)
        state = {}
        if args.preflight:
            receipt["default_image_assets"] = check_assets(root)
            if not owned(root, ".codex-tmp/bootstrap-cache/tmp").is_dir():
                raise BootstrapError("Installed bootstrap temporary/cache directory is missing")
            context = foam_context(root, prefix, env)
            check_build_binding(root, context)
            receipt["foam_context"] = context
            receipt["native_source_output_binding"] = "matched completed fresh build"
            if not owned(root, REACT + "/node_modules").is_dir() or not owned(root, REACT + "/dist/index.html").is_file():
                raise BootstrapError("React dependencies/production build are missing")
            if not args.air_only:
                command(root, (str(prefix / "bin/python"), "-c", AI_IMPORTS), env={**env, "CUDA_VISIBLE_DEVICES": ""})
            if args.ov:
                check_kit_paths(root)
                verify(root, "kit_source")
                verify(root, "kit_output")
            print(json.dumps({"mode": "preflight", "ok": True, **receipt,
                              "runtime_video_mesh_solver_acceptance": "not exercised"}, indent=2))
            return 0
        owned(root, ".codex-tmp/bootstrap-cache/tmp").mkdir(parents=True, exist_ok=True)
        for step in steps:
            print(step.label, flush=True)
            if step.verify:
                verify(root, step.verify, prefix=prefix, env=env, state=state)
            else:
                # Git clone creates only the intended vendor directory below the repo.
                if step.argv[:2] == ("git", "clone"):
                    owned(root, KIT).parent.mkdir(parents=True, exist_ok=True)
                command(root, step.argv, step.cwd, capture=False, env=env)
        print("Installation/build actions completed. Run preflight, then the documented real runtime acceptance; no service was started.")
        return 0
    except KeyboardInterrupt:
        print("bootstrap: interrupted", file=sys.stderr)
        return 130
    except (BootstrapError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("bootstrap: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
