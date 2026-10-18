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