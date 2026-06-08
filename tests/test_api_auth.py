"""Unit tests for the ENGIE Auth0 login flow (steps 1-7, 8-13, orchestration)."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.engie_be.api import (
    REDIRECT_URI,
    AuthFlowState,
    EngieBeApiClient,
    EngieBeApiClientAuthenticationError,
    EngieBeApiClientCommunicationError,
    EngieBeApiClientMfaError,
    _base64url,
    _generate_pkce,
)
from custom_components.engie_be.const import MFA_METHOD_EMAIL, MFA_METHOD_SMS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Self

# Representative auth code / state values from a real ENGIE callback URI.
_AUTH_CODE = "pq0tnfXpRtYTw3HHqbtNRs38BWRZYEVPmI76MZdBfDiZe"
_OAUTH_STATE = "c1bcf471a3a27c7223374111e9634104"
_LOGIN_STATE = "gJ4fp_FAQ4AgtEnaVeSwmoU24ULQs0oQ"
_PASSKEY_STATE = "AbcdEfgh1234567890PassKeyStateXYZ"

_USERNAME = "user@example.com"
_PASSWORD = "hunter2"  # noqa: S105


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


def _body_with_state(state: str) -> str:
    """Return an HTML body carrying an extractable ``state=`` value."""
    return f"<html><form action='/u/x?state={state}'></form></html>"


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def test_base64url_is_urlsafe_and_unpadded() -> None:
    """``_base64url`` emits URL-safe characters and strips ``=`` padding."""
    # These bytes encode to the URL-safe-only glyphs ``-`` and ``_``.
    assert _base64url(b"\xfb\xff\xfe") == "-__-"
    # A single byte would normally pad with ``==`` in standard base64.
    assert "=" not in _base64url(b"\x00")


def test_generate_pkce_returns_distinct_well_formed_values() -> None:
    """PKCE tuple is (state, nonce, verifier, challenge) with sane shapes."""
    state, nonce, verifier, challenge = _generate_pkce()

    # state/nonce are 16 random bytes rendered as 32 hex chars.
    assert len(state) == 32
    assert len(nonce) == 32
    assert state != nonce
    int(state, 16)  # must be valid hexadecimal
    int(nonce, 16)

    # verifier is base64url(32 bytes) -> 43 unpadded chars.
    assert len(verifier) == 43
    assert "=" not in verifier

    # challenge is the base64url SHA-256 of the verifier.
    expected_challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    assert challenge == expected_challenge

    # Successive calls produce fresh randomness.
    assert _generate_pkce()[0] != state


# ---------------------------------------------------------------------------
# async_refresh_token edge branches
# ---------------------------------------------------------------------------


class _MutatingLock:
    """
    Async-lock stand-in that rotates the client's tokens on enter.

    Simulates a racing caller that already rotated the refresh-token pair
    while this caller was queued on the real ``asyncio.Lock``.
    """

    def __init__(self, client: EngieBeApiClient) -> None:
        self._client = client

    async def __aenter__(self) -> Self:
        """Rotate the client tokens, mimicking the racing caller."""
        self._client.access_token = "racer-access"  # noqa: S105
        self._client.refresh_token = "racer-refresh"  # noqa: S105
        return self

    async def __aexit__(self, *_args: object) -> bool:
        """Do not suppress exceptions from the guarded block."""
        return False


async def test_refresh_token_without_token_raises() -> None:
    """Refreshing with no refresh token is an authentication error."""
    client = EngieBeApiClient(session=MagicMock(), client_id="client-1")
    with pytest.raises(EngieBeApiClientAuthenticationError):
        await client.async_refresh_token()


async def test_refresh_token_returns_fresh_pair_when_racing_caller_rotated() -> None:
    """A racing rotation short-circuits without re-issuing the refresh request."""
    client = _make_client()
    wrapper = AsyncMock()
    client._api_wrapper = wrapper  # type: ignore[method-assign]
    client._token_lock = _MutatingLock(client)  # type: ignore[assignment]

    access, refresh = await client.async_refresh_token()

    assert access == "racer-access"
    assert refresh == "racer-refresh"
    wrapper.assert_not_awaited()


# ---------------------------------------------------------------------------
# async_start_authentication (steps 1-7 orchestration + session lifecycle)
# ---------------------------------------------------------------------------


async def test_start_authentication_returns_flow_state_on_success() -> None:
    """A successful start returns the flow state and leaves the session open."""
    client = _make_client()
    flow = _flow_state()
    client._run_auth_steps_1_to_7 = AsyncMock(return_value=flow)  # type: ignore[method-assign]
    fake_session = MagicMock()
    fake_session.close = AsyncMock()

    with patch(
        "custom_components.engie_be.api.aiohttp.ClientSession",
        return_value=fake_session,
    ):
        result = await client.async_start_authentication(_USERNAME, _PASSWORD)

    assert result is flow
    fake_session.close.assert_not_awaited()
    # The freshly created session is threaded into steps 1-7.
    assert client._run_auth_steps_1_to_7.await_args.args[0] is fake_session


async def test_start_authentication_closes_session_on_failure() -> None:
    """A failure inside steps 1-7 closes the temporary session and re-raises."""
    client = _make_client()
    client._run_auth_steps_1_to_7 = AsyncMock(  # type: ignore[method-assign]
        side_effect=EngieBeApiClientAuthenticationError("boom"),
    )
    fake_session = MagicMock()
    fake_session.close = AsyncMock()

    with (
        patch(
            "custom_components.engie_be.api.aiohttp.ClientSession",
            return_value=fake_session,
        ),
        pytest.raises(EngieBeApiClientAuthenticationError),
    ):
        await client.async_start_authentication(_USERNAME, _PASSWORD)

    fake_session.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# async_complete_authentication (steps 8-13 orchestration + session lifecycle)
# ---------------------------------------------------------------------------


async def test_complete_authentication_success_sets_tokens_and_closes() -> None:
    """Success stores both tokens and closes the temporary session."""
    client = _make_client()
    flow = _flow_state()
    flow.session.close = AsyncMock()
    client._run_auth_steps_8_to_13 = AsyncMock(  # type: ignore[method-assign]
        return_value=("acc", "ref"),
    )

    access, refresh = await client.async_complete_authentication(flow, "123456")

    assert (access, refresh) == ("acc", "ref")
    assert client.access_token == "acc"  # noqa: S105
    assert client.refresh_token == "ref"  # noqa: S105
    flow.session.close.assert_awaited_once()


async def test_complete_authentication_mfa_error_keeps_session_open() -> None:
    """An MFA error keeps the session open so the user can retry the code."""
    client = _make_client()
    flow = _flow_state()
    flow.session.close = AsyncMock()
    client._run_auth_steps_8_to_13 = AsyncMock(  # type: ignore[method-assign]
        side_effect=EngieBeApiClientMfaError("bad code"),
    )

    with pytest.raises(EngieBeApiClientMfaError):
        await client.async_complete_authentication(flow, "000000")

    flow.session.close.assert_not_awaited()


async def test_complete_authentication_other_error_closes_session() -> None:
    """A non-MFA error closes the session before re-raising."""
    client = _make_client()
    flow = _flow_state()
    flow.session.close = AsyncMock()
    client._run_auth_steps_8_to_13 = AsyncMock(  # type: ignore[method-assign]
        side_effect=EngieBeApiClientCommunicationError("net"),
    )

    with pytest.raises(EngieBeApiClientCommunicationError):
        await client.async_complete_authentication(flow, "123456")

    flow.session.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# _run_auth_steps_1_to_7
# ---------------------------------------------------------------------------


async def test_run_auth_steps_1_to_7_sms_happy_path() -> None:
    """SMS flow extracts all three states and fires the SMS challenge."""
    client = _make_client()
    session = MagicMock()
    wrapper = _seq_wrapper(
        [
            _body_with_state("authstate1"),  # step 1: /authorize
            "",  # step 2: GET identifier
            "",  # step 3: POST identifier
            "",  # step 4: GET password
            _body_with_state("loginstate2"),  # step 5: POST password
            _body_with_state("mfastate3"),  # step 6: /authorize/resume
            "",  # step 7: GET /u/mfa-sms-challenge
        ]
    )
    client._api_wrapper = wrapper  # type: ignore[method-assign]

    flow = await client._run_auth_steps_1_to_7(
        session, _USERNAME, _PASSWORD, MFA_METHOD_SMS
    )

    assert flow.session is session
    assert flow.authorize_state == "authstate1"
    assert flow.login_state == "loginstate2"
    assert flow.mfa_challenge_state == "mfastate3"
    assert flow.code_verifier  # populated by _generate_pkce
    assert wrapper.await_count == 7

    urls = [c.kwargs["url"] for c in wrapper.await_args_list]
    assert urls[0].endswith("/authorize")
    assert urls[6].endswith("/u/mfa-sms-challenge")
    assert wrapper.await_args_list[6].kwargs["method"] == "GET"


async def test_run_auth_steps_1_to_7_email_runs_switch_detour() -> None:
    """Email flow skips the SMS GET and runs the ALT authenticator switch."""
    client = _make_client()
    session = MagicMock()
    wrapper = _seq_wrapper(
        [
            _body_with_state("authstate1"),  # step 1
            "",  # step 2
            "",  # step 3
            "",  # step 4
            _body_with_state("loginstate2"),  # step 5
            _body_with_state("mfastate3"),  # step 6
            "",  # ALT-1: POST /u/mfa-sms-challenge
            "",  # ALT-2: GET /u/mfa-login-options
            "",  # ALT-3: POST /u/mfa-login-options
            "",  # ALT-4: GET /u/mfa-email-challenge
        ]
    )
    client._api_wrapper = wrapper  # type: ignore[method-assign]

    flow = await client._run_auth_steps_1_to_7(
        session, _USERNAME, _PASSWORD, MFA_METHOD_EMAIL
    )

    assert flow.mfa_challenge_state == "mfastate3"
    assert wrapper.await_count == 10

    urls = [c.kwargs["url"] for c in wrapper.await_args_list]
    methods = [c.kwargs["method"] for c in wrapper.await_args_list]
    # ALT detour endpoints, in order.
    assert urls[6].endswith("/u/mfa-sms-challenge")
    assert methods[6] == "POST"
    assert urls[7].endswith("/u/mfa-login-options")
    assert urls[8].endswith("/u/mfa-login-options")
    assert urls[9].endswith("/u/mfa-email-challenge")
    # The SMS path's step-7 GET must NOT happen on the email flow.
    assert not any(
        c.kwargs["url"].endswith("/u/mfa-sms-challenge") and c.kwargs["method"] == "GET"
        for c in wrapper.await_args_list
    )


async def test_run_auth_steps_1_to_7_missing_authorize_state_raises() -> None:
    """A step-1 body with no extractable state aborts immediately."""
    client = _make_client()
    wrapper = _seq_wrapper(["<html>no state</html>"])
    client._api_wrapper = wrapper  # type: ignore[method-assign]

    with pytest.raises(EngieBeApiClientAuthenticationError):
        await client._run_auth_steps_1_to_7(
            MagicMock(), _USERNAME, _PASSWORD, MFA_METHOD_SMS
        )

    assert wrapper.await_count == 1


async def test_run_auth_steps_1_to_7_missing_login_state_raises() -> None:
    """A step-5 body with no state is treated as bad credentials."""
    client = _make_client()
    wrapper = _seq_wrapper(
        [
            _body_with_state("authstate1"),
            "",
            "",
            "",
            "<html>bad credentials, no state</html>",  # step 5
        ]
    )
    client._api_wrapper = wrapper  # type: ignore[method-assign]

    with pytest.raises(EngieBeApiClientAuthenticationError):
        await client._run_auth_steps_1_to_7(
            MagicMock(), _USERNAME, _PASSWORD, MFA_METHOD_SMS
        )

    assert wrapper.await_count == 5


async def test_run_auth_steps_1_to_7_missing_mfa_state_raises() -> None:
    """A step-6 resume body with no state aborts before any MFA trigger."""
    client = _make_client()
    wrapper = _seq_wrapper(
        [
            _body_with_state("authstate1"),
            "",
            "",
            "",
            _body_with_state("loginstate2"),
            "<html>no mfa challenge state</html>",  # step 6
        ]
    )
    client._api_wrapper = wrapper  # type: ignore[method-assign]

    with pytest.raises(EngieBeApiClientAuthenticationError):
        await client._run_auth_steps_1_to_7(
            MagicMock(), _USERNAME, _PASSWORD, MFA_METHOD_SMS
        )

    assert wrapper.await_count == 6


# ---------------------------------------------------------------------------
# _run_auth_steps_8_to_13 (real submit helpers + remaining edges)
# ---------------------------------------------------------------------------


async def test_run_auth_steps_8_to_13_sms_real_submit_outcome_a() -> None:
    """Real SMS submit + callback short-circuit yields tokens in 3 calls."""
    client = _make_client()
    callback = f"{REDIRECT_URI}?code={_AUTH_CODE}&state={_OAUTH_STATE}"
    wrapper = _seq_wrapper(
        [
            _body_with_state("postmfastate"),  # step 8: _submit_sms_mfa
            ("<form>irrelevant</form>", {"Location": callback}),  # step 9
            {"access_token": "a", "refresh_token": "r"},  # step 13
        ]
    )
    client._api_wrapper = wrapper  # type: ignore[method-assign]

    access, refresh = await client._run_auth_steps_8_to_13(
        _flow_state(), mfa_code="123456", mfa_method=MFA_METHOD_SMS
    )

    assert (access, refresh) == ("a", "r")
    assert wrapper.await_count == 3
    submit_call = wrapper.await_args_list[0]
    assert submit_call.kwargs["url"].endswith("/u/mfa-sms-challenge")
    assert submit_call.kwargs["method"] == "POST"
    assert submit_call.kwargs["raise_on_error"] is False


async def test_run_auth_steps_8_to_13_email_real_submit_outcome_a() -> None:
    """Real email submit posts the email challenge with action=default."""
    client = _make_client()
    callback = f"{REDIRECT_URI}?code={_AUTH_CODE}&state={_OAUTH_STATE}"
    wrapper = _seq_wrapper(
        [
            _body_with_state("postmfastate"),  # step 8: _submit_email_mfa
            ("<form>irrelevant</form>", {"Location": callback}),  # step 9
            {"access_token": "a", "refresh_token": "r"},  # step 13
        ]
    )
    client._api_wrapper = wrapper  # type: ignore[method-assign]

    access, refresh = await client._run_auth_steps_8_to_13(
        _flow_state(), mfa_code="123456", mfa_method=MFA_METHOD_EMAIL
    )

    assert (access, refresh) == ("a", "r")
    submit_call = wrapper.await_args_list[0]
    assert submit_call.kwargs["url"].endswith("/u/mfa-email-challenge")
    assert submit_call.kwargs["data"]["action"] == "default"


async def test_run_auth_steps_8_to_13_invalid_code_raises_mfa_error() -> None:
    """A post-submit body without a fresh state is an MFA error."""
    client = _make_client()
    wrapper = _seq_wrapper(["<html>wrong code, MFA form returned</html>"])
    client._api_wrapper = wrapper  # type: ignore[method-assign]

    with pytest.raises(EngieBeApiClientMfaError):
        await client._run_auth_steps_8_to_13(
            _flow_state(), mfa_code="000000", mfa_method=MFA_METHOD_SMS
        )

    assert wrapper.await_count == 1


async def test_run_auth_steps_8_to_13_passkey_step12_no_code_raises() -> None:
    """Outcome B with no code in step-12 body or Location is an auth error."""
    client = _make_client()
    step9_location = (
        f"https://account.engie.be/u/passkey-enrollment?state={_PASSKEY_STATE}"
    )
    wrapper = _seq_wrapper(
        [
            _body_with_state("postmfastate"),  # step 8 submit
            (  # step 9: passkey interstitial (Location not the callback URI)
                f"<html>state={_PASSKEY_STATE}</html>",
                {"Location": step9_location},
            ),
            "<html>passkey page</html>",  # step 10
            "<html>aborted</html>",  # step 11
            (  # step 12: neither body nor Location carries a code
                "<html>no code here</html>",
                {"Location": "https://account.engie.be/u/no-code"},
            ),
        ]
    )
    client._api_wrapper = wrapper  # type: ignore[method-assign]

    with pytest.raises(EngieBeApiClientAuthenticationError):
        await client._run_auth_steps_8_to_13(
            _flow_state(), mfa_code="123456", mfa_method=MFA_METHOD_SMS
        )

    assert wrapper.await_count == 5
