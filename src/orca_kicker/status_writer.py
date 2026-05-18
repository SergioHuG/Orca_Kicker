"""
status_writer.py — Kicker → Flask IPC via status.json

The kicker main loop calls StatusWriter.write() every tick.
Flask reads the file via GET /api/status.
Fully decoupled: a Flask crash cannot affect motor control.
Writes are atomic (write-to-temp + os.replace) to prevent Flask
from ever reading a partial / torn JSON file.
"""

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class KickerStatus:
    # Current state machine state
    state: str                        # IDLE | KICK | SLEEP | ERROR | AUTOZERO | HOMING

    # Actuator telemetry
    position_um: Optional[float]      # Current actuator position in micrometres

    # Kick history
    last_kick_time_s: Optional[float] # Unix timestamp of last completed kick; None before first kick

    # Error detail — populated when state == ERROR, None otherwise
    last_error_msg: Optional[str]

    # Wall-clock timestamp of this write (Unix seconds, float)
    timestamp: float


class StatusWriter:
    """Writes KickerStatus atomically to a JSON file for the Flask UI to read."""

    def __init__(self, path: str) -> None:
        self._path = os.path.abspath(path)
        self._dir = os.path.dirname(self._path)

    def write(self, status: KickerStatus) -> None:
        """
        Atomically write *status* to disk.

        Safe to call from the main kicker loop. Silently swallows I/O
        errors so a filesystem problem never raises into motor-control code.
        """
        payload = asdict(status)
        fd, tmp_path = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_path, self._path)
        except Exception:           # noqa: BLE001
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            # Do NOT re-raise — status write failure must never abort motor control
