"""Private, server-facing deployment configuration."""
from __future__ import annotations

import copy
import ipaddress
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

import yaml
from . import windows_files
from .runtime_retention import DEFAULT_RETENTION, validate_retention

_WINDOWS = os.name == "nt"
_SERVER_LIMIT = 1024 * 1024

DEFAULT_SERVER = {
    "schema_version": 1,
    "bind": ["127.0.0.1"],
    "port": 8941,
    "public_origins": [],
    "session_ttl_seconds": 604800,
    "tls": {"method": "none", "cert": "", "key": ""},
    "runtime": DEFAULT_RETENTION,
    "debug": {"profiling": False},
}


def config_path(data_dir: Path) -> Path:
    root = windows_files.address(data_dir) if _WINDOWS else Path(data_dir).expanduser().resolve()
    return root / "config" / "server.yaml"


def write_file(path: Path, contents: str | bytes, *, mode: int = 0o600) -> None:
    """Atomically write an app-owned file with its intended permissions."""
    if _WINDOWS and mode != 0o644:
        raise NotImplementedError("Windows private writes require write_private with an explicit owned_root")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(contents.encode() if isinstance(contents, str) else contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_private(path: Path, text: str | bytes, *, owned_root: Path | None = None) -> None:
    """Atomically write private configuration, never briefly exposing it."""
    if _WINDOWS:
        if owned_root is None:
            raise NotImplementedError("Windows private writes require an explicit owned_root")
        path = windows_files.address(path)
        with windows_files.directory(path.parent, owned_root=owned_root, create=True) as pinned:
            pinned.publish_private(path.name, text.encode("utf-8") if isinstance(text, str) else text,
                                   replace=True)
        return
    write_file(path, text)


def is_loopback(value: str) -> bool:
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def canonical_host(value: str) -> str:
    """Return the single canonical representation accepted for host names."""
    if not isinstance(value, str) or not value:
        raise ValueError("Host must be a valid IP address or IDNA hostname.")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    try:
        host = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Host must be a valid IDNA hostname.") from exc
    if not host or len(host) > 253:
        raise ValueError("Host must be a valid IDNA hostname.")
    labels = host.split(".")
    if any(not label or len(label) > 63 or label[0] == "-" or label[-1] == "-"
           or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in label)
           for label in labels):
        raise ValueError("Host must be a valid IDNA hostname.")
    return host


def validate_origin(value: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("Public origins must be exact http:// or https:// origins.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Public origins must be exact http:// or https:// origins.")
    if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Public origins must not contain credentials, paths, queries, or fragments.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Public origin has an invalid port.") from exc
    if port == 0:
        raise ValueError("Public origin has an invalid port.")
    host = canonical_host(parsed.hostname)
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host}{f':{port}' if port is not None and port != default_port else ''}"


def validate_server(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Server configuration must be a mapping.")
    # Preserve top-level extensions from other versions or local tooling.
    # Validate the settings this version consumes without rejecting those it does not.
    config = copy.deepcopy(DEFAULT_SERVER)
    config.update(copy.deepcopy(value))
    config["runtime"] = validate_retention(config["runtime"], stored=True)
    # debug is a versioned extension, not part of the serving/security schema.
    # Preserve it exactly as .38 does. The optional profiling consumer admits
    # only an explicit boolean True and fails closed for other values.
    if config["schema_version"] != 1:
        raise ValueError("Unsupported server configuration schema.")
    binds = config["bind"]
    if not isinstance(binds, list) or not binds or not all(isinstance(item, str) and item.strip() for item in binds):
        raise ValueError("bind must be a non-empty list of addresses.")
    try:
        config["bind"] = list(dict.fromkeys(canonical_host(item.strip()) for item in binds))
    except ValueError as exc:
        raise ValueError("bind must contain valid IP addresses or IDNA hostnames.") from exc
    if type(config["port"]) is not int or not 1 <= config["port"] <= 65535:
        raise ValueError("port must be an integer from 1 through 65535.")
    if type(config["session_ttl_seconds"]) is not int or config["session_ttl_seconds"] <= 0:
        raise ValueError("session_ttl_seconds must be a positive integer.")
    origins = config["public_origins"]
    if not isinstance(origins, list):
        raise ValueError("public_origins must be a list.")
    config["public_origins"] = list(dict.fromkeys(validate_origin(item) for item in origins))
    tls = config["tls"]
    if not isinstance(tls, dict) or set(tls) - {"method", "cert", "key"}:
        raise ValueError("tls must contain only method, cert, and key.")
    tls = {"method": tls.get("method", "none"), "cert": tls.get("cert", ""), "key": tls.get("key", "")}
    if tls["method"] not in {"none", "ca"} or not all(isinstance(tls[key], str) for key in ("cert", "key")):
        raise ValueError("Invalid TLS configuration.")
    enabled_tls = tls["method"] != "none" and bool(tls["cert"]) and bool(tls["key"])
    remote = any(not is_loopback(bind) for bind in config["bind"])
    if remote and (not enabled_tls or not config["public_origins"] or any(not origin.startswith("https://") for origin in config["public_origins"])):
        raise ValueError("Non-loopback binds require configured TLS and at least one HTTPS public origin.")
    # Allow a loopback-only host to stage the two paths in separate config
    # commands. A remote bind still requires the complete pair above, and
    # ssl_context refuses to start HTTPS until both paths are configured.
    if tls["method"] == "none" and (tls["cert"] or tls["key"]):
        raise ValueError("TLS certificate and key require tls.method to be enabled.")
    if enabled_tls and any(not origin.startswith("https://") for origin in config["public_origins"]):
        raise ValueError("HTTPS TLS configuration requires HTTPS public origins.")
    config["tls"] = tls
    return config


def load_server_config(data_dir: Path, *, overrides: dict | None = None) -> dict:
    path = config_path(data_dir)
    requested_overrides = overrides
    if _WINDOWS:
        try:
            with windows_files.directory(path.parent, owned_root=data_dir) as pinned:
                text = pinned.read_private(path.name, limit=_SERVER_LIMIT).decode("utf-8")
        except FileNotFoundError:
            text, missing = "", True
        else:
            missing = False
        loaded = yaml.safe_load(text) or {}
    else:
        missing = not path.exists()
        loaded = (yaml.safe_load(path.read_text()) or {}) if not missing else {}
    if not isinstance(loaded, dict):
        raise ValueError("Server configuration must be a mapping.")
    if overrides:
        overrides = {key: value for key, value in overrides.items() if value is not None}
        if "tls" in overrides:
            configured_tls, override_tls = loaded.get("tls", {}), overrides.pop("tls")
            if not isinstance(configured_tls, dict):
                raise ValueError("tls must contain only method, cert, and key.")
            if isinstance(override_tls, dict):
                loaded = {**loaded, "tls": {**configured_tls, **override_tls}}
            else:
                loaded = {**loaded, "tls": override_tls}
        loaded = {**loaded, **overrides}
    config = validate_server(loaded)
    if missing:
        if _WINDOWS:
            try:
                with windows_files.directory(path.parent, owned_root=data_dir, create=True) as pinned:
                    pinned.publish_private(path.name, yaml.safe_dump(config, sort_keys=False).encode("utf-8"))
            except FileExistsError:
                # Another creator won. Read/validate that complete winner;
                # never replace their settings with our defaults or overrides.
                return load_server_config(data_dir, overrides=requested_overrides)
        else:
            save_server_config(data_dir, config)
    return config


def save_server_config(data_dir: Path, config: dict) -> dict:
    config = validate_server(config)
    text = yaml.safe_dump(config, sort_keys=False)
    if _WINDOWS:
        write_private(config_path(data_dir), text, owned_root=data_dir)
    else:
        write_private(config_path(data_dir), text)
    return config


def setting_get(config: dict, key: str):
    value = config
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"Unknown server configuration setting: {key}")
        value = value[part]
    return value


def setting_set(config: dict, key: str, value):
    result = copy.deepcopy(config)
    target = result
    parts = key.split(".")
    for part in parts[:-1]:
        if not isinstance(target, dict) or part not in target:
            raise ValueError(f"Unknown server configuration setting: {key}")
        target = target[part]
    if parts[-1] not in target:
        raise ValueError(f"Unknown server configuration setting: {key}")
    target[parts[-1]] = value
    return validate_server(result)


def resolve_path(data_dir: Path, configured_path: str) -> Path:
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else Path(data_dir).expanduser().resolve() / path
