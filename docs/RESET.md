# Repair a broken installation

Use `amplifier-unified reset` when an update was interrupted, installed packages
were edited, or damaged runtime caches prevent Unified from starting. It repairs
the supported **uv tool installation** on Linux, WSL and macOS.

```sh
amplifier-unified reset --dry-run
amplifier-unified reset
```

Reset shows its plan and asks for confirmation. Use `--yes` for unattended repair.
Run it as the account that installed Unified, with the same `UV_TOOL_DIR` and
`UV_TOOL_BIN_DIR` if those were customized. For a custom data directory:

```sh
amplifier-unified reset --data-dir /path/to/unified-data
```

## What it does

1. Resolves the latest published stable Unified release to an exact Git commit.
   Public release metadata is read without authentication first; GitHub CLI is
   optional. No model/provider requests are made.
2. Stops the generated systemd/launchd service for this data directory. Stop
   manually launched hosts and workers first; reset refuses detected running
   processes. Do not run other installers or start hosts during repair.
3. Moves the old uv tool environment aside and installs a completely fresh app
   and dependencies, bypassing uv caches and project configuration. This removes
   local changes inside the managed installation. Development checkouts are left
   alone; reset installs the normal uv tool rather than repairing a checkout.
4. Checks the new app's imports, bundled web assets, authentication library and
   worker bootstrap. If installation or validation fails, restores the old
   environment and launcher.
5. Retires Unified's `updates`, `runtime`, `foundation`, and `source-store`
   directories to a recovery backup. This clears active version pointers,
   downloaded source edits, worker environments and failed update candidates.
   It clears only the saved update status in the application database, including
   interrupted-update fences; a database backup is taken first.
6. Starts the generated service again, then runs `doctor` and `service status`.
   It waits up to one minute for the configured app to respond and only says
   “Unified is ready” after both checks pass. Use `--no-start` to leave it stopped;
   both checks still run, but a stopped service is expected in that mode.
   Installation verification, saved update-state changes and a passing `doctor`
   qualify an intentional `--no-start` reset as successful without a listener.
   The next message can take a few minutes while worker caches rebuild.

**Chats, credentials, settings, TLS certificates, attachments, bookmarks, workspace
files and unrecognized data are preserved.** The separate `~/.amplifier` data and
shared session store are untouched. Optional desktop/TUI features can be enabled
again in Settings after this base installation repair.

This is installation recovery, not a factory wipe or a database salvage tool.
Invalid user configuration or a corrupt chat database may still need separate
repair. Custom/unmanaged service definitions require separate attention. External
shared source stores are not cleared: unset `AMPLIFIER_SOURCE_STORE` before
reset, and repair an externally configured store separately if necessary.

## If the launcher itself is broken

Reset normally works even when application dependencies or an active update
receipt are broken. If the Python environment or reset module itself is missing,
run a fresh copy of the same dependency-free recovery script with system Python:

```sh
repair_dir=$(mktemp -d)
curl --proto '=https' --tlsv1.2 -fsSL \
  https://raw.githubusercontent.com/microsoft/amplifier-unified/main/amplifier_web/reset.py \
   -o "$repair_dir/reset.py" &&
curl --proto '=https' --tlsv1.2 -fsSL \
  https://raw.githubusercontent.com/microsoft/amplifier-unified/main/amplifier_web/reset_retention.py \
  -o "$repair_dir/reset_retention.py" && python3 "$repair_dir/reset.py"
```

Use Python 3.10+ for this recovery script. uv obtains Python 3.13 for the new app.
The script and its sibling retention policy use only the standard library, with
the same options and safeguards as the installed command. Keep both files from
the same reviewed revision together; no ordinary app dependencies are imported.

### Public downloads on a device with restricted GitHub sign-in

An account's organization policy can deny authenticated requests even when the
repository is publicly readable. Release checks try public access first. To also
bypass personal Git credential helpers, injected headers, and HTTPS-to-SSH URL
rewrites during repair, use:

```sh
amplifier-unified reset --anonymous
# Or append --anonymous to the standalone recovery script above.
```

This applies to the repair's downloads, including its Git dependencies. It does
not sign you out or change your Git configuration. For future background updates
and bundle preparation, install the service with the same download policy:

```sh
AMPLIFIER_GITHUB_ACCESS=anonymous amplifier-unified service install --replace
```

This command backs up the generated service definition and restarts Unified;
wait for current work to finish first. Use the same `--data-dir` and `--workspace`
as your existing installation if customized. Both systemd and launchd retain the
choice across service replacement. Set `AMPLIFIER_GITHUB_ACCESS=auto` with the
same command to restore normal Git configuration and authenticated fallback.
For a foreground host, set the variable on `amplifier-unified serve` instead.

Anonymous mode is for public sources. Private sources still require authorized
access and will fail in this mode. Personal Git proxy/CA configuration is also
bypassed; configure necessary transport settings through the process environment.
This is a download compatibility option, not a sandbox for arbitrary Git sources.
For a fresh installation, `scripts/install.sh --anonymous` applies this policy
to dependency downloads and the new background service as well.

To reinstall a specific trusted release wheel or Git revision instead of looking
up the latest release:

```sh
amplifier-unified reset --source /path/to/amplifier_unified-VERSION-py3-none-any.whl
```

Only use sources you trust: installing a package can execute its build code.
The source must provide the `amplifier-unified` distribution.

## Recovery backups

Reset prints the path to a private repair report containing the exact backup locations.
Package lists and service-manager details go into this report by default. Use
`reset --verbose` to also display technical details. During longer steps, reset
prints elapsed time so you can see it is still working.

`doctor` and `service status` provide short summaries by default; use `--verbose`
for technical details. Both return a nonzero exit code when a check fails. Service
status checks the running app’s version and data directory, not just whether a
background process exists. A failed startup check leaves the fresh installation
in place and reports that it is not ready; it does not claim a successful repair.

Retired runtime directories and a copy
of the database are under `<data-dir>/reset-backups/<id>/`. The old app environment
is retained outside uv's tool directory, under the sibling
`<tool-directory-name>-amplifier-unified-recovery/<id>/previous`. Failed
replacement environments are retained in that same recovery folder as `failed`.
The backup manifest records the exact path. Keeping recovery copies and the
installation repair lock outside uv's tool directory prevents them from being
mistaken for installed tools by `uv tool list` and `uv tool upgrade`.
Older versions used hidden `.amplifier-unified-reset-<id>` and
`.amplifier-unified-failed-<id>` tool-directory siblings. The manifest in
the data backup records the paths. Backups can contain credentials or private
conversation data; keep them private. They are not active runtime paths.

### One shared reset recovery point

After a **successful** CLI or in-app reset, Unified automatically retains its
recovery set and deletes older validated successful reset sets for that same
data directory. The two entry points share one keep-one policy, not one backup
each. A set includes every paired recovery folder, including the previous tool
environment outside the data directory. For in-app resets,
`backups/<id>/private-state.tar.gz` and `backups/reset-<id>/` are one set with
the same operation ID. Recovery covers the latest reset, not a separate point
for each previously selected component. Archive contents and restore format
are unchanged.

CLI reset commits success only after installation verification, saved update
state, restart and passing `doctor` and service-status checks. With `--no-start`,
verified installation and passing `doctor` count as a successful, intentionally
stopped reset. A failing doctor, installation, rollback, startup or readiness
check does **not** prune earlier sets. In-app success requires both the applied
reset stores and its terminal action outcome to be durably saved.

Reset ownership and monotonic successful-completion order live in
`<data-dir>/reset-recovery/state.json`; modification times do not choose the
retained point. Do not edit this ledger or member ownership markers. Pending,
failed, cancelled, interrupted or unverified attempts are preserved as unresolved
recovery evidence; a later success does not silently discard them. Legacy CLI
manifests lack a reliable successful-completion witness, and legacy in-app
archives cannot reliably be paired with retained originals. Those copies are
reported for explicit review, never paired by timestamps or automatically
deleted. Independent manual private backups, migration backups, current data,
workspaces and shared CLI history are not reset-retention candidates. Storage
keeps the newest manual private backup separately and protects reset members.

If deletion fails, Unified reports **reset succeeded; older-backup cleanup
incomplete**, preserves the new point and saves retry progress. It does not
claim exactly one set remains. Resolve the reported access/I/O issue, then retry
without resetting again:

```sh
amplifier-unified reset --data-dir /path/to/unified-data --cleanup-only
```

The equivalent in-app/agent action is `maintenance.resetCleanup`. The next
successful reset also retries obsolete-member cleanup. Cleanup is awaited, runs
off the in-app event loop and outside database/runtime/publishing locks, and
does not replay reset changes. Unresolved attempts and ambiguous legacy material
still need explicit owner review. If committing the retention ledger itself
fails after a healthy reset, old copies remain; do not promote the unverified
record by hand or assume cleanup retry can establish missing success evidence.

Do not restore an old update pointer by itself: it refers to matching retired
runtime directories. Keep recovery copies private.

### Linux: background service manager unavailable

Reset reconnects to your existing user service manager when a terminal is missing its login environment. If Linux reports “Failed to connect to bus” or “No medium found” and no user service manager is available, sign in directly as the account that installed Unified and retry. On a machine without a desktop login, an administrator may need to enable background services for that account with `sudo loginctl enable-linger ACCOUNT`, then have that user sign in again. Run reset as the original user, never with `sudo`. Reset leaves the installation unchanged when it cannot safely stop the service.

If release discovery fails, repair tries both the anonymous GitHub release API
and GitHub's public latest-release link before optional account access. The
error distinguishes HTTP failures (including reported rate limits), DNS,
timeouts and TLS certificate verification. It never disables TLS verification.
Use the private repair report for more detail.

If Git downloads work but release discovery is unavailable, you can bypass the
lookup with `amplifier-unified reset --anonymous --source git+https://github.com/microsoft/amplifier-unified@FULL_RELEASE_COMMIT`.
Replace `FULL_RELEASE_COMMIT` with the exact commit of a verified published
release; do not use a moving branch. This performs the normal repair, including
its backup and service restart. `--anonymous` alone does not bypass network,
certificate or rate-limit failures.

Public repository downloads automatically retry once without GitHub sign-in when
an account is rejected by organization SSO or authentication. The retry affects
only child downloads and leaves your saved sign-ins and Git configuration intact.
`--anonymous` remains available to force that mode immediately. Private sources
still need authorized access. This policy also applies to in-app update downloads;
see the [repository access contract](../contracts/repository-access.v1.md).
