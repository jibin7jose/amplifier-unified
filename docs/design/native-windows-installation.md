# Native Windows installation

Status: implementation design and trial preparation; **not a native Windows support claim**.

## Zero-prerequisite bootstrap prototype

The requested direction is a single PowerShell paste for an ordinary Windows
information worker, with **no curl, Git, Python, uv, WSL, PowerShell 7 or winget
assumed**. `scripts/install.ps1` now has a separate explicit prototype entry:
`-PrototypeManifestUrl` plus `-PrototypeManifestSha256`. The no-argument public
release installer still requires its native-support record and retains its
closed activation gate. A prototype manifest has `status: prototype`; it is
not a native-support record and cannot enable that public path.

**Not yet a released installation command.** The Windows trial owner must first
join the exact native candidate, collect its verified prototype inputs,
publish the immutable prototype assets and provide their digests. This code
candidate does not claim those artifacts exist or that absent-prerequisite
installation has run on a clean Windows account.

### Chosen prerequisite strategy

1. The selected `pinned-vcs` mode always acquires root-local official uv
   **0.12.18** and standard **MinGit
   2.56.0.windows.4** ZIPs. Verify pinned SHA-256 before extraction/execution;
   retain whole distributions, licenses and receipts. It does not rely on
   preinstalled developer tools. The retained `offline-wheels` mode may reuse
   compatible, ACL-validated existing helpers.
2. Use uv's managed ordinary **CPython 3.13**, selecting an exact supported patch
   in the prototype plan. Download into this installation's private Python
   directory with `--no-bin --no-registry`. Store aliases and a system Python
   3.15 are neither invoked nor replaced. No compiler is installed.
3. Install the original app plus 59 registry wheels by hash, without dependency
   resolution or source-build fallback. Then install **five full-commit Git
   requirements** noneditably using normal uv semantics and six pinned isolated
   Hatchling backend wheels. This online step preserves honest installed Git
   origins; it does not replace them with locally built dependency wheels.
4. Use an absolute Python/launcher and retained helper paths on every relaunch.
   No user/machine PATH, shell profile, default Python, registry registration,
   service or Scheduler mutation is needed.

Official references: [uv standalone installation](https://docs.astral.sh/uv/getting-started/installation/),
[uv unmanaged installer options](https://docs.astral.sh/uv/reference/installer/),
[managed Python provenance](https://docs.astral.sh/uv/concepts/python-versions/),
and [MinGit's non-interactive application distribution](https://gitforwindows.org/mingit).
The [Scoop installer](https://github.com/ScoopInstaller/Install) demonstrates
non-admin PowerShell setup but adds a package-manager and policy surface; it is
not installed here. [rustup's installation methods](https://rust-lang.github.io/rustup/installation/other.html)
are a useful architecture/checksum precedent, not a dependency.
Do not copy execution-policy bypasses from other projects.

Pinned helper digests are those reported by the publishers' exact
[uv release metadata](https://api.github.com/repos/astral-sh/uv/releases/tags/0.12.18)
and [Git for Windows metadata](https://api.github.com/repos/git-for-windows/git/releases/tags/v2.56.0.windows.4).
Publisher-served digests establish integrity, not independent signatures.
uv verifies its own pinned python-build-standalone download digest; the exact
Python patch and resulting interpreter still need native candidate checks.

### Build and delivery interface — trial owner only

#### Selected online frozen-VCS mode

The short path is now `--source-mode pinned-vcs`. A completely offline capsule
was an engineering option, not a product promise. The original offline packager
(`--source-mode offline`, or the unchanged default) below remains for review and
prior evidence, but does **not** establish
accepted Git origins. No canonical provenance facility or fabricated
`direct_url.json` is introduced.

Supply the unchanged app wheel plus **59** actual registry wheels in the runtime
wheel directory, the **six** build-only wheels in a separate directory, the exact
frozen lock and retained production requirements, and a reviewed inventory for
the 60 runtime wheel inputs. The builder validates the frozen source/lock/export,
actual wheel identities, original app hash/size/metadata, registry lock hashes,
five canonical full Git refs and the separate backend closure before creating
output. The source-mode manifest and capsule identity must agree.

```sh
python scripts/windows_bootstrap_capsule.py --source-mode pinned-vcs --wheelhouse /path/to/60-runtime-wheels --build-wheelhouse /path/to/6-build-wheels --lock /path/to/frozen/uv.lock --production-requirements /path/to/production-requirements.txt --inventory /path/to/reviewed-runtime-inventory.json --output /path/to/new-output --source 1c4ac3b4f9ed5d2d3f588f6fa6c74a119e439153 --version 0.54.5 --python EXACT_3.13_PATCH --url https://github.com/microsoft/amplifier-unified/releases/download/EXACT_PROTOTYPE_TAG/windows-prototype.zip
```

That is an owner-side build template, not a usable installation command or a
claim that those wheel bytes/public assets are present here. Runtime inputs
exclude the five Git wheels. Build-only pins from the exact lock are
`hatchling==1.32.4`, `packaging==26.3`, `pathspec==1.1.1`, `pluggy==1.6.0`,
`tomlkit==0.15.2`, and `trove-classifiers==2026.9.21.13`; all must be verified
pure-Python wheels. They do not become target host dependencies merely because
they build the five source packages.

The installer validates the extracted source payload using the private managed
Python **before** venv creation or package installation. It reconstructs the
requirements/constraints from the frozen inputs and refuses injected options,
wrong refs/subdirectories, missing/extra wheels or changed hashes. It then uses:

1. `uv pip install --no-index --no-deps --require-hashes --only-binary :all:`
   with the local runtime wheel directory and the 60 generated hashed requirements.
2. `uv pip install --no-index --no-deps --no-require-hashes --verify-hashes
   --only-binary :all: --no-binary <the-five-Git-names> --find-links <build-wheels>
   --build-constraint <build-constraints.txt> -r <git-requirements.txt>`.
   The named exceptions permit only the five trusted source builds. Git fetching
   stays online; isolated backend resolution has only the verified local wheels.
3. Read-only installed metadata auditing for exactly 65 distributions, no
   editables, all five real repository/commit/subdirectory origins and expected
   versions, then `uv pip check`, normal launcher validation and shortcut creation.

These flags were checked against
[uv 0.12.18 CLI definitions](https://raw.githubusercontent.com/astral-sh/uv/0.12.18/crates/uv-cli/src/lib.rs),
[build policy](https://raw.githubusercontent.com/astral-sh/uv/0.12.18/crates/uv-configuration/src/build_options.rs),
[hash modes](https://raw.githubusercontent.com/astral-sh/uv/0.12.18/crates/uv-configuration/src/hash.rs),
and [pip installation](https://raw.githubusercontent.com/astral-sh/uv/0.12.18/crates/uv/src/commands/pip/install.rs).
`--no-require-hashes` alone disables hash checking in this version; pairing it
with `--verify-hashes` retains supplied build-constraint hash verification.
`--no-deps` skips runtime dependency resolution, not isolated backend setup.
Constraints are not themselves an allowlist; complete local wheel-set validation
is also required. `--no-index` does not prohibit Git URLs or network calls made
by trusted build code. Build isolation is not an OS security sandbox.

Installer child processes clear inherited `UV_*`, `PIP_*`, `PYTHONHOME`,
`PYTHONPATH` and virtualenv overrides before applying the private layout.
They also discard Git executable/template redirects so inherited
`GIT_EXEC_PATH` or `GIT_TEMPLATE_DIR` cannot substitute another installation's
helpers or hooks. The pinned-mode relaunch environment applies the same redirect
removal while keeping the caller's provider credentials inherited, not saved.
Anonymous Git access disables credential helpers/configuration. Ordinary
`USERPROFILE`, `HOMEDRIVE` and `HOMEPATH` stay inherited, not removed or redirected.
No credentials are saved in the plan/receipt. On relaunch the wrapper restores
root-local cache, tools, Python and absolute helper PATH. Bootstrap phase uv is
**0.12.18**; the installed host's `uv==0.13.4` dependency is a distinct artifact,
not silently substituted into the install phase.

The original app wheel is a narrow allowed root artifact; this does not grant
verified app Git identity for later optional-feature addition. Keep that
limitation and the actual worker-graph/onboarding acceptance separate.

#### Retained all-wheel mode (`offline`)

`scripts/windows_bootstrap_capsule.py` packages an **already-built**, complete
Windows x64/CPython 3.13 host wheelhouse. It performs no dependency resolution,
installation or publication. For example, with the selected candidate's actual
values and an absent output directory:

```sh
python scripts/windows_bootstrap_capsule.py --wheelhouse /path/to/qualified-wheels --inventory /path/to/reviewed-inventory.json --output /path/to/new-output --source EXACT_APP_40_HEX_COMMIT --version EXACT_APP_VERSION --python EXACT_3.13_PATCH --url https://github.com/microsoft/amplifier-unified/releases/download/EXACT_PROTOTYPE_TAG/windows-prototype.zip
```

The capsule contains `capsule.json` (app source/version), `requirements.txt` with
every wheel's SHA-256, `wheels/`, and this candidate's
`windows_prototype_launch.py` as `launch.py`, plus the reviewed `inventory.json`.
The builder emits
`windows-prototype.json` with schema 1, status `prototype`, exact source/version,
architecture `x86_64`, Python patch and capsule URL/SHA-256. Output is create-new
only; duplicate distributions, missing app identity, incompatible CPython tags,
symlinks, and any name/version/hash difference from the reviewed inventory refuse
before output creation. The inventory is a separate reviewed input, **not** a
list generated from whatever happens to be in the wheel directory:

```json
{
  "schema": 1,
  "source": "EXACT_APP_40_HEX_COMMIT",
  "lockSha256": "EXACT_LOCK_64_HEX_SHA256",
  "packages": {
    "amplifier-unified": {
      "version": "EXACT_APP_VERSION",
      "sha256": "EXACT_ORIGINAL_WHEEL_64_HEX_SHA256"
    }
  }
}
```

This abbreviated schema example is deliberately invalid until resolved; a real
inventory must contain the **entire** reviewed production set. Do not self-certify
closure by hashing arbitrary input wheels. The owner first checks actual
`METADATA`, active Windows markers/extras, source/build receipts and licenses.
The builder only enforces equality with that reviewed selection. Neither a
manifest label nor `pip check` establishes source provenance or selected extras.

#### Frozen 0.54.5 production inputs — provisional packaging plan

The app input is `1c4ac3b4f9ed5d2d3f588f6fa6c74a119e439153`, tree
`9657754b48f03f518fc421861738a82f8864baf7`, version **0.54.5**. Preserve the
already accepted original app wheel, **10,218,316 bytes**, SHA-256
`de950fe6dcf43e9944bfae4c45b6adeedc4b2fdbee4eda565443516a3ffcc9f5`.
Do not rebuild it, rename it as a different source, or label it with the host
application's newer version. A bootstrap join is a separate code identity:
record that commit and the exact builder, launcher and installer hashes alongside
the capsule. `--source` remains the **app** source above.

The frozen `uv.lock` SHA-256 is
`02ca2a403cb6546238e1e3b1eed2b07e2b848f7f1edc301fc67f4cc956f89c45`.
The retained production requirements export is **2,047 bytes**, SHA-256
`4013b4a681ce2e7729c7763069a0fc621d46b34e5bc0511c17fa074f1bafa6a0`.
It has 65 cross-platform dependency lines, excluding the app. Windows CPython
3.13 activates **64 dependencies plus the app = 65 distributions**. It is not a
hash-required wheelhouse manifest and must not be given directly to the offline
installer. A sanitized native installed-host audit verifies this production
selection from actual metadata; its SHA-256 is
`a7dbcf604af61d65edba22d5a17ad415e7b2b77d21916abaf90674337fa2db7d`.
That evidence does not establish an offline capsule installation.

Select only the app's base dependencies, including its explicit
`amplifier-bundle-context-intelligence[client]` and transitive `pyjwt[crypto]`.
Evaluate Windows markers: include `colorama` and `pywin32`; exclude
`httpx2-jsfetch` (`emscripten` only). Keep unconditionally declared `python-pam`
and `six`. Exclude dev/artifacts groups, app optional extras, pytest, tool-exec,
QuickJS, custom runtime fixtures, optional provider modules and browser/desktop
automation. Do not copy the producer's 87-distribution development environment.

The 64 dependencies consist of **59 registry wheels and five VCS wheels**:

| Production source | Exact input |
| --- | --- |
| Core | `2.0.2`, registry `amplifier_core-2.0.2-cp311-abi3-win_amd64.whl`, SHA-256 `a784e9f0382c471794e429818d9e55360625523bc62c54cae56be172364c936c` |
| Foundation | `c716df3cea84b2539aabdfea58a69ae676a1e853`, repository root |
| Session naming | Same Foundation commit, `modules/hooks-session-naming` |
| Memory | `5ac75ac85b9972e06936630e347023af107fd364`, repository root |
| UI Use / Context Intelligence | `5e3b08b8e06a6095314200476d387508ddb32f67` / `a80ce69d0e96e9a343501f238dcd867350dd9aa2`, respective repository roots |

For each registry package, use its exact selected version, Windows-compatible
wheel URL and SHA-256 in the frozen lock. All 59 have compatible wheel candidates
in that lock; this is metadata availability, not a download result. Accept
ordinary `cp313-cp313-win_amd64`, compatible older `cp3N-abi3-win_amd64` (for
example Core's `cp311-abi3`), and compatible pure Python wheels. Reject sdists,
free-threaded/other-platform/newer-interpreter wheels. Never compile Core.
Keep the private bootstrap uv **0.12.18** separate from the production
`uv==0.13.4` dependency pulled in by Memory.

The retained all-wheel packaging procedure, **not executed by this local join**,
is below. Its installed Git-origin boundary remains unresolved; it is **not**
the selected short path above:

1. Obtain the exact original app wheel through the supported artifact transfer.
   Verify its size/hash before use. Reuse the accepted production export and
   exact lock; do not resolve moving `main` or repeat the installed-host audit.
2. In an authorized build environment, collect the 59 registry wheels using the
   lock's URLs/hashes. Obtain or build the five VCS wheels at the full commits
   above, with `python -m build --wheel --no-isolation --outdir <new-dir>
   <exact-checkout-or-subdirectory>`. Record build-tool versions and build inputs;
   build dependencies stay outside the host wheelhouse. Do not run this command
   on the information worker's box. Preserve each wheel's licenses/notices.
3. Verify actual wheel metadata and compatible tags against the accepted
   production set: exactly 65 names/versions, no missing or extra distributions.
   Match the five build receipts to the exact repository/commit/subdirectory;
   check all registry wheel hashes against the lock and the app against its
   original digest. Create the reviewed inventory with these **actual** hashes,
   retain its review/source receipts, then run the builder above into a new output.
4. Before delivery, qualify actual offline installation with the existing
   hash-required/binary-only/no-deps commands. Verify installed provenance as well
   as the graph: local wheel installs may record archive `direct_url.json`, not
   the original Git commit. `app_component_graph`, update identity and selected
   dependency recovery consume this information. Do **not** manufacture Git
   provenance records, remove those checks or treat `pip check` as their proof.
5. Only after those checks and the missing ordinary-user bootstrap checks below,
   publish immutable prototype assets through the publication owner. Supply their
   real digests and one resolved paste. No capsule, digest, public URL or native
   support record is asserted by this plan.

**Remaining all-wheel inputs:** a materialized complete Windows wheelhouse and its
reviewed hash inventory/build receipts; verified installed-wheel provenance behavior;
exact managed Python patch supported by pinned uv; and eventual publication
locations. The accepted app wheel and native host graph already exist, but this
local packaging checkout does not receive binaries merely by reading their
retained identities. The first actual worker graph remains **unprepared**.
UI/onboarding work proceeds independently; a later app correction requires a
new exact app input, not relabeling this candidate.

After publication and review, the owner supplies one **fully resolved** paste,
not placeholders for the information worker to assemble. The following is its
reviewable template; its guards refuse until real exact values replace them:

```powershell
$ErrorActionPreference = 'Stop'
$BootstrapRef = 'EXACT_40_HEX_BOOTSTRAP_COMMIT'
$BootstrapHash = 'EXACT_64_HEX_SCRIPT_SHA256'
$PlanUrl = 'https://github.com/microsoft/amplifier-unified/releases/download/EXACT_PROTOTYPE_TAG/windows-prototype.json'
$PlanHash = 'EXACT_64_HEX_MANIFEST_SHA256'
if ($BootstrapRef -notmatch '^[0-9a-f]{40}$' -or $BootstrapHash -notmatch '^[0-9a-f]{64}$' -or $PlanHash -notmatch '^[0-9a-f]{64}$') { throw 'Obtain the resolved candidate paste from the trial owner.' }
$SetupText = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 30 -Uri "https://raw.githubusercontent.com/microsoft/amplifier-unified/$BootstrapRef/scripts/install.ps1").Content
$SetupBytes = [Text.Encoding]::UTF8.GetBytes($SetupText)
if ($SetupBytes.Length -gt 1048576) { throw 'Setup script exceeds its size budget.' }
$Hash = [Security.Cryptography.SHA256]::Create()
try { $ObservedHash = ([BitConverter]::ToString($Hash.ComputeHash($SetupBytes))).Replace('-', '').ToLowerInvariant() } finally { $Hash.Dispose() }
if ($ObservedHash -cne $BootstrapHash) { throw 'Setup script identity mismatch; no setup was run.' }
. ([scriptblock]::Create($SetupText))
Invoke-UnifiedPrototype $PlanUrl $PlanHash $false
```

This uses built-in Windows PowerShell 5.1 and .NET, without downloading another
package manager, running a `.ps1` through a policy override or installing a
machine-wide prerequisite. The downloaded script runs only after its reviewed
hash matches. Enterprise script/language restrictions remain authoritative:
if policy blocks this flow or `Add-Type`, stop and report the policy restriction,
not a bypass. In particular, after a `.ps1` is denied as scripts disabled, do not
retry its contents through `ScriptBlock`, dot-sourcing, `-Command`, `Unblock-File`,
elevation or execution-policy changes. Report the effective policy and ask the
organization's administrator for an approved software delivery route; do not ask
the information worker to weaken policy. The existing Restricted-policy refusal
is not cleared by an elevated CI runner's permissive policy.
Windows 11 x64, a normal non-admin token, usable PowerShell/.NET,
local fixed NTFS storage and permitted official HTTPS downloads are still
environment requirements. No silent proxy or certificate exception is added.

### Installation and restoration effects

| Effect | Owned location / recovery |
| --- | --- |
| Setup artifacts and helpers | New private `LocalAppData/amplifier-install-<random>` root, owner/SYSTEM DACL at creation. Existing roots are never adopted or ACL-repaired. |
| Python, host and caches | Root-local `python`, `python-bin`, `host`, `cache`, child `TEMP`/`TMP` in `temp`; host install uses `--no-index --require-hashes --only-binary :all: --no-deps`, followed by `uv pip check`. |
| App, history and ownership | Distinct root-local `app`, `shared`, `ownership`; capture `shared/projects`, import `shared`, empty trial `workspace`. No personal history/config/keys copying. Inherited environment/tool-specific OAuth and external files remain outside this isolation. |
| Launch and TLS | Private `Launch Unified.lnk`, exact venv Python and retained uv/Git paths. Normal CLI migration/TLS/ownership chain, numeric HTTPS loopback 8942, `serve --no-open`, then real owner `open`. Public CA export only; CurrentUser trust is the user's explicit certificate-viewer action, never an agent/installer import. |
| Failure and rollback | No old install, pointer or global launcher is replaced. Failed partial root is retained with stage-only private log; no mutation retry. Download-only retry is bounded. Use the previous launcher to roll back. |

Bootstrap directory pins permit WRITE sharing only for its own private tool
mutation parents (`root`, `python`, `python-bin`, `cache`, `temp`) so uv can atomically
rename staged downloads. ACL/handle identity validation and no-DELETE sharing
remain; traversal ancestors and downloaded/extracted artifact files retain their
strict sharing. The native offline fixture checks this exact rename boundary.

Setup reruns are **side-by-side**, not an in-place updater: each gets a new
private root and leaves previous roots intact. Do not repeat setup to relaunch;
use the printed private shortcut. Relaunch restores helper/root settings and,
when port 8942 is already occupied, attempts only real pinned owner entry; it
does not reconfigure, adopt or stop that listener. A refusal stays a refusal.
If setup failed, retain the printed root and stage before choosing a fresh retry.

Keep the foreground console open; **Ctrl+C** requests normal host shutdown and
the launcher retains the exact child until exit. Closing the browser does not
stop it. There is no sign-in startup or background-lifetime promise.
If TLS trust is missing, the launcher prints the public CA path and SHA-256,
explains the scope, and offers `REVIEW` to open the certificate viewer.
The user chooses Current User / Trusted Root only when policy permits; the
launcher rechecks trust before real owner entry. It never bypasses a warning
or requests a password/Hello PIN. Complete standard AI connections/model
onboarding in the web UI; inherited API keys are not automatically configured
connections, and a ChatGPT subscription is not OpenAI API billing.

Uninstall is deliberately data-preserving until chosen: stop the foreground
host, remove only the exact trial CA entry **yourself** if you imported it and
no longer need it (see the manual runbook below), then remove only the printed
private prototype directory when its data is no longer needed. No registry,
PATH, startup task or global tool restoration should be necessary. Trust removal
and data deletion are never automatic.

### Qualification still required

Portable capsule/command-model tests are not absent-prerequisite Windows proof.
Before giving a resolved paste to another user, retain these focused results:

1. Reuse unchanged native offline mechanism evidence from
   [run 42057508481](https://github.com/microsoft/amplifier-unified/actions/runs/42057508481)
   at `bf3e851a191907165f0a2e01b3ee6b8cf94fcd59`: both standalone fixtures
   under PS5.1/PS7, plus two PATH cases and observed fixture cleanup. Its tokens
   were elevated, Desktop policy Unrestricted and Core RemoteSigned. These are
   native mechanism/model checks, not downloads, ordinary-user policy or app
   installation. Changed capsule inventory/tag checks have portable evidence
   only; this join does not claim another native run.
2. On an authorized disposable ordinary Windows account, run the exact published
   paste with no developer tools on PATH. Check helper hashes/licenses, managed
   Python identity, complete offline host graph, spaces/Unicode and restart from
   the private shortcut without tool PATH.
3. Exercise bad hash, blocked proxy/policy, partial package failure and duplicate
   setup without replacing prior installs. Confirm listener conflict refusal,
   user-controlled CA trust, real owner entry and standard onboarding.
4. Confirm Stop/owned-descendant cleanup, Ctrl+C, saved-history relaunch and
   nonzero-host-error reporting. Keep native product/device/live-provider,
   clean-machine bootstrap and installer/background/Scheduler claims separate.

### Prepared real-prerequisite CI check — not dispatched

`.github/workflows/windows-prototype-cold.yml` is a **manual-only, single-job**
driver for the real `pinned-vcs` installation with `-NoOpen`. It is not the
offline command-model job and does not run any broad platform/app/runtime suite.
The original installer remains byte-identical to `d9752da50d4afe16c5fb1e67b499bae146a48cd2`.
The driver requires its exact reviewed checkout SHA, the published manifest URL
and its reviewed SHA-256. No moving `main`/`latest` asset or unreviewed candidate
is admitted.

The assembled candidate supplied for review is a **53,046,193-byte** capsule,
SHA-256 `c35e1a66b4de88aebc871bd8a6b025eb8475c0f6f525adc44455fd0688fa58f8`,
and a **443-byte** manifest, SHA-256
`a54ea968ba1c9f0bb1fb5f182edee3a3c53c707de8cddb3533bee2fae5935b57`.
Its ordinary managed Python selection is **3.13.15**, supported by the pinned
uv download metadata but not yet executed by this driver. These are assembled
input identities, **not publication or cold-install evidence**.

The planned release path is
`releases/download/windows-prototype-1c4-d9752da5/windows-prototype.json` and its
matching `windows-prototype.zip`. Both remain **unpublished planning locations**
until the publication owner supplies the actual immutable release/asset receipt.
The driver first makes one bounded unauthenticated HTTPS GET and verifies the
exact manifest bytes. Missing or different assets refuse before installation.
Do not dispatch merely because the plan names a URL.

**Runner readiness is a separate delivery dependency.** The unchanged installer
refuses Windows Server, build numbers below 22000, non-x64 processors and elevated
tokens. The current `windows-latest` workflow target must not be presented as a
successful-install route: hosted runner facts can violate that gate. Preflight
records product type, processor architecture, elevation and refusal reasons and
fails **before installation** on an ineligible host. A future authorized owner
must select a confirmed Windows 11 client x64 runner operating with an ordinary
non-elevated token; this local preparation does not provision or enroll one.
Do not change the installer, bypass its gate, demote a process or create a user
merely to obtain a green result.

After source review, publication/availability verification, conforming runner
selection and authorization of the one future run, the responsible owner can use:

```sh
gh workflow run windows-prototype-cold.yml --repo microsoft/amplifier-unified --ref REVIEWED_DRIVER_BRANCH -f expected_head=EXACT_REVIEWED_DRIVER_COMMIT -f manifest_url=ACTUAL_PUBLISHED_EXACT_MANIFEST_URL -f manifest_sha256=a54ea968ba1c9f0bb1fb5f182edee3a3c53c707de8cddb3533bee2fae5935b57
```

That is a future operator template, not an instruction for an information worker
and not a command executed during driver preparation.

1. The setup Python/Git belong only to the controller. The actual PowerShell
   5.1 installer child has **only System32 and Windows on PATH**, with standard
   `USERPROFILE`/`HOMEDRIVE`/`HOMEPATH` retained. Runner tokens, provider keys and
   package-manager overrides are not passed. Discovery must show no Python,
   launcher, Git, uv or PowerShell 7 on this PATH. OS-provided curl/WSL may still
   exist and are reported without invocation. No global tool is uninstalled.
2. The unmodified installer performs the real private helper downloads, managed
   Python installation, 60 hashed wheel installs and five isolated Git builds.
   The driver verifies the actual 65-distribution metadata, Git origins,
   backend/runtime inputs, retained helper hashes/notices, absolute CLI version,
   actual COM shortcut target/arguments and a fresh child `launch.py --check`.
   It does not serve the app, open a browser, configure TLS, read provider keys,
   change trust or prepare a worker.
3. Each phase uses the maintained native Job owner, explicit controller
   `sys._base_executable`, finite deadlines and bounded private pipe capture.
   Receipts are saved **before** process admission. Deadline/forced descendant
   cleanup/truncated output/unknown custody cannot become PASS. Failed partial
   installation roots and private diagnostics are not automatically removed or
   replayed. CI runner disposal is not a cleanup check or durable log retention.
4. Successful disposal is limited to the one newly observed direct LocalAppData
   install root after native owner/ACL/reparse validation, unchanged file
   identity, zero retained Job processes and a separate process-absence check.
   No prior root is adopted or deleted. Only bounded fixed-field `result.json`
   is uploaded; private root addresses, process IDs, SID and raw logs are not.

This is a prepared future CI check, not a completed installation. An elevated or
Server runner is an **explicit prerequisite refusal**, not a weaker PASS with
disclaimers. A conforming runner can still have permissive execution policy and
developer tools outside the child PATH; **restricted-PATH CI does not establish
a pristine machine**.
Execution-policy refusal stays failure; no bypass, alternate script route,
unblock, elevation or policy mutation is permitted. Native PS5.1 grammar and
real installer behavior remain pending until the reviewed driver actually runs.

## Conditional foreground trial runbook

**Proposed instructions, not yet cleared for a user trial.** Commands below are
source-checked against `4f59da50639ad80c87f9201736f58fecddd0cf7d`; that is a
candidate, not public-activation acceptance. The Windows trial owner must supply
an exact qualified commit with owner-browser entry activated before use.
Do not substitute `main`, a moving branch, or the current public release.
The source-checked `4f59` checkpoint deliberately refuses browser entry. Later
integration candidates include the public existing-only credential reader, but
that source change is not foreground CLI or device acceptance. Never bypass a
refusal, relax ACLs, disable ownership, or put the control token in a browser URL.

This is an ordinary-user **foreground** path for Windows 11 x64, Windows
PowerShell 5.1 or PowerShell 7, and Edge or Chrome. It needs no WSL, administrator
terminal, execution-policy bypass or Task Scheduler registration. Foundation
PR456 is merged at `a49c22f0f2e7ad4cee954e3091cd115f7d25d41f`; its passing
mechanism/consumer checks do not qualify the combined Unified app.

### 1. Check prerequisites and install the selected checkout

Have Git, [uv](https://docs.astral.sh/uv/getting-started/installation/) and
ordinary, non-free-threaded **CPython 3.13 x64** available. A system `py` default
of 3.15 can remain installed: select `py -3.13` when using that launcher, or
`uv venv --python 3.13` as below. Do not use an unqualified `python`, install a compiler
to work around missing wheels, or change the system Python.

Run each block in order in the same ordinary PowerShell window. Stop on any
failure. Assignments and invocations are separate statements; these examples
do not require PowerShell 7 syntax or venv activation scripts.

```powershell
$ErrorActionPreference = 'Stop'
git --version
if ($LASTEXITCODE -ne 0) { throw 'Git is required.' }
uv --version
if ($LASTEXITCODE -ne 0) { throw 'uv is required.' }
$QualifiedRef = 'MANAGER-SUPPLIED-40-HEX-COMMIT'
if ($QualifiedRef -notmatch '^[0-9a-fA-F]{40}$') { throw 'Obtain the qualified trial commit first.' }
$Source = Join-Path $HOME ('amplifier-unified-windows-source-' + [guid]::NewGuid().ToString('N'))
git clone --no-checkout https://github.com/microsoft/amplifier-unified.git "$Source"
if ($LASTEXITCODE -ne 0) { throw 'Source download failed.' }
git -C "$Source" checkout --detach "$QualifiedRef"
if ($LASTEXITCODE -ne 0) { throw 'Qualified checkout failed.' }
$ActualRef = git -C "$Source" rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $ActualRef -ne $QualifiedRef) { throw 'Candidate identity mismatch.' }
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Source '.venv'
uv venv --python 3.13 "$env:UV_PROJECT_ENVIRONMENT"
if ($LASTEXITCODE -ne 0) { throw 'Trial environment creation failed.' }
$Python = Join-Path $Source '.venv\Scripts\python.exe'
$Unified = Join-Path $Source '.venv\Scripts\amplifier-unified.exe'
& "$Python" -I -S -c "import sys,sysconfig,struct; assert sys.platform=='win32'; assert sys.implementation.name=='cpython'; assert sys.version_info[:2]==(3,13); assert struct.calcsize('P')==8; assert not sysconfig.get_config_var('Py_GIL_DISABLED'); print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw 'Requires ordinary CPython 3.13 x64.' }
uv sync --project "$Source" --python 3.13 --locked --no-dev --no-editable
if ($LASTEXITCODE -ne 0) { throw 'Candidate installation failed; do not launch.' }
```

This installs non-editably into this new checkout's venv, not the global uv
tool environment. `--locked` preserves the checkout's host dependency resolution;
the project's binary-only Core rule still applies. It does not freeze dynamic
worker/bundle downloads. Record the exact checkout and the trial owner's resolved
host/worker evidence; a version string or newer package is not equivalent.
Do not run global resync/reset or the no-argument background launcher.

### 2. Create private trial state before writing trial payloads

`--data-dir` alone is **not isolation**. Use a new trial root on local fixed
NTFS storage under your real profile. Do not change `HOME` or `USERPROFILE`.
The installed Windows directory helper creates owner-private directories at
creation and validates ancestors; `New-Item` followed by an ACL repair is not
equivalent. An unsafe ACL, junction, network/redirected profile or non-NTFS path
must fail. Do not weaken it; ask the trial owner for a supported location.

```powershell
$Trial = Join-Path $HOME ('amplifier-unified-windows-trial-' + [guid]::NewGuid().ToString('N'))
$Provision = @'
import sys
from pathlib import Path
from amplifier_web.windows_files import directory
root = Path(sys.argv[1])
if root.exists():
    raise SystemExit("Choose a new trial root; existing state was not changed.")
with directory(root, owned_root=root, create=True):
    for relative in ("app", "shared", "ownership", "shared/projects", "export"):
        with directory(root / relative, owned_root=root, create=True):
            pass
'@
$Provision | & "$Python" -I - "$Trial"
if ($LASTEXITCODE -ne 0) { throw 'Private trial provisioning failed; do not continue.' }
$env:AMPLIFIER_WEB_HOME = Join-Path $Trial 'app'
$env:AMPLIFIER_HOME = Join-Path $Trial 'shared'
$env:AMPLIFIER_SESSION_STATE_HOME = Join-Path $Trial 'ownership'
$env:AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH = Join-Path $Trial 'shared\projects'
$env:AMPLIFIER_UNIFIED_IMPORT_HOME = $env:AMPLIFIER_HOME
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" --version
if ($LASTEXITCODE -ne 0) { throw 'Installed launcher check failed.' }
$Workspace = (Get-Item -LiteralPath 'C:\path\to\existing\trial workspace').FullName
if (-not (Test-Path -LiteralPath $Workspace -PathType Container)) { throw 'Choose an existing workspace directory.' }
Write-Output "Candidate: $ActualRef"
Write-Output "Trial root: $Trial"
Write-Output "Launcher: $Unified"
```

Replace the workspace placeholder with an existing, disposable local workspace
you intend the trial to access, preferably containing spaces and Unicode. This
is not a sandbox: tools can use their granted file/network access.
The roots have distinct owners:

| Setting | Trial location and meaning |
| --- | --- |
| `AMPLIFIER_WEB_HOME` / `--data-dir` | `app`: Unified app configuration and state |
| `AMPLIFIER_HOME` | `shared`: shared settings, keys location and native transcripts |
| `AMPLIFIER_SESSION_STATE_HOME` | `ownership`: Foundation execution ownership, **not transcripts** |
| `AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` | `shared/projects`: capture location |
| `AMPLIFIER_UNIFIED_IMPORT_HOME` | `shared`: legacy import scan root, not the personal `.amplifier` |

The workspace's `.amplifier/settings.yaml` and `settings.local.yaml` still apply.
Normal startup also runs additive settings migration, which can write workspace
settings from legacy `.amplifier-unified` files. Review these inputs or use a
disposable workspace copy; do not claim existing workspace files are untouched.
Inherited environment credentials, external OAuth stores, Git configuration,
proxy settings, caches and tool-specific homes are not isolated by these roots.
Do not copy personal shared settings/history or create a new `keys.env` for this
trial. Use an existing provider API key already available to the host environment
first; never print it, paste it into chat or place it in a command argument.
An OpenAI API account/key with API billing is distinct from a ChatGPT subscription.
If no suitable credential exists, leave the live-provider check pending and use
the app's connection UI only after its native credential path is qualified.

### 3. Set up TLS and review the public CA

Keep all connection options **before** the subcommand, as the CLI parser requires.
Persist the dedicated numeric loopback endpoint before certificate creation:

```powershell
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" config set port 8942
if ($LASTEXITCODE -ne 0) { throw 'Port configuration failed.' }
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" config set bind '[127.0.0.1]'
if ($LASTEXITCODE -ne 0) { throw 'Bind configuration failed.' }
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" config set public_origins '[https://127.0.0.1:8942]'
if ($LASTEXITCODE -ne 0) { throw 'Origin configuration failed.' }
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" setup-tls
if ($LASTEXITCODE -ne 0) { throw 'TLS setup failed.' }
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" setup-tls status
if ($LASTEXITCODE -ne 0) { throw 'TLS inspection failed.' }
$CaFile = Join-Path $Trial 'export\trial-ca.pem'
$CaPem = & "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" setup-tls export
if ($LASTEXITCODE -ne 0) { throw 'Public CA export failed.' }
$CaPem | Set-Content -LiteralPath "$CaFile" -Encoding Ascii
& "$Python" -I -c "import sys; from pathlib import Path; from cryptography import x509; from cryptography.hazmat.primitives import hashes; print(':'.join(format(b,'02X') for b in x509.load_pem_x509_certificate(Path(sys.argv[1]).read_bytes()).fingerprint(hashes.SHA256())))" "$CaFile"
if ($LASTEXITCODE -ne 0) { throw 'Exported CA inspection failed.' }
```

Require `TLS listener: ready` and compare the exported certificate's SHA-256
fingerprint with `setup-tls status`. This hashes the X.509 certificate, not the
PEM file bytes. Explicit ASCII avoids PowerShell 5.1's UTF-16 redirection default.
Only the public CA is exported; never export `ca.key`, `leaf.key` or auth files.

**Security-sensitive user action:** only after the fingerprints match, decide
whether to trust this trial CA for your Windows account. A trusted root can
authenticate certificates it signs; this is not merely dismissing a warning.
If your device policy permits it, you may run the following yourself in the
same ordinary-user shell. An agent must not perform this trust-store change.

```powershell
$ImportedCa = Import-Certificate -FilePath "$CaFile" -CertStoreLocation Cert:\CurrentUser\Root
$TrialCaStorePath = 'Cert:\CurrentUser\Root\' + $ImportedCa.Thumbprint
Write-Output "$TrialCaStorePath"
```

Record that exact store path for later user-controlled removal. The store's
Thumbprint identifies the entry; it is not the SHA-256 verification above.
Do not use LocalMachine trust, change organizational policy or bypass a browser
certificate warning. If trust is prohibited or the browser still rejects HTTPS,
stop browser entry and retain that limitation. `TLS listener: ready` proves
loadable local material, not browser trust or authenticated chat.

### 4. Start the foreground host and open the owned browser

Before serving, open a child PowerShell window that inherits these process-only
environment settings. It will be used for `open`; it does not inherit `$Unified`.
Keep the printed absolute launcher path.

```powershell
Start-Process powershell.exe -ArgumentList '-NoProfile', '-NoExit'
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" --workspace "$Workspace" --port 8942 --bind 127.0.0.1 --public-origin https://127.0.0.1:8942 --no-open serve
```

Keep the first window running. This is the normal CLI startup chain, including
the repair lock, settings migration, workspace configuration, TLS loading and
host ownership. Do not call internal server constructors to skip a failed stage.
Do not stop an unrelated listener if port 8942 is busy. A listening message proves
socket binding, not browser authentication, first-worker preparation or chat.

**Only with the trial owner's qualified browser-activated commit**, in the child
window set the printed absolute launcher path, then invoke the real CLI:

```powershell
$Unified = 'C:\absolute\path\to\trial-source\.venv\Scripts\amplifier-unified.exe'
& "$Unified" --data-dir "$env:AMPLIFIER_WEB_HOME" --port 8942 --bind 127.0.0.1 --public-origin https://127.0.0.1:8942 open
if ($LASTEXITCODE -ne 0) { throw 'Owner browser entry failed; retain the refusal.' }
```

Do not use a globally installed launcher, a fresh shell without the trial
environment, a raw URL/token handoff or a password/Hello-PIN workaround.
The launcher requires app-owned leaf-pinned HTTPS before reading/sending its
control credential. If entry is disabled, that is an expected qualification
fence, not permission to bypass it.

### 5. Record what actually works

After entry and the native configuration path are qualified, open **Settings →
AI connections → Connect AI**. Choose the intended API service and **Use the key
already on this host**, save/check the connection, and choose a supported model.
Select that connection/model for the trial conversation. A key in the environment
alone is not a saved connection or a working model; retain any setup failure.
These checks contact the provider and may use its API allowance.

Use the existing trial workspace and selected default bundle.
Check a first response and a small file-backed task with UTF-8 output
attachment/readback. Record the selected provider/model and resolved worker graph
without secrets. Runtime download/setup can take minutes; startup alone is not
default-bundle or live-provider acceptance.

Test **Stop** while owned descendants are active; confirm cleanup, not just a
stopped spinner. In the serving window, **Ctrl+C is ordinary foreground shutdown**,
not a product failure. Wait for shutdown before restarting with the same roots
and command. Reopen with the same launcher and verify saved history plus a second
turn. Do not start a competing host or delete ownership state to force recovery.
Closing this foreground terminal stops the host; background lifetime is not
being tested.

After the trial and shutdown, remove only this trial's CA if you no longer need
its trust. Closing PowerShell or deleting trial files does **not** remove trust.
This is another security-sensitive **user-only** action. In the original shell,
inspect the exact entry and its thumbprint first; if the variables are gone,
restore `$TrialCaStorePath` from the exact path you recorded, not a subject search.
Do not remove certificates by wildcard or name:

```powershell
Get-Item -LiteralPath "$TrialCaStorePath" | Format-List Subject, Thumbprint, PSPath
Remove-Item -LiteralPath "$TrialCaStorePath" -Confirm
```

Confirm removal only after matching that entry to the trial import. Keep the
trial state for diagnostics until the trial owner has reviewed the result.

| Can be prepared now | Still requires exact acceptance |
| --- | --- |
| Source review and this conditional runbook | Native installed cold startup, ownership, Stop/descendant cleanup and restart checks for the selected candidate |
| A separate checkout after the owner selects its ref | Public CLI/browser entry activation and the rendered owner grant-to-cookie flow |
| A planned ordinary-user device trial | Actual Windows profile/ACLs, Edge/Chrome trust, AI-account connection, default bundle/tools and live-provider response |
| Foreground-only scope | Installer, no-argument background launcher and Task Scheduler lifecycle; clean-machine bootstrap remains separate |

In-app workspace creation/starters, publishing and cross-host transfer are outside
this initial trial. Unsupported controls must report unavailability, not bypass
retained fences. Synthetic native CI can qualify scripted responses without paid
calls; it cannot establish these device/live-provider observations. No native
trial, trust import or live-provider execution is claimed by this documentation.

## Original design assessment

Source assessment: Unified `7b9358bf6443cc003edc1f3c380da8d01a8c2f5e`,
October 31, 2026. Recheck source and resolved upstream revisions before implementation.
The [current setup guide](../../README.md#set-up-unified-on-your-computer)
continues to cover Linux, macOS and Windows through WSL.

## Intent and boundary

Requested direction:

> Let's design and document the more turnkey approach for vanilla Windows
> installation, fixing whatever is missing to make it work with Windows along
> with the currently supported environments.

Interpretation: a normal Windows user starts in PowerShell, installs Unified
without WSL, and reaches a useful authenticated conversation without selecting
Python, installing a compiler, or learning service management. Preserve existing
Linux, WSL and macOS behavior. This is a native **backend** deployment of the
existing web app, not the planned Windows desktop client.

The triggering report showed `uv tool install` selecting CPython 3.15 and
compiling PyYAML 6.0.3, then failing for lack of Microsoft C++ Build Tools.
That is user-supplied evidence, not a Windows reproduction from this assessment.
Selecting 3.13 avoids that interpreter-selection problem; it does not fix the
runtime blockers below. Installing Visual Studio is not the proposed solution.

## Recommended experience

The authentication/lifetime policies below define the proposed experience.
Implementation details and platform qualification remain pending.

1. Run one PowerShell bootstrap as the signed-in user. It explains what it will
   install and where; no administrator terminal or machine execution-policy change.
2. Reuse valid helpers or obtain user-local uv and Git, then a managed ordinary
   CPython 3.13. Do not invoke the `python` Store alias or change system Python.
3. Verify a stable release and its platform support record. Install the host and
   prepare the default worker graph without native compilation.
4. Register and start a least-privilege, per-user logon task. Verify the expected
   authenticated host identity before opening a browser.
5. Authorize that browser from the owner's launcher, then connect an AI account
   in the app. Package installation, runtime readiness and first response remain
   separately reported states.

Initial qualification target: Windows 11 x64, built-in Windows PowerShell 5.1
and PowerShell 7, Edge and Chrome. Windows ARM64, Windows 10 and other Python
versions require their own results; none are silently excluded from future work
or inferred supported from the universal Unified wheel.

### Bootstrap contract

Add `scripts/install.ps1` only with its executable Windows checks. It is a thin
bootstrap; retain lifecycle, diagnostics and recovery in Python owners.
Use `Invoke-WebRequest` with explicit failure handling, TLS verification and
bounded downloads. Neither separate curl nor Bash should be required.
Publish a review-first download/run path beside any one-line convenience form.
Do not publish a command pointing to a script that does not yet exist.

Detect actual OS and interpreter architecture, not only the current shell's
architecture. Resolve latest once to an exact stable release; support explicit
version, no-open, verbose and anonymous-public-download options equivalent to
the existing installer. Reject unsupported tuples before replacing a working
installation. Private logs identify the failed stage and one next action.

Use the existing uv installation when valid. Otherwise obtain an official
Windows uv artifact with pinned identity/integrity checks. While Git sources
remain dependencies, reuse valid Git or provision verified user-local Git from
its official distribution, including license notices and an exact receipt.
Do not silently install a machine-wide Git package or require winget.
Record provenance and constrain archive extraction to the owned directory.
Refresh only the child/current process PATH; preserve personal Git configuration.
An anonymous retry applies to that download process, not global account settings.

Request ordinary non-free-threaded Python 3.13 explicitly. Let uv manage it
alongside any system Python. Use absolute helper and launcher paths so no terminal
restart is needed. Do not rely on whichever `python` happens to resolve first.

Verify the wheel against the release checksum before installation. A checksum
served beside an artifact is integrity evidence, not an independent signature.
Preserve the current Core binary-only rule. Qualify all default host/worker
native dependencies and fail with a supported-package explanation rather than
trying MSVC/Rust builds. Pure-Python Git packages may still need packaging.

Current manifests follow component branches. Preserve that update policy:
record the exact resolved host and worker graphs, qualify candidates before
activation, and retain the prior qualified generation for rollback. Do not
convert a one-time tested revision into an undocumented permanent pin.
For initial installation, distribution-owned constraints or a verified wheel
set may reproduce a qualified graph; define and check that artifact before
advertising reproducibility. A top-level wheel alone does not freeze Git dependencies.

Re-running setup must reuse the same data identity, refuse an unmanaged startup
entry, and retain chats, settings and credentials. Failed installation must not
reset user data. Removal unregisters only owned startup/program files; data
removal remains a separate explicit action.

## Platform boundaries

### Authentication that works with Windows Hello accounts

Accepted policy: the authenticated owner's Windows session can authorize a local
browser through the launcher. Identify the owner by process-token **SID**, not
display name, environment variables or a guessed email address.
Do not require a Hello PIN in a password form: it is not an account password.
A `LogonUser` password-only path would not cover all passwordless Microsoft
and Entra accounts.

The owner launcher authenticates using the private existing local control
credential and requests a distinct short-lived, single-use browser grant.
Bind it to the host/data identity and exact origin. Store a verifier, consume
atomically, and exchange for the existing HttpOnly session cookie.
Do not place the long-lived control credential in a browser URL.
Design the grant handoff to avoid request/access logs, referrer leakage and
persisted browser history; strip transient browser material immediately.
Qualify CSRF, login-CSRF, expiry, replay and cross-origin rejection before use.

Loopback alone is never authentication. Preserve Host/Origin checks, API
authorization, TLS policy and browser-cookie protections.
The current launcher implementation requires an app-owned, private-read leaf
certificate pin and HTTPS **before reading or transmitting the control bearer**.
Validating a health JSON response after sending the bearer is too late: another
local account could occupy the port while the host is stopped. This is a
listener-authentication requirement, not a new Windows Hello challenge.
This delegates existing OS-session authority; it does not prove a fresh Hello
challenge. A new native Hello challenge is a different product/security promise.
Keep PAM authentication unchanged for existing POSIX deployments.

Remote browsers need an explicit owner-approved browser-pairing design over
trusted configured HTTPS. Do not silently reuse terminal/companion grants with
broader authority. Until that path is qualified, report its limitation rather
than offering the unusable Windows password form.

### Background lifetime

Implementation choice for the accepted lifetime: an interactive-token Scheduled Task under the owner SID,
least privilege, with a user-logon trigger and no stored password.
Do not use LocalSystem or S4U to avoid account-password prompts.
Use absolute executable/data/workspace/shared-state paths and explicit quoting.
Configure indefinite execution, restart-on-failure and battery behavior
deliberately. Automatic logon startup must not open a browser.

Work survives browser/terminal closure while the owner remains signed in.
This model does **not** promise execution after sign-out or before sign-in.
If that is required, design a separate headless service deployment, including
network credentials and ownership. Do not pretend a logon task provides it.

Integrate install/start/stop/restart/status/logs with existing lifecycle commands.
Task registration is not health: verify version, data identity, authenticated
response and actual task process. Preserve the single host/reset exclusion.
An occupied port or unrelated task must never be adopted or terminated.
If policy blocks registration, explain the failure and offer explicit foreground
`serve` once that path itself works; do not silently elevate or report turnkey success.

### Storage and execution safety

Keep each mechanism with its existing owner; avoid a general portability
framework or duplicated conversation authority.

| Boundary | Required Windows behavior |
| --- | --- |
| Private storage | Owner/SYSTEM DACLs on app-owned secret storage and temporaries before secret writes; validate existing/custom roots. POSIX mode checks do not prove Windows confidentiality. |
| Contained file operations | Handle-based identity, reparse/junction refusal and replacement detection for workspace creation, attachments, outputs, themes and task storage. Retain bounded reads and configured access boundaries. |
| Locks and durable writes | Preserve shared host/exclusive repair locking, memory serialization, and Foundation single-writer/fence semantics. Flush and atomically replace on the same volume; never delete the destination first to bypass a sharing failure. |
| Worker transport | Bounded Windows-compatible pipe reads, explicit UTF-8 protocol bytes, separate logs, EOF and cancellation that cannot strand shutdown. Retain message-size limits. |
| Process lifecycle | Passive process-handle liveness, never Windows `os.kill(pid, 0)`. Job Objects contain owned workers/preparation descendants before tool execution; graceful cancellation precedes bounded termination. Unknown effects remain unknown. |

Python 3.13 provides Windows `os.fchmod` and special handling for new
`mkdir(mode=0o700)` directories. Do not misclassify `fchmod` as an unavailable API
at this Python floor. These APIs do not validate existing DACLs, containment or
directory durability. Inspect actual Windows guarantees at each boundary.

Resolve host venv interpreters as `Scripts/python.exe` on Windows and `bin/python`
on POSIX, using an existing owner/helper where possible. Do not rewrite intentional
Linux **guest** paths in remote test-runner protocols.
Job containment must not kill the detached update helper that must outlive the old
host. Validate nested-job restrictions and escape prevention separately.

#### Private config and credential boundary

The first filesystem increment covers `config/server.yaml`, `session-secret`
and `control-token`; it is not a claim that all application storage is adapted.
Pass the original expanded absolute Windows address to the native boundary.
Resolving it first can remove the junction/reparse component being inspected.
Keep app data, shared settings/native history and Foundation coordination roots
distinct and preserve their explicit overrides.

Pin and inspect each traversal ancestor before creating or opening its child.
Denying delete sharing protects against rename, but sharing alone does not
prevent reparse conversion through attribute access. Validate actual handle
identity, type, filesystem and ACLs. Treat the owner and narrowly identified
Windows system authorities as trusted; reject ordinary foreign mutation rights
on traversal ancestors and ordinary foreign read access to private state.
Do not silently rewrite an existing custom root's ACLs.

New private directories and temporary files receive their security descriptors
at creation, before content is written. Credential initialization publishes
without replacing an existing credential and reads the winner; concurrent
processes must converge on one value. Existing empty, malformed or unsafe
credentials fail rather than regenerate. Config replacement uses a flushed
same-directory temporary and never deletes the destination to bypass sharing
errors. Atomic visibility does not establish namespace power-loss durability.

Shared settings migration, provider OAuth files, TLS material and SQLite/WAL
storage remain separate privacy checks. Do not mark browser authorization
ready merely because its ticket protocol passes with synthetic credentials.

#### Owned process launch boundary

Python 3.13's documented `STARTUPINFO.lpAttributeList` exposes `handle_list`,
not `job_list`. An ignored dictionary key is not creation-time containment.
Do not launch the payload and assign it to a job afterward.

A bounded candidate mechanism may launch a trusted, isolated standard-library
guard with `-I -S` and no payload/application imports. It must exit on control
EOF or a finite startup timeout. The host retains a non-inheritable Job Object
with kill-on-close, verifies the exact guard process identity and membership,
then releases a single execution gate. The payload may start only afterward;
it receives exact argv, environment, cwd and binary streams without shell
reinterpretation. The guard must never inherit the job's lifetime handle.
Assignment or nested-job failure refuses execution, not an uncontained retry.

The current isolated guard implementation supports **CPython 3.13 only**.
Its trusted bootstrap executable must explicitly name the host-controlled
`sys._base_executable`: the standard Windows venv executable is a redirector,
so its initial process ID is not the interpreter child's process ID. Do not
weaken exact process-identity checks to accept it. A contained payload may still
use its own qualified venv after the guard is assigned and released. The narrow
asyncio I/O-finalization bridge is version-specific; newer Python versions
require separate qualification rather than an assumed `>=3.13` guarantee.

Observe process exit separately from pipe EOF: descendants can keep stdout
open. After graceful shutdown, or after a finite command exits successfully,
settle remaining owned descendants and verify zero active job processes before
claiming cleanup. Preserve failed/unknown cleanup ownership and external-effect
receipts. Job termination does not undo a provider request, file write or cloud
resource. Qualify this mechanism on Windows before integrating runtime callers.

Optional Linux controllers must not crash ordinary startup. Gate unavailable
actions explicitly; preserve retained resource records without launching, deleting
or silently repairing them. Local Linux Docker/Incus testing is not a native
Windows prerequisite. Remote Linux runners retain their own supported contract.
Publishing and task transfer require their own faithful Windows durability/locking
checks or explicit limitations; do not weaken their promises to make startup pass.

## Source-backed implementation dependencies

The following are observed source assumptions, not recorded Windows test failures.
References identify the assessed source; line numbers may move during implementation.

| Work and owner | Evidence at the assessed revision | Required increment and falsifier |
| --- | --- | --- |
| Unified startup/repair | `cli.py:214`, `reset.py:391`; `service.py:778`; `amplifier_publishing/publisher.py:253`; `amplifier_portability/protocol.py:37` | Portable shared/exclusive repair lock; isolate optional constructors. Fails if `serve` imports `fcntl` or skips ownership/durability. |
| Foundation session state | Locked Foundation `amplifier_foundation/session/shared_state.py`, `_ensure_supported` and writer acquisition; consumed at `runtime_worker.py:838` | Windows ownership/private storage/atomic publication backend through the existing API, plus cooperative release transport. Fails if two contenders write or crash recovery overwrites newer history. |
| Memory store | Locked memory `src/amplifier_memory/store.py:69`, `:612`; reached through `memory_consolidation.py:7` | Portable exclusive store lock and non-inheritable descriptors. Fails if host import requires `fcntl`, timeout mutates data, or a dead holder strands the lock. |
| Unified login/lifecycle | `auth.py:141`, `deployment_service.py:264`, `installation_health.py:37`, `app_updates.py:29` | Owner authorization and Windows task backend shared with health/update/reset. Fails if login requires PAM or task registration is reported as readiness. |
| Unified I/O/process/files | `runtime_worker.py:26`, `:1696`; `generation_leases.py:19`; `workspace_placement.py:151`; `outputs.py:45`; `task_resources.py:95`; `theme_library.py:173`; `managed_deletion.py:497`; `smart_tools.py:605` | Platform-specific implementations preserving containment, encoding and cleanup. Fails on Unicode corruption, junction substitution, unsafe liveness inspection or surviving owned descendants. |

Paths without a package prefix are under `amplifier_web/`. Dependency assessments
must use the exact release-resolution receipts, not a convenient sibling checkout.
The assessed lock records Foundation `8d26ece...`, memory `b011380...`, and
Context Intelligence `a80ce69...`; capture full IDs from `uv.lock` for each check.
Core 2.0.2 already lists `cp311-abi3-win_amd64` in that lock; an abi3 wheel can
support ordinary CPython 3.13. Native-wheel availability is not runtime qualification.

Audit the default tools/providers as well. A tool named Bash may require Git Bash
or need an explicit Windows implementation. Do not silently reinterpret shell
programs as PowerShell, install WSL, or remove default capabilities. Qualify a real
file-backed task through the exact installed bundle/module graph.

These changes advance optionality and faithful ownership/recovery under
[CB1–CB6](../../contracts/component-boundaries.v1.md) and
[AP2–AP6](../../contracts/agent-protocol.v1.md). No locked contract is amended by
this document. Changes to promised lifetime/authentication or deliberate feature
omissions must be resolved explicitly rather than hidden as compatibility work.

## Delivery order and independent acceptance

Each stage returns exact-source evidence. None is a substitute for the next.

1. **Safety and upstream mechanisms:** land the passive liveness fix; qualify
   Foundation writer/release and memory locks on Windows. Preserve POSIX behavior.
   Integrate upstream only after its merge/CI; do not publish downstream first.
2. **Host runtime:** portable repair lock, storage, optional-controller isolation,
   pipe transport, process trees and default tool behavior. Prove host start,
   first worker, file tool, Stop and recovery against combined candidates.
3. **Owner entry and lifecycle:** apply the accepted policy choices below; implement
   local browser authorization and user-logon task, including health, update,
   rollback and reset. An unchanged data root must remain the same identity.
4. **Bootstrap:** implement PowerShell setup with verified prerequisites and
   release artifacts. Exercise missing/preinstalled helpers, an incompatible
   system Python, partial install, bad checksum, proxy/policy failure and rerun.
5. **Platform delivery:** run the [native acceptance checks](../validation/release-acceptance.md#native-windows-candidate-checks)
   on a clean Windows account and the affected Linux/macOS/WSL regressions.
   Only then add a supported native-Windows command to the README.

Run focused Windows mechanism tests automatically on a real Windows runner;
mocked platform branches on Linux are regression coverage only. Follow the
[local-first release policy](../validation/release-qualification.md); do not add
every unrelated suite as a foreground gate. First support qualification is still
required for the new platform. CI runners with Python/Git preinstalled do not
prove a vanilla-machine bootstrap.

For multi-repository changes, use one isolated combined-source integration
environment as well as native Windows. Retain source revisions, dependency graphs,
artifact hashes, OS/architecture, account class, commands, results and limitations.
Keep private usernames, SIDs, paths, tokens and screenshots out of public evidence.

## Accepted policy choices

| Choice | Accepted policy | Consequence |
| --- | --- | --- |
| Is the signed-in owner OS session sufficient to authorize a browser? | Yes, through a narrow single-use launcher grant. | Supports passwordless accounts without asking for the OS password. A fresh Hello challenge would add a different security requirement. |
| Must work continue after Windows sign-out? | Not for the ordinary user-logon deployment. | Browser and terminal closure are supported; sign-out is a documented lifecycle boundary. Always-on execution needs a separately designed service mode. |

The accepted choices cover only owner-session browser authorization and
user-logon lifetime, not every implementation detail in this document.
No fresh Windows password or Hello challenge is required. A user-logon host starts
again on the next sign-in; unknown tool effects are never replayed.
Remote browser access and architecture coverage remain explicit qualification work,
not an implicit promise or a silent scope reduction.

## Qualification record

This document records the design, source audit and bounded qualification results.
The README selects 3.13 in manual commands and identifies the native-Windows
limitation. No working native Windows installer is claimed. Earlier results below
are historical evidence for their exact candidates, not current release acceptance.

The accompanying safety increment replaces Windows lease liveness signaling with
`OpenProcess(SYNCHRONIZE)` and a zero-timeout wait. Uncertain results retain storage.
Its focused workflow runs the real sentinel on Windows, Linux and macOS; no remote
workflow run was observed during this assessment.

Local verification on Linux with Python 3.13: **14 tests passed**, including a real
subprocess survival check and stubbed Win32 outcomes, using:

```sh
python -I -S -B -c "import sys, unittest; sys.path.insert(0, '.'); suite = unittest.TestSuite(unittest.defaultTestLoader.discover('tests', pattern=pattern) for pattern in ('test_generation_leases.py', 'test_installation_guidance.py')); result = unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())"
```

Local link targets, workflow YAML parsing and `git diff --check` also passed.
Independent source review found no actionable defect in the passive probe.

### Host mechanism implementation increment

The candidate adds Windows `LockFileEx` shared/exclusive repair locking and
bounded worker stdin reading, with explicit binary UTF-8 protocol output. POSIX
keeps its existing `flock` and asynchronous pipe reader. The repair lock file is
stable and never unlinked. A Windows reader uses one daemon thread and a
one-slot request handoff; cancellation stops delivery but does not interrupt a
synchronous OS read. The retained descriptor/thread ends on input/EOF or
worker-process exit. It is not a reusable connection pool.

Independent review found a hidden test dependency: child worker probes loaded
installed Foundation even when the parent used standard-library isolation.
History-only Foundation imports now occur inside the history-reading function,
and the real worker probe subprocesses also use `-I -S`. The focused workflow
includes the complete pipe and repair-lock tests on all three platforms.

Observed on Linux ARM64, Python 3.13.11:

- **37 tests passed** with `-I -S -B`, covering passive inspection, guidance,
  real subprocess lock contention/crash release, and real worker transport.
- **173 tests passed, 1 skipped, 54 subtests passed** in the focused runtime,
  shutdown, reset, installer, guidance and mechanism regression selection.
- **4 targeted delivery checks passed**, preserving passive history inspection,
  ownership checks and duplicate-input refusal after the import isolation.

A broader delivery-suite invocation exceeded its 120-second command budget;
no result is claimed for that invocation. The smaller named checks above ran
separately. Source review and these Linux results do not establish Win32 API
execution, Windows account/privacy behavior or clean-machine installation.

At this stage, Foundation and memory were separate upstream candidates. Their
subsequent merge and CI results are recorded below. The bounded Foundation
ownership increment is not checkpoint persistence or Windows cooperative handoff.
Optional publishing/transfer startup isolation must preserve retained ownership
and transfer fences, not replace unavailable controllers with empty state.
Owner browser authorization, user-logon lifecycle, process-tree containment,
general filesystem compatibility and PowerShell bootstrap remain unfinished.
Native Windows, macOS and WSL runtime execution remain unverified here.

### Subsequent bounded qualification

These results qualify the named increments only. They do not establish an
installed native Windows host or a clean-machine installer.

| Increment | Observed evidence | Remaining boundary |
| --- | --- | --- |
| Foundation ownership and advisory reads | [PR 455](https://github.com/microsoft/amplifier-foundation/pull/455), reviewed head `06a825db4732f7c540b51f1d095d499c67d92b0a`, merged as `99fdb524037fb998b9c36355315e66a3052b662f`. [Candidate CI 41218283216](https://github.com/microsoft/amplifier-foundation/actions/runs/41218283216) passed Windows and Linux Python 3.11/3.12/3.13. Each Windows job ran all 20 native cases, with zero native skips; 2548 total passed and 75 other skips. [Post-merge CI 41267999104](https://github.com/microsoft/amplifier-foundation/actions/runs/41267999104) passed all six Windows/Linux jobs at the merge commit. | Windows Server 2025 build 26100, not minimum Windows 10 or clean Windows 11. Checkpoint/fence mutation and Windows handoff remain unsupported. |
| Memory store locking/encoding | [PR 24](https://github.com/microsoft/amplifier-bundle-memory/pull/24), reviewed head `6820d5cc2cb7701ec9f965c493886a1e89ef22d5`, merged as `5ac75ac85b9972e06936630e347023af107fd364`. [Candidate CI 41215809263](https://github.com/microsoft/amplifier-bundle-memory/actions/runs/41215809263) passed all four Windows/Linux Python 3.11/3.13 jobs: 34 tests per job, no skips. [Post-merge CI 41268245374](https://github.com/microsoft/amplifier-bundle-memory/actions/runs/41268245374) passed all four Windows/Linux jobs at the merge commit. | Selected store mechanisms, not the whole installed runtime. |
| Unified private config files and owned processes | Reviewed local candidates retain lexical paths, handle-bound rename, no-replace credential initialization, exact interpreter identity and bounded pipe finalization. Focused checks: 24 file tests passed/13 native skips; 29 deployment tests passed/1 native skip; 34 process tests passed/11 native skips. | Linux control-flow and real-pipe checks are not native Win32 acceptance. No production process callers integrated yet. |
| Owner browser entry | The reproduced unverified-HTTP bearer disclosure is repaired in the local candidate. Independent real-socket HTTP/wrong-leaf/proxy/redirect checks pass; private credential adapters use bounded pinned-directory reads. | Browser activation still unconditionally refuses pending native credential/TLS and combined rendered-flow qualification. No passwordless-account acceptance is claimed. |

The initial Foundation [CI 41215809534](https://github.com/microsoft/amplifier-foundation/actions/runs/41215809534)
remains failure evidence: `MoveFileExW` replacement failed with access denied
while an advisory reader held the old file open. The repair uses
`FileRenameInfoEx` replace/POSIX semantics. The new native regression passes
release and successor publication while the old reader still returns the
complete original JSON. This evidence is not substituted for Unified's separate
private-file tests.

Native TLS storage/loading, Scheduled Task lifecycle, PowerShell bootstrap,
process caller integration and the native acceptance rubric remain separate
work. Publication of Unified still follows upstream merge/CI; isolated
combined-source testing does not require those merges.
An installed release does not contain local candidate changes merely because
its version is newer than the candidate's base.

### Reviewed local candidates, not native acceptance

The retained local increments now include:

1. **Private files and TLS:** fixed app-owned certificate/key paths, bounded
   reads, held file handles through OpenSSL loading, and a stable bounded TLS
   lock. Focused checks: 49 passed, 7 native skips; independent changed-risk
   review: 24 passed. Per-file publication is not pair-atomic. Partial material
   fails explicitly without automatic CA regeneration.
2. **Interactive Scheduled Task:** owner-SID identity, least privilege,
   live-definition ownership checks and private explicit-replacement backups.
   Executable and working-directory fields reject `%` because Scheduler expands
   those fields; encoded data/state paths retain literal percent characters.
   Focused checks: 98 passed, 2 native skips before the narrow repair; 21 focused
   repair cases pass. The CLI's 26 checks pass after forwarding the selected data
   root. Task state is not health; authenticated host/process correlation, logs
   and automatic managed restart remain unavailable.
3. **PowerShell bootstrap:** pinned, owner/DACL-validated traversal, exclusive
   extraction-root creation, immutable checked-file handles and correct
   PowerShell source quoting. Implementation verification and independent review each observed
   15 portable guards passed, 2 native skips. Neither PowerShell parsing,
   embedded C# compilation nor Win32 fixture execution has occurred here.
   Both release-support and activation gates remain closed.

The focused platform workflow selects the standalone file, process and installer
fixtures without importing installed application dependencies. Windows preflight
requires ordinary x64 CPython 3.13 and both PowerShell 5.1 and 7. A separate
four-dependency job selects configuration, TLS and service policy checks,
explicitly excluding Task Scheduler registration. Local verification of that
selection passed 145 tests, with 9 native skips and 1 deselection. YAML and
embedded Python checks passed. The following native runs supersede the earlier
unexecuted status, but do not establish full mechanism or host acceptance.

### Initial Unified native CI

Draft [PR 1431](https://github.com/microsoft/amplifier-unified/pull/1431)
published only the bounded committed mechanisms, not the remaining local
browser/runtime candidates. The failed runs below remain qualification evidence.

| Candidate and run | Observed result | Unresolved boundary |
| --- | --- | --- |
| `e824e014010b6280324416dab40c66457511509b`, [CI 41269236533](https://github.com/microsoft/amplifier-unified/actions/runs/41269236533) | Linux passed. All 11 native Windows process cases passed. Windows standalone had 3 failures, 1 error and 9 POSIX-only skips. Configuration/TLS had 151 passes, 2 failures, 1 skip and 1 deselection. macOS failed the cwd path-spelling assertion. | Inherited-file/config fixtures, junction command quoting, both PowerShell fixtures and macOS alias handling required repair. No aggregate native pass. |
| `1c57dacb41f3cfa45f7422e4c635880aff8c3ab0`, [CI 41270115320](https://github.com/microsoft/amplifier-unified/actions/runs/41270115320) | Linux and macOS passed. Windows configuration/TLS/service-policy passed: 153 passed, 1 generic TLS skip and 1 Scheduler deselection. Windows standalone ran 139 checks with 3 failures and 9 POSIX-only skips. | The inherited-owner assertion and both PowerShell fixtures still failed. New sharing access strengthened directory pins; native fixture lifetime and structural owner checks still require qualification. |

The repair requests real directory-list access so bootstrap directory handles
participate in sharing enforcement. The Windows PowerShell fixture removes an
inherited cross-edition module path only from its child environment. Private
ownership rules and both activation gates remain unchanged.

Ordinary private native-history persistence is separately proposed in Foundation
[PR 456](https://github.com/microsoft/amplifier-foundation/pull/456), candidate
`1cc7cd0dc0b082f4e61da5b15a5a2b5e138dbb4b`. Its initial
[CI 41269990061](https://github.com/microsoft/amplifier-foundation/actions/runs/41269990061)
passed all three Linux legs and failed all three Windows legs. The Windows
failures include existing fork/store paths with nonprivate creation and the
native warm-append bound. This candidate is not merged or qualified; the original
ownership/store merges above do not qualify these new history operations.

**Next native-CI dependency:** repair the demonstrated failures and verify their
named native cases. The original upstream ownership/store merge and CI
prerequisites above are satisfied; the new private-history dependency is not.
Until the standalone mechanism passes its own native checks, do not integrate
runtime process callers. After that boundary, preserve owner cleanup-failure
custody and generation leases, distinguish guard/payload/worker identities, and
qualify the runtime-sized stream limit. Detached restart and synchronous
standalone reset require their own lifetime boundaries; they are not drop-in
consumers of the asynchronous finite-process owner.

### Installed Linux consumer checks, October 31, 2026

One isolated Ubuntu 24.04 aarch64 DTU, ordinary CPython 3.13.9, exercised
non-editable installed candidate packages. Explicit Foundation, delegate and
memory overrides selected the candidate sources rather than the consumers'
upstream pins. This is combined-candidate evidence, not a published installation.

| Source | Tested identity |
| --- | --- |
| Unified | HEAD `9f94e5355e0e78be8796ce16160af1af30374a8f` plus dirty/untracked source, frozen as snapshot `4067404ec0897fd9c57f071009b58f75bf453a40` |
| Foundation | `06a825db4732f7c540b51f1d095d499c67d92b0a` |
| Memory | `6820d5cc2cb7701ec9f965c493886a1e89ef22d5` |
| Agent SDK and engine | `95877ef54cd8849f8f2066ef3e97ce4c8a1669af`; complete SDK/engine package trees match the SDK-declared `v0.25.0` revision `1da05aa81fa2239d9044b375f21f75ed0e46c89f` |
| App CLI | `631c4bdbcf3dcd2da20456ad6ccef386e778c101` |

Agent retained Core **2.0.1** in its own environment. CLI/Unified retained Core
**2.0.2** in another environment on the same DTU. A single Agent/Unified package
environment is unsatisfiable: the engine pins Core 2.0.1 and Unified requires
Core >=2.0.2. Do not relax either requirement to manufacture co-installability.

The final focused selection passed **341 test executions, zero failures and
zero skips**: 141 in the Agent environment and 200 in CLI/Unified. This includes
Foundation/memory checks in each environment, not 341 distinct tests. Earlier
setup and fixture failures remain retained; no production source was patched.
Nineteen same-process import audits found no candidate source shadowing, and
installed Python bytes matched their selected source packages.

The deterministic roundtrips saved/read UTF-8 memory through the real library,
resumed SDK history, preserved synthetic configuration bytes, and persisted CLI
user/tool/assistant content. Separate-process checks established live-owner
refusal, release, old-capability fencing and successor checkpoint readback
across the two environments. Independent verification repeated the two consumer
roundtrips and ownership sequence with fresh synthetic data, and checked all
208 exported evidence-file hashes before teardown. The owned DTU was removed;
unrelated environments were left untouched.

**Mocked boundaries remain explicit:** scripted provider, CLI first-run and
bundle-assembly seams, and fixture tool bridges to the real memory library.
The shipped memory tool/injector adapters were not mounted. Actual Foundation
loading of the memory bundle and its declarations passed separately. No
live-provider, rendered-browser, native Windows or full-default-bundle acceptance
is established by these checks.

A separate Windows x64/ordinary CPython 3.13 resolution passed for each default
package graph: Unified, Agent and App CLI. Native-only binary rechecks preserved
the selected identities; 24 unique native wheels across 91 selected registry
releases were available and hash-verified. Independent readback verified the wheels,
210 retained evidence files and 97 AMD64 PE members. PyYAML 6.0.3 has a matching
CPython 3.13 Windows wheel but none compatible with ordinary CPython 3.15.
Pure-Python/VCS packaging was not globally prohibited. Wheel availability does
not establish Windows execution, OS-wrapper behavior or optional/dynamic graphs.

Two independent reviews assessed the earlier evidence brief, not source or
DTU execution. Their requested consumer/provenance and native-wheel checks now
have the bounded evidence above. Their native runtime, security/lifecycle and
clean-install gaps remain open. Both activation gates stay closed.