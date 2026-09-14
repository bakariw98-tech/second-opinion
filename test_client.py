"""MCP stdio test client for the second-opinion server."""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = "/home/hatch/workspace/second-opinion"
PARAMS = StdioServerParameters(
    command=f"{ROOT}/.venv/bin/python",
    args=[f"{ROOT}/server.py"],
    # MCP 2.x spawns servers with a minimal env allowlist by default;
    # pass the real env through (proxy vars etc.) like a real MCP host would.
    env=dict(os.environ),
)


async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "tools"
    async with stdio_client(PARAMS) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            if mode == "tools":
                tools = await s.list_tools()
                print(json.dumps([t.name for t in tools.tools], indent=2))
            elif mode == "models":
                r = await s.call_tool("list_models", {})
                print(json.dumps(r.content[0].text, indent=2)[:1500])
            elif mode == "live":
                r = await s.call_tool("second_opinion", {
                    "prompt": "Should a solo founder learn to code or hire a developer? Answer in 3 sentences.",
                    "max_tokens": 150,
                })
                out = json.loads(r.content[0].text)
                print("consultation_id:", out["consultation_id"])
                for t in out["takes"]:
                    body = t["take"] or f"ERROR: {t.get('error')}"
                    print(f"\n=== {t['name']} ===\n{body[:600]}")
                print("\n=== SYNTHESIS ===\n", out["synthesis"][:800])
            elif mode == "transcript":
                cid = sys.argv[2]
                r = await s.call_tool("get_transcript", {"consultation_id": cid})
                d = json.loads(r.content[0].text)
                print("transcript ok:", d["consultation_id"], "| takes:", len(d["takes"]),
                      "| synthesis chars:", len(d["synthesis"]))
            elif mode == "recent":
                r = await s.call_tool("list_consultations", {"limit": 5})
                print(r.content[0].text[:800])


asyncio.run(main())
