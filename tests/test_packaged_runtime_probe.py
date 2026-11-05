"""Cheap fail-closed checks for the opt-in packaged Mac acceptance adapter."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("packaged_probe", ROOT / "tests/fixtures/packaged_runtime_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class PackagedProbeTests(unittest.TestCase):
    def test_worker_manifest_preserves_graph_and_pins_both_foundation_selectors(self):
        content = (ROOT / "amplifier_web/runtime_deps/pyproject.toml").read_bytes()
        original = tomllib.loads(content.decode())
        actual = probe.pinned_manifest(content)
        expected = copy.deepcopy(original)
        sources = expected["tool"]["uv"]["sources"]
        for name in ("amplifier-foundation", "amplifier-module-hooks-session-naming"):
            sources[name]["rev"] = probe.FOUNDATION
        sources["amplifier-module-loop-live"]["rev"] = probe.LOOP
        expected["tool"]["uv"]["override-dependencies"] = [
            f"amplifier-foundation @ git+{probe.FOUNDATION_URL}@{probe.FOUNDATION}"]
        self.assertEqual(actual, expected)
        self.assertIn("amplifier-core", actual["tool"]["uv"]["no-build-package"])

    def test_manifest_refuses_unexpected_foundation_owner(self):
        content = (ROOT / "amplifier_web/runtime_deps/pyproject.toml").read_bytes()
        with self.assertRaisesRegex(AssertionError, "Unexpected Foundation source"):
            probe.pinned_manifest(content.replace(probe.FOUNDATION_URL.encode(), b"https://other.invalid/foundation"))

    def test_environment_does_not_inherit_secrets_or_import_paths(self):
        with patch.dict(os.environ, {"PATH": "/usr/bin", "OPENAI_API_KEY": "secret", "GH_TOKEN": "secret",
                                     "PYTHONPATH": "/checkout", "AMPLIFIER_HOME": "/real/home"}, clear=True):
            env = probe.clean_environment(Path("/private/probe"), Path("/private/loop"))
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["AMPLIFIER_HOME"], "/private/probe/home/amplifier")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], probe.LOOP_URL)
        self.assertEqual(env["UV_NO_BUILD_PACKAGE"], "amplifier-core")

    def test_identity_rejects_wrong_commit_bytes_and_checkout_import(self):
        record = {"foundation": "/installed/lib/amplifier_foundation/sources/shared.py",
                  "sharedSha256": probe.SHARED_SHA256,
                  "directUrl": {"url": probe.FOUNDATION_URL, "vcs_info": {"commit_id": probe.FOUNDATION}}}
        probe.verify_identity(record, prefix=Path("/installed"), checkout=Path("/checkout"))
        wrong = copy.deepcopy(record)
        wrong["directUrl"]["vcs_info"]["commit_id"] = "8d26"
        with self.assertRaisesRegex(AssertionError, "commit differs"):
            probe.verify_identity(wrong, prefix=Path("/installed"), checkout=Path("/checkout"))
        with self.assertRaisesRegex(AssertionError, "bytes differ"):
            probe.verify_identity({**record, "sharedSha256": "0" * 64}, prefix=Path("/installed"), checkout=Path("/checkout"))
        with self.assertRaisesRegex(AssertionError, "checkout"):
            probe.verify_identity(record, prefix=Path("/installed"), checkout=Path("/installed"))

    def test_publication_oracle_rejects_writable_root_changed_bytes_modes_and_extras(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin, target = Path(tmp) / "origin", Path(tmp) / "target"
            origin.mkdir()
            target.mkdir()
            (origin / "file").write_bytes(b"original")
            (origin / "file").chmod(0o644)
            (target / "file").write_bytes(b"original")
            (target / "file").chmod(0o444)
            marker = target / ".amplifier_cache_meta.json"
            marker.write_text(json.dumps({"commit": "rev", "immutable": True}))
            marker.chmod(0o444)
            target.chmod(0o555)
            def git(_path, *args):
                return {("rev-parse", "HEAD"): "rev", ("ls-files",): "file",
                        ("status", "--porcelain", "--untracked-files=no"): "",
                        ("ls-files", "--others"): ".amplifier_cache_meta.json"}[args]
            try:
                with patch.object(probe, "git", git):
                    self.assertEqual(probe.inspect_object(target, origin, "rev")["rootMode"], "0o555")
                    target.chmod(0o755)
                    with self.assertRaisesRegex(AssertionError, "exactly 0555"):
                        probe.inspect_object(target, origin, "rev")
                    target.chmod(0o555)
                    (target / "file").chmod(0o644)
                    with self.assertRaisesRegex(AssertionError, "mode changed"):
                        probe.inspect_object(target, origin, "rev")
                    (target / "file").write_bytes(b"changed")
                    (target / "file").chmod(0o444)
                    with self.assertRaisesRegex(AssertionError, "bytes changed"):
                        probe.inspect_object(target, origin, "rev")
            finally:
                target.chmod(0o755)
                marker.chmod(0o644)
                (target / "file").chmod(0o644)

    def test_synthetic_modules_compile_without_import_side_effects(self):
        compile(probe.HOOK, "synthetic-hook", "exec")
        compile(probe.PROVIDER, "synthetic-provider", "exec")
        self.assertIn('raise AssertionError("Packaged cache acceptance must never generate")', probe.PROVIDER)


if __name__ == "__main__":
    unittest.main()
