"""Development ledger checks; stdlib-only, no application runtime required."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "workspace_manifest.py"
spec = importlib.util.spec_from_file_location("workspace_manifest", SCRIPT)
wm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wm)


class WorkspaceManifestTests(unittest.TestCase):
    def test_listing_never_serializes_historical_bodies(self):
        value = {f"lane-{i:04d}": {"owner": "agent", "status": "complete",
                 "evidence": "private-payload" * 2000} for i in range(1000)}
        raw = json.dumps(value).encode()
        result = wm.summary(raw, value)
        self.assertEqual(result["totalRecords"], 1000)
        self.assertEqual(result["nextOffset"], 10)
        self.assertNotIn("private-payload", json.dumps(result))
        self.assertLess(len(json.dumps(result)), 2500)
        self.assertEqual(wm.summary(raw, value, query="LANE-099", offset=5)["matchedRecords"], 10)
        self.assertIsNone(wm.summary(raw, value, query="missing")["nextOffset"])

    def test_pointer_selects_exact_field_and_rejects_ambiguity(self):
        value = {"a/b": {"~": ["first", "second"]}, "private": "not selected"}
        self.assertEqual(wm.select(value, "/a~1b/~0/1"), "second")
        for pointer in ("a/b", "/a~2b", "/a~1b/~0/01", "/a~1b/~0/3"):
            with self.assertRaises(wm.ManifestError):
                wm.select(value, pointer)
        for raw in (b'{"owner": 1, "owner": 2}', b'{"n": NaN}'):
            with self.assertRaises(wm.ManifestError):
                wm.decode(raw)

    def test_update_preserves_unselected_records_and_file_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "WORKSPACE-MANIFEST.json"
            path.write_text('{"mine": {"status": "active"}, "peer": {"unknown": [1, 2]}}')
            path.chmod(0o640)
            before, original = wm.load(path)
            expected = wm.record_digest(original, "mine")
            result = wm.update(path, "mine", {"status": "done"}, expected)
            self.assertTrue(result["changed"])
            self.assertEqual(wm.load(path)[1]["peer"], {"unknown": [1, 2]})
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            same = wm.update(path, "mine", {"status": "done"}, result["recordSha256"])
            self.assertFalse(same["changed"])
            with self.assertRaises(wm.ManifestError):
                wm.update(path, "mine", {}, expected)
            self.assertFalse(list(Path(folder).glob("*.tmp")))

    def test_competing_writers_do_not_lose_an_update(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / "WORKSPACE-MANIFEST.json"
            manifest.write_text('{"one": 0, "two": 0}')
            value = root / "value.json"
            value.write_text("1")
            sha = wm.record_digest(wm.load(manifest)[1], "one")
            commands = [[sys.executable, str(SCRIPT), "--manifest", str(manifest),
                         "put", key, "--value-file", str(value), "--expect", sha]
                        for key in ("one", "one")]
            processes = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for cmd in commands]
            for process in processes:
                process.communicate(timeout=10)
            self.assertEqual(sorted(p.returncode for p in processes), [0, 1])
            self.assertEqual(sum(wm.load(manifest)[1].values()), 1)

    def test_unrelated_updates_do_not_require_rereading_other_work(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "WORKSPACE-MANIFEST.json"
            path.write_text('{"one": 0, "two": 0}')
            _, original = wm.load(path)
            wm.update(path, "one", 1, wm.record_digest(original, "one"))
            wm.update(path, "two", 2, wm.record_digest(original, "two"))
            self.assertEqual(wm.load(path)[1], {"one": 1, "two": 2})
            missing = wm.record_digest(original, "new")
            wm.update(path, "new", None, missing)
            with self.assertRaises(wm.ManifestError):
                wm.update(path, "new", 3, missing)

    def test_json_boolean_does_not_equal_numeric_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "WORKSPACE-MANIFEST.json"
            path.write_text('{"policy": {"enabled": 1}}')
            expected = wm.record_digest(wm.load(path)[1], "policy")
            result = wm.update(path, "policy", {"enabled": True}, expected)
            self.assertTrue(result["changed"])
            self.assertIs(wm.load(path)[1]["policy"]["enabled"], True)

    def test_failed_replace_keeps_original_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "WORKSPACE-MANIFEST.json"
            original = b'{"mine": 0}'
            path.write_bytes(original)
            with patch.object(os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    wm.update(path, "mine", 1, wm.record_digest(wm.decode(original), "mine"))
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(Path(folder).glob("*.tmp")))

    def test_symbolic_link_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "target.json"
            target.write_text('{"mine": 0}')
            link = Path(folder) / "WORKSPACE-MANIFEST.json"
            link.symlink_to(target)
            with self.assertRaises(wm.ManifestError):
                wm.update(link, "mine", 1, wm.record_digest(wm.load(target)[1], "mine"))
            self.assertTrue(link.is_symlink())
            self.assertEqual(wm.load(target)[1], {"mine": 0})


if __name__ == "__main__":
    unittest.main()
