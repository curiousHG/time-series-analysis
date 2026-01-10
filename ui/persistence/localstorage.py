import json
import streamlit as st
import streamlit.components.v1 as components


def load_from_local_storage(key: str, default):
    result = components.html(
        f"""
        <script>
        const data = localStorage.getItem("{key}");
        if (data) {{
            document.write(data);
        }}
        </script>
        """,
        height=0,
    )
    try:
        return json.loads(result)
    except Exception:
        return default


def save_to_local_storage(key: str, value):
    components.html(
        f"""
        <script>
        localStorage.setItem("{key}", {json.dumps(value)});
        </script>
        """,
        height=0,
    )
