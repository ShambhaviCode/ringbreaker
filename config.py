from pathlib import Path
import os

def setting(name: str, default=None):
    import os

    value = os.getenv(name)
    if value is not None:
        return value

    try:
        import streamlit as st
        value = st.secrets.get(name, default)
        return value
    except Exception:
        return default
ROOT = Path(__file__).parent


def connect():
    from falkordb import FalkorDB
    return FalkorDB(
        host=setting("FALKORDB_HOST", "localhost"),
        port=int(setting("FALKORDB_PORT", 6379)),
        username=setting("FALKORDB_USER", "falkordb"),
        password=setting("FALKORDB_PASSWORD", "Dramarkga266@66"),
    )

def graph_name(tenant: str) -> str:
    return f"bank_{tenant}"

