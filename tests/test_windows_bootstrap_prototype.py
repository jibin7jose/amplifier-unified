"""Prototype packaging/command models, not clean-Windows installation evidence."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capsule = load("windows_bootstrap_capsule")
launcher = load("windows_prototype_launch")


def wheel(path, name="amplifier-unified", version="0.54.5"):
    with zipfile.ZipFile(path, "w") as output:
        output.writestr(f"{name.replace('-', '_')}-{version}.dist-info/METADATA",
                        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")


def model_inventory(base, wheels):
    """Synthetic inventory for packaging tests, not a production closure audit."""
    path = base / "inventory.json"
    packages = {}
    for item in wheels.glob("*.whl"):
        name, version = capsule.wheel_identity(item)
        packages[name] = {"version": version, "sha256": capsule.sha256(item)}
    path.write_text(json.dumps({"schema": 1, "source": "a" * 40,
                               "lockSha256": "b" * 64, "packages": packages}))
    return path


class CapsuleTests(unittest.TestCase):
    def test_reproducible_hashed_capsule_and_fresh_output_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            wheels = base / "wheels"
            wheels.mkdir()
            wheel(wheels / "amplifier_unified-0.54.5-py3-none-any.whl")
            wheel(wheels / "dependency-1-py3-none-any.whl", "dependency", "1")
            args = dict(inventory=model_inventory(base, wheels),
                        source="a" * 40, version="0.54.5", python="3.13.8",
                        url="https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/windows-prototype.zip")
            first = capsule.build(wheels, base / "first", **args)
            second = capsule.build(wheels, base / "second", **args)
            self.assertEqual(first, second)
            manifest = json.loads((base / "first/windows-prototype.json").read_text())
            self.assertEqual(manifest["status"], "prototype")
            self.assertEqual(manifest["source"], "a" * 40)
            with zipfile.ZipFile(base / "first/windows-prototype.zip") as archive:
                requirements = archive.read("requirements.txt").decode()
                self.assertEqual(requirements.count("--hash=sha256:"), 2)
                self.assertNotIn("git+", requirements)
                self.assertEqual(archive.read("inventory.json"), args["inventory"].read_bytes())
                self.assertEqual(archive.read("launch.py"),
                                 (ROOT / "scripts/windows_prototype_launch.py").read_bytes())
                for entry in archive.infolist():
                    self.assertNotIn("..", entry.filename)
                    self.assertFalse(entry.filename.startswith("/"))
            with self.assertRaises(FileExistsError):
                capsule.build(wheels, base / "first", **args)
            self.assertEqual(capsule.sha256(base / "first/windows-prototype.zip"), first["capsuleSha256"])

    def test_rejects_missing_app_duplicate_identity_and_invalid_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            wheel(base / "other-1-py3-none-any.whl", "other", "1")
            args = dict(inventory=model_inventory(base, base),
                        source="a" * 40, version="0.54.5", python="3.13.8",
                        url="https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/windows-prototype.zip")
            with self.assertRaisesRegex(ValueError, "selected app"):
                capsule.build(base, base / "out", **args)
            wheel(base / "duplicate-1-py3-none-any.whl", "other", "1")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                capsule.build(base, base / "out", **args)
            for key, value in (("source", "main"), ("python", "3.15"),
                               ("url", "http://example.test/capsule.zip")):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    capsule.build(base, base / "out", **{**args, key: value})
            self.assertFalse((base / "out").exists())

    def test_rejects_symlink_wheel(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            wheel(base / "real-0.54.5-py3-none-any.whl")
            (base / "link-0.54.5-py3-none-any.whl").symlink_to(base / "real-0.54.5-py3-none-any.whl")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                capsule.build(base, base / "out", inventory=base / "unused.json",
                              source="a"*40, version="0.54.5", python="3.13.8",
                              url="https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/windows-prototype.zip")

    def test_wrong_platform_and_free_threaded_wheels_refuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("pkg-1-cp313-cp313-manylinux_x86_64.whl",
                         "pkg-1-cp313t-cp313t-win_amd64.whl",
                         "pkg-1-cp314-cp314-win_amd64.whl",
                         "pkg-1-cp314-abi3-win_amd64.whl",
                         "pkg-1-py314-none-any.whl"):
                path = Path(temporary) / name
                wheel(path, "pkg", "1")
                with self.assertRaisesRegex(ValueError, "ordinary Windows"):
                    capsule.wheel_identity(path)

    def test_ordinary_cp313_accepts_compatible_abi3_and_pure_tags(self):
        for tag in (("cp313", "cp313", "win_amd64"),
                    ("cp311", "abi3", "win_amd64"),
                    ("cp310", "abi3", "win_amd64"),
                    ("py3", "none", "win_amd64"), ("py3", "none", "any")):
            with self.subTest(tag=tag):
                self.assertTrue(capsule.compatible_tag(*tag))

    def test_inventory_rejects_extra_missing_wrong_version_or_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            wheels = base / "wheels"
            wheels.mkdir()
            app = wheels / "amplifier_unified-0.54.5-py3-none-any.whl"
            wheel(app)
            inventory = model_inventory(base, wheels)
            args = dict(inventory=inventory, source="a" * 40, version="0.54.5", python="3.13.8",
                        url="https://github.com/microsoft/amplifier-unified/releases/download/prototype-abc/windows-prototype.zip")
            dev = wheels / "pytest-9-py3-none-any.whl"
            wheel(dev, "pytest", "9")
            with self.assertRaisesRegex(ValueError, "reviewed production inventory"):
                capsule.build(wheels, base / "extra", **args)
            # Change the reviewed expected set, not the wheel inputs.
            record = json.loads(inventory.read_text())
            record["packages"]["required-dependency"] = {"version": "1", "sha256": "c" * 64}
            record["packages"]["pytest"] = {"version": "9", "sha256": capsule.sha256(dev)}
            inventory.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "reviewed production inventory"):
                capsule.build(wheels, base / "missing", **args)
            record["packages"].pop("required-dependency")
            record["packages"]["pytest"]["version"] = "8"
            inventory.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "reviewed production inventory"):
                capsule.build(wheels, base / "version", **args)
            record["packages"]["pytest"]["version"] = "9"
            record["packages"]["amplifier-unified"]["sha256"] = "0" * 64
            inventory.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "reviewed production inventory"):
                capsule.build(wheels, base / "bytes", **args)
            record["source"] = "c" * 40
            inventory.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "source/lock inventory"):
                capsule.build(wheels, base / "source", **args)
            self.assertFalse(any((base / name).exists()
                                 for name in ("extra", "missing", "version", "bytes", "source")))


class SourceCapsuleTests(unittest.TestCase):
    """Portable synthetic frozen-input models, never real artifact qualification."""

    @staticmethod
    def source_wheel(directory, name, version, tag="py3-none-any"):
        dist = name.replace("-", "_")
        path = directory / f"{dist}-{version}-{tag}.whl"
        metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode()
        with zipfile.ZipFile(path, "w") as archive:
            for member, raw in (
                (f"{dist}-{version}.dist-info/METADATA", metadata),
                (f"{dist}-{version}.dist-info/WHEEL",
                 f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n".encode()),
            ):
                archive.writestr(zipfile.ZipInfo(member, (2026, 1, 1, 0, 0, 0)), raw)
        return path, metadata

    @contextmanager
    def model(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            wheels, backend = base / "wheels", base / "build-wheels"
            wheels.mkdir()
            backend.mkdir()
            versions = {"packaging": "26.3", "uv": "0.13.4",
                        **{f"synthetic-dep-{i:02d}": "1.0" for i in range(57)}}
            locked = {}
            for name, version in versions.items():
                path, _ = self.source_wheel(wheels, name, version)
                locked[name] = (version, path)
            for name, version in capsule.BUILD_PACKAGES.items():
                path, _ = self.source_wheel(backend, name, version)
                locked[name] = (version, path)
            app, metadata = self.source_wheel(wheels, "amplifier-unified", capsule.APP_VERSION)
            blocks = [
                f'[[package]]\nname = "amplifier-unified"\nversion = "{capsule.APP_VERSION}"\n'
                'source = { editable = "." }\n'
            ]
            for name, (version, path) in sorted(locked.items()):
                blocks.append(
                    f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
                    'source = { registry = "https://pypi.org/simple" }\n'
                    f'wheels = [{{ url = "https://files.pythonhosted.org/model/{path.name}", '
                    f'hash = "sha256:{capsule.sha256(path)}", size = {path.stat().st_size} }}]\n'
                )
            requirements = [f"{name}=={version}" for name, version in versions.items()]
            for name, origin in capsule.GIT_SOURCES.items():
                source = origin["url"] + "?"
                if origin["subdirectory"]:
                    source += "subdirectory=" + origin["subdirectory"].replace("/", "%2F") + "&"
                source += "rev=main#" + origin["commit"]
                blocks.append(f'[[package]]\nname = "{name}"\nversion = "1.0"\n'
                              f'source = {{ git = "{source}" }}\n')
                requirements.append(capsule.git_requirement(name, origin, extras=False))
            requirements.append("httpx2-jsfetch==1.0 ; sys_platform == 'emscripten'")
            lock = base / "uv.lock"
            lock.write_bytes("\n".join(blocks).encode())
            production = base / "production-requirements.txt"
            production.write_bytes(("\n".join(sorted(requirements)) + "\n").encode())
            inventory = model_inventory(base, wheels)
            record = json.loads(inventory.read_text())
            record["source"] = capsule.APP_SOURCE
            record["lockSha256"] = capsule.sha256(lock)
            inventory.write_text(json.dumps(record))
            # Only frozen constants change for model bytes; all real validators run.
            with mock.patch.multiple(
                capsule, APP_SHA256=capsule.sha256(app), APP_BYTES=app.stat().st_size,
                APP_METADATA_SHA256=hashlib.sha256(metadata).hexdigest(),
                LOCK_SHA256=capsule.sha256(lock), PRODUCTION_REQUIREMENTS_SHA256=capsule.sha256(production),
            ):
                args = dict(
                    inventory=inventory, source=capsule.APP_SOURCE, version=capsule.APP_VERSION,
                    python="3.13.8",
                    url="https://github.com/microsoft/amplifier-unified/releases/download/prototype-model/windows-prototype.zip",
                    source_mode="pinned-vcs", build_wheelhouse=backend, lock=lock,
                    production_requirements=production,
                )
                yield base, wheels, args

    def payload(self, base, wheels, args):
        result = capsule.build(wheels, base / "output", **args)
        payload = base / "payload"
        payload.mkdir()
        with zipfile.ZipFile(base / "output/windows-prototype.zip") as archive:
            archive.extractall(payload)
        return payload, result

    def distributions(self, payload):
        spec = capsule.validate_source(payload)
        distributions = []
        app_path = payload / "wheels" / spec["packages"]["amplifier-unified"]["file"]
        with zipfile.ZipFile(app_path) as archive:
            app_metadata = archive.read(f"amplifier_unified-{capsule.APP_VERSION}.dist-info/METADATA").decode()
        for name, version in spec["expected"].items():
            direct = None
            if name in capsule.GIT_SOURCES:
                origin = capsule.GIT_SOURCES[name]
                direct = {"url": origin["url"],
                          "vcs_info": {"vcs": "git", "commit_id": origin["commit"], "requested_revision": "main"}}
                if origin["subdirectory"]:
                    direct["subdirectory"] = origin["subdirectory"]
            texts = {"direct_url.json": json.dumps(direct) if direct is not None else None,
                     "METADATA": app_metadata if name == "amplifier-unified" else ""}
            distributions.append(types.SimpleNamespace(
                metadata={"Name": name}, version=version, texts=texts, read_text=texts.get,
            ))
        return distributions

    def test_deterministic_source_capsule_exact_phases_and_original_checker(self):
        with self.model() as (base, wheels, args):
            payload, result = self.payload(base, wheels, args)
            self.assertEqual(result, capsule.build(wheels, base / "second", **args))
            spec = capsule.validate_source(payload)
            self.assertEqual(len(spec["packages"]), 60)
            self.assertEqual(len(spec["expected"]), 65)
            self.assertEqual(len(spec["git"]), 5)
            self.assertEqual(len(spec["buildPackages"]), 6)
            self.assertEqual((payload / "uv.lock").read_bytes(), args["lock"].read_bytes())
            self.assertEqual((payload / "production-requirements.txt").read_bytes(),
                             args["production_requirements"].read_bytes())
            self.assertEqual((payload / "source_install.py").read_bytes(),
                             (ROOT / "scripts/windows_bootstrap_capsule.py").read_bytes())
            hashed = (payload / "requirements.txt").read_text()
            self.assertEqual(hashed.count("--hash=sha256:"), 60)
            self.assertNotIn("git+", hashed)
            self.assertIn("uv==0.13.4 ", hashed)
            git = (payload / "git-requirements.txt").read_text()
            self.assertEqual(len(git.splitlines()), 5)
            self.assertEqual(git.count("git+https://github.com/microsoft/"), 5)
            self.assertIn("amplifier-bundle-context-intelligence[client] @ ", git)
            self.assertIn("#subdirectory=modules/hooks-session-naming", git)
            for forbidden in ("-e ", "--editable", "@main", "amplifier-unified @"):
                self.assertNotIn(forbidden, git)
            manifest = json.loads((base / "output/windows-prototype.json").read_text())
            self.assertEqual(manifest["sourceMode"], "pinned-vcs")
            self.assertEqual(manifest["status"], "prototype")
            self.assertEqual(json.loads((payload / "capsule.json").read_text())["sourceMode"], "pinned-vcs")
            backend = json.loads((payload / "source-spec.json").read_text())["buildPackages"]
            self.assertEqual(set((payload / "build-constraints.txt").read_text().splitlines()),
                             {f"{name}=={item['version']} --hash=sha256:{item['sha256']}"
                              for name, item in backend.items()})
            with self.assertRaises(FileExistsError):
                capsule.build(wheels, base / "output", **args)

    def test_missing_mode_inputs_app_source_and_unrecognized_mode_refuse(self):
        with self.model() as (base, wheels, args):
            for key, value in (
                ("lock", None), ("production_requirements", None), ("build_wheelhouse", None),
                ("source", "a" * 40), ("version", "0.54.6"), ("source_mode", "unknown"),
                ("source_mode", "offline"),
            ):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    capsule.build(wheels, base / "bad", **{**args, key: value})
            self.assertFalse((base / "bad").exists())


    def test_frozen_lock_requirements_and_inventory_hashes_refuse(self):
        with self.model() as (base, wheels, args):
            for key in ("lock", "production_requirements"):
                path = args[key]
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, "hash mismatch"):
                    capsule.build(wheels, base / "bad", **args)
                path.write_bytes(original)
            record = json.loads(args["inventory"].read_text())
            record["lockSha256"] = "0" * 64
            args["inventory"].write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "inventory frozen lock"):
                capsule.build(wheels, base / "bad", **args)
            self.assertFalse((base / "bad").exists())

    def test_wrong_git_commit_or_subdirectory_in_lock_reject_even_with_model_hash(self):
        with self.model() as (base, wheels, args):
            original = args["lock"].read_bytes()
            for old, new in (
                (b"c716df3cea84b2539aabdfea58a69ae676a1e853", b"0" * 40),
                (b"modules%2Fhooks-session-naming", b"modules%2Fother"),
                (b"https://github.com/microsoft/amplifier-foundation", b"http://github.com/microsoft/amplifier-foundation"),
            ):
                changed = original.replace(old, new)
                with mock.patch.object(capsule, "LOCK_SHA256", hashlib.sha256(changed).hexdigest()), \
                     self.subTest(old=old), self.assertRaisesRegex(ValueError, "Git lock"):
                    capsule.frozen_selection(changed, args["production_requirements"].read_bytes())

    def test_wrong_git_requirement_or_production_selection_refuses(self):
        with self.model() as (base, wheels, args):
            original = args["production_requirements"].read_bytes()
            for old, new in (
                (b"@a80ce69d0e96e9a343501f238dcd867350dd9aa2", b"@main"),
                (b"#subdirectory=modules/hooks-session-naming", b"#subdirectory=other"),
                (b"synthetic-dep-00==1.0\n", b""),
                (b"synthetic-dep-00==1.0", b"pytest==9.0"),
            ):
                changed = original.replace(old, new)
                with mock.patch.object(capsule, "PRODUCTION_REQUIREMENTS_SHA256",
                                       hashlib.sha256(changed).hexdigest()), \
                     self.subTest(old=old), self.assertRaises(ValueError):
                    capsule.frozen_selection(args["lock"].read_bytes(), changed)

    def test_missing_extra_dev_and_changed_registry_wheel_refuse(self):
        with self.model() as (base, wheels, args):
            selected = sorted(wheels.glob("synthetic_dep_*.whl"))[0]
            raw = selected.read_bytes()
            selected.unlink()
            with self.assertRaisesRegex(ValueError, "missing or extra"):
                capsule.build(wheels, base / "bad", **args)
            selected.write_bytes(raw)
            dev, _ = self.source_wheel(wheels, "pytest", "9.0")
            with self.assertRaisesRegex(ValueError, "missing or extra"):
                capsule.build(wheels, base / "bad", **args)
            dev.unlink()
            with zipfile.ZipFile(selected, "a") as archive:
                archive.writestr("changed.py", "changed bytes")
            # Even a self-certified actual-hash inventory cannot bypass the lock.
            record = json.loads(args["inventory"].read_text())
            name, _ = capsule.wheel_identity(selected)
            record["packages"][name]["sha256"] = capsule.sha256(selected)
            args["inventory"].write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "frozen lock wheel"):
                capsule.build(wheels, base / "bad", **args)
            self.assertFalse((base / "bad").exists())

    def test_wrong_app_bytes_size_metadata_and_reviewed_inventory_refuse(self):
        with self.model() as (base, wheels, args):
            for constant, value in (
                ("APP_SHA256", "0" * 64), ("APP_BYTES", 10),
                ("APP_METADATA_SHA256", "0" * 64),
            ):
                with mock.patch.object(capsule, constant, value), self.subTest(constant=constant), \
                     self.assertRaisesRegex(ValueError, "Original app wheel"):
                    capsule.build(wheels, base / "bad", **args)
            record = json.loads(args["inventory"].read_text())
            record["packages"].pop("uv")
            args["inventory"].write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "reviewed production inventory"):
                capsule.build(wheels, base / "bad", **args)

    def test_wrong_build_backend_version_hash_extra_and_native_wheel_refuse(self):
        with self.model() as (base, wheels, args):
            backend = args["build_wheelhouse"]
            selected = backend / "hatchling-1.32.4-py3-none-any.whl"
            original = selected.read_bytes()
            selected.unlink()
            wrong, _ = self.source_wheel(backend, "hatchling", "1.32.5")
            with self.assertRaisesRegex(ValueError, "wrong-version"):
                capsule.build(wheels, base / "bad", **args)
            wrong.unlink()
            selected.write_bytes(original + b"changed")
            with self.assertRaisesRegex(ValueError, "frozen lock wheel"):
                capsule.build(wheels, base / "bad", **args)
            selected.write_bytes(original)
            extra, _ = self.source_wheel(backend, "setuptools", "1")
            with self.assertRaisesRegex(ValueError, "missing or extra"):
                capsule.build(wheels, base / "bad", **args)
            extra.unlink()
            selected.unlink()
            native, _ = self.source_wheel(backend, "hatchling", "1.32.4", "cp313-cp313-win_amd64")
            with self.assertRaisesRegex(ValueError, "frozen lock wheel"):
                capsule.build(wheels, base / "bad", **args)
            with self.assertRaisesRegex(ValueError, "pure Python"):
                capsule.strict_wheel(native.read_bytes(), native.name, pure=True)

    def test_payload_drift_malicious_requirements_manifest_mode_and_spec_refuse(self):
        with self.model() as (base, wheels, args):
            payload, _ = self.payload(base, wheels, args)
            for name, changed in (
                ("git-requirements.txt", b"evil @ git+https://example.test/evil@main\n"),
                ("requirements.txt", b"--index-url https://example.test\n"),
                ("build-constraints.txt", b"hatchling==0\n"),
                ("source-spec.json", b"{}\n"),
                ("capsule.json", capsule.canonical_json({"source": capsule.APP_SOURCE, "version": capsule.APP_VERSION})),
                ("source_install.py", b"print('not the checker')\n"),
            ):
                path = payload / name
                original = path.read_bytes()
                path.write_bytes(changed)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    capsule.validate_source(payload)
                path.write_bytes(original)
            (payload / "extra.txt").write_text("not allowed")
            with self.assertRaisesRegex(ValueError, "payload paths"):
                capsule.validate_source(payload)

    def test_symlinks_sdist_and_unsafe_wheel_members_refuse(self):
        with self.model() as (base, wheels, args):
            selected = sorted(wheels.glob("synthetic_dep_*.whl"))[0]
            original = selected.read_bytes()
            retained = base / "retained.whl"
            selected.rename(retained)
            selected.symlink_to(retained)
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                capsule.build(wheels, base / "bad", **args)
            selected.unlink()
            selected.write_bytes(original)
            selected.rename(selected.with_suffix(".tar.gz"))
            with self.assertRaisesRegex(ValueError, "filename"):
                capsule.build(wheels, base / "bad", **args)
            selected.with_suffix(".tar.gz").rename(selected)
            with zipfile.ZipFile(selected, "a") as archive:
                archive.writestr("../escape.py", "not executed")
            with self.assertRaisesRegex(ValueError, "frozen lock wheel"):
                capsule.build(wheels, base / "bad", **args)
            with self.assertRaisesRegex(ValueError, "Unsafe wheel member"):
                capsule.strict_wheel(selected.read_bytes(), selected.name)


    def test_audit_exact_set_honest_git_ignores_requested_revision_and_is_read_only(self):
        with self.model() as (base, wheels, args):
            payload, _ = self.payload(base, wheels, args)
            before = {str(path.relative_to(payload)): capsule.sha256(path)
                      for path in payload.rglob("*") if path.is_file()}
            distributions = self.distributions(payload)
            capsule.audit_source(payload, distributions)
            for dist in distributions:
                if dist.metadata["Name"] in capsule.GIT_SOURCES:
                    direct = json.loads(dist.texts["direct_url.json"])
                    direct["vcs_info"]["requested_revision"] = "a-human-label"
                    dist.texts["direct_url.json"] = json.dumps(direct)
            capsule.audit_source(payload, distributions)
            self.assertEqual(before, {str(path.relative_to(payload)): capsule.sha256(path)
                                      for path in payload.rglob("*") if path.is_file()})

    def test_audit_wrong_git_url_commit_subdir_editable_and_fake_archive_refuse(self):
        with self.model() as (base, wheels, args):
            payload, _ = self.payload(base, wheels, args)
            distributions = self.distributions(payload)
            target = next(dist for dist in distributions
                          if dist.metadata["Name"] == "amplifier-module-hooks-session-naming")
            original = target.texts["direct_url.json"]
            for change in (
                {"url": "file:///a/local/wheel"},
                {"vcs_info": {"vcs": "git", "commit_id": "0" * 40}},
                {"subdirectory": "other"},
                {"dir_info": {"editable": True}},
                {"archive_info": {"hash": "sha256=fake"}},
            ):
                direct = {**json.loads(original), **change}
                target.texts["direct_url.json"] = json.dumps(direct)
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, "Installed Git origin"):
                    capsule.audit_source(payload, distributions)
            target.texts["direct_url.json"] = None
            with self.assertRaisesRegex(ValueError, "Installed Git origin"):
                capsule.audit_source(payload, distributions)

    def test_audit_extra_missing_wrong_version_registry_origin_and_app_metadata_refuse(self):
        with self.model() as (base, wheels, args):
            payload, _ = self.payload(base, wheels, args)
            distributions = self.distributions(payload)
            with self.assertRaisesRegex(ValueError, "set/versions"):
                capsule.audit_source(payload, distributions[:-1])
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                capsule.audit_source(payload, distributions + [distributions[0]])
            extra = types.SimpleNamespace(metadata={"Name": "pytest"}, version="9.0", read_text=lambda _: None)
            with self.assertRaisesRegex(ValueError, "set/versions"):
                capsule.audit_source(payload, distributions + [extra])
            app = next(dist for dist in distributions if dist.metadata["Name"] == "amplifier-unified")
            app.version = "0.0"
            with self.assertRaisesRegex(ValueError, "set/versions"):
                capsule.audit_source(payload, distributions)
            app.version = capsule.APP_VERSION
            app.texts["direct_url.json"] = json.dumps({"dir_info": {"editable": True}, "url": "file:///src"})
            with self.assertRaisesRegex(ValueError, "unexpected direct origin"):
                capsule.audit_source(payload, distributions)
            app.texts["direct_url.json"] = None
            app.texts["METADATA"] += "\nChanged: yes\n"
            with self.assertRaisesRegex(ValueError, "METADATA"):
                capsule.audit_source(payload, distributions)

    def test_frozen_backend_lock_version_and_source_refuse(self):
        with self.model() as (_, _, args):
            original = args["lock"].read_bytes()
            for old, new in (
                (b'version = "1.32.4"', b'version = "1.32.5"'),
                (b'name = "hatchling"', b'name = "other-backend"'),
            ):
                changed = original.replace(old, new)
                with mock.patch.object(capsule, "LOCK_SHA256", hashlib.sha256(changed).hexdigest()), \
                     self.subTest(old=old), self.assertRaisesRegex(ValueError, "backend selection"):
                    capsule.frozen_selection(changed, args["production_requirements"].read_bytes())

    def test_checker_cli_runs_without_site_or_application_imports(self):
        with self.model() as (base, wheels, args):
            payload, _ = self.payload(base, wheels, args)
            constants = {name: getattr(capsule, name) for name in (
                "APP_SHA256", "APP_BYTES", "APP_METADATA_SHA256", "LOCK_SHA256", "PRODUCTION_REQUIREMENTS_SHA256",
            )}
            # Load the shipped checker in a site-free child, replacing only its
            # frozen constants for synthetic bytes. No application install/import.
            code = (
                "import importlib.util,json,sys;"
                "s=importlib.util.spec_from_file_location('checker',sys.argv[1]);"
                "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                "[setattr(m,k,v) for k,v in json.loads(sys.argv[3]).items()];"
                "sys.argv=['source_install.py','--validate-source',sys.argv[2]];m.main()"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", code, str(payload / "source_install.py"),
                 str(payload), json.dumps(constants)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), "PINNED VCS SOURCE VALIDATION PASS")
            self.assertFalse(any(payload.rglob("__pycache__")))

    def test_source_size_budgets_and_payload_symlinks_refuse(self):
        with self.model() as (base, wheels, args):
            with mock.patch.object(capsule, "MAX_WHEEL_BYTES", 8), \
                 self.assertRaisesRegex(ValueError, "oversized"):
                capsule.build(wheels, base / "bad", **args)
            with mock.patch.object(capsule, "MAX_EXPANDED_BYTES", 8), \
                 self.assertRaisesRegex(ValueError, "budget"):
                capsule.build(wheels, base / "bad", **args)
            payload, _ = self.payload(base, wheels, args)
            path = payload / "git-requirements.txt"
            retained = base / "retained-requirements.txt"
            path.rename(retained)
            path.symlink_to(retained)
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                capsule.validate_source(payload)
            path.unlink()
            retained.rename(path)
            (payload / "wheels").rename(base / "retained-wheels")
            (payload / "wheels").symlink_to(base / "retained-wheels", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                capsule.validate_source(payload)

    def test_strict_filename_metadata_tag_and_member_path_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path, _ = self.source_wheel(directory, "pkg", "1")
            raw = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "METADATA identity"):
                capsule.strict_wheel(raw, "different-1-py3-none-any.whl")
            with self.assertRaisesRegex(ValueError, "WHEEL tag"):
                capsule.strict_wheel(raw, "pkg-1-cp313-cp313-win_amd64.whl")
            for tag in ("cp313t-cp313t-win_amd64", "cp314-cp314-win_amd64", "cp313-cp313-manylinux_x86_64"):
                with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "ordinary Windows"):
                    capsule.strict_wheel(raw, f"pkg-1-{tag}.whl")
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("pkg.py", "one")
                # A distinct slash spelling must not evade member path checks.
                archive.writestr("pkg/../pkg.py", "two")
            with self.assertRaisesRegex(ValueError, "Unsafe wheel member"):
                capsule.strict_wheel(path.read_bytes(), path.name)


class LauncherTests(unittest.TestCase):
    def test_environment_survives_missing_shell_path_without_copying_keys(self):
        root = Path("/synthetic/trial space/Unicode-λ")
        receipt = {"uv": "/private/tools/uv", "git": "/private/git/bin/git",
                   "environment": {"OPENAI_API_KEY": "must-not-be-used"}}
        with mock.patch.dict(os_environ := launcher.os.environ, {"PATH": "/old/path"}, clear=True):
            before = dict(os_environ)
            env = launcher.child_environment(root, receipt)
            self.assertEqual(env["AMPLIFIER_SESSION_STATE_HOME"], str(root / "ownership"))
            self.assertEqual(env["AMPLIFIER_HOME"], str(root / "shared"))
            self.assertEqual(env["AMPLIFIER_UNIFIED_IMPORT_HOME"], str(root / "shared"))
            self.assertEqual(env["AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH"], str(root / "shared/projects"))
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("HOME", env)
            self.assertNotIn("USERPROFILE", env)
            self.assertEqual(env["PATH"], launcher.os.pathsep.join([
                str(Path(receipt["uv"]).parent),
                str(Path(receipt["git"]).parent),
                before["PATH"],
            ]))
            self.assertEqual(dict(os_environ), before)

    def test_standard_windows_profile_survives_root_local_tool_overrides(self):
        profile = {"USERPROFILE": r"C:\Users\Trial User",
                   "HOMEDRIVE": "C:", "HOMEPATH": r"\Users\Trial User", "PATH": "",
                   "GIT_EXEC_PATH": "/untrusted/helpers", "GIT_TEMPLATE_DIR": "/untrusted/hooks",
                   "UV_CONFIG_FILE": "/untrusted/uv.toml", "PIP_INDEX_URL": "https://example.invalid",
                   "PYTHONPATH": "/untrusted/modules", "OPENAI_API_KEY": "synthetic-inherited-only"}
        with mock.patch.dict(launcher.os.environ, profile, clear=True):
            env = launcher.child_environment(Path("/trial"),
                                             {"sourceMode": "pinned-vcs", "uv": "/uv/uv", "git": "/git/git"})
        for key in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "OPENAI_API_KEY"):
            self.assertEqual(env[key], profile[key])
        for key in ("GIT_EXEC_PATH", "GIT_TEMPLATE_DIR", "UV_CONFIG_FILE", "PIP_INDEX_URL", "PYTHONPATH"):
            self.assertNotIn(key, env)
        self.assertEqual(env["UV_TOOL_DIR"], str(Path("/trial/tools")))
        self.assertEqual(env["UV_TOOL_BIN_DIR"], str(Path("/trial/tool-bin")))

    def test_cli_commands_use_list_argv_and_hide_sensitive_child_errors(self):
        root = Path("/synthetic/space λ")
        scope = launcher.connection_args(root)
        self.assertEqual(scope[scope.index("--bind")+1], "127.0.0.1")
        self.assertEqual(scope[scope.index("--port")+1], "8942")
        self.assertEqual(scope[scope.index("--public-origin")+1], "https://127.0.0.1:8942")
        with mock.patch.object(launcher.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"public-ca", b"")
            self.assertEqual(launcher.run_cli(root / "exe", ["setup-tls", "export"], {}), b"public-ca")
            self.assertEqual(run.call_args.args[0], [str(root / "exe"), "setup-tls", "export"])
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            run.return_value = subprocess.CompletedProcess([], 1, b"sensitive-grant", b"secret")
            with self.assertRaises(RuntimeError) as caught:
                launcher.run_cli(root / "exe", ["open"], {})
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("sensitive-grant", str(caught.exception))

    def test_listener_timeout_and_exited_child_are_distinct(self):
        server = mock.Mock()
        server.poll.return_value = 1
        with mock.patch.object(launcher.ssl, "create_default_context"), self.assertRaisesRegex(RuntimeError, "exited"):
            launcher.wait_for_listener(server, Path("/public/ca"))
        server.poll.return_value = None
        with mock.patch.object(launcher.ssl, "create_default_context"), \
             mock.patch.object(launcher.time, "monotonic", side_effect=[0, 100]), \
             self.assertRaisesRegex(RuntimeError, "startup did not finish"):
            launcher.wait_for_listener(server, Path("/public/ca"), timeout=1)
        server.kill.assert_not_called()
        server.terminate.assert_not_called()

    def test_check_rejects_non_windows_before_listener_or_mutation(self):
        with mock.patch.dict(sys.modules, {"amplifier_web.windows_files": types.SimpleNamespace(directory=None)}), \
             mock.patch.object(launcher.sys, "platform", "linux"), \
             self.assertRaisesRegex(RuntimeError, "Windows CPython"):
            launcher.check_installation(Path("/trial"), {})

    def test_host_nonzero_exit_is_not_launcher_success(self):
        server = mock.Mock()
        server.poll.return_value = 1
        server.returncode = 1
        with self.assertRaisesRegex(RuntimeError, "unexpectedly"):
            launcher.keep_foreground(server)
        server.kill.assert_not_called()
        server.terminate.assert_not_called()

    def test_ctrl_c_waits_exact_child_and_preserves_cleanup_custody(self):
        server = mock.Mock()
        server.poll.side_effect = [None, None, 0]
        server.wait.side_effect = [KeyboardInterrupt, 0]
        server.returncode = 0
        launcher.keep_foreground(server)
        self.assertEqual(server.wait.call_count, 2)
        server.kill.assert_not_called()

    def test_ctrl_c_during_browser_setup_retains_normal_exit_classification(self):
        server = mock.Mock()
        server.poll.return_value = 130
        server.returncode = 130
        launcher.keep_foreground(server, interrupted=True)
        with self.assertRaisesRegex(RuntimeError, "did not complete normally"):
            launcher.keep_foreground(server)

    def test_browser_refusal_is_pending_without_shutdown_wait(self):
        with mock.patch.object(launcher, "run_cli", side_effect=RuntimeError("closed")), \
             mock.patch.object(launcher, "keep_foreground") as keep:
            self.assertFalse(launcher.open_owned_browser(Path("/host/exe"), Path("/trial"), {}))
            keep.assert_not_called()

    def test_relaunch_refuses_unowned_occupied_port_before_config(self):
        with mock.patch.dict(sys.modules, {"amplifier_web.windows_files": types.SimpleNamespace(directory=None)}), \
             mock.patch.object(launcher, "check_installation", return_value=Path("/host/exe")), \
             mock.patch.object(launcher, "child_environment", return_value={}), \
             mock.patch.object(launcher, "listener_present", return_value=True), \
             mock.patch.object(launcher, "browser_trust_ready", return_value=False), \
             mock.patch.object(launcher, "run_cli") as run, \
             mock.patch.object(launcher.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "occupied"):
                launcher.launch(Path("/trial"), {})
            run.assert_not_called()
            spawn.assert_not_called()

    def test_relaunch_existing_host_uses_owner_open_without_second_server(self):
        with mock.patch.dict(sys.modules, {"amplifier_web.windows_files": types.SimpleNamespace(directory=None)}), \
             mock.patch.object(launcher, "check_installation", return_value=Path("/host/exe")), \
             mock.patch.object(launcher, "child_environment", return_value={}), \
             mock.patch.object(launcher, "listener_present", return_value=True), \
             mock.patch.object(launcher, "browser_trust_ready", return_value=True), \
             mock.patch.object(launcher, "run_cli") as run, \
             mock.patch.object(launcher.subprocess, "Popen") as spawn:
            launcher.launch(Path("/trial"), {})
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[1][-1], "open")
            spawn.assert_not_called()


class BootstrapSourceTests(unittest.TestCase):
    def test_prototype_keeps_public_gate_and_separate_artifact_identity(self):
        source = (ROOT / "scripts/install.ps1").read_text()
        public = source.split("function Assert-NativeActivationContract", 1)[1].split("function ", 1)[0]
        self.assertIn("throw 'Native activation integration is pending", public)
        proto = source.split("function Invoke-UnifiedPrototype", 1)[1].split("# Dot sourcing", 1)[0]
        self.assertNotIn("Assert-NativeActivationContract", proto)
        self.assertIn("Read-PrototypeManifest", proto)
        self.assertLess(proto.index("Assert-FileChecksum $planPath"), proto.index("Find-InstallHelper"))
        self.assertLess(proto.index("Assert-FileChecksum $archive"), proto.index("Expand-ContainedZip"))
        for required in ("'--require-hashes'", "'--no-index'", "'--no-deps'", "'--only-binary', ':all:'"):
            self.assertIn(required, proto)
        self.assertNotIn("'tool', 'install'", proto)
        self.assertNotIn("'service'", proto)
        self.assertNotIn("Register-ScheduledTask", proto)
        self.assertNotIn("Import-Certificate", proto)
        self.assertNotIn("-ExecutionPolicy", source)
        self.assertNotRegex(source, r"\$env:\w+\s*=")

    def test_no_prerequisite_fallback_and_absolute_relaunch(self):
        source = (ROOT / "scripts/install.ps1").read_text()
        proto = source.split("function Invoke-UnifiedPrototype", 1)[1]
        self.assertIn("if (-not $uv) { $uv = Install-VerifiedHelper 'uv' }", proto)
        self.assertIn("if (-not $git) { $git = Install-VerifiedHelper 'git' }", proto)
        self.assertIn("'python', 'install', $plan.python, '--no-bin', '--no-registry'", proto)
        self.assertIn("$shortcutPath = Write-PrototypeShortcut $hostPython $launcher", proto)
        self.assertIn("$shortcut.TargetPath = $Python", source)
        self.assertIn("$shortcut.WorkingDirectory = $script:InstallWork", source)
        self.assertIn("ConvertTo-NativeArgument $launcher", proto)
        self.assertNotIn("py.exe", proto)
        self.assertNotIn("winget", proto.lower())
        self.assertNotIn("curl", proto.lower())
        for field in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "UV_PYTHON_BIN_DIR"):
            self.assertIn(field, source.split("function Get-PrototypeEnvironment", 1)[1])

    def test_pinned_vcs_phase_is_private_explicit_and_audited_before_launch(self):
        source = (ROOT / "scripts/install.ps1").read_text()
        proto = source.split("function Invoke-UnifiedPrototype", 1)[1]
        self.assertIn("if ($mode -eq 'pinned-vcs')", proto)
        self.assertIn("$uv = Install-VerifiedHelper 'uv'", proto)
        self.assertIn("$git = Install-VerifiedHelper 'git'", proto)
        self.assertIn(r"^uv 0\.12\.18", proto)
        self.assertLess(proto.index("'--validate-source'"), proto.index("@('venv'"))
        self.assertLess(proto.index("'--audit-source'"), proto.index("Write-PrototypeShortcut $hostPython"))
        self.assertIn("'--build-constraint'", proto)
        for flag in ("'--verify-hashes'", "'--no-require-hashes'", "'--no-binary'"):
            self.assertIn(flag, proto)
        self.assertNotIn("'--no-build-isolation'", proto)
        environment = source.split("function Get-PrototypeEnvironment", 1)[1].split("function ", 1)[0]
        self.assertNotIn("USERPROFILE =", environment)
        self.assertNotIn("HOMEDRIVE =", environment)
        self.assertNotIn("HOMEPATH =", environment)

    def test_package_child_overrides_cannot_redirect_frozen_install(self):
        source = (ROOT / "scripts/install.ps1").read_text()
        child = source.split("function Invoke-InstallChild", 1)[1].split("function Find-InstallHelper", 1)[0]
        for pattern in ("'UV_*'", "'PIP_*'", "'PYTHONHOME'", "'PYTHONPATH'", "'VIRTUAL_ENV'",
                        "'GIT_EXEC_PATH'", "'GIT_TEMPLATE_DIR'"):
            self.assertIn(pattern, child)
        self.assertLess(child.index("$info.EnvironmentVariables.Remove($key)"),
                        child.index("foreach ($key in $Environment.Keys)"))
        for key in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
            self.assertNotIn(f"'{key}'", child)

    def test_launcher_normal_startup_explicit_trust_and_no_auto_credential_read(self):
        source = (ROOT / "scripts/windows_prototype_launch.py").read_text()
        for text in ('"--no-open", "serve"', '"open"', 'subprocess.Popen', 'pinned.read_private("prototype.json"',
                     'os.startfile(str(ca))', 'answer == "REVIEW"', 'browser_trust_ready()'):
            self.assertIn(text, source)
        for text in ("control-token", "session-secret", "Import-Certificate", "CERT_NONE", "check_hostname = False",
                     "create_app(", "os.kill(", "TerminateProcess"):
            self.assertNotIn(text, source)

    def test_tool_rename_sharing_is_private_directory_only(self):
        source = (ROOT / "scripts/install.ps1").read_text()
        method = source.split("public void EnableToolWrites", 1)[1].split("public FileStream CreateFile", 1)[0]
        for required in ("!pin.directory", "!pin.privateAcl", "!IsOwnedDirectory(path)",
                         "Verify(pin)", "0x20081u, 3u", "info.high != pin.high",
                         "previous.Dispose()"):
            self.assertIn(required, method)
        self.assertLess(method.index("Info info = Inspect"), method.index("previous.Dispose()"))
        proto = source.split("function Invoke-UnifiedPrototype", 1)[1]
        self.assertLess(proto.index("EnableToolWrites($path)"), proto.index("'python', 'install'"))


@unittest.skipUnless(sys.platform == "win32", "native prototype PowerShell model pending")
class NativePrototypeModelTests(unittest.TestCase):
    def test_both_available_powershell_editions_offline(self):
        available = [name for name in ("powershell.exe", "pwsh.exe") if shutil.which(name)]
        if not available:
            self.skipTest("No native PowerShell executable; no model execution claimed")
        for name in available:
            with self.subTest(shell=name):
                environment = {key: value for key, value in launcher.os.environ.items()
                               if name != "powershell.exe" or key.upper() != "PSMODULEPATH"}
                result = subprocess.run(
                    [shutil.which(name), "-NoProfile", "-NonInteractive", "-File",
                     str(ROOT / "tests/fixtures/windows_bootstrap_prototype.ps1"),
                     "-Installer", str(ROOT / "scripts/install.ps1")],
                    capture_output=True, text=True, timeout=90, env=environment,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PROTOTYPE OFFLINE MODEL PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()