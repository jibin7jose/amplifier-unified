"""Synthetic supply guards only; no uv, runtime imports, or fixture main."""
from copy import deepcopy
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tomllib
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import pytest


REVISION = "a" * 40
SOURCE = "https://example.invalid/approval?branch=main&subdirectory=modules/context"
CANONICAL = '''version = 1
revision = 3
requires-python = ">=3.13, <3.14"
resolution-markers = ["sys_platform == 'linux'"]

[[package]]
name = "synthetic-root"
version = "0.0.0"
source = { virtual = "." }
dependencies = [{ name = "canonical-dep" }]
[package.metadata]
requires-dist = [{ name = "canonical-dep", specifier = "==1.2.3" }]
provides-extras = ["retained-extra"]

[[package]]
name = "canonical-dep"
version = "1.2.3"
source = { registry = "https://pypi.org/simple" }
wheels = [{ url = "https://example.invalid/dep.whl", hash = "sha256:1234", size = 42 }]
'''
FIXTURES = (
    ("amplifier-module-context-omittedfixture", "context"),
    ("amplifier-module-provider-omittedfixture", "provider"),
    ("amplifier-module-hooks-omittedfixture", "hooks"),
)


@pytest.fixture(scope="module")
def probe():
    path = Path(__file__).parent / "fixtures/omitted_ref_worker_probe.py"
    spec = importlib.util.spec_from_file_location("supply_guard_fixture", path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "dont_write_bytecode", True):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def lock_case(tmp_path, probe):
    dependencies, requirements, packages = [], [], []
    for name, kind in FIXTURES:
        source = f"https://example.invalid/approval?branch=main&subdirectory=modules/{kind}"
        dependencies.append(f'{{ name = "{name}" }}')
        requirements.append(f'{{ name = "{name}", git = "{source}" }}')
        packages.append(f'''\n[[package]]
name = "{name}"
version = "0.1.0"
source = {{ git = "{source}#{REVISION}" }}
''')
    expanded = CANONICAL.replace(
        'dependencies = [{ name = "canonical-dep" }]',
        'dependencies = [{ name = "canonical-dep" }, ' + ', '.join(dependencies) + ']',
    ).replace(
        'specifier = "==1.2.3" }]',
        'specifier = "==1.2.3" }, ' + ', '.join(requirements) + ']',
    ) + ''.join(packages)
    original_path, expanded_path = tmp_path / "canonical.lock", tmp_path / "expanded.lock"
    # Expected graph comes from explicit input, never the production identity filter.
    original_graph = tomllib.loads(CANONICAL)["package"][1:]

    def verify(candidate=expanded, original=CANONICAL):
        original_path.write_text(original)
        expanded_path.write_text(candidate)
        result = probe.verify_expanded_lock(
            SimpleNamespace(runtime_lock=original_path), expanded_path,
            "synthetic-root", original_graph, REVISION,
        )
        assert original_path.read_text() == original
        assert expanded_path.read_text() == candidate
        return result

    return expanded, verify


def test_exact_expansion_preserves_canonical_rows_and_root_metadata(lock_case):
    _, verify = lock_case
    assert verify() == 5


@pytest.mark.parametrize("location", ["package", "requirement"])
@pytest.mark.parametrize("before,after", [
    ("https://example.invalid/approval", "https://example.invalid/other"),
    ("branch=main", "branch=HEAD"),
    ("branch=main", "rev=main"),
    ("modules/context", "modules/provider"),
])
def test_fixture_source_identity_is_exact(lock_case, location, before, after):
    expanded, verify = lock_case
    source = SOURCE + (f"#{REVISION}" if location == "package" else "")
    old = f'git = "{source}"'
    assert expanded.count(old) == 1
    with pytest.raises(AssertionError, match="Fixture source differs"):
        verify(expanded.replace(old, f'git = "{source.replace(before, after)}"'))


@pytest.mark.parametrize("location", ["package", "requirement"])
def test_wrong_commit_or_pinned_requirement_is_rejected(lock_case, location):
    expanded, verify = lock_case
    source = SOURCE + (f"#{REVISION}" if location == "package" else "")
    old = f'git = "{source}"'
    assert expanded.count(old) == 1
    with pytest.raises(AssertionError, match="Fixture source differs"):
        verify(expanded.replace(old, f'git = "{SOURCE}#{"b" * 40}"'))


@pytest.mark.parametrize("before,after,message", [
    ('version = "1.2.3"', 'version = "1.2.4"', "original package graph"),
    ('size = 42', 'size = 43', "original package graph"),
    ('revision = 3', 'revision = 4', "canonical lock policy"),
    ('requires-python = ">=3.13, <3.14"', 'requires-python = ">=3.12"', "canonical lock policy"),
    ('specifier = "==1.2.3"', 'specifier = ">=1.2.3"', "original root declaration"),
    ('provides-extras = ["retained-extra"]', 'provides-extras = []', "original root declaration"),
    ('source = { virtual = "." }', 'source = { editable = "." }', "original root declaration"),
])
def test_canonical_rows_and_metadata_cannot_change(lock_case, before, after, message):
    expanded, verify = lock_case
    assert expanded.count(before) == 1
    with pytest.raises(AssertionError, match=message):
        verify(expanded.replace(before, after))


@pytest.mark.parametrize("document", ["original", "expanded"])
def test_duplicate_canonical_row_is_rejected(lock_case, document):
    expanded, verify = lock_case
    duplicate = '\n[[package]]\nname = "canonical-dep"\nversion = "1.2.3"\n'
    with pytest.raises(AssertionError, match="duplicate package rows"):
        if document == "original":
            verify(original=CANONICAL + duplicate)
        else:
            verify(expanded + duplicate)


def test_plain_path_accepts_regular_file_and_tree(tmp_path, probe):
    file = tmp_path / "lock"
    file.write_text("synthetic")
    assert probe.plain_path(file) == file.resolve()
    assert probe.plain_path(tmp_path, tree=True) == tmp_path.resolve()


@pytest.mark.parametrize("location", ["leaf", "ancestor", "tree"])
def test_plain_path_refuses_symlinks(tmp_path, probe, location):
    directory = tmp_path / "supply"
    directory.mkdir()
    file = directory / "lock"
    file.write_text("synthetic")
    link = tmp_path / "link"
    link.symlink_to(file if location == "leaf" else directory,
                    target_is_directory=location != "leaf")
    if location == "tree":
        target, tree = tmp_path, True
    else:
        target, tree = (link if location == "leaf" else link / "lock"), False
    with pytest.raises(AssertionError, match="symlink"):
        probe.plain_path(target, tree=tree)


@pytest.mark.parametrize("origin_kind", ["source", "installed", "build-view", "wrong-build", "unrelated", "wrong-source", "wrong-bytes"])
def test_adoption_binds_both_valid_origins_to_installed_source(tmp_path, probe, origin_kind):
    prefix = tmp_path / "project/.venv"
    source_root = tmp_path / "store/objects/repository" / REVISION
    prefix.mkdir(parents=True)
    rows, graph = [], []
    payload = b"PAYLOAD = 1\n"
    payload_hash = hashlib.sha256(payload).hexdigest()
    for name, kind in FIXTURES:
        module = name.replace("-", "_")
        source = source_root / "modules" / kind
        package = source / module
        installed = prefix / "lib/python3.13/site-packages" / module
        for target in (package, installed):
            target.mkdir(parents=True)
            (target / "__init__.py").write_text("# synthetic module\n")
            (target / "_proof.py").write_text("# proof\n")
            (target / "_payload.py").write_bytes(payload)
        installed_source = source
        if origin_kind in ("build-view", "wrong-build"):
            build = tmp_path / ("store/builds" if origin_kind == "build-view" else "elsewhere") / "build-id"
            installed_source = build / "modules" / kind
            target = installed_source / module
            target.mkdir(parents=True)
            for filename in ("__init__.py", "_proof.py", "_payload.py"):
                (target / filename).write_bytes((package / filename).read_bytes())
            (build / ".amplifier_cache_meta.json").write_text('{"buildInput":true}')
        origin = installed if origin_kind == "installed" else package
        if origin_kind == "unrelated":
            origin = tmp_path / "unrelated" / module
            origin.mkdir(parents=True)
            (origin / "__init__.py").write_text("# synthetic module\n")
            (origin / "_payload.py").write_bytes(payload)
        if origin_kind == "wrong-bytes":
            (installed / "__init__.py").write_text("# different bytes\n")
        rows.append({"kind": "worker", "event": "mount", "pid": 123,
                     "module": module, "generation": "g", "revision": REVISION,
                     "payloadSha256": payload_hash, "prefix": str(prefix),
                     "executable": str(prefix / "bin/python"), "origin": str(origin / "__init__.py")})
        graph.append({"name": name, "path": str(installed_source),
                      "cacheSource": {"url": probe.URL, "revision": "b" * 40 if origin_kind == "wrong-source" else REVISION,
                                      "subdirectory": "modules/" + kind, "dirty": False}})
    kwargs = dict(generation="g", revision=REVISION, payload_hash=payload_hash,
                  prefix=prefix, pid=123, source_root=source_root, graph=graph)
    if origin_kind in ("source", "installed", "build-view"):
        assert probe.adopted(rows, **kwargs) == rows
    else:
        with pytest.raises(AssertionError):
            probe.adopted(rows, **kwargs)


CANONICAL_GIT_URL = "https://github.com/microsoft/amplifier-synthetic.git"
CANONICAL_REF_CASES = [
    ("branch=main", "refs/heads/main"),
    ("rev=main", "refs/heads/main"),
    ("tag=main", "refs/tags/main"),
    (f"rev={REVISION}", REVISION),
]


def canonical_package(policy="branch=main", *, url=CANONICAL_GIT_URL,
                      revision=REVISION, name="synthetic-git"):
    return {"name": name, "source": {"git": f"{url}?{policy}#{revision}"}}


def canonical_transport(ref="refs/heads/main", *, url=CANONICAL_GIT_URL):
    # Expected identity is derived from explicit input, not the production filter.
    return {"url": url, "ref": ref, "revision": REVISION,
            "mirrorrelative": "canonical-git/" + hashlib.sha256(url.encode()).hexdigest() + ".git"}


@pytest.mark.parametrize("policy,ref", CANONICAL_REF_CASES)
def test_canonical_transport_preserves_exact_authored_ref(probe, policy, ref):
    assert probe.canonical_git_transports([canonical_package(policy)]) == [canonical_transport(ref)]


def test_canonical_transport_deduplicates_subdirectories_not_repository_urls(probe):
    other_url = "https://github.com/microsoft/amplifier-other"
    graph = [
        canonical_package("branch=main&subdirectory=modules/first", name="first"),
        canonical_package("rev=main&subdirectory=modules/second", name="second"),
        canonical_package("branch=main&subdirectory=modules/first", name="duplicate"),
        canonical_package(url=other_url, name="first"),
        {"name": "wheel", "source": {"registry": "https://example.invalid/simple"}},
    ]
    assert probe.canonical_git_transports(graph) == [
        canonical_transport(url=other_url), canonical_transport(),
    ]


@pytest.mark.parametrize("policy,revision", [
    ("branch=main", "b" * 40),
    ("tag=main", REVISION),
])
@pytest.mark.parametrize("reverse", [False, True])
def test_canonical_transport_refuses_conflicting_commits_or_ref_kinds(probe, policy, revision, reverse):
    graph = [canonical_package("rev=main"), canonical_package(policy, revision=revision)]
    with pytest.raises(AssertionError, match="conflicting kinds or locked commits"):
        probe.canonical_git_transports(graph[::-1] if reverse else graph)


@pytest.mark.parametrize("url", [
    "https://example.invalid/microsoft/amplifier-synthetic",
    "https://github.com.example.invalid/microsoft/amplifier-synthetic",
    "https://github.com/another-owner/amplifier-synthetic",
    "https://github.com/microsoft/unrelated",
    CANONICAL_GIT_URL + "/nested",
    "https://github.com/microsoft/../amplifier-synthetic",
    "file:///tmp/amplifier-synthetic.git",
])
def test_canonical_transport_refuses_noncanonical_host_repository_or_path(probe, url):
    with pytest.raises(AssertionError, match="owned URL and exact commit"):
        probe.canonical_git_transports([canonical_package(url=url)])


@pytest.mark.parametrize("policy,message", [
    ("", "ambiguous ref policy"),
    ("subdirectory=modules/first", "ambiguous ref policy"),
    ("branch=", "ambiguous ref policy"),
    ("branch=main&branch=main", "ambiguous ref policy"),
    ("branch=main&rev=main", "ambiguous ref policy"),
    ("branch=main&unexpected=value", "ambiguous ref policy"),
    ("branch=HEAD", "only named main"),
    ("rev=HEAD", "only named main"),
    ("tag=HEAD", "only named main"),
    ("branch=develop", "only named main"),
    (f"branch={REVISION}", "exact ref differs"),
    (f"tag={REVISION}", "exact ref differs"),
    ("rev=" + "b" * 40, "exact ref differs"),
])
def test_canonical_transport_refuses_missing_ambiguous_or_invented_policy(probe, policy, message):
    with pytest.raises(AssertionError, match=message):
        probe.canonical_git_transports([canonical_package(policy)])


@pytest.mark.parametrize("revision", ["", "HEAD", "a" * 39])
def test_canonical_transport_requires_exact_locked_commit(probe, revision):
    with pytest.raises(AssertionError, match="owned URL and exact commit"):
        probe.canonical_git_transports([canonical_package(revision=revision)])


@pytest.fixture
def canonical_mirror_case(tmp_path):
    row = canonical_transport()
    mirror = tmp_path / row["mirrorrelative"]
    (mirror / "objects/info").mkdir(parents=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main\n")
    return tmp_path, row, mirror


def test_canonical_mirror_accepts_private_regular_tree(probe, canonical_mirror_case):
    root, row, mirror = canonical_mirror_case
    assert probe.canonical_mirror(root, row) == mirror.resolve()


@pytest.mark.parametrize("location", ["leaf", "ancestor", "tree"])
def test_canonical_mirror_refuses_symlinks(probe, canonical_mirror_case, location):
    root, row, mirror = canonical_mirror_case
    if location == "tree":
        (mirror / "linked-head").symlink_to(mirror / "HEAD")
    else:
        target = mirror if location == "leaf" else mirror.parent
        moved = root / "moved"
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
    with pytest.raises(AssertionError, match="symlink"):
        probe.canonical_mirror(root, row)


@pytest.mark.parametrize("relative", [
    "objects/info/alternates", "objects/info/http-alternates", "info/grafts",
    "commondir", "shallow", ".git", ".venv",
])
def test_canonical_mirror_refuses_external_or_incomplete_state(probe, canonical_mirror_case, relative):
    root, row, mirror = canonical_mirror_case
    target = mirror / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if relative in (".git", ".venv"):
        target.mkdir()
    else:
        target.write_text("")  # Even an empty marker must fail closed.
    with pytest.raises(AssertionError, match="not self-contained"):
        probe.canonical_mirror(root, row)


def test_canonical_mirror_refuses_hardlink_outside_tree(probe, canonical_mirror_case):
    root, row, mirror = canonical_mirror_case
    (root / "shared-head").hardlink_to(mirror / "HEAD")
    with pytest.raises(AssertionError, match="shared hardlinks"):
        probe.canonical_mirror(root, row)


@pytest.fixture
def canonical_process(tmp_path):
    """All process responses are synthetic; this never invokes a subprocess."""
    def make(refs, *, peeled=REVISION):
        mirror = tmp_path / canonical_transport()["mirrorrelative"]
        clone = ("git", "clone", "--bare", "--no-hardlinks", CANONICAL_GIT_URL, str(mirror))
        identity = ("git", "rev-parse", "--absolute-git-dir",
                    "--is-bare-repository", "--is-shallow-repository", REVISION + "^{commit}")
        list_refs = ("git", "for-each-ref", "--format=%(refname)")
        answers = {
            identity: "\n".join((str(mirror), "true", "false", REVISION)),
            list_refs: "\n".join(refs),
            ("git", "update-ref", "refs/heads/main", REVISION): "",
            ("git", "rev-parse", "refs/tags/main^{commit}"): peeled,
        }

        async def simulate(*argv, **kwargs):
            if argv == clone:
                mirror.mkdir()
                return ""
            assert argv in answers, f"Unexpected process call: {argv}"
            return answers[argv]

        process = AsyncMock(side_effect=simulate)
        expected = [call(*clone, timeout=900), call(*identity, cwd=mirror), call(*list_refs, cwd=mirror)]
        return process, mirror, expected

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,ref", CANONICAL_REF_CASES)
async def test_prepare_canonical_git_clones_exact_url_and_binds_only_existing_ref(
    tmp_path, probe, canonical_process, policy, ref,
):
    refs = [ref] if ref.startswith("refs/") else ["refs/heads/other"]
    process, mirror, expected = canonical_process(refs)
    graph = [canonical_package(policy + "&subdirectory=modules/first", name="first"),
             canonical_package(policy + "&subdirectory=modules/second", name="second")]
    await probe.prepare_canonical_git(tmp_path, graph, process)
    if ref == "refs/heads/main":
        expected.append(call("git", "update-ref", ref, REVISION, cwd=mirror))
    elif ref == "refs/tags/main":
        expected.append(call("git", "rev-parse", ref + "^{commit}", cwd=mirror))
    # Exact call list forbids alias creation, extra clones, or a substituted URL.
    assert process.call_args_list == expected
    assert process.await_args_list == expected
    receipt = tmp_path / "canonical-transports.json"
    assert json.loads(receipt.read_text()) == [canonical_transport(ref)]
    assert receipt.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,refs,peeled,message", [
    ("branch=main", ["HEAD", "refs/heads/other"], REVISION, "named ref is absent"),
    ("rev=main", ["HEAD", "refs/heads/other"], REVISION, "named ref is absent"),
    ("branch=main", ["refs/heads/main", "refs/tags/main"], REVISION, "ambiguous ref kind"),
    ("rev=main", ["refs/heads/main", "refs/tags/main"], REVISION, "ambiguous ref kind"),
    ("tag=main", ["refs/heads/main"], REVISION, "named ref is absent"),
    ("tag=main", ["refs/tags/main"], "b" * 40, "tag differs"),
])
async def test_prepare_canonical_git_refuses_missing_ambiguous_or_wrong_main(
    tmp_path, probe, canonical_process, policy, refs, peeled, message,
):
    process, mirror, expected = canonical_process(refs, peeled=peeled)
    with pytest.raises(AssertionError, match=message):
        await probe.prepare_canonical_git(tmp_path, [canonical_package(policy)], process)
    if message == "tag differs":
        expected.append(call("git", "rev-parse", "refs/tags/main^{commit}", cwd=mirror))
    assert process.call_args_list == expected  # No manufactured main or HEAD alias.
    assert process.await_args_list == expected
    assert not (tmp_path / "canonical-transports.json").exists()


@pytest.mark.parametrize("policy,_", CANONICAL_REF_CASES)
@pytest.mark.parametrize("subdirectory", ["", "modules/context"])
def test_canonical_lock_source_retains_semantics(probe, policy, _, subdirectory):
    query = policy + (f"&subdirectory={subdirectory}" if subdirectory else "")
    kind, ref = policy.split("=", 1)
    assert probe.canonical_lock_source(canonical_package(query)["source"]["git"]) == {
        "url": CANONICAL_GIT_URL, "ref": ref, "refKind": kind,
        "subdirectory": subdirectory, "revision": REVISION,
    }


@pytest.mark.parametrize("source", [
    None,
    CANONICAL_GIT_URL + "?rev=main",
    CANONICAL_GIT_URL + "?rev=main#HEAD",
    CANONICAL_GIT_URL + f"#{REVISION}",
    CANONICAL_GIT_URL + f"?rev=#{REVISION}",
    CANONICAL_GIT_URL + f"?rev=main&branch=main#{REVISION}",
    CANONICAL_GIT_URL + f"?rev=main&rev=main#{REVISION}",
    CANONICAL_GIT_URL + f"?rev=main&subdirectory=#{REVISION}",
    CANONICAL_GIT_URL + f"?rev=main&other=value#{REVISION}",
])
def test_canonical_lock_source_refuses_incomplete_or_ambiguous_identity(probe, source):
    with pytest.raises(AssertionError, match="Canonical Git lock source"):
        probe.canonical_lock_source(source)


@pytest.fixture
def canonical_generation_case():
    def make(kind="rev", pinned=True):
        git = {**canonical_package(f"{kind}=main&subdirectory=modules/context"), "version": "1.2.3"}
        core = {"name": "amplifier-core", "version": "2.0.0",
                "source": {"registry": "https://pypi.org/simple"}}
        graph = [git, core]
        source = {"url": CANONICAL_GIT_URL, "ref": REVISION if pinned else "main",
                  "subdirectory": "modules/context", "revision": REVISION}
        origin = {**source, "kind": ("tag-reference" if kind == "tag" else "unresolved-reference")
                  if pinned else "prepared-manifest"}
        if pinned:
            origin["originalRef"] = "main"
        policies = {"synthetic-git": {**source, "policyOrigin": origin}}
        return graph, {"package": deepcopy(graph)}, policies
    return make


@pytest.mark.parametrize("kind,pinned", [
    ("branch", False), ("rev", False), ("rev", True), ("tag", True),
])
@pytest.mark.parametrize("normalize", [False, True])
def test_canonical_generation_accepts_bound_policy_forms_without_mutation(
    probe, canonical_generation_case, kind, pinned, normalize,
):
    graph, lock, policies = canonical_generation_case(kind, pinned)
    if normalize:
        lock["package"][0]["source"]["git"] = canonical_package(
            f"rev={REVISION}&subdirectory=modules/context")["source"]["git"]
    lock["package"][0]["metadata"] = {"resolver-only": True}
    lock["package"].reverse()
    lock["package"].append({"name": "synthetic-root", "source": {"virtual": "."}})
    inputs = deepcopy((graph, lock, policies))
    semantic = [
        {"name": "amplifier-core", "version": "2.0.0", "source": {"registry": "https://pypi.org/simple"}},
        {"name": "synthetic-git", "version": "1.2.3", "source": {
            "url": CANONICAL_GIT_URL, "ref": "main", "refKind": kind,
            "subdirectory": "modules/context", "revision": REVISION}},
    ]
    assert probe.verify_canonical_generation(graph, lock, policies) == {
        "packageCount": 2, "gitPackageCount": 1, "registryPackageCount": 1,
        "semanticSha256": hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest(),
    }
    assert (graph, lock, policies) == inputs


@pytest.mark.parametrize("target,path,value,message", [
    ("lock", ("package", 0, "version"), "1.2.4", "changed version"),
    ("lock", ("package", 1, "version"), "2.0.1", "changed version"),
    ("lock", ("package", 0, "source"), {"registry": "https://pypi.org/simple"}, "changed source kind"),
    ("lock", ("package", 1, "source"), {"git": SOURCE + f"#{REVISION}"}, "registry package changed origin"),
    ("lock", ("package", 1, "source", "registry"), "https://example.invalid/simple", "registry package changed origin"),
    ("policies", ("synthetic-git",), None, "policy changed repository"),
    ("policies", ("synthetic-git", "policyOrigin"), None, "origin is not bound"),
    ("policies", ("synthetic-git", "policyOrigin", "originalRef"), None, "lost its authored ref semantics"),
    ("policies", ("synthetic-git", "policyOrigin", "originalRef"), "HEAD", "lost its authored ref semantics"),
    ("policies", ("synthetic-git", "policyOrigin", "kind"), "prepared-manifest", "lost its authored ref semantics"),
    ("policies", ("synthetic-git", "policyOrigin", "kind"), "", "origin is not bound"),
    ("policies", ("synthetic-git", "policyOrigin", "ref"), "main", "origin is not bound"),
])
def test_canonical_generation_refuses_changed_rows_or_unbound_policy(
    probe, canonical_generation_case, target, path, value, message,
):
    graph, lock, policies = canonical_generation_case()
    node = {"lock": lock, "policies": policies}[target]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(AssertionError, match=message):
        probe.verify_canonical_generation(graph, lock, policies)


@pytest.mark.parametrize("field,before,after", [
    ("url", CANONICAL_GIT_URL, "https://github.com/microsoft/amplifier-other"),
    ("subdirectory", "modules/context", "modules/provider"),
    ("revision", REVISION, "b" * 40),
])
@pytest.mark.parametrize("target", ["lock", "policy", "origin"])
def test_canonical_generation_binds_every_source_identity_field(
    probe, canonical_generation_case, field, before, after, target,
):
    graph, lock, policies = canonical_generation_case()
    if target == "lock":
        source = lock["package"][0]["source"]
        source["git"] = source["git"].replace(before, after)
    else:
        node = policies["synthetic-git"]
        if target == "origin":
            node = node["policyOrigin"]
        node[field] = after
    with pytest.raises(AssertionError, match="changed repository|origin is not bound"):
        probe.verify_canonical_generation(graph, lock, policies)


@pytest.mark.parametrize("defect,message", [
    ("missing-row", "missing or ambiguous"), ("duplicate-row", "missing or ambiguous"),
    ("duplicate-original", "missing or ambiguous"), ("missing-map", "no source policy map"),
    ("wrong-lock-ref", "lost the authored ref"), ("wrong-lock-kind", "lost the authored ref"),
    ("branch-as-pin", "lost its authored ref semantics"),
    ("tag-as-main", "lost its authored ref semantics"), ("wrong-original-core", "original registry origin"),
])
def test_canonical_generation_refuses_identity_and_policy_substitution(
    probe, canonical_generation_case, defect, message,
):
    graph, lock, policies = canonical_generation_case(
        "branch" if defect == "branch-as-pin" else "tag" if defect == "tag-as-main" else "rev",
        pinned=defect != "tag-as-main",
    )
    if defect == "missing-row":
        lock["package"].pop(0)
    elif defect == "duplicate-row":
        lock["package"].append(deepcopy(lock["package"][0]))
    elif defect == "duplicate-original":
        graph.append(deepcopy(graph[0]))
    elif defect == "missing-map":
        policies = None
    elif defect == "wrong-lock-ref":
        lock["package"][0]["source"]["git"] = lock["package"][0]["source"]["git"].replace("rev=main", "rev=HEAD")
    elif defect == "wrong-lock-kind":
        lock["package"][0]["source"]["git"] = lock["package"][0]["source"]["git"].replace("rev=main", "tag=main")
    elif defect == "wrong-original-core":
        for rows in (graph, lock["package"]):
            rows[1]["source"] = {"registry": "https://example.invalid/simple"}
    with pytest.raises(AssertionError, match=message):
        probe.verify_canonical_generation(graph, lock, policies)


@pytest.fixture
def canonical_consumer(tmp_path, probe, monkeypatch):
    root, supply = tmp_path / "consumer", tmp_path / "supply"
    mirror = supply / canonical_transport()["mirrorrelative"]
    mirror.mkdir(parents=True)
    for key, value in {"GIT_ALLOW_PROTOCOL": "file", "GIT_CONFIG_COUNT": "0",
                       "GIT_CONFIG_GLOBAL": str(root / "gitconfig")}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(probe.shutil, "which", lambda name: str(root / "bin" / name))
    return root, supply, mirror


@pytest.mark.asyncio
@pytest.mark.parametrize("ref,output", [
    ("refs/heads/main", f"{REVISION}\trefs/heads/main\n"),
    ("refs/tags/main", f"{REVISION}\trefs/tags/main\n"),
    ("refs/tags/main", f"{'b' * 40}\trefs/tags/main\n{REVISION}\trefs/tags/main^{{}}\n"),
    (REVISION, f"{REVISION}\t{REVISION}\n"),
])
async def test_canonical_transport_verification_reads_actual_wrapper_and_exact_ref(
    probe, canonical_consumer, ref, output,
):
    root, supply, mirror = canonical_consumer
    process = AsyncMock(side_effect=[CANONICAL_GIT_URL, output])
    assert await probe.verify_canonical_transports(root, supply, [canonical_transport(ref)], process) == {
        "verifiedTransportRefCount": 1,
    }
    expected = [
        call("git", "remote", "get-url", "origin", cwd=mirror, timeout=15),
        call("git", "ls-remote", "--exit-code", CANONICAL_GIT_URL, ref, ref + "^{}", timeout=15),
    ]
    assert process.call_args_list == process.await_args_list == expected  # No alternate executable/env or HEAD.


@pytest.mark.asyncio
@pytest.mark.parametrize("ref,output,message", [
    ("refs/heads/main", "", "invalid ref evidence"),
    ("refs/heads/main", "not-a-sha\trefs/heads/main", "invalid ref evidence"),
    ("refs/heads/main", f"{REVISION} refs/heads/main", "invalid ref evidence"),
    ("refs/heads/main", f"{'b' * 40}\trefs/heads/main", "exact locked commit"),
    ("refs/heads/main", f"{REVISION}\tHEAD", "exact locked commit"),
    ("refs/heads/main", f"{REVISION}\trefs/heads/main\n" * 2, "exact locked commit"),
    ("refs/heads/main", f"{REVISION}\trefs/heads/main\n{REVISION}\trefs/heads/main^{{}}", "exact locked commit"),
    ("refs/tags/main", f"{REVISION}\trefs/tags/main\n{'b' * 40}\trefs/tags/main^{{}}", "exact locked commit"),
    ("refs/tags/main", f"{REVISION}\trefs/tags/main^{{}}", "exact locked commit"),
    (REVISION, "", "invalid ref evidence"),
    (REVISION, f"{REVISION}\trefs/heads/main", "exact locked commit"),
])
async def test_canonical_transport_verification_refuses_unadvertised_or_false_evidence(
    probe, canonical_consumer, ref, output, message,
):
    root, supply, mirror = canonical_consumer
    process = AsyncMock(side_effect=[CANONICAL_GIT_URL, output])
    with pytest.raises(AssertionError, match=message):
        await probe.verify_canonical_transports(root, supply, [canonical_transport(ref)], process)
    assert process.call_args_list == process.await_args_list == [
        call("git", "remote", "get-url", "origin", cwd=mirror, timeout=15),
        call("git", "ls-remote", "--exit-code", CANONICAL_GIT_URL, ref, ref + "^{}", timeout=15),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["GIT_ALLOW_PROTOCOL", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "wrapper", "origin"])
async def test_canonical_transport_verification_requires_isolated_wrapper_and_authored_origin(
    probe, canonical_consumer, monkeypatch, defect,
):
    root, supply, mirror = canonical_consumer
    process = AsyncMock(return_value="https://example.invalid/alias")
    if defect == "wrapper":
        monkeypatch.setattr(probe.shutil, "which", lambda name: "/usr/bin/git")
    elif defect != "origin":
        monkeypatch.setenv(defect, "incorrect")
    message = "authored canonical origin" if defect == "origin" else "isolated Git wrapper"
    with pytest.raises(AssertionError, match=message):
        await probe.verify_canonical_transports(root, supply, [canonical_transport()], process)
    expected = [call("git", "remote", "get-url", "origin", cwd=mirror, timeout=15)] if defect == "origin" else []
    assert process.call_args_list == process.await_args_list == expected
