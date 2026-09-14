"""Second Opinion — remote MCP endpoint for Vercel (ASGI).

Implements the MCP Streamable-HTTP JSON-RPC surface by hand (stateless,
one request = one exchange): initialize, tools/list, tools/call.
No sessions, no SSE streams — plain JSON responses, which the spec allows.

Auth: if SECOND_OPINION_API_KEY is set, requests must carry it as
`Authorization: Bearer <key>` (or `x-api-key`). Set it in Vercel env vars —
otherwise anyone with the URL can burn your OpenRouter credit.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import core  # noqa: E402

PROTOCOL_VERSION = "2026-07-28"
KNOWN_VERSIONS = {"2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"}
SERVER_INFO = {"name": "second-opinion", "version": "0.1"}

TOOL_SCHEMAS = [
    {
        "name": "second_opinion",
        "description": ("Ask rival frontier models for their take. Returns each "
                        "rival's take plus a synthesis of where they agree, "
                        "disagree, and the strongest point made."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string",
                           "description": "The question, artifact, or decision."},
                "models": {"type": "array", "items": {"type": "string"},
                           "description": ("OpenRouter model ids to consult "
                                           "(default: frontier panel).")},
                "context": {"type": "string",
                            "description": "Optional background for the rivals."},
                "max_tokens": {"type": "integer", "default": 800},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "get_transcript",
        "description": "Fetch the full stored transcript of a past consultation.",
        "inputSchema": {
            "type": "object",
            "properties": {"consultation_id": {"type": "string"}},
            "required": ["consultation_id"],
        },
    },
    {
        "name": "list_consultations",
        "description": "Recent consultations (id, time, prompt preview, models).",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 10}},
        },
    },
    {
        "name": "list_models",
        "description": "Curated frontier models with friendly names.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _authorized(headers: dict) -> bool:
    want = os.environ.get("SECOND_OPINION_API_KEY", "").strip()
    if not want:
        return True
    auth = headers.get("authorization", "")
    if auth == f"Bearer {want}":
        return True
    return headers.get("x-api-key", "") == want


async def _dispatch(method: str, params: dict) -> dict | None:
    if method == "initialize":
        # Negotiate: answer with the client's version when we support it,
        # otherwise fall back to our latest. Answering with an unsupported
        # version makes strict clients (Claude, ChatGPT) disconnect.
        params = params or {}
        asked = params.get("protocolVersion")
        version = asked if asked in KNOWN_VERSIONS else PROTOCOL_VERSION
        return {"protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO}
    if method == "tools/list":
        return {"tools": TOOL_SCHEMAS}
    if method == "tools/call":
        name = (params or {}).get("name")
        args = (params or {}).get("arguments") or {}
        try:
            if name == "second_opinion":
                result = await core.second_opinion(
                    args["prompt"], args.get("models"),
                    args.get("context"), args.get("max_tokens", 800))
            elif name == "get_transcript":
                result = core.get_transcript(args["consultation_id"])
            elif name == "list_consultations":
                result = core.list_consultations(args.get("limit", 10))
            elif name == "list_models":
                result = core.list_models()
            else:
                return {"error": {"code": -32602,
                                  "message": f"unknown tool: {name}"}}
        except Exception as e:  # noqa: BLE001
            return {"content": [{"type": "text",
                                 "text": json.dumps({"error": str(e)[:300]})}],
                    "isError": True}
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
    return {"error": {"code": -32601, "message": f"unknown method: {method}"}}


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    headers = {k.decode().lower(): v.decode()
               for k, v in scope.get("headers", [])}

    if scope["method"] == "OPTIONS":
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"access-control-allow-origin", b"*"),
                                (b"access-control-allow-headers", b"*"),
                                (b"access-control-allow-methods", b"POST, OPTIONS")]})
        await send({"type": "http.response.body", "body": b""})
        return

    if scope["method"] == "GET":
        body = json.dumps({"name": "second-opinion",
                           "mcp": "POST JSON-RPC to this URL",
                           "protocolVersion": PROTOCOL_VERSION}).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
        return

    if scope["method"] != "POST":
        await send({"type": "http.response.start", "status": 405,
                    "headers": [(b"allow", b"POST")]})
        await send({"type": "http.response.body", "body": b""})
        return

    if not _authorized(headers):
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body",
                    "body": b'{"error":"unauthorized"}'})
        return

    raw = b""
    while True:
        event = await receive()
        raw += event.get("body", b"")
        if not event.get("more_body"):
            break
    try:
        msg = json.loads(raw.decode() or "{}")
    except Exception:  # noqa: BLE001
        msg = {}

    method = msg.get("method", "")
    if method.startswith("notifications/"):
        await send({"type": "http.response.start", "status": 202})
        await send({"type": "http.response.body", "body": b""})
        return

    result = await _dispatch(method, msg.get("params") or {})
    if "error" in result and "content" not in result:
        payload = {"jsonrpc": "2.0", "id": msg.get("id"), "error": result["error"]}
    else:
        payload = {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}
    body = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json"),
                            (b"access-control-allow-origin", b"*")]})
    await send({"type": "http.response.body", "body": body})
