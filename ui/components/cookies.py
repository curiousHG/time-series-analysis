import streamlit as st
from streamlit_cookies_controller import CookieController
from datetime import datetime, timedelta
import json

controller = CookieController()

COOKIE_DAYS = 180
COOKIE_VERSION = 1


def set_cookie(key: str, value):
    payload = {
        "v": COOKIE_VERSION,   # schema version
        "data": value,
    }

    controller.set(
        key,
        json.dumps(payload),
        expires=datetime.now() + timedelta(days=COOKIE_DAYS),
    )


def get_cookie(key: str, default=None):
    raw = controller.get(key)
    if not raw:
        return default

    try:
        payload = json.loads(raw)
        if payload.get("v") != COOKIE_VERSION:
            return default
        return payload.get("data", default)
    except Exception:
        return default
