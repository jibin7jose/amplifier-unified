# Device freshness: bounded follow-up

**Shared implementation in review, 2026-11-07.** This follows the accepted
[autonomous-progress direction](autonomous-progress-20261107.md#separate-device-freshness-follow-up).
It does not change Unified device authority or authorize publication or adoption.

The shared `amplifier-bundle-computer-use` owner at
`fc02bc969e675e7664485c28b324163297f1b80a` samples presence before input. Its
remote-Windows `operator_presence_override.expires_at` is an explicit finite
operator grant, with wall and monotonic deadlines. It is not a host freshness
lease. Expiry and revocation remain terminal; renewal must not be inferred from
Full access, a quiet device or unchanged pixels. The active Windows focus and
native acceptance work remains separately owned.

The bounded implementation concerns its optional local-X11 observed control.
At the investigated base it rejects selections older than 15 seconds before
performing the same-binding pixel readback already required after an approval
wait. Letting that existing readback validate an older selection removes a
timer-only re-observe step. Shared [PR #41](https://github.com/microsoft/amplifier-bundle-computer-use/pull/41)
at `113e65dcecc6ed254c332cf65192480c1ed2ff0c` updates its
[observed-control contract](https://github.com/microsoft/amplifier-bundle-computer-use/blob/113e65dcecc6ed254c332cf65192480c1ed2ff0c/docs/OBSERVED-CONTROL-SPIKE.md)
and checks the binding both before and after fresh capture. Exact canonical
approval, read-only policy, binding,
presence/halt, cancellation and uncertain-effect handling continue to apply.
This does not fix Windows grant expiry or establish cross-process device ownership.

The owned DTU qualification passed 125 focused observed-control and existing
finite-operator-policy regressions. A private installed wheel, with all 35 module
files matched to source, ran the maintained qualifier in a separate worker against
its own Xvfb. A genuinely 15.1-second-old selection delivered one approved native
click. Changed pixels, denied approval, Stop and replacement binding refused input;
a deliberately lost reply after input remained unknown with no replay. The
qualifier supplies a known candidate: it does not qualify OCR recognition,
Unified-host behavior, live human coexistence, physical devices or Windows. The
local evidence is `output/device-freshness-20261107/qualification.json` in the
workspace, with the native report and focused test receipts alongside it.

## Remaining Windows policy proposal

A future standing device policy must be explicit and separate from an existing
finite grant; upgrading must not reinterpret saved `expires_at` values. The
shared owner must first define and expose evidence for one current controller,
the same target, current host authority and presence. Its current per-tool
binding and per-process transport sharing do not prove exclusive ownership
across worker processes. Unknown presence or input outcome must remain a
specific blocker, and genuine user takeover must suppress later input.

Any host freshness renewal should then happen in that existing owner's normal
pre-input validation, under an explicitly accepted standing policy. It must not
extend a deliberately limited grant, clear a durable halt, reset an unknown
receipt or replay an input. Define the target-side in-flight limit and test Stop,
revocation, expiry, target/controller replacement and transport uncertainty
before enabling it. This proposal adds no broker, new authority store, blanket
timeout increase or new Arcade permission.

Windows and physical-device acceptance remain separately required for any future
standing-policy implementation; the local-X11 evidence does not satisfy them.
