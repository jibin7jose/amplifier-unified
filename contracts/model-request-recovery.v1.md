# Recovery of an interrupted model request

The host's portable provider boundary may retry an unreturned model request in
its still-running execution. Browser reconnect restores views; it never sends a
new turn or triggers this policy. Other clients observe the same host-owned work.

- Retry only provider-declared retryable timeout/unavailable errors, at most two
  additional attempts with bounded backoff (2 and 5 seconds, respecting provider
  retry-after up to 30 seconds). Provider/SDK internal retry policies remain their
  own responsibility; these bounds count host attempts, not physical API calls.
- Retain the exact prepared request, selected connection/model, options, current
  generation and completed tool results. Never restart the orchestrator, repeat
  a tool or append the human message again. Changed requests or authority decline
  retry. User Stop cancels the current request or its backoff.
- Any yielded streaming event, exposed response/output hook, or tool invocation
  prevents generic retry. Stateful native transports retain their provider-owned
  reconciliation policy. They are never resubmitted through this wrapper.
- Authentication, invalid input, unknown errors, cancellation and exhausted retries
  keep the existing truthful failure/recovery path. Attempt diagnostics contain
  metadata only, never request or response bodies.

This is recovery of a surviving worker after transient transport loss (including
host suspension). It does not infer process death was sleep, automatically start
terminated workers, resume partial output, or replay requests from diagnostic
recordings. A terminated process or provider-owned native request needs a durable
checkpoint/receipt qualified by its execution owner before automatic continuation.
A provider may have performed and billed inference before a response was lost;
retry does not promise exactly-once remote inference or unchanged generated text.

Tests exercise retry exhaustion, Stop, changed authority, partial output,
provider opt-out, exact input retention, and root/child adapter installation with
synthetic providers. Physical sleep and paid inference require separate evidence.

## A changed fitted request before transport

At the prepared dispatch boundary, Streaming may request one new measurement
through the optional provider `refit_request(request, error=...)` capability.
SurfaceProvider returns a budget decision only for its exact retained local
content/binding refusal, before any provider transport began. The existing
explicit budget preparation stabilizes stale visual warnings before measurement.
The loop validates the normal hard-fit decision and sends that newly measured
request once, without repeating a tool, hook, input, turn or context compaction.

Selected-model wrappers preserve this optional capability through the execution
adapter. They retain the exact refused selected request and error for that adapter,
including the measured model, effort and output reservation; a copied request or
changed selection cannot stand in for it. Native regression coverage traverses
the selected-model wrapper as well as the surface adapter and real loop boundary.

The one refit retains its original request-scoped preparation observation instead
of fetching all sections again. It refreshes the required Work version through
the canonical currentness check after refit/measurement and before transport,
including when Full waived fitted-context assurance. Each new model request obtains its
own observation. Required currentness reads use the bridge's response deadline
and cancellation; optional context collection's shorter deadline must not cancel
an otherwise valid required check. Stop and input/generation identity are checked
again after the await. An unavailable observation is not evidence of changed
authority and must be reported separately.

The original native generation, complete inputs, Stop revision and required Work
version must remain valid. Changed or unavailable authority, unknown/sent calls,
ordinary provider errors, any stream event, an unavailable/oversized measurement
or a second refusal remain terminal. This is not the timeout retry policy above.
Local diagnostic reasons distinguish missing/changed binding, changed request
components, changed native scope and changed/unavailable authority; they contain
only fixed reason/component names, never prompt text, image data or request hashes.
The recovery event is metadata only and does not prove provider admission.
