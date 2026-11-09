# Public-readiness stabilization

Baseline: main 0.20.83, `8c2b0e726724941416a699b2f0a258b4c223d470`.
Released: [0.20.86](https://github.com/microsoft/amplifier-unified/releases/tag/v0.20.86),
`687fb99f26ee7cf3d131419d55657f3b8efd438a`. Acceptance completed October 8, 2026.
Scope: existing main application; AHP/ACP remains a separate secondary track.

This is a historical acceptance record, not current release instructions. Follow
the [Fast Release Guide](release-qualification.md#qualification-policy):
exhaustive readiness belongs to nightly/manual verification, not a per-release
campaign. The required PR gate is Fast release checks.

## Changes

- Saved recovery notices are grouped and initially collapsed. Internal observations remain hidden. Saved tool results use the existing authenticated, paginated detail API and load only when opened.
- Restart/process-exit recovery marks unfinished work interrupted. Warming a replacement worker cannot turn that outcome into success or dismiss its notice. Starting an explicit new generation clears the prior notice. Settled cold histories remain unloaded.
- A chat with a worker-startup failure can preview and select a replacement bundle without first loading its unavailable old root. The replacement is validated from its composed characteristics, rechecked under Foundation's writer lock, and journaled. Failure/cancellation restores the original files. Model-pin incompatibility requires the existing explicit reset choice; no message or tool call is replayed.
- Chat info is remounted for each selected session so old paths, errors and late responses cannot leak into the next chat.
- The Public readiness workflow runs focused Python checks, the full frontend unit suite and three real UI/API browser regressions on PRs and main. Release publishing additionally requires that workflow at the selected immutable release revision. Repository status-check enforcement is separate from workflow execution.

## Verified in the owned DTU

Environment: an owned isolated DTU, Ubuntu 24.04 ARM64,
Python 3.13.16, Node 22.23.3, Chromium. Source, dependencies, account, service,
state and ports are private to this environment. No shared app was changed.

| Check | Result |
| --- | --- |
| Final focused Python suite (20 modules) | 550 passed, 2 skipped |
| Full frontend unit suite | 511 passed |
| Recovery-notice browser | Groups, unknown outcomes, lazy saved result, technical details, narrow layout; passed |
| Chat-menu browser | Session ID/path details, rename/auto-name, diagnostics copy/download, advanced destinations, light/dark and mobile; passed |
| First-chat browser | Empty real API/storage, persisted draft, attachment, definite creation rejection, explicit retry, exactly one synthetic delivery, reload without replay; passed |
| Production frontend, wheel and source archive | Built successfully; existing bundle-size advisory |
| Fresh wheel install and user service | Installed as a fresh synthetic Linux account; doctor/status passed |
| Installed-app sign-in | Real PAM rejected an incorrect password and accepted the synthetic account; unauthenticated API rejected; authenticated composer opened |
| Actual reset to final candidate wheel | Service stopped, package repaired, restarted, doctor/status passed; nine sampled config/auth/attachment/session-evidence files byte-identical |
| Update behavior | Focused replacement/readiness/deployment tests passed, including preservation and durable activation |

The skipped tests are the optional Python Playwright module (not installed in
this test environment) and the opt-in uv installation test. Actual installed
sign-in and reset were separately exercised as described above. Node browser
checks ran with single-process Chromium because multiprocess Chromium crashes in
this ARM container; hosted CI uses normal Chromium.

Fixtures were corrected to exercise the current bootstrap and callback
interfaces. A definite creation rejection uses HTTP 409: HTTP 503 correctly
leaves delivery uncertain and must not be represented as permission to resend.
The first-chat test waits for draft-save acknowledgement before reload; it does
not qualify an immediate reload during the debounce interval.

## Published-artifact acceptance

The [release workflow](https://github.com/microsoft/amplifier-unified/actions/runs/37807214946)
passed at the immutable released revision. The `Recovery and first-use checks`
status is now enforced on main by repository ruleset 24735550.

| Check | Result |
| --- | --- |
| Published wheel upgrade | Downloaded 0.20.83 and 0.20.86 assets, verified their published checksums, upgraded with `reset --source`; doctor, status and health passed; nine sampled saved files remained byte-identical |
| Settings release update | Real Mac Edge clicked Check for updates and Update Amplifier against the installed Linux DTU. 0.20.83 staged and activated 0.20.86; restarted health was acknowledged; included/other component checks reached complete with no error or pending restart |
| Mac browser continuity | Edge 154.0.4258.62, isolated profile, real synthetic PAM sign-in and composer; saved draft survived the actual server upgrade and same-tab reload; no JavaScript errors |
| Installed Mac PWA | Installed and launched in a separate Edge profile, explicitly selected standalone display; 0.20.86 reported active window-controls overlay, a 38-pixel title-bar area and no horizontal overflow; test PWA uninstalled afterward |
| OpenAI | Published app's normal-user worker, real gpt-4.1-mini response; no tool calls |
| Anthropic | Published app's normal-user worker, real claude-haiku-4-5 response; no tool calls |
| Missing-root recovery | Actual installed worker rejected a missing bundle, then previewed and selected a valid replacement; saved message content preserved, no inference or replay |
| Long-history recovery | 97 synthetic messages / 336,136 characters; real Anthropic semantic compaction saved a checkpoint, then worker restart and a second response retained the seeded fact. Canonical history preserved; zero tool calls. Completed in 92.99 seconds |

Model acceptance used scoped environment credentials with synthetic messages and
no configured tools. The app's injected control tool remained mounted, but the
acceptance bridge rejected tool operations; no tool call occurred. Temporary
GitHub authentication for the Settings updater was removed after completion.
Generated provider credential artifacts were redacted after acceptance.

The first long-history fixture used an artificially reduced summary output
allowance that conflicted with Anthropic's thinking budget. The passing run
used the normal 8192-token summary allowance. Failed receipts were retained.

## Remaining promotion acceptance

Windows/WSL real-device acceptance remains outstanding: clean installation,
sign-in, first chat, update, restart/repair and diagnostics export. The known
Windows machine was offline; an alternate SSH route failed host-key verification
and was not bypassed.

The Mac tests qualify Edge and the installed PWA against the owned Linux DTU,
not a local macOS backend installation. The native overlay result comes from
the browser's overlay/display APIs, not a screenshot of the OS window frame.
The long-history check is a bounded live recovery test, not a multi-hour soak or
an acceptance test using the original user's history. These limits must remain
visible when making a broader promotion decision.

Existing main remains the shipped implementation. AHP/ACP is a separate
secondary track; no adoption or production switch is authorized by this release.

Local evidence is retained by the owning workspace in `output/public-readiness`.
`WORKSPACE-MANIFEST.json` records the DTU and cleanup command. The private
installed preview remains on DTU loopback port 8941. Host ports 8443 and 9543
were not used. No original user history or writable host application state was mounted.
Scoped acceptance credentials were removed or redacted after the checks.
