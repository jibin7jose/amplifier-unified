"""Package materialized Windows wheels as an explicit prototype capsule.

This standard-library tool does not resolve/install dependencies or claim Windows
support. Offline mode preserves the complete wheelhouse interface. Pinned-vcs
mode carries the frozen app/registry wheels and six pure build-backend wheels;
the installer, not this tool, performs the five exact noneditable Git installs.
"""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import io
from itertools import islice
import json
from pathlib import Path
import re
import stat
import tomllib
from urllib.parse import parse_qs, urlsplit
import zipfile


APP_SOURCE = "1c4ac3b4f9ed5d2d3f588f6fa6c74a119e439153"
APP_VERSION = "0.54.5"
APP_SHA256 = "de950fe6dcf43e9944bfae4c45b6adeedc4b2fdbee4eda565443516a3ffcc9f5"
APP_BYTES = 10218316
APP_METADATA_SHA256 = "f5291c87ec784e2b7ece5a77db9f4d4c543eeab7c16e16189e3232c1e974aa9f"
LOCK_SHA256 = "02ca2a403cb6546238e1e3b1eed2b07e2b848f7f1edc301fc67f4cc956f89c45"
PRODUCTION_REQUIREMENTS_SHA256 = "4013b4a681ce2e7729c7763069a0fc621d46b34e5bc0511c17fa074f1bafa6a0"
BUILD_PACKAGES = {
    "hatchling": "1.32.4", "packaging": "26.3", "pathspec": "1.1.1",
    "pluggy": "1.6.0", "tomlkit": "0.15.2", "trove-classifiers": "2026.9.21.13",
}
# These are the five accepted production sources, not whatever VCS entries a
# supplied lock happens to contain. Only Context Intelligence activates an extra.
GIT_SOURCES = {
    "amplifier-bundle-context-intelligence": {
        "url": "https://github.com/microsoft/amplifier-bundle-context-intelligence",
        "commit": "a80ce69d0e96e9a343501f238dcd867350dd9aa2", "subdirectory": "", "extra": "client",
    },
    "amplifier-bundle-ui-use": {
        "url": "https://github.com/microsoft/amplifier-bundle-ui-use",
        "commit": "5e3b08b8e06a6095314200476d387508ddb32f67", "subdirectory": "", "extra": "",
    },
    "amplifier-foundation": {
        "url": "https://github.com/microsoft/amplifier-foundation",
        "commit": "c716df3cea84b2539aabdfea58a69ae676a1e853", "subdirectory": "", "extra": "",
    },
    "amplifier-memory": {
        "url": "https://github.com/microsoft/amplifier-bundle-memory",
        "commit": "5ac75ac85b9972e06936630e347023af107fd364", "subdirectory": "", "extra": "",
    },
    "amplifier-module-hooks-session-naming": {
        "url": "https://github.com/microsoft/amplifier-foundation",
        "commit": "c716df3cea84b2539aabdfea58a69ae676a1e853",
        "subdirectory": "modules/hooks-session-naming", "extra": "",
    },
}
SOURCE_MODE = "pinned-vcs"
BOOTSTRAP_HELPERS = {
    "uv": {"version": "0.12.18", "sha256": "cae6a3bc25239f83dffb467a4b180508d9da23986c04639ebfa44e43e6a84bff"},
    "git": {"version": "2.56.0.windows.4", "sha256": "e3bf78351fe777fba47ddee5950082f8a98bb0f02604fdc7ba3ee2cb68ffa877"},
}
MAX_WHEEL_BYTES = 134217728
MAX_EXPANDED_BYTES = 400 * 1024 * 1024


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wheel_identity(path: Path) -> tuple[str, str]:
    tags = path.name.removesuffix(".whl").rsplit("-", 3)
    if len(tags) != 4 or not any(
        compatible_tag(interpreter, abi, platform)
        for interpreter in tags[1].split(".")
        for abi in tags[2].split(".")
        for platform in tags[3].split(".")
    ):
        raise ValueError("Wheelhouse must target ordinary Windows x64 or pure Python.")
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)]
        if len(names) != 1:
            raise ValueError("Wheel must contain one distribution metadata identity.")
        metadata = BytesParser().parsebytes(archive.read(names[0]))
    name, version = str(metadata["Name"]), str(metadata["Version"])
    if not re.fullmatch(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*", name):
        raise ValueError("Invalid wheel distribution name.")
    if not re.fullmatch(r"[A-Za-z0-9.!+_-]+", version):
        raise ValueError("Invalid wheel version.")
    return re.sub(r"[-_.]+", "-", name).lower(), version


def compatible_tag(interpreter: str, abi: str, platform: str) -> bool:
    """Conservative ordinary CPython 3.13 x64 tag subset; never free-threaded."""
    if platform not in ("any", "win_amd64"):
        return False
    if abi == "none":
        return interpreter in {"py3", "cp313", *(f"py3{minor}" for minor in range(14))}
    if platform != "win_amd64":
        return False
    if interpreter == "cp313" and abi == "cp313":
        return True
    match = re.fullmatch(r"cp3(\d+)", interpreter)
    return abi == "abi3" and match is not None and 2 <= int(match[1]) <= 13


def checked_inventory(path: Path, source: str, identities: dict) -> bytes:
    """Check an independently reviewed inventory, not one inferred from inputs.

    The owner establishes METADATA closure and source provenance first. Equality
    prevents adding dev wheels or omitting a reviewed dependency; it does not
    independently establish those prior facts.
    """
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 262144:
        raise ValueError("Unsafe reviewed inventory input.")
    raw = path.read_bytes()
    record = json.loads(raw)
    if (not isinstance(record, dict) or record.get("schema") != 1 or record.get("source") != source
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("lockSha256", "")))
            or not isinstance(record.get("packages"), dict) or not record["packages"]):
        raise ValueError("Supply the reviewed source/lock inventory.")
    for name, item in record["packages"].items():
        if (not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                or not isinstance(item, dict) or set(item) != {"version", "sha256"}
                or not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"]))):
            raise ValueError("Malformed reviewed package inventory.")
    if record["packages"] != identities:
        raise ValueError("Wheelhouse differs from the reviewed production inventory.")
    return raw


def build(wheelhouse: Path, output: Path, *, inventory: Path, source: str, version: str, python: str, url: str,
          source_mode: str = "offline", build_wheelhouse: Path | None = None,
          lock: Path | None = None, production_requirements: Path | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Supply exact source commit and app version.")
    if not re.fullmatch(r"3\.13\.\d+", python):
        raise ValueError("Supply an ordinary CPython 3.13 patch supported by the pinned uv.")
    if (not re.fullmatch(r"https://github\.com/microsoft/amplifier-unified/releases/download/[^/?#]+/[^/?#]+\.zip", url)
            or "/download/latest/" in url or "/download/main/" in url):
        raise ValueError("Supply the intended immutable official capsule asset URL.")
    if source_mode == SOURCE_MODE:
        return build_source(wheelhouse, output, inventory=inventory, source=source, version=version,
                            python=python, url=url, build_wheelhouse=build_wheelhouse,
                            lock=lock, production_requirements=production_requirements)
    if source_mode != "offline" or any(item is not None for item in (build_wheelhouse, lock, production_requirements)):
        raise ValueError("Source inputs require pinned-vcs mode.")
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels or len(wheels) > 256:
        raise ValueError("Supply a complete bounded Windows wheelhouse.")
    identities = {}
    requirements = []
    for wheel in wheels:
        if wheel.is_symlink() or not wheel.is_file() or wheel.stat().st_size > 134217728:
            raise ValueError("Unsafe wheel input.")
        name, item_version = wheel_identity(wheel)
        if name in identities:
            raise ValueError("Duplicate distribution in wheelhouse.")
        digest = sha256(wheel)
        identities[name] = {"version": item_version, "sha256": digest}
        requirements.append(f"{name}=={item_version} --hash=sha256:{digest}")
    if sum(wheel.stat().st_size for wheel in wheels) > 400 * 1024 * 1024:
        raise ValueError("Expanded capsule wheel budget exceeded.")
    if identities.get("amplifier-unified", {}).get("version") != version:
        raise ValueError("Wheelhouse does not contain the selected app version.")
    reviewed = checked_inventory(inventory, source, identities)
    # No sdist, VCS resolution, editable install or native build on user machines.
    # pip check does not establish selected extras, exact source or set equality.
    launch = Path(__file__).with_name("windows_prototype_launch.py")
    contents = {
        "requirements.txt": ("\n".join(sorted(requirements)) + "\n").encode(),
        "launch.py": launch.read_bytes(),
        "capsule.json": json.dumps({"source": source, "version": version}, sort_keys=True).encode(),
        "inventory.json": reviewed,
    }
    output.mkdir(parents=True, exist_ok=False)
    capsule = output / "windows-prototype.zip"
    with zipfile.ZipFile(capsule, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        for wheel in wheels:
            info = zipfile.ZipInfo("wheels/" + wheel.name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            archive.writestr(info, wheel.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    if capsule.stat().st_size > 134217728:
        raise ValueError("Capsule exceeds bootstrap download budget; retain failed output for review.")
    record = {
        "schemaVersion": 1, "status": "prototype", "source": source, "version": version,
        "architecture": "x86_64", "python": python,
        "capsule": {"url": url, "sha256": sha256(capsule)},
    }
    manifest = output / "windows-prototype.json"
    manifest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {"manifestSha256": sha256(manifest), "capsuleSha256": record["capsule"]["sha256"]}


def read_bounded(path: Path, limit: int) -> bytes:
    if (path.is_symlink() or path.is_junction() or not stat.S_ISREG(path.stat().st_mode)
            or path.stat().st_size > limit):
        raise ValueError("Unsafe or oversized capsule input.")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Capsule input grew beyond its budget.")
    return raw


def canonical_json(record: dict) -> bytes:
    return (json.dumps(record, sort_keys=True, indent=2) + "\n").encode("utf-8")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def git_requirement(name: str, item: dict, *, extras: bool) -> str:
    suffix = f"[{item['extra']}]" if extras and item["extra"] else ""
    subdir = f"#subdirectory={item['subdirectory']}" if item["subdirectory"] else ""
    return f"{name}{suffix} @ git+{item['url']}@{item['commit']}{subdir}"


def frozen_selection(lock_raw: bytes, requirements_raw: bytes) -> tuple[dict, dict]:
    """Derive the Windows selection from the exact reviewed export, not resolve it.

    Deliberately not a general marker evaluator: the export hash binds every
    marker and the one literal emscripten-only exclusion. New inputs need review.
    """
    if hashlib.sha256(lock_raw).hexdigest() != LOCK_SHA256:
        raise ValueError("Frozen lock hash mismatch.")
    if hashlib.sha256(requirements_raw).hexdigest() != PRODUCTION_REQUIREMENTS_SHA256:
        raise ValueError("Frozen production requirements hash mismatch.")
    records = tomllib.loads(lock_raw.decode("utf-8"))["package"]
    locked = {}
    for item in records:
        name = item["name"]
        if name in locked:
            raise ValueError("Ambiguous frozen lock identity.")
        locked[name] = item
    app = locked.get("amplifier-unified", {})
    if app.get("version") != APP_VERSION or app.get("source") != {"editable": "."}:
        raise ValueError("Frozen app source/version mismatch.")
    expected = {"amplifier-unified": APP_VERSION}
    for line in requirements_raw.decode("utf-8").splitlines():
        if line == "httpx2-jsfetch==1.0 ; sys_platform == 'emscripten'":
            continue
        requirement = line.split(" ; ", 1)[0]
        name = re.split(r"==| @ ", requirement, maxsplit=1)[0]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or name in expected or name not in locked:
            raise ValueError("Malformed or duplicate production selection.")
        item = locked[name]
        if name in GIT_SOURCES:
            origin = GIT_SOURCES[name]
            if requirement != git_requirement(name, origin, extras=False):
                raise ValueError("Frozen Git requirement source mismatch.")
            source = item.get("source", {})
            if set(source) != {"git"}:
                raise ValueError("Frozen Git lock source mismatch.")
            url = urlsplit(source["git"])
            query = parse_qs(url.query, strict_parsing=True)
            wanted_query = {"rev": ["main"]}
            if origin["subdirectory"]:
                wanted_query["subdirectory"] = [origin["subdirectory"]]
            if (url._replace(query="", fragment="").geturl() != origin["url"]
                    or url.fragment != origin["commit"] or query != wanted_query):
                raise ValueError("Frozen Git lock commit/subdirectory mismatch.")
        else:
            if (requirement != f"{name}=={item['version']}"
                    or item.get("source") != {"registry": "https://pypi.org/simple"}):
                raise ValueError("Frozen registry version/source mismatch.")
        expected[name] = item["version"]
    if (len(expected) != 65 or not GIT_SOURCES.keys() <= expected.keys()
            or expected.get("uv") != "0.13.4"):
        raise ValueError("Expected exactly 64 production dependencies plus the app.")
    for name, version in BUILD_PACKAGES.items():
        item = locked.get(name, {})
        if item.get("version") != version or item.get("source") != {"registry": "https://pypi.org/simple"}:
            raise ValueError("Frozen build backend selection mismatch.")
    return expected, locked


def wheel_filename(filename: str) -> tuple[str, str, set[str], str, str]:
    match = re.fullmatch(
        r"([A-Za-z0-9_.]+)-([A-Za-z0-9_.!+]+)(?:-[0-9][A-Za-z0-9_.]*)?"
        r"-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)-([A-Za-z0-9_.]+)\.whl", filename)
    if not match:
        raise ValueError("Unsafe wheel filename.")
    dist, version, interpreters, abis, platforms = match.groups()
    tags = {f"{i}-{a}-{p}" for i in interpreters.split(".")
            for a in abis.split(".") for p in platforms.split(".")}
    return dist, version, tags, abis, platforms


def strict_wheel(raw: bytes, filename: str, *, pure: bool = False) -> tuple[str, str, bytes]:
    """Check wheel identity, bounded archive members and filename/WHEEL tags.

    Packaging calls this only after the exact original/locked hash matches.
    """
    dist, version, tags, abis, platforms = wheel_filename(filename)
    if not any(compatible_tag(*tag.split("-")) for tag in tags):
        raise ValueError("Wheel must target ordinary Windows x64 or pure Python.")
    if pure and (abis != "none" or platforms != "any"):
        raise ValueError("Build backend must use pure Python wheels.")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if (len(entries) > 20000 or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES
                or len({item.filename for item in entries}) != len(entries)):
            raise ValueError("Wheel archive exceeds bounds or has duplicate members.")
        for entry in entries:
            name = entry.filename
            if (not name or name.startswith("/") or "\\" in name or ":" in name
                    or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.file_size > MAX_WHEEL_BYTES):
                raise ValueError("Unsafe wheel member.")
        names = [item.filename for item in entries if re.fullmatch(r"[^/]+\.dist-info/METADATA", item.filename)]
        if len(names) != 1:
            raise ValueError("Wheel must contain one distribution metadata identity.")
        metadata_name = names[0]
        if archive.getinfo(metadata_name).file_size > 1048576:
            raise ValueError("Oversized wheel metadata.")
        metadata_raw = archive.read(metadata_name)
        metadata = BytesParser().parsebytes(metadata_raw)
        if (len(metadata.get_all("Name", [])) != 1 or len(metadata.get_all("Version", [])) != 1
                or normalize(str(metadata["Name"])) != normalize(dist) or str(metadata["Version"]) != version
                or metadata_name.split("/")[0] != f"{dist}-{version}.dist-info"):
            raise ValueError("Wheel filename/METADATA identity mismatch.")
        wheel_name = metadata_name.rsplit("/", 1)[0] + "/WHEEL"
        if archive.getinfo(wheel_name).file_size > 65536:
            raise ValueError("Oversized wheel tags.")
        wheel_metadata = BytesParser().parsebytes(archive.read(wheel_name))
        if set(wheel_metadata.get_all("Tag", [])) != tags:
            raise ValueError("Wheel filename/WHEEL tag mismatch.")
        if pure and wheel_metadata.get("Root-Is-Purelib", "").lower() != "true":
            raise ValueError("Build backend must be pure Python.")
    return normalize(dist), version, metadata_raw


def source_wheels(directory: Path, versions: dict, locked: dict, *, backend: bool = False) -> tuple[dict, dict]:
    if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
        raise ValueError("Unsafe wheelhouse directory.")
    paths = sorted(islice(directory.iterdir(), len(versions) + 1))
    if len(paths) != len(versions):
        raise ValueError("Wheelhouse has missing or extra production/backend inputs.")
    packages, bodies = {}, {}
    total = 0
    for path in paths:
        if path.is_symlink() or path.is_junction():
            raise ValueError("Unsafe wheel input.")
        dist, version, _, _, _ = wheel_filename(path.name)
        name = normalize(dist)
        if name in packages or versions.get(name) != version:
            raise ValueError("Wheelhouse has duplicate, extra or wrong-version inputs.")
        raw = read_bounded(path, MAX_WHEEL_BYTES)
        total += len(raw)
        if total > MAX_EXPANDED_BYTES:
            raise ValueError("Wheelhouse budget exceeded.")
        digest = hashlib.sha256(raw).hexdigest()
        if name == "amplifier-unified" and not backend:
            if digest != APP_SHA256 or len(raw) != APP_BYTES:
                raise ValueError("Original app wheel hash/size/METADATA mismatch.")
        else:
            if not any(item.get("hash") == "sha256:" + digest and item.get("size") == len(raw)
                       and urlsplit(item.get("url", "")).scheme == "https"
                       and urlsplit(item["url"]).netloc == "files.pythonhosted.org"
                       and urlsplit(item["url"]).path.rsplit("/", 1)[-1] == path.name
                       and not urlsplit(item["url"]).query and not urlsplit(item["url"]).fragment
                       for item in locked[name].get("wheels", [])):
                raise ValueError("Wheel hash/size/filename is not a compatible frozen lock wheel.")
        _, _, metadata = strict_wheel(raw, path.name, pure=backend)
        if name == "amplifier-unified" and hashlib.sha256(metadata).hexdigest() != APP_METADATA_SHA256:
            raise ValueError("Original app wheel hash/size/METADATA mismatch.")
        packages[name] = {"version": version, "sha256": digest, "file": path.name, "bytes": len(raw)}
        bodies[path.name] = raw
    if packages.keys() != versions.keys():
        raise ValueError("Wheelhouse selection/budget mismatch.")
    return packages, bodies


def source_material(wheelhouse: Path, build_wheelhouse: Path, lock_raw: bytes,
                    production_raw: bytes, inventory: Path) -> tuple[dict, dict]:
    expected, locked = frozen_selection(lock_raw, production_raw)
    registry = {name: version for name, version in expected.items() if name not in GIT_SOURCES}
    packages, wheels = source_wheels(wheelhouse, registry, locked)
    build_packages, build_wheels = source_wheels(build_wheelhouse, BUILD_PACKAGES, locked, backend=True)
    identities = {name: {"version": item["version"], "sha256": item["sha256"]} for name, item in packages.items()}
    read_bounded(inventory, 262144)
    reviewed = checked_inventory(inventory, APP_SOURCE, identities)
    if json.loads(reviewed)["lockSha256"] != LOCK_SHA256:
        raise ValueError("Reviewed inventory frozen lock mismatch.")
    spec = {
        "schema": 1, "sourceMode": SOURCE_MODE, "source": APP_SOURCE, "version": APP_VERSION,
        "lockSha256": LOCK_SHA256, "productionRequirementsSha256": PRODUCTION_REQUIREMENTS_SHA256,
        "packages": packages, "buildPackages": build_packages, "expected": expected,
        "bootstrapHelpers": BOOTSTRAP_HELPERS,
        "git": {name: {**item, "version": expected[name]} for name, item in GIT_SOURCES.items()},
    }
    contents = {
        "uv.lock": lock_raw, "production-requirements.txt": production_raw, "inventory.json": reviewed,
        "source-spec.json": canonical_json(spec),
        "capsule.json": canonical_json({"source": APP_SOURCE, "version": APP_VERSION, "sourceMode": SOURCE_MODE}),
        "requirements.txt": ("\n".join(
            f"{name}=={item['version']} --hash=sha256:{item['sha256']}" for name, item in sorted(packages.items())
        ) + "\n").encode(),
        "git-requirements.txt": ("\n".join(
            git_requirement(name, item, extras=True) for name, item in sorted(GIT_SOURCES.items())
        ) + "\n").encode(),
        "build-constraints.txt": ("\n".join(
            f"{name}=={item['version']} --hash=sha256:{item['sha256']}"
            for name, item in sorted(build_packages.items())) + "\n").encode(),
    }
    contents.update({"wheels/" + name: raw for name, raw in wheels.items()})
    contents.update({"build-wheels/" + name: raw for name, raw in build_wheels.items()})
    return spec, contents


def build_source(wheelhouse: Path, output: Path, *, inventory: Path, source: str, version: str,
                 python: str, url: str, build_wheelhouse: Path | None, lock: Path | None,
                 production_requirements: Path | None) -> dict:
    if source != APP_SOURCE or version != APP_VERSION:
        raise ValueError("Pinned-vcs mode requires the frozen app source/version.")
    if any(item is None for item in (build_wheelhouse, lock, production_requirements)):
        raise ValueError("Pinned-vcs mode requires build wheelhouse, lock and production requirements.")
    _, contents = source_material(wheelhouse, build_wheelhouse, read_bounded(lock, 8 * 1024 * 1024),
                                  read_bounded(production_requirements, 65536), inventory)
    contents["source_install.py"] = read_bounded(Path(__file__), 1048576)
    contents["launch.py"] = read_bounded(Path(__file__).with_name("windows_prototype_launch.py"), 262144)
    if sum(map(len, contents.values())) > MAX_EXPANDED_BYTES:
        raise ValueError("Expanded capsule budget exceeded.")
    output.mkdir(parents=True, exist_ok=False)
    capsule = output / "windows-prototype.zip"
    with zipfile.ZipFile(capsule, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(contents.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            archive.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED)
    if capsule.stat().st_size > MAX_WHEEL_BYTES:
        raise ValueError("Capsule exceeds bootstrap download budget; retain failed output for review.")
    record = {
        "schemaVersion": 1, "status": "prototype", "sourceMode": SOURCE_MODE,
        "source": source, "version": version, "architecture": "x86_64", "python": python,
        "capsule": {"url": url, "sha256": sha256(capsule)},
    }
    manifest = output / "windows-prototype.json"
    manifest.write_bytes(canonical_json(record))
    return {"manifestSha256": sha256(manifest), "capsuleSha256": record["capsule"]["sha256"]}


def validate_source(payload: Path) -> dict:
    """Read-only pre-install check. Never imports application or executes code."""
    files = {
        "uv.lock", "production-requirements.txt", "inventory.json", "source-spec.json", "capsule.json",
        "requirements.txt", "git-requirements.txt", "build-constraints.txt", "source_install.py", "launch.py",
    }
    if (payload.is_symlink() or payload.is_junction() or not payload.is_dir()
            or {path.name for path in islice(payload.iterdir(), len(files) + 3)} != files | {"wheels", "build-wheels"}):
        raise ValueError("Source capsule contains missing or extra payload paths.")
    raw_files = {name: read_bounded(payload / name, 8 * 1024 * 1024 if name == "uv.lock" else 1048576)
                 for name in files}
    spec, contents = source_material(payload / "wheels", payload / "build-wheels", raw_files["uv.lock"],
                                    raw_files["production-requirements.txt"], payload / "inventory.json")
    for name in files - {"source_install.py", "launch.py"}:
        if raw_files[name] != contents[name]:
            raise ValueError("Source capsule derived files or source mode differ from frozen inputs.")
    if raw_files["source_install.py"] != read_bounded(Path(__file__), 1048576):
        raise ValueError("Source capsule checker differs from the running checker.")
    if sum(map(len, contents.values())) + len(raw_files["launch.py"]) + len(raw_files["source_install.py"]) > MAX_EXPANDED_BYTES:
        raise ValueError("Expanded capsule budget exceeded.")
    return spec


def audit_source(payload: Path, distributions=None) -> None:
    """Audit real installer-written PEP 610 records; never create or repair them."""
    spec = validate_source(payload)
    if distributions is None:
        from importlib.metadata import distributions as installed_distributions
        distributions = installed_distributions()
    installed = {}
    for distribution in islice(distributions, len(spec["expected"]) + 1):
        name = normalize(distribution.metadata["Name"] or "")
        if name in installed:
            raise ValueError("Duplicate installed distribution.")
        installed[name] = distribution
    if {name: item.version for name, item in installed.items()} != spec["expected"]:
        raise ValueError("Installed distribution set/versions differ from the 65 production inputs.")
    for name, distribution in installed.items():
        direct_raw = distribution.read_text("direct_url.json")
        if name not in GIT_SOURCES:
            if direct_raw is not None:
                raise ValueError("Registry/app installation has an unexpected direct origin.")
            continue
        origin = GIT_SOURCES[name]
        direct = json.loads(direct_raw) if direct_raw else {}
        if not isinstance(direct, dict):
            raise ValueError("Malformed installed Git origin.")
        keys = {"url", "vcs_info"} | ({"subdirectory"} if origin["subdirectory"] else set())
        vcs = direct.get("vcs_info", {})
        if (set(direct) != keys or direct.get("url") != origin["url"]
                or direct.get("subdirectory", "") != origin["subdirectory"]
                or not isinstance(vcs, dict) or not {"vcs", "commit_id"} <= vcs.keys()
                or not vcs.keys() <= {"vcs", "commit_id", "requested_revision"}
                or ("requested_revision" in vcs and not isinstance(vcs["requested_revision"], str))
                or vcs.get("vcs") != "git" or vcs.get("commit_id") != origin["commit"]):
            raise ValueError("Installed Git origin/commit/subdirectory differs from frozen source.")
    app_metadata = installed["amplifier-unified"].read_text("METADATA")
    if app_metadata is None or hashlib.sha256(app_metadata.encode("utf-8")).hexdigest() != APP_METADATA_SHA256:
        raise ValueError("Installed original app METADATA mismatch.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--validate-source", type=Path, metavar="PAYLOADDIR")
    checks.add_argument("--audit-source", type=Path, metavar="PAYLOADDIR")
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--inventory", type=Path,
                        help="Independently reviewed production closure/source/lock and actual wheel hashes")
    parser.add_argument("--output", type=Path, help="New, absent directory; never replace previous artifacts")
    parser.add_argument("--source-mode", choices=("offline", SOURCE_MODE), default="offline")
    parser.add_argument("--build-wheelhouse", type=Path)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--production-requirements", type=Path)
    for flag in ("source", "version", "python", "url"):
        parser.add_argument("--" + flag)
    args = parser.parse_args()
    if args.validate_source is not None:
        validate_source(args.validate_source)
        print("PINNED VCS SOURCE VALIDATION PASS")
        return
    if args.audit_source is not None:
        audit_source(args.audit_source)
        print("PINNED VCS SOURCE AUDIT PASS")
        return
    for flag in ("wheelhouse", "inventory", "output", "source", "version", "python", "url"):
        if getattr(args, flag) is None:
            parser.error("--" + flag + " is required for packaging")
    print(json.dumps(build(args.wheelhouse, args.output, inventory=args.inventory, source=args.source, version=args.version,
                           python=args.python, url=args.url, source_mode=args.source_mode,
                           build_wheelhouse=args.build_wheelhouse, lock=args.lock,
                           production_requirements=args.production_requirements), sort_keys=True))


if __name__ == "__main__":
    main()