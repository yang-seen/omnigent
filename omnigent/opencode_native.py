"""Native OpenCode wrapper agent spec for ``opencode-native-ui``.

Materializes the terminal-first built-in agent the server seeds (parallel
to ``omnigent.codex_native._materialize_codex_agent_spec``). The runner
owns the ``opencode serve`` process and SSE forwarder; this spec just binds
the ``opencode-native`` harness and declares the spawn/terminal surface so
the web UI renders the session terminal-first.

The interactive local ``omnigent opencode`` CLI wrapper (the analog of
``omnigent codex``) is intentionally out of scope for this module — the
web-UI + runner takeover path is the supported surface; see the design's
deviations note.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Built-in native-UI agent name (matches the descriptor's
# ``wrapper_agent_name`` and the ap-web native registry).
_AGENT_NAME = "opencode-native-ui"


def _materialize_opencode_agent_spec(
    tmpdir: Path,
    *,
    model: str | None = None,
) -> Path:
    """
    Write the terminal-first agent spec used by the OpenCode native UI.

    :param tmpdir: Temporary directory for the generated YAML file.
    :param model: Optional model id, e.g. ``"anthropic/claude-opus-4"``.
    :returns: Path to the generated YAML spec.
    """
    yaml_path = tmpdir / "opencode-native-ui.yaml"
    executor: dict[str, str] = {"harness": "opencode-native"}
    if model is not None:
        executor["model"] = model
    raw: dict[str, Any] = {
        "name": _AGENT_NAME,
        "prompt": (
            "OpenCode is running in the session terminal. Web UI messages are "
            "forwarded into the same native OpenCode server session."
        ),
        "executor": executor,
        # Opt the native session into the child-session spawn writes
        # (sys_session_create / sys_session_send / sys_session_close) so the
        # wrapped opencode can author agent configs and launch them as
        # sub-agent sessions. The relay derives its advertised tool set from
        # this spec via ToolManager.
        "spawn": True,
        "os_env": {
            "type": "caller_process",
            "cwd": ".",
            "sandbox": {"type": "none"},
        },
        # Declare a default shell terminal so the relay advertises the
        # ``sys_terminal_*`` family to the wrapped opencode (the relay's gate
        # is a non-empty ``terminals:`` block on this spec).
        "terminals": {
            "shell": {
                "command": "bash",
                "allow_cwd_override": True,
                "os_env": {
                    "type": "caller_process",
                    "cwd": ".",
                    "sandbox": {"type": "none"},
                },
            },
        },
    }
    yaml_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return yaml_path
