"""CSV logger for kick cycle data.

Fixed schema — do NOT change column names:
    epoch_s, phase, elapsed_s, position_um, velocity_um_s, event

- phase:  EXTEND | RETURN
- event:  KICK_TRIGGERED | PEAK_REACHED | HOME_REACHED | TIMEOUT | (empty)
"""

from __future__ import annotations

import csv
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Fixed column order — never change
CSV_COLUMNS = [
    "epoch_s",
    "phase",
    "elapsed_s",
    "position_um",
    "velocity_um_s",
    "event",
]


class CsvLogger:
    """Writes kick cycle telemetry rows to a CSV file."""

    def __init__(self, log_dir: str, csv_prefix: str) -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._path = self._log_dir / f"{csv_prefix}_{timestamp}.csv"

        self._file = self._path.open("w", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_COLUMNS)
        self._writer.writeheader()
        self._file.flush()

        self._start_s: float = time.monotonic()
        logger.info("CSV log: %s", self._path)

    def write(
        self,
        phase: str,
        position_um: int,
        velocity_um_s: float,
        event: Optional[str] = None,
    ) -> None:
        """Write one telemetry row.

        Args:
            phase:          'EXTEND' or 'RETURN'
            position_um:    current motor position in µm
            velocity_um_s:  current motor velocity in µm/s
            event:          optional event tag — one of the fixed event strings
        """
        self._writer.writerow(
            {
                "epoch_s": f"{time.time():.6f}",
                "phase": phase,
                "elapsed_s": f"{time.monotonic() - self._start_s:.6f}",
                "position_um": position_um,
                "velocity_um_s": f"{velocity_um_s:.2f}",
                "event": event or "",
            }
        )

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        try:
            self._file.flush()
            self._file.close()
        except Exception:
            pass
        logger.debug("CSV log closed: %s", self._path)
