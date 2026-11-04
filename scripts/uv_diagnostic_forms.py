"""Pure, privacy-safe recognition of a small pinned uv 0.13.6 text subset.

Public-source research preceded this parser; no public structured stderr interface
was verified. These are reported forms, NOT proof of an actual dependency conflict
or a root-manifest/CLI failure. The caller owns raw hashes, sizes, capture and
receipt health. No captured text or tokens leave this module.

Pinned sources (329bf9448afd9f9088d14a4d57fbc61eb454f117):
https://github.com/astral-sh/uv/blob/329bf9448afd9f9088d14a4d57fbc61eb454f117/crates/uv/tests/lock/lock.rs#L4308-L4314
https://github.com/astral-sh/uv/blob/329bf9448afd9f9088d14a4d57fbc61eb454f117/crates/uv/tests/lock/lock.rs#L47060-L47067
https://github.com/astral-sh/uv/blob/329bf9448afd9f9088d14a4d57fbc61eb454f117/crates/uv/tests/lock/lock.rs#L20840-L20849
https://github.com/astral-sh/uv/blob/329bf9448afd9f9088d14a4d57fbc61eb454f117/crates/uv/tests/lock/lock.rs#L74-L91
https://github.com/astral-sh/uv/blob/329bf9448afd9f9088d14a4d57fbc61eb454f117/crates/uv-errors/src/lib.rs#L349-L403
https://github.com/astral-sh/uv/blob/329bf9448afd9f9088d14a4d57fbc61eb454f117/crates/uv-cli/src/lib.rs#L3813-L3896

CLI-only forms, context splits, wrapping, colors, deeper chains and extra content
are unsupported. Only the nested TOML template permits its exact warning prefix;
only offline templates permit their exact hint. Paths are a bounded safe subset,
not a general TOML formatter. At most one terminal LF is accepted.
"""

import re
import unicodedata

_VERSION = "0.13.6"
_MAX_BYTES = 64 * 1024
_PACKAGE = r"[a-z][a-z0-9_-]{0,63}"
_NUMERIC = r"(?=[0-9.]{1,64}(?![0-9.]))[0-9]+(?:\.[0-9]+){0,3}"
_SEGMENT = r"(?!\.{1,2}(?:/|$))[A-Za-z0-9_.-]{1,64}"
_ROOT = rf"(?:\[TEMP_DIR\]|(?:/{_SEGMENT}){{0,16}})"
_HEADER = re.escape("error: No solution found when resolving dependencies\n")
_HINT = re.escape(
    "\n\nhint: Packages were unavailable because the network was disabled. "
    "When the network is disabled, registry packages may only be read from the cache."
)
_CONFLICT = re.compile(
    _HEADER
    + rf"  cause: Because (?P<a>{_PACKAGE})==(?P<av>{_NUMERIC}) depends on "
    + rf"(?P<b>{_PACKAGE})==(?P<bv>{_NUMERIC}) and your project depends on "
    + r"(?P=a)==(?P=av), we can conclude that your project depends on (?P=b)==(?P=bv)\.\n"
    + rf"         And because your project depends on (?P=b)==(?P<other>{_NUMERIC}), "
    + r"we can conclude that your project's requirements are unsatisfiable\.\n?"
)
_OFFLINE_PROJECT = re.compile(
    _HEADER
    + rf"  cause: Because (?P<p>{_PACKAGE}) was not found in the cache and your project "
    + r"depends on (?P=p), we can conclude that your project's requirements are unsatisfiable\."
    + _HINT + r"\n?"
)
_OFFLINE_WORKSPACE = re.compile(
    _HEADER
    + rf"  cause: Because (?P<p>{_PACKAGE}) was not found in the cache and "
    + rf"(?P<leaf>{_PACKAGE}) depends on (?P=p)>{_NUMERIC}, we can conclude that "
    + r"(?P=leaf)'s requirements are unsatisfiable\.\n"
    + r"         And because your workspace requires (?P=leaf), we can conclude that "
    + r"your workspace's requirements are unsatisfiable\."
    + _HINT + r"\n?"
)
_TOML = re.escape(
    "  cause: TOML parse error at line 3, column 11\n"
    "           |\n"
    "         3 | version = 42\n"
    "           |           ^^\n"
    "         invalid type: integer `42`, expected a string"
)
_NESTED_TOML = re.compile(
    re.escape("warning: Failed to validate existing lockfile\n  cause: Failed to parse `")
    + rf"(?P<path>{_ROOT}/(?P<child>{_PACKAGE}))/pyproject\.toml`\n"
    + _TOML
    + r"\nerror: Failed to build `(?P=child) @ file://(?P=path)`\n"
    + re.escape("  cause: Failed to parse metadata from built wheel\n")
    + _TOML + r"\n?"
)
_RECOGNIZED = {
    "resolution-unsatisfiable": ("resolver-proof", (True, None, None)),
    "offline-missing-cache-or-metadata": ("resolver-proof", (True, True, None)),
    "manifest-or-cli": ("toml-in-build", (None, None, True)),
}
_ENUMS = {
    "uvVersion": (_VERSION, "unsupported"),
    "parseHealth": (
        "recognized", "unsupported", "incomplete", "oversize", "invalid-utf8",
        "invalid-exit", "unsupported-version", "control-character",
    ),
    "kind": (*_RECOGNIZED, "other", "unknown"),
    "form": ("resolver-proof", "toml-in-build", "unknown"),
}
_FLAG_KEYS = ("resolutionProof", "offlineAbsence", "tomlDiagnostic")
_KEYS = {"grammarVersion", "flags", *_ENUMS}


def _record(health, kind="unknown"):
    form, flags = _RECOGNIZED.get(kind, ("unknown", (None, None, None)))
    return {
        "grammarVersion": 1,
        "uvVersion": "unsupported" if health == "unsupported-version" else _VERSION,
        "parseHealth": health,
        "kind": kind,
        "form": form,
        "flags": dict(zip(_FLAG_KEYS, flags)),
    }


def _numeric_version(token):
    parts = tuple(int(part) for part in token.split("."))
    return parts + (0,) * (4 - len(parts))


def parse_form(data: bytes, returncode, *, complete=True, uv_version="0.13.6") -> dict:
    """Recognize a complete failure body; never infer a cause from partial text.

    Preconditions are checked in order: exact version, exit, completeness, bytes,
    size, UTF-8, controls. Wrong data types yield unsupported; complete must be the
    singleton True. Versions are at most 64 characters. Cc/Cf controls are rejected
    except LF. Paths allow at most 16 base segments of at most 64 ASCII characters,
    followed by a package segment. No I/O, subprocesses or product imports occur.
    """
    if type(uv_version) is not str or uv_version != _VERSION:
        return _record("unsupported-version")
    if type(returncode) is not int or not 1 <= returncode <= 255:
        return _record("invalid-exit")
    if complete is not True:
        return _record("incomplete")
    if type(data) is not bytes:
        return _record("unsupported")
    if len(data) > _MAX_BYTES:
        return _record("oversize")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _record("invalid-utf8")
    if any(c != "\n" and unicodedata.category(c) in {"Cc", "Cf"} for c in text):
        return _record("control-character")
    match = _CONFLICT.fullmatch(text)
    if match is not None:
        # Reject aliases of the same package and numerically equivalent versions.
        a = re.sub(r"[-_]+", "-", match["a"])
        b = re.sub(r"[-_]+", "-", match["b"])
        if a != b and _numeric_version(match["bv"]) != _numeric_version(match["other"]):
            return _record("recognized", "resolution-unsatisfiable")
        return _record("unsupported")
    if _OFFLINE_PROJECT.fullmatch(text) or _OFFLINE_WORKSPACE.fullmatch(text):
        return _record("recognized", "offline-missing-cache-or-metadata")
    if _NESTED_TOML.fullmatch(text):
        return _record("recognized", "manifest-or-cli")
    return _record("unsupported")


def validate_form(value) -> bool:
    """Validate exact JSON types/keys, enums and all allowed cross-field states.

    Booleans cannot stand in for integers. Unrecognized states carry no flags or
    classification. The reserved 'other' kind has no recognized form in grammar 1.
    """
    if type(value) is not dict:
        return False
    if any(type(key) is not str for key in value) or set(value) != _KEYS:
        return False
    if type(value["grammarVersion"]) is not int or value["grammarVersion"] != 1:
        return False
    for key, allowed in _ENUMS.items():
        if type(value[key]) is not str or value[key] not in allowed:
            return False
    flags = value["flags"]
    if type(flags) is not dict:
        return False
    if any(type(key) is not str for key in flags) or set(flags) != set(_FLAG_KEYS):
        return False
    if any(flag is not None and type(flag) is not bool for flag in flags.values()):
        return False
    flag_values = tuple(flags[key] for key in _FLAG_KEYS)
    unsupported_version = value["uvVersion"] == "unsupported"
    if unsupported_version != (value["parseHealth"] == "unsupported-version"):
        return False
    if value["parseHealth"] == "recognized":
        expected = _RECOGNIZED.get(value["kind"])
        return expected is not None and (value["form"], flag_values) == expected
    return (
        value["kind"] == "unknown"
        and value["form"] == "unknown"
        and flag_values == (None, None, None)
    )
