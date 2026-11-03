"""The isolated diagnostic fixture must not poison a larger test process."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


FIXTURE = Path(__file__).with_name("test_uv_diagnostic_forms.py")
PROBE = r"""
import importlib.util, json, subprocess, sys
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('diagnostic_fixture', sys.argv[1])
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
before = (subprocess.Popen, subprocess.run)
mode, fail_at = sys.argv[2], int(sys.argv[3])
calls = 0
def definitions(*args):
    global calls
    calls += 1
    if calls == fail_at:
        raise {'error': RuntimeError, 'exit': SystemExit}[mode]('setup refused')
    return SimpleNamespace()
if mode != 'actual-startup':
    fixture.definitions = definitions
failure, guarded = None, False
try:
    fixture.setUpModule()
except BaseException as exc:
    failure = type(exc).__name__
else:
    try:
        subprocess.run(['must-not-execute'])
    except AssertionError as exc:
        guarded = str(exc) == 'Real process forbidden'
    if hasattr(fixture, 'tearDownModule'):
        fixture.tearDownModule()
        fixture.tearDownModule()
print(json.dumps({'failure': failure, 'guarded': guarded,
                  'restored': (subprocess.Popen, subprocess.run) == before}))
"""


def probe(mode, fail_at=0):
    result = subprocess.run(
        [sys.executable, "-I", "-c", PROBE, str(FIXTURE), mode, str(fail_at)],
        check=True, capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode,expected", [("error", "RuntimeError"), ("exit", "SystemExit")])
@pytest.mark.parametrize("fail_at", [1, 2])
def test_partial_module_setup_restores_process_functions(mode, expected, fail_at):
    assert probe(mode, fail_at) == {
        "failure": expected, "guarded": False, "restored": True,
    }


def test_successful_module_setup_guards_processes_until_teardown():
    assert probe("success") == {"failure": None, "guarded": True, "restored": True}


def test_actual_unsupported_startup_rejects_without_leaking_process_guards():
    # The fixture still rejects ordinary Python: -I alone is not -I -S -B.
    assert probe("actual-startup") == {
        "failure": "SystemExit", "guarded": False, "restored": True,
    }
