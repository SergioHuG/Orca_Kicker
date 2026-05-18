/**
 * app.js — Orca Kicker Control Panel
 *
 * Responsibilities:
 *   - Poll /api/status every 500 ms, update Tab 1 state badge + telemetry
 *   - Tab switching (preserves auth check on Tab 2 open)
 *   - Login / logout flow
 *   - Config load and save
 *   - Confirmation dialog for Restart / Reboot
 *   - Navigate to /restarting page before server goes down
 */

"use strict";

// ── Constants ────────────────────────────────────────────────────────────────

var POLL_INTERVAL_MS = 500;

var STATE_CSS = {
    IDLE:     "state-idle",
    KICK:     "state-kick",
    SLEEP:    "state-sleep",
    ERROR:    "state-error",
    AUTOZERO: "state-autozero",
    HOMING:   "state-homing",
};

var EDITABLE_PARAMS = [
    "home_offset_um",
    "extended_position_um",
    "motion_extend_time_ms",
    "motion_return_time_ms",
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatTimestamp(unixSec) {
    if (unixSec === null || unixSec === undefined) return "—";
    var d = new Date(unixSec * 1000);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function el(id) { return document.getElementById(id); }

// ── Status polling ───────────────────────────────────────────────────────────

function updateStatusUI(data) {
    var state = (data.state || "UNKNOWN").toUpperCase();
    var badge = el("state-badge");

    // Badge text + colour class
    badge.textContent = state;
    badge.className   = "state-badge " + (STATE_CSS[state] || "state-unknown");

    // Position
    el("position-val").textContent =
        (data.position_um !== null && data.position_um !== undefined)
            ? Number(data.position_um).toFixed(1) + " µm"
            : "—";

    // Last kick
    el("last-kick-val").textContent = formatTimestamp(data.last_kick_time_s);

    // Error row — only visible in ERROR state
    var errorRow = el("error-row");
    if (state === "ERROR" && data.last_error_msg) {
        el("error-val").textContent = data.last_error_msg;
        errorRow.classList.remove("hidden");
    } else {
        errorRow.classList.add("hidden");
    }
}

function pollStatus() {
    fetch("/api/status", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(updateStatusUI)
        .catch(function () {
            // Server unreachable — show dash, keep polling
            var badge = el("state-badge");
            badge.textContent = "—";
            badge.className   = "state-badge state-unknown";
        })
        .finally(function () {
            setTimeout(pollStatus, POLL_INTERVAL_MS);
        });
}

// ── Tab switching ─────────────────────────────────────────────────────────────

function switchTab(name) {
    document.querySelectorAll(".tab-btn").forEach(function (btn) {
        var active = btn.dataset.tab === name;
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".tab-pane").forEach(function (pane) {
        pane.classList.toggle("hidden", pane.id !== "tab-" + name);
    });

    if (name === "config") {
        // Always re-check auth when the supervisor tab is opened
        refreshConfigTab();
    }
}

// ── Auth ──────────────────────────────────────────────────────────────────────

function refreshConfigTab() {
    fetch("/api/auth-status", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.authenticated) {
                showConfigSection();
                loadConfig();
            } else {
                showLoginSection();
            }
        });
}

function showLoginSection() {
    el("login-section").classList.remove("hidden");
    el("config-section").classList.add("hidden");
}

function showConfigSection() {
    el("login-section").classList.add("hidden");
    el("config-section").classList.remove("hidden");
}

function doLogin() {
    var password = el("password-input").value;
    var errEl    = el("login-error");
    errEl.classList.add("hidden");

    fetch("/api/login", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ password: password }),
    })
    .then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, data: data }; });
    })
    .then(function (result) {
        if (result.ok) {
            el("password-input").value = "";
            showConfigSection();
            loadConfig();
        } else {
            errEl.textContent = result.data.error || "Login failed";
            errEl.classList.remove("hidden");
        }
    });
}

function doLogout() {
    fetch("/api/logout", { method: "POST" }).then(showLoginSection);
}

// ── Config ────────────────────────────────────────────────────────────────────

function loadConfig() {
    fetch("/api/config", { cache: "no-store" })
        .then(function (r) {
            if (r.status === 401) { showLoginSection(); return null; }
            return r.json();
        })
        .then(function (data) {
            if (!data) return;
            EDITABLE_PARAMS.forEach(function (key) {
                var input = el(key);
                if (input && data[key] !== null && data[key] !== undefined) {
                    input.value = data[key];
                }
            });
        });
}

function saveConfig() {
    var payload = {};
    EDITABLE_PARAMS.forEach(function (key) {
        var input = el(key);
        if (input) payload[key] = parseFloat(input.value);
    });

    var msgEl = el("save-msg");
    msgEl.textContent = "Saving…";
    msgEl.className   = "save-msg";
    msgEl.classList.remove("hidden");

    fetch("/api/config", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(payload),
    })
    .then(function (r) {
        return r.json().then(function (data) { return { ok: r.ok, data: data }; });
    })
    .then(function (result) {
        if (result.ok) {
            msgEl.textContent = result.data.message || "Changes saved. Restart the app for changes to take effect.";
            msgEl.className   = "save-msg success";
        } else {
            msgEl.textContent = result.data.error || "Save failed";
            msgEl.className   = "save-msg err";
        }
    });
}

// ── Confirmation dialog ───────────────────────────────────────────────────────

var _dialogCallback = null;

function showDialog(title, msg, confirmLabel, confirmClass, callback) {
    el("dialog-title").textContent   = title;
    el("dialog-msg").textContent     = msg;
    var btn = el("dialog-confirm-btn");
    btn.textContent = confirmLabel;
    btn.className   = "btn " + confirmClass;
    _dialogCallback = callback;
    el("confirm-overlay").classList.remove("hidden");
}

function closeDialog() {
    el("confirm-overlay").classList.add("hidden");
    _dialogCallback = null;
}

// ── System controls ───────────────────────────────────────────────────────────

function confirmRestart() {
    showDialog(
        "Restart Application",
        "This will restart the kicker application. Motor control will be interrupted for approximately 5 seconds.",
        "Restart",
        "btn-warning",
        function () {
            fetch("/api/restart", { method: "POST" }).then(function () {
                window.location.replace("/restarting?type=restart");
            });
        }
    );
}

function confirmReboot() {
    showDialog(
        "Full System Reboot",
        "This will reboot the Raspberry Pi. The system will be unavailable for approximately 45 seconds.",
        "Reboot",
        "btn-danger",
        function () {
            fetch("/api/reboot", { method: "POST" }).then(function () {
                window.location.replace("/restarting?type=reboot");
            });
        }
    );
}

// ── DOM-ready wiring ──────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", function () {
    // Password field — submit on Enter
    el("password-input").addEventListener("keydown", function (e) {
        if (e.key === "Enter") doLogin();
    });

    // Dialog confirm button
    el("dialog-confirm-btn").addEventListener("click", function () {
        var cb = _dialogCallback;
        closeDialog();
        if (cb) cb();
    });

    // Close dialog on backdrop click
    el("confirm-overlay").addEventListener("click", function (e) {
        if (e.target === el("confirm-overlay")) closeDialog();
    });

    // Close dialog on Escape
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") closeDialog();
    });
});

// ── Boot ──────────────────────────────────────────────────────────────────────

pollStatus();   // Begin status polling immediately
