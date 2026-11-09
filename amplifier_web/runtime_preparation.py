"""Bind the trusted launcher's dependency policy at the native process boundary.

Foundation owns enforcement and propagation to lazy activators. This is not a
sandbox for module code or a promise that source resolution is offline.
"""
from contextlib import contextmanager
import os


@contextmanager
def preparation_scope():
    """Return readiness facts only while their Foundation policy is enforced."""
    value = os.environ.get("AMPLIFIER_RUNTIME_IMMUTABLE", "")
    if value not in {"", "0", "1"}:
        raise ValueError("Invalid trusted immutable-runtime environment policy")
    if value != "1":
        yield {}
        return
    try:
        from amplifier_foundation.modules import (
            DependencyInstallationDenied, dependency_installation_policy,
        )
    except ImportError:
        raise RuntimeError(
            "Immutable runtime requires Foundation dependency-installation policy "
            "support; prepare a compatible runtime outside this process."
        ) from None
    if (not callable(dependency_installation_policy)
            or not isinstance(DependencyInstallationDenied, type)
            or not issubclass(DependencyInstallationDenied, Exception)):
        raise RuntimeError("Unsupported Foundation dependency-installation policy")
    with dependency_installation_policy(False):
        yield {"runtimePreparation": {
            "version": 1, "immutable": True, "dependencyInstallation": False,
            "policy": "foundation-dependency-installation",
        }}
