"""Tests for the server-side workspace lookup used by native resume."""

from __future__ import annotations

import httpx
import pytest

from omnigent import _native_session_workspace
from omnigent._native_session_workspace import fetch_session_workspace


class _FakeHttpxClient:
    """Minimal context-manager stand-in for :class:`httpx.Client`.

    :param response: The response every ``get`` returns.
    :param calls: Shared list capturing constructor kwargs and URLs.
    """

    response: httpx.Response = httpx.Response(200, json={})
    calls: list[dict[str, object]] = []

    def __init__(self, *, base_url: str, headers: dict[str, str], timeout: float) -> None:
        """Capture construction arguments for later assertions.

        :param base_url: Omnigent server base URL.
        :param headers: HTTP headers passed by the wrapper.
        :param timeout: Request timeout in seconds.
        """
        type(self).calls.append({"base_url": base_url, "headers": headers, "timeout": timeout})

    def __enter__(self) -> _FakeHttpxClient:
        """Enter the fake client context.

        :returns: This fake client.
        """
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Exit the fake client context.

        :param exc_type: Exception type, or ``None``.
        :param exc: Exception instance, or ``None``.
        :param traceback: Traceback, or ``None``.
        """

    def get(self, url: str) -> httpx.Response:
        """Return the canned response for the requested URL.

        :param url: Relative session path, e.g. ``"/v1/sessions/conv_x"``.
        :returns: The canned HTTP response.
        """
        type(self).calls.append({"url": url})
        return type(self).response


@pytest.fixture()
def _fake_client(monkeypatch: pytest.MonkeyPatch) -> type[_FakeHttpxClient]:
    """Install the fake httpx client and reset its capture state."""
    _FakeHttpxClient.calls = []
    _FakeHttpxClient.response = httpx.Response(200, json={})
    monkeypatch.setattr(_native_session_workspace.httpx, "Client", _FakeHttpxClient)
    return _FakeHttpxClient


def test_fetch_session_workspace_returns_server_workspace(
    _fake_client: type[_FakeHttpxClient],
) -> None:
    """The session endpoint's ``workspace`` field is returned verbatim."""
    _fake_client.response = httpx.Response(200, json={"workspace": "/home/me/repo"})

    result = fetch_session_workspace(
        base_url="http://ap.example",
        headers={"Authorization": "Bearer t"},
        session_id="conv with space",
    )

    assert result == "/home/me/repo"
    assert _fake_client.calls == [
        {
            "base_url": "http://ap.example",
            "headers": {"Authorization": "Bearer t"},
            "timeout": 10.0,
        },
        {"url": "/v1/sessions/conv%20with%20space"},
    ]


def test_fetch_session_workspace_none_base_url_skips_request(
    _fake_client: type[_FakeHttpxClient],
) -> None:
    """``base_url=None`` (no server context) makes no HTTP request."""
    result = fetch_session_workspace(base_url=None, headers={}, session_id="conv_x")

    assert result is None
    assert _fake_client.calls == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={"detail": "not found"}),
        httpx.Response(200, json={"workspace": None}),
        httpx.Response(200, json={"workspace": ""}),
        httpx.Response(200, json={}),
        httpx.Response(200, json=["not", "a", "dict"]),
    ],
)
def test_fetch_session_workspace_degrades_to_none(
    _fake_client: type[_FakeHttpxClient],
    response: httpx.Response,
) -> None:
    """Errors and absent/blank workspaces degrade to ``None`` (no raise)."""
    _fake_client.response = response

    result = fetch_session_workspace(base_url="http://ap.example", headers={}, session_id="conv_x")

    assert result is None


def test_fetch_session_workspace_transport_error_degrades_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport failure is swallowed; resume falls back to current cwd."""

    def boom(**_kwargs: object) -> object:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(_native_session_workspace.httpx, "Client", boom)

    result = fetch_session_workspace(base_url="http://ap.example", headers={}, session_id="conv_x")

    assert result is None
