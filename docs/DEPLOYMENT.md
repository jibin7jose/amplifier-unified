# Deploying Unified beyond localhost

This guide is the canonical reference for making Unified available on a LAN or Tailscale network. It covers the service host, TLS trust, and Linux service management. The short path is in the [README](../README.md).

## Before you start

Unified defaults to `127.0.0.1:8941`. Remote access requires:

- an exact HTTPS origin for every address people will use;
- a certificate trusted on every client device (trust Unified's local CA when using `setup-tls`); and
- a non-loopback bind address.

Use an already trusted local console or SSH connection to configure the host. Do not disable certificate checks or enter credentials through an untrusted HTTPS connection.

## Configure the host

Replace the examples with the hostname and IP address clients will use:

```sh
amplifier-unified config set public_origins '["https://your-host.tailnet.ts.net:8941"]'
amplifier-unified setup-tls
amplifier-unified config set bind '["100.64.0.10", "127.0.0.1"]'
amplifier-unified doctor
```

The private server configuration is stored at `config/server.yaml` under Unified's data directory, normally `~/.amplifier-unified`. Non-loopback binds do not start until TLS and at least one exact HTTPS public origin are configured.

Additional top-level settings from local tooling or other versions do not prevent startup. Unified preserves them when saving ordinary configuration changes and does not activate features it does not implement. Known settings, including TLS, public origins and worker retention, retain their validation. Loading an existing configuration does not rewrite its file; an explicit `config reset all` resets the complete configuration.

`setup-tls` creates Unified's local CA and a leaf certificate. Run `setup-tls force`
after changing an address that must appear in the certificate or to renew an
expiring leaf; it keeps the CA and replaces the leaf certificate. Restart the
service afterward. An expired/expiring CA needs a deliberate CA rotation and
redistribution of client trust, not `setup-tls force`, which preserves that CA.

`doctor` inspects the configured certificate's validity (including a 30-day
expiry warning), SAN coverage of every explicit bind and public-origin host,
and chain against the app CA or this computer's system roots for supplied
certificates. IP addresses require IP SANs; DNS wildcards cover one label only.
Wildcard binds are not certificate identities. Include each intended LAN and
Tailscale name/IP in `public_origins` before renewing an app-owned leaf.
These are offline checks of configured files, without DNS queries or credentials:
they do not prove which certificate the running process serves, network access,
or trust on another device. Supplied certificates should be renewed with their
issuer, not replaced with an app-owned CA. Native Windows certificate diagnostics
and authenticated service/process qualification remain pending.

To use an existing trusted certificate and its private key, keep the bind on
loopback while configuring the pair, then enable the remote bind:

```sh
amplifier-unified config set tls.method ca
amplifier-unified config set public_origins '["https://your-host.example:8941"]'
amplifier-unified config set tls.cert '/absolute/path/server.crt'
amplifier-unified config set tls.key '/absolute/path/server.key'
amplifier-unified config set bind '["192.0.2.10", "127.0.0.1"]'
```

The certificate and key commands may be entered in either order. Until both are
configured, HTTPS cannot start and a non-loopback bind is refused. Invalid
configuration changes leave the previous saved configuration intact. To replace
an existing pair together, use one atomic setting:
`amplifier-unified config set tls '{method: ca, cert: /absolute/path/server.crt, key: /absolute/path/server.key}'`.
Starting the server also verifies that the certificate and key files can be loaded
as a matching pair; saving their paths alone is not a successful TLS check.

`amplifier-unified setup-tls status` reports the certificate/key readiness
separately from the availability of Unified's local CA. A supplied certificate
can be ready even when no app-owned CA exists to export. This check does not
establish that a client trusts the certificate, or that it covers the public
hostname.

If Unified uses a non-default data directory, pass the same `--data-dir` value to `setup-tls` and `doctor`.

## Trust the CA on each client

On the Unified host, export the public CA and record the fingerprint:

```sh
amplifier-unified setup-tls export > amplifier-unified-ca.crt
amplifier-unified setup-tls status
```

Transfer the CA over an already verified connection, then verify it before trusting it:

```sh
scp '<owner>@<host>:/absolute/path/amplifier-unified-ca.crt' .
openssl x509 -in amplifier-unified-ca.crt -noout -fingerprint -sha256
```

Compare that fingerprint with the trusted host's `amplifier-unified setup-tls status` output. Use the same data directory as the running host if non-default. On macOS, use Keychain Access's **login** keychain and trust it for SSL. Windows can use the current user's Trusted Root Certification Authorities store. Follow your organization's approved process for mobile-device certificate trust.

The `/setup` page can provide platform-specific follow-up instructions after TLS is trusted. Its displayed fingerprint or an anonymous certificate download is not independent proof of authenticity.

### Ubuntu and Debian clients

Run these steps on the computer running the browser, not the Unified server.
WSL trust does not configure a Windows browser. Other Linux distributions need
their own approved procedure. Use a normal-user terminal with `sudo` access and
the `openssl`, `coreutils` and `ca-certificates` packages. If tools are missing or
policy blocks installation, ask your administrator; the page installs nothing.

1. Set the exact public CA path from the trusted transfer. For a download from an
   **already trusted** connection:

```sh
ca_file="$HOME/Downloads/amplifier-unified-ca.crt"
```

If the browser saved a duplicate, use this **instead**:

```sh
ca_file="$HOME/Downloads/amplifier-unified-ca (1).crt"
```

Keep the quotes around spaces and parentheses. Adjust the directory if needed;
do not use a wildcard or select an arbitrary downloaded certificate.

```sh
openssl x509 -in "$ca_file" -noout -subject -issuer -fingerprint -sha256
```

2. **Stop and compare the SHA-256 fingerprint** with `amplifier-unified setup-tls
   status` on the intended host, through a trusted console or verified SSH. Do
   not install if the fingerprints differ or origin is uncertain. A root CA can
   authenticate other sites, not just Unified.
3. After verification, install the single PEM certificate using the same terminal.
   The destination is absolute `/usr/...`, **not `~/usr/...`** under your home:

```sh
ca_dest=/usr/local/share/ca-certificates/amplifier-unified-ca.crt
if [ -e "$ca_dest" ] || [ -L "$ca_dest" ]; then
  printf '%s\n' "Stop: $ca_dest already exists; ask your administrator to check it." >&2
  false
elif [ "$(grep -c -- '-----BEGIN ' "$ca_file")" != 1 ] ||
     [ "$(grep -c -- '-----END ' "$ca_file")" != 1 ]; then
  printf '%s\n' "Stop: expected exactly one PEM certificate, not a bundle." >&2
  false
else
  sudo install -d -m 0755 /usr/local/share/ca-certificates &&
  sudo install -m 0644 -- "$ca_file" "$ca_dest" &&
  sudo update-ca-certificates
fi
```

The stable `.crt` destination holds exactly one PEM certificate, readable by
applications. Do not overwrite an existing CA; another host needs a distinct
destination. Stop on any error. A rehash warning about the generated
`ca-certificates.crt` bundle containing multiple certificates is normally benign,
but a warning naming your individual CA file needs investigation.

4. Check OS trust:

```sh
openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt "$ca_file"
```

Expect the selected filename followed by `OK`. OpenSSL can also consult its
configured default trust locations. This checks OS trust for the CA, not the
server's hostname/connection or the browser's trust.

5. Save browser work, fully quit and reopen the browser yourself, and open the
   intended HTTPS URL. Confirm there is no certificate warning before signing in.
   Firefox, Chromium/Chrome and Edge can use different stores depending on version,
   packaging, profile and policy. Confined Snap packages may not inherit OS trust.
   OS success is **not** proof of browser success.

If the browser still says **Not secure**, read its connection/certificate details
before importing anything again. If warnings were turned off for this site,
save unsent work, turn warnings back on and reload without proceeding past a
warning. Check the served host and chain against the verified CA. Match the
URL hostname against the Subject Alternative Name (SAN), not just the Common
Name (CN). A hostname mismatch needs the correct URL or server-certificate
repair; a date error needs a clock/validity check, not another CA import. For an
untrusted issuer with matching host/CA identities, inspect the installed browser's
certificate manager and follow its local-CA/Authorities import procedure or your
administrator's policy. Firefox has a separate Authorities store. Do not disable
verification, bypass warnings or run NSS/certutil against a guessed database.
Chromium's documented Linux NSS location has changed across versions; it is not
evidence of the location used by a particular Edge installation.

**Back out only your new installation**, after checking that this exact path is
the file you installed (not a pre-existing CA):

```sh
sudo rm -- /usr/local/share/ca-certificates/amplifier-unified-ca.crt &&
sudo update-ca-certificates --fresh
```

Remove any separately imported browser copy in that browser's certificate
settings too. OS removal alone does not prove browser removal.

References: [Ubuntu trust-store guide](https://ubuntu.com/server/docs/how-to/security/install-a-root-ca-certificate-in-the-trust-store/),
[Debian update-ca-certificates](https://manpages.debian.org/testing/ca-certificates/update-ca-certificates.8.en.html),
[Chromium Linux certificate management](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/linux/cert_management.md),
[Firefox certificate policies](https://firefox-admin-docs.mozilla.org/reference/policies/certificates/),
and [Edge root-store policy history](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/microsoftrootstoreenabled).
The obsolete Edge root-store policy is not a workaround.

## Keep the host running

On Linux, including a WSL distribution with systemd enabled, install and manage
Unified with:

```sh
amplifier-unified service install
amplifier-unified service status
```

Available lifecycle commands are `install`, `start`, `stop`, `restart`, `status`, `logs`, and `uninstall`. Repeating `install` with the same configuration reuses its definition and starts an inactive service without restarting a running host. After a package update, use `amplifier-unified service restart` to load the new version.

`service status` checks every locally assigned numeric address resolved from its
configured binds, not just the first successful response. Each `Host listener`
line is a matching authenticated health response with the actual HTTP or HTTPS
scheme. Failed probes and configured binds remain separate from verified
listeners; the singular JSON `url` remains the first success, while `listenerUrls`
contains all successes, `listenerChecks` retains individual failures, and
`listenerScope` states the evidence limit and `listenerScopeComplete` reports
whether candidate enumeration/admission was complete.
Service readiness can still be true when one listener responds and another
probe fails; inspect the individual results. A name is resolved once per pass
and only locally assigned results are probed, with no second DNS lookup on
connect. Probes use at most four concurrent two-second requests. On Linux and
macOS, wildcard binds enumerate assigned IPv4/IPv6 addresses directly through
the OS `getifaddrs` interface snapshot, including LAN, VPN and scoped link-local
addresses, without public DNS. Each numeric candidate must still pass a local
ownership check and authenticated health probe. Unsupported/failed enumeration
falls back to loopback and reports **PARTIAL** scope, never complete wildcard
coverage. Interfaces can change after the snapshot. This is not an inventory of
unconfigured sockets, and it does not infer LAN reachability from loopback
success. Configured public addresses are listed separately as
**not checked**: a healthy host does not prove DNS, network access or certificate
trust on another device. The authenticated status probe never follows redirects
or selects `public_origins` as credential-bearing probe targets. Nonlocal or
unresolved bind names remain configuration only, not verified listeners; Linux
nonloopback probes fail closed if global `ip_nonlocal_bind` prevents establishing
local ownership. DNS resolution uses the operating system's resolver timeout,
not the per-request HTTP timeout. Normal launch still
opens the configured public address when one is set; verify it on the client.

Foreground `serve` announces the listener and schedules browser opening only
after all configured addresses bind successfully. An occupied port, unavailable
address or permission refusal gives a specific next step. It does not stop an
existing process, choose another interface, or change the saved configuration.

If the generated definition differs, rerun the same install command with `--replace` to save a timestamped private backup and restart with the new definition. The command reports the backup location. Custom service files are preserved unless `--replace` is explicit; review them before replacing. Expected service setup errors are reported without a Python traceback and do not undo a separately completed package installation.

Generated systemd services use `KillMode=mixed`: shutdown signals the host first,
allowing it to close its workers deliberately, while the service manager retains
final cleanup of the entire control group. Custom instance generators should
use the same policy. Older units using `control-group` can signal workers before
the host begins cleanup and record a false worker failure during a restart;
updating the package alone does not replace those unit definitions.

The installer supplies the selected `uv` and app executable directories to the
launcher before service registration. The service captures that `PATH` and
supplements it with the user's standard helper directories (`~/.local/bin` and
`~/.cargo/bin`; also Homebrew locations on macOS). Startup applies the same rule
to older service definitions, including after reset, without changing their
workspace, data directory, or custom configuration. Existing PATH precedence and
dispatcher symlinks are preserved. No shell startup scripts are evaluated.

If `uv` lives in a custom directory and works in a terminal but not in the app,
run `amplifier-unified service install --replace` from that terminal, preserving
any original `--data-dir` and `--workspace` options. This backs up the definition
and restarts the service; saved chats and settings remain. Confirm with
`amplifier-unified service status --wait 60`, then refresh the browser and retry
the connection. A reboot or reinstall alone does not change an older service's
captured custom PATH. `amplifier-unified doctor` checks helper discovery as well
as application settings; it does not claim provider sign-in succeeded.

Installation acceptance must include a fresh Linux user with no curl/Git/uv,
OS-correct grouped prerequisite guidance, installer-provisioned uv absent from
the original PATH, service startup with that environment, and later helper
discovery with a stale service PATH. Missing helpers during ChatGPT sign-in
must produce an actionable setup error, while arbitrary provider errors stay
redacted. These are covered by `test_installer_script.py`,
`test_host_dependencies.py`, and `test_deployment_service.py`; retain them in
the onboarding smoke checks when changing installation or service ownership.

For WSL, enable systemd in `/etc/wsl.conf`, restart WSL with `wsl --shutdown`
from Windows PowerShell, then run the Linux installation commands above.

On macOS, `amplifier-unified service install` creates and starts a per-user
launchd agent. Its standard output and error logs are private files under
Unified's data directory. Use `amplifier-unified service status` to inspect the
agent and `amplifier-unified service logs` to read its recent output.

## Installed app opens a cross-origin error

An installed PWA or external link can enter through its service worker.
Chromium's `fetch(event.request)` forwards that navigation with
`Sec-Fetch-Mode: navigate` but changes `Sec-Fetch-Dest` from `document` to the
literal `empty`. Servers that require `document` for every entry reject the
forwarded launch with `{"error": "Cross-origin requests are not permitted."}`.

The entry check permits this forwarded form only for GET/HEAD `/` and `/login`,
with no Origin header. Host validation, login/session authentication, exact
Origin checks, CSRF checks, and cross-site API/frame rejection remain in place.
Do not work around this error by disabling these protections or accepting
arbitrary origins. A server containing this fix handles already-installed
workers; reinstalling the PWA is not required for this particular failure.

The regression in `tests/test_browser_auth.py` exercises the shipped service
worker and real Chromium request metadata before and after worker control,
including sign-in and blocked API/frame navigation. It is not a physical
Android Edge installation test.

## Desktop window controls overlay

The manifest prefers `window-controls-overlay` with `standalone` fallback.
Supporting installed Chromium/Edge apps can enable it from their browser-owned
title-bar controls. Declaring it does not prove the browser has enabled it:
the frontend activates only when `navigator.windowControlsOverlay.visible`
reports a valid title-bar rectangle.

The compact header occupies that usable rectangle, reserving the native
buttons on either side. Small usable areas keep a blank draggable strip and
the header below it. Focused Canvas, dialogs, loading, login and offline pages
also preserve native clearance. Buttons, links, tabs, editors and contributed
interactive controls are excluded from dragging. Appearances supply chrome
colors; they do not supply the native geometry.

After rebuilding the frontend, run `npm run test:wco-browser` and
`npm run test:window-chrome-browser` under Node 22+. The former substitutes
native geometry, not application CSS; neither establishes native dragging.
For an optional real Linux installed-window probe with an available desktop:

```sh
AMPLIFIER_TEST_INSTALLED_PWA=1 uv run --with playwright python -m pytest -q -s \
  tests/test_browser_auth.py::test_installed_pwa_launch
```

It installs and removes only a disposable profile's app, signs in with synthetic
credentials, and records whether overlay actually became visible. It does not
enable the browser's overlay toggle or click native window controls. Keep
that result separate from a real macOS/Windows check of dragging, button
operation, appearance changes and browser fallback. A successful standalone
launch is not overlay acceptance. Deployment and physical-phone verification
remain separate from these local tests.

## Limits

Unified owns its own HTTPS listener and authentication. It does not trust a reverse proxy, shared cookies, or `X-Forwarded-*` headers. Configure exact public origins rather than broad address patterns.

The public bootstrap endpoints are limited to `/login`, `/setup`, `/api/ca`, `/ca.crt`, and `/api/health`.

## Debug-only profiling

Opaque server extensions remain unchanged on load and save. The profiling
consumer accepts only a literal boolean `true`; other extension values do not
enable capture or prevent startup.

The optional `amplifier-profiling` library must be installed in the serving host's
Python environment. Normal installations do not require it. An operator can
enable it through `amplifier-unified config set debug.profiling true`; no app
action, bundle or tool configuration can enable this setting.

`profiling.status`, `profiling.targets`, `profiling.start`, `profiling.read`,
`profiling.stop` and `profiling.release` are authenticated shared actions.
Mutations require a stable command ID. Start accepts only the process-local target
returned by targets, 0.1–60 seconds, and 1–50 Hz. Results are memory-bounded and
process-local, not retained across restart; small admission receipts prevent an
exact retry from starting another capture. An unsettled receipt after restart is
reported unavailable, never replayed. Download settled captures from the returned
authenticated URL before explicitly releasing their retention slot.

Disable through `amplifier-unified config set debug.profiling false`. While a
capture is active, the host rechecks config every 250 ms (subject to event-loop and
filesystem delays); malformed, missing or unreadable config fails closed.
Download authorization is rechecked before returning bytes; already-returned
bytes cannot be recalled. Shutdown stops the sampler.

This is cooperative sampling of all Python threads in **the serving host
process**, not arbitrary PID attachment. It records symbol metadata, not locals,
arguments, source text, prompts or event bodies. GIL delays, missed samples and
backend limitations remain visible. Function observations are wall-stack counts,
not per-function CPU percentages. Do not infer native-thread/C-stack coverage.
Profiles never enter canonical Context Intelligence capture or ordinary app-state
publication. The native event JSONL and resume transcript remain authoritative.

Enabling this setting grants the authenticated owner and its agents access to
process-wide symbol metadata. Unified is currently single-owner; do not enable
this adapter in a multi-tenant shared process without separate operator-only
authorization and tenant-isolated targets. Hosted containers need no ptrace or
root privileges for self-sampling. External/native sampling is not supplied.

## Rebuildable history metadata

The app's `native-catalog.sqlite3` stores derived metadata, not canonical
transcripts or context-intelligence event bodies. Cache loading validates
workspace path and availability types before discovery consumes them. An invalid
cache is reported as unavailable and bypassed on ordinary and forced refreshes;
original history and the rejected cache are preserved. This fallback is not
permission to delete or repair canonical history. Resume admission continues to
read native metadata rather than trusting cached classification.
