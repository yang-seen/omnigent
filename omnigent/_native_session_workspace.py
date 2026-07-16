"""Server-side workspace lookup for native-wrapper resume alignment.

The native Claude/Codex wrappers record the launch cwd client-side at
session creation, but sessions created elsewhere (desktop app, another
machine) have no local record. The server session snapshot
(``GET /v1/sessions/{id}``) carries an authoritative ``workspace``
field for those sessions; this helper fetches it so resume can align
the wrapper cwd instead of silently adopting whatever directory the
CLI happens to run from.
"""

from __future__ import annotations

import logging

import httpx

from omnigent.native_terminal import url_component

_logger = logging.getLogger(__name__)


def fetch_session_workspace(
    *,
    base_url: str | None,
    headers: dict[str, str],
    session_id: str,
) -> str | None:
    """
    Fetch a session's server-recorded workspace path.

    Best-effort: any transport or payload problem returns ``None`` so
    resume falls back to the wrapper's current cwd, matching the
    behavior before server-side alignment existed.

    :param base_url: Omnigent server base URL, or ``None`` when unavailable.
    :param headers: HTTP auth headers for the Omnigent request.
    :param session_id: Omnigent conversation id, e.g. ``"conv_abc123"``.
    :returns: Absolute workspace path recorded on the server, e.g.
        ``"/home/me/repo"``, or ``None`` when unknown.
    """
    if base_url is None:
        return None
    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=10.0) as client:
            resp = client.get(f"/v1/sessions/{url_component(session_id)}")
        if resp.status_code >= 400:
            return None
        payload = resp.json()
    except Exception:  # noqa: BLE001 - optional resume preflight
        _logger.warning(
            "failed to fetch server workspace for resume alignment; session=%s",
            session_id,
            exc_info=True,
        )
        return None
    workspace = payload.get("workspace") if isinstance(payload, dict) else None
    if not isinstance(workspace, str) or not workspace:
        return None
    return workspace
