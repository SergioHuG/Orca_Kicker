"""Main state machine and event loop for Orca Kicker.

State cycle:
    BOOT → AUTOZERO_HOME → IDLE → KICK → RETURN_HOME → IDLE
                             ↑              |
                             └──────────────┘
                       IDLE ↔ SLEEP (toggle via sleep button)

Rules:
    - client.run() MUST be called every loop tick.
    - While in SLEEP, ALL events except SLEEP_TOGGLE are drained and discarded.
    - Sleep indicator pin MUST be driven LOW on any exception or shutdown.
    - safe_stop() must never raise.
"""

from __future__ import annotations

import logging
import sys
import time
from enum import Enum, auto

from pyorcasdk import MotorMode

from orca_kicker.config import load_config
from orca_kicker.orca_client import OrcaClient, AUTO_ZERO_ERROR
from orca_kicker.triggers import TriggerQueue, TriggerType
from orca_kicker.gpio_inputs import GpioInputs
from orca_kicker.gpio_outputs import SleepIndicator
from orca_kicker.logging_csv import CsvLogger
from orca_kicker.kick_sequence import run_kick_cycle
import threading
import yaml
from orca_kicker.status_writer import StatusWriter, KickerStatus
from orca_kicker.web_ui import create_app

logger = logging.getLogger(__name__)


class Phase(Enum):
    BOOT = auto()
    AUTOZERO_HOME = auto()
    IDLE = auto()
    KICK = auto()
    SLEEP = auto()
    FAULT = auto()

_PHASE_TO_STATE = {
    Phase.BOOT:          "BOOT",
    Phase.AUTOZERO_HOME: "AUTOZERO",
    Phase.IDLE:          "IDLE",
    Phase.KICK:          "KICK",
    Phase.SLEEP:         "SLEEP",
    Phase.FAULT:         "ERROR",
}


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def run(config_path: str = "configs/default.yaml") -> None:
    _setup_logging()
    cfg = load_config(config_path)

    # ── Status writer (IPC to Flask UI) ───────────────────────────────────────
    with open(config_path) as _f:
        _web_ui_cfg = (yaml.safe_load(_f) or {}).get("web_ui", {})
    status_writer = StatusWriter(_web_ui_cfg.get("status_json_path", "status.json"))

    # ── Flask web UI (daemon thread — cannot affect motor control) ────────────
    def _run_web_ui() -> None:
        try:
            app = create_app(config_path)
            app.run(
                host=app.config["HOST"],
                port=app.config["PORT"],
                debug=False,
                use_reloader=False,   # MUST be False — reloader would fork the process
            )
        except Exception:
            logger.exception("Web UI thread crashed — continuing without UI.")

    threading.Thread(target=_run_web_ui, daemon=True, name="web-ui").start()
    logger.info("Web UI starting on http://0.0.0.0:%d", _web_ui_cfg.get("port", 5000))

    # ── Kick telemetry tracking ───────────────────────────────────────────────
    last_kick_time_s = None
    last_error_msg   = None


    loop_interval_s = 1.0 / cfg.loop.control_hz
    queue = TriggerQueue()
    sleep_indicator = SleepIndicator(cfg.gpio)
    gpio_inputs = GpioInputs(cfg.gpio, queue)
    client = OrcaClient(
        name="OrcaKicker",
        port=cfg.serial.port,
        baudrate=cfg.serial.baudrate,
        interframe_us=cfg.serial.interframe_us,
    )
    csv_logger = CsvLogger(
        log_dir=cfg.logging.log_dir,
        csv_prefix=cfg.logging.csv_prefix,
    ) if cfg.logging.enabled else None

    phase = Phase.BOOT
    home_um: int = 0

    try:
        client.open()
        client.wait_for_stream_alive(timeout_s=2.0)
        client.set_mode(MotorMode.KinematicMode)

        phase = Phase.AUTOZERO_HOME
        logger.info("Phase: AUTOZERO_HOME")
        _do_autozero_home(client, cfg, loop_interval_s)
        home_um = client.position_um or cfg.motion.home_offset_um
        _program_motions(client, cfg, home_um)

        phase = Phase.IDLE
        logger.info("Phase: IDLE (home_um=%d µm)", home_um)

        # --- Main event loop ---
        while True:
            client.run()
            events = queue.drain()

            # --- SLEEP guard: discard all events except SLEEP_TOGGLE ---
            if phase == Phase.SLEEP:
                for ev in events:
                    if ev.trigger_type == TriggerType.SLEEP_TOGGLE:
                        logger.info("Sleep toggle — waking up.")
                        sleep_indicator.off()
                        client.set_mode(MotorMode.KinematicMode)
                        _do_home(client, cfg, home_um, loop_interval_s)
                        phase = Phase.IDLE
                        logger.info("Phase: IDLE (woke from SLEEP)")
                    else:
                        logger.debug(
                            "Event %s discarded — in SLEEP", ev.trigger_type.name
                        )
                status_writer.write(KickerStatus(
                    state=_PHASE_TO_STATE[phase],
                    position_um=client.position_um,
                    last_kick_time_s=last_kick_time_s,
                    last_error_msg=None,
                    timestamp=time.time(),
                ))

                time.sleep(loop_interval_s)
                continue

            # --- Process events (IDLE only) ---
            for ev in events:
                if phase != Phase.IDLE:
                    logger.debug(
                        "Event %s discarded — phase=%s",
                        ev.trigger_type.name, phase.name,
                    )
                    continue

                if ev.trigger_type == TriggerType.SLEEP_TOGGLE:
                    logger.info("Phase: SLEEP")
                    client.set_mode(MotorMode.SleepMode)
                    sleep_indicator.on()
                    phase = Phase.SLEEP

                elif ev.trigger_type == TriggerType.AUTOZERO:
                    logger.info("Phase: AUTOZERO_HOME (manual trigger)")
                    phase = Phase.AUTOZERO_HOME
                    _do_autozero_home(client, cfg, loop_interval_s)
                    home_um = client.position_um or home_um
                    _program_motions(client, cfg, home_um)
                    phase = Phase.IDLE
                    logger.info("Phase: IDLE (home_um=%d µm)", home_um)

                elif ev.trigger_type == TriggerType.KICK:
                    logger.info("Phase: KICK")
                    phase = Phase.KICK
                    result = run_kick_cycle(
                        client=client,
                        csv_logger=csv_logger,
                        motion=cfg.motion,
                        timeouts=cfg.timeouts,
                        tolerances=cfg.tolerances,
                        home_um=home_um,
                        loop_interval_s=loop_interval_s,
                    )
                    if not result.ok:
                        logger.error(
                            "Kick cycle failed: %s — entering FAULT", result.reason
                        )
                        last_error_msg = result.reason
                        phase = Phase.FAULT
                        break
                    last_kick_time_s = time.time()
                    phase = Phase.IDLE
                    logger.info(
                        "Phase: IDLE (kick done in %.3f s)", result.cycle_time_s
                    )

            if phase == Phase.FAULT:
                break

            status_writer.write(KickerStatus(
                state=_PHASE_TO_STATE[phase],
                position_um=client.position_um,
                last_kick_time_s=last_kick_time_s,
                last_error_msg=last_error_msg if phase == Phase.FAULT else None,
                timestamp=time.time(),
            ))

            time.sleep(loop_interval_s)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — shutting down.")
    except Exception as exc:
        logger.exception("Unhandled exception: %s", exc)
        phase = Phase.FAULT
    finally:
        status_writer.write(KickerStatus(
            state="OFFLINE",
            position_um=None,
            last_kick_time_s=last_kick_time_s,
            last_error_msg=last_error_msg,
            timestamp=time.time(),
        ))
        client.safe_stop()
        sleep_indicator.off()   # MUST be driven LOW on any exit
        sleep_indicator.close()
        gpio_inputs.close()
        if csv_logger is not None:    
            csv_logger.close()
        client.close()
        logger.info("Shutdown complete.")

    if phase == Phase.FAULT:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _do_autozero_home(client: OrcaClient, cfg, loop_interval_s: float) -> None:
    logger.info("AutoZero starting...")
    client.start_autozero()

    # Give the motor time to actually enter AutoZeroMode before polling
    time.sleep(0.3)

    # Use wait_until_not_mode — polls until mode changes away from AutoZeroMode
    client.wait_until_not_mode(
        MotorMode.AutoZeroMode,
        timeout_s=cfg.timeouts.autozero_timeout_s,
    )

    if client.autozero_failed():
        raise RuntimeError("AutoZero failed — error bit 8192 set.")

    logger.info("AutoZero complete — mode is now %s", client.get_mode())

    pos_after_um = client.get_position_um_blocking()
    logger.info("Post-AutoZero position: %d µm", pos_after_um)

    client.set_mode(MotorMode.KinematicMode)
    time.sleep(0.1)

    home_um = pos_after_um + cfg.motion.home_offset_um
    _program_home_motion(client, cfg, home_um)

    logger.info("Moving to home position (home_um=%d µm)...", home_um)
    _do_home(client, cfg, home_um, loop_interval_s)

    # Read position after autozero (blocking read for accuracy)
    pos_after_um = client.get_position_um_blocking()
    logger.info("Post-AutoZero position: %d µm", pos_after_um)

    # Manually enter KinematicMode (motor may have exited to SleepMode)
    client.set_mode(MotorMode.KinematicMode)
    time.sleep(0.1)

    # Program HOME motion slot before triggering it
    home_um = pos_after_um + cfg.motion.home_offset_um
    _program_home_motion(client, cfg, home_um)

    logger.info("Moving to home position (home_um=%d µm)...", home_um)
    _do_home(client, cfg, home_um, loop_interval_s)


def _do_home(client: OrcaClient, cfg, home_um: int, loop_interval_s: float) -> None:
    """Trigger home motion and wait for arrival."""
    client.trigger_kinematic_motion(cfg.motion.motion_home_id)
    deadline = time.monotonic() + cfg.timeouts.home_timeout_s
    while time.monotonic() < deadline:
        client.run()
        pos = client.position_um
        if pos is not None and abs(pos - home_um) <= cfg.tolerances.home_tol_um:
            logger.info("Home reached at %d µm", pos)
            return
        time.sleep(loop_interval_s)
    raise RuntimeError(
        f"Home move timed out after {cfg.timeouts.home_timeout_s:.0f} s."
    )


def _program_home_motion(client: OrcaClient, cfg, home_um: int) -> None:
    """Programme only the HOME motion slot (ID 0). Called right after AutoZero."""
    m = cfg.motion
    client.set_kinematic_motion(
        id=m.motion_home_id,
        position=home_um,
        time_ms=m.motion_home_time_ms,
        delay=0,
        type=m.kin_type_min_jerk,
        auto_next=False,
        next_id=-1,
    )


def _program_motions(client: OrcaClient, cfg, home_um: int) -> None:
    """Programme all three kinematic motion slots after autozero + home."""
    m = cfg.motion

    # ID 0 — HOME
    client.set_kinematic_motion(
        id=m.motion_home_id,
        position=home_um,
        time_ms=m.motion_home_time_ms,
        delay=0,
        type=m.kin_type_min_jerk,
        auto_next=False,
        next_id=-1,
    )
    # ID 1 — EXTEND (chains to RETURN automatically via firmware)
    client.set_kinematic_motion(
        id=m.motion_extend_id,
        position=home_um + m.extended_position_um,
        time_ms=m.motion_extend_time_ms,
        delay=0,
        type=m.kin_type_min_jerk,
        auto_next=True,
        next_id=m.motion_return_id,
    )
    # ID 2 — RETURN (chained by firmware — never triggered manually)
    client.set_kinematic_motion(
        id=m.motion_return_id,
        position=home_um,
        time_ms=m.motion_return_time_ms,
        delay=0,
        type=m.kin_type_min_jerk,
        auto_next=False,
        next_id=-1,
    )
    logger.info("All kinematic motions programmed (home_um=%d µm)", home_um)
