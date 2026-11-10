# Stories joined workflow owner

This optional integration closes the outer-worker and native MCP App boundary
left untested by the narrower #712 permission/child checks. It uses synthetic
content and a deterministic provider driving the **actual Work loop and
RuntimeManager worker**. It does not replace the resumed-child ceiling tests.

## Ownership and execution

The Smart Tools integration owner runs this manual lane in an owned DTU when
changing task-storage/file policy, native worker composition, Smart Tools calls
or receipts, MCP App review/reconnect/export, or the qualified Stories source.
Run it before calling that integration qualified, and after a release affecting
those boundaries. Record its result separately from publication. It is not a
new all-release gate or a nightly job. Stories is optional and its independent
Agent dependency is not a Unified Core/Foundation dependency.

This is an executable owner, not a skipped pytest: missing source, wrong source
revision, installed-source mismatch, missing MCP/browser packages, wrong DTU,
or any failed assertion exits nonzero. An owner who cannot run it records
**unqualified**, with the prerequisite failure; the ordinary green suite cannot
substitute for it. `result.json` is written only for a begun run; absence is not
success. The preflight records the installed service build and dependency
identities. Keep the publication/source receipt with the report.

Inside the owned DTU, prepare a clean Stories checkout at an explicit revision,
its private `.venv` (including the `mcp` extra), the existing private Unified
Work runtime, and Playwright/Chromium. Do not point this at a user's active app.
The runner creates a new chat, workspace, task allocation and MCP registration;
it neither installs dependencies nor restarts the service. The Work bundle and
supported Agent revision are explicit constants in the runner. Qualify a new
revision deliberately rather than silently replacing either.

```sh
python scripts/stories_acceptance/run.py \
  --owned-dtu "$OWNED_DTU" --url http://127.0.0.1:8941 \
  --token-file /owned/app/config/auth/control-token \
  --app-db /owned/app/app.sqlite3 \
  --state /owned/evidence/stories-run-1 --fixture /owned/fixtures/stories-run-1 \
  --stories-source /owned/sources/stories --stories-revision "$STORIES_SHA" \
  --node /owned/node/bin/node \
  --playwright /owned/unified/tests/web/node_modules/playwright/index.mjs
```

`--state` and `--fixture` must not already exist. The caller chooses browser
binaries using `PLAYWRIGHT_BROWSERS_PATH`. Optional `AMPLIFIER_CHROMIUM_ARGS` is
a JSON array recorded in preflight; this ARM DTU needed `--no-sandbox`,
`--single-process`, `--no-zygote`, `--disable-gpu`. That workaround does not
qualify standard multiprocess Chromium or native desktop dialogs.

## Checklist and interpretation

| ID | Required witness | Current result / desired result |
| --- | --- | --- |
| ST-01 | Exact clean Stories source and installed Agent/MCP provenance; CLI manifest without models | Frozen source and repaired fresh-install candidate qualified; future source must pass same preflight |
| ST-02 | Actual worker allocates inside execution workspace; native writer and reader use `library-context.json` | Passed; no grant or allowlist expansion, sibling write denied with `file_source_access` and no effect |
| ST-03 | Same worker imports via `app_control` and real stdio Stories MCP, then reads retained result/story | Passed; exactly one retained import and the same story identity |
| ST-04 | Packaged Stories MCP App renders the retained storyboard in Unified; browser exports ZIP | Passed; exported structured panels exactly equal original native-file input |
| ST-05 | Disconnect/reconnect and reload reopen retained content without another import | Passed; read-only backend operation receipt comparison requires exactly one unchanged host import receipt (IDs, hashes and bounded receipts retained), plus browser dispatch observation |
| ST-06 | No browser errors; evidence names source/build, time, current limits | Passed in Linux ARM headless context; retain that scope on future reports |
| ST-07 | Open while MCP is offline, explicitly reconnect, and recover in the same iframe without reload; later reconnect preserves initialized edits | Newly discovered failure on Stories `c31ab746`: host reconnect succeeds but its initial read error remains. Separate Stories recovery fix required; ST-05's initialized reconnect pass does not qualify this case. Retry only the failed read, never an import or other mutation. |

Around 20 seconds was observed for the joined warm-runtime synthetic run;
preparation time is separate. Desired result: correct bounded work, no replay,
no unexpected permission broadening, and no unexplained increase over the
same-runtime baseline. This is not a paid-model latency target or a scale test.

The source-specific [qualification receipt](stories-joined-20261016.json)
preserves the original install failure separately. Stories' documented Git
installation followed Agent main to a new incompatible monorepo root. Pinning
Stories to its already-tested Agent revision restores its supported API; moving
to Agent 0.23 remains a separate Stories migration. A successful frozen-lock
run alone must not be presented as a successful fresh installation.

## Retention and limits

Keep one synthetic chat/store and its preview while a reviewer needs it.
Afterward disconnect/remove the **recorded** MCP registration, archive the owned
chat, and retire its owned task allocation/fixture only after confirming no
worker or MCP process uses it. Keep `preflight.json`, `result.json`, the screenshot,
export checksum and failure receipts. Never clean another project's state.

This lane does not prove model-generated content, source-truth review, speech,
credential setup, OS save/permission dialogs, mobile/Windows/macOS backend,
or inherited permission expansion for an already-resumed child. No grant is
needed for the fixed workspace-local allocation. Exact external-directory grant
and saved child ceilings retain their separate #797/#799/#800 owners. It also
does not imply that all standard UX residual outcomes have passed.
