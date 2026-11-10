# Home and sidebar review basis

This note separates the accepted agreement from implementation evidence and
questions for human review. Baseline: Unified commit
`5da1e1ce137866b780c37d0ebb9a4b63aecfcadc`, released as v0.20.188.
The [review entry](README.md) is the short starting point.

## What the documentation already says

| Owner | Existing direction | Current treatment |
| --- | --- | --- |
| [Family vision](../architecture/VISION.md) | Durable work, bounded discovery, independent clients, truthful recovery. | Keep. These constraints support the UX; a new protocol or architecture is not required for this review. |
| [Client vision](../clients/VISION.md) | Continuity across devices and interfaces. | Now includes arrival, retrieval, and work that begins without project setup. Keep platform-specific layouts separate. |
| [Client orientation and interaction](../../contracts/client-experience.v1.md) | Orientation and economical interaction. | Now includes discovery without execution vocabulary and useful repeated access from independently scrolled surfaces. |
| [Private interaction state](../../contracts/client-state.v1.md) | Separate private interaction from shared work. | Reference rather than duplicate. Remembered UI is not proof that the target client-local persistence migration is complete. |
| [Workspace vision](../workspaces/docs/VISION.md) | Start with purpose, names before paths, preserve native work. | Keep. Logical cross-folder projects remain a separate proposal. |
| [Workspace navigation](../workspaces/contracts/experience.v1.md) | Three collapsible sidebar sections, with other filters behind disclosure. | Amended: those are the Browse organization. Permit the explicit Status alternative and preserve section behavior. |
| [Appearance](../shell/appearance-contract.md) | Presentation can vary; required actions, meaning, and accessibility survive. | Keep and reference. The chosen starting view is separate from current view and screen size. |
| [Adaptive work views](../shell/adaptive-work-views.md) | Detailed Home, Focus, sidebar, and acceptance history. | A short accepted agreement now governs these implementation notes; their preview-only framing is corrected. |
| [Working set](../../contracts/working-set.v1.md) | Bounded catalogs, on-demand detail, no dormant-worker hydration. | Keep. The Home/sidebar contract names the Home/sidebar application of these promises. |

The previous client-direction packet already reviewed Converge and Method on
October 2. There is no earlier explicit request in the available UX conversation
to establish this designer collaboration process. The recent implementation
updated adaptive-work documentation, but had not amended the client vision or the
older sidebar promise. The [accepted amendments](decisions/20261020.md) close that documentation gap.

## Basis for the accepted direction

| Observation or expressed intent | Consequence for the agreement |
| --- | --- |
| The product owner reported feedback that the experience felt too technical and workspaces felt confusing. | Make the intended arrival/retrieval experience explicit. These qualitative reports do not establish a measured usability improvement. |
| The owner chose Focus, Brief, and Studio as different ways into the same work, with Appearance supplying defaults. | Preserve independent preferences and common meaning; leave visual composition open. |
| Return navigation lost useful context; the owner explicitly requested remembered Home state. | Make continuity observable in the return-context promise, including leaving while a search is pending. |
| Live review found no way to pass an item temporarily, a redundant Home label, and a stale chat selection. | Navigation and temporary-pass promises preserve location truth and a temporary pass without disposition effects. |
| The owner requested Browse/Status in one row, a stable header toggle, and Search/New chat on Home's sidebar. | Amend the old sidebar promise and retain deliberate access from both scrolled regions. |
| The initial iteration was limited to Home/sidebar and existing processing. The accepted follow-up extends presentation to the existing workspace and chat browsers. | The bounded-discovery promise and the exclusions prevent design polish from silently expanding session or inference scope. |

The shipped changes are traceable through
[#860](https://github.com/microsoft/amplifier-unified/pull/860),
[#875](https://github.com/microsoft/amplifier-unified/pull/875),
[#886](https://github.com/microsoft/amplifier-unified/pull/886), and the
[returning-demand correction](https://github.com/microsoft/amplifier-unified/pull/889).
The existing adaptive-work notes retain the progression and its limits.

## Review with real work

Run the same journeys before and after a proposed design change. Record the
actual build, device, width, Appearance, starting view, sidebar mode, and whether
the data is synthetic or familiar live work. Record pauses and wrong turns as
well as action counts. Do not turn a small design review into a claim about all users.

1. **Return and orient.** After time away, find one request that needs a decision,
   one unread response, and one running chat. Before opening them, say what you
   expect each state to mean. Record any confusion about “ready” or “done.”
2. **Make a selective pass.** In Focus, pass two items, open a third, then return.
   Find the first item again. Check that passing did not answer or clear it and
   that your search, scope, and reading position remain useful.
3. **Find something known.** Use Browse, Status, and Search to find a familiar
   chat. Keep a different Home filter active. Record which route you expected,
   which you used, and whether either surface unexpectedly changed the other.
4. **Begin without organizing.** Start a one-off chat, then start from a project
   scope. Explain where each will belong before sending. If you want to regroup
   it later, record that need without treating new membership behavior as approved.
5. **Change density and device.** Try all three Home views and both sidebar modes.
   Use a narrow screen, keyboard, and enlarged text. Find Search, New chat, required
   attention, and the way back. Include both open and closed navigation.
6. **Understand a quiet or uncertain state.** Compare a quiet Home with pending,
   unavailable, and failed data in a synthetic preview. Check that old saved chats
   feel available rather than overdue, and that uncertainty does not look empty.

7. **Carry the presentation into browsing.** Open All chats, All workspaces, and
   a workspace. Try all three layouts; find search, sorting, archives, and row
   actions. In the workspace, find Files, Details, plans, and Workspace work.
   Open a chat from a later Focus page and return to the same place. Record whether
   layout choices and active status filters remain understandable.
8. **Find Home without coaching.** Find both the header Home destination and the
   sidebar icon beside Browse/Status. Try a fresh web window, a refresh, and a
   direct chat link; record what destination you expected and received.

Observe real work without answering approvals, sending messages, dismissing
attention, or changing project membership solely to complete a design exercise.
Use synthetic cases for effects that would otherwise change someone's work.
Keep real chat content and private environment details out of public evidence.

## Record one observation

```text
Review build and device:
Journey and intended outcome:
Document, clause, and exact reviewed revision:
What I expected:
What I saw or did (include pauses, extra steps, and ambiguity):
Suggested wording or design alternative:
What should remain true:
Type: broken promise / proposed promise change / presentation experiment
Evidence: sanitized capture, reproducible steps, or attributed qualitative feedback
Decision: pending / ratified / ratified with edits / declined / later
Decision-maker, date, and exact accepted wording or hash:
```

For example: “Use the same work facts; I thought Ready for you meant a finished deliverable, but it
opened an unread progress response. Try wording that says it is an unread
response. Keep the existing unread state and chat destination.” This proposes a
label experiment; it does not request an output classifier or new review workflow.

Use **pending** only as the absence of a decision. The four decisions in the
[review process](README.md#how-the-collaboration-works) settle a proposal.
Do not treat a saved draft, an applied wording revision, a mockup choice, or an
agent's summary as that decision.

## Evidence available and still needed

These are bounded observations against the accepted promises, not a declaration of
formal Converge conformance. The release qualification recorded 8 Home, 7 Focus,
and 4 sidebar browser checks on a synthetic installed build. They were run for
the release; this documentation review does not claim to rerun them.

| Observation | Reading | Basis and limit |
| --- | --- | --- |
| Independent sidebar and Home choices, reload, draft preservation, visible location, and narrow navigation | Kept for the recorded synthetic cases | [Sidebar browser checks](../../tests/web/home-sidebar-browser.mjs), source review and retained release receipt. No native-device or real-history usability claim. |
| Focus paging, cursor continuity, and no attention/runtime mutation while stepping | Kept for the recorded synthetic cases | [Focus browser checks](../../tests/web/focus-navigation-browser.mjs). Changing scope/search deliberately starts a new pass. |
| Bounded status pages and releasing hidden/closed sidebar demand | Kept for the recorded cases | Sidebar checks and [query ownership tests](../../tests/test_shell_query_owner.py). This is not a whole-host performance benchmark. |
| The workspace navigation agreement accommodates Status | Kept | The [accepted amendment](../workspaces/contracts/experience.v1.md) identifies the three sections as Browse and delegates the alternative's presentation. |
| A short, agreed UX contract connected to the shared-client vision | Kept | The [product-owner decision](decisions/20261020.md) accepts the direction and the [Home/sidebar promises](../shell/work-discovery.v1.md). This establishes agreement, not implementation quality. |
| Labels, hierarchy, and workspace terminology are clear to the designer using familiar work | Can't check | The planned human review has not occurred. Interaction tests cannot settle comprehension or taste. |
| Full assistive-technology usability and target-device comfort | Can't check | Keyboard, narrow-layout, and forced-color fixtures cover only part of this. Record actual device and assistive-technology review separately. |

The October 20 deployed-use feedback identified three **Broken** promises in the
reported build. The correction is **Kept for the synthetic cases below**; repeat
the designer's journeys on the adopted build before concluding that it works
well with familiar live work.

| Correction | Qualification and limit |
| --- | --- |
| Unread chats offer Mark read; read chats offer Mark unread in both sidebar views. Home also acknowledges manual unread marks. | [Read-state browser checks](../../tests/web/reviewed-error-browser.mjs) exercise both directions and preserve approvals, saved errors, messages, and runtime inactivity. [Receipt checks](../../tests/test_shell_modules.py) cover later Status pages, scope rejection, and a newer response surviving an older acknowledgement. |
| Background refresh keeps useful content silent and stable. | [Delayed-query browser checks](../../tests/web/scoped-navigation-browser.mjs) hold a real query and compare row geometry, then verify failure and Retry. [Presentation checks](../../components/web-client/tests/navigation-resource-status.test.mjs) retain initial loading and errors. Routine model-list and chat-reference refresh text is also removed; actions and failures keep their feedback. |
| Browse and Status use the same chat rows and display preferences. | [Home/sidebar browser checks](../../tests/web/home-sidebar-browser.mjs) compare row height, status, location text, and the folder-path preference, alongside narrow-screen navigation. Everything detail reveals context consistently; pinning retains its existing reorder controls. |

These checks use an isolated owned development instance, synthetic histories and
the built web client. They introduce no model calls or new session lifecycle.
They are not native-device, live-provider, assistive-technology, or human taste
acceptance. The earlier release observations remain evidence for their original
builds, rather than a declaration that the whole experience conforms.

A failed source check, unperformed journey, or subjective disagreement must keep
its own result. “Can't check” is not a pass. “Pinned open” is appropriate only
when a governing contract explicitly leaves that question open.

## Return to implementation

For each accepted change, return the exact candidate revision, the decision and
rationale, the affected promise, and one example that should work and one that
must fail. Name the smallest implementation scope and the changed journeys to
verify. Pure presentation refinements may cite an existing promise without
amending it; a change in meaning needs the wording decision first.

Measure the changed flows in the project's owned isolated environment. Reuse
existing acceptance where its inputs still match. Keep implementation evidence,
publication, and live adoption separate. No new release gate, background
processing, manager service, or runtime migration is introduced by this packet.
