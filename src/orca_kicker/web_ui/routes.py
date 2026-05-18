"""
web_ui/routes.py — All route definitions for the Orca Kicker control panel.

Route map:
    GET  /               → Main page (Tab 1 public, Tab 2 requires auth)
    GET  /restarting     → Branded spinner page; polls /health until server is back
    GET  /health         → Liveness check (used by restarting page)
    GET  /api/status     → Public: returns status.json contents as JSON
    GET  /api/auth-status → Public: returns {"authenticated": bool}
    POST /api/login      → Authenticate with supervisor password
    POST /api/logout     → Clear session
    GET  /api/config     → Auth-gated: returns whitelisted config values
    POST /api/config     → Auth-gated: saves whitelisted config values
    POST /api/restart    → Auth-gated: kills this process (systemd respawns it)
    POST /api/reboot     → Auth-gated: triggers OS-level reboot
"""

import json
import os
import signal
import subprocess
import threading
import time

import yaml
from flask import Flask, current_app, jsonify, render_template, request, session

from .auth import is_authenticated, require_auth, touch_session

# ── Whitelist ─────────────────────────────────────────────────────────────────
# Only these config keys may be read or written via the UI.
# All mechanical limits, GPIO pins, and autozero settings are SSH-only.
EDITABLE_PARAMS: frozenset = frozenset({
    "home_offset_um",
    "motion_extend_time_ms",
    "motion_return_time_ms",
    "extended_position_um",
})

# YAML section that contains all four editable parameters.
_MOTION_KEY = "motion"


def register_routes(app: Flask) -> None:  # noqa: C901

    # ── Health ────────────────────────────────────────────────────────────────

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    # ── Pages ─────────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return render_template("index.html", authenticated=is_authenticated())

    @app.route("/restarting")
    def restarting_page():
        restart_type = request.args.get("type", "restart")
        if restart_type == "reboot":
            title = "Rebooting…"
            message = (
                "The Raspberry Pi is rebooting. "
                "This page will automatically refresh when the system is back online (~45 seconds)."
            )
        else:
            title = "Restarting…"
            message = (
                "The kicker application is restarting. "
                "This page will automatically refresh when the application is back online (~5 seconds)."
            )
        return render_template("restarting.html", title=title, message=message)

    # ── Public status API ─────────────────────────────────────────────────────

    @app.route("/api/status")
    def api_status():
        path = current_app.config["STATUS_JSON_PATH"]
        try:
            with open(path) as f:
                data = json.load(f)
            return jsonify(data)
        except FileNotFoundError:
            return jsonify({
                "state": "UNKNOWN",
                "position_um": None,
                "last_kick_time_s": None,
                "last_error_msg": "status.json not found — kicker process may not be running",
                "timestamp": None,
            })
        except (json.JSONDecodeError, OSError) as exc:
            return jsonify({
                "state": "UNKNOWN",
                "position_um": None,
                "last_kick_time_s": None,
                "last_error_msg": f"Could not read status: {exc}",
                "timestamp": None,
            })

    # ── Auth ──────────────────────────────────────────────────────────────────

    @app.route("/api/auth-status")
    def api_auth_status():
        return jsonify({"authenticated": is_authenticated()})

    @app.route("/api/login", methods=["POST"])
    def api_login():
        data = request.get_json(silent=True) or {}
        password = data.get("password", "")
        correct  = current_app.config["SUPERVISOR_PASSWORD"]

        if not correct:
            return jsonify({"error": "No supervisor password is configured — access denied"}), 403
        if password != correct:
            # Constant-time comparison would be ideal; acceptable for LAN-only panel
            return jsonify({"error": "Incorrect password"}), 401

        session.clear()
        session["authenticated"]  = True
        session["last_activity"]  = time.time()
        return jsonify({"status": "ok"})

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        session.clear()
        return jsonify({"status": "ok"})

    # ── Config ────────────────────────────────────────────────────────────────

    @app.route("/api/config")
    @require_auth
    def api_config_get():
        config_path = current_app.config["CONFIG_PATH"]
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        motion = config.get(_MOTION_KEY) or {}
        result = {k: motion.get(k) for k in EDITABLE_PARAMS}
        return jsonify(result)

    @app.route("/api/config", methods=["POST"])
    @require_auth
    def api_config_post():
        data = request.get_json(silent=True) or {}

        # Reject any keys not on the whitelist
        unknown = set(data.keys()) - EDITABLE_PARAMS
        if unknown:
            return jsonify({
                "error": f"Unknown or locked parameters: {sorted(unknown)}"
            }), 400

        config_path = current_app.config["CONFIG_PATH"]
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        motion = config.setdefault(_MOTION_KEY, {})

        # Coerce types to match the existing YAML (int stays int, float stays float)
        for key, raw_value in data.items():
            existing = motion.get(key)
            try:
                if isinstance(existing, int):
                    motion[key] = int(raw_value)
                else:
                    motion[key] = float(raw_value)
            except (TypeError, ValueError) as exc:
                return jsonify({"error": f"Invalid value for '{key}': {exc}"}), 400

        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        return jsonify({
            "status": "saved",
            "message": "Changes saved. Restart the app for changes to take effect.",
        })

    # ── System controls ───────────────────────────────────────────────────────

    @app.route("/api/restart", methods=["POST"])
    @require_auth
    def api_restart():
        """
        Kill this process after the response is flushed.
        Relies on systemd Restart=always to respawn the application.
        """
        def _deferred_kill():
            time.sleep(0.4)   # Give the HTTP response time to flush
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=_deferred_kill, daemon=True).start()
        return jsonify({"status": "restarting"})

    @app.route("/api/reboot", methods=["POST"])
    @require_auth
    def api_reboot():
        """Trigger a full OS reboot via sudo reboot."""
        subprocess.Popen(["sudo", "reboot"])
        return jsonify({"status": "rebooting"})