# Temporary component delivery

`web-client/` is a verbatim snapshot of the separate client repository selected
by `web-client.source.json`. Maintain the client in its owning repository, then
refresh the whole snapshot and its record together. Do not make independent fixes
inside this copy. Its nested guides, including `AGENTS.md`, describe development
in the upstream repository; this wrapper governs maintenance of the copy.

Read [README.md](README.md) and the small source record before opting into copied
contents. Unified builds this selected client through `scripts/web_client_build.py`.
`frontend/` owns integration tests and commands; its ignored source view is generated
for those tests and local experiments. It is not another authored client.
