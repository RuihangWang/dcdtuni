"""CPU source/metadata controls; never install dependencies or run native apps."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import subprocess
import shutil
import time
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("bootstrap_dcdtuni", Path(__file__).with_name("bootstrap_dcdtuni.py"))
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)


class BootstrapControls(unittest.TestCase):
    def setUp(self):
        owner = Path(__file__).resolve().parents[1] / ".codex-tmp/bootstrap-source-controls"
        owner.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=owner)
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        package = {"name": "control", "version": "1", "dependencies": {}, "devDependencies": {},
                   "engines": {"node": "^24.12.0", "npm": ">=10.0.0"}}
        self.put(b.REACT + "/package.json", json.dumps(package))
        self.put(b.REACT + "/package-lock.json", json.dumps({"lockfileVersion": 3, "packages": {"": package}}))
        for tree in (b.CART, b.FOAM):
            for name in ("Allwmake", "env.sh", "deploy.sh"):
                self.put(tree + "/" + name, "# private control input\n")
            self.put(tree + "/src/Make/files", "main.C\nEXE = control\n")
            self.put(tree + "/src/Make/options", "# private control input\n")
            self.put(tree + "/src/main.C", "// synthetic CPU source control; not a native solver\n")

    def put(self, rel, value):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())
        return path

    def context(self):
        return {"WM_OPTIONS": "linux64GccDPInt32Opt", "WM_PROJECT_VERSION": "v2412",
                "WM_PRECISION_OPTION": "DP", "environment_prefix": str(self.root / "envs/dcdtuni")}

    def binding(self):
        context = self.context()
        groups = {}
        for tree in (b.CART, b.FOAM):
            names = ("bin/dcCartMesh", "lib/libdcCartMesh.so") if tree == b.CART else (
                "bin/buoyantBoussinesqSimpleFoam_new", "bin/buoyantBoussinesqPimpleFoam_new", "bin/writeExplicitBC")
            for name in names:
                path = self.put(tree + "/platforms/" + context["WM_OPTIONS"] + "/" + name,
                                b"SYNTHETIC source-control artifact, not native ELF\n")
                path.chmod(0o755)
            groups[tree] = {"source_sha256": b.source_snapshot(self.root, tree),
                            "outputs": b.output_snapshot(self.root, tree, context["WM_OPTIONS"])}
        self.put(".codex-tmp/placeholder", "")
        b.write_build_binding(self.root, context, groups)
        return context

    def test_supported_node_and_npm_bounds(self):
        b.check_node("v24.12.0", "10.9.3")
        b.check_node("v24.15.2", "11.0.0")
        for node, npm in (("v24.11.9", "10.9.3"), ("v22.20.0", "10.9.3"),
                          ("v25.0.0", "10.9.3"), ("v24.12.0-rc.1", "10.9.3"), ("v24.12.0", "9.0.0")):
            with self.subTest(node=node, npm=npm), self.assertRaises(b.BootstrapError):
                b.check_node(node, npm)

    def test_package_lock_must_match_all_root_dependency_sections(self):
        b.check_package_lock(self.root)
        path = self.root / b.REACT / "package-lock.json"
        data = json.loads(path.read_text())
        data["packages"][""]["engines"]["node"] = "^24.0.0"
        path.write_text(json.dumps(data))
        with self.assertRaises(b.BootstrapError):
            b.check_package_lock(self.root)

    def test_prefix_ownership_and_name(self):
        selected = b.environment_prefix(self.root, "custom env/dcdtuni")
        self.assertEqual(selected, (self.root / "custom env/dcdtuni").resolve())
        for requested in (str(self.root.parent / "outside/dcdtuni"), "custom/wrongname"):
            with self.subTest(requested=requested), self.assertRaises(b.BootstrapError):
                b.environment_prefix(self.root, requested)

    def test_borrowed_platform_symlink_is_rejected_before_any_build(self):
        self.assertEqual(b.check_native_sources(self.root), 2)
        platform = self.root / b.CART / "platforms"
        platform.symlink_to(self.root.parent / "not-created-external-build")
        with self.assertRaises(b.BootstrapError):
            b.check_native_sources(self.root)

    def test_missing_make_source_is_rejected(self):
        (self.root / b.CART / "src/main.C").unlink()
        with self.assertRaises(b.BootstrapError):
            b.check_native_sources(self.root)

    def test_lfs_actual_bytes_and_oid_gate(self):
        relative = "src/frontend/shared/textures/control.jpg"
        value = b"SYNTHETIC image-byte control, not real JPEG"
        pointer = ("version https://git-lfs.github.com/spec/v1\noid sha256:" + hashlib.sha256(value).hexdigest()
                   + "\nsize " + str(len(value)) + "\n").encode()
        path = self.put(relative, pointer)
        def read_git(root, argv, *args, **kwargs):
            if argv[1] == "ls-tree":
                return b"100644 blob 0000000000000000000000000000000000000000\t" + relative.encode() + b"\0"
            if argv[:2] == ("git", "show"):
                return pointer
            raise AssertionError("unexpected command")
        with patch.object(b, "command", read_git):
            with self.assertRaises(b.BootstrapError):
                b.check_assets(self.root)
            path.write_bytes(value)
            self.assertEqual(b.check_assets(self.root), 1)
            path.write_bytes(value[:-1] + b"!")
            with self.assertRaises(b.BootstrapError):
                b.check_assets(self.root)

    @unittest.skipUnless(shutil.which("git") and shutil.which("git-lfs"), "Actual Git LFS is needed for hook preservation")
    def test_real_lfs_filter_install_preserves_existing_user_hook(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
        env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True, env=env, capture_output=True)
        hook = self.root / ".git/hooks/pre-push"
        sentinel = b"#!/bin/sh\n# Existing project/user policy must survive bootstrap.\nexit 0\n"
        hook.write_bytes(sentinel)
        hook.chmod(0o755)
        original = subprocess.run(["git", "lfs", "install", "--local"], cwd=self.root,
                                  env=env, capture_output=True)
        self.assertEqual(original.returncode, 2)
        self.assertEqual(hook.read_bytes(), sentinel)
        step = next(x for x in b.plan(self.root, ov=False, air_only=True)
                    if x.argv[:3] == ("git", "lfs", "install"))
        b.command(self.root, step.argv, env=env)
        self.assertEqual(hook.read_bytes(), sentinel)
        process = subprocess.check_output(["git", "config", "--local", "--get", "filter.lfs.process"],
                                          cwd=self.root, env=env).decode().strip()
        self.assertEqual(process, "git-lfs filter-process")

    def test_missing_lfs_inventory_cannot_pass(self):
        with patch.object(b, "command", return_value=b""), self.assertRaises(b.BootstrapError):
            b.check_assets(self.root)

    def test_native_presence_without_receipt_cannot_pass(self):
        with self.assertRaises(b.BootstrapError):
            b.check_build_binding(self.root, self.context())

    def test_native_binding_reopens_source_and_output_values(self):
        context = self.binding()
        b.check_build_binding(self.root, context)
        source = self.root / b.CART / "src/main.C"
        source.write_text(source.read_text() + "// changed\n")
        with self.assertRaises(b.BootstrapError):
            b.check_build_binding(self.root, context)

    def test_changed_binary_cannot_reuse_a_build_receipt(self):
        context = self.binding()
        binary = self.root / b.CART / "platforms" / context["WM_OPTIONS"] / "bin/dcCartMesh"
        binary.write_bytes(binary.read_bytes() + b"changed")
        with self.assertRaises(b.BootstrapError):
            b.check_build_binding(self.root, context)

    def test_other_environment_or_precision_cannot_reuse_binding(self):
        context = self.binding()
        for field, value in (("WM_PRECISION_OPTION", "SP"), ("environment_prefix", str(self.root / "other/dcdtuni"))):
            changed = {**context, field: value}
            with self.subTest(field=field), self.assertRaises(b.BootstrapError):
                b.check_build_binding(self.root, changed)

    def test_source_change_during_build_refuses_receipt(self):
        prefix = self.root / "envs/dcdtuni"
        state = {"foam_context": self.context()}
        b.verify(self.root, "source_before:" + b.CART, prefix=prefix, state=state)
        path = self.root / b.CART / "src/main.C"
        path.write_text("// mutated during simulated build\n")
        with self.assertRaises(b.BootstrapError):
            b.verify(self.root, "source_after:" + b.CART, prefix=prefix, state=state)
        self.assertFalse((self.root / ".codex-tmp/bootstrap-native-build.json").exists())

    def test_actual_foam_metadata_and_dp_context_required(self):
        prefix = self.root / "envs/dcdtuni"
        record = self.put("envs/dcdtuni/conda-meta/openfoam-control.json", json.dumps({"version": "2412"}))
        good = {"WM_PROJECT_VERSION": "v2412", "WM_OPTIONS": "linux64GccDPInt32Opt", "WM_PRECISION_OPTION": "DP", "FOAM_API": "2412",
                "python_implementation": "cpython", "python_version": [3, 10], "python_prefix": str(prefix), "python_ssl": "OpenSSL 3.6.5"}
        with patch.object(b, "command", return_value=json.dumps(good).encode()):
            self.assertEqual(b.foam_context(self.root, prefix, {})["environment_prefix"], str(prefix))
        for key, value in (("WM_PROJECT_VERSION", "v2406"), ("WM_PRECISION_OPTION", "SP"), ("FOAM_API", "2406"),
                           ("python_implementation", "graalpy"), ("python_version", [3, 11]),
                           ("python_prefix", str(self.root / "foreign/dcdtuni")), ("python_ssl", "GraalVM JSSE")):
            with patch.object(b, "command", return_value=json.dumps({**good, key: value}).encode()), self.assertRaises(b.BootstrapError):
                b.foam_context(self.root, prefix, {})
        record.write_text(json.dumps({"version": "2506"}))
        with patch.object(b, "command", side_effect=AssertionError("must reject package first")), self.assertRaises(b.BootstrapError):
            b.foam_context(self.root, prefix, {})

    def test_pinned_kit_and_dirty_vendor_gate(self):
        kit = self.root / b.KIT
        kit.mkdir(parents=True)
        replies = [str(kit).encode(), b.KIT_COMMIT.encode(), b""]
        with patch.object(b, "command", side_effect=replies):
            self.assertTrue(b.check_kit_source(self.root))
        for head, dirty in ((b"bad", b""), (b.KIT_COMMIT.encode(), b" M source/apps/omni.cae_streaming.kit\n")):
            with patch.object(b, "command", side_effect=[str(kit).encode(), head, dirty]), self.assertRaises(b.BootstrapError):
                b.check_kit_source(self.root)

    def test_owned_ov_config_preserves_unexpected_existing_content(self):
        config, _ = b.kit_path_config(self.root)
        directory = b.prepare_kit_paths(self.root)
        self.assertEqual(directory, config.parent)
        b.check_kit_paths(self.root)
        for line in config.read_text().splitlines()[1:]:
            value = json.loads(line.split(" = ", 1)[1])
            self.assertTrue(Path(value).is_relative_to(self.root))
        foreign = '[paths]\ndata_root = "/outside-existing-owner"\n'
        config.write_text(foreign)
        with self.assertRaises(b.BootstrapError):
            b.prepare_kit_paths(self.root)
        self.assertEqual(config.read_text(), foreign)
        with self.assertRaises(b.BootstrapError):
            b.check_kit_paths(self.root)

    def test_ov_path_only_cli_needs_no_toolchain_and_starts_no_commands(self):
        with patch.object(b, "__file__", str(self.root / "tools/bootstrap_dcdtuni.py")), \
             patch.object(b, "command", side_effect=AssertionError("path-only mode started a process")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(b.main(["--prepare-ov-paths"]), 0)
        self.assertEqual(output.getvalue().strip(), str(b.kit_path_config(self.root)[0].parent))
        self.assertFalse((self.root / ".codex-tmp/envs/dcdtuni").exists())

    def test_kit_foreign_cache_link_is_not_portable(self):
        self.put(b.KIT + "/_build/marker", b"")
        (self.root / b.KIT / "_build/foreign").symlink_to(self.root.parent / "foreign-not-created")
        with self.assertRaises(b.BootstrapError):
            b.check_kit_links(self.root)

    def test_kit_absolute_link_inside_checkout_is_not_portable(self):
        self.put(b.KIT + "/_cache/packman/pkg/file", b"x")
        (self.root / b.KIT / "_build").mkdir(parents=True, exist_ok=True)
        (self.root / b.KIT / "_build/dep").symlink_to(self.root / b.KIT / "_cache/packman/pkg")
        with self.assertRaises(b.BootstrapError):
            b.check_kit_links(self.root)

    def test_relativize_kit_links_keeps_targets_and_drops_absolute_paths(self):
        self.put(b.KIT + "/_cache/packman/pkg/file", b"x")
        self.put(b.KIT + "/templates/app.kit", b"y")
        build = self.root / b.KIT / "_build/linux-x86_64/release"
        build.mkdir(parents=True)
        (build / "dep").symlink_to(self.root / b.KIT / "_cache/packman/pkg")
        (self.root / b.KIT / "source").mkdir()
        (self.root / b.KIT / "source/app.kit").symlink_to(self.root / b.KIT / "templates/app.kit")
        (build / "already").symlink_to("dep")
        self.assertEqual(b.relativize_kit_links(self.root), 2)
        for link in (build / "dep", self.root / b.KIT / "source/app.kit", build / "already"):
            self.assertFalse(os.path.isabs(os.readlink(link)))
        self.assertEqual((build / "dep/file").read_bytes(), b"x")
        self.assertEqual((self.root / b.KIT / "source/app.kit").read_bytes(), b"y")
        b.check_kit_links(self.root)
        # the checkout moves as a whole and every link still resolves
        moved = self.root.parent / "moved"
        shutil.move(str(self.root), str(moved))
        self.assertEqual((moved / b.KIT / "_build/linux-x86_64/release/dep/file").read_bytes(), b"x")

    def test_relativize_refuses_a_link_leaving_the_kit_checkout(self):
        (self.root / b.KIT / "_build").mkdir(parents=True)
        (self.root / b.KIT / "_build/foreign").symlink_to(self.root.parent / "elsewhere")
        with self.assertRaises(b.BootstrapError):
            b.relativize_kit_links(self.root)

    def test_kit_build_caches_live_inside_the_kit_checkout(self):
        kit = (self.root / b.KIT).resolve()
        env = b.cache_environment(self.root, self.root / ".codex-tmp/envs/dcdtuni")
        self.assertTrue(Path(env["PM_PACKAGES_ROOT"]).resolve().is_relative_to(kit))
        body = b.kit_path_config(self.root)[1]
        data_root = json.loads(re.search(r"data_root = (.*)", body).group(1))
        self.assertTrue(Path(data_root).resolve().is_relative_to(kit))

    def test_dry_run_executes_no_commands_and_does_not_create_environment(self):
        with patch.object(b, "__file__", str(self.root / "tools/bootstrap_dcdtuni.py")), \
             patch.object(b, "command", side_effect=AssertionError("dry-run executed a subprocess")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(b.main(["--dry-run", "--ov"]), 0)
        data = json.loads(output.getvalue())
        self.assertFalse(data["gpu_or_service_launch"])
        self.assertFalse((self.root / ".codex-tmp/envs/dcdtuni").exists())
        steps = data["steps"]
        build = [x for x in steps if x["argv"][:2] == ["bash", "deploy.sh"]]
        self.assertEqual([x["cwd"] for x in build], [b.CART, b.FOAM])
        self.assertTrue(all("--clean" in x["argv"] and "--no-test" in x["argv"] for x in build))
        npm = next(x for x in steps if x["argv"][:2] == ["npm", "ci"])
        self.assertIn("--include=dev", npm["argv"])
        self.assertIn("--include=optional", npm["argv"])
        verifies = [x["verify"] for x in steps if x["verify"]]
        self.assertLess(verifies.index("relativize_kit_links"), verifies.index("kit_output"))
        pip = next(i for i, x in enumerate(steps) if x["argv"][-1:] == ["pip_download"])
        self.assertLess(pip, next(i for i, x in enumerate(steps) if x["verify"] == "relativize_kit_links"))

    def test_native_source_snapshot_ignores_only_native_build_directories(self):
        for ancestor in ("ordinary", "platforms", "linux64-shared-parent"):
            root = self.root / ancestor / "dcdtuni"
            native = root / b.CART
            (native / "src").mkdir(parents=True)
            (native / "src/main.C").write_text("// own source fixture\n")
            for generated in ("platforms", "lnInclude", "linux64GccDPInt32Opt"):
                directory = native / generated
                directory.mkdir()
                (directory / "generated.H").write_text("// generated fixture\n")
            with self.subTest(ancestor=ancestor):
                self.assertEqual(set(b.source_snapshot(root, b.CART)), {b.CART + "/src/main.C"})

    def test_expensive_install_command_has_a_bounded_owned_session(self):
        process = Mock()
        process.communicate.return_value = (b"", b"")
        process.returncode = 0
        with patch.object(b.subprocess, "Popen", return_value=process) as call:
            b.command(self.root, ("control", "build"), capture=False)
        self.assertTrue(call.call_args.kwargs["start_new_session"])
        process.communicate.assert_called_once_with(timeout=1800)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Owned process groups are a Linux server contract")
    def test_real_timeout_stops_owned_descendants_and_masks_arguments(self):
        marker = self.root / "timeout-child.json"
        token = "owned-timeout-" + self.root.name
        child = ("import json,os,signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                 "open(sys.argv[1],'w').write(json.dumps({'pid':os.getpid(),'pgid':os.getpgrp()}));time.sleep(20)")
        runner = self.put("timeout-runner.py", "import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-B','-c',"
                          + repr(child) + ",sys.argv[1],sys.argv[2]])\ntime.sleep(20)\n")
        try:
            with self.assertRaisesRegex(b.BootstrapError, "Command timed out") as caught:
                b.command(self.root, (sys.executable, "-B", str(runner), str(marker), token), capture=False, _timeout=0.5)
            self.assertNotIn(token, str(caught.exception))
            self.assertTrue(marker.is_file(), "The actual child must exist before the timeout")
            pid = json.loads(marker.read_text())["pid"]
            status = Path("/proc") / str(pid) / "status"
            deadline = time.monotonic() + 3
            while status.exists() and "\nState:\tZ" not in status.read_text() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(not status.exists() or "\nState:\tZ" in status.read_text(), "Owned child is still running")
        finally:
            # Failure-only cleanup is restricted by this exact marker and command token.
            if marker.exists():
                pid = json.loads(marker.read_text())["pid"]
                proc = Path("/proc") / str(pid)
                if proc.exists() and token.encode() in (proc / "cmdline").read_bytes():
                    os.kill(pid, 9)



if __name__ == "__main__":
    unittest.main()
