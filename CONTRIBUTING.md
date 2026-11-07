# Contributing

Use the [source and ownership map](docs/source-context.md) to select relevant
authored code, contracts and tests, and distinguish generated output and retained
evidence. The path selector does not change packaging or release validation.

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
