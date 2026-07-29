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
