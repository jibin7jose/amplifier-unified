"""READY.'s portable appearance and passive built-in library contract."""
from copy import deepcopy
from importlib.resources import files
import re

import pytest
import tinycss2

from amplifier_web import theme_library
from amplifier_web.service import AppError, validate_theme
from test_canvas_apps import app, dispatch


def resource():
    return files("amplifier_web").joinpath("appearances", "ready.amplifier.css")


def source():
    return resource().read_text(encoding="utf-8")


def rules():
    """Parse authored declarations and retain media/container context."""
    def walk(nodes, context=""):
        for node in nodes:
            assert node.type != "error"
            if node.type == "at-rule":
                assert node.lower_at_keyword in {"media", "container"} and node.content is not None
                prelude = tinycss2.serialize(node.prelude).strip()
                nested = f"container {prelude}" if node.lower_at_keyword == "container" else prelude
                yield from walk(tinycss2.parse_rule_list(
                    node.content, skip_comments=True, skip_whitespace=True,
                ), nested)
                continue
            assert node.type == "qualified-rule"
            declarations = tinycss2.parse_declaration_list(
                node.content, skip_comments=True, skip_whitespace=True,
            )
            assert all(value.type == "declaration" for value in declarations)
            yield context, tinycss2.serialize(node.prelude).strip(), {
                value.name: tinycss2.serialize(value.value).strip()
                + ("!important" if value.important else "") for value in declarations
            }

    return list(walk(tinycss2.parse_stylesheet(
        source(), skip_comments=True, skip_whitespace=True,
    )))


def declarations(selector, context=""):
    found = [values for media, target, values in rules()
             if media == context and normalize_selector(target) == normalize_selector(selector)]
    assert len(found) == 1, (context, selector)
    return found[0]


def normalize_selector(selector):
    selector = re.sub(r"\s+", " ", selector).strip()
    return re.sub(r"\s*([,()>])\s*", r"\1", selector)


def pair(root, token):
    value = root["--a-" + token] if token in theme_library.TOKENS or token in {"on-accent", "warning"} else root["--ready-" + token]
    match = re.fullmatch(
        r"light-dark\(\s*(#[0-9a-f]{6})\s*,\s*(#[0-9a-f]{6})\s*\)",
        value,
        re.I,
    )
    assert match, (token, value)
    return match.group(1), match.group(2)


def luminance(color):
    def linear(channel):
        channel = int(channel, 16) / 255
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(color[index:index + 2]) for index in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first, second):
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def test_ready_is_small_portable_static_and_uses_authored_contrast():
    data = resource().read_bytes()
    assert len(data) < 100_000
    css = data.decode("utf-8")
    validate_theme(css)
    metadata, body = theme_library.metadata(css)
    assert metadata["name"] == "Breadbin"
    assert metadata["description"] == theme_library.RESOURCE_PRESETS["ready"]["description"]
    assert css.count(theme_library.MARKER) == 1
    assert body.lstrip().startswith("/* Breadbin.")
    assert not re.search(r"https?://|file:|@import|@font-face|url\s*\(|</?script", css, re.I)

    forbidden = {
        "animation", "animation-name", "transition", "mask", "-webkit-mask-image",
        "position", "z-index", "display", "height", "min-height", "max-height",
        "width", "min-width", "max-width", "overflow", "overflow-x", "overflow-y",
        "backdrop-filter", "transform", "pointer-events", "content",
    }
    important_font = (
        '#amp-one.a-chat-shell.a-approachable-shell [data-part="messages"] '
        ':is(.a-message, .a-markdown) :is(p, li, span, strong, em, blockquote, td, th, '
        'h1, h2, h3, h4, h5, h6, summary, pre, code)'
    )
    for _, selector, values in rules():
        assert selector.startswith("#amp-one")
        assert "::before" not in selector and "::after" not in selector
        assert all(not name.startswith(("-webkit-mask", "mask")) for name in values)
        assert forbidden.isdisjoint(values), selector
        important = {name: value for name, value in values.items() if value.endswith("!important")}
        if important:
            assert normalize_selector(selector) == normalize_selector(important_font)
            assert important == {"font-family": "var(--ready-mono)!important"}
    assert not any("prefers-color-scheme" in context for context, _, _ in rules())

    root = declarations("#amp-one")
    assert root["color-scheme"] == "light dark"
    for mode in ("light", "dark"):
        assert declarations(f'#amp-one[data-theme-scheme="{mode}"]') == {"color-scheme": mode}
    for token in (*theme_library.TOKENS, "on-accent", "warning", "screen", "code-surface", "code-ink",
                  "user-surface", "user-ink", "user-edge"):
        pair(root, token)
    assert root["--a-accent-ink"] == "var(--a-on-accent)"
    assert "system-ui" in root["--ready-ui"] and "monospace" in root["--ready-mono"]

    text_pairs = [
        ("ink", "bg"), ("ink", "surface"), ("ink", "soft"), ("ink", "tint"),
        ("ink", "screen"), ("ink", "code-surface"), ("ink", "user-surface"),
        ("muted", "bg"), ("muted", "surface"), ("muted", "soft"), ("muted", "screen"),
        ("green", "bg"), ("green", "surface"), ("danger", "bg"), ("danger", "surface"),
        ("warning", "soft"), ("warning", "tint"), ("warning", "surface"),
        ("on-accent", "accent"), ("code-ink", "code-surface"), ("user-ink", "user-surface"),
    ]
    focus_pairs = [
        ("accent", "bg"), ("accent", "surface"), ("accent", "soft"), ("accent", "tint"),
        ("accent", "screen"), ("accent", "code-surface"), ("accent", "user-surface"),
    ]
    for mode in (0, 1):
        colors = {token: pair(root, token)[mode] for token in (
            *theme_library.TOKENS, "on-accent", "warning", "screen", "code-surface", "code-ink",
            "user-surface", "user-ink", "user-edge",
        )}
        for foreground, background in text_pairs:
            assert contrast(colors[foreground], colors[background]) >= 4.5, (
                mode, foreground, background,
            )
        for foreground, background in focus_pairs:
            assert contrast(colors[foreground], colors[background]) >= 3, (
                mode, foreground, background,
            )
        assert contrast(colors["user-edge"], colors["user-surface"]) >= 3


def test_ready_paints_real_parts_and_preserves_native_layout_owners():
    ruleset = rules()
    selectors = [selector for _, selector, _ in ruleset]
    header = '#amp-one.a-chat-shell.a-approachable-shell [data-part="header"]'
    navigation = '#amp-one.a-chat-shell.a-approachable-shell [data-part="navigation"] .a-nav-rail'
    rows = (
        '#amp-one.a-chat-shell.a-approachable-shell [data-part="navigation"] :is('
        '.a-nav-chat:not(.a-nav-chat-rename), .a-workspace-row)'
    )
    body = '#amp-one.a-chat-shell.a-approachable-shell .a-message.a-user > .a-message-body'
    composer = '#amp-one.a-chat-shell.a-approachable-shell [data-part="composer-surface"]'
    assert header in selectors and navigation in selectors
    assert declarations(header)["background-size"] == "100% 3px"
    spectrum_rules = [
        normalize_selector(selector)
        for _, selector, values in ruleset
        if any("var(--ready-spectrum)" in value for value in values.values())
    ]
    assert spectrum_rules == [normalize_selector(header)]
    assert declarations(rows)["padding-inline-start"] == "8px"
    assert declarations(rows)["padding-inline-end"] == "4px"
    assert ".a-nav-chat:not(.a-nav-chat-rename)" in normalize_selector(rows)
    navigation_heading = (
        "#amp-one.a-chat-shell.a-approachable-shell :is("
        ".a-nav-eyebrow, .a-nav-conversations, .a-nav-chat-group-title, "
        ".a-workspace-explorer-heading)"
    )
    assert declarations(navigation_heading)["text-transform"] == "none"

    chat = (
        '#amp-one.a-chat-shell.a-approachable-shell [data-part="messages"] '
        ':is(.a-message, .a-markdown)'
    )
    chat_type = declarations(chat)
    assert chat_type["font-family"] == "var(--ready-mono)"
    assert chat_type["font-variant-ligatures"] == "none"
    assert chat_type["line-height"] == "1.7"
    prose = (
        chat + " :is(p, li, span, strong, em, blockquote, td, th, "
        "h1, h2, h3, h4, h5, h6, summary, pre, code)"
    )
    assert declarations(prose)["font-family"] == "var(--ready-mono)!important"
    assert declarations(navigation_heading)["font-family"] == "var(--ready-ui)"

    wrapper = (
        "#amp-one.a-chat-shell.a-approachable-shell "
        ":is(.a-message.a-assistant, .a-message.a-user)"
    )
    assert declarations(wrapper) == {
        "background": "transparent",
        "border": "0",
        "border-radius": "0",
        "box-shadow": "none",
        "padding": "0",
    }

    assistant = "#amp-one.a-chat-shell.a-approachable-shell .a-message.a-assistant > .a-message-body"
    assistant_body = declarations(assistant)
    assert assistant_body["background-color"] == "var(--ready-screen)"
    assert assistant_body["border"] == "1px solid var(--a-line)"
    assert assistant_body["border-radius"] == "var(--a-message-radius)"
    assert assistant_body["color"] == "var(--a-ink)"
    assert assistant_body["padding"] == "16px 20px"
    assert declarations(assistant, "container conversation (max-width: 520px)") == {
        "padding": "12px",
    }

    bubble = declarations(body)
    assert bubble["background-color"] == "var(--ready-user-surface)"
    assert bubble["padding"] == "11px 16px"
    assert bubble["box-shadow"] == "inset 3px 0 var(--ready-user-edge)"
    narrow = declarations(body, "container conversation (max-width: 520px)")
    assert narrow == {"padding": "9px 12px"}
    footer_rules = [
        (normalize_selector(selector), values)
        for context, selector, values in ruleset
        if not context and ".a-message-actions" in selector
    ]
    assert len(footer_rules) == 1
    assert footer_rules[0][0].endswith(".a-primary")
    assert set(footer_rules[0][1]) == {"background-color", "border-color", "color"}
    wrapper_paint = [
        (normalize_selector(selector), values)
        for _, selector, values in ruleset
        if ".a-message" in normalize_selector(selector)
        and ".a-message-body" not in normalize_selector(selector)
        and ".a-message-actions" not in normalize_selector(selector)
        and any(name in values for name in ("background", "background-color", "border", "padding"))
    ]
    assert wrapper_paint == [(normalize_selector(wrapper), declarations(wrapper))]

    surface = declarations(composer)
    assert surface["background-color"] == "var(--a-surface)"
    assert surface["border-radius"] == "var(--a-composer-radius)"
    assert not any(
        '[data-part="composer"]' in selector and '[data-part="composer-surface"]' not in selector
        for selector in selectors
    )
    assert "outline-color" not in declarations(composer + ":focus-within")
    assert declarations(composer + ":focus-within")["border-color"] == "var(--a-accent)"

    focus_selector = (
        "#amp-one.a-chat-shell.a-approachable-shell :is("
        "a, button, input, select, textarea, summary, [role=\"button\"], [role=\"link\"], "
        "[role=\"checkbox\"], [role=\"radio\"], [role=\"switch\"], [role=\"tab\"], "
        "[role=\"textbox\"]):focus-visible"
    )
    assert declarations(focus_selector) == {
        "outline": "2px solid var(--a-accent)",
        "outline-offset": "3px",
    }
    inner_editor_focus = composer + ' [role="textbox"][contenteditable="true"]:focus-visible'
    assert declarations(inner_editor_focus) == {"outline": "none"}
    # This exception removes only the nested editor outline. The host's
    # composer-surface :focus-within still owns the visible boundary indicator.
    assert "outline" not in declarations(composer + ":focus-within")
    # This authored outline also applies in forced colors; its accent resolves
    # to Highlight below. No separate media-context selector is required.

    controls = declarations("#amp-one.a-chat-shell.a-approachable-shell :is(.a-primary, .a-send)")
    assert controls["color"] == "var(--a-on-accent)"
    delivery = declarations(
        "#amp-one.a-chat-shell.a-approachable-shell .a-message-actions .a-delivery-confirm .a-primary"
    )
    assert delivery["color"] == "var(--a-on-accent)"
    active = declarations(
        "#amp-one.a-chat-shell.a-approachable-shell :is(.a-soft:not([aria-pressed]), .a-primary, .a-send):active"
    )
    assert set(active) == {"box-shadow"}
    assert declarations('#amp-one[data-decorations="off"]') == {
        "--ready-spectrum": "none",
        "--ready-header-bevel": "none",
        "--ready-key-bevel": "none",
        "--ready-key-pressed": "none",
    }
    selection = declarations("#amp-one ::selection")
    assert selection == {"background-color": "var(--a-accent)", "color": "var(--a-on-accent)"}
    forced = declarations("#amp-one", "(forced-colors: active)")
    assert forced["--a-bg"] == "Canvas" and forced["--a-accent"] == "Highlight"
    assert forced["--a-warning"] == "CanvasText"
    assert forced["--a-accent-ink"] == "HighlightText"
    assert forced["--ready-spectrum"] == "none"
    assert declarations("#amp-one ::selection", "(forced-colors: active)") == {
        "background-color": "Highlight",
        "color": "HighlightText",
    }


async def test_ready_is_a_passive_builtin_and_saves_as_a_copy(app):
    before = deepcopy(app.state["theme"])
    selected = app.state["selectedSessionId"]
    listing = (await dispatch(app, "theme.list", {}))["result"]
    row = next(item for item in listing["items"] if item["id"] == "builtin:ready")
    assert row["name"] == "Breadbin" and row["source"] == "built-in"
    assert row["tag"] == "Retro / Hardware"
    assert row["radius"] == "5px" and row["background"] == "none"
    assert row["palette"]["accent"] == theme_library.RESOURCE_PRESETS["ready"]["palette"]["accent"]
    assert "css" not in row
    assert not theme_library.folder(app).exists(), "Browsing a built-in does not allocate a user library"

    theme = (await dispatch(app, "theme.read", {"id": row["id"]}))["result"]
    assert theme["css"] == source()
    assert theme["fingerprint"] == row["fingerprint"] == theme_library.fingerprint(source())
    saved = (await dispatch(app, "theme.save", {"name": "My READY copy", "css": theme["css"]}))["result"]
    copy = (await dispatch(app, "theme.read", {"id": saved["id"]}))["result"]
    assert saved["id"].startswith("saved:") and copy["name"] == "My READY copy"
    assert theme_library.metadata(copy["css"])[1] == theme_library.metadata(theme["css"])[1]
    assert (await dispatch(app, "theme.read", {"id": theme["id"]}))["result"] == theme
    assert app.state["theme"] == before and app.state["selectedSessionId"] == selected


async def test_ready_builtin_is_immutable_and_export_roundtrip_does_not_apply(app):
    before = deepcopy(app.state["theme"])
    selected = app.state["selectedSessionId"]
    theme = (await dispatch(app, "theme.read", {"id": "builtin:ready"}))["result"]

    for action in ("theme.update", "theme.delete"):
        args = {"id": theme["id"], "expectedFingerprint": theme["fingerprint"]}
        if action == "theme.update":
            args.update(name="Changed READY", css="#amp-one{color:red}")
        with pytest.raises(AppError):
            await dispatch(app, action, args)
    assert (await dispatch(app, "theme.read", {"id": theme["id"]}))["result"] == theme

    exported = await dispatch(app, "theme.export", {"id": theme["id"]})
    css = exported["result"]["css"]
    assert exported["effects"][0]["content"] == css
    assert exported["effects"][0]["filename"] == "breadbin.amplifier.css"
    assert theme_library.metadata(css) == theme_library.metadata(theme["css"])
    validate_theme(css)
    saved = (await dispatch(app, "theme.import", {"css": css}))["result"]
    repeated = (await dispatch(app, "theme.import", {"css": css}))["result"]
    assert saved["source"] == "saved" and repeated["id"] == saved["id"]
    assert (await dispatch(app, "theme.read", {"id": saved["id"]}))["result"]["css"] == css
    assert app.state["theme"] == before and app.state["selectedSessionId"] == selected
