"""Shared configuration and application-owned runtime source caches."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import shlex


from ..deployment import write_private
from ..shared_settings import read_yaml, read_settings
from ..session_files import amplifier_home

FOUNDATION_SOURCE = "git+https://github.com/microsoft/amplifier-foundation@main"
WORK_SOURCE = "git+https://github.com/microsoft/amplifier-bundle-work@main#subdirectory=bundle.md"
PRECONFIGURED_BUNDLES = {
    "anchors": "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors/bundle.md",
    "anchors-amp-dev": "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=bundles/anchors-amp-dev/bundle.md",
    "work": WORK_SOURCE,
    "work-amp-dev": "git+https://github.com/microsoft/amplifier-bundle-work@main#subdirectory=bundles/work-amp-dev.md",
}
_KEY_FILE_VALUES = {}


def app_home() -> Path:
    return Path(os.environ.get("AMPLIFIER_WEB_HOME", os.environ.get("AMPLIFIER_WEB_DATA_DIR", Path.home() / ".amplifier-unified"))).expanduser().resolve()


def merge(base, overlay):
    """Config dictionaries deep-merge; named module lists merge by identity."""
    if isinstance(base, dict) and isinstance(overlay, dict):
        result = copy.deepcopy(base)
        for key, value in overlay.items():
            result[key] = merge(result[key], value) if key in result else copy.deepcopy(value)
        return result
    if isinstance(base, list) and isinstance(overlay, list) and all(isinstance(r, dict) and "module" in r for r in base + overlay):
        result = copy.deepcopy(base)
        indexes = {r.get("id") or r.get("instance_id") or r["module"]: i for i, r in enumerate(result)}
        for row in overlay:
            key = row.get("id") or row.get("instance_id") or row["module"]
            if key in indexes:
                result[indexes[key]] = merge(result[indexes[key]], row)
            else:
                indexes[key] = len(result)
                result.append(copy.deepcopy(row))
        return result
    return copy.deepcopy(overlay)


def _load_keys(path):
    values = {}
    if path.exists():
        lines = path.read_text().splitlines()
    else:
        lines = ()
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            parsed = shlex.split(value, comments=True)
            values[name] = " ".join(parsed)
    # Keep explicitly supplied process environment authoritative, while values
    # this loader previously installed are refreshed (or removed) on remount.
    for name, previous in tuple(_KEY_FILE_VALUES.items()):
        if name not in values and os.environ.get(name) == previous:
            os.environ.pop(name, None)
            _KEY_FILE_VALUES.pop(name, None)
    for name, value in values.items():
        previous = _KEY_FILE_VALUES.get(name)
        if name not in os.environ or (previous is not None and os.environ.get(name) == previous):
            os.environ[name] = value
            _KEY_FILE_VALUES[name] = value


def worker_environment():
    """Let a fresh worker load file-managed keys itself, so remounts can refresh.

    Explicit launch-environment credentials remain authoritative. File-managed
    values must not become indistinguishable from explicit credentials merely
    because a worker inherited the server's environment.
    """
    from ..update_sources import store_environment
    return {**{name: value for name, value in os.environ.items()
            if name not in _KEY_FILE_VALUES or value != _KEY_FILE_VALUES[name]},
            **store_environment(app_home())}


def credential_environment_source(name):
    """Describe the effective environment value without returning its contents."""
    if name in _KEY_FILE_VALUES and os.environ.get(name) == _KEY_FILE_VALUES[name]:
        return "key-file"
    return "environment"


def expand_environment(value, *, environment=None):
    values = os.environ if environment is None else environment
    if isinstance(value, dict):
        return {key: expand_environment(item, environment=values) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_environment(item, environment=values) for item in value]
    if not isinstance(value, str):
        return value
    def substitute(match):
        name, fallback = match.groups()
        shell_default = fallback is not None and fallback.startswith("-")
        current = values.get(name)
        if current is not None and (current or not shell_default):
            return current
        if fallback is not None:
            return fallback[1:] if shell_default else fallback
        raise ValueError(f"Configured environment variable {name} is not set; update the shared Amplifier keys.env or environment.")
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}", substitute, value)


@dataclass
class HostConfig:
    home: Path
    workspace: Path
    settings: dict
    registry_home: Path
    config_home: Path | None = None
    global_only: bool = False
    access_mode: str = "ask"
    image_intent_sources: tuple = ()

    @property
    def settings_file(self):
        return (self.config_home or amplifier_home()) / "settings.yaml"

    @property
    def active_bundle(self):
        return self.settings.get("bundle", {}).get("active") or "work"

    @property
    def app_bundles(self):
        from ..builtin_behaviors import app_behaviors
        return app_behaviors(self.settings)

    @property
    def providers(self):
        rows=self.settings.get("config", {}).get("providers", [])
        return sorted(rows, key=lambda row: row.get("config", {}).get("priority", 100))

    @property
    def configured_connections(self):
        """Explicit connection rows with the same overrides as bundle composition."""
        overrides = self.settings.get('overrides', {})
        disabled = self.settings.get('configurator', {}).get('disabled', {}).get('providers', [])
        rows = []
        for original in self.providers:
            identity = original.get('id') or original.get('instance_id') or original['module'].removeprefix('provider-')
            patch = merge(overrides.get(original['module'], {}), overrides.get(identity, {}))
            row = merge(original, {key:value for key,value in patch.items() if key in {'source','config'}})
            row['enabled'] = original.get('enabled', True) and patch.get('enabled') is not False and identity not in disabled
            rows.append(row)
        return sorted(rows, key=lambda row: row.get('config', {}).get('priority', 100))

    @property
    def module_sources(self):
        result = dict(self.settings.get("sources", {}).get("modules", {}))
        result.update({key:row["source"] for key,row in self.settings.get("overrides", {}).items()
            if isinstance(row, dict) and "source" in row})
        result.update({row["module"]:row["source"] for row in self.providers if row.get("source")})
        return result

    @property
    def bundle_sources(self):
        return self.settings.get("sources", {}).get("bundles", {})

    @property
    def registrations(self):
        return {
            "foundation": FOUNDATION_SOURCE,
            **PRECONFIGURED_BUNDLES,
            **self.settings.get("bundle", {}).get("added", {}),
            **self.bundle_sources,
        }

    def resolve_source(self, source):
        # Source overrides also support namespace-relative includes.
        if source in self.bundle_sources:
            return self.bundle_sources[source]
        if ":" in source and not source.startswith(("git+", "https:", "http:", "file:")):
            namespace, suffix = source.split(":", 1)
            replacement = self.bundle_sources.get(namespace)
            if replacement:
                if replacement.startswith("git+"):
                    base, _, fragment = replacement.partition("#")
                    # Foundation treats fragment values as literal paths. Keep
                    # their spelling and unrelated fragment options unchanged.
                    fields = {}
                    for field in fragment.split("&"):
                        key, separator, value = field.partition("=")
                        if separator:
                            fields[key] = value
                    sub = fields.get("subdirectory", "").rstrip("/")
                    # Bundle sources may point at a manifest, not just a folder.
                    # Includes are relative to the directory containing that file.
                    if PurePosixPath(sub).suffix.lower() in {".md", ".yaml", ".yml"}:
                        sub = str(PurePosixPath(sub).parent)
                        if sub == ".":
                            sub = ""
                    fields["subdirectory"] = "/".join(v for v in (sub, suffix) if v)
                    return base + "#" + "&".join(f"{key}={value}" for key, value in fields.items())
                path = Path(replacement.removeprefix("file://"))
                return str((path.parent if path.is_file() else path) / suffix)
        return None


def prepare_registry(config):
    """Create owned source storage; never read/copy the CLI registry or cache.

    Settings and user-authored resources remain shared. Registry aliases are
    constructed per session from those settings, including on older installs
    whose owned directory still contains a former imported registry.
    """
    config.registry_home.mkdir(parents=True, exist_ok=True, mode=0o700)


def configure_skill_cache(bundle, registry_home):
    """Keep remote clones private while local user/workspace skills stay shared."""
    def apply(tools, agents):
        for row in tools:
            if isinstance(row, dict) and row.get("module") == "tool-skills":
                config = row.setdefault("config", {})
                # Explicit user paths remain authoritative; only the default
                # remote cache is host-owned. Never change AMPLIFIER_HOME:
                # settings, instructions and native history share that root.
                if config.get("cache_dir") is None:
                    config["cache_dir"] = str(Path(registry_home) / "cache/skills")
        for child in agents.values():
            if isinstance(child, dict):
                apply(child.get("tools", []), child.get("agents", {}))
    apply(getattr(bundle, "tools", []), getattr(bundle, "agents", {}))

def load_config(workspace, *, home=None, legacy_home=None, session_id=None, global_only=False, owner_session_id=None):
    workspace = Path(workspace).expanduser().resolve(strict=True)
    shared = Path(legacy_home or amplifier_home()).expanduser().resolve()
    _load_keys(shared / "keys.env")
    return read_config(workspace, home=home, shared_home=shared, session_id=session_id, global_only=global_only, owner_session_id=owner_session_id)


def read_config(workspace, *, home=None, shared_home=None, session_id=None, global_only=False, settings_cache=None, registry_home=None, owner_session_id=None):
    """Read runtime settings without loading keys, preparing caches or writing."""
    home = Path(home or app_home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve(strict=True)
    shared = Path(shared_home or amplifier_home()).expanduser().resolve()
    settings = read_settings(workspace, shared_home=shared, session_id=session_id, global_only=global_only, cache=settings_cache)
    from ..updates import foundation_home
    registry_home = Path(registry_home) if registry_home is not None else foundation_home(home)
    # Keep explicit app-cache source overrides aligned with the active snapshot.
    def relocate(value):
        if isinstance(value, dict): return {k:relocate(v) for k,v in value.items()}
        if isinstance(value, list): return [relocate(v) for v in value]
        if isinstance(value, str): return value.replace(str(home / "foundation"), str(registry_home))
        return value
    from ..access_mode import read_mode
    from ..image_migration import scoped_image_sources
    return HostConfig(home, workspace, relocate(settings), registry_home, shared, global_only,
                      read_mode(home, workspace, session_id, owner_session_id=owner_session_id),
                      tuple(scoped_image_sources(workspace, shared_home=shared,
                                                session_id=session_id, global_only=global_only)))
