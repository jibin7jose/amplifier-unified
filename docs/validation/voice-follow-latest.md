# Following a voice conversation

The chat scroll controller follows new transcripts while this browser owns a
connected call and its conversation is selected. Scrolling, touching, navigating
by keyboard, or selecting a message pauses following. Switching conversations
preserves that choice; the other chat does not follow the call. Jump to latest
and submitting a message explicitly resume following. Ending the call restores
ordinary reading behavior. A call owned by another browser does not enable it.

The controller also owns message anchoring during content growth. Browser scroll
anchoring is disabled while the controller is mounted, avoiding an observed
150-pixel jump after mobile hangup. The existing visible message and offset are
retained when earlier rows change size. Unchanged delayed scroll events cannot
replace that saved anchor before a resize is handled.

The production browser fixture uses real transcript storage and state updates,
with synthetic signaling and microphone access. It checks incoming transcripts,
manual scrollback, another conversation, returning to a paused call conversation,
explicit latest, mobile width, reduced motion, hangup, and no runtime submission.
The geometry regression checks earlier content growth as well as reading position
across reply growth and chat switches. The ordinary chat and full-history
navigation browsers remain required regression checks.

This verifies the view, not physical audio: provider interruption timing, spoken
handoffs, device suspension, and microphone/headset behavior remain separate
acceptance work for issue #496. No paid model call is made by these fixtures.

Local qualification in the owned Linux DTU: production build, the voice browser
(including a second browser without call ownership), reading geometry regression,
ordinary chat browser, and full-history navigation browser passed. All 551
frontend unit tests passed before the final unchanged-offset scroll-event guard;
the browser checks above exercised that final guard. CI repeats the unit suite
on the submitted revision.

Failed investigative checks are retained in the workspace evidence directory:
the initial hangup shift, native anchoring experiments, a fixture context setup
error, and a navigation Chromium launch failure before using its supported DTU
launch flag. These are not counted as passing feature checks.

October 15 post-release follow-up found a distinct call-following defect: when a
completed Work notice disappears at the bottom, the browser clamps `scrollTop`
to the shorter content before the controller's next layout frame. That automatic
move was incorrectly treated as reading intent, silently pausing the call. The
controller now preserves following only for that bounded clamp; wheel, touch,
keyboard, scrollbar and browser focus movement retain their existing behavior.

The owned-DTU regression delivers the observed clamp-before-layout order, then
grows the transcript. It also checks actual scrollback, browser focus movement,
explicit resume and resize. The production voice browser waits for accepted
current Work before comparing raw scroll coordinates and additionally checks the
visible message identifier and offset. Full voice following, ordinary chat
scrolling and the controller regression passed on the built candidate. These
remain synthetic signaling checks, with the physical-audio limits above.

October 23 follow-up reproduced two additional ways to pause following without
scrolling back. A downward wheel at the bottom moved no content but paused the
call. With fractional display scaling, collapsing Work clamped `scrollTop` to
1299.0908 while integer height metrics reported a bottom of 1300; exact equality
mistook that browser adjustment for reading intent. The controller ignores wheel
gestures that cannot move vertically during call following, and
allows one CSS pixel of rounding only in its bottom-geometry checks. Deliberate
scrollback, browser focus movement and another conversation retain their behavior.

`components/web-client/tests/chat-scroll-call-boundaries-browser.mjs` exercises fractional
geometry, Work collapse/growth, resize, boundary wheel gestures and intentional
scrollback. `tests/web/voice-follow-latest-browser.mjs` also checks starting
a call from a saved reading position and new transcript delivery after a downward
wheel at the bottom. These reproduce mechanisms consistent with the reported
failure; the user's original browser input sequence was not recorded.
