"""
web_ui/auth.py — Session authentication helpers.

Inactivity timeout: 5 minutes. Enforced manually on every protected request
so we can detect "timed out" vs "never logged in" without relying on cookie
expiry (which isn't precise enough for short timeouts).
"""

import time
from functools import wraps

from flask import jsonify, session

INACTIVITY_TIMEOUT_S: int = 300  # 5 minutes


def is_authenticated() -> bool:
    """Return True only if the session is valid and hasn't timed out."""
    if not session.get("authenticated"):
        return False
    last_activity = session.get("last_activity", 0)
    if time.time() - last_activity > INACTIVITY_TIMEOUT_S:
        session.clear()
        return False
    return True


def touch_session() -> None:
    """Refresh the inactivity timer to now."""
    session["last_activity"] = time.time()


def require_auth(f):
    """
    Route decorator: returns 401 JSON immediately if the caller is not
    authenticated. On success, refreshes the inactivity timer.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_authenticated():
            return jsonify({"error": "unauthorized"}), 401
        touch_session()
        return f(*args, **kwargs)
    return decorated
