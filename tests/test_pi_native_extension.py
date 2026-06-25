"""End-to-end tests for the generated pi-native bridge extension."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_delivery_cap_drops_followup_without_failed_session_status(
    tmp_path: Path,
) -> None:
    """The extension must not terminal-fail a session when follow-up delivery caps.

    This runs the real JavaScript extension under Node with a real inbox payload
    and mocked Pi/fetch boundaries. Five consecutive ``sendUserMessage`` throws
    should consume the inbox file and emit an informational conversation item,
    never ``external_session_status`` with ``status: "failed"``.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the pi-native extension e2e test")

    extension_path = (
        Path(__file__).resolve().parents[1]
        / "omnigent"
        / "resources"
        / "pi_native"
        / "omnigent_pi_native_extension.js"
    )

    script = r"""
const assert = require("assert").strict;
const fs = require("fs");
const path = require("path");

const extensionPath = process.argv[1];
const tmpDir = process.argv[2];
const inboxDir = path.join(tmpDir, "inbox");
const payloadPath = path.join(inboxDir, "000-msg.json");
const configPath = path.join(tmpDir, "config.json");

fs.mkdirSync(inboxDir, { recursive: true });
fs.writeFileSync(
  payloadPath,
  JSON.stringify({ id: "msg-1", type: "user_message", content: "follow up" }),
);
fs.writeFileSync(
  configPath,
  JSON.stringify({
    serverUrl: "http://omnigent.test",
    sessionId: "session-1",
    inboxDir,
    authHeaders: { authorization: "Bearer test" },
  }),
);

process.env.OMNIGENT_PI_NATIVE_CONFIG = configPath;

const postedEvents = [];
global.fetch = async (_url, request) => {
  postedEvents.push(JSON.parse(request.body));
  return { ok: true };
};

let pollInbox = null;
global.setInterval = (fn, _ms) => {
  pollInbox = fn;
  return { fakeInterval: true };
};

const handlers = {};
const sendAttempts = [];
const pi = {
  registerCommand() {},
  on(eventName, handler) {
    handlers[eventName] = handler;
  },
  sendUserMessage(content, options) {
    sendAttempts.push({ content, options });
    throw new Error("Pi is not ready");
  },
};

require(extensionPath)(pi);

(async () => {
  assert.equal(typeof handlers.session_start, "function");
  await handlers.session_start({}, {
    sessionManager: { getSessionId: () => "native-session-1" },
    ui: { setTitle() {}, setStatus() {}, notify() {} },
  });
  assert.equal(typeof pollInbox, "function");

  for (let attempt = 0; attempt < 5; attempt += 1) {
    pollInbox();
  }
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(
    sendAttempts,
    Array.from({ length: 5 }, () => ({
      content: "follow up",
      options: { deliverAs: "followUp" },
    })),
  );
  assert.equal(fs.existsSync(payloadPath), false);
  assert.equal(
    postedEvents.some(
      (event) =>
        event.type === "external_session_status" &&
        event.data &&
        event.data.status === "failed",
    ),
    false,
    JSON.stringify(postedEvents),
  );

  const dropNote = postedEvents.find(
    (event) =>
      event.type === "external_conversation_item" &&
      event.data &&
      event.data.item_type === "error" &&
      event.data.item_data &&
      event.data.item_data.code === "pi_followup_delivery_dropped",
  );
  assert.ok(dropNote, JSON.stringify(postedEvents));
  assert.equal(dropNote.data.item_data.source, "execution");
  assert.match(dropNote.data.response_id, /^pi-deliver-dropped-/);
  // The note must be actionable: include the dropped message id and a preview
  // of its content so an operator can identify what was lost.
  assert.match(dropNote.data.item_data.message, /msg-1/);
  assert.match(dropNote.data.item_data.message, /follow up/);
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""

    result = subprocess.run(
        [node, "-e", script, str(extension_path), str(tmp_path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_registers_omnigent_tools_and_execute_round_trips(tmp_path: Path) -> None:
    """The extension registers config.tools and execute() round-trips via /mcp.

    Drives the real JavaScript extension under Node with a config carrying a
    flat tool list (as the runner now writes). Asserts each tool is registered
    via ``pi.registerTool`` with its schema, and that calling a registered
    tool's ``execute`` POSTs a JSON-RPC ``tools/call`` to the Omnigent server's
    ``/v1/sessions/{id}/mcp`` proxy and returns the tool output to Pi.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the pi-native extension e2e test")

    extension_path = (
        Path(__file__).resolve().parents[1]
        / "omnigent"
        / "resources"
        / "pi_native"
        / "omnigent_pi_native_extension.js"
    )

    script = r"""
const assert = require("assert").strict;
const fs = require("fs");
const path = require("path");

const extensionPath = process.argv[1];
const tmpDir = process.argv[2];
const inboxDir = path.join(tmpDir, "inbox");
const configPath = path.join(tmpDir, "config.json");

fs.mkdirSync(inboxDir, { recursive: true });
fs.writeFileSync(
  configPath,
  JSON.stringify({
    serverUrl: "http://omnigent.test",
    sessionId: "conv_abc",
    inboxDir,
    authHeaders: { authorization: "Bearer test" },
    tools: [
      {
        name: "sys_os_read",
        description: "Read a file from the OS environment",
        parameters: {
          type: "object",
          properties: { path: { type: "string" } },
          required: ["path"],
        },
      },
      {
        name: "sys_os_shell",
        description: "Run a shell command",
        parameters: { type: "object", properties: {} },
      },
    ],
  }),
);

process.env.OMNIGENT_PI_NATIVE_CONFIG = configPath;

// Capture every fetch so we can assert the execute() round-trip hits /mcp with
// a JSON-RPC tools/call and the right auth headers.
const fetchCalls = [];
global.fetch = async (url, request) => {
  fetchCalls.push({ url, request });
  // Mimic the Omnigent /mcp proxy success envelope.
  return {
    ok: true,
    async json() {
      return {
        jsonrpc: "2.0",
        id: 1,
        result: { content: [{ type: "text", text: "file contents here" }] },
      };
    },
  };
};

global.setInterval = () => ({ fakeInterval: true });

const registered = {};
const pi = {
  registerCommand() {},
  on() {},
  registerTool(spec) {
    registered[spec.name] = spec;
  },
  sendUserMessage() {},
};

require(extensionPath)(pi);

(async () => {
  // Both configured tools must be registered with their schema.
  assert.ok(registered.sys_os_read, "sys_os_read not registered");
  assert.ok(registered.sys_os_shell, "sys_os_shell not registered");
  assert.equal(registered.sys_os_read.name, "sys_os_read");
  assert.equal(registered.sys_os_read.label, "sys_os_read");
  assert.equal(
    registered.sys_os_read.description,
    "Read a file from the OS environment",
  );
  assert.deepEqual(registered.sys_os_read.parameters, {
    type: "object",
    properties: { path: { type: "string" } },
    required: ["path"],
  });
  assert.equal(typeof registered.sys_os_read.execute, "function");

  // execute() must round-trip through the /mcp proxy and return the output.
  const result = await registered.sys_os_read.execute("call-1", {
    path: "/etc/hosts",
  });

  assert.equal(fetchCalls.length, 1, JSON.stringify(fetchCalls));
  const call = fetchCalls[0];
  assert.equal(call.url, "http://omnigent.test/v1/sessions/conv_abc/mcp");
  assert.equal(call.request.method, "POST");
  assert.equal(call.request.headers.authorization, "Bearer test");
  const body = JSON.parse(call.request.body);
  assert.equal(body.jsonrpc, "2.0");
  assert.equal(body.method, "tools/call");
  assert.equal(body.params.name, "sys_os_read");
  assert.deepEqual(body.params.arguments, { path: "/etc/hosts" });

  // The MCP text content is surfaced to Pi as a single text block.
  assert.ok(result && Array.isArray(result.content), JSON.stringify(result));
  assert.equal(result.content[0].type, "text");
  assert.equal(result.content[0].text, "file contents here");
  assert.equal(result.isError, false);
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""

    result = subprocess.run(
        [node, "-e", script, str(extension_path), str(tmp_path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_bridged_tool_call_skips_hook_policy_eval(tmp_path: Path) -> None:
    """The tool_call hook must NOT re-evaluate policy for bridged Omnigent tools.

    Bridged tools are policy-evaluated server-side inside the /mcp proxy when
    execute() runs, so the hook-level ``policies/evaluate`` call would
    double-evaluate (and, for ASK, double-prompt). The hook must skip bridged
    tool names but still evaluate Pi's own built-in tools.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the pi-native extension e2e test")

    extension_path = (
        Path(__file__).resolve().parents[1]
        / "omnigent"
        / "resources"
        / "pi_native"
        / "omnigent_pi_native_extension.js"
    )

    script = r"""
const assert = require("assert").strict;
const fs = require("fs");
const path = require("path");

const extensionPath = process.argv[1];
const tmpDir = process.argv[2];
const inboxDir = path.join(tmpDir, "inbox");
const configPath = path.join(tmpDir, "config.json");

fs.mkdirSync(inboxDir, { recursive: true });
fs.writeFileSync(
  configPath,
  JSON.stringify({
    serverUrl: "http://omnigent.test",
    sessionId: "conv_abc",
    inboxDir,
    authHeaders: {},
    tools: [
      { name: "sys_os_read", description: "", parameters: { type: "object", properties: {} } },
    ],
  }),
);

process.env.OMNIGENT_PI_NATIVE_CONFIG = configPath;

const policyUrls = [];
global.fetch = async (url, _request) => {
  if (typeof url === "string" && url.indexOf("/policies/evaluate") !== -1) {
    policyUrls.push(url);
  }
  return { ok: true, async json() { return {}; } };
};
global.setInterval = () => ({ fakeInterval: true });

const handlers = {};
const pi = {
  registerCommand() {},
  on(name, fn) { handlers[name] = fn; },
  registerTool() {},
  sendUserMessage() {},
};

require(extensionPath)(pi);

(async () => {
  const ctx = { isIdle: () => false, abort() {} };
  await handlers.session_start({}, ctx);
  await handlers.agent_start({}, ctx);
  await handlers.turn_start({ turnIndex: 1 }, ctx);

  // Bridged tool: hook must NOT call policies/evaluate (server gates it).
  await handlers.tool_call({ toolCallId: "t1", toolName: "sys_os_read", input: {} }, ctx);
  assert.equal(
    policyUrls.length,
    0,
    "bridged tool must not trigger hook-level policy eval: " + JSON.stringify(policyUrls),
  );

  // Pi's own built-in tool (not bridged): hook MUST evaluate policy.
  await handlers.tool_call({ toolCallId: "t2", toolName: "read", input: {} }, ctx);
  assert.equal(
    policyUrls.length,
    1,
    "non-bridged tool must trigger hook-level policy eval: " + JSON.stringify(policyUrls),
  );
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""

    result = subprocess.run(
        [node, "-e", script, str(extension_path), str(tmp_path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_input_required_approve_round_trips_then_executes(tmp_path: Path) -> None:
    """An ASK-gated bridged tool resolves the elicitation and retries once.

    On the initial ``tools/call`` the /mcp proxy returns an MCP ``input_required``
    (MRTR) envelope instead of executing. The extension must:
      1. long-poll ``/policies/evaluate`` for the human verdict (ALLOW here),
      2. retry ``tools/call`` ONCE with ``requestState`` + ``inputResponses``
         carrying the proxy's elicitation id and ``{action: "accept"}``,
      3. surface the executed output to Pi with ``isError: false``.
    It must NEVER hand the raw ``input_required`` envelope back as success.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the pi-native extension e2e test")

    extension_path = (
        Path(__file__).resolve().parents[1]
        / "omnigent"
        / "resources"
        / "pi_native"
        / "omnigent_pi_native_extension.js"
    )

    script = r"""
const assert = require("assert").strict;
const fs = require("fs");
const path = require("path");

const extensionPath = process.argv[1];
const tmpDir = process.argv[2];
const inboxDir = path.join(tmpDir, "inbox");
const configPath = path.join(tmpDir, "config.json");

fs.mkdirSync(inboxDir, { recursive: true });
fs.writeFileSync(
  configPath,
  JSON.stringify({
    serverUrl: "http://omnigent.test",
    sessionId: "conv_abc",
    inboxDir,
    authHeaders: { authorization: "Bearer test" },
    tools: [
      {
        name: "sys_os_shell",
        description: "Run a shell command",
        parameters: { type: "object", properties: {} },
      },
    ],
  }),
);

process.env.OMNIGENT_PI_NATIVE_CONFIG = configPath;

const ELICIT_ID = "elicit_abc123";
const REQUEST_STATE = JSON.stringify({ elicitation_id: ELICIT_ID, session_id: "conv_abc" });

const mcpCalls = [];
let mcpCallCount = 0;
global.fetch = async (url, request) => {
  if (typeof url === "string" && url.indexOf("/policies/evaluate") !== -1) {
    // The server-side ASK park collapses to a hard ALLOW after the human approves.
    return { ok: true, async json() { return { result: "POLICY_ACTION_ALLOW" }; } };
  }
  // /mcp proxy.
  mcpCallCount += 1;
  mcpCalls.push({ url, body: JSON.parse(request.body) });
  if (mcpCallCount === 1) {
    // First call → ASK → input_required (MRTR envelope).
    return {
      ok: true,
      async json() {
        return {
          jsonrpc: "2.0",
          id: 1,
          result: {
            resultType: "input_required",
            inputRequests: { [ELICIT_ID]: { method: "elicitation/create", params: {} } },
            requestState: REQUEST_STATE,
          },
        };
      },
    };
  }
  // Retry (post-approval) → executed result.
  return {
    ok: true,
    async json() {
      return { jsonrpc: "2.0", id: 2, result: { content: [{ type: "text", text: "ran ok" }] } };
    },
  };
};
global.setInterval = () => ({ fakeInterval: true });

const registered = {};
const pi = {
  registerCommand() {},
  on() {},
  registerTool(spec) { registered[spec.name] = spec; },
  sendUserMessage() {},
};

require(extensionPath)(pi);

(async () => {
  const result = await registered.sys_os_shell.execute("call-1", {});

  // Exactly two /mcp calls: the initial ASK and ONE approval retry.
  assert.equal(mcpCallCount, 2, JSON.stringify(mcpCalls));
  const retry = mcpCalls[1].body;
  assert.equal(retry.id, 2, "retry must use a different JSON-RPC id");
  assert.equal(retry.params.requestState, REQUEST_STATE);
  assert.deepEqual(retry.params.inputResponses, {
    [ELICIT_ID]: { action: "accept" },
  });

  // The executed output surfaces as a normal (non-error) tool result.
  assert.ok(result && Array.isArray(result.content), JSON.stringify(result));
  assert.equal(result.content[0].text, "ran ok");
  assert.equal(result.isError, false);
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""

    result = subprocess.run(
        [node, "-e", script, str(extension_path), str(tmp_path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_input_required_denied_fails_closed_not_false_success(tmp_path: Path) -> None:
    """A declined ASK gate fails CLOSED (isError) — never reports false success.

    When the elicitation park collapses to DENY, the extension retries once with
    ``{action: "decline"}``; the proxy returns a -32000 error which surfaces as an
    ``isError: true`` tool result. The raw ``input_required`` envelope must never
    be returned to the model as a successful result.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the pi-native extension e2e test")

    extension_path = (
        Path(__file__).resolve().parents[1]
        / "omnigent"
        / "resources"
        / "pi_native"
        / "omnigent_pi_native_extension.js"
    )

    script = r"""
const assert = require("assert").strict;
const fs = require("fs");
const path = require("path");

const extensionPath = process.argv[1];
const tmpDir = process.argv[2];
const inboxDir = path.join(tmpDir, "inbox");
const configPath = path.join(tmpDir, "config.json");

fs.mkdirSync(inboxDir, { recursive: true });
fs.writeFileSync(
  configPath,
  JSON.stringify({
    serverUrl: "http://omnigent.test",
    sessionId: "conv_abc",
    inboxDir,
    authHeaders: {},
    tools: [
      { name: "sys_os_shell", description: "", parameters: { type: "object", properties: {} } },
    ],
  }),
);

process.env.OMNIGENT_PI_NATIVE_CONFIG = configPath;

const ELICIT_ID = "elicit_deny";
const REQUEST_STATE = JSON.stringify({ elicitation_id: ELICIT_ID, session_id: "conv_abc" });

let mcpCallCount = 0;
let lastRetryBody = null;
global.fetch = async (url, request) => {
  if (typeof url === "string" && url.indexOf("/policies/evaluate") !== -1) {
    // The human declined → the ASK park collapses to DENY.
    return { ok: true, async json() { return { result: "POLICY_ACTION_DENY", reason: "nope" }; } };
  }
  mcpCallCount += 1;
  if (mcpCallCount === 1) {
    return {
      ok: true,
      async json() {
        return {
          jsonrpc: "2.0",
          id: 1,
          result: {
            resultType: "input_required",
            inputRequests: { [ELICIT_ID]: { method: "elicitation/create", params: {} } },
            requestState: REQUEST_STATE,
          },
        };
      },
    };
  }
  lastRetryBody = JSON.parse(request.body);
  // Server denies the declined retry with the MCP -32000 convention.
  return {
    ok: true,
    async json() {
      return {
        jsonrpc: "2.0",
        id: 2,
        error: { code: -32000, message: "Tool call denied by user" },
      };
    },
  };
};
global.setInterval = () => ({ fakeInterval: true });

const registered = {};
const pi = {
  registerCommand() {},
  on() {},
  registerTool(spec) { registered[spec.name] = spec; },
  sendUserMessage() {},
};

require(extensionPath)(pi);

(async () => {
  const result = await registered.sys_os_shell.execute("call-1", {});

  assert.equal(mcpCallCount, 2, "expected one approval retry");
  assert.deepEqual(lastRetryBody.params.inputResponses, {
    [ELICIT_ID]: { action: "decline" },
  });

  // Must surface as an error, NOT a false success, and must not leak the raw
  // input_required envelope.
  assert.equal(result.isError, true, JSON.stringify(result));
  assert.ok(result.content[0].text.indexOf("denied") !== -1, result.content[0].text);
  assert.equal(result.content[0].text.indexOf("input_required"), -1, result.content[0].text);
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});
"""

    result = subprocess.run(
        [node, "-e", script, str(extension_path), str(tmp_path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
