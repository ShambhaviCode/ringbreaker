
"""Shared settings for RingBreaker."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

ROOT = Path(__file__).parent


def setting(name: str, default=None):
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets.get(name, default)
    except Exception:
        return default


FALKORDB_HOST = setting("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(setting("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = setting("FALKORDB_PASSWORD") or None

DEFAULT_TENANT = setting("TENANT", "demobank")
TENANTS = [
    t.strip()
    for t in setting("TENANTS", "demobank,suraksha").split(",")
    if t.strip()
]

LLM_PROVIDER = setting("LLM_PROVIDER", "none").lower()
LLM_MODEL = setting("LLM_MODEL", "")
LLM_BASE_URL = setting("LLM_BASE_URL") or None
LLM_API_KEY = (
    setting("LLM_API_KEY")
    or setting("ANTHROPIC_API_KEY")
    or setting("OPENAI_API_KEY")
)
TOOL_TRANSPORT = setting("TOOL_TRANSPORT", "mcp").lower()


def graph_name(tenant: str | None = None) -> str:
    return f"bank_{tenant or DEFAULT_TENANT}"


def connect():
    from falkordb import FalkorDB
    return FalkorDB(
        host=FALKORDB_HOST,
        port=FALKORDB_PORT,
        password=FALKORDB_PASSWORD,
    )


def get_graph(tenant: str | None = None):
    return connect().select_graph(graph_name(tenant))

