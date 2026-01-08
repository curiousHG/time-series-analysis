import streamlit as st
from streamlit_cookies_controller import CookieController
from datetime import datetime, timedelta
import json

controller = CookieController()

COOKIE_DAYS = 180
COOKIE_VERSION = 1


def set_cookie(key: str, value):
    if not controller:
        return
    payload = {
        "v": COOKIE_VERSION,   # schema version
        "data": value,
    }
    try:
        controller.set(
            key,
            json.dumps(payload),
            expires=datetime.now() + timedelta(days=COOKIE_DAYS),
        )
    except Exception as e:
        st.error(f"Failed to set cookie {key} - {e}")


def get_cookie(key: str, default=None):
    if not controller:
        return default
    try:
        raw = controller.get(key)
    except Exception as e:
        st.error(f"Failed to get cookie: {key} - {e}")
        return default
    if not raw:
        return default

    try:
        payload = json.loads(raw)
        if payload.get("v") != COOKIE_VERSION:
            return default
        return payload.get("data", default)
    except Exception:
        return default
