# Ordinary browser coverage owners

The October 17 source audit found 80 standalone browser scripts outside the
requested 211-invocation / 203-distinct-script execution union (including the
historical 141-script ledger). That count is not 80 failed workflows
or 80 proven duplicates. The retained per-script crosswalk separates partial
overlap, specialized/Advanced checks and unqualified effects. A related passing
script gives credit only for its actual assertions, under
`tests/web/standard-ux-completion-policy.json`; it does not qualify all
assertions in another script.

Four existing scripts now have additive extended-runner owners. Each retains its
four built-in pointer/keyboard/compact contexts and original effect assertions.
The frozen 65-scenario baseline and its hash remain unchanged.

| Owner | Additional ordinary effect | Related existing proof and its limit |
| --- | --- | --- |
| `standard-ux-connection-controls` | Failed credential replacement preserves the previous key; explicit retry changes only the selected global account, with no inference | `standard-ux-connection-inspection` checks exact catalog owners; it does not replace credentials or prove save refusal |
| `standard-ux-preferences` | Held preference saves, explicit notification-permission denial/recovery, and saved preference independent of browser capability | `standard-ux-final-settings` saves phone configuration without sending; Settings route coverage does not prove browser-permission behavior |
| `standard-ux-privacy-effects` | Ordinary history import preserves tool rows without execution; explicit backup contains the expected database/transcript with private file/directory modes | `standard-ux-privacy-links` visits destinations read-only; `portability` covers Advanced task transfer, not this import or backup |
| `standard-ux-voice-settings` | Private-key refusal/retry, exact explicit preview identity, unsupported saved voice/model and fallback controls | `settings-completeness` saves model/fallback preferences; it does not prove private credential failure or selected preview effects |

Run these IDs with the canonical extended browser runner in an owned environment.
Each requires a private `AMPLIFIER_TEST_ARTIFACTS` directory; the registry supplies
one. The fixtures use synthetic accounts, local sample generation and blocked
external browser requests. They do not qualify real credentials, providers,
microphones, native permission prompts or live restart effects. Registration is
coverage ownership, not a passing result; execution receipts must retain the
tested source/build, all variants, assertions and strict process cleanup.

The audit also retained two already-executed supplemental owners,
`standard-ux-privacy-links` and `standard-ux-storage-controls`, with their separate
four-context receipts. `standard-ux-discovery` explicitly records partial route
discovery rather than complete workflow effects. `standard-ux-update-controls`
overlaps the existing `update-restart` confirmation/target-drift/no-replay proof;
its unrun input variants do not acquire passes through that overlap. Message
copy/reply/reaction/export checks similarly require an exact effect crosswalk,
not a blanket equivalence based on their names.

Current-batch evidence and the 80-script crosswalk remain under
`output/afk-approved-20261017/ordinary-coverage832`. The original stopped browser
census remains unchanged; supplemental checks are additive observations.
