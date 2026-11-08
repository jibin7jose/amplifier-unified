# Amplifier Unified

Amplifier Unified is a local, authenticated workspace for Amplifier. It provides a web app for chat, voice, and work with your configured Amplifier bundles and tools.

## Writing messages

The chat input formats Markdown as you type: `**bold**`, `_emphasis_`,
`~~strikethrough~~`, headings with `# `, and lists with `* ` or `1. `.
Type three backticks, optionally a language such as `python`, then **Enter**
or **Space** to open a code block. **Enter inside code inserts a newline and
keeps the line's indentation; it never sends.** Use **Alt+Enter** to leave the
block, or the Send button to send it. Undo restores the typed fence.

Outside literal blocks, press **Enter** to send, or **Shift+Enter**
or **Ctrl+Enter** to continue writing. In a list, these continue the list;
**Backspace** at the beginning of an empty item returns to a paragraph.
Undo and redo work as usual. Pasted images remain attachments, and pasted text
longer than 10,000 characters becomes a **Pasted text** attachment.

Select text to use the compact formatting controls: link, bold, italic, inline
code, strikethrough, headings, lists, task lists, ordinary block quotes and code
blocks. **Alt+F10** focuses these controls; **Escape** returns to the selection.
Right-click still opens the browser's copy, paste and spelling menu. The app does
not replace those native controls.

The composer reuses CommonMark and the reader's GitHub Flavored Markdown (GFM)
grammar. Tables, task lists, strikethrough and safe links have rich representations.
Where the rich representation would change GFM meaning, it keeps editable
**Markdown source** instead. Reference definitions, footnotes, alerts, math,
front matter, raw HTML and CRLF input use this literal representation.
Markdown source is not a rendered preview: its punctuation remains visible.
Rich editing may normalize equivalent Markdown delimiters and spacing.
GitHub website features such as mentions, issue references and emoji shortcodes
remain notation, with no GitHub account lookup. Diagram fences remain literal
code in the editor. Images are inert references; raw HTML never executes.

Reply adds a saved quotation inside the composer without sending or moving your
reading position. Add several messages, or select text and choose **Quote
selection** beside the selected text. Formatted passages, simple lists, links
and literal code retain exact source spans. Selections that cannot be mapped
exactly (such as transformed entities or complex blocks) show an explanation
without adding a quote or opening a source editor.
Write before, between and after quotes. Quote controls move or remove only that
quote; commentary and attachments stay. Long quotes expand to show their full
text. Quotes are immutable historical references, not new instructions.
Quote drafts have a larger viewport-bounded writing area; source and move/remove
controls remain reachable while scrolling within a quote.

This first increment quotes supported saved messages in the current chat only.
It rejects oversized or ambiguous selections instead of silently expanding or
truncating them. Corrections to a sent quoted message use a new reply; ordinary
plain-message editing is unchanged.

### Choose settings before resuming a chat

The conversation's model and bundle controls save **Next message** choices
without starting its worker. You can read history, browse saved artifacts and
rename the chat while preparation is unavailable. Saved choices are not a claim
that the runtime has applied them. The next genuine input validates the selected
bundle, provider, model and effort before delivery; a failed check keeps the
choices and does not silently substitute another model. Active work and unresolved
configuration operations keep their existing guards. Automatic prewarming is not enabled.

### Collapse interim replies (experimental)

In **Settings → Appearance**, enable **Collapse interim replies** to move earlier
assistant updates into the existing work details after a confirmed final response.
It is off by default and applies to this browser. Expand the disclosure to read,
copy, or reply to those messages; exports retain the complete conversation.
The explicit option also collapses these groups at the Everything detail level,
but preserves details you opened manually. Active, interrupted, or unqualified
older turns remain visible. Turning the option off restores normal placement.

## Set up Unified on your computer

On a fresh **Ubuntu or Debian** computer (including WSL), first install the
download prerequisites together:

```sh
sudo apt-get update && sudo apt-get install -y curl git ca-certificates
```

On **macOS**, install Apple's command line tools if Git is not already available:
`xcode-select --install`. Other Linux distributions need curl, Git, and CA
certificates from their package manager.

Then, for macOS and Linux (including WSL with systemd), paste this into your terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/microsoft/amplifier-unified/main/scripts/install.sh | bash
```

The installer finds or installs uv and Python, verifies the latest stable release,
installs Unified, starts its per-user service, and opens your browser. Git and curl
must be available. Existing Amplifier CLI configuration, accounts, and history are
kept. Run the Amplifier installer as your normal user, without sudo. You do not
need to install uv or Python yourself, reopen your terminal, or reboot.

For more detail, add `-s -- --verbose` after `bash`. Use `--version 0.20.101`
for a specific stable release or `--no-open` to print the address without opening
a browser. Installation logs are private and their location is printed.

In the web app, connect an AI account if needed. You can finish setup with an
assistant alongside Settings, or go straight to a new chat. Component preparation
continues there with expandable details; errors remain visible for review.

To manage prerequisites and package installation yourself, use the instructions
below with [uv](https://docs.astral.sh/uv/). Select Python **3.13** explicitly,
as the installer does. A newer system Python can lack prebuilt dependency wheels
and trigger compiler errors; you do not need to remove or downgrade it.
`uv` can download the requested Python into its own managed installation.

**Native Windows is not yet supported.** The Bash installer is for macOS,
Linux and WSL, not PowerShell. Package installation alone does not establish
native sign-in, worker readiness or safe background startup.
The [conditional foreground trial runbook](docs/design/native-windows-installation.md#conditional-foreground-trial-runbook)
describes an isolated PowerShell trial **without WSL**, but awaits an exact
qualified, browser-activated candidate from the Windows trial owner. It is not
a command to run the current public release. The native installer, default
background launcher and Task Scheduler path remain unqualified.
For the zero-developer-tool-prerequisite work, see the
[explicit Windows bootstrap prototype](docs/design/native-windows-installation.md#zero-prerequisite-bootstrap-prototype).
It uses a reviewed candidate manifest and private portable tools, not a claim
that the public Windows installer is enabled.

<details>
<summary><strong>Linux</strong></summary>

Install Unified, then set it up as a background service:

```sh
uv tool install --python 3.13 git+https://github.com/microsoft/amplifier-unified@v0.20.101
amplifier-unified
```

Open [http://127.0.0.1:8941](http://127.0.0.1:8941) and sign in with the
account that runs Unified.

</details>

<details>
<summary><strong>Windows with WSL</strong></summary>

Use a Linux distribution in WSL with systemd enabled. In the WSL distribution,
add this to `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

From Windows PowerShell, restart WSL:

```powershell
wsl --shutdown
```

Open WSL again, then install Unified and set it up as a background service:

```sh
uv tool install --python 3.13 git+https://github.com/microsoft/amplifier-unified@v0.20.101
amplifier-unified
```

Open [http://127.0.0.1:8941](http://127.0.0.1:8941) in your Windows browser
and sign in with the account that runs Unified.

</details>

<details>
<summary><strong>macOS</strong></summary>

Install Unified, then set it up as a background service:

```sh
uv tool install --python 3.13 git+https://github.com/microsoft/amplifier-unified@v0.20.101
amplifier-unified
```

Open [http://127.0.0.1:8941](http://127.0.0.1:8941) and sign in with the
account that runs Unified.

</details>

The first launch sets up and starts your background service, waits until it responds,
and opens your browser. Later launches reuse it. To run only in the current
terminal, use `amplifier-unified serve`. Package installation alone does not
register a service. On macOS, the background activity notification confirms
registration; the launcher separately checks that the app responds.

The first message can take a few minutes while Unified prepares its runtime.

Published wheels and source archives (`.tar.gz` release assets) include the built
web client. Installing either format needs no Node.js, npm or frontend download;
Python and runtime dependencies are still resolved normally. A raw Git checkout
is a development source tree: rebuilding or changing its client requires Node.js
and npm. See the [development setup](CONTRIBUTING.md#development-setup).

After installation, open **Settings → Updates** to check for and install updates,
review any failed checks, or choose the automatic check interval. See the
[update guide](docs/UPDATES.md) for active-work handling and recovery details.

For opt-in, read-only local observations shared with CLI tools, see the
[status emitter guide](docs/status-emitter.md). Publication is disabled by default.

## Default capabilities

Ordinary conversations compose Unified shell, image generation, Computer Use,
full Context Intelligence, and Model Routing over the selected root bundle.
Existing explicit capability lists gain the latter four unless opted out;
chosen source revisions and list order are retained. Saved snapshot bundles
are complete plans and do not gain these additions.

Use **Settings → Configured bundles** to disable or remove a default. A retained
routing-matrix setting does not re-enable a disabled routing behavior. The
Context Intelligence opt-out removes its analysis/navigation capabilities;
Unified's existing local capture hook remains.

New Computer Use access is read-only. It targets the **host computer**, not the
browser's shared screen. Explicit module configuration can enable writes with
`overrides.tool-computer-use.config.read_only: false`; existing explicit access
settings remain authoritative. Child sessions cannot widen a parent's desktop
restrictions.

Context Intelligence forwarding requires explicit destination configuration;
ambient server credentials alone do not enable it. Explicit routing matrices,
agent provider choices, and per-delegation model pins take precedence over
default role routing. When only automatically added routing has no candidate,
the child inherits the conversation's selected provider, model, and effort
(retaining that snapshot on resume). If there is no selected model to inherit,
the delegation fails visibly. Explicit matrices, including an explicitly chosen
`balanced` matrix or a user-authored override, remain strict, as do unavailable
provider/model pins and resolver errors. A saved explicit app-behavior list is
treated as explicit routing when its original intent is ambiguous.
An effective-plan edit containing `hooks` also makes routing explicit, even if
the hook values are unchanged. A custom child orchestrator that cannot apply the
inherited selection fails visibly rather than substituting another model.

For ordinary roots already including the upstream logging behavior, its ambient
URL/key placeholders alone no longer opt into forwarding; configure a destination
or explicit scoped legacy URL/key settings. An explicit empty destination map
still disables forwarding. A named agent's explicit provider/model pin also
outranks a call's role-based default; pass explicit `provider_preferences` to
override that pin. Snapshot roots retain their saved composition and prior
routing lifecycle rather than adopting this ordinary-root policy.

## Create a dedicated desktop or mobile app

You can add Unified to a desktop or mobile device as its own app-like window or home-screen icon. This does not install or move the Unified service; it opens the same workspace more directly.

After opening Unified in a browser:

- **Chrome or Edge:** use the install icon in the address bar or the browser's install menu.
- **Safari on macOS:** choose **File → Add to Dock**.
- **Safari on iPhone or iPad:** choose **Share → Add to Home Screen**.

This works on desktop and mobile platforms supported by your browser. Unified must keep running at the same address.

## Access Unified over your LAN or Tailscale

<details>
<summary><strong>Set up access from another device</strong></summary>

Unified listens only on `127.0.0.1:8941` by default. To use it from another
device, configure trusted HTTPS with the exact address you will use:

```sh
amplifier-unified config set public_origins '["https://your-host.tailnet.ts.net:8941"]'
amplifier-unified setup-tls
amplifier-unified config set bind '["100.64.0.10", "127.0.0.1"]'
amplifier-unified doctor
```

Replace the example origin and Tailscale IP with your host's real addresses. Before opening the remote address, export Unified's public CA on the host, verify its fingerprint with `amplifier-unified setup-tls status`, and trust that CA on each device. Do not disable certificate checks.

For the complete setup—including certificate transfer and trust, reverse-proxy limits, and Linux service management—see the [deployment guide](docs/DEPLOYMENT.md).

</details>

<details>
<summary><strong>Repair a broken installation</strong></summary>

First, get the latest recovery command:

```sh
uv tool install --force --python 3.13 git+https://github.com/microsoft/amplifier-unified@main
```

Then preview the repair or run it:

```sh
amplifier-unified reset --dry-run
amplifier-unified reset
```

Reset reinstalls Unified while keeping your chats, settings, sign-ins, and files.
It handles stopping and restarting the app, then runs the installation and service
checks for you. Allow a few minutes. Public releases do not require GitHub sign-in.
Use `reset --anonymous` if personal Git configuration or organization sign-in
policy prevents public downloads. [More recovery options](docs/RESET.md).

</details>

## Contributing

Review the [repository privacy guidance](CONTRIBUTING.md) before publishing
examples, diagnostics or screenshots.

The [AHP/ACP architecture design packet](docs/architecture/README.md) describes
the proposed protocol boundaries, client-local state, bounded history discovery
and repository decomposition. It includes ratified vision/contracts and parallel
implementation lanes; it is not a claim about current release capabilities.

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
