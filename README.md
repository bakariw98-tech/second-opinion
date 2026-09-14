# Second Opinion

Ask the rival frontiers. An MCP server that fans a prompt out to competitor
frontier models via OpenRouter and brings their takes back into whatever
agent called it.

## Tools

- **`second_opinion(prompt, models?, context?, max_tokens?)`** — put rivals
  in the room. `models` is a list of OpenRouter ids; the caller chooses how
  many. Defaults to the frontier panel: GPT-6 Astra, Claude Fable, Gemini,
  Grok. Returns each rival's take plus a short synthesis (where they agree,
  where they disagree, strongest single point). The consultation is stored;
  you get a `consultation_id` back.
- **`get_transcript(consultation_id)`** — full transcript of a past
  consultation. Only surfaces when asked for.
- **`list_consultations(limit?)`** — recent consultations.
- **`list_models()`** — the curated frontier panel with friendly names.

## Run it

```bash
./.venv/bin/python server.py   # MCP over stdio
```

Wire into an MCP host (Claude / ChatGPT custom connector):

```json
{
  "mcpServers": {
    "second-opinion": {
      "command": "/home/hatch/workspace/second-opinion/.venv/bin/python",
      "args": ["/home/hatch/workspace/second-opinion/server.py"]
    }
  }
}
```

## Auth

The server never sees a raw key. In this sandbox it uses the stored
`custom.openrouter` credential via authd surrogates. Anywhere else, set:

```bash
export OPENROUTER_API_KEY=...
```

## Transcripts

Stored in `transcripts.db` (SQLite, same directory). Visible only via
`get_transcript` / `list_consultations` — nothing is pushed anywhere.

## Notes

- Built on MCP Python SDK 2.x (`mcp.server.mcpserver.MCPServer`).
- One rival failing never kills the room; its take comes back as an error.
- The synthesis is a bonus — if it fails, the raw takes still return.
