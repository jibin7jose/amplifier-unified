"""The release guard must reject green-looking but incomplete native proof."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

spec = importlib.util.spec_from_file_location(
    "goal_peer_junit", Path(__file__).parents[1] / "scripts/check_goal_peer_junit.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def report():
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests="43", failures="0", errors="0", skipped="0")
    for module, names in checker.EXPECTED.items():
        for name in sorted(names):
            ET.SubElement(suite, "testcase", classname=module, name=name)
    return root, suite


def test_exact_native_cases_pass(tmp_path):
    root, _ = report()
    path = tmp_path / "results.xml"
    ET.ElementTree(root).write(path)
    assert [len(names) for names in checker.EXPECTED.values()] == [35, 8]
    assert checker.check(path) == 43


def test_release_workflow_requires_and_retains_native_evidence():
    import yaml
    root = Path(__file__).parents[1]
    steps = yaml.safe_load((root / ".github/workflows/release.yml").read_text())["jobs"]["prepare"]["steps"]
    gate = next(step for step in steps if step.get("name") == "Require native goal and peer continuation regressions without skips")
    assert 'tests/test_goal_peer_continuation.py tests/test_task_goal_runtime.py' in gate["run"]
    assert 'scripts/check_goal_peer_junit.py "$RUNNER_TEMP/goal-peer/results.xml"' in gate["run"]
    assert gate["env"]["UNIFIED_RUNTIME_PYTHON"].endswith("/surface-runtime/.venv/bin/python")
    assert gate["env"]["GOAL_PEER_EVIDENCE"].endswith("/goal-peer/traces")
    upload = next(step for step in steps if step.get("with", {}).get("name") == "goal-peer-regression")
    assert upload["if"] == "always()" and upload["with"]["if-no-files-found"] == "error"


@pytest.mark.parametrize("mutation", [
    "missing", "duplicate", "renamed", "failure", "error", "skipped",
    "suite-skip", "import-skip", "empty",
])
def test_incomplete_or_unsuccessful_native_evidence_is_rejected(tmp_path, mutation):
    root, suite = report()
    first = suite[0]
    if mutation == "missing":
        suite.remove(first)
    elif mutation == "duplicate":
        ET.SubElement(suite, "testcase", **first.attrib)
    elif mutation == "renamed":
        first.set("name", "unrelated-green-test")
    elif mutation in {"failure", "error", "skipped"}:
        ET.SubElement(first, mutation)
    elif mutation == "suite-skip":
        suite.set("skipped", "1")
    else:
        suite.clear()
        if mutation == "import-skip":
            ET.SubElement(ET.SubElement(suite, "testcase", name="tests.test_goal_peer_continuation"), "skipped")
    path = tmp_path / "results.xml"
    ET.ElementTree(root).write(path)
    with pytest.raises(ValueError):
        checker.check(path)