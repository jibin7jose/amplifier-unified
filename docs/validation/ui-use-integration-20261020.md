# UI-use in normal Unified chats — October 20, 2026

The preview now includes main **0.25.0**, commit
`361cf121ab0ab71bacd253ce3b92688fcac81e61`, updated on October 22. The current
client build is `5ae1db42e9f1baca`. See the dated qualifications below; the
original measurements remain the October 20 baseline.

The isolated preview integrates shared UI-use
with ordinary Work chats. It can use a private managed browser, an explicitly
connected browser tab, or the canonical computer-use executor. Optional decision
models choose observed actions; the chat model still plans, writes text and
recovers when needed. Ordinary selection remains available independently.

This is an isolated development preview, not a release or production adoption.
It proves representative end-to-end flows, not universal website/application
compatibility. In particular, native desktop decision selection still needs
improvement; the tested desktop tasks completed through ordinary vision recovery.

## Recorded trial flow

For current setup, follow [browser tasks and optional decision services](../clients/browser-and-decision-setup.md).
These historical trials used a new chat with the Work bundle including UI-use,
an isolated synthetic form and desktop fixture, and Jev explicitly configured
for browser and computer selection. No special tool names were needed.

For a quick browser check, send:

> Open http://127.0.0.1:8962/ and fill the form with Ada, select the Blue team,
> and save it. Verify the page confirms Saved Ada Blue.

That address is reached by the browser inside the preview. For a real public
website, send:

> Open https://books.toscrape.com/, find A Light in the Attic, open its product
> page, and report the price and availability you actually see.

For a desktop check, send:

> On the computer desktop, use the Project notes app. Set the title to Trip plan,
> replace the notes with Book the train, and save. Verify the saved confirmation.

The desktop in this preview is an isolated Linux desktop, **not the
Mac or phone viewing the chat**. The browser extension controls web pages only.

Use the monitor icon, **Browser & computer**, in the chat header to:

- Select Amplifier's browser or a ready connected tab. Preferences can apply to
  all chats, the workspace or this chat; explicit task/session context comes first.
- Choose **Prefer configured decision model** or **Use ordinary chat model**.
  With no configured decision backend, the former also uses the ordinary model.
- Open **Watch & interact** for the actual managed browser. Click or type there
  to take over/sign in; this pauses agent access until **Resume agent access**.
- Open the controls in the canvas, or capture the configured computer desktop.
- Use **Stop agent access** at any time. Stop remains latched across service
  restarts. Closing a managed browser also pauses that chat's agent access.

Expand a tool under **Worked for…** for its actual input and result. UI-use's
result lists individual action selections, execution outcomes, decision model,
fallback and timing. The Browser & computer panel also shows the latest step.
Older cold sessions may require **View saved runtime history** for retained work.

Under **Settings → AI connections → Decision models (optional)**, select a saved
provider connection, model, surface and observation consent. Provider-owned
metadata exposes installed Jev and local services as optional connections. Jev
can reuse the key already on the host. Local setup takes the endpoint and token
of an already running inference service; it does not install models or hardware.
Saving credentials is not a live connectivity check. Decision-provider changes
apply to new conversations; per-chat ordinary-model preference can change live.

## Updating an existing browser connection

For the screenshot-boundary and receipt-recovery fixes, download **extension
preview 0.2.2** from `/connect`, choose **Stop browser access**, replace the files
in the existing unpacked directory, and Reload that same extension in Chrome or
Edge's Extensions page.
Verify version **0.2.2** and ID `hihclhdmelacndmdafanjafjbmaobgeo`; retain the
supplied manifest key and trusted service CA. Updating from 0.2.1 adds no
permissions or grants and preserves enrollment. Do not remove/reinstall or pair
again. Stop clears the selected tab; reload drops pending observations.
Explicitly select the intended tab again when ready. Qualify this version separately from the
historical 0.2.1 physical-browser evidence below.

Adding across-pages access is a separate, optional change. Open the connection
page, choose **Enable tasks across pages**, and approve the
additional access once. Then open the intended tab and choose **Use this tab
across pages** in the toolbar popup. Select that ready target under the chat's
Browser & computer controls. Later chat requests may originate from a mobile
browser while the selected desktop browser remains online.

This upgrade uses Chrome's debugger permission and a fixed executor; it does not
expose arbitrary JavaScript or a general remote debugging endpoint. Older grants
retain their exact-document behavior and require reselection after navigation.
Local **Stop browser access** detaches the debugger. Revocation removes access;
worker/browser restart preserves enrollment but does not silently reattach a tab.

All new extension qualification ran in isolated Linux Chromium. No control was
sent to the user's Mac during this integration, and Mac Edge 0.2.0, device SSO,
physical mobile operation and Tempo execution are not claimed as qualified.

## Ownership and configuration

| Owner | Responsibility / local commit |
| --- | --- |
| `amplifier-bundle-work` | One UI-use include; work-amp-dev inherits it. Existing spike commit `5580006`. |
| `amplifier-bundle-ui-use` | Portable controller, action selection, bounded verification, target adapter, fallback. `ea03114`. Optional `behaviors/computer.yaml` composes the canonical desktop executor. |
| `amplifier-bundle-computer-use` | Desktop capture/input, permission and screenshot authority; observed controls and ordinary vision recovery. `4eead15`. |
| Existing OpenAI/Azure provider modules | Their existing optional decision implementations and account configuration; no changes in this integration. |
| `amplifier-module-provider-jev` | Jev inference/auth plus decision-only setup metadata. `0943ac3`. |
| `amplifier-module-provider-local-decisions` | Local inference adapter and setup/cost metadata. `c3cdee5`. |
| `amplifier-lib-decisions` | Shared decision contracts and dispatch; unchanged in this integration. |
| Existing loop-live / loop-streaming spike branches | Bounded programmatic tool dispatch and ordinary-model inference capabilities; unchanged in this integration. |
| `amplifier-unified` | Target discovery/lifetime, preferences, generic device delivery, optional host adapter, in-app setup/viewing and actual tool-detail projection. Final revision recorded in the workspace manifest. |

Unified wires the browser host adapter only when the selected bundle already
includes UI-use. It enables observed desktop control only when canonical
computer-use is also included. Explicit opt-outs win. No module installation is
implied by chat creation. Other apps can implement the documented host boundary;
CLI users keep their existing computer-use/browser-bridge experience.

The host does not replace the worker's mounted decision configuration with
newly saved global settings. Its live preference may force ordinary selection;
otherwise the worker uses the provider/bundle snapshot it actually mounted.
Missing optional backends fall back to ordinary selection. Missing executors
return a recovery choice. A pinned connected account/device never silently moves
to a different browser. Possible unknown effects are not replayed automatically.

See [UI-use host contract](../../contracts/ui-use.v1.md) and
[device connection contract](../../contracts/device-connections.v1.md).

## Measured live flows

These are acceptance samples, not a controlled performance benchmark. The wall
clock starts before `conversation.send` and ends at the final idle response,
including runtime startup and planning. Some cases ran concurrently. All used
OpenAI `gpt-6-luna` for orchestration/writing/recovery and Jev `jev-1.14.0` when
decision selection was configured. All completed without per-step approvals in
this explicitly configured, isolated preview.

| Case | Wall time | Actual behavior |
| --- | ---: | --- |
| Natural-language managed form | 21.50 s | Jev selected type, select and click; all three executed and final page was independently verified. UI-use loop 3.51 s. |
| Public books website | 23.18 s | Jev selected the product link and completion; real page verified. UI-use loop 0.99 s. |
| Connected tab, including navigation | 49.10 s | Jev selected form actions and Continue; actual tab reached the next page. Includes ordinary fallback after a case-sensitive success-text mismatch. |
| Same managed form, ordinary selection | 24.15 s | All three selections used the ordinary model; no decision calls. UI-use loop 6.32 s. |
| Native desktop, configured selection | 78.69 s | UI-use tried Jev/ordinary candidates; screen freshness prevented several actions. Canonical vision/input recovery saved the exact note, independently checked by file content and new modification time. |
| Native desktop, ordinary selection | 60.10 s | UI-use plus canonical vision recovery; independent saved-file check. |
| New chat after settings saved in the UI | 22.20 s | Newly configured Jev selected all three successful form actions; real page verified. |

Jev's responses report usage and latency but no price. **Unknown decision price
is not zero**; the UI warns that chat totals may exclude it. Local inference's
zero API charge excludes hardware/electricity and paid planning/writing/fallback.
Azure Decision-1 and local model adapters are installed/discoverable but were not
live-qualified again in this DTU; their earlier spike evidence remains separate.

## Checks and retained evidence

Evidence artifacts are retained in the private qualification archive. Deployment
addresses, user paths and account/enrollment records are not included here.

| Check | Result / file |
| --- | --- |
| UI host + decision configuration, final | 20 passed; `ui-host-setup-final-2.log` |
| Earlier affected integration tests | 39 passed; `ui-focused-final.log` |
| Existing setup/runtime/history/readiness regressions | 195 passed, 5 skipped; `ui-regression-final.log` |
| Actual-Core app guidance | 5 passed; `ui-core-guidance.log` |
| Shared controller and real-browser tests | 34 passed; `ui-controller-final-3.log` |
| Canonical computer input/screenshot authority | 177 passed; `ui-computer-tests-2.log` |
| Tool-detail/settings client tests | 8 passed; `ui-client-unit-final.log` |
| Real extension / independent clients / normal chat | 17 checks; `extension-020-browser-final.log` |
| In-app decision setup and credential reuse | 6 checks; `ui-decision-settings-browser-2.log` |
| Actual chat tool details, live viewer, takeover, stop, canvas, mobile width | 10 checks; `ui-use-browser-acceptance-final.log` |
| Production frontend | Build `0b48b093455db111`; `ui-build-final-4.log` |
| Separately installed wheel | Passed packaged UI, APIs and extension 0.2.0; `package-check-ui-final-2.log` |
| Live cases | `ui-integration-live-receipt.json` and individual `live-*.json` |

Test counts overlap; they are not summed. Failures and intermediate logs are
retained: they exposed provider-native action-format mismatch, missing image
projection during desktop fallback, bypass of UI-use by desktop delegation,
and CDP object-key ordering in freshness checks. Those defects were repaired and
the affected flows rerun. One controller run omitted the installed-browser path;
the recorded final run uses the owned executable. An initial setup-browser run
hit navigation during startup; the final run waits for the actual chat surface.

## Environment, limits and retention

Task-owned Linux ARM64 DTU with an editable source installation,
Python 3.13.16, Node 22.23.3, Core 2.0.2
in the private worker environment. App baseline is `cc0a183cf` / 0.20.187; newer
main `a17b62ee6` / 0.20.195 was reviewed, not merged into these measurements.
Upstream delivery still needs a focused integration onto current main.

The task owns its listener, state/history/browser contexts and shared-module
copies. Provider credentials were configured privately for authorized live checks.
No host state is mounted, copied outbound schedules are
not enabled, and no shared/live installation was changed. The backend and
frontend watcher remain available, with fixture and virtual desktop services.

Native decision observation currently uses OCR and strict screen freshness; a
blinking caret can cause stale candidates and slow recovery. Browser observation
currently covers ordinary top-level DOM controls, not every iframe, shadow DOM,
canvas editor, permission prompt or protected page. Managed-browser login is
co-interactive but does not supply IT-managed device identity. Captchas and
unsupported/login flows require handoff. PWA screen sharing supplies context,
not control, and remote Mac desktop execution is not supplied by this extension.

Transfer archives, disposable installed-wheel dependencies and stale owned test
resources are cleaned after evidence capture. Current preview, useful review
chats, existing enrollments and recoverable local commits are retained. Exact
owned resources, source parity and cleanup actions are recorded in the private
workspace manifest.

## October 21 main merge qualification

Merged main 0.20.207 into `codex/device-connect-poc-20261020`; this does not
publish the spike or merge it into upstream main. The recoverable merge commit
and exact live source are recorded in the workspace manifest. The shared
UI-use, computer-use, provider and orchestrator spike revisions remain unchanged.

Main now owns the complete saved-tool observation binding, bounded lookup and
open-detail refresh. Those implementations replace this spike's earlier
`WorkWindows`/resident-page tool-record workaround. Browser/computer/UI-use
labels retain their tool identity; app-control dispatch also retains main's
nested action label. Optional provider setup, the host adapter, connection
permissions and chat controls remain composed on top of main.

All qualification ran in the task-owned DTU. The dependency
lock updates aiohttp 3.14.4, certifi 2026.10.19, uv 0.13.4 and markdown-it 14.3.3;
Core was not rebuilt. Both the editable backend and separately installed wheel
identify as 0.20.207 with client build `a4259d144b44d2f0`.

| Check on the merged code | Result |
| --- | --- |
| UI host, decision setup, device delivery, saved tool fields/identity, open details and actual Core guidance | 149 passed; one direct context-logger test skipped because the hook module is not installed in the app test environment. Actual mounted logging and tool display are exercised by the live chat/viewer check. |
| Native worker configuration identity/refusal/recovery | 6 passed |
| Client action display, request preview and execution rendering | 29 passed |
| Real extension, exact tab/document access, cross-page navigation, mobile-sized independent client, Stop, restart, rotation and revocation | 17 checks passed |
| In-app optional decision setup and existing-key reuse | 6 checks passed |
| Actual saved input/result disclosure, managed browser image, takeover, Stop/Resume, canvas and mobile layout | 10 checks passed |
| Production client build and separately installed wheel/resources | Passed |

The first focused run lacked the optional streaming orchestrator in its test
import path. Its affected test passed after pointing it to our unchanged shared
source. The first package-check launcher inherited `/root` as its working
directory; rerunning from the owned source directory passed. Neither required a
product-code change. Initial and corrected logs are retained.

These are individual live acceptance samples, not a comparative benchmark:

| Fresh normal chat | Total wall time | Verified behavior |
| --- | ---: | --- |
| Managed form, configured | 18.86 s | Three successful Jev 1.14.0 selections; actual saved page independently verified. |
| Managed form, ordinary | 24.39 s | Three ordinary-model selections, no decision selections; actual saved page verified. |
| Native Project notes app, configured | 45.88 s | Decision/ordinary candidates hit the existing stale-screen boundary; canonical ordinary vision/input recovery saved the exact new note, checked by file content and modification time. |
| Connected browser across pages | 43.63 s | Four successful Jev-selected actions and a completion decision; independent real-tab navigation verification. |

All used OpenAI `gpt-6-luna` for the main conversation and recovery, with no
per-step approval prompts in this explicitly configured preview. The extension
run used its own temporary enrollment and browser, not the user's Mac. Existing
user history and Mac enrollment were preserved. Physical Mac/Tempo, enterprise
SSO, universal website/app support and Azure/local live requalification remain
outside this merge check. Desktop decision freshness remains a known limitation.

Private evidence includes the `main-merge-*` logs and the dated merge receipts. Temporary archives,
package-check environment and test resources are reclaimed after recording the
results; the active editable preview and representative review chats are retained.

## October 21 follow-up: main 0.20.211

Updated the same isolated branch and preview to main
`812ccb3ea9864e34e313633bd9e7dbdef1acbe27`, including the fixes merged after the
0.20.211 release tag. Source merged without manual conflicts; generated browser
assets were rebuilt as `853abfda030f6acb`. Upstream changes cover update handling,
selected module-source observation, mixed assignment capacity and retaining a
failed generation's diagnosis while preparing a worker. All shared UI-use and
provider spike revisions remain unchanged. Dependencies did not change; only
the editable app metadata and browser build were refreshed.

Focused qualification: **157 backend tests and 67 client tests passed**, with
no skips. This covers the changed runtime/update behavior and our UI host,
decision setup and device-delivery boundaries. The real extension's 17 checks
and live viewer/controls' 10 checks passed again. The separately installed wheel
and downloadable extension contents were also checked.

Fresh normal chats completed the managed form with Jev (19.83 s), the same form
with ordinary selection (23.80 s), the desktop note with canonical vision/input
recovery (95.14 s), and connected-browser navigation with Jev (47.55 s). Each
result was independently verified against the actual page or newly saved file.
These are acceptance observations, not comparable performance benchmarks.
Desktop freshness limits remain as described above; no new physical Mac or
native-client execution was qualified.

Exact source parity, model/action receipts, cleanup and preservation of existing
history and enrollments are recorded in the private workspace manifest and
dated main-update qualification archive.

## October 21 release preparation: latest main and public sources

The candidate includes main `4f7d671896b7d28b248e706b3e1c9cc648732e3d`
(0.20.213), with client build `421e721159654662` and extension 0.2.1.
This is preparation for a feature release coordinated by Main; it is not part
of the 0.20.213 repair release and does not authorize live-host adoption. Main
assigns the release identity after dependency approval and qualification. UI-use
does not hold another accepted feature batch if its prerequisites are unfinished.

Focused package/update/browser-readiness checks passed (192 tests). Client
receipt, executor, setup and API checks passed (48 tests). A fresh worker exposed
an accidental server dependency in bundle setup; moving that wiring into the
worker-owned configuration module passed 22 focused tests, including actual
settings application with the server HTTP dependency unavailable.

The built client caught missing request metadata in two Settings wrappers.
After correction, seven browser checks verified both browser and provider setup:
confirmed refusal permits a new explicit attempt, while an accepted operation
followed by a failed state read keeps the exact request identity through reload
and reconciliation. Installation responses were intercepted for this test; no
real package installation was performed by the browser harness.

Actual isolated Chromium extension acceptance passed 17 checks, including a
normal chat using Jev across page navigation, independent page verification,
local Stop, reconnect, credential rotation, and revocation. Managed-browser
controls passed ten checks covering retained tool inputs/results, live images,
human takeover, Stop/Resume, canvas and mobile layout. A separately installed
wheel passed 11 checks; its 325 relevant static, extension and worker-helper
files matched the tested source. That wheel check reused the owned environment's
dependencies read-only and does not establish clean dependency installation.

Fresh form acceptance took 37.035 seconds with three Jev selections and 22.421
seconds with three ordinary selections. Connected-browser navigation took
47.129 seconds with five Jev selections. These are separate acceptance samples,
not comparable performance benchmarks. The main conversation used OpenAI
`gpt-6-luna`; no per-step approvals were required in the explicitly configured
preview.

The server-desktop note acceptance also passed in 88.718 seconds, with the new
saved file independently verified. Its decision loop encountered the existing
stale-screen boundary and stopped; ordinary vision/input recovery finished the
task. This remains a desktop efficiency limitation, not evidence that decision
models reliably complete native desktop tasks by themselves.

The first anonymous installation correctly failed while Microsoft repository
setup automation held the four new repositories private pending corporate
registration. After registration, all four became public. Their canonical OSS
files, README legal sections, and repository settings were checked against the
Microsoft audit recipe, which was read but not executed. Both standalone
ordinary and decision-enabled installations now pass from anonymous public Git
sources, with exact installed commit provenance and no local source overrides.

The complete default Unified wheel also installed into a fresh environment
without optional browser or decision packages. Its packaged worker manifest
resolved in another fresh environment, mounted UI-use on a real Core
coordinator, and correctly blocked execution without host authority. A third
worker installed the wheel-declared Jev requirement and verified opt-in registry,
catalog, and cleanup behavior without credentials or inference. These checks use
the current public main branches. Qualifying the default public-main Work
execution path still requires the shared PRs to merge in dependency order. The
editable live preview uses explicitly recorded candidate sources and is separate
evidence.

A separate synthetic host and worker qualified the full proposed Work graph
from anonymous public Git sources, pinning unmerged dependencies to their exact
public PR commits. Both ordinary and configured-Jev normal chats independently
completed the managed-browser form. Installed packages and available credentials
did not activate decisions in the ordinary case. In the configured case, a
five-second Jev timeout fell back to ordinary selection for typing; Jev then
selected Team Blue and Save. Host, worker, and bundle provenance was captured
without development source overrides. This used the frozen 0.20.213 candidate
artifact, distinct from the subsequently updated 0.20.214 preview. It establishes
pre-merge candidate integration, not canonical-main qualification or a controlled
performance comparison. The temporary environment and synthetic state were
removed after retaining the evidence.

The optional Mac client setup handoff passed its native checks and an actual
Mac discovery/setup-page exercise. A normal chat also completed the synthetic
form in the user's paired Edge Work profile on a physical Mac, using extension
0.2.1 with the existing enrollment and document-only grants. Both the service
and a separate read-only Mac observation verified Ada, Team Blue, and exactly
one save. This fresh chat took 47.136 seconds with no approval prompts. It is an
acceptance measurement, not a performance comparison.

The trace records four Jev 1.14.0 decisions. Name entry and Save used Jev's
selection; the team choice fell back to the ordinary model after a low-confidence
answer. Luna also supplied the text argument and handled conversation and final
verification. The controller rejected an exact-text success condition because
the test instruction's counter wording differed from the page in punctuation;
the outer chat then observed the page and confirmed the result without another write. This is
successful hybrid execution and recovery, not a decision-only completion claim.

Physical Mac acceptance covers the selected document only. It does not establish
cross-page tasks, enterprise SSO, or native Mac desktop control. The companion
remains optional and does not gain an executor or reuse its chat credential for
device execution.

## October 22 accepted-main catch-up

The isolated candidate now incorporates main
`361cf121ab0ab71bacd253ce3b92688fcac81e61`, including configuration revision
admission, Stop fencing, approval retirement, credential redaction, Work effect
checks, and the compact composer. The combined source preserves the optional
UI-use behavior and was rebuilt into client `5ae1db42e9f1baca`. Its inherited
0.25.0 version identifies the development baseline; it does not publish UI-use
as part of that release.

Focused qualification passed 35 backend and 70 frontend tests. The built
setup-recovery flow and compact-composer browser flow passed. A separately
installed wheel passed 11 checks with all 325 relevant package files matching
the tested source. This package check reused the isolated environment's
dependencies; it is not fresh accepted-public-main dependency qualification.
The existing saved chats and Mac enrollment were preserved, without physical
Mac actions. Earlier measurements retain their original source identities.

Streaming #68 has merged at `e04bba7e85bff9836bfc48cd8ac1db676f4f5fab`;
its tree is identical to candidate `20792f1c` tested through both a fresh CLI
installation and Unified normal chats. Live #19 now locks that accepted commit
and has passing CI, with independent maintainer approval pending. Work and the
other provider/desktop PRs retain their normal review requirements.

The application lock now records the declared UI-use and optional support
packages while preserving the existing versions and dev fixture pins. Public
OpenAI and Azure sources do not yet expose their proposed `decisions` extras;
their adapter PRs must land before those optional paths can be qualified from
accepted public sources. The editable preview's candidate modules remain
separate from the final clean public Work host/worker qualification.

Two new managed-browser chats passed on the combined source. Explicit ordinary
selection completed in 25.607 seconds with three standard selections; configured
Jev completed in 22.249 seconds with three actual Jev selections and no fallback.
Each trace contains one Save invocation, and independent page observation
confirmed the intended name and team. These are acceptance timings, not a
controlled performance comparison. Global settings and the existing Mac sample
were unchanged. An initial evidence reader used an obsolete capture location;
the existing ordinary trace was reconciled without repeating the task.

Decision-only Jev and local-decisions are not supported provider mounts on Core
2.0.2: its provider contract requires a newly mounted chat-provider object.
The actual installed Core 2.0.2 `RustSession` probe reproduced Jev's rejected
mount, the surviving decision backend, and its removal and closure during normal
cleanup. The live Jev task above used that surviving
backend; successful execution does not make the rejected mount supported.
Local-decisions has the same capability-only mount pattern by source inspection;
no equivalent actual-Core local-decisions reproduction is claimed here.

Source inspection of [Azure PR #45](https://github.com/microsoft/amplifier-module-provider-azure-openai/pull/45)
at `2f36a9d` also shows that its optional `chat_enabled: false` mode skips the
chat-provider mount. That combination remains unsupported; this is a source
finding, not a new Azure runtime reproduction. Ordinary Azure chat-plus-decisions
is outside this finding. The narrow Jev/local lifecycle proposal does not cover
this conditional Azure mode, and configuration cannot waive Core's mount checks.

[Core PR #124](https://github.com/microsoft/amplifier-core/pull/124) proposes an
explicit capability-only lifecycle, owned rejection cleanup, and compatibility
checks, with provider-owned hooks as an alternative requiring account-binding
integration. It is a documentation-only draft, not a ratified contract or an
implementation. Decision-only shipping needs an accepted, qualified lifecycle;
it must not depend on effects surviving a rejected mount. Unrelated Main
releases remain independent. These findings do not change the source identities,
timings, or outcomes of the frozen runtime evidence above.

The optional adjacent native Work-effect probe was not run because its isolated
fixture build lacked a build backend. The existing affected checks above passed
independently.
