# Work capability access

Work uses the existing patch-editing module from the filesystem bundle. Unified
tracks Work and its dependencies on `main`; an explicit user source registration remains
authoritative. Existing conversations pick up changed composition when remounted,
without replaying prior tools. A saved snapshot keeps its original module roster.

The file-access controls stored in `modules.tools`, `config.tools`, or
`overrides.tool-filesystem.config` apply to
both filesystem writes and patch editing, including nested agents and portable
snapshots. An explicit patch allowlist is intersected with the shared allowlist;
deny lists are combined. Empty allowlists deny writes. This is a file-tool policy,
not an operating-system sandbox for arbitrary shell processes. A denied write is
not an instruction to retry through another tool.

Unified adds source-version checks to the mounted reference `read_file`,
`write_file`, `edit_file` and both `apply_patch` engines. Read an existing file
in the current worker before changing it. If it changed since that read, the
tool refuses the operation; read again and reconsider the edit. A successful
write advances that worker's observation. Children and remounted workers must
make their own reads. Creation and patch move destinations must be absent.
Function patches touch each path once and preflight all sources before editing.

The comparison and operation share short file locks across workers in one app
data home. They do not lock the project, fence shell commands or external
editors, protect unknown/custom writers, or make a multi-file patch transactional.
Failed or interrupted mutations require inspection before retrying. Source
observations are bounded, remain in memory and are not inherited from history.
See [implementation and evidence](workspaces/IMPLEMENTATION.md) for qualification.

## Existing capabilities to discover

| Need | Existing access |
| --- | --- |
| Inspect or choose standing access | `access.get` reads the calling chat's effective Ask/Full mode. The user can choose Full in **Access & approvals**, with `amplifier-unified access full` for the app default, or `amplifier-unified run --yolo "Do the work"` for a task. Agent app actions can inspect this choice but cannot change it. See [standing preference](workspaces/configuration-authority.md#implemented-standing-preference) and [CLI access](workspaces/configuration-authority.md#command-line-access). |
| Find/read other conversations | `app_control` operation `history`, actions `list`, `search`, `read`; bounded pages and workspace scope by default. Explicit `scope=all` uses the registered same-user library. Coordination and recall use the same selector; exact request results and artifacts retain their narrower participant scope. See [read authority](workspaces/owned-history-scope.md). |
| Manage conversations/workspaces | `app_control` operation `list_actions` with `session.` or `workspace.` prefix, followed by `dispatch` |
| Commission a durable peer | `coordination.create` establishes the ordinary peer, explicit placement, initial request and return subscription before releasing work; stable command retries inspect the retained operation rather than create another peer. |
| See active work and declare file intent | `coordination.context/awareness`, `coordination.intent` and `coordination.intent.read` expose bounded active-work context and revision-aware declarations. Overlap is advisory; a declaration grants no file or peer-control authority. |
| Catch up after returning and inspect work timing | `coordination.recap` reads bounded changes from a caller-owned cursor; Related work retains its cursor per browser tab. `coordination.diagnostics` exports scoped numeric phase observations without private content or waking workers. Missing timing samples remain unavailable. |
| Select a repository working copy | Discover `worktree.*` actions for explicit repository selection, create/attach, handoff and reconciliation. Canonical history stays separate from the selected execution folder; no automatic dependency rebinding or sandbox is implied. |
| Diagnose/recover a failed conversation | `session.inspect`, `session.recover`; recovery creates an idle copy and never replays tools |
| Show and revisit artifacts | `canvas.show`, `canvas.select`, canvas application/view actions |
| Inspect exact peer results after source changes | `coordination.reply` retains supported local `path@sha256:<digest>` references when reported; `coordination.artifact` reads bounded saved text/downloads within the exact request relationship. Related work opens the saved version. A snapshot is not independent correctness or integration evidence. |
| Save an authorized retained file for local use | `coordination.artifact.save` takes an exact request, response revision and reference, then saves verified bytes at a relative path in the caller's current working folder. Existing parent folders and current mounted write access are required. Large binaries stream; identical files are verified/reused and different files are never overwritten. Inspect the destination after an unknown return. |
| Inspect loaded dependencies and detect candidate drift | `runtime.provenance` observes already-loaded worker modules without importing code or warming a worker. `integration.capture/check/read/list` retain explicit repository revisions and expected module origins/current hashes in existing command receipts. Advanced task execution controls use the same actions. Mismatch/unknown are explicit; matching files never certify tests, exact imported bytes or isolation. |
| Validate an exact combined candidate | `assignment.integration` explicitly names a commissioned member responsible for combining assignment work. `integration.validate` runs one chosen command through Operations and ordinary shell policy; `integration.collect` records native outcomes, pre/post candidate checks, retained files and partial results per repository. Publication reports are labeled reports; these actions never merge, publish or deploy. Independent chats can validate their own candidates without assignment enrollment. |
| Discover connected tools/resources | `smartTools.*` actions and the mounted tool catalog; availability does not imply a connected account |
| Discover/use skills | Mounted `load_skill` from the configured skills behavior |
| Delegate and steer workers | Mounted `delegate` and `live_worker`; async opt-in and pending receipts remain distinct from completion |
| Control commissioned work together | `assignment.read/start/set/stop`; human-only `assignment.resume` reopens a settled stop without replaying queued work. new commissioned roots and scheduled descendants share bounded admission. Independent existing chats remain separate until the manager uses `assignment.adoption/adopt` under current human authorization; see [existing-chat responsibility](workspaces/assignment-adoption.md). Adding never starts work or invents commissioning history. `assignment.detach` is an explicit user action after work settles. |
| Retire a checkout without discarding files | `worktree.archive/restore` through UI, agent and attached session clients; retains the complete unused app-created Git worktree, including ignored files. No deletion, execution or disk reclamation. |
| Follow exact work outcomes | `coordination.reply/result/subscribe` retain response revisions and guarded notifications; `coordination.watch`, `coordination.watch.read` and `coordination.watch.cancel` provide expiring one-shot root observations. Negative reports never satisfy dependencies. |
| Schedule work | `schedule.preview/create/update/resume` bind the reviewed prompt and timing. Standing Full supports owner-local create/update, normal resume and reconciliation without a fresh human message; Ask retains actual user authorization. `schedule.list/read/pause/cancel/report/reconcile` retain inspection and control. The host must be running for due checks. See [schedules](SCHEDULES.md). |
| Run and inspect managed processes | `operations.list/status/read/wait/cancel` and `operations.submit/write/request` use the existing owner and admission receipts. Execution/stdin require a capable mounted producer and its trusted policy; they are not enabled by every bundle. See [Operations](OPERATIONS.md). |
| Inspect and allocate task resources | `workspace.resources.managed` reads bounded local publishing, native process/kernel lifecycle and task storage. `workspace.resources.provision/read/retire` own private scratch folders, browser-profile folders and local SQLite files; retirement preserves files. They do not launch or confine browsers, provision database servers or expand file permissions. Cooperative reservations remain separate. |
| Read original admitted history | Work's `read_transcript`, separate from cross-conversation discovery |
| Ask durable choices while independent work continues | Shared `question.*` actions; [question and delivery contract](DURABLE-QUESTIONS.md) |
| Voice lifecycle and scoped file access | `call.*` and `permissions.*` shared actions |
| Administer settings | Standing Full preauthorizes the shared configuration effects at all scopes for current roots, qualified native children and explicitly identified trusted systems. Ask retains `capability.read/request/grant/revoke` and reusable scoped root grants. Delayed effects recheck their captured authority. See [configuration authority and consumer audit](workspaces/configuration-authority.md). |

Host services are supplied by Unified, not by the portable Work bundle. The
agent's app-control description and runtime guidance explain these discovery
paths; separate aliases need not duplicate the implementations.

Availability depends on the mounted runtime and negotiated capabilities.
Shared folders remain cooperative. Independent mixed conversations do not support
request-only assignment interruption. Owned folders and SQLite files are supported;
general infrastructure provisioning, isolated running browsers, database servers
and automatic dependency mapping remain unavailable. Source provenance checks
observe loaded origins and current files, not the exact bytes previously imported.
Explicit integration validation separately records the named native command's
outcome; it does not establish test quality or complete dependency isolation. The
[implementation ledger](workspaces/IMPLEMENTATION.md) separates these limits
from delivered behavior and remaining acceptance work.

Qualification includes composition and native mounted-tool checks, installed
packages, browser journeys and bounded real-provider probes for named slices.
Those evidence tiers do not establish general multi-agent efficacy, parity with
another product, publication or adoption of the combined candidate. The optional
web module remains outside this default composition; adding it still requires
qualification of its search-error behavior.

## Ecosystem updates

Work, its tools, live loop, context modules, routing, and worker dependencies follow
`main`. The update check compares both cached source checkouts and the worker's
installed Git revisions with their branches. Installation refreshes an isolated
cache and worker lock, validates the configured bundles without a model turn,
and activates only when work and calls are idle. Resuming mounts the new code.
Automatic checking and automatic installation are separate settings.

Lockfiles and update receipts record what was installed and support rollback;
they do not prevent the next ecosystem update from advancing the branch. The
server's own Python libraries follow their dependency declarations when the app
is installed or updated. They are distinct from the independently updateable
conversation worker libraries. User-supplied fixed sources and local edits remain
explicit choices; the app does not rewrite arbitrary user configuration.
