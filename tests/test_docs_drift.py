"""
Tests for ``scripts/check-docs-drift``.

The script has no ``.py`` extension, so it is loaded by path the same way
``tests/test_bruno_drift.py`` loads its own subject.

The important test here is the last one. Stripping code spans before the
terminology rules run is what keeps ``solar_surplus`` as an identifier
from being reported as a misspelling of the words "Solar Surplus", and a
checker that fires on correct text gets switched off.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-docs-drift"


def _load(monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> ModuleType:
    """Import the extension-less script with REPO pointed at a copy."""
    loader = importlib.machinery.SourceFileLoader("check_docs_drift", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, "REPO", repo_root)
    monkeypatch.setattr(module, "PACKAGE", repo_root / "custom_components" / "engie_be")
    return module


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """Build a throwaway copy of the parts of the repo the script reads."""
    root = tmp_path / "repo"
    (root / "custom_components").mkdir(parents=True)
    shutil.copytree(
        REPO_ROOT / "custom_components" / "engie_be",
        root / "custom_components" / "engie_be",
    )
    return root


def test_passes_on_the_real_tree() -> None:
    """The repository as committed has no documentation drift."""
    loader = importlib.machinery.SourceFileLoader("check_docs_drift", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    assert module.main([]) == 0


def test_fails_on_a_reference_that_resolves_nowhere(
    monkeypatch: pytest.MonkeyPatch, repo_copy: Path
) -> None:
    """A docstring naming a symbol that does not exist is reported."""
    module = _load(monkeypatch, repo_copy)
    target = repo_copy / "custom_components" / "engie_be" / "_probe.py"
    target.write_text(
        '"""Probe module.\n\nMentions ``NoSuchSymbolAnywhereAtAll`` on purpose.\n"""\n',
        encoding="utf-8",
    )
    assert module.main(["custom_components/engie_be"]) == 1


def test_resolves_a_translation_key_named_in_en_json(
    monkeypatch: pytest.MonkeyPatch, repo_copy: Path
) -> None:
    """
    A docstring may name a trigger key without it reading as a broken symbol.

    The key is a value defined in en.json, not a Python name, so hand-copying
    it into KNOWN_VALUES was the old workaround. The paired assertion is the
    load-bearing half: a key that is NOT in en.json must still be reported,
    otherwise this rule would resolve any lowercase word at all.
    """
    module = _load(monkeypatch, repo_copy)
    target = repo_copy / "custom_components" / "engie_be" / "_probe.py"
    target.write_text(
        '"""Probe module.\n\nMentions ``epex_became_negative`` on purpose.\n"""\n',
        encoding="utf-8",
    )
    assert module.main(["custom_components/engie_be"]) == 0

    target.write_text(
        '"""Probe module.\n\nMentions ``epex_became_fictional`` on purpose.\n"""\n',
        encoding="utf-8",
    )
    assert module.main(["custom_components/engie_be"]) == 1


def test_fails_on_lowercase_solar_surplus_in_prose(
    monkeypatch: pytest.MonkeyPatch, repo_copy: Path
) -> None:
    """The product name is capitalised, so prose must be too."""
    module = _load(monkeypatch, repo_copy)
    target = repo_copy / "custom_components" / "engie_be" / "_probe.py"
    target.write_text(
        '"""Probe module.\n\nThe solar surplus forecast is fetched per EAN.\n"""\n',
        encoding="utf-8",
    )
    assert module.main(["custom_components/engie_be"]) == 1


def test_fails_on_a_line_number_reference(
    monkeypatch: pytest.MonkeyPatch, repo_copy: Path
) -> None:
    """Line numbers go stale silently, so they are rejected outright."""
    module = _load(monkeypatch, repo_copy)
    target = repo_copy / "custom_components" / "engie_be" / "_probe.py"
    target.write_text(
        '"""Probe module.\n\nSee sensor.py:12 for the mapping.\n"""\n',
        encoding="utf-8",
    )
    assert module.main(["custom_components/engie_be"]) == 1


def test_fails_on_a_private_name_that_resolves_only_in_ha_core(
    monkeypatch: pytest.MonkeyPatch, repo_copy: Path
) -> None:
    """
    A private name found nowhere locally, only in core, is reported.

    Core is broad enough (roughly 29,600 names) that a private name can
    collide with it by accident. `homeassistant_symbols` is stubbed to
    add one fake private name on top of the real scan, so the case does
    not depend on which private names the installed HA version happens
    to define, while genuine core citations elsewhere in the package
    still resolve.
    """
    module = _load(monkeypatch, repo_copy)
    real_homeassistant_symbols = module.homeassistant_symbols
    monkeypatch.setattr(
        module,
        "homeassistant_symbols",
        lambda: real_homeassistant_symbols() | {"_totally_fake_core_only_name"},
    )
    target = repo_copy / "custom_components" / "engie_be" / "_probe.py"
    target.write_text(
        '"""Probe module.\n\n'
        "Mentions ``_totally_fake_core_only_name`` on purpose.\n"
        '"""\n',
        encoding="utf-8",
    )
    assert module.main(["custom_components/engie_be"]) == 1


def test_passes_on_a_private_name_listed_in_ha_internals(
    monkeypatch: pytest.MonkeyPatch, repo_copy: Path
) -> None:
    """
    A private core-only name is not reported once it is in HA_INTERNALS.

    Adding an entry there is a claim that the name really is an HA
    internal worth citing, such as `_async_process_on_unload`, rather
    than a stale local reference that got lucky.
    """
    module = _load(monkeypatch, repo_copy)
    real_homeassistant_symbols = module.homeassistant_symbols
    monkeypatch.setattr(
        module,
        "homeassistant_symbols",
        lambda: real_homeassistant_symbols() | {"_totally_fake_core_only_name"},
    )
    monkeypatch.setattr(
        module, "HA_INTERNALS", module.HA_INTERNALS | {"_totally_fake_core_only_name"}
    )
    target = repo_copy / "custom_components" / "engie_be" / "_probe.py"
    target.write_text(
        '"""Probe module.\n\n'
        "Mentions ``_totally_fake_core_only_name`` on purpose.\n"
        '"""\n',
        encoding="utf-8",
    )
    assert module.main(["custom_components/engie_be"]) == 0


def test_identifier_in_a_code_span_is_not_a_terminology_error(
    monkeypatch: pytest.MonkeyPatch, repo_copy: Path
) -> None:
    """
    A quoted value must not read as bad prose.

    A lowercase ``solar surplus`` inside a code span is quoting a
    literal, not writing the product name, so the rule must not fire.
    This is the false positive most likely to get the whole check
    disabled, so it is guarded explicitly.
    """
    module = _load(monkeypatch, repo_copy)
    target = repo_copy / "custom_components" / "engie_be" / "_probe.py"
    target.write_text(
        '"""Probe module.\n\nThe ``solar surplus`` wire label is lowercase.\n"""\n',
        encoding="utf-8",
    )
    assert module.main(["custom_components/engie_be"]) == 0
