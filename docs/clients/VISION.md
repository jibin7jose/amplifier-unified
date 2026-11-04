# Amplifier shared clients — Vision (RATIFIED 2026-10-02)

This is a proposed destination for Unified and its connected clients.
It does not describe the current release or replace a client's own direction.
Observable promises live in the [contracts](direction.md); implementation
evidence and missing work live in the linked assessment and plan.

## What Amplifier shared clients are

Amplifier is a continuing conversation with work happening on an identified host.
A person can begin with a one-off request or an established project, then return
to work that needs attention or find something they already know. They reach
that work through a browser, terminal, desktop or mobile client. The interface
fits the device; the work, its meaning and its progress remain recognizable
when the person changes interfaces.

The target Unified host exposes AHP to clients and uses ACP for every agent,
including Amplifier. The web and TUI are independently developed experiences.
Other clients and agents join through those standards and optional negotiated
capabilities, without importing the host or embedding its execution runtime.

Several people’s windows and devices can observe authorized work concurrently.
Opening another view neither creates another execution nor takes another view
away from what its user is doing. Work continues when its viewers leave.

Foundation supplies reusable storage, configuration and ownership mechanisms.
Applications choose their roots, policies and presentation. Existing standalone
interfaces participate through shared storage and cooperative ownership; they
are distinct from clients attached to Unified's running session.

## Principles

### 1. Continuity is the product
A person returning to work can tell what needs their input, what has a response
to read, and what is running. They can find a familiar conversation without
reconstructing its organization. The conversation, workspace when present,
history, and next available action remain recognizable. Their current context
survives visits elsewhere in the same client. Moving between devices does not
become an import/export task; moving execution to another machine remains a
separate, explicit capability.

### 2. Common meaning, native interaction
People learn the concepts once. A terminal uses its keyboard and scrollback well;
a browser uses its panels and rich views well. A quiet view and a detailed view
remain ways into the same work, with the same essential actions. Neither density
nor a change of interface changes what a status means or grants more authority.
Clients express these promises in the form that fits their device.

### 3. Independent attention, shared work
A person's selection, unfinished input and reading position persist locally in
that client with no routine backend synchronization. Shared drafting or agent
inspection is explicit. Accepted work is shared; AHP owns its replication and
recovery while the host continues unattended work and preserves its results.

### 4. Feedback makes the next action clear
People can tell whether input is pending, work is running, a connection is lost,
or a decision is needed. Feedback lives where the person acted and uses motion,
state and emphasis economically. It never claims completion from mere acceptance.

### 5. Configuration has a place and a time
People understand which host, workspace or conversation a change affects and
when it becomes effective. Another device can observe that change without losing
its user's unfinished settings edits. Device capabilities remain device-specific.

### 6. Authority survives changes of interface
An interface presents the host's actual permissions, ownership and capabilities.
A new display does not grant new execution authority. Public operations give
agents the same meaningful actions and outcomes available through client controls.
An operator can choose Full access once for unattended work, including trusted
system requests and delegation. Clients must not add approval prompts to that
saved choice or require a human to remain connected. The host reports the
effective preference and supports revocation and Stop through the same controls.

### 7. Clients can advance independently
A shared behavioral change has a clear home and named consumers. Teams can improve
rendering, accessibility and platform interaction independently while testing the
same journeys in separate repos. Compatibility is checked against actual peers
and specific revisions. The [family vision](../architecture/VISION.md) owns decomposition.

### 8. Evidence stays honest
Source inspection, simulated transport, a real terminal and a released device
each prove different things. Direction describes the intended experience;
the assessment says how much of it has actually been demonstrated.

## What this deliberately resists

- A second execution runtime for every connected window.
- Loading historical catalogs, child histories or dormant agents just to browse.
- A browser-shaped specification that makes the terminal a lesser client.
- Implicit replication of credentials, files or execution between hosts.
- Different authoritative names or histories for the same stored session.
- An API success response presented as finished work or an undone tool effect.
- A second copy of shared rules in every repository.
- Requiring all client teams to approve an internal implementation improvement.
- Treating a draft contract, a passing fixture or a merged PR as a shipped result.

## How you can tell it is working

- A person uses **2 browser tabs and 2 TUIs** across **2 conversations** and keeps
  every view's selection and unfinished draft throughout concurrent updates.
- Two people observing one authorized conversation see **1 accepted input** for
  a deliberately retried command, and **0 repeated tool effects** on reconnect.
- A terminal user leaves **1 conversation** working, reconnects through the web,
  and finds its latest accepted state without resubmitting the request.
- A person changes **1 workspace default** and can identify its scope and effective
  state in both clients without affecting a second, overriding workspace.
- A reviewer traces **every shared behavior change** to its contract clause,
  affected consumers and evidence; **0 unmeasured claims** are reported as passes.
- A returning person identifies **1 required decision**, **1 unread response**,
  and **1 running conversation** without inspecting execution settings.
- A person begins **1 standalone request** with **0 mandatory project or folder
  choices**, then can find that conversation again.
- A person visits **2 conversations**, returns to their work overview, and
  reconstructs **0 previously chosen filters or reading positions** in that client.

## Current desktop consumers

[Amplifier for Mac and the planned Windows client](desktop-clients.md) apply this
vision through the shared desktop contract. The Mac companion preview is released;
the integrated workspace and Windows implementation remain future work. This
consumer record does not change the AHP/ACP destination or claim current parity.

The optional [device connection PoC contract](../../contracts/device-connections.v1.md)
applies these boundaries to enrollment and local capability providers in the
current host. It does not require the future AHP/ACP replacement, Tempo or
new CLI onboarding. The bundled web client supplies one setup experience.

## Changelog

| Date | Change | Basis |
| --- | --- | --- |
| 2026-10-20 | Identify current Mac and planned Windows consumers; link shared desktop journeys. | Product-owner request for coordinated platform documentation. |
| 2026-10-20 | Apply the accepted Home/sidebar UX amendment. | [Product-owner decision](../ux/decisions/20261020.md). |
| 2026-10-02 | Adopt protocol boundaries, client-local state and independent repos. | [Family architecture direction](../architecture/README.md); implementation pending. |
| 2026-09-20 | Initial draft. | Shared-client design discussion and the sources in the direction index. |
