"""Source-only qualification of the pinned diagnostic subset and its adapter.

Direct invocation in a clean Python guest:
  <absolute-python> -I -S -B -m unittest discover -s tests \
      -p test_uv_diagnostic_forms.py -v
Environment: PATH=<python-directory>:/usr/bin:/bin LANG=C.UTF-8. Do not add
TMPDIR: the expanded fixture deliberately rejects it.

Ordinary pytest delegates each unchanged unittest case to that clean interpreter.
The parent never loads the guarded fixture or patches its process functions.
Synthetic temporary files use the interpreter's default temporary directory.

Definitions are compiled directly from source, never through a bytecode loader.
The expanded fixture's startup guard is not patched. Its main is exercised only
through a fake failing uv-version command, with all readiness/process boundaries
mocked and synthetic files. No canonical product imports, real subprocesses,
resolver work, worker adoption, or successful full fixture run are covered.

The literals below mirror the four documented complete public text templates at
uv 329bf9448afd9f9088d14a4d57fbc61eb454f117, crates/uv/tests/lock/lock.rs:
4308-4314, 47060-47067, 20840-20849, and 74-91. The workspace leaf name and nested
project name/path are synthetic substitutions. Expected values are independent
of the implementation's regexes/constants. These are reported forms, not a
causal diagnosis of the earlier unavailable 986-byte log.
"""

from contextlib import ExitStack, redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
LIMIT = 64 * 1024
VERSION = "0.13.6"
SECRET = "synthetic-user:synthetic-token@unit.invalid/private SYNTHETIC_SECRET"
MARKERS = ("synthetic-user", "synthetic-token", "unit.invalid", "SYNTHETIC_SECRET")
FORM_KEYS = {"grammarVersion", "uvVersion", "parseHealth", "kind", "form", "flags"}
FLAG_KEYS = {"resolutionProof", "offlineAbsence", "tomlDiagnostic"}

CONFLICT = (
    "error: No solution found when resolving dependencies\n"
    "  cause: Because anyio==3.7.0 depends on idna==3.2 and your project depends on "
    "anyio==3.7.0, we can conclude that your project depends on idna==3.2.\n"
    "         And because your project depends on idna==3.6, we can conclude that "
    "your project's requirements are unsatisfiable.\n"
).encode()
OFFLINE_PROJECT = (
    "error: No solution found when resolving dependencies\n"
    "  cause: Because excluded was not found in the cache and your project depends "
    "on excluded, we can conclude that your project's requirements are unsatisfiable.\n"
    "\n"
    "hint: Packages were unavailable because the network was disabled. When the "
    "network is disabled, registry packages may only be read from the cache.\n"
).encode()
OFFLINE_WORKSPACE = (
    "error: No solution found when resolving dependencies\n"
    "  cause: Because anyio was not found in the cache and leaf depends on anyio>3, "
    "we can conclude that leaf's requirements are unsatisfiable.\n"
    "         And because your workspace requires leaf, we can conclude that "
    "your workspace's requirements are unsatisfiable.\n"
    "\n"
    "hint: Packages were unavailable because the network was disabled. When the "
    "network is disabled, registry packages may only be read from the cache.\n"
).encode()
NESTED_TOML = (
    "warning: Failed to validate existing lockfile\n"
    "  cause: Failed to parse `[TEMP_DIR]/child/pyproject.toml`\n"
    "  cause: TOML parse error at line 3, column 11\n"
    "           |\n"
    "         3 | version = 42\n"
    "           |           ^^\n"
    "         invalid type: integer `42`, expected a string\n"
    "error: Failed to build `child @ file://[TEMP_DIR]/child`\n"
    "  cause: Failed to parse metadata from built wheel\n"
    "  cause: TOML parse error at line 3, column 11\n"
    "           |\n"
    "         3 | version = 42\n"
    "           |           ^^\n"
    "         invalid type: integer `42`, expected a string\n"
).encode()


def definitions(name, relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Do not call spec.loader.exec_module: an existing pyc must not be consulted.
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


_module_guards = None


def setUpModule():
    global forms, expanded, forbidden_run, _module_guards
    if "pytest" in sys.modules:
        # Pytest's site imports/environment cannot satisfy the fixture's guard.
        # Assertions below execute each case in a fresh isolated interpreter.
        return
    guards = ExitStack()
    try:
        for target in ("Popen", "run"):
            guarded = guards.enter_context(patch.object(
                subprocess, target, side_effect=AssertionError("Real process forbidden")))
            if target == "run":
                forbidden_run = guarded
        forms = definitions("qualified_uv_forms", "scripts/uv_diagnostic_forms.py")
        expanded = definitions("qualified_expanded_lock", "tests/fixtures/omitted_ref_expanded_lock.py")
    except BaseException:
        # Pytest does not run unittest's module-cleanup registry after setup fails.
        # In particular, the isolated fixture can reject startup with SystemExit.
        guards.close()
        raise
    _module_guards = guards


def tearDownModule():
    global _module_guards
    guards, _module_guards = _module_guards, None
    if guards is not None:
        guards.close()


def expected(health="recognized", kind="resolution-unsatisfiable"):
    variants = {
        "resolution-unsatisfiable": ("resolver-proof", True, None, None),
        "offline-missing-cache-or-metadata": ("resolver-proof", True, True, None),
        "manifest-or-cli": ("toml-in-build", None, None, True),
        "unknown": ("unknown", None, None, None),
    }
    form, resolution, offline, toml = variants[kind]
    return {
        "grammarVersion": 1,
        "uvVersion": "unsupported" if health == "unsupported-version" else VERSION,
        "parseHealth": health,
        "kind": kind,
        "form": form,
        "flags": {"resolutionProof": resolution, "offlineAbsence": offline, "tomlDiagnostic": toml},
    }


def facts(payload, code=23, complete=True):
    return {"complete": complete, "returncode": code, "rawLogBytes": len(payload),
            "rawLogSha256": hashlib.sha256(payload).hexdigest(), "classifierVersion": 1}


class Assertions(unittest.TestCase):
    def _callSetUp(self):
        if "pytest" not in sys.modules:
            super()._callSetUp()

    def _callTestMethod(self, method):
        if "pytest" not in sys.modules:
            return super()._callTestMethod(method)
        python = str(Path(sys.executable).absolute())
        result = subprocess.run(
            [python, "-I", "-S", "-B", str(Path(__file__).resolve()),
             f"{type(self).__name__}.{self._testMethodName}"],
            env={"PATH": str(Path(python).parent) + ":/usr/bin:/bin", "LANG": "C.UTF-8"},
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ran 1 test", result.stderr)
        self.assertTrue(result.stderr.endswith("\nOK\n"), result.stderr)

    def _callTearDown(self):
        if "pytest" not in sys.modules:
            super()._callTearDown()

    def assert_private(self, value, extra=()):
        rendered = json.dumps(value, sort_keys=True)
        for marker in (*MARKERS, *extra):
            self.assertNotIn(marker, rendered)

    def assert_unknown(self, value, health=None):
        self.assertTrue(forms.validate_form(value))
        self.assertEqual(set(value), FORM_KEYS)
        self.assertEqual(value["kind"], "unknown")
        self.assertEqual(value["form"], "unknown")
        self.assertEqual(value["flags"], dict.fromkeys(FLAG_KEYS))
        if health is not None:
            self.assertEqual(value, expected(health, "unknown"))
        self.assert_private(value)

    def assert_capture_failed(self, observation):
        self.assertEqual(set(observation), {"captureHealth", "form"})
        self.assertEqual(observation["captureHealth"], "failed")
        self.assert_unknown(observation["form"])


class ParseFormTests(Assertions):
    def test_complete_conflict_with_optional_single_final_lf(self):
        for payload in (CONFLICT, CONFLICT[:-1]):
            with self.subTest(final_lf=payload.endswith(b"\n")):
                result = forms.parse_form(payload, 1)
                self.assertEqual(result, expected())
                self.assertTrue(forms.validate_form(result))

    def test_complete_offline_project_keeps_both_reported_flags(self):
        result = forms.parse_form(OFFLINE_PROJECT, 1)
        self.assertEqual(result, expected(kind="offline-missing-cache-or-metadata"))
        self.assertIs(result["flags"]["resolutionProof"], True)
        self.assertIs(result["flags"]["offlineAbsence"], True)
        self.assertNotEqual(result["kind"], "resolution-unsatisfiable")
        self.assertNotIn("dependency-conflict", json.dumps(result))

    def test_complete_offline_workspace_keeps_nested_resolution_context(self):
        self.assertEqual(forms.parse_form(OFFLINE_WORKSPACE, 1),
                         expected(kind="offline-missing-cache-or-metadata"))

    def test_complete_nested_toml_does_not_claim_root_or_cli_cause(self):
        result = forms.parse_form(NESTED_TOML, 1)
        self.assertEqual(result, expected(kind="manifest-or-cli"))
        self.assertEqual(result["form"], "toml-in-build")
        self.assertIsNone(result["flags"]["resolutionProof"])
        self.assert_private(result, ("child", "[TEMP_DIR]", "pyproject.toml"))

    def test_synthetic_parameter_variants_without_copying_parser_patterns(self):
        cases = (
            (CONFLICT.replace(b"anyio", b"outer_pkg").replace(b"idna", b"inner-pkg")
             .replace(b"3.7.0", b"12.4").replace(b"3.2", b"1.0").replace(b"3.6", b"2.0"),
             "resolution-unsatisfiable"),
            (OFFLINE_PROJECT.replace(b"excluded", b"missing_pkg2"), "offline-missing-cache-or-metadata"),
            (OFFLINE_WORKSPACE.replace(b"anyio", b"missing-pkg").replace(b"leaf", b"workspace_leaf")
             .replace(b">3", b">12.4"), "offline-missing-cache-or-metadata"),
            (NESTED_TOML.replace(b"[TEMP_DIR]", b"/artifacts/project-a")
             .replace(b"child", b"nested_pkg"), "manifest-or-cli"),
        )
        for payload, kind in cases:
            with self.subTest(kind=kind):
                self.assertEqual(forms.parse_form(payload, 23), expected(kind=kind))

    def test_inconsistent_names_versions_and_equivalent_versions_reject(self):
        cases = (
            CONFLICT.replace(b"depends on anyio==3.7.0", b"depends on other==3.7.0"),
            CONFLICT.replace(b"depends on anyio==3.7.0", b"depends on anyio==3.8.0"),
            CONFLICT.replace(b"depends on idna==3.2.", b"depends on other==3.2."),
            CONFLICT.replace(b"anyio", b"idna"),
            CONFLICT.replace(b"anyio", b"same_pkg").replace(b"idna", b"same-pkg"),
            CONFLICT.replace(b"idna==3.6", b"idna==3.2.0"),
            CONFLICT.replace(b"idna==3.6", b"idna==03.2.0.0"),
            OFFLINE_PROJECT.replace(b"depends on excluded", b"depends on different"),
            OFFLINE_WORKSPACE.replace(b"requires leaf", b"requires other"),
            NESTED_TOML.replace(b"child @", b"other @"),
            NESTED_TOML.replace(b"file://[TEMP_DIR]/child", b"file://[TEMP_DIR]/other"),
        )
        for index, payload in enumerate(cases):
            with self.subTest(case=index):
                self.assert_unknown(forms.parse_form(payload, 1), "unsupported")

    def test_missing_altered_or_unrelated_hint_is_not_offline_proof(self):
        for body in (OFFLINE_PROJECT, OFFLINE_WORKSPACE):
            for payload in (body.split(b"\n\nhint:")[0] + b"\n",
                            body.replace(b"network was disabled", b"network was enabled"),
                            body.replace(b"hint:", b"note:"),
                            CONFLICT + body[body.index(b"\nhint:"):]):
                with self.subTest(payload_size=len(payload)):
                    self.assert_unknown(forms.parse_form(payload, 1), "unsupported")

    def test_extra_causes_multiple_diagnostics_and_cli_text_remain_unknown(self):
        cases = (
            CONFLICT + b"  cause: extra unrecognized cause\n",
            b"warning: unrelated\n" + CONFLICT,
            CONFLICT + OFFLINE_PROJECT,
            OFFLINE_WORKSPACE + NESTED_TOML,
            CONFLICT + CONFLICT,
            CONFLICT + b"\n",
            b"error: unexpected argument '--private' found\n",
            b"error: Failed to parse pyproject.toml\n",
            b"error: No solution found when resolving dependencies\n",
            b"", b"ordinary unrelated output\n",
        )
        for index, payload in enumerate(cases):
            with self.subTest(case=index):
                self.assert_unknown(forms.parse_form(payload, 1), "unsupported")

    def test_partial_or_altered_toml_body_is_not_complete_nested_form(self):
        for payload in (
            NESTED_TOML[:NESTED_TOML.index(b"error: Failed to build")],
            NESTED_TOML[NESTED_TOML.index(b"error: Failed to build"):],
            NESTED_TOML.replace(b"line 3, column 11", b"line 4, column 11", 1),
            NESTED_TOML.replace(b"version = 42", b"version = 43", 1),
            NESTED_TOML.replace(b"           ^^", b"           ^", 1),
            NESTED_TOML.replace(b"metadata from built wheel", b"root manifest"),
            NESTED_TOML.replace(b"[TEMP_DIR]", b"/artifacts/../private"),
        ):
            with self.subTest(payload_size=len(payload)):
                self.assert_unknown(forms.parse_form(payload, 1), "unsupported")

    def test_ansi_cr_nul_and_unicode_controls_reject_before_matching(self):
        for control in (b"\x1b[31m", b"\r", b"\x00", b"\t", b"\x7f",
                        "\u202e".encode(), "\u200b".encode()):
            for payload in (control + CONFLICT, CONFLICT + control):
                with self.subTest(control=control):
                    self.assert_unknown(forms.parse_form(payload, 1), "control-character")
        self.assert_unknown(forms.parse_form(CONFLICT.replace(b"\n", b"\r\n"), 1), "control-character")

    def test_invalid_utf8_never_recognizes_valid_prefix(self):
        for payload in (CONFLICT + b"\xff", b"\xc3" + OFFLINE_PROJECT, b"\xed\xa0\x80"):
            self.assert_unknown(forms.parse_form(payload, 1), "invalid-utf8")

    def test_incomplete_capture_requires_singleton_true(self):
        for complete in (False, None, 1, "true", [], {}):
            with self.subTest(complete=complete):
                self.assert_unknown(forms.parse_form(CONFLICT, 23, complete=complete), "incomplete")

    def test_oversize_boundary_never_accepts_recognizable_prefix(self):
        for size, health in ((LIMIT, "unsupported"), (LIMIT + 1, "oversize"), (2 * LIMIT, "oversize")):
            payload = CONFLICT + b"x" * (size - len(CONFLICT))
            self.assert_unknown(forms.parse_form(payload, 1), health)

    def test_exit_codes_are_exact_positive_bounded_integers(self):
        for code in (True, False, 0, -1, -9, None, "1", 1.0, 256, [], {}):
            with self.subTest(code=code):
                self.assert_unknown(forms.parse_form(CONFLICT, code), "invalid-exit")
        for code in (1, 23, 255):
            self.assertEqual(forms.parse_form(CONFLICT, code), expected())

    def test_version_is_exact_pinned_string_and_never_echoed(self):
        for version in ("0.13.5", "0.13.6+local", "uv 0.13.6", SECRET, None, True, 0.136, [], {}):
            with self.subTest(version_type=type(version).__name__):
                self.assert_unknown(forms.parse_form(CONFLICT, 1, uv_version=version), "unsupported-version")

    def test_payload_types_are_exact_bytes(self):
        class BytesSubclass(bytes):
            pass
        for data in (CONFLICT.decode(), bytearray(CONFLICT), memoryview(CONFLICT), None, 1, {}, [],
                     BytesSubclass(CONFLICT)):
            with self.subTest(data_type=type(data).__name__):
                self.assert_unknown(forms.parse_form(data, 1), "unsupported")

    def test_sensitive_literals_and_recognized_tokens_never_leave_parser(self):
        cases = (
            SECRET.encode(), CONFLICT + SECRET.encode(),
            CONFLICT.replace(b"anyio", b"sensitive_outer").replace(b"idna", b"sensitive_inner"),
            NESTED_TOML.replace(b"[TEMP_DIR]", b"/artifacts/sensitive-path"),
            OFFLINE_PROJECT.replace(b"excluded", b"sensitive_package"),
        )
        for payload in cases:
            result = forms.parse_form(payload, 23)
            self.assertTrue(forms.validate_form(result))
            self.assert_private(result, ("sensitive_outer", "sensitive_inner", "sensitive-path",
                                         "sensitive_package", "anyio", "idna", "3.7.0", "file://"))


class ValidateFormTests(Assertions):
    def test_all_supported_and_unknown_states_validate_and_roundtrip(self):
        values = [expected(kind=kind) for kind in (
            "resolution-unsatisfiable", "offline-missing-cache-or-metadata", "manifest-or-cli")]
        values.extend(expected(health, "unknown") for health in (
            "unsupported", "incomplete", "oversize", "invalid-utf8", "invalid-exit",
            "unsupported-version", "control-character"))
        for value in values:
            self.assertIs(forms.validate_form(value), True)
            self.assertIs(forms.validate_form(json.loads(json.dumps(value))), True)

    def test_exact_keys_required_at_record_and_flags_levels(self):
        for parent_path in ((), ("flags",)):
            baseline = expected()
            node = baseline if not parent_path else baseline["flags"]
            for key in tuple(node):
                value = deepcopy(baseline)
                target = value if not parent_path else value["flags"]
                del target[key]
                self.assertIs(forms.validate_form(value), False)
            for key in ("rawStderr", SECRET, 1, None):
                value = deepcopy(baseline)
                target = value if not parent_path else value["flags"]
                target[key] = SECRET
                self.assertIs(forms.validate_form(value), False)

    def test_exact_types_reject_bool_integer_aliases_and_subclasses(self):
        class DictSubclass(dict):
            pass
        class StringSubclass(str):
            pass
        class IntSubclass(int):
            pass
        for value in (None, [], (), SECRET, DictSubclass(expected())):
            self.assertIs(forms.validate_form(value), False)
        replacements = {
            "grammarVersion": (True, 1.0, "1", IntSubclass(1), None),
            "uvVersion": (None, [VERSION], StringSubclass(VERSION)),
            "parseHealth": (None, ["recognized"], StringSubclass("recognized")),
            "kind": (None, {}, StringSubclass("resolution-unsatisfiable")),
            "form": (None, {}, StringSubclass("resolver-proof")),
            "flags": (None, [], DictSubclass(expected()["flags"])),
        }
        for key, values in replacements.items():
            for replacement in values:
                value = expected()
                value[key] = replacement
                self.assertIs(forms.validate_form(value), False)
        for key in FLAG_KEYS:
            for replacement in (0, 1, "true", [], {}, 1.0):
                value = expected()
                value["flags"][key] = replacement
                self.assertIs(forms.validate_form(value), False)
        for nested in (False, True):
            value = expected()
            target = value["flags"] if nested else value
            key = next(iter(target))
            original = target.pop(key)
            target[StringSubclass(key)] = original
            self.assertIs(forms.validate_form(value), False)

    def test_cross_field_states_cannot_invent_a_diagnosis(self):
        mutations = (
            ("parseHealth", "unsupported"), ("uvVersion", "unsupported"),
            ("kind", "other"), ("kind", "unknown"), ("kind", "manifest-or-cli"),
            ("form", "unknown"), ("form", "toml-in-build"),
        )
        for key, replacement in mutations:
            value = expected()
            value[key] = replacement
            self.assertIs(forms.validate_form(value), False)
        for key in FLAG_KEYS:
            value = expected("unsupported", "unknown")
            value["flags"][key] = True
            self.assertIs(forms.validate_form(value), False)
        for kind in ("resolution-unsatisfiable", "offline-missing-cache-or-metadata", "manifest-or-cli"):
            for key in FLAG_KEYS:
                value = expected(kind=kind)
                value["flags"][key] = False
                self.assertIs(forms.validate_form(value), False)
        value = expected("unsupported-version", "unknown")
        value["uvVersion"] = VERSION
        self.assertIs(forms.validate_form(value), False)

    def test_malicious_values_at_every_leaf_and_cycles_are_rejected_quietly(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            for key in FORM_KEYS - {"flags"}:
                for replacement in (SECRET, {"raw": SECRET}, [SECRET]):
                    value = expected()
                    value[key] = replacement
                    self.assertIs(forms.validate_form(value), False)
            for key in FLAG_KEYS:
                value = expected()
                value["flags"][key] = {"raw": SECRET}
                self.assertIs(forms.validate_form(value), False)
            value = expected()
            value["flags"] = value
            self.assertIs(forms.validate_form(value), False)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")


class ExpandedAdapterTests(Assertions):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="uv-forms-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.log = self.base / "stderr.private"
        self.log.write_bytes(CONFLICT)

    def observe(self, supplied=None, parser=None):
        return expanded.observe_form(self.log, facts(CONFLICT) if supplied is None else supplied,
                                     23, forms if parser is None else parser, uv_version=VERSION)

    def test_capture_complete_binds_exact_bytes_without_mutating_primary_facts(self):
        supplied = facts(CONFLICT)
        before = deepcopy(supplied)
        result = self.observe(supplied)
        self.assertEqual(result, {"captureHealth": "complete", "form": expected()})
        self.assertEqual(supplied, before)
        self.assertEqual(supplied["returncode"], 23)
        self.assert_private(result, ("anyio", "idna", str(self.base)))

    def test_capture_changed_hash_size_and_incomplete_are_failed_unknown(self):
        changes = (
            {"rawLogSha256": "0" * 64}, {"rawLogBytes": len(CONFLICT) + 1},
            {"complete": False}, {"complete": 1},
        )
        for change in changes:
            self.assert_capture_failed(self.observe({**facts(CONFLICT), **change}))
        # Same-size modification defeats a length-only observation.
        self.log.write_bytes(CONFLICT.replace(b"idna", b"odna"))
        self.assert_capture_failed(self.observe())

    def test_capture_read_errors_and_malformed_facts_do_not_export_private_data(self):
        class StringSubclass(str):
            pass
        class IntSubclass(int):
            pass
        with patch.object(Path, "open", side_effect=OSError(SECRET)):
            self.assert_capture_failed(self.observe())
        for supplied in ({}, [], SECRET, {**facts(CONFLICT), "rawLogBytes": SECRET},
                         {**facts(CONFLICT), "rawLogBytes": float(len(CONFLICT))},
                         {**facts(CONFLICT), "rawLogBytes": True},
                         {**facts(CONFLICT), "rawLogBytes": IntSubclass(len(CONFLICT))},
                         {**facts(CONFLICT), "rawLogSha256": StringSubclass(facts(CONFLICT)["rawLogSha256"])},
                         {**facts(CONFLICT), "rawLogSha256": {"secret": SECRET}}):
            with self.subTest(facts_type=type(supplied).__name__):
                self.assert_capture_failed(self.observe(supplied))
        result = self.observe({**facts(CONFLICT), "unusedPrivateField": SECRET})
        self.assert_private(result)
        self.assertTrue(forms.validate_form(result["form"]))

    def test_capture_parser_failure_and_invalid_parser_result_are_failed_unknown(self):
        def broken_complete(data, code, *, complete=True, uv_version=VERSION):
            if complete:
                raise RuntimeError(SECRET)
            return forms.parse_form(data, code, complete=complete, uv_version=uv_version)
        invalid = expected()
        invalid["rawStderr"] = SECRET
        for parser in (
            SimpleNamespace(parse_form=broken_complete, validate_form=forms.validate_form),
            SimpleNamespace(parse_form=Mock(side_effect=RuntimeError(SECRET)), validate_form=forms.validate_form),
            SimpleNamespace(parse_form=lambda data, code, **kw: (
                invalid if kw.get("complete", True) else expected("incomplete", "unknown")),
                validate_form=forms.validate_form),
        ):
            supplied = facts(CONFLICT)
            with self.subTest(parser=type(parser.parse_form).__name__):
                self.assert_capture_failed(self.observe(supplied, parser))
                self.assertEqual(supplied["returncode"], 23)

    def report(self):
        return {"schema": 1, "status": "failed", "commands": [
            {"returncode": 23, "diagnostic": {"captureHealth": "complete", "form": expected()}}]}

    def test_durable_report_orders_fsync_replace_fsync_and_saves_only_write_intent(self):
        output = self.base / "receipt"
        output.mkdir()
        report = self.report()
        before = deepcopy(report)
        events = []
        real_replace, real_open = Path.replace, os.open
        def replace(path, target):
            events.append("replace")
            return real_replace(path, target)
        def directory_open(path, flags):
            events.append("directory-open")
            return real_open(path, flags)
        with patch.object(expanded.os, "fsync", side_effect=lambda fd: events.append("fsync")), \
             patch.object(Path, "replace", autospec=True, side_effect=replace), \
             patch.object(expanded.os, "open", side_effect=directory_open):
            self.assertIs(expanded.durable_report(output, report, forms), True)
        self.assertEqual(events, ["fsync", "replace", "directory-open", "fsync"])
        saved = json.loads((output / "result.json").read_bytes())
        self.assertEqual(saved, {**before, "receiptHealth": "write-intent"})
        self.assertEqual(report, before)
        self.assertFalse((output / "result.json.pending").exists())
        self.assertNotIn("durable", json.dumps(saved))

    def test_durable_report_io_failures_are_false_without_exception_text(self):
        for failure in ("open", "short-write", "flush", "file-fsync", "replace", "dir-open", "dir-fsync"):
            with self.subTest(failure=failure):
                output = self.base / failure
                output.mkdir()
                out, err = io.StringIO(), io.StringIO()
                with ExitStack() as stack:
                    if failure == "open":
                        stack.enter_context(patch.object(Path, "open", side_effect=OSError(SECRET)))
                    elif failure in ("short-write", "flush"):
                        stream = Mock()
                        stream.__enter__ = Mock(return_value=stream)
                        stream.__exit__ = Mock(return_value=False)
                        stream.write.side_effect = lambda payload: 0 if failure == "short-write" else len(payload)
                        stream.flush.side_effect = OSError(SECRET)
                        stack.enter_context(patch.object(Path, "open", return_value=stream))
                    elif failure in ("file-fsync", "dir-fsync"):
                        effects = [OSError(SECRET)] if failure == "file-fsync" else [None, OSError(SECRET)]
                        stack.enter_context(patch.object(expanded.os, "fsync", side_effect=effects))
                    elif failure == "replace":
                        stack.enter_context(patch.object(Path, "replace", side_effect=OSError(SECRET)))
                    else:
                        stack.enter_context(patch.object(expanded.os, "open", side_effect=OSError(SECRET)))
                    with redirect_stdout(out), redirect_stderr(err):
                        self.assertIs(expanded.durable_report(output, self.report(), forms), False)
                self.assertEqual(out.getvalue(), "")
                self.assertEqual(err.getvalue(), "")
                # Rename may already have landed when directory fsync fails.
                if (output / "result.json").exists():
                    saved = json.loads((output / "result.json").read_bytes())
                    self.assertEqual(saved["receiptHealth"], "write-intent")
                    self.assert_private(saved)
                    self.assertEqual(saved["status"], "failed")

    def test_durable_report_rejects_malicious_diagnostic_schema_before_writing(self):
        class StringSubclass(str):
            pass
        output = self.base / "schema"
        output.mkdir()
        observations = (
            SECRET, [], {"captureHealth": "complete", "form": expected(), "rawStderr": SECRET},
            {"captureHealth": SECRET, "form": expected()},
            {"captureHealth": StringSubclass("complete"), "form": expected()},
            {"captureHealth": StringSubclass("failed"), "form": expected("incomplete", "unknown")},
            *({"captureHealth": "failed", "form": expected(kind=kind)} for kind in (
                "resolution-unsatisfiable", "offline-missing-cache-or-metadata", "manifest-or-cli")),
            {"captureHealth": "complete", "form": {**expected(), "raw": SECRET}},
            {"captureHealth": "complete", "form": {**expected(), "flags": {"raw": SECRET}}},
        )
        for observation in observations:
            report = self.report()
            report["commands"][0]["diagnostic"] = observation
            self.assertIs(expanded.durable_report(output, report, forms), False)
            self.assertFalse((output / "result.json").exists())
        invalid_validator = SimpleNamespace(validate_form=Mock(side_effect=RuntimeError(SECRET)))
        self.assertIs(expanded.durable_report(output, self.report(), invalid_validator), False)
        report = self.report()
        report["not-json"] = object()
        self.assertIs(expanded.durable_report(output, report, forms), False)
        self.assertFalse((output / "result.json").exists())
        # A failed capture with no diagnosis is valid evidence, not a pass.
        report = self.report()
        report["commands"][0]["diagnostic"] = {
            "captureHealth": "failed", "form": expected("incomplete", "unknown")}
        self.assertIs(expanded.durable_report(output, report, forms), True)
        saved = json.loads((output / "result.json").read_bytes())
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["receiptHealth"], "write-intent")

    def test_postreplace_directory_fsync_failure_leaves_passed_intent_not_durability(self):
        output = self.base / "passed-intent"
        output.mkdir()
        report = self.report()
        report["status"] = "passed"
        report["commands"][0]["returncode"] = 0
        report["commands"][0]["diagnostic"]["form"] = expected("invalid-exit", "unknown")
        before = deepcopy(report)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(expanded.os, "fsync", side_effect=[None, OSError(SECRET)]) as fsync, \
             redirect_stdout(out), redirect_stderr(err):
            result = expanded.durable_report(output, report, forms)
        self.assertIs(result, False)
        self.assertEqual(fsync.call_count, 2)
        self.assertFalse((output / "result.json.pending").exists())
        saved = json.loads((output / "result.json").read_bytes())
        self.assertEqual(saved, {**before, "receiptHealth": "write-intent"})
        self.assertEqual(report, before)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")
        self.assert_private(saved)
        # The rename landed, but persistence returned False. Stored "passed"
        # alone cannot establish durability or authorize process acceptance.
        # This tests the writer boundary, not an independently executed consumer.

    def run_mocked_main(self, *, observe_failure=False, persist_failure=False,
                        final_dir_fsync_failure=False, primary_code=23):
        """Run the real adapter until its first fake command or capture failure."""
        checkout = self.base / "synthetic-checkout"
        checkout.mkdir()
        contents = b"synthetic placeholder; never imported or executed\n"
        digest = hashlib.sha256(contents).hexdigest()
        relative_paths = (
            "tests/fixtures/omitted_ref_acquisition_experiment.py",
            "tests/fixtures/omitted_ref_worker_probe.py", "scripts/qualification_diagnostics.py",
            "scripts/uv_diagnostic_forms.py", "amplifier_web/runtime_deps/pyproject.toml",
            "uv.lock", "tests/fixtures/omitted_ref_expanded_lock.py",
        )
        for relative in relative_paths:
            path = checkout / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        supplied_manifest, supplied_lock, uv = (self.base / name for name in ("supplied.toml", "supplied.lock", "fake-uv"))
        for path in (supplied_manifest, supplied_lock, uv):
            path.write_bytes(contents)
        cache = self.base / "cache"
        cache.mkdir()
        (cache / "sentinel").write_bytes(b"synthetic cache sentinel")
        args = SimpleNamespace(root=self.base / "work", output=self.base / "output",
                               runtime_manifest=supplied_manifest, runtime_lock=supplied_lock,
                               uv_cache=cache, uv=uv, parent_netns="net:[1]", parent_pidns="pid:[1]")
        def stream_facts(chunks, code, *, complete):
            return facts(b"".join(chunks), code, complete)
        def isolate(root, git, uv_cache):
            os.environ["UV_CACHE_DIR"] = str(uv_cache)
            os.environ["GIT_ALLOW_PROTOCOL"] = "file"
            os.environ.pop("UV_OFFLINE", None)
        fixture = SimpleNamespace(
            diagnostics=SimpleNamespace(stream_facts=Mock(side_effect=stream_facts)),
            require_network_sandbox=Mock(), isolated_environment=Mock(side_effect=isolate))
        experiment = SimpleNamespace(
            FIXTURE_SHA256=digest, HELPER_SHA256=digest,
            load_fixture=Mock(return_value=fixture), wait_group_absent=Mock(return_value=True),
            stop_owned_group=Mock(side_effect=AssertionError("Unexpected process cleanup")))
        process = SimpleNamespace(returncode=primary_code, wait=Mock(return_value=primary_code), pid=999999)
        def fake_popen(argv, **kwargs):
            self.assertEqual(argv, [str(uv), "--version"])
            kwargs["stdout"].write(b"uv 0.13.6\n")
            kwargs["stderr"].write(("unrecognized failure " + SECRET + "\n").encode())
            return process
        proc_status = "NoNewPrivs:\t1\n" + "".join(
            key + ":\t0000000000000000\n" for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))
        real_read_text = Path.read_text
        def read_text(path, *positional, **kwargs):
            if path == Path("/proc/self/status"):
                return proc_status
            return real_read_text(path, *positional, **kwargs)
        snapshots = []
        real_durable = expanded.durable_report
        def persist(output, report, parser):
            snapshots.append(deepcopy(report))
            if persist_failure and any(row["returncode"] == primary_code for row in report["commands"]):
                return False
            if final_dir_fsync_failure and report["finalized"]:
                with patch.object(expanded.os, "fsync", side_effect=[None, OSError(SECRET)]) as fsync:
                    result = real_durable(output, report, parser)
                self.assertIs(result, False)
                self.assertEqual(fsync.call_count, 2)
                return result
            return real_durable(output, report, parser)
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, dict(os.environ), clear=True))
            replacements = {
                "CHECKOUT": checkout, "__file__": str(checkout / relative_paths[-1]),
                "EXPERIMENT_SHA256": digest, "CANONICAL_SHA256": digest,
                "REPOSITORY_LOCK_SHA256": digest,
                "load_experiment": Mock(return_value=(experiment, checkout / relative_paths[0])),
                "load_forms": Mock(return_value=(forms, checkout / "scripts/uv_diagnostic_forms.py", digest)),
                "durable_report": persist,
            }
            for name, value in replacements.items():
                stack.enter_context(patch.object(expanded, name, value))
            stack.enter_context(patch.object(expanded.QuietParser, "parse_args", return_value=args))
            stack.enter_context(patch.object(expanded.sys, "platform", "linux"))
            stack.enter_context(patch.object(expanded.sys, "version_info", (3, 13, 0)))
            for name in ("getuid", "geteuid", "getgid", "getegid"):
                stack.enter_context(patch.object(expanded.os, name, return_value=1000))
            stack.enter_context(patch.object(expanded.os, "getgroups", return_value=[1000]))
            stack.enter_context(patch.object(expanded.os, "access", return_value=True))
            stack.enter_context(patch.object(expanded.os, "readlink", return_value="pid:[2]"))
            stack.enter_context(patch.object(Path, "read_text", autospec=True, side_effect=read_text))
            stack.enter_context(patch.object(expanded.shutil, "which", return_value="/synthetic/git"))
            stack.enter_context(patch.object(expanded.signal, "getsignal", return_value=None))
            stack.enter_context(patch.object(expanded.signal, "signal"))
            popen = stack.enter_context(patch.object(expanded.subprocess, "Popen", side_effect=fake_popen))
            if observe_failure:
                stack.enter_context(patch.object(expanded, "observe_form", side_effect=RuntimeError(SECRET)))
            with redirect_stdout(out), redirect_stderr(err):
                result = expanded.main()
        popen.assert_called_once()
        process.wait.assert_called_once_with(timeout=90)
        experiment.wait_group_absent.assert_called_once_with(process, 2)
        experiment.stop_owned_group.assert_not_called()
        experiment.load_fixture.assert_called_once()
        fixture.require_network_sandbox.assert_called_once_with("net:[1]")
        fixture.isolated_environment.assert_called_once()
        forbidden_run.assert_not_called()
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(out.getvalue()), {
            "status": "failed", "phase": "readiness",
            "receiptHealth": "failed" if persist_failure or final_dir_fsync_failure else "durable"})
        self.assertEqual(err.getvalue(), "")
        final = snapshots[-1]
        self.assertEqual(final["status"], "failed")
        self.assertTrue(final["finalized"])
        self.assertEqual(final["commands"][0]["returncode"], primary_code)
        self.assertEqual(final["commands"][0]["status"], "failed")
        self.assertEqual(final["commands"][0]["cleanup"], "confirmed-absent")
        self.assertEqual(final["immutableInputs"], "unchanged")
        self.assertEqual(final["phases"]["finalization"], "passed")
        self.assertEqual(final["failure"]["phase"], "readiness")
        self.assertTrue(all(row["status"] == "unrun" for row in final["commands"][1:]))
        self.assertEqual(final["workerStarts"], 0)
        self.assertEqual(final["probeStarts"], 0)
        self.assertEqual(final["modelCalls"], 0)
        self.assertEqual(final["canonicalInstalls"], 0)
        self.assert_private(final, (str(self.base), "unrecognized failure"))
        return final, args.output

    def test_real_main_fake_exit_23_remains_primary_failure(self):
        report, output = self.run_mocked_main()
        self.assertEqual(report["commands"][0]["diagnostic"]["captureHealth"], "complete")
        self.assert_unknown(report["commands"][0]["diagnostic"]["form"], "unsupported-version")
        saved = json.loads((output / "result.json").read_bytes())
        self.assertEqual(saved["commands"][0]["returncode"], 23)
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["receiptHealth"], "write-intent")

    def test_real_main_observer_exception_does_not_replace_exit_23_or_pass(self):
        report, output = self.run_mocked_main(observe_failure=True)
        self.assertEqual(report["commands"][0]["capture"], "failed")
        saved = json.loads((output / "result.json").read_bytes())
        self.assertEqual(saved["commands"][0]["returncode"], 23)
        self.assertEqual(saved["status"], "failed")
        self.assert_private(saved)

    def test_real_main_exit_zero_with_capture_failure_is_not_fixture_success(self):
        report, output = self.run_mocked_main(primary_code=0, observe_failure=True)
        self.assertEqual(report["commands"][0]["returncode"], 0)
        self.assertEqual(report["commands"][0]["capture"], "failed")
        self.assertTrue(all(row["status"] == "unrun" for row in report["commands"][1:]))
        saved = json.loads((output / "result.json").read_bytes())
        self.assertEqual(saved["commands"][0]["returncode"], 0)
        self.assertEqual(saved["commands"][0]["capture"], "failed")
        self.assertEqual(saved["status"], "failed")
        self.assertTrue(all(row["status"] == "unrun" for row in saved["commands"][1:]))
        self.assert_private(saved)

    def test_real_main_receipt_failure_after_exit_23_never_becomes_pass(self):
        report, output = self.run_mocked_main(observe_failure=True, persist_failure=True)
        self.assertEqual(report["commands"][0]["capture"], "failed")
        self.assertEqual(report["receiptHealth"], "failed")
        self.assertTrue(report["evidenceWriteFailure"])
        # Earlier intent can exist on disk; it is not a completed failure receipt.
        saved = json.loads((output / "result.json").read_bytes())
        self.assertNotEqual(saved["status"], "passed")
        self.assertEqual(saved["receiptHealth"], "write-intent")
        self.assertIsNone(saved["commands"][0]["returncode"])
        self.assert_private(saved)

    def test_real_main_final_directory_fsync_failure_keeps_exit_23_and_returns_one(self):
        report, output = self.run_mocked_main(final_dir_fsync_failure=True)
        self.assertEqual(report["commands"][0]["returncode"], 23)
        saved = json.loads((output / "result.json").read_bytes())
        self.assertEqual(saved["commands"][0]["returncode"], 23)
        self.assertEqual(saved["status"], "failed")
        self.assertTrue(saved["finalized"])
        self.assertEqual(saved["receiptHealth"], "write-intent")
        self.assert_private(saved)


if __name__ == "__main__":
    unittest.main()
