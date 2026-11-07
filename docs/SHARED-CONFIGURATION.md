# Shared Amplifier configuration

Unified uses the same settings files as amplifier-app-cli. A future TUI can
consume this contract without importing either application. `AMPLIFIER_HOME`
defaults to `~/.amplifier`; `--data-dir` / `AMPLIFIER_WEB_HOME` only relocate
Unified's own state.

| Priority (last wins) | File |
| --- | --- |
| User defaults | `$AMPLIFIER_HOME/settings.yaml` |
| Workspace / team | `<workspace>/.amplifier/settings.yaml` |
| Workspace / machine | `<workspace>/.amplifier/settings.local.yaml` |
| Conversation | `$AMPLIFIER_HOME/projects/<slug>/sessions/<id>/settings.yaml` |

Workspaces are real directories. The slug and native session ID are the same
ones used for shared transcripts. Dictionaries overlay recursively; ordinary
lists replace. `config.providers` is the CLI exception: merge entries by `id`,
falling back to `module`, retaining other provider instances and merging each
instance's configuration. Lower numeric `config.priority` values come first.
Unified reports malformed YAML instead of silently ignoring it.

Shared fields include bundle registrations/defaults/behaviors (`bundle`),
provider instances (`config.providers`), module configuration and source
overrides (`modules`, `config`, `overrides`, `sources`), routing selection and
role overrides (`routing`), and supported saved configurator settings
(`configurator`). The existing module/bundle runtime still determines which
configuration fields it supports. A shared file does not make all host features
identical. In particular, older CLI versions do not consume Unified's hook
enablement extension; `web_bundles` contains optional editor metadata, never
authority over the standard `bundle.app` / `bundle.added` fields.

Credentials use `$AMPLIFIER_HOME/keys.env`. Explicit launch-environment values
win; values loaded from the file refresh on subsequent mounts. Provider editing
stores credential references in YAML and secrets in this private file. Existing
explicit OAuth token paths are retained. Newly created ChatGPT token files live
under the shared root. Tokens are not copied to a second refresh owner.

In Settings → AI connections, open a connection to see its **Service API key**
preview and source (service environment, private `keys.env`, or provider
configuration). Advanced provider settings show the same preview. **Refresh
credentials** reads the running service's environment and reloads file-managed
keys; it cannot import a changed environment from your terminal. Restart the
service with its updated environment if that source still shows an old key.
Existing chats can retain credentials from when their provider was mounted.

Previews expose only the first six and last four characters of keys at least
16 characters long; shorter or unusual values remain fully masked. Full keys
and OAuth tokens stay private. The same masked metadata is available through
`providers.list` and `providers.credentials`. Known provider fallback order is
respected, including the compatible API provider's environment-first behavior.
Provider-managed authentication without an inspectable API key has no preview;
an available key does not establish that the provider will accept it.

User-authored routing matrices live in `$AMPLIFIER_HOME/routing/`, as in the CLI.
Unified additionally supports `.amplifier/routing/` and
`.amplifier/routing.local/` in a workspace, ahead of global custom matrices and
the routing bundle's shipped matrices. These workspace directories are a
Unified extension until other hosts adopt them. The same lookup order is used
by the editor and the mounted routing hook.

Voice preferences are common settings, with no app namespace:

```yaml
voice:
  preferred_model: gpt-live-1
  fallback_model: gpt-realtime-2.1
```

These fields select models supported by the consuming app's voice transport.
Unified currently supports its existing Live and Realtime transports; this
change does not add another voice provider. A workspace or conversation can
override the global voice preferences. Other hosts can adopt these fields when
they add voice.

Settings writes lock `<file>.lock` across read/modify/atomic replace, matching
the CLI's locking convention. Unknown fields are preserved. This coordinates
with CLI writers that use the lock; an older or external editor that ignores
the lock can still race. Settings reads never import a cache or rewrite YAML.
File changes invalidate parked session mounts; active work is not interrupted.
New sessions resolve the default bundle from their own workspace. Deliberate
existing Unified conversation overrides (`configuration.json` and
`control-state.json`) remain in force; they are not flattened into user defaults.

## Configured connection and model discovery

The bundled Model for this chat panel, new-chat setup and Settings use the
configured connection roster for their effective global/workspace scope.
Mounted reports and saved/default selections are separate facts, not extra
connection or model options. Managed chats use global settings only.
Connection labels and order come from this roster; accounts/endpoints remain
separate even when they use the same provider implementation.
Supported module/instance overrides are resolved before discovery, including
disabled connections and endpoint/configuration overrides. They do not introduce
additional bundle-only connections into the shared roster.

Unified consumes the existing async `list_models() -> list[ModelInfo]` API.
Optional `get_model_list_info()` metadata with `version: 1` and
`source: "provider-api"` declares strict fresh API listing, not account access or
model entitlement. Missing/unknown metadata or `source: "unsupported"` remains
unverified and exposes explicit model-ID entry, never a static fallback presented
as fresh inventory. The superseded `discover_models()` method is not consumed.
Failed listing raises an error; a genuine empty result is successful and distinct
from failure or unsupported listing. Returned IDs are preserved exactly.

Providers own recommendations. Unified calls `list_models()` without keywords,
preserving that provider's effective configured `model_filters` or defaults.
For adapters declaring `unfiltered: {}` and `override: "replace"`, explicit saved
`model_filters: {}` requests the full inventory; a custom mapping replaces
defaults, not merges with them. OpenAI's declared default is
`include: ["gpt-6*"]`, `exclude: []`, `hide_dated: true`,
`hide_shutdown: true`; explicit `{}` disables both optional exclusions.
Other providers' policies remain their owners' decisions. Optional v1 metadata
advertises `filters_keyword`, `filters_schema`, `default_filters`, `unfiltered`
and `override`; selectors are passed only when advertised. Unified does not copy
the recommendation algorithm, mutate private provider fields or maintain a
second unfiltered catalog.
Provider-specific fields remain opaque to Unified: it neither restricts a
selector to OpenAI's field names nor fills omitted fields itself. The entire
effective `model_filters` mapping participates in catalog identity. Version 1
metadata can explicitly declare `configured`, `static` or `simulated` origin.
Those rows remain useful choices labeled Configured models, Static recommendations
or Simulated models; none claims fresh API inventory or a reachable account.
The same origin remains visible after empty success or a stale/error refresh.
Unknown origins, absent metadata and `unsupported` remain manual-only. A failed
API listing never falls back to another origin. Connection testing requires
`provider-api`; browsing configured/static/simulated choices does not test or
log in to the service. Their exact IDs remain valid selection intent.

The opt-in candidate graph in `tests/fixtures/canonical_model_catalog_candidate.json`
binds reviewed provider revisions and module hashes, including required SDK versions.
Its offline checks exercise real installed providers through Unified's probe/cache
with synthetic HTTP or SDK responses. This is candidate integration evidence, not
an upstream merge, runtime-lock adoption, live entitlement or inference qualification.
The separate `tests/fixtures/model_catalog_accepted.json` binds merged primary
provider, Foundation and routing revisions. Set `UNIFIED_CATALOG_ACCEPTED=1`
alongside the existing opt-in to use it. Unmerged community repair candidates are
excluded from that graph; their older evidence remains separate. The host lock
records accepted revisions while runtime source declarations retain their existing
main-following policy. Neither manifest is evidence of live account access.

The host's existing private `ProviderCatalog` owns single-flight discovery and
restart reuse. Explicit Refresh reloads the selected connection once. Reopen,
chat navigation and elapsed time do not refresh it. An unsuccessful refresh
retains the same identity's last successful models and timestamp with a visible
error; it does not retry automatically. Successful empty refresh removes previous
options. Credential, endpoint, discovery configuration, source or qualified
runtime changes create a distinct identity. Catalog identity conservatively
includes configured `model` and `default_model` because a provider can derive
its listing defaults from those constructor inputs (including explicit legacy
filtering). This can refresh recommendations even when that provider ignores
these fields. `model_filters`, `model` and `default_model` never change the
account binding of a saved exact model selection. `reasoning_effort`, priority
and connection labels do not affect catalog identity. Other provider
configuration remains identity-bearing.
Legacy unproven cache entries are not adopted as authentic snapshots.
Ordinary model display/refresh does not prepare a whole bundle to resolve an
unknown default label. Saved intent or a current native report supplies that
label; otherwise the panel states that Send will resolve the bundle default.

Conversation-specific account/endpoint overrides remain intact. The panel marks
known mismatches separately instead of treating an equal instance name as equal
credentials. A newly saved configured selection is bound to that connection's
identity; input admission checks the actual mounted identity and refuses a
mismatch without sending or substituting another account.
This connection binding is stable across host and worker processes and distinct
from the runtime-qualified discovery cache identity. Unqualified processes may
agree on a connection without sharing discovery across restarts. Effective
Copilot credential promotion and implicit ChatGPT OAuth/Plan token files are
included; token-file replacement or removal invalidates discovery reuse.

For supported ChatGPT Codex and Plan credential records, account binding uses
the configured path/auth mode and the provider-owned stable account fields
(`account_id`, or Plan `client_id` and `subject`), not rotating token bytes.
This is a local identity comparison, not authentication. The provider still
validates current credentials. Malformed/unknown records retain byte identity;
removal, account changes, API-key changes and endpoint changes cannot reuse a
saved binding. Catalog and health generations still observe token rotation.
A refresh that rotates the same account re-queries once under the new generation;
it never relabels an older response or retries a different account.

Send preparation loads only the selected connection's catalog, without using
membership as an inference gate. Unrelated connections do not add listing work.
Provider selection, bundle changes and runtime inventory reads do not warm a
second mounted catalog. The explicit raw `configuration.providerModels` control
remains available; its result is not an automatic UI discovery subscription.
Settings disables API connection testing for declared local origins and explains
why; explicit tests return a bounded not-verifiable remedy, never fake success.

The cache is bounded by entry count and encoded bytes. Eviction is not a history
policy. Relative credential/configuration paths retain their workspace scope.
Late results cannot be published under a changed connection identity.

An explicit manual ID is saved as intent, not as a discovered option. Unknown
capabilities do not clear saved reasoning intent. Advertised finite reasoning
choices still validate it. A model hidden by recommendations, absent from a
successful list, or retained while listing fails remains usable as an exact ID;
listing is not an unconditional success gate for inference. Sending preserves current authority, Stop, revision,
connection availability and provider invocation checks; a failed provider
request is never retried under a different connection or model.

## Upgrade from private settings

Startup performs an additive migration once per shared root and used workspace:

- If a shared settings file is absent, seed it from its corresponding old
  Unified file, translating provider ordering to numeric priorities.
- If it already exists, keep its values. Add only missing named bundle
  registrations; do not import old model choices, behavior lists or routing
  selections over an existing shared configuration.
- Copy missing credential names and custom routing files. Existing shared names
  and files win, including intentionally empty settings files.
- Seed missing global voice preferences from the saved app preferences once.
- Retain old files and private pre-migration backups under the app's
  `backups/shared-settings-<id>/`. Imported workspace snapshots are not replayed.

After migration, changes to `~/.amplifier-unified/config/settings.yaml`, imported
workspace snapshots and `.amplifier-unified/settings*.yaml` have no effect.
Inspect the retained files for intentionally conflicting choices you want to
adopt, and put those choices in the appropriate shared scope.

The app keeps deployment/TLS/authentication, update policy, browser layout,
Smart Tool server registrations, notification transport configuration,
diagnostic destinations, runtime caches, and conversation UI/operation state.
Resetting app settings does not reset the shared settings. Private backups now
include shared configuration and relevant workspace settings.

## Smart Tools and optional native runtimes

This cutover is a host configuration change. It does not inject Amplifier
settings discovery into portable Smart Tools. The opt-in
`amplifier_web.host.shared_runtime_config` adapter supplies shared provider
instances and the ordinary routing hook to a compatible native runtime, while
retaining its domain bundle and agent declarations. It has no required provider
or model. A runtime owner can explicitly adopt this optional interface;
adoption remains separate from this migration and must not restart an active
manager or bypass its saved-session configuration guard.

The adapter is an Amplifier host extension, not a universal Smart Tools
invocation contract. Deterministic domain operations remain independent of
provider credentials. See [the TUI handoff](TUI-SHARED-CONFIGURATION.md) for the
shared file contract, optional adapter interface and cross-host adoption checks.

## Root bundle defaults in Unified

The conversation bundle picker uses built-in conversation profiles and explicit
standalone registrations (`bundle.added`), independently of provider/model selection.
Cached namespace roots and source overrides alone do not make a bundle selectable:
add-ons remain in capability management. The shared `bundles.list` catalog applies
this policy to both UI and agent clients, without fetching or mounting bundles.
New conversations use an explicit bundle choice first,
then the workspace's shared local/project default, then Unified's optional app
preference, then the shared user default (falling back to `work`, shown as **Work**). Existing
conversations retain their saved root.

The default picker exposes three scopes. **This Unified app** stores `appBundle`
in app state and leaves shared settings untouched. **This workspace on this
computer** edits `bundle.active` in `.amplifier/settings.local.yaml`; clearing it
reveals a project/team default if present. **Shared Amplifier settings** edits
`~/.amplifier/settings.yaml` and can affect the CLI and other consuming hosts.
`bundle.default` provides the same scoped action to agents; `bundle:null` clears
only the chosen override. The older `settings.update {patch:{bundle:...}}` action
retains its shared-global meaning for compatibility.

For an existing conversation, choose a new root from the bundle control beside
the model, then **Switch bundle** directly or **Preview changes** first. Forking
also supports either route. Switching requires idle work. When a preview token is
supplied, the current configuration must still match that preview. Without a
token, the target is validated during Apply. The validated composition is reused
within that switch instead of resolving it again during mounting; it is not
cached between requests. First-time module installation still takes time. The
root is resolved with current host composition; previous conversation mount-plan edits,
module toggles, mode, and budget overrides are discarded. A compatible model pin
and its reasoning effort survive. An unavailable provider pin requires an
explicit reset choice. Original user/tool history is retained; old authoritative
system/developer rows are replaced by the new root's instructions. The worker
journals the affected session files, mounts the replacement under the same
Foundation execution ownership, and restores the original files/configuration if
mounting fails. Interrupted transactions are recovered on the next mount without
replaying work. Per-conversation settings from shared files still apply.

**Fork with this bundle** leaves the source unchanged and validates a new root
before publishing its independent history. It retains visible voice references,
tool evidence, and artifact snapshots, without copying job ownership, approvals,
or active goals. It does not clone underlying external MCP tool work. Both paths
use `bundle.preview`, `bundle.switch`, and `bundle.fork` through the shared UI/agent
action registry. A receipt means accepted; final `bundleChange` state reports the
outcome. Previews are intentionally invalid after host restart.
