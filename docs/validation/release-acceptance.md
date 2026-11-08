# Platform and first-use acceptance

Status: checklist prepared; platform and human results are still pending.
Run against the exact candidate artifact supplied by the release owner. Record
its version, commit, artifact checksum and source link before starting. Follow
the [setup instructions](../../README.md#set-up-unified-on-your-computer) for
that candidate, rather than installing a moving main branch. Use a fresh test
profile/account and synthetic work; keep any working installation separate.

## Platform checks

Run once on **Windows with WSL and a Windows browser**, and once on **macOS with
the backend running locally**. A Mac browser connected to Linux does not qualify
the macOS backend.

1. Install and start from the published instructions. Open the app, sign in,
   connect an AI account and send a simple request. Record any extra steps or
   help needed, plus the time until the first visible response.
2. Attach a short sample document. Ask for a useful summary or action list,
   save the output, then reopen both the conversation and the saved output.
3. Leave a distinctive unsent draft and reload immediately after typing, before
   waiting for autosave. Confirm it survives without sending. Clear it and reload
   again; the cleared text must not return. Switch chats and return, then
   sleep/wake the computer. Confirm the draft survives. Temporarily disconnect
   the network and reconnect; confirm the app recovers without duplicate sends.
4. In a long synthetic chat, read an older message, switch away and return,
   then reload. Check that the reading position and navigation remain useful.
   Try keyboard navigation and reduced motion.
5. With work idle, restart only the test backend. Reopen the app and check the
   saved conversation, output and draft. If supplied an upgrade pair, update
   through Settings and repeat. Record restart and upgrade results separately.

Record **pass**, **fail**, or **not tried** for each step. Stop a failing scenario
long enough to capture its visible message and approximate timing; do not hide a
failure behind repeated retries. A successful retry is additional evidence.

## Image and everyday controls

For releases containing the image and sidebar updates, also check:

1. With an image backend configured, request one simple image. Confirm a visible
   placeholder appears during generation and the finished image follows its
   work in the conversation. Reload and download it. If no backend is configured,
   record this as **not tried**, including whether the setup guidance is clear.
2. Copy a code block, quote and table. On a desktop, the compact control should
   appear inside the content on hover or keyboard focus; on touch it should be
   available without hover. Confirm the copied content and stable layout.
3. Narrow the window. Confirm navigation can collapse, chat content fits, and
   model or bundle menus appear above the Latest messages control.
4. Hover a clipped chat name and open its details panel. Check that there is no
   duplicate name tooltip, and that reduced motion remains comfortable.

## First-use observation

Give someone unfamiliar with the app only this task and the normal setup guide:

> Set up Amplifier, connect an AI account, and use a short sample document to
> make something useful for your work. Save the result and find it again.

Do not point out controls or coach the route. Record where they hesitate, what
they expect, any help requested, and whether they can find their result again.
Self-testing by someone familiar with the app is valuable platform evidence;
label it separately from an uncoached first-use observation.

## Native Windows candidate checks

Status: **pending implementation and native execution**. This section is the
qualification rubric for the [native setup design](../design/native-windows-installation.md),
not instructions to install a currently supported Windows backend.
Keep native Windows results separate from WSL and from a Windows browser
connected to a Linux backend.

1. On a fresh, non-administrator Windows 11 x64 account without WSL, Python,
   uv, Git or compilers, run the candidate's PowerShell setup. Record every extra
   action and time to authenticated entry and first response. Repeat with Python
   3.15 already installed; verify the candidate selects its qualified Python
   without changing the system installation. Check both PowerShell 5.1 and 7.
2. Test owner sign-in for local, Microsoft/Hello and Entra account classes.
   Reject anonymous, other-user, expired, replayed and cross-origin grants.
   A second ordinary account must not read credentials or private temporary
   files. Record untested account classes rather than inferring coverage.
3. Create a workspace under spaces/Unicode, attach a synthetic document, run a
   real default file/tool task, save and reopen an output. Check Unicode worker
   messages, large bounded messages, EOF and Stop. Race independent writers;
   exactly one may own the conversation. Test junction/reparse replacement.
4. Close the browser and terminal; verify work continues while signed in.
   Check logon startup, explicit stop/restart, host crash and owned grandchildren.
   Passive status/retention inspection must leave a sentinel process alive.
   After cleanup is confirmed, no owned descendants remain; unrelated processes
   survive. Sign-out behavior must match the accepted deployment lifetime.
5. Re-run setup, upgrade and force a candidate failure/rollback. Preserve exact
   data identity, chats, credentials, drafts and unknown-effect receipts without
   replay. Verify reset and uninstall affect only owned program/startup entries.
   Run affected Linux, WSL and macOS startup/health/runtime regression checks.

Record pass/fail/not tried against exact artifacts and resolved dependency graphs.
No compiler invocation, disabled ownership lock, broad ACL, unauthenticated
loopback shortcut, or missing optional controller may be hidden behind a successful
package install. Mocked tests and an existing developer Windows account do not
establish the clean-machine result.

## Result record

Use GitHub aliases or anonymous tester labels. Include platform/OS version,
processor architecture, browser/version, local versus remote backend, candidate
identity, results and rough timings. Exclude people's real names, machine names,
private addresses, personal file paths, account details and private document
content. Review diagnostics and screenshots before attaching them.

This checklist does not qualify physical voice interruption, Bluetooth/mobile
handoffs, original-history recovery or every provider. Record those separately;
do not infer them from successful chat or installation checks.
