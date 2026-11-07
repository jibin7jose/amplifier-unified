# Vision and contracts that help delivery

Use these documents to make product intent and ownership clear, not to create
another approval workflow. Start with the relevant owners in the
[source map](source-context.md); a focused change does not require reading every
vision and contract in the repository.

## Give each document one job

| Document | Its job | What it is not |
| --- | --- | --- |
| Vision | Explain the destination, user benefit and important exclusions. | A claim that the destination has shipped. |
| Contract | State observable promises at a boundary, their audience and failure behavior. | A source-file layout, task ledger or permission grant. |
| Design note | Explain diagnosis, alternatives, implementation choices and rollout. | Additional binding promises hidden outside the contract. |
| Implementation and evidence record | Identify actual source, checks, results and limitations. | A new product promise or a substitute for acceptance. |
| Contributor or agent guidance | Help developers find owners and perform the relevant work. | A prose reimplementation of runtime controls. |

Keep one canonical home for each promise. Link to its owner rather than copying
its rules into every contract, skill, starter or scratch file. A related repo or
newer document does not automatically govern Unified: establish the actual
dependency, boundary and accepted revision first.

Label **draft**, **accepted direction**, **implemented**, **qualified** and
**published** distinctly. An accepted vision can describe intended behavior that
is not implemented yet. A passing test establishes only what it exercised on its
recorded inputs. Publication does not establish live adoption.

## Write contracts around observable behavior

Name who relies on the boundary and who implements it. Give a concrete example,
then a small related set of promises that can be disproved by a check or an
observed failure. Describe what failure looks like and what the caller can do
next. Put internal source maps, options and rollout details in a linked note.

Use descriptive promise names and retain established identifiers and anchors.
When moving or retiring a promise, leave an explicit mapping; do not silently
renumber obligations or break their evidence links. Do not rewrite an accepted
contract merely to meet a line count or a preferred number of clauses. Split it
when distinct audiences or owners benefit, not to satisfy a formatting quota.

State scope and exclusions. Say what evidence or decision would justify extending
the boundary later. Bundled clients and server can evolve together; independently
released clients and public APIs retain their own compatibility owners and policy.
Do not add version negotiation where the existing coordinated release already
owns compatibility.

## Connect changes to evidence without adding ceremony

For a changed promise, explain the reason and identify the focused check that
could show it is false. Exercise the real consumer where the boundary requires
it. A source assertion, mock or document-format check is not runtime acceptance.
Prefer maintained checks; label missing or planned evidence honestly.

Use existing authorization, review and
[release policy](validation/release-qualification.md). This guidance adds no
ratification service or extra approval round for an already-authorized defect
fix. A substantive change in direction must be explicit and reviewed with the
affected owners; reorganizing documentation must preserve its meaning and status.
Keep evidence bound to the source and inputs actually checked.

## Keep guidance small and current

Ship retirement of manual workarounds with the engineered mechanism that replaces
them. Keep historical diagnosis and incident receipts available on demand, not in
always-loaded instructions. Following the
[context contract](workspaces/contracts/context.v1.md), general orientation should
point to specialist procedures; load those procedures when the task needs them.
Do not turn contracts into a checklist that a manager repeats in every turn.

Run affected checks when their inputs change and reuse applicable evidence.
Broader coverage has its nightly or explicit investigation owner. Historical
acceptance notes do not silently expand today's release gates. Document formatting
can help discovery, but satisfying it is not proof of sound product design.
