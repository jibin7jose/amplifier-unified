# Acceptance and rollout evidence

This is a proposed acceptance program, not a report of production conformance. The design direction was accepted on 2026-09-22; implementation evidence is tracked in ../IMPLEMENTATION.md. Their semantic promises require implementation evidence beyond document-shape checks.

## Design audit before implementation

- Unified source was inspected at `172fee399ba0b35a78e3c5b914d38bb1457530a6`; the original placement, registration, and managed-chat findings are recorded in CURRENT-BEHAVIOR.md. Later native-history findings identify their own revision in NATIVE-HISTORY.md.
- The Mac Amplifier PWA against the production host was inspected using its visible UI: the current sidebar, managed/workspace draft chooser, explicit workspace field, Chat details modal, Subagent history modal, and right-hand Canvas. No chat was submitted and no settings or files were changed. The original conversation was restored with Canvas closed.
- The mockups adopt that app's observed shell and styling and use the supplied Codex screenshots for organization and drill-down inspiration.
- The original design bundle included document-shape and simulated-prototype checks. Selected rendered designs are retained under `mockups/`. Those checks and images do not prove filesystem, Git, permission, provider, or deployment behavior. Current product evidence is in [implementation status](../IMPLEMENTATION.md).

## Required product scenarios

| ID | Scenario and action | Required observable result | Owner contract |
|---|---|---|---|
| P01 | Name-only creation with unset root, then `~/dev` | One directory and stable registration on the selected host; no saved empty chat | Placement 1–2 |
| P02 | Existing registered, unregistered empty, and populated directories | Open existing or explicit attach; original bytes unchanged | Placement 3 |
| P03 | Rename, change root, and reopen legacy native sessions | Stable identity and history aliases; no folder move or duplicate history | Placement 4,6 |
| P04 | Double-submit, two-client collision, crash after mkdir, lost acknowledgment | Stable receipt; no second allocation; owned partial state recoverable | Placement 5 |
| P05 | Unicode/case aliases, traversal, reserved names, symlink swap | Destination remains inside the resolved permitted root or is refused | Placement 7 |
| C01 | Attach a folder with native chats, voice, artifacts, and existing runtime | Discover native history without import, copies, or replacement IDs | Context 1, History 1–3 |
| C02 | Zero, one, two repos, and supported existing submodules | No compulsory parent Git repo; per-binding state and unchanged originals | Context 2,7 |
| C03 | Context assembly with workspace, repo, and subtree instructions | Provenance and priority observable; AGENTS.md unchanged | Context 3 |
| C04 | Concurrent private drafts and shared-decision edits | Private state excluded; admitted references versioned; collisions visible | Context 4–5 |
| E01 | Two chats write one folder; then authorize independent parallel work | Conflicting writes queue; authorized separate tasks have distinct visible paths | Execution 3,5 |
| E02 | Inspection-only helper, then supported write assignment | Reader cannot mutate source; independent writer area prepared before edits | Execution 1,4 |
| E03 | Expired/stale writer after crash and concurrent owner claim | Managed actions fence stale owner; uncertain external processes reconciled | Execution 3 |
| E04 | Use a folder and edit in ordinary chat, terminal, and editor | All see the same files at the chosen path; no mode selector needed | Execution 2 |
| E05 | App+runtime edits and conflicting dependency versions/ports | Shell, imports, tests, environment, and preview bind the recorded snapshot | Context 6, Execution 6 |
| E06 | Shared document changes after generated draft was based on it | Accept detects version conflict; no silent replacement | Execution 7 |
| H01 | Handoff with staged, unstaged, untracked files and drafts | Preserved supported bytes and baselines; unsupported state blocks transfer | Handoff 1 |
| H02 | Individually passing changes fail in combination | Integration remains unaccepted; helper pass badges do not imply live success | Handoff 2–3 |
| H03 | Unrelated chat/helper requests preview update or release | Permissions checked; one updater; destination/snapshot stated | Handoff 4 |
| H04 | Restart after unknown external effect or half a multi-repo publish | Reconcile exact receipts; no blind replay or fabricated all-or-nothing claim | Handoff 2,5 |
| H05 | Cleanup borrowed/dirty/unpublished/active/unresolved resources | Refusal with blockers; original/history/output bytes retained | Handoff 6–7 |
| U01 | In global New chat, select/create/attach a workspace and cancel/retry | Draft retained; chosen workspace preselected on return; no execution-mode choices | Experience 1 |
| U02 | Pin/unpin, group, search, archive, unavailable paths, duplicate names | Predictable identity; recovery accessible; distinct qualifiers | Experience 2,8 |
| U03 | Inspect sources/output/helper with draft and Canvas tabs open | Draft, focus, tabs, and execution unchanged; explicit context attachment only | Experience 3 |
| U04 | Repeat create/attach/context/inspect/steer through UI and agent | Same action semantics, authority checks, receipts, and errors | Experience 4–5 |
| U05 | Working, unread, failed, needs input, stop requested, unknown | Distinct text/accessibility labels; no success implied by silence | Experience 6 |
| U06 | Keyboard, screen reader, 320px mobile, zoom, light/dark theme | Create, inspect, return, and stop reachable; focus restored; no clipping | Experience 7 |

For E01 and E05, inspect the actual process working directories and resolved dependencies as well as the changed files. Merely observing distinct branch names is insufficient.

For H01–H05, use disposable fixture resources and injected failures. Do not exercise destructive cleanup against a user's borrowed checkout or running Spark instance to prove a design promise.

## Migration gates

Preserve existing registrations and native-history associations. Introduce stable workspace identities with aliases rather than rewriting historical paths. Existing sessions retain their actual working folder until an explicit, supported handoff. A new sidebar does not relocate execution.

Missing folders retain history and recovery references. Workspace chat lists derive from native project history; do not add a private app membership roster. Shared context never automatically imports another chat's transcript. Reclaiming a worktree is not archiving a chat, and archiving is not deletion.

Before enabling new defaults, run comparison fixtures against old persisted state and verify the same conversations, voice entries, outputs, and IDs remain readable. Cover interrupted migration and idempotent rerun. Record explicit unknown states where recovery cannot yet prove an effect.

## Suggested release gates

These historical slice scenarios guide affected implementation acceptance. They
do not add per-release reruns or override the current
[release policy](../../validation/release-qualification.md).

1. Placement/history slice: P01–P05, N01–N04 below, U01–U02, U04 for supported placement actions, and legacy-history preservation.
2. Context/details slice: C01–C04, U03–U06, and browser acceptance with the current PWA shell.
3. Execution slice: E01–E06 plus H01 and failure/restart recovery before enabling separate task working areas.
4. Integration/cleanup slice: H02–H05, exact running preview provenance, and independent verification of resource preservation.

Usability sessions should include an information worker who does not use Git, a developer with existing CLI/TUI folders, and an operator who owns a multi-repo live preview. Have each perform their own scenario without teaching the internal vocabulary first.

## Decisions implemented

- Placement uses the current installation account and an explicit host label.
- Pins appear in one shared Pinned section, are excluded from Recent, and remain available in workspace drill-in. Check identical pin contents during workspace browsing, search, and dragging.
- Recent contains unpinned chats across all locations. Check totals 50 and 51, pages of 40, independent workspace filters, and persisted collapse state for all three sections.

## Decisions left for review

- Default idle working-copy retention: decide from observed storage pressure; avoid an arbitrary deletion timer before preservation evidence exists.
- Non-Git editor adapters: name the supported first-release writers. Unsupported tools must not inherit an unproven isolation promise.
- Shared-direction edits by agents: recommend suggestions by default, with an explicit workspace policy allowing attributed updates later.

These questions refine implementation policy. They do not block reviewing the name-first journey and proposed separation of workspace, history, and execution.

## Added native-history gates

| ID | Scenario | Required result |
|---|---|---|
| N01 | Attach a folder with existing Web, CLI, TUI, root, and child sessions | One native-derived list; roots discoverable, children reachable under parents; no import/copy |
| N02 | Rebuild only derived indexes; read a known native ID through the agent API | Same conversations rediscovered; same native IDs resolve; legacy app aliases still work |
| N03 | Create in Web, continue in TUI, reopen in Web; attempt simultaneous resume | One native transcript and acknowledged writer; no duplicate or silently forked session |
| N04 | Migrate old web-only voice, pending inputs, Canvas references and app IDs | Original bytes and records preserved until lossless shared-history representation is verified |

The actual native slug algorithm, shared reader/writer, and path ambiguity handling must be reused. A new workspace-name slug is not a replacement for the native project slug. A lost or inaccessible native store must report unavailable, not an empty workspace ready for duplicate recreation.
