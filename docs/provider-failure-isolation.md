# Provider failure isolation

Accepted scope, 2026-10-17: an explicitly selected healthy connection may run
when an unrelated configured connection cannot be prepared or mounted. This is
host policy on Core/Foundation's native runtime, not an account fallback policy.

The host retains every configured provider declaration, source, instance ID,
declared priority and model. Preparation and schema/configuration outcomes belong to the
exact declaration. A failed instance is represented in the runtime provider table
by a non-executable unavailable marker, with its original identity and priority.
The marker cannot list models, complete a request, stream, or supply credentials.
If the failed constructor's implicit priority is unknown, or an explicit priority
cannot be resolved, automatic selection refuses that uncertainty; a healthy
explicit account selection still works. The host does not guess a provider's
constructor default or use that guess to silently choose a different account.
Safe diagnostics identify the instance and preparation phase without raw errors
or secrets. Stored settings and native history are not rewritten to remove it.
Provider failures also retain a bounded diagnostic with the observed preparation,
schema, configuration or mount stage, an allowlisted cause category and exception
type, and an HTTP error status when the exception supplies one. Local missing
environment references and missing required credentials are distinct from provider
authentication rejection. Generic exception prose is not classified or retained;
unknown causes remain unknown. These facts survive worker reports and failure
persistence without publishing messages, endpoints, configuration or credentials.

Core owns provider mount-table rollback, including overwritten and removed slots,
before host recovery is called. This does not reverse arbitrary external effects
inside third-party mount code. Unavailable preflight instances never enter loader
or mount code. Unrelated non-provider module failures remain fatal. Unsupported Core
versions retain strict all-or-nothing preparation; they do not approximate isolation.

The optional decision-account adapter is a separate, narrow composition policy.
It retains saved provider accounts but mounts their package-owned decision hooks,
without adding a chat provider or weakening Core's provider validation. Exact
source/account credentials and child/resume binding remain authoritative. A hook
may report an account unavailable only after complete rollback and explicit host
acceptance; safe diagnostics explain the failure while configured ordinary UI-use
selection may continue. Unknown, cancelled or unsafe mount receipts abort
initialization. The host does not excuse arbitrary hook failures or use a backend
left behind by a rejected mount. See [the UI-use contract](../contracts/ui-use.v1.md).

Root explicit selection, default selection, named child accounts, inherited and
resumed selections are checked before executing work. A broken selected account
requires a deliberate selection/configuration change. Known declared priority and
tie order do not change when a provider fails. Routing checks availability before
using exact models or querying glob catalogs; only another explicitly declared
preference or matrix candidate may supply fallback. Exhaustion raises the typed
instance failure; automatic conversation fallback cannot conceal it. A differently
named or removed saved account is not inferred from module or ambient credentials.

Each child builds its own instance outcomes against its effective declaration;
parent failures cannot override an explicitly repaired child configuration, and
parent success cannot grant an unavailable child authority. Resume checks the
saved choice against the current authorized configuration without replaying input.
Public provider choices retain failed rows as unavailable; selecting or probing
one returns its safe typed failure, never a successful receipt.

Qualification must use actual Core initialization/rollback and Foundation routing,
including same-module accounts, preparation and partial-mount failures, preserved
priority, explicit/default/named/role/glob choices, child and resume. Synthetic
providers prove selection and no-call boundaries, not real account authentication.
The shipping dependency must contain Core #116/#117/#122; private source or wheel proof
does not imply an upstream wheel was published.
