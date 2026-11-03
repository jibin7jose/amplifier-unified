"""The included animated appearance stays self-contained and layout-neutral."""
import base64
import re
import struct
from importlib.resources import files

import pytest
import tinycss2

from amplifier_web.service import validate_theme
from test_canvas_apps import app, dispatch


def source():
    return files("amplifier_web").joinpath("appearances/digital-rain.amplifier.css").read_text()


def test_digital_rain_is_portable_and_has_three_original_tiles():
    css = source()
    validate_theme(css)
    assert len(css.encode()) < 100_000
    tiles = re.findall(r"data:image/png;base64,([A-Za-z0-9+/=]+)", css)
    assert len(tiles) == 3
    dimensions = []
    for tile in tiles:
        data = base64.b64decode(tile, validate=True)
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        dimensions.append(struct.unpack(">II", data[16:24]))
    assert dimensions == [(512, 768), (704, 960), (896, 1152)]
    readable = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", css)
    assert not re.search(r"\b(matrix|tron|kitt)\b", readable, re.I)
    assert not re.search(r"https?://|@import|@font-face", readable)
    assert "digital-rain-drift 72s linear infinite" in css
    assert 'data-decorations="off"' in css
    assert "prefers-reduced-motion:reduce" in css
    assert "forced-colors:active" in css
    assert not re.search(r'conversation["\']?\]?\s*>\s*\*', css)


def test_landing_art_reuses_tiles_without_owning_layout_or_busy_feedback():
    css = source()
    landing = css.split("/* Landing pages use their own background", 1)[1].split("*/", 1)[1]
    assert 'background-image:var(--digital-rain-art)' in landing
    assert 'background-size:512px 768px,704px 960px,896px 1152px' in landing
    assert '.a-work-group-heading' in landing
    assert '.a-nav-chat-title' in landing
    assert '.a-workspace-label>span[data-scroll-title]' in landing
    assert ':has(.a-work-tabs button[aria-pressed="true"]:not(:first-child))' in landing
    assert 'data-decorations="off"' in landing
    assert 'prefers-reduced-motion:reduce' in landing
    assert 'forced-colors:active' in landing
    assert 'animation:none' in landing and 'background-image:none' in landing

    rules = tinycss2.parse_stylesheet(landing, skip_comments=True, skip_whitespace=True)
    def inspect(rows):
        for rule in rows:
            assert rule.type != "error"
            if rule.type == "at-rule":
                inspect(tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True))
            elif rule.type == "qualified-rule":
                selector = tinycss2.serialize(rule.prelude)
                assert '[data-part="work-browser"]' in selector
                assert not any(value in selector for value in ("aria-busy", "data-region-pending", "::before", "::after"))
                declarations = tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True)
                for declaration in declarations:
                    assert declaration.type == "declaration"
                    assert declaration.name not in {
                        "position", "display", "height", "min-height", "max-height",
                        "width", "min-width", "max-width", "overflow", "overflow-y",
                        "padding", "margin", "transform", "z-index", "pointer-events",
                    }
    inspect(rules)


def test_assistant_box_excludes_action_row_in_all_color_modes():
    css = source()
    body = ".a-message.a-assistant>.a-message-body"
    assert f"{body}{{box-sizing:border-box;background:#041009ed" in css
    assert f'#amp-one[data-theme-scheme="light"] {body}{{background:#fbfefced' in css
    assert f"#amp-one:not([data-theme-scheme]) {body}{{background:#fbfefced" in css
    assert f"@media(max-width:760px){{#amp-one {body}{{padding:12px}}" in css
    assert ".a-message.a-assistant{background:transparent!important;border:0!important" in css
    assert ".a-message.a-assistant>.a-message-actions{background:transparent;border:0;box-shadow:none}" in css


def test_digital_rain_inherits_shared_transcript_edges():
    # Omission exposes the host fallback. A theme-level none/unset reset would
    # suppress it, including the host's focus and forced-colors handling.
    assert not re.search(r"(?:-webkit-)?mask(?:-[\w-]+)?\s*:", source())


def test_sidebar_content_clears_selection_edge_without_changing_native_rows():
    selector = '#amp-one [data-part="navigation"] :is(.a-nav-chat:not(.a-nav-chat-rename),.a-workspace-row)'
    expected = {
        "box-sizing": "border-box",
        "padding-inline-start": "8px",
        "padding-inline-end": "4px",
    }
    # Share the accepted spacing, not the entire theme. The navigation scope
    # excludes landing cards; the :not() preserves native rename form padding.
    for name in ("digital-rain", "lightline"):
        css = files("amplifier_web").joinpath(f"appearances/{name}.amplifier.css").read_text()
        rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
        matches = [
            rule for rule in rules
            if rule.type == "qualified-rule" and tinycss2.serialize(rule.prelude).strip() == selector
        ]
        assert len(matches) == 1, name
        declarations = tinycss2.parse_declaration_list(
            matches[0].content, skip_comments=True, skip_whitespace=True,
        )
        assert all(item.type == "declaration" and not item.important for item in declarations)
        assert {item.name: tinycss2.serialize(item.value).strip() for item in declarations} == expected


def test_navigation_paint_does_not_target_unrelated_semantic_asides():
    rules = tinycss2.parse_stylesheet(source(), skip_comments=True, skip_whitespace=True)
    rail_rules = [
        rule for rule in rules
        if rule.type == "qualified-rule"
        and "box-shadow:inset 1px 0 var(--rain-rail)" in tinycss2.serialize(rule.content)
    ]
    assert len(rail_rules) == 1
    assert tinycss2.serialize(rail_rules[0].prelude).strip() == (
        '#amp-one :is(.a-nav-rail,[data-part="navigation"])'
    )
    # <aside> is also used for worker status, connection/update notices and
    # Canvas. Its semantic tag must not make it a navigation styling target.
    def inspect(rows):
        for rule in rows:
            if rule.type == "at-rule" and rule.content is not None:
                inspect(tinycss2.parse_rule_list(
                    rule.content, skip_comments=True, skip_whitespace=True,
                ))
            elif rule.type == "qualified-rule":
                assert not re.search(r"\baside\b", tinycss2.serialize(rule.prelude))
    inspect(rules)


def luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))


@pytest.mark.parametrize("ink,surface", [
    ("#143522", "#f8fcf9"), ("#486652", "#e8f3ec"),
    ("#ffffff", "#176c3b"), ("#143522", "#d6ebdd"),
    ("#e0f4e5", "#06100b"), ("#a0bdab", "#0b1a12"),
    ("#03200c", "#85eea2"),
])
def test_digital_rain_text_palette_contrast(ink, surface):
    css = source()
    assert ink in css and surface in css
    a, b = sorted((luminance(ink), luminance(surface)))
    assert (b + .05) / (a + .05) >= 4.5


async def test_included_digital_rain_can_be_read_saved_and_applied(app):
    initial = dict(app.state["theme"])
    listing = (await dispatch(app, "theme.list", {}))["result"]
    row = next(item for item in listing["items"] if item["id"] == "builtin:digital-rain")
    assert row["name"] == "Digital Rain" and row["source"] == "built-in"
    assert "css" not in row
    theme = (await dispatch(app, "theme.read", {"id": row["id"]}))["result"]
    assert theme["css"] == source()
    validate_theme(theme["css"])
    saved = (await dispatch(app, "theme.save", {
        "name": "My rain", "css": theme["css"],
    }))["result"]
    assert saved["id"].startswith("saved:")
    await dispatch(app, "theme.preview", {"name": theme["name"], "css": theme["css"]})
    assert app.state["theme"] == initial
    await dispatch(app, "theme.revert", {})
    await dispatch(app, "theme.apply", {"name": theme["name"], "css": theme["css"]})
    assert app.state["theme"]["name"] == "Digital Rain"
    assert app.state["theme"]["css"] == theme["css"]