"""Shared settings. Everything configurable lives in .env (see .env.example)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
except ImportError:  # dotenv is optional
    pass

ROOT = Path(__file__).parent

FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD") or None

# Each bank (tenant) gets its own isolated graph inside one FalkorDB instance.
DEFAULT_TENANT = os.getenv("TENANT", "demobank")
TENANTS = [t.strip() for t in os.getenv("TENANTS", "demobank,suraksha").split(",") if t.strip()]

# LLM: "anthropic", "openai" (any OpenAI-compatible endpoint) or "none" (offline playbook mode)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none").lower()
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or None
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")

# How agents reach the tools: "mcp" (spawns our MCP server over stdio) or "local" (direct import)
TOOL_TRANSPORT = os.getenv("TOOL_TRANSPORT", "mcp").lower()


def graph_name(tenant: str | None = None) -> str:
    return f"bank_{tenant or DEFAULT_TENANT}"


def connect():
    from falkordb import FalkorDB

    return FalkorDB(host=FALKORDB_HOST, port=FALKORDB_PORT, password=FALKORDB_PASSWORD)


def get_graph(tenant: str | None = None):
    """A fresh connection per caller, so concurrent agents never share a socket."""
    return connect().select_graph(graph_name(tenant))
