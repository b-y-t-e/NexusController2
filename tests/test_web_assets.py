"""Checks on the dashboard's own files.

There is no JavaScript test runner in this project and there is not going to be
one for the sake of four files — but a stray brace in the stylesheet took out
half the dashboard, and nothing in 660-odd tests or in Android lint could have
noticed. These are the cheap, mechanical facts about those files that a person
reviewing a diff cannot reliably check by eye.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "server" / "nexus_server" / "web"


def strip_comments(text: str) -> str:
    """Remove ``/* … */`` so braces inside prose do not count."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


class TestStylesheet:
    def test_braces_are_balanced(self):
        """An unmatched ``}`` does not fail loudly — it eats the next rule.

        That is exactly what happened: a brace left behind by an edit made the
        parser discard ``.graph canvas``, the canvas lost the height that was
        pinning it, and on any display not at 100% scaling it grew by a factor of
        devicePixelRatio on every redraw until the compositor gave up and drew
        the broken-image placeholder over the page.
        """
        text = strip_comments((WEB / "style.css").read_text(encoding="utf-8"))
        depth = 0
        for number, line in enumerate(text.splitlines(), start=1):
            for char in line:
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth < 0:
                        pytest.fail(f"style.css line {number}: '}}' closes nothing")
        assert depth == 0, f"style.css ends inside {depth} unclosed block(s)"

    def test_the_chart_canvas_has_a_pinned_height(self):
        """The one declaration whose absence is a runaway rather than a wobble."""
        text = strip_comments((WEB / "style.css").read_text(encoding="utf-8"))
        rule = re.search(r"\.graph\s+canvas\s*\{([^}]*)\}", text)
        assert rule is not None, "no `.graph canvas` rule at all"
        assert re.search(r"height:\s*\d+px", rule.group(1)), (
            "`.graph canvas` must pin a pixel height; without it the element's "
            "size follows its width/height attributes, which drawChart() then "
            "recomputes from that size"
        )

    def test_grid_children_cannot_be_pushed_wider_than_their_track(self):
        text = strip_comments((WEB / "style.css").read_text(encoding="utf-8"))
        assert "min-width: 0" in text, (
            "grid items default to min-content width, so one oversized child "
            "drags the whole layout past the window edge"
        )


class TestScriptsAndMarkup:
    def test_every_element_the_dashboard_reaches_for_exists(self):
        """`$('typo')` returns null and the page dies at the first property access.

        Cheap to check and impossible to see in a diff that renames an id in the
        markup but not in the four places the script asks for it.
        """
        markup = (WEB / "index.html").read_text(encoding="utf-8")
        present = set(re.findall(r'id="([^"]+)"', markup))
        # Ids the script creates itself, per slot: `mini-0`, `pname-3`, …
        generated = re.compile(r"^[a-z]+-(?:'|\" \+|$)")

        missing: list[str] = []
        for script in ("app.js", "designer.js"):
            source = (WEB / script).read_text(encoding="utf-8")
            for wanted in re.findall(r"\$\('([^']+)'\)", source):
                if wanted not in present and not generated.match(wanted):
                    missing.append(f"{script}: {wanted}")
        assert not missing, f"ids used by the scripts but absent from index.html: {missing}"

    def test_nothing_is_loaded_from_the_network(self):
        """The dashboard has to work with the cable unplugged (CLAUDE.md)."""
        for name in ("index.html", "app.js", "designer.js", "pad.js", "style.css"):
            text = (WEB / name).read_text(encoding="utf-8")
            for pattern in ("http://", "https://", "//cdn.", "@import url("):
                assert pattern not in text, f"{name} reaches for {pattern}"
