"""Guard against Python-syntax regressions across all modules."""

import importlib
import pkgutil

import custom_components.engie_be as pkg


def test_all_engie_be_modules_import() -> None:
    """Every submodule must import cleanly on the runner's Python."""
    for module in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}."):
        importlib.import_module(module.name)
