"""Bootstrap path helpers must not load the native runtime dependency graph."""
from pathlib import Path
import subprocess
import sys


def test_shared_state_helpers_import_without_foundation(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = f"""
import builtins
import os
from pathlib import Path
import sys
sys.path.insert(0, {str(root)!r})
original_import = builtins.__import__
def without_foundation(name, *args, **kwargs):
    if name == 'amplifier_foundation' or name.startswith('amplifier_foundation.'):
        raise AssertionError('Bootstrap helpers imported the native runtime')
    return original_import(name, *args, **kwargs)
builtins.__import__ = without_foundation
from amplifier_web.shared_state import shared_state_home, configuration_paths, configuration_stamp
os.environ['AMPLIFIER_SESSION_STATE_HOME'] = {str(tmp_path / 'private-state')!r}
assert shared_state_home() == Path({str(tmp_path / 'private-state')!r})
assert callable(configuration_paths) and callable(configuration_stamp)
assert not any(name == 'amplifier_foundation' or name.startswith('amplifier_foundation.') for name in sys.modules)
"""
    subprocess.run([sys.executable, '-I', '-S', '-c', script], check=True,
                   capture_output=True, text=True, timeout=10)
