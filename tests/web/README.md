# Unified web integration checks

This directory owns checks that need Unified's host, Python fixtures, canonical
shared-input producer, release assembly or examples. Its package and lock contain
explicit test tools. Browser source, client units, synthetic client-only journeys
and production build dependencies belong to
[amplifier-client-web](https://github.com/microsoft/amplifier-client-web).
See [component ownership and assembly](../../components/README.md).

From the Unified root, in the owned test environment:

```sh
python scripts/web_client_build.py prepare
npm ci --prefix .build/web-client
npm ci --prefix tests/web
npm test --prefix tests/web
python scripts/web_client_build.py build
npm run test:shell-browser --prefix tests/web
```

`npm test` here runs the host integration/producer unit files, not every browser
journey. Client units use `npm test --prefix .build/web-client`; built-asset checks
use `npm run test:artifact-assets --prefix .build/web-client` after building.
Select checks for changed behavior under the existing release policy. A command
listed here is not a record that it passed on the current source.

[browser-checks.json](browser-checks.json) is the host browser registry. The normal
runner composes it with the selected client's own registry:

```sh
python scripts/browser_check_runner.py --plan tests/web/browser-checks.json --output /owned/evidence/browser-checks
```

Host scripts run from the Unified root and client scripts from `.build/web-client`, unless a row explicitly
selects its working directory. Use the runner's help for modes, time budgets and coverage initialization;
it retains both registry identities in evidence.
Named host journeys are also available through this directory's package scripts.

Fixtures use synthetic state and an owned Python interpreter selected through
`fixture-python.mjs`; install matching Playwright Chromium or reuse the matching
owned browser cache. Preserve fixture lifecycle, cancellation and cleanup helpers
rather than starting a shared application. Renderer checks use the selected
client's dependency tree, not a second production dependency owner. Shared generic
helpers keep one implementation with injected test-framework facades.

Current source inspection belongs in the canonical client repository or its
selected delivery copy at `components/web-client`; `.build/web-client` is prepared,
disposable input, not an authoring location. Keep saved historical results bound
to their original paths and sources. After review, remove unneeded owned fixtures,
outputs and dependencies while retaining finite evidence and recoverable changes.
