"""Second Opinion — MCP server over stdio. Thin wrapper around core.py."""

from mcp.server.mcpserver import MCPServer

import core

mcp = MCPServer("second-opinion")


@mcp.tool()
def list_models() -> dict:
    """Curated frontier models you can put in the room, with friendly names."""
    return core.list_models()


@mcp.tool()
async def second_opinion(prompt: str, models: list[str] | None = None,
                         context: str | None = None,
                         max_tokens: int = 800) -> dict:
    """Ask rival frontier models for their take.

    prompt:  the question, artifact, or decision to get opinions on.
    models:  OpenRouter model ids to consult (default: the frontier panel).
             The caller chooses how many rivals are in the room.
    context: optional extra background the rivals should see.
    """
    result = await core.second_opinion(prompt, models, context, max_tokens)
    if result.get("error"):
        # Surface as a failed tool call, never a result with null takes.
        raise RuntimeError(result["error"])
    return result


@mcp.tool()
def get_transcript(consultation_id: str) -> dict:
    """Fetch the full stored transcript of a past consultation."""
    return core.get_transcript(consultation_id)


@mcp.tool()
def list_consultations(limit: int = 10) -> dict:
    """Recent consultations (id, time, prompt preview, models)."""
    return core.list_consultations(limit)


if __name__ == "__main__":
    mcp.run()
