"""Second Opinion core: no MCP dependency. Shared by the stdio MCP server
and the Vercel HTTP function."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import ssl
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
try:
    from dynamic_credentials import dynamic_credential_entry
except Exception:  # noqa: BLE001 - not in sandbox, use env key
    dynamic_credential_entry = None  # type: ignore[assignment]

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
APP_NAME = "Second Opinion/0.1"

FRONTIERS = {
    "openai/gpt-6-astra": "GPT-6 Astra",
    "openai/gpt-6-astra-pro": "GPT-6 Astra Pro",
    "anthropic/claude-fable-5.1": "Claude Fable",
    "google/gemini-3.8-flash": "Gemini",
    "x-ai/grok-4.6": "Grok",
}
DEFAULT_PANEL = [
    "openai/gpt-6-astra",
    "anthropic/claude-fable-5.1",
    "google/gemini-3.8-flash",
    "x-ai/grok-4.6",
]
SYNTH_MODEL = "anthropic/claude-haiku-4.5"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transcripts.db")


def _auth_headers() -> dict:
    if dynamic_credential_entry is not None:
        try:
            entry = dynamic_credential_entry("custom.openrouter")
            surrogate = str(entry["surrogate"]).strip()
            if entry.get("placement") == "bearer_header" and surrogate.startswith("hsurr:"):
                return {"Authorization": f"Bearer {surrogate}"}
        except Exception:
            pass
    env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env_key:
        return {"Authorization": f"Bearer {env_key}"}
    raise RuntimeError("no OpenRouter credential: authd surrogate unavailable "
                       "and OPENROUTER_API_KEY not set")


def _sync_client() -> httpx.Client:
    ctx = ssl.create_default_context()
    ca = "/usr/local/share/ca-certificates/hatch-egress-ca.crt"
    if os.path.exists(ca):
        ctx.load_verify_locations(cafile=ca)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    return httpx.Client(
        proxy=proxy or None,
        trust_env=False,
        verify=ctx,
        timeout=httpx.Timeout(120.0),
        headers={"HTTP-Referer": "https://second-opinion.local",
                 "X-Title": APP_NAME},
    )


def _complete_sync(model: str, messages: list, max_tokens: int) -> str:
    last: Exception | None = None
    for attempt in range(3):
        try:
            with _sync_client() as client:
                resp = client.post(
                    OPENROUTER_URL,
                    headers={**_auth_headers(), "Content-Type": "application/json"},
                    json={"model": model, "messages": messages,
                          "max_tokens": max_tokens},
                )
                resp.raise_for_status()
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = (msg.get("content") or msg.get("reasoning") or "").strip()
                if not content:
                    raise RuntimeError(f"{model} returned an empty response")
                return content
        except (httpx.TransportError, ssl.SSLError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{model} failed after 3 attempts: {last}")


async def _complete(model: str, messages: list, max_tokens: int = 800) -> str:
    return await asyncio.to_thread(_complete_sync, model, messages, max_tokens)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS consultations(
             id TEXT PRIMARY KEY, ts TEXT, prompt TEXT, context TEXT,
             models TEXT, takes TEXT, synthesis TEXT)"""
    )
    return conn


def _store(cid: str, prompt: str, context: str | None, models: list,
           takes: list, synthesis: str) -> None:
    """Best-effort: transcripts must never break a consultation (serverless
    disks are ephemeral)."""
    try:
        conn = _db()
        conn.execute(
            "INSERT INTO consultations VALUES (?,?,?,?,?,?,?)",
            (cid, datetime.now(timezone.utc).isoformat(), prompt, context or "",
             json.dumps(models), json.dumps(takes), synthesis),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# --- public API (used by both transports) -----------------------------------

def list_models() -> dict:
    return {"models": [{"id": mid, "name": name} for mid, name in FRONTIERS.items()],
            "default_panel": DEFAULT_PANEL}


async def second_opinion(prompt: str, models: list[str] | None = None,
                         context: str | None = None,
                         max_tokens: int = 800) -> dict:
    models = models or DEFAULT_PANEL
    unknown = [m for m in models if m not in FRONTIERS]
    if unknown:
        return {"error": f"unknown models: {unknown}",
                "known": list(FRONTIERS)}

    user_msg = prompt if not context else f"Context:\n{context}\n\nQuestion:\n{prompt}"
    messages = [
        {"role": "system",
         "content": "Give your honest, direct take. Be specific and concise. "
                    "Do not hedge or flatter. Do not truncate your answer."},
        {"role": "user", "content": user_msg},
    ]

    async def ask(model: str) -> dict:
        name = FRONTIERS[model]
        try:
            take = await _complete(model, messages, max_tokens)
            return {"model": model, "name": name, "take": take}
        except Exception as e:  # noqa: BLE001 - one rival failing must not kill the room
            return {"model": model, "name": name, "take": None, "error": str(e)[:300]}

    takes = await asyncio.gather(*[ask(m) for m in models])

    synthesis = ""
    good = [t for t in takes if t.get("take")]
    if len(good) >= 2:
        digest = "\n\n".join(f"--- {t['name']} ---\n{t['take']}" for t in good)
        try:
            synthesis = await _complete(
                SYNTH_MODEL,
                [{"role": "system",
                  "content": "You compare answers from rival AI models. Be blunt and brief."},
                 {"role": "user",
                  "content": "These rival models answered the same question. Summarize:\n"
                             "1) Where they AGREE (one or two lines)\n"
                             "2) Where they DISAGREE and how (one or two lines)\n"
                             "3) The single strongest point any of them made (one line)\n\n" + digest}],
                max_tokens=300,
            )
        except Exception:  # noqa: BLE001 - synthesis is a bonus, not the product
            synthesis = ""

    cid = uuid.uuid4().hex[:12]
    _store(cid, prompt, context, models, takes, synthesis)
    return {"consultation_id": cid, "takes": takes, "synthesis": synthesis}


def get_transcript(consultation_id: str) -> dict:
    try:
        conn = _db()
        row = conn.execute(
            "SELECT id, ts, prompt, context, models, takes, synthesis "
            "FROM consultations WHERE id = ?", (consultation_id,),
        ).fetchone()
        conn.close()
    except Exception:
        return {"error": "transcript store unavailable"}
    if not row:
        return {"error": "unknown consultation_id"}
    cid, ts, prompt, context, models, takes, synthesis = row
    return {"consultation_id": cid, "ts": ts, "prompt": prompt,
            "context": context, "models": json.loads(models),
            "takes": json.loads(takes), "synthesis": synthesis}


def list_consultations(limit: int = 10) -> dict:
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT id, ts, prompt, models FROM consultations "
            "ORDER BY ts DESC LIMIT ?", (max(1, min(limit, 50)),),
        ).fetchall()
        conn.close()
    except Exception:
        return {"consultations": []}
    return {"consultations": [
        {"consultation_id": r[0], "ts": r[1], "prompt": r[2][:120],
         "models": json.loads(r[3])} for r in rows]}
