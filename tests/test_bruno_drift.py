"""
Guard test for ``scripts/check-bruno-drift``.

That script is the only thing standing between the Bruno collection and the
kind of silent rot that left it covering one endpoint out of thirteen. It is
non-trivial AST and YAML parsing, it lives outside ``custom_components`` so
the coverage gate never touches it, and ruff does not discover it because it
has no ``.py`` extension. Two real false negatives have already been found in
it by hand.

So each check gets a case that must pass and a case that must fail. Follows the
same shape as ``tests/test_icons.py``: a structural guard over files rather
than over integration behaviour.
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

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "check-bruno-drift"


def _load(monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> ModuleType:
    """Import the extensionless script with its REPO pointed at *repo_root*."""
    spec = importlib.util.spec_from_loader(
        "check_bruno_drift",
        importlib.machinery.SourceFileLoader("check_bruno_drift", str(SCRIPT)),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "REPO", repo_root)
    monkeypatch.setattr(
        module, "API_PY", repo_root / "custom_components" / "engie_be" / "api.py"
    )
    monkeypatch.setattr(
        module, "CONST_PY", repo_root / "custom_components" / "engie_be" / "const.py"
    )
    monkeypatch.setattr(module, "BRUNO", repo_root / ".bruno")
    monkeypatch.setattr(module, "ENV_DIR", repo_root / ".bruno" / "environments")
    return module


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """Copy the parts of the repo the script reads into a temp tree."""
    root = tmp_path / "repo"
    (root / "custom_components").mkdir(parents=True)
    shutil.copytree(REPO / ".bruno", root / ".bruno")
    shutil.copytree(
        REPO / "custom_components" / "engie_be",
        root / "custom_components" / "engie_be",
        ignore=shutil.ignore_patterns("__pycache__", "translations", "brand"),
    )
    return root


def test_passes_on_the_real_collection() -> None:
    """The committed tree is in sync. If this fails, the collection drifted."""
    spec = importlib.util.spec_from_loader(
        "check_bruno_drift_real",
        importlib.machinery.SourceFileLoader("check_bruno_drift_real", str(SCRIPT)),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.main() == 0


def test_fails_when_an_endpoint_method_has_no_request(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a request must fail even though its folder.yml keeps the claim."""
    module = _load(monkeypatch, repo_copy)
    (repo_copy / ".bruno" / "02-token" / "refresh-token.yml").unlink()
    assert module.main() == 1


def test_fails_when_a_base_url_is_missing_from_an_environment(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host ENGIE moved must not silently stay stale in an environment."""
    module = _load(monkeypatch, repo_copy)
    env = repo_copy / ".bruno" / "environments" / "CI.yml"
    env.write_text(
        env.read_text(encoding="utf-8").replace(
            "https://api.engie.be/engie/ms/billing/customer/v1",
            "https://example.invalid/v1",
        ),
        encoding="utf-8",
    )
    assert module.main() == 1


def test_fails_when_a_query_param_is_missing_from_the_url(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bruno drops a param that is only in the array, so the check must catch it."""
    module = _load(monkeypatch, repo_copy)
    request = repo_copy / ".bruno" / "04-contracts" / "energy-contracts.yml"
    request.write_text(
        request.read_text(encoding="utf-8").replace("&includeSapData=true", "", 1),
        encoding="utf-8",
    )
    assert module.main() == 1


def test_fails_when_a_request_type_is_misspelled(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrecognised info.type must not wave the file past every other check."""
    module = _load(monkeypatch, repo_copy)
    request = repo_copy / ".bruno" / "04-contracts" / "energy-contracts.yml"
    request.write_text(
        request.read_text(encoding="utf-8").replace("  type: http", "  type: HTTP", 1),
        encoding="utf-8",
    )
    assert module.main() == 1


def test_tolerates_an_extra_scratch_environment(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A local mock environment is an ordinary thing to add and must not fail."""
    module = _load(monkeypatch, repo_copy)
    (repo_copy / ".bruno" / "environments" / "Scratch.yml").write_text(
        "name: Scratch\nvariables:\n- name: whatever\n  value: hello\n",
        encoding="utf-8",
    )
    assert module.main() == 0


def test_tolerates_a_base_url_the_client_never_calls(
    repo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only base URLs api.py imports are held to the contract."""
    module = _load(monkeypatch, repo_copy)
    const = repo_copy / "custom_components" / "engie_be" / "const.py"
    const.write_text(
        const.read_text(encoding="utf-8")
        + '\nDOCS_BASE_URL = "https://example.invalid/docs"\n',
        encoding="utf-8",
    )
    assert module.main() == 0
