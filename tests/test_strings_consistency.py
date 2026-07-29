"""
Guard test: ``strings.json`` and ``translations/en.json`` must agree.

The two files carry the same keys, and every test that existed before this
one compared only the key *sets*. That let the literals drift apart
unnoticed: a past edit inserted "hour" across the EPEX hourly strings in
``en.json`` to tell them apart from the quarter-hourly ones and never
reached ``strings.json``, leaving 21 keys with two different English
wordings. ``en.json`` is what a user reads, ``strings.json`` is what the
translation pipeline extracts, so the drift shipped a different product
name to translators than to users.

That same edit also overwrote the words distinguishing two triggers, so
``epex_current_crossed_threshold`` and ``epex_next_hour_crossed_threshold``
rendered the same sentence in the automation editor. Comparing the files
would not have caught it on its own, because a bulk edit that reaches both
files leaves them agreeing and both wrong. Hence the second test.

``strings.json`` states most values as ``[%key:component::engie_be::...%]``
references, so it is resolved before comparing. A reference that points
nowhere resolves to a sentinel and fails the comparison, which is the
behaviour we want: an unresolvable reference renders as literal
``[%key:...%]`` text in the UI.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

PACKAGE = Path(__file__).parent.parent / "custom_components" / "engie_be"
STRINGS = json.loads((PACKAGE / "strings.json").read_text(encoding="utf-8"))
EN = json.loads((PACKAGE / "translations" / "en.json").read_text(encoding="utf-8"))

KEY_REFERENCE = re.compile(r"^\[%key:component::engie_be::(.+?)%\]$")

# Condition keys that deliberately share one description. Each pair is two
# names for one behaviour: the direction of the comparison is a field the
# user fills in, not something the condition class constrains, so both
# names describe the same thing. The names still differ, which is what
# keeps them apart in the automation picker. See
# plans/192-threshold-condition-direction.md for why the keys were kept
# rather than collapsed. Adding to this list is a claim that a new pair is
# deliberate, so do not extend it to silence a genuine copy-paste slip.
ALIASED_DESCRIPTIONS = frozenset(
    {
        frozenset({"epex_price_is_below_threshold", "epex_price_is_above_threshold"}),
        frozenset(
            {
                "epex_price_is_below_threshold_quarter_hour",
                "epex_price_is_above_threshold_quarter_hour",
            }
        ),
        frozenset(
            {"solar_surplus_is_below_threshold", "solar_surplus_is_above_threshold"}
        ),
    }
)


def _lookup(dotted: str) -> Any | None:
    """Walk a ``a::b::c`` path into strings.json, or None if absent."""
    current: Any = STRINGS
    for part in dotted.split("::"):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _resolve(value: str, depth: int = 0) -> str:
    """Follow a ``[%key:%]`` reference to the literal it stands for."""
    match = KEY_REFERENCE.match(value.strip())
    if match is None:
        return value
    if depth > 10:
        return f"<REFERENCE CYCLE {value}>"
    target = _lookup(match.group(1))
    if not isinstance(target, str):
        return f"<UNRESOLVED {match.group(1)}>"
    return _resolve(target, depth + 1)


def _divergences(strings: Any, en: Any, path: str = "") -> list[tuple[str, str, str]]:
    """Return every leaf where the two files disagree, as (path, strings, en)."""
    if isinstance(strings, dict) and isinstance(en, dict):
        found: list[tuple[str, str, str]] = []
        for key in sorted(set(strings) | set(en)):
            where = f"{path}::{key}" if path else key
            if key not in strings:
                found.append((where, "<missing from strings.json>", "<present>"))
            elif key not in en:
                found.append((where, "<present>", "<missing from en.json>"))
            else:
                found.extend(_divergences(strings[key], en[key], where))
        return found
    if isinstance(strings, str) and isinstance(en, str):
        resolved = _resolve(strings)
        return [] if resolved == en else [(path, resolved, en)]
    return []


def test_strings_json_and_en_json_carry_the_same_english() -> None:
    """Every resolved strings.json literal must match its en.json twin."""
    divergences = _divergences(STRINGS, EN)
    report = "\n".join(
        f"  {where}\n    strings.json: {left}\n    en.json     : {right}"
        for where, left, right in divergences
    )
    assert not divergences, (
        f"{len(divergences)} keys word the same thing differently:\n{report}"
    )


def test_no_two_triggers_or_conditions_share_a_description() -> None:
    """
    Two entries with one description are indistinguishable in the editor.

    A user picking between them in the automation editor has only the name
    and the description to go on, so a shared description hides which one
    does what. Deliberate aliases are listed in ``ALIASED_DESCRIPTIONS``.
    """
    for section in ("triggers", "conditions"):
        by_description: defaultdict[str, list[str]] = defaultdict(list)
        for key, entry in EN[section].items():
            if "description" in entry:
                by_description[entry["description"]].append(key)
        shared = [
            sorted(keys)
            for keys in by_description.values()
            if len(keys) > 1 and frozenset(keys) not in ALIASED_DESCRIPTIONS
        ]
        assert not shared, (
            f"{section} sharing a description with no alias entry: {shared}"
        )


def test_no_two_triggers_or_conditions_share_a_name() -> None:
    """
    Names are the only thing separating the aliased pairs, so they must differ.

    ``ALIASED_DESCRIPTIONS`` exempts three condition pairs from the
    description rule on the grounds that their names tell them apart. This
    test is what makes that grounds true, so there is no exemption list here.
    """
    for section in ("triggers", "conditions"):
        by_name: defaultdict[str, list[str]] = defaultdict(list)
        for key, entry in EN[section].items():
            if "name" in entry:
                by_name[entry["name"]].append(key)
        shared = [sorted(keys) for keys in by_name.values() if len(keys) > 1]
        assert not shared, f"{section} sharing a name: {shared}"


def test_aliased_descriptions_list_has_no_dead_entries() -> None:
    """
    An alias entry must name real keys that really do share a description.

    A renamed or collapsed condition would leave a stale exemption behind,
    which then silently excuses a future duplicate.
    """
    conditions = EN["conditions"]
    for pair in ALIASED_DESCRIPTIONS:
        missing = sorted(key for key in pair if key not in conditions)
        assert not missing, f"alias entry names conditions that do not exist: {missing}"
        descriptions = {conditions[key]["description"] for key in pair}
        assert len(descriptions) == 1, (
            f"alias entry {sorted(pair)} no longer shares one description, "
            "so the exemption is stale and should be removed"
        )
