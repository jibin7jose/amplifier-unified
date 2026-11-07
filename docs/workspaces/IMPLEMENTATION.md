# Implementation and evidence

This page connects workspace behavior to its current owners and exact historical
qualification. It is an index, not a second contract or a current conformance
report. The earlier running narrative mixed September–October results with later
behavior changes; its source, failures and limitations remain unchanged in Git.

## Find current behavior

| Area | Start here |
| --- | --- |
| Intended workspace behavior and contract status | [Workspace vision and contracts](README.md), [family direction](../architecture/VISION.md) |
| Supported actions and implementation entrypoints | [Work capabilities](../WORK-CAPABILITIES.md), [source and ownership map](../source-context.md) |
| Cooperation, assignments and retained replies | [Task coordination](../TASK-COORDINATION.md), [existing-chat membership](assignment-adoption.md) |
| Current task, Stop and continuation | [Task continuity](../TASK-CONTINUITY.md) |
| Full access and scoped configuration | [Configuration authority](configuration-authority.md), [registered-history scope](owned-history-scope.md) |
| Placement, checkout handoff and preservation | [Worktrees](../WORKTREES.md), [execution inspection](../EXECUTION-INSPECTION.md) |
| Task resources, saved results and storage | [Workspace resources](README.md), [Operations](../OPERATIONS.md), [Outputs](../OUTPUTS.md), [Storage](../STORAGE.md) |
| Qualification and release evidence | [Focused release policy](../validation/release-qualification.md), [performance guidance](../validation/performance.md) |

Read the relevant owner's contract, source and affected checks for a proposed
change. Accepted direction, implemented behavior, qualification, publication and
live adoption are separate facts. Historical test counts overlap; do not add them
or treat a dated “remaining gate” as today's release prerequisite. In particular,
earlier count limits and human-only recovery descriptions predate later optional
limits and standing Full authorization.

## Historical qualification

The [complete prior record][historical] is frozen at commit
`c380e459e5417e77d9ae458e9e232c702a452cbe`. Every section below links to that
same immutable record, including failed attempts, exact measured revisions and
acceptance limits. These links preserve existing section entrypoints without
copying the narrative into another permanent archive.

For new changes, retain concise exact-input evidence with the affected owner and
link it where needed. Do not resume appending progress history here. Follow the
[vision and contract guidance](../vision-and-contracts.md) when a promise changes.

<details>
<summary>Browse the historical sections</summary>

| Historical topic | Exact record |
| --- | --- |
| <a id="current-program-coverage--2026-10-14"></a>Current program coverage — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#current-program-coverage--2026-10-14) |
| <a id="combined-candidate-qualification--2026-10-14"></a>Combined candidate qualification — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#combined-candidate-qualification--2026-10-14) |
| <a id="explicit-integration-commands-and-collected-results--2026-10-14"></a>Explicit integration commands and collected results — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#explicit-integration-commands-and-collected-results--2026-10-14) |
| <a id="preserving-checkout-archive--2026-10-14"></a>Preserving checkout archive — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#preserving-checkout-archive--2026-10-14) |
| <a id="loaded-source-provenance-and-combined-candidates--2026-10-14"></a>Loaded-source provenance and combined candidates — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#loaded-source-provenance-and-combined-candidates--2026-10-14) |
| <a id="retained-reported-results--2026-10-14"></a>Retained reported results — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#retained-reported-results--2026-10-14) |
| <a id="consistent-registered-history-scope--2026-10-14"></a>Consistent registered-history scope — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#consistent-registered-history-scope--2026-10-14) |
| <a id="scoped-configuration-administration--2026-10-14"></a>Scoped configuration administration — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#scoped-configuration-administration--2026-10-14) |
| <a id="human-directed-member-resume--2026-10-20"></a>Human-directed member resume — 2026-10-20 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#human-directed-member-resume--2026-10-20) |
| <a id="assignment-admission-and-scoped-stop--2026-10-14"></a>Assignment admission and scoped Stop — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#assignment-admission-and-scoped-stop--2026-10-14) |
| <a id="supported-file-tool-source-checks--2026-10-14"></a>Supported file-tool source checks — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#supported-file-tool-source-checks--2026-10-14) |
| <a id="active-work-awareness-and-advisory-file-intent--2026-10-13"></a>Active-work awareness and advisory file intent — 2026-10-13 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#active-work-awareness-and-advisory-file-intent--2026-10-13) |
| <a id="explicit-repository-selection--2026-10-13"></a>Explicit repository selection — 2026-10-13 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#explicit-repository-selection--2026-10-13) |
| <a id="cooperative-resource-reservations--2026-10-13"></a>Cooperative resource reservations — 2026-10-13 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#cooperative-resource-reservations--2026-10-13) |
| <a id="coherent-commissioned-task-operation--2026-10-13"></a>Coherent commissioned-task operation — 2026-10-13 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#coherent-commissioned-task-operation--2026-10-13) |
| <a id="initial-repair-tranche--2026-10-13"></a>Initial repair tranche — 2026-10-13 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#initial-repair-tranche--2026-10-13) |
| <a id="reply-revisions-watches-and-fair-inbox-admission--2026-10-14"></a>Reply revisions, watches and fair inbox admission — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#reply-revisions-watches-and-fair-inbox-admission--2026-10-14) |
| <a id="remaining-program-gates"></a>Remaining program gates | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#remaining-program-gates) |
| <a id="historical-collaborative-workspaces-candidate--october-6"></a>Historical collaborative-workspaces candidate — October 6 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#historical-collaborative-workspaces-candidate--october-6) |
| <a id="historical-placement-implementation--september-22"></a>Historical placement implementation — September 22 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#historical-placement-implementation--september-22) |
| <a id="historical-placement-evidence"></a>Historical placement evidence | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#historical-placement-evidence) |
| <a id="earlier-release-candidate-validation"></a>Earlier release-candidate validation | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#earlier-release-candidate-validation) |
| <a id="historical-main-integration-refresh"></a>Historical main integration refresh | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#historical-main-integration-refresh) |
| <a id="existing-mechanisms-retained"></a>Existing mechanisms retained | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#existing-mechanisms-retained) |
| <a id="broader-workspace-contract-limits"></a>Broader workspace contract limits | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#broader-workspace-contract-limits) |
| <a id="task-resource-owner-projection--2026-10-14"></a>Task resource owner projection — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#task-resource-owner-projection--2026-10-14) |
| <a id="workspace-local-task-storage-correction--2026-10-14"></a>Workspace-local task storage correction — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#workspace-local-task-storage-correction--2026-10-14) |
| <a id="owned-task-storage--2026-10-14"></a>Owned task storage — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#owned-task-storage--2026-10-14) |
| <a id="return-recaps-and-portable-work-diagnostics--2026-10-14"></a>Return recaps and portable work diagnostics — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#return-recaps-and-portable-work-diagnostics--2026-10-14) |
| <a id="optional-assignment-work-items--2026-10-14"></a>Optional assignment work items — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#optional-assignment-work-items--2026-10-14) |
| <a id="workspace-work-overview-and-combined-check-controls"></a>Workspace work overview and combined-check controls | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#workspace-work-overview-and-combined-check-controls) |
| <a id="chat-write-settings-and-inherited-defaults"></a>Chat write settings and inherited defaults | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#chat-write-settings-and-inherited-defaults) |
| <a id="guided-review-before-continuing--2026-10-14"></a>Guided review before continuing — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#guided-review-before-continuing--2026-10-14) |
| <a id="recovery-and-guided-review-integration-acceptance--2026-10-14"></a>Recovery and guided-review integration acceptance — 2026-10-14 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#recovery-and-guided-review-integration-acceptance--2026-10-14) |
| <a id="managed-checkout-handoff-boundary--2026-10-15"></a>Managed checkout handoff boundary — 2026-10-15 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#managed-checkout-handoff-boundary--2026-10-15) |
| <a id="ordinary-reads-and-searches"></a>Ordinary reads and searches | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#ordinary-reads-and-searches) |
| <a id="idle-worker-retirement-preserves-peer-admission--2026-10-19"></a>Idle worker retirement preserves peer admission — 2026-10-19 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#idle-worker-retirement-preserves-peer-admission--2026-10-19) |
| <a id="settled-failed-members"></a>Settled failed members | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#settled-failed-members) |
| <a id="assignment-model-admission-waiting"></a>Assignment model admission waiting | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#assignment-model-admission-waiting) |
| <a id="retained-terminal-evidence-for-older-failed-members"></a>Retained terminal evidence for older failed members | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#retained-terminal-evidence-for-older-failed-members) |
| <a id="explicit-responsibility-for-existing-chats"></a>Explicit responsibility for existing chats | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#explicit-responsibility-for-existing-chats) |
| <a id="optional-peer-inbox-counts--2026-10-30"></a>Optional peer inbox counts — 2026-10-30 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#optional-peer-inbox-counts--2026-10-30) |
| <a id="optional-message-rate-and-complete-watch-delivery--2026-10-30"></a>Optional message rate and complete watch delivery — 2026-10-30 | [Evidence](https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md#optional-message-rate-and-complete-watch-delivery--2026-10-30) |

</details>

[historical]: https://github.com/microsoft/amplifier-unified/blob/c380e459e5417e77d9ae458e9e232c702a452cbe/docs/workspaces/IMPLEMENTATION.md
