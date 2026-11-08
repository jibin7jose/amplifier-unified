# Contributing

Use the [source and ownership map](docs/source-context.md) to select relevant
authored code, contracts and tests, and distinguish generated output and retained
evidence. The path selector does not change packaging or release validation.

## Development setup

A raw Git checkout is a development source tree, distinct from a published source
archive. Use Python 3.13, uv and Node.js 22 with npm in an owned test environment,
following the workspace's DTU rules. Prepare dependencies once, then build the
client after relevant frontend or package-version changes:

```sh
uv sync --locked --group dev --group artifacts
npm ci --prefix frontend
npm run build --prefix frontend
```

Reuse prepared dependencies while their lockfiles match. The canonical frontend
build writes `amplifier_web/static`; keep that generated output committed for now.
Do not edit minified assets. Follow the [release preparation and asset recovery
guidance](docs/validation/release-qualification.md#preparing-a-version-change) when
moving an exact build from its owned environment back to the source checkout.

Published wheels **and source archives** already contain this complete built UI,
including vendor assets and licenses. Building a wheel from a released source
archive uses the declared Python build backend, without Node, npm or a frontend
download. It still requires the declared Python build dependencies; installation
resolves the normal application/runtime dependencies. Release packaging verifies
the same static file set and bytes in both formats before publishing.

## Contribution guidance

For behavior or architecture proposals, use [vision and contract guidance](docs/vision-and-contracts.md).
Keep one owner per promise, distinguish proposed behavior from qualified behavior,
and connect changed promises to focused checks under the existing release policy.

Keep repository content useful without exposing a contributor's environment.
This applies to code, fixtures, documentation, screenshots, logs, issues and
pull-request descriptions.

- Use synthetic people, host labels, paths and data in examples and tests.
  Keep private machine names, people's real names, account home directories,
  private service addresses and credentials out of repository content.
- GitHub aliases, public repository links and required license/copyright
  attribution may remain. Never remove third-party attribution when sanitizing.
- Keep exact private resource mappings, access details and cleanup commands in
  the owner's private operational record outside Git. Public evidence should
  retain source revisions, platform classes, results and limitations.
- Inspect screenshots and diagnostics before publishing. Prefer a synthetic
  reproduction; redact private identifiers without changing the result claimed.
- Check tracked filenames as well as file contents before committing. A cleanup
  of the current tree does not remove information from published Git history;
  report that limitation instead of silently rewriting shared history.

For performance-sensitive changes, use [performance validation](docs/validation/performance.md)
at design and review. Describe the affected operation, scaling inputs, measured
budget and semantic checks in the PR; distinguish source tests from installed or
live evidence. Select checks for the changed risk under the
[release policy](docs/validation/release-qualification.md#local-first-fast-publication-policy-updated-november-4-2026).

For release acceptance, use the [platform and first-use checklist](docs/validation/release-acceptance.md).

Keep native test dependencies in the development group. When a fixture package
and its dependency declare the same source, align their authored refs and retain
the exact qualified commits in `uv.lock`; do not resolve a test-only conflict with
a root `[tool.uv].override-dependencies` entry, which affects the whole resolver.
Check the shipped-source policy and affected real native fixtures after changing
these declarations. Runtime selections retain their separate source policy.

For changes to cross-surface artifact controls, routing or presentation actions,
update the affected [control inventory entry](docs/canvas.md#artifact-controls-and-reuse)
or explain why its contract is unchanged, and extend the linked focused test.
Reuse the existing card and viewer owners; do not add parallel controls merely
because the content has a different format.
