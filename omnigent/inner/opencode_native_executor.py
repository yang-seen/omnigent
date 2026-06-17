"""Executor that bridges Omnigent web turns into a native OpenCode session.

Built on :class:`omnigent.native_server_harness.NativeServerHarness`: the
runner owns the ``opencode serve`` process + SSE forwarder, and this
executor injects the latest web turn over the
:class:`omnigent.opencode_http_transport.OpenCodeHttpTransport` using the
loopback URL + auth secret published in the bridge state. Output is
streamed back by the runner-side forwarder, so ``run_turn`` only admits the
prompt and yields ``TurnComplete`` — the same injection/completion split as
codex-native.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omnigent.native_server_harness import NativeServerHarness
from omnigent.native_server_transport import NativePrompt
from omnigent.opencode_http_transport import OpenCodeHttpTransport
from omnigent.opencode_native_bridge import (
    OPENCODE_NATIVE_BRIDGE_DIR_ENV_VAR,
    OPENCODE_NATIVE_REQUEST_SESSION_ID_ENV_VAR,
    read_bridge_state,
)
from omnigent.runtime.harness_descriptors import HARNESS_DESCRIPTORS


class OpenCodeNativeExecutor(NativeServerHarness):
    """
    Harness-side executor for ``omnigent opencode`` web UI turns.

    :param bridge_dir: Optional bridge directory override. ``None`` reads
        :data:`OPENCODE_NATIVE_BRIDGE_DIR_ENV_VAR`.
    """

    def __init__(self, bridge_dir: Path | None = None) -> None:
        self._bridge_dir = bridge_dir or _bridge_dir_from_env()
        self._request_session_id = _request_session_id_from_env()
        super().__init__(
            descriptor=HARNESS_DESCRIPTORS["opencode-native"],
            transport=OpenCodeHttpTransport(bridge_dir=self._bridge_dir),
            resolve_session_id=self._resolve_session_id,
            build_prompt=_content_to_native_prompt,
        )

    async def _resolve_session_id(self) -> str | None:
        """
        Resolve the OpenCode session id from bridge state.

        :returns: The OpenCode session id when this harness may inject into
            it, else ``None``.
        """
        state = read_bridge_state(self._bridge_dir)
        if state is None:
            return None
        if not _session_is_active(state.session_id, self._request_session_id):
            return None
        return state.opencode_session_id


def _bridge_dir_from_env() -> Path:
    """
    Resolve the native OpenCode bridge directory from harness spawn env.

    :returns: Bridge directory path.
    :raises RuntimeError: If the env var is missing.
    """
    raw = os.environ.get(OPENCODE_NATIVE_BRIDGE_DIR_ENV_VAR, "").strip()
    if not raw:
        raise RuntimeError(f"{OPENCODE_NATIVE_BRIDGE_DIR_ENV_VAR} is required")
    return Path(raw)


def _request_session_id_from_env() -> str | None:
    """
    Resolve the Omnigent session id that requested this harness process.

    :returns: Omnigent session id, e.g. ``"conv_abc123"``, or ``None``.
    """
    raw = os.environ.get(OPENCODE_NATIVE_REQUEST_SESSION_ID_ENV_VAR, "").strip()
    return raw or None


def _session_is_active(session_id: str, request_session_id: str | None) -> bool:
    """
    Return whether this harness may inject into the native session.

    :param session_id: Session id from bridge state.
    :param request_session_id: Session id from harness spawn env.
    :returns: ``True`` when injection is allowed.
    """
    return request_session_id is None or request_session_id == session_id


def _content_to_native_prompt(content: Any) -> NativePrompt | None:
    """
    Normalize executor message content into a :class:`NativePrompt`.

    Text blocks are concatenated; image/file blocks pass through as
    attachments (the transport renders them as OpenCode file parts using
    their data URIs, so there is no socket-size limit to work around).

    :param content: Message content — a string or a list of content blocks
        such as ``{"type": "input_text", "text": "..."}`` and
        ``{"type": "input_image", "image_url": "data:image/png;base64,..."}``.
    :returns: The prompt, or ``None`` when there is nothing to send.
    """
    if isinstance(content, str):
        return NativePrompt(text=content) if content else None
    if isinstance(content, list):
        texts: list[str] = []
        attachments: list[Mapping[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in {"input_text", "text"}:
                text = block.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
            elif block_type in {"input_image", "input_file"}:
                attachments.append(block)
        if not texts and not attachments:
            return None
        return NativePrompt(text="\n".join(texts), attachments=tuple(attachments))
    if content is None:
        return None
    return NativePrompt(text=json.dumps(content, ensure_ascii=True))
