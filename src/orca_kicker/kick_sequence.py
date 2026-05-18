"""Kick motion helpers — extend and return triggers.

The full kick cycle is now driven by the main loop state machine.
These helpers encapsulate the motor calls only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from orca_kicker.orca_client import OrcaClient
from orca_kicker.config import MotionConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KickCycleResult:
    ok: bool
    reason: str          # "COMPLETED" | "TIMEOUT" | "COMM_ERROR"
    cycle_time_s: float
    peak_position_um: int


def trigger_extend(client: OrcaClient, motion: MotionConfig) -> bool:
    """Trigger the EXTEND motion (ID 1). Returns True on success."""
    try:
        client.trigger_kinematic_motion(motion.motion_extend_id)
        logger.info("EXTEND triggered (motion ID %d)", motion.motion_extend_id)
        return True
    except Exception as exc:
        logger.error("Failed to trigger EXTEND: %s", exc)
        return False


def trigger_return(client: OrcaClient, motion: MotionConfig) -> bool:
    """Trigger the RETURN motion (ID 2). Returns True on success."""
    try:
        client.trigger_kinematic_motion(motion.motion_return_id)
        logger.info("RETURN triggered (motion ID %d)", motion.motion_return_id)
        return True
    except Exception as exc:
        logger.error("Failed to trigger RETURN: %s", exc)
        return False