"""
Regression tests for the Auth0 step-9 branching introduced in v0.10.0b3.

After MFA acceptance, Auth0's GET ``/authorize/resume`` has two
possible outcomes per session/account:

A. **Callback short-circuit.** Auth0 redirects directly to the native
   callback URI ``be.engie.smart://login-callback/nl?code=...&state=...``.
   The auth code is in the ``Location`` header; steps 10-12 must be
   skipped (a second ``/authorize/resume`` would 302 to
   ``error=access_denied`` because the session was already consumed).

B. **Passkey-enrollment interstitial.** Auth0 redirects to
   ``/u/passkey-enrollment?state=<passKeyState>``. The integration
   loads (step 10), aborts (step 11), and re-resumes (step 12).

Pre-v0.10.0b3 always took the body's ``state=`` value as if it were a
``passKeyState``, which for outcome A returned the OAuth ``state``
nonce from step 1 and produced an ``access_denied`` at step 12. These
tests lock both branches.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.engie_be.api import (
    REDIRECT_URI,
    AuthFlowState,
    EngieBeApiClient,
    EngieBeApiClientAuthenticationError,
)
from custom_components.engie_be.const import MFA_METHOD_SMS

if TYPE_CHECKING:
    from collections.abc import Sequence


# A representative auth code and state pair from a real ENGIE callback URI.
_AUTH_CODE = "pq0tnfXpRtYTw3HHqbtNRs38BWRZYEVPmI76MZdBfDiZe"
_OAUTH_STATE = "c1bcf471a3a27c7223374111e9634104"
_LOGIN_STATE = "gJ4fp_FAQ4AgtEnaVeSwmoU24ULQs0oQ"
_PASSKEY_STATE = "AbcdEfgh1234567890PassKeyStateXYZ"


def _make_client() -> EngieBeApiClient:
    """Build a client with the minimum constructor surface."""
    return EngieBeApiClient(
        session=MagicMock(),
        client_id="client-1",
        refresh_token="v0.original",  # noqa: S106
    )


def _flow_state() -> AuthFlowState:
    return AuthFlowState(
        session=MagicMock(),
        authorize_state=_OAUTH_STATE,
        login_state=_LOGIN_STATE,
        mfa_challenge_state="mfa-state",
        code_verifier="verifier-xyz",
    )


def _seq_wrapper(responses: Sequence[Any]) -> AsyncMock:
    """Build an ``_api_wrapper`` stub that returns *responses* in order."""
    return AsyncMock(side_effect=list(responses))


# ---------------------------------------------------------------------------
# Outcome A: Auth0 redirects directly to the native callback URI.
# ---------------------------------------------------------------------------


async def test_step9_callback_short_circuit_skips_passkey_enrollment() -> None:
    """When step 9 Location is the callback URI, steps 10-12 are skipped."""
    client = _make_client()
    callback_location = f"{REDIRECT_URI}?code={_AUTH_CODE}&state={_OAUTH_STATE}"

    # Sequence:
    #   1. step 8: _submit_sms_mfa  -> returns HTML body containing ``state=``
    #   2. step 9: GET /authorize/resume (include_headers=True)
    #      -> (body, {"Location": <callback>})
    #   3. step 13: POST /oauth/token -> tokens dict
    wrapper = _seq_wrapper(
        [
            (
                "<form>step9 body irrelevant in outcome A</form>",
                {"Location": callback_location},
            ),
            {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
            },
        ]
    )

    submit_sms = AsyncMock(
        return_value=f"<html><a href='/x?state={_OAUTH_STATE}'>x</a></html>"
    )

    client._api_wrapper = wrapper  # type: ignore[method-assign]
    client._submit_sms_mfa = submit_sms  # type: ignore[method-assign]

    access, refresh = await client._run_auth_steps_8_to_13(
        _flow_state(),
        mfa_code="123456",
        mfa_method=MFA_METHOD_SMS,
    )

    assert access == "new-access"
    assert refresh == "new-refresh"

    # Exactly two _api_wrapper calls: step 9 + step 13. Steps 10/11/12
    # MUST have been skipped.
    assert wrapper.await_count == 2

    step9_call = wrapper.await_args_list[0]
    assert step9_call.kwargs["url"].endswith("/authorize/resume")
    assert step9_call.kwargs["include_headers"] is True
    assert step9_call.kwargs["params"] == {"state": _LOGIN_STATE}

    step13_call = wrapper.await_args_list[1]
    assert step13_call.kwargs["url"].endswith("/oauth/token")
    assert step13_call.kwargs["data"]["code"] == _AUTH_CODE


async def test_step9_callback_location_without_code_raises() -> None:
    """Defensive: callback URI without a ``code`` parameter is an auth error."""
    client = _make_client()
    wrapper = _seq_wrapper(
        [
            (
                "<html>no code in body</html>",
                {"Location": f"{REDIRECT_URI}?state={_OAUTH_STATE}"},
            ),
        ]
    )
    client._api_wrapper = wrapper  # type: ignore[method-assign]
    client._submit_sms_mfa = AsyncMock(  # type: ignore[method-assign]
        return_value=f"<html>state={_OAUTH_STATE}</html>"
    )

    with pytest.raises(EngieBeApiClientAuthenticationError):
        await client._run_auth_steps_8_to_13(
            _flow_state(),
            mfa_code="123456",
            mfa_method=MFA_METHOD_SMS,
        )

    # Only the step-9 call should have happened; we must not fall
    # through to the passkey-enrollment path on a malformed callback.
    assert wrapper.await_count == 1


# ---------------------------------------------------------------------------
# Outcome B: Auth0 redirects to the passkey-enrollment interstitial.
# ---------------------------------------------------------------------------


async def test_step9_passkey_enrollment_path_runs_steps_10_to_12() -> None:
    """When step 9 does not redirect to the callback, steps 10-12 run."""
    client = _make_client()

    # Step 9 body contains the passKeyState; Location points to the
    # passkey-enrollment page.
    step9_body = (
        f"<html><form action='/u/passkey-enrollment?state={_PASSKEY_STATE}'>"
        "</form></html>"
    )
    step9_location = (
        f"https://account.engie.be/u/passkey-enrollment?state={_PASSKEY_STATE}"
    )
    # Step 12 returns the auth code in the body.
    step12_body = (
        f"<html><a href='{REDIRECT_URI}?code={_AUTH_CODE}&state={_OAUTH_STATE}'>"
        "continue</a></html>"
    )

    wrapper = _seq_wrapper(
        [
            # Step 9
            (step9_body, {"Location": step9_location}),
            # Step 10
            "<html>passkey page</html>",
            # Step 11
            "<html>aborted</html>",
            # Step 12
            (step12_body, {"Location": ""}),
            # Step 13
            {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
            },
        ]
    )

    submit_sms = AsyncMock(
        return_value=f"<html><a href='/x?state={_OAUTH_STATE}'>x</a></html>"
    )

    client._api_wrapper = wrapper  # type: ignore[method-assign]
    client._submit_sms_mfa = submit_sms  # type: ignore[method-assign]

    access, refresh = await client._run_auth_steps_8_to_13(
        _flow_state(),
        mfa_code="123456",
        mfa_method=MFA_METHOD_SMS,
    )

    assert access == "new-access"
    assert refresh == "new-refresh"

    # Five _api_wrapper calls: 9, 10, 11, 12, 13.
    assert wrapper.await_count == 5

    urls = [call.kwargs["url"] for call in wrapper.await_args_list]
    assert urls[0].endswith("/authorize/resume")
    assert urls[1].endswith("/u/passkey-enrollment")
    assert urls[2].endswith("/u/passkey-enrollment")
    assert urls[3].endswith("/authorize/resume")
    assert urls[4].endswith("/oauth/token")

    # Step 11 uses passKeyState (not loginState) in the form data.
    step11_data = wrapper.await_args_list[2].kwargs["data"]
    assert step11_data == {
        "state": _PASSKEY_STATE,
        "action": "abort-passkey-enrollment",
    }

    # Step 12 re-uses the original loginState.
    assert wrapper.await_args_list[3].kwargs["params"] == {"state": _LOGIN_STATE}

    # Step 13 receives the auth code.
    assert wrapper.await_args_list[4].kwargs["data"]["code"] == _AUTH_CODE


async def test_step9_passkey_path_step12_code_via_location_header() -> None:
    """Outcome B variant: step 12 returns code only in the Location header."""
    client = _make_client()
    step9_body = f"<html>state={_PASSKEY_STATE}</html>"
    step9_location = (
        f"https://account.engie.be/u/passkey-enrollment?state={_PASSKEY_STATE}"
    )
    callback = f"{REDIRECT_URI}?code={_AUTH_CODE}&state={_OAUTH_STATE}"

    wrapper = _seq_wrapper(
        [
            (step9_body, {"Location": step9_location}),
            "<html>passkey</html>",
            "<html>aborted</html>",
            ("<html>no code in body</html>", {"Location": callback}),
            {"access_token": "new-access", "refresh_token": "new-refresh"},
        ]
    )

    client._api_wrapper = wrapper  # type: ignore[method-assign]
    client._submit_sms_mfa = AsyncMock(  # type: ignore[method-assign]
        return_value=f"<html>state={_OAUTH_STATE}</html>"
    )

    access, _ = await client._run_auth_steps_8_to_13(
        _flow_state(),
        mfa_code="123456",
        mfa_method=MFA_METHOD_SMS,
    )

    assert access == "new-access"
    assert wrapper.await_args_list[4].kwargs["data"]["code"] == _AUTH_CODE


async def test_step9_passkey_path_missing_state_raises() -> None:
    """Defensive: outcome B with no extractable passKeyState is an auth error."""
    client = _make_client()
    wrapper = _seq_wrapper(
        [
            (
                "<html>no state here</html>",
                {"Location": "https://account.engie.be/u/passkey-enrollment"},
            ),
        ]
    )
    client._api_wrapper = wrapper  # type: ignore[method-assign]
    client._submit_sms_mfa = AsyncMock(  # type: ignore[method-assign]
        return_value=f"<html>state={_OAUTH_STATE}</html>"
    )

    with pytest.raises(EngieBeApiClientAuthenticationError):
        await client._run_auth_steps_8_to_13(
            _flow_state(),
            mfa_code="123456",
            mfa_method=MFA_METHOD_SMS,
        )

    # Only step 9 ran; steps 10/11/12 must not be attempted.
    assert wrapper.await_count == 1
