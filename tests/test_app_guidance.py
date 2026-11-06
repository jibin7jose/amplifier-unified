"""Verify real provider requests, without network calls to a model service."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


def test_collaboration_guidance_does_not_require_an_optional_skill():
    from amplifier_web.app_guidance import CANVAS_GUIDANCE

    assert 'search for coordinate-work and load it when found' in CANVAS_GUIDANCE
    assert 'A missing skill is not a coordination failure' in CANVAS_GUIDANCE
    assert 'Preserve selected sources and exclusions' in CANVAS_GUIDANCE
    assert 'discover coordination.* and load the coordinate-work skill' not in CANVAS_GUIDANCE


def test_surface_runtime_import_does_not_require_host_css_parser():
    result = subprocess.run([sys.executable, '-c', """
import importlib.abc
import sys

class RuntimeWithoutCssParser(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tinycss2' or fullname.startswith('tinycss2.'):
            raise ModuleNotFoundError('CSS parser is only installed in the web host')

sys.meta_path.insert(0, RuntimeWithoutCssParser())
from amplifier_web.surface_delivery import SurfaceProvider
"""], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_core_receives_canvas_tool_and_ephemeral_instructions():
    python = os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON for the real Core canvas probe')
    result = subprocess.run([python, str(Path(__file__).parent/'fixtures/canvas_runtime_probe.py')],capture_output=True,text=True,timeout=45)
    assert result.returncode == 0, result.stdout+result.stderr
    assert '"canvas_guidance_in_provider_request": true' in result.stdout
    assert '"instructions_ephemeral": true' in result.stdout


def test_real_core_receives_fresh_notice_then_typed_surface_image():
    python = os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON for the real Core surface probe')
    result = subprocess.run([python, str(Path(__file__).parent/'fixtures/surface_context_probe.py')], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    assert '"typed_image_before_answer": true' in result.stdout


@pytest.mark.parametrize('mode', ['persist', 'tail'])
def test_real_core_refreshes_workspace_awareness_without_history_accumulation(mode):
    python = os.environ.get('UNIFIED_RUNTIME_PYTHON')
    if not python:
        pytest.skip('Set UNIFIED_RUNTIME_PYTHON for the real Core awareness probe')
    result = subprocess.run([python, str(Path(__file__).parent/'fixtures/workspace_awareness_runtime_probe.py'), mode], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"fresh_each_request": true' in result.stdout
    assert '"history_clean": true' in result.stdout


def test_recurring_guidance_is_orientation_and_manual_stays_discoverable():
    from amplifier_web.app_guidance import CANVAS_GUIDANCE
    from amplifier_web.builtin_behaviors import resource_root

    # A regression budget, not runtime truncation of user-authored instructions.
    assert len(CANVAS_GUIDANCE.encode('utf-8')) < 9000
    skill = resource_root() / 'skills/unified-app'
    assert (skill / 'SKILL.md').is_file()
    for marker, reference in (
        ('window.BABYLON', 'canvas.md'),
        ('smartTools.open', 'smart-tools.md'),
        ('coordination.artifact.save', 'collaboration.md'),
        ('outputs.attachMedia', 'outputs.md'),
    ):
        assert marker not in CANVAS_GUIDANCE
        assert marker in (skill / reference).read_text()
    assert 'unknown' in CANVAS_GUIDANCE and 'Stop' in CANVAS_GUIDANCE
    assert 'A missing skill is not a coordination failure' in CANVAS_GUIDANCE
