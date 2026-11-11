# Provider request recording

Unified displays request details from a provider's recorded `llm:request`
event. It does not reconstruct a supposed wire request from chat messages.
Without the provider's raw logging option, the event normally contains only
model, counts, and other metadata, so the request cannot be displayed later.

## Enable recording

Open **Settings → Privacy & files → App diagnostic capture**, select
**Record provider requests and responses for troubleshooting**, and save. This option is off
by default. The agent-side equivalent is `diagnostics.configure` with
`config.providerRequests: true`, retaining the other desired configuration.

The setting is stored in this app instance's private `diagnostics/config.json`.
It supplies a default to supported provider instances in the root and all
nested agent declarations, and to separately prepared child overrides. Existing
provider-specific options, including explicit `false`, retain precedence.
Naming calls use the mounted providers and receive the same policy.

Changing the setting makes idle workers prepare again for their next turn; it
does not interrupt an active call. Old calls cannot gain data that was never
recorded. Provider declarations retain the provenance of host-injected defaults
outside the adapter's config, so disabling the setting also removes those
defaults from saved snapshots and resumed children. Explicit provider opt-ins
continue to apply.

Requests can include messages, instructions, tool arguments and attachments.
New diagnostic copies replace typed inline image transport with a compact
`__unified_image_transport__` fingerprint: media type when supplied, encoding,
character or byte count, and SHA-256 of the omitted transport (UTF-8 for strings).
This applies to event previews and larger downloads, before capture limits.
Image blocks, text, model options, tool definitions and usage/cache metadata stay
available within those limits. A fingerprint is diagnostic evidence, not a
resolvable attachment reference or a promise that an image was saved.

Image recognition follows provider message/content containers: OpenAI Responses
and Chat Completions image blocks, Anthropic base64 image sources (including
tool-result content), Gemini inline image parts, Ollama message images and OpenAI
image-generation results. Remote URLs, ordinary text containing data URLs or
base64, tool arguments and image-shaped schema examples are not reclassified as
image transport. Unknown adapter shapes retain the existing bounded capture
behavior. The actual provider request, attachments and canonical transcript are
unchanged. Historical recordings are not rewritten.

Unified scrubs recognizable credentials, including known credential environment
values, before the community logging hook observes the diagnostic copy. It
reads known environment credentials once per capture, rather than once per
field or key. The next capture reads current values again, including rotations;
no process-wide secret snapshot is retained. It limits each captured raw field
to approximately one million characters (plus
JSON formatting), bounds depth, and marks truncation. Redaction failure replaces
the raw copy with a capture-unavailable marker; it does not interrupt inference
or forward the unsanitized raw field. Neither redaction nor bounding modifies
the actual provider request. Arbitrary private text is still private text;
credential redaction is best effort, not a guarantee that a log is public-safe.

### Larger recordings and downloads

The one-million-character event preview keeps activity and conversation state
bounded. When that preview truncates an emitted request or response, the host
also saves a separate redacted JSON copy, with the same image fingerprints,
beside that session's event log, in
`provider-payloads/`. This copy permits up to 16 Mi characters (16,777,216),
24 nesting levels and 64 MiB of encoded JSON. These are capture limits, not a
promise that the provider exposes its complete wire payload.

Expand a model call to download the larger recorded request or response without
loading it into the transcript or JSON viewer. Each download identifies the
root session, actual calling session (including delegates), call, provider,
model, redaction and truncation. A capped recording is explicitly labelled
**partial** and has `-partial` in its filename. Image fingerprinting is deliberate
redaction, not size truncation; a **full** download still omits image transport. The smaller viewer and its copy
button still show the event preview. Historical truncated events cannot gain a
larger copy retroactively.

Downloads require normal host authentication and a matching canonical event
reference. The host verifies the file's SHA-256 and length, then streams it in
64 KiB chunks. Browser arguments cannot choose paths. Missing files produce an
unavailable error; they are not reconstructed from chat history. Diagnostic I/O
failure preserves the safe preview and does not stop inference.

Copies are private (files mode 600, directory mode 700), owned by native session
storage and kept until that storage is removed. Disabling capture does not
remove earlier copies. Existing bundle exporters and session-export formats
may contain only the event preview, not these separate files. Use the model
call's download for a portable copy, and treat it as private conversation data.

Some provider raw flags also enable response recording. The same redaction and
preview/download limits apply to those raw response fields. Existing session logging and
bundle exporters remain in control of storage and external delivery; this
setting does not enable app forwarding or create a new destination. Shared
event files keep their existing retention behavior and are not deleted when
capture is disabled. The app's metadata-index retention limits do not delete
native session logs.

## Adapter audit

Audited all eleven Microsoft provider entries in the current upstream
[`amplifier/docs/MODULES.md`](https://github.com/microsoft/amplifier/blob/main/docs/MODULES.md),
and refreshed the source evidence on October 1, 2026 after the provider parity
repairs merged. The app's root and child preparation paths were checked against
these adapter contracts.

The revisions below are the source evidence for this implementation, not pins
to apply to user installations.

| Module | Audited revision | Applied option | Actual emitted evidence |
| --- | --- | --- | --- |
| provider-anthropic | `be0eef2d2cdb2b1a0d4f960337a1d4374ddef7da` | `raw` | Redacted SDK parameters in `llm:request.data.raw`. |
| provider-openai | `1aed3279fd7287f896b5bab6c6571a3983d1e411` | `raw` | Redacted final request parameters in `data.raw`. |
| provider-openai-chatgpt | `37699a5ed7b53041d8cb757ca3e75383c2ef8556` | `raw` | Redacted built request payload in `data.raw`. |
| provider-azure-openai | `3a67ed10e70d185bd1061ec7b0d4aa9bd361b136` | `raw` | Inherits OpenAI request capture. |
| provider-chat-completions | `bd0de352072efca17ee332912d90a6b0633742bf` | `raw` | Redacted chat-completions parameters in `data.raw`. |
| provider-gemini | `ac522d4f36c5817acf3526b089b43728c005c525` | `raw` | Structured Gemini request parameters in `data.raw`; `debug` and `raw_debug` are not substitutes. |
| provider-github-copilot | `ed942e2c8a0c6d4ff6d878bc4ba480592f6c676a` | `raw` | Adapter summary: prompt/system lengths, tool schemas and settings. The SDK owns the final wire request. **Not full prompt/wire capture.** |
| provider-ollama | `f86ae6c7bdf55745294cc1719b5df5dda4f84c2b` | `raw` | Request parameters in `data.raw`, for streaming and nonstreaming. Unified adds redaction before recording. |
| provider-vllm | `bc8031d0431c6490764c8a2228b9078ef06c7352` | `raw` | Redacted request parameters in `data.raw`. |
| provider-litellm | `1908d0eb5f4560ca187c360c8618f75cbd55abb1` | `raw_debug` (compatible alias for `raw`) | Full redacted SDK parameters in **`data.raw`**, with the legacy `data.raw_request` field retained. Unified honors explicit settings in either spelling. The adapter no longer applies its old 16,384-character limit; the host capture limit still applies. LiteLLM input is not necessarily the downstream provider's final HTTP body. |
| provider-mock | `a90821f6b3767f8e52df5e754a96c20352684b43` | None | Debug emits a separate `llm:request:raw` count summary. No actual network request exists; Unified does not pretend that enabling debug creates one. |

The setting covers every audited Microsoft real-provider adapter. Unknown/custom adapters
keep their own declared policy until their contract is known; Unified does not
invent unsupported flags or reconstruct missing payloads. Provider-emitted
`raw` or `raw_request` fields are still available for these adapters when
recorded. An upstream Copilot SDK/adapter feature would be needed for true full
wire capture; this Unified change cannot recover it. No upstream adapter change
is required for the other audited capture paths.

The catalog also lists three community providers. Their current contracts were
checked on September 30, 2026; they are not silently given Microsoft adapter
flags or included in the capture claim above.

| Community module | Audited revision | Current boundary |
| --- | --- | --- |
| provider-bedrock | `61ef9ca84e9877c425258aaae7ded0c20ddf3281` | Requires both `debug` and `raw_debug`, and emits a separate `llm:request:raw` event with `params`. Supporting it requires an explicit event normalization and redaction path; the app does not enable this alternate logging path. |
| provider-perplexity | `768fa6c1ca0371af660e1dbb0c6806943c579d79` | Emits request counts, with no raw request capture flag or payload. Provider support is needed before the app can display an actual recorded request. |
| provider-openai-realtime | `6a27a86183d62d5122f09065cc1ff9144becdeab` | Uses `provider:request` and `provider:response` events and an older response contract. These are not `llm:request` capture and require separate compatibility work. |

These community paths remain unchanged. Their own configured logging behavior
is not covered by the app's `llm:request` and `llm:response` redaction hook.

## Verification and acceptance boundary

- Backend tests cover config validation/private storage, all adapter defaults,
  explicit opt-outs, nested declarations, actual root/child preparation,
  Foundation snapshot round trips, and configuration invalidation.
- A real Core hook registry proves the diagnostic copy is scrubbed before a
  priority-100 capture hook, that raw request objects remain unchanged, and that
  redaction failures do not leak the original payload.
- Event-log tests prove LiteLLM's existing recorded field survives lazy readback
  exactly, without reconstructed history, and retain redaction/truncation status.
- Frontend interaction tests cover default-off, explicit save, disabling again,
  and independence from app forwarding. A browser test verifies save/reload on
  desktop and mobile, with no horizontal overflow.
- No paid inference or live account calls were used. Adapter coverage here is
  source-contract coverage plus host tests, not a claim that every current
  account/endpoint was exercised. Release and installed-worker
  acceptance must be verified separately after integration.
