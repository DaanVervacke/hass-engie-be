"""Tests for data.py helper utilities."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.engie_be.data import unwrap_dict_payload


def _coordinator(data: object) -> MagicMock:
    """Build a MagicMock coordinator with the given ``.data``."""
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def test_unwrap_dict_payload_none_when_coordinator_data_is_none() -> None:
    """A coordinator with no data yet returns None."""
    assert unwrap_dict_payload(_coordinator(None), "peaks") is None


def test_unwrap_dict_payload_none_when_coordinator_data_not_a_dict() -> None:
    """A non-dict top-level container (e.g. a list) returns None."""
    assert unwrap_dict_payload(_coordinator(["not", "a", "dict"]), "peaks") is None


def test_unwrap_dict_payload_none_when_key_missing() -> None:
    """A dict without the requested key returns None."""
    assert unwrap_dict_payload(_coordinator({"other": {}}), "peaks") is None


def test_unwrap_dict_payload_none_when_wrapper_not_a_dict() -> None:
    """A wrapper value under the key that is not a dict returns None."""
    coordinator = _coordinator({"peaks": "not a dict"})
    assert unwrap_dict_payload(coordinator, "peaks") is None


def test_unwrap_dict_payload_none_when_inner_data_missing() -> None:
    """A wrapper dict without an inner 'data' key returns None."""
    coordinator = _coordinator({"peaks": {"fetched_at": "2026-07-15T10:00:00+00:00"}})
    assert unwrap_dict_payload(coordinator, "peaks") is None


def test_unwrap_dict_payload_none_when_inner_data_not_a_dict() -> None:
    """A wrapper whose inner 'data' value is not a dict returns None."""
    coordinator = _coordinator({"peaks": {"data": ["not", "a", "dict"]}})
    assert unwrap_dict_payload(coordinator, "peaks") is None


def test_unwrap_dict_payload_returns_inner_dict_on_happy_path() -> None:
    """A well-formed wrapper returns the inner 'data' dict."""
    inner = {"value": 42}
    coordinator = _coordinator({"peaks": {"data": inner}})
    assert unwrap_dict_payload(coordinator, "peaks") is inner
