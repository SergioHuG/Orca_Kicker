"""Kick cycle sequence — replaces apply_sequence.py from V1.

Executes one full kick cycle (EXTEND → firmware chains RETURN)
and polls until the motor returns home.

Returns a KickCycleResult. Never raises — all errors are captured in result.

Rules:
- Python triggers motion ID 1 (EXTEND) only.
- ID 2 (RETURN) is chained by firmware via auto_next — never triggered here.
- Polls position_um from stream cache.
- StreamData has no velocity field — velocity is computed from position delta.
- Timeout → safe_stop() → result with reason=TIMEOUT.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from orca_kicker.orca_client import OrcaClient
from orca_kicker.logging_csv import CsvLogger
from orca_kicker.config import MotionConfig, TimeoutsConfig, TolerancesConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KickCycleResult:
    ok: bool
    reason: str          # "COMPLETED" | "TIMEOUT" | "COMM_ERROR"
    cycle_time_s: float
    peak_position_um: int


def run_kick_cycle(
    client: OrcaClient,
    csv_logger: Optional[CsvLogger],
    motion: MotionConfig,
    timeouts: TimeoutsConfig,
    tolerances: TolerancesConfig,
    home_um: int,
    loop_interval_s: float,
) -> KickCycleResult:
    """Execute one full kick cycle and return the result.

    Args:
        client:          OrcaClient (only caller of pyorcasdk)
        csv_logger:      CsvLogger — receives telemetry rows
        motion:          motion config section
        timeouts:        timeout config section
        tolerances:      tolerance config section
        home_um:         current home position in µm (set after autozero)
        loop_interval_s: control loop tick interval in seconds

    Returns:
        KickCycleResult
    """
    start_s = time.monotonic()
    deadline_s = start_s + timeouts.kick_timeout_s
    peak_position_um: int = home_um

    # Velocity is not in StreamData — compute from position delta
    prev_pos: Optional[int] = None
    prev_t: float = start_s
    vel: float = 0.0

    # --- Trigger EXTEND (firmware chains RETURN automatically) ---
    try:
        client.trigger_kinematic_motion(motion.motion_extend_id)
    except Exception as exc:
        logger.error("Failed to trigger EXTEND motion: %s", exc)
        return KickCycleResult(
            ok=False,
            reason="COMM_ERROR",
            cycle_time_s=time.monotonic() - start_s,
            peak_position_um=peak_position_um,
        )
    if csv_logger is not None:
        csv_logger.write(
            phase="EXTEND",
            position_um=home_um,
            velocity_um_s=0.0,
            event="KICK_TRIGGERED",
        )
    logger.info("Kick triggered — EXTEND motion ID %d", motion.motion_extend_id)

    # --- Poll until home or timeout ---
    phase = "EXTEND"
    peak_logged = False

    while True:
        client.run()
        now = time.monotonic()

        # --- Timeout check ---
        if now >= deadline_s:
            logger.error("Kick cycle TIMEOUT after %.2f s", timeouts.kick_timeout_s)
            client.safe_stop()
            if csv_logger is not None:    
                csv_logger.write(
                    phase=phase,
                    position_um=peak_position_um,
                    velocity_um_s=0.0,
                    event="TIMEOUT",
                )
                csv_logger.flush()
            return KickCycleResult(
                ok=False,
                reason="TIMEOUT",
                cycle_time_s=now - start_s,
                peak_position_um=peak_position_um,
            )

        pos = client.position_um
        if pos is None:
            time.sleep(loop_interval_s)
            continue

        # --- Compute velocity from position delta ---
        dt = now - prev_t
        if prev_pos is not None and dt > 0:
            vel = (pos - prev_pos) / dt
        prev_pos = pos
        prev_t = now

        # --- Track peak ---
        if pos > peak_position_um:
            peak_position_um = pos

        # --- Phase transition: EXTEND → RETURN ---
        # Heuristic: once past the midpoint we're in return territory
        midpoint_um = home_um + motion.extended_position_um // 2
        if phase == "EXTEND" and pos >= midpoint_um:
            if not peak_logged:
                if csv_logger is not None:
                    csv_logger.write(
                        phase="EXTEND",
                        position_um=pos,
                        velocity_um_s=vel,
                        event="PEAK_REACHED",
                    )
                peak_logged = True
                phase = "RETURN"
                logger.debug("Phase → RETURN (pos=%d µm)", pos)

        # --- Log telemetry row ---
        if csv_logger is not None:
            csv_logger.write(
                phase=phase,
                position_um=pos,
                velocity_um_s=vel,
            )

        # --- Home reached check ---
        if phase == "RETURN" and abs(pos - home_um) <= tolerances.home_tol_um:
            cycle_time_s = time.monotonic() - start_s
            if csv_logger is not None:
                csv_logger.write(
                    phase="RETURN",
                    position_um=pos,
                    velocity_um_s=vel,
                    event="HOME_REACHED",
                )
                csv_logger.flush()
                logger.info(
                    "Kick cycle COMPLETED in %.3f s, peak=%d µm",
                    cycle_time_s,
                    peak_position_um,
                )
            return KickCycleResult(
                ok=True,
                reason="COMPLETED",
                cycle_time_s=cycle_time_s,
                peak_position_um=peak_position_um,
            )

        time.sleep(loop_interval_s)
