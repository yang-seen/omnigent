"""E2E tests running against the mock LLM server (no API key needed).

These tests exercise real server → runner → harness dispatch with
pre-canned LLM responses. They run automatically when no
``--llm-api-key`` is provided and are skipped when a real key is
present (real-LLM e2e tests cover the same paths with nondeterministic
responses).

Each test registers an inline agent with ``mock-model`` and configures
the mock server's response queue before dispatching turns.

Usage::

    pytest tests/e2e/test_mock_llm_e2e.py -v
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest

from tests.e2e.conftest import (
    configure_mock_llm,
    create_runner_bound_session,
    poll_session_until_terminal,
    register_inline_agent,
    reset_mock_llm,
    send_user_message_to_session,
)


def _extract_all_text(body: dict[str, Any]) -> str:
    """Concatenate all assistant output_text blocks."""
    parts: list[str] = []
    for item in body.get("output", []):
        if item.get("type") == "message" and item.get("role") == "assistant":
            for block in item.get("content", []):
                text = block.get("text")
                if text:
                    parts.append(text)
    return "\n".join(parts)


def _wait_for_session_running(
    client: httpx.Client,
    session_id: str,
    timeout: float = 60,
) -> None:
    """Poll until session status == 'running'."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/v1/sessions/{session_id}")
        r.raise_for_status()
        if r.json().get("status") == "running":
            return
        time.sleep(0.5)
    raise AssertionError(f"Session {session_id} did not reach 'running' within {timeout}s")


@pytest.fixture(autouse=True)
def _skip_when_real_llm(using_mock_llm: bool) -> None:
    """Skip mock-only tests when a real --llm-api-key is provided."""
    if not using_mock_llm:
        pytest.skip("mock-only test; real LLM key provided")


@pytest.fixture()
def _reset_mock(mock_llm_server_url: str | None) -> None:
    """Reset mock server state before each test."""
    reset_mock_llm(mock_llm_server_url)


# ── Single-turn echo ────────────────────────────────────


def test_single_turn_echo(
    http_client: httpx.Client,
    live_runner_id: str,
    mock_llm_server_url: str | None,
    _reset_mock: None,
) -> None:
    """Single user message → mock response round-trips through the stack."""
    marker = f"ECHO-{uuid.uuid4().hex[:8]}"
    model = f"mock-echo-{uuid.uuid4().hex[:6]}"

    agent_name = register_inline_agent(
        http_client,
        name=f"echo-{uuid.uuid4().hex[:6]}",
        harness="openai-agents",
        model=model,
        profile="",
        prompt="You are a test assistant.",
        mock_llm_base_url=(f"{mock_llm_server_url}/v1" if mock_llm_server_url else None),
    )
    configure_mock_llm(mock_llm_server_url, [{"text": marker}], key=model)

    session_id = create_runner_bound_session(
        http_client, agent_name=agent_name, runner_id=live_runner_id
    )
    resp_id = send_user_message_to_session(http_client, session_id=session_id, content="Hello")
    body = poll_session_until_terminal(
        http_client, session_id=session_id, response_id=resp_id, timeout=30
    )
    assert body["status"] == "completed", f"failed: {body.get('error')}"
    assert marker in _extract_all_text(body)


# ── Multi-turn context ──────────────────────────────────


def test_multi_turn_two_sequential(
    http_client: httpx.Client,
    live_runner_id: str,
    mock_llm_server_url: str | None,
    _reset_mock: None,
) -> None:
    """Two sequential turns complete independently (no steering)."""
    model = f"mock-multi-{uuid.uuid4().hex[:6]}"
    marker_1 = f"FIRST-{uuid.uuid4().hex[:8]}"
    marker_2 = f"SECOND-{uuid.uuid4().hex[:8]}"

    agent_name = register_inline_agent(
        http_client,
        name=f"multi-{uuid.uuid4().hex[:6]}",
        harness="openai-agents",
        model=model,
        profile="",
        prompt="You are a test assistant.",
        mock_llm_base_url=(f"{mock_llm_server_url}/v1" if mock_llm_server_url else None),
    )
    configure_mock_llm(
        mock_llm_server_url,
        [{"text": marker_1}, {"text": marker_2}],
        key=model,
    )

    session_id = create_runner_bound_session(
        http_client, agent_name=agent_name, runner_id=live_runner_id
    )

    # Turn 1
    r1 = send_user_message_to_session(http_client, session_id=session_id, content="Say hello")
    b1 = poll_session_until_terminal(
        http_client, session_id=session_id, response_id=r1, timeout=30
    )
    assert b1["status"] == "completed"
    assert marker_1 in _extract_all_text(b1)

    # Turn 2
    r2 = send_user_message_to_session(http_client, session_id=session_id, content="What is 2+2?")
    b2 = poll_session_until_terminal(
        http_client, session_id=session_id, response_id=r2, timeout=30
    )
    assert b2["status"] == "completed"
    assert marker_2 in _extract_all_text(b2)


# ── File upload + analysis ──────────────────────────────


def test_file_upload_and_mock_analysis(
    http_client: httpx.Client,
    live_runner_id: str,
    mock_llm_server_url: str | None,
    _reset_mock: None,
) -> None:
    """Upload a file and verify the agent responds with mock text.

    Mirrors test_journey_file_upload_analysis but with deterministic
    mock responses — validates that file upload → session dispatch →
    LLM call → response persistence works end-to-end.
    """
    model = f"mock-file-{uuid.uuid4().hex[:6]}"

    agent_name = register_inline_agent(
        http_client,
        name=f"file-{uuid.uuid4().hex[:6]}",
        harness="openai-agents",
        model=model,
        profile="",
        prompt="You are a document analyst.",
        mock_llm_base_url=(f"{mock_llm_server_url}/v1" if mock_llm_server_url else None),
    )
    configure_mock_llm(
        mock_llm_server_url,
        [{"text": "The capital of Freedonia is Quuxville."}],
        key=model,
    )

    session_id = create_runner_bound_session(
        http_client, agent_name=agent_name, runner_id=live_runner_id
    )

    # Upload a markdown file
    doc_content = b"# Freedonia\n\nThe capital is Quuxville.\n"
    upload = http_client.post(
        f"/v1/sessions/{session_id}/resources/files",
        files={"file": ("doc.md", doc_content, "text/markdown")},
    )
    upload.raise_for_status()
    file_id = upload.json()["id"]

    # Ask about the file
    resp_id = send_user_message_to_session(
        http_client,
        session_id=session_id,
        content=[
            {"type": "input_text", "text": "What is the capital?"},
            {"type": "input_file", "file_id": file_id},
        ],
    )
    body = poll_session_until_terminal(
        http_client, session_id=session_id, response_id=resp_id, timeout=30
    )
    assert body["status"] == "completed", f"failed: {body.get('error')}"
    assert "Quuxville" in _extract_all_text(body)


# ── Steering: message into running task ─────────────────


def test_steering_acknowledged_mock(
    http_client: httpx.Client,
    live_runner_id: str,
    mock_llm_server_url: str | None,
    _reset_mock: None,
) -> None:
    """Steer into a running session and verify the mock response surfaces.

    The first LLM call blocks on a gate. While it's blocked, we send
    a steer message. Then we release the gate and the LLM returns
    a response containing PINEAPPLE.

    This validates the steering plumbing (events endpoint → inbox →
    re-run) without requiring real LLM nondeterminism.
    """
    model = f"mock-steer-{uuid.uuid4().hex[:6]}"

    agent_name = register_inline_agent(
        http_client,
        name=f"steer-{uuid.uuid4().hex[:6]}",
        harness="openai-agents",
        model=model,
        profile="",
        prompt="You are a test assistant. Follow instructions exactly.",
        mock_llm_base_url=(f"{mock_llm_server_url}/v1" if mock_llm_server_url else None),
    )
    # First response: a text response (the agent processes the
    # initial prompt). Second response: after the steer arrives,
    # the agent re-runs and gets this response containing PINEAPPLE.
    configure_mock_llm(
        mock_llm_server_url,
        [
            {"text": "Working on your request..."},
            {"text": "PINEAPPLE"},
        ],
        key=model,
    )

    session_id = create_runner_bound_session(
        http_client, agent_name=agent_name, runner_id=live_runner_id
    )

    # Start a turn
    task_id = send_user_message_to_session(
        http_client,
        session_id=session_id,
        content="Write a long essay about testing.",
    )

    # Wait for the session to be running
    _wait_for_session_running(http_client, session_id, timeout=30)

    # Send a steer while the turn is in progress
    send_user_message_to_session(
        http_client,
        session_id=session_id,
        content="Say only: PINEAPPLE",
    )

    body = poll_session_until_terminal(
        http_client, session_id=session_id, response_id=task_id, timeout=60
    )
    assert body["status"] == "completed", f"failed: {body.get('error')}"
    all_text = _extract_all_text(body)
    assert "PINEAPPLE" in all_text.upper(), (
        f"Steering not acknowledged in mock mode: {all_text[:300]}"
    )
