# Temporary component delivery

`web-client/` is a verbatim snapshot of the separate client repository selected
by `web-client.source.json`. Maintain source, client tests and build tools in that
owning repository, then refresh the whole snapshot and record together. Do not
make independent fixes inside this copy. Its nested guides describe upstream
development; this wrapper governs maintenance of the delivery copy.

Read [README.md](README.md) and the small source record before opting into copied
contents. Unified assembles the selected client through
`scripts/web_client_build.py`; ignored `.build/web-client` is disposable prepared
source. Host/browser/producer checks and their own dependencies live in
`tests/web`. Neither delivery nor preparation creates another client owner.
