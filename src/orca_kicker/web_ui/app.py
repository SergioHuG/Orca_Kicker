"""
web_ui/app.py — Flask application factory.

Usage (from repo root):
    from orca_kicker.web_ui import create_app
    app = create_app("configs/default.yaml")
    app.run(host=app.config["HOST"], port=app.config["PORT"])
"""

import os

import yaml
from flask import Flask

from .routes import register_routes


def create_app(config_path: str = "configs/default.yaml") -> Flask:
    """Build and return the configured Flask application."""

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # ── Load main config ──────────────────────────────────────────────────────
    with open(config_path) as f:
        main_config = yaml.safe_load(f) or {}

    web_cfg = main_config.get("web_ui", {})

    app.config["HOST"]             = web_cfg.get("host", "0.0.0.0")
    app.config["PORT"]             = int(web_cfg.get("port", 5000))
    app.config["STATUS_JSON_PATH"] = web_cfg.get("status_json_path", "status.json")
    app.config["CONFIG_PATH"]      = config_path

    # ── Load secrets ──────────────────────────────────────────────────────────
    secrets_path = web_cfg.get("secrets_path", "configs/secrets.yaml")
    supervisor_password = ""
    if os.path.exists(secrets_path):
        with open(secrets_path) as f:
            secrets = yaml.safe_load(f) or {}
        supervisor_password = secrets.get("supervisor_password", "")

    app.config["SUPERVISOR_PASSWORD"] = supervisor_password
    app.config["SECRETS_PATH"]        = secrets_path

    # ── Session ───────────────────────────────────────────────────────────────
    # Random key: sessions are intentionally invalidated on every app restart.
    # Inactivity timeout is enforced manually in auth.py, not via Flask's
    # PERMANENT_SESSION_LIFETIME, so we can distinguish "expired" from "never logged in".
    app.secret_key = os.urandom(32)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    register_routes(app)
    return app
