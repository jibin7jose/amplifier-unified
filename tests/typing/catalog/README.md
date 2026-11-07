# Catalog read typing

From the repository root, with Pyright installed in your development environment:

```sh
python3 scripts/check_catalog_types.py
```

Qualified with Pyright 1.1.408 and Python language version 3.13. Use
`--checker /path/to/pyright` for a separate development environment. The command
checks the directory page producer, real imports and the existing browser consumer
without importing or starting the application. It also proves that returning only
rows to the page consumer, changing `total` to a string, misspelling a result key, or assuming every result has a page index
produces an error. Missing or failed checker execution is a failure, not a skip.

This is a focused developer check, not a repository-wide type or release gate.
Only the page envelope is typed here. Native metadata, session rows, scope
payloads, authorization, history ownership and runtime behavior keep their
existing owners and runtime tests. Pyright follows the other producers'
annotations; this fixture does not certify every implementation in an imported module. Editing a
producer still requires its focused parity and query-budget tests. The invalid
fixture is deliberately excluded from the default positive Pyright project.
