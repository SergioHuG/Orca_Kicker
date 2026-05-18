"""Orca motor client — the ONLY file allowed to call pyorcasdk.

All motor communication goes through this module.
No other module may import or call pyorcasdk directly.

API source: pyorcasdk official docs + orca_applicator_v1.

Key facts:
- Actuator(name) constructor
- open_serial_port(port, baudrate, interframe_us) — returns OrcaError
- close_serial_port() — exists, must call on shutdown
- clear_errors() + enable_stream() required after open
- run() must be called every loop tick
- get_stream_data() → StreamData: position, force, power, voltage, temperature, errors
  NOTE: StreamData has NO velocity field — compute from position delta if needed
- get_mode() → OrcaResultMotorMode — unwrap with .value
- AutoZero is register-based: write ZERO_MODE, AUTO_ZERO_FORCE_N,
  AUTO_ZERO_SPEED_MMPS, AUTO_ZERO_EXIT_MODE, then set_mode(MotorMode.AutoZeroMode)
- set_kinematic_motion auto_next takes int (0 or 1), not bool
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from pyorcasdk import Actuator, MotorMode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AutoZero register addresses (from pyorcasdk tutorial)
# ---------------------------------------------------------------------------
_ZERO_MODE              = 171
_AUTO_ZERO_FORCE_N      = 172
_AUTO_ZERO_EXIT_MODE    = 173
_AUTO_ZERO_SPEED_MMPS   = 177

_ZERO_MODE_ENABLED      = 2      # Auto Zero enabled (started by set_mode)
AUTO_ZERO_ERROR         = 8192   # Error bitmask: AutoZero failed


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OrcaError(RuntimeError):
    """Base exception for Orca client errors."""


class OrcaCommError(OrcaError):
    """Serial/transport or communication related errors."""


class OrcaDeviceError(OrcaError):
    """Device returned an error, fault, or unexpected state."""


def _err_to_str(err) -> str:
    try:
        return err.what()
    except Exception:
        return str(err)


def _raise_if_err(err, label: str) -> None:
    if err:
        raise OrcaDeviceError(f"{label}: {_err_to_str(err)}")


# ---------------------------------------------------------------------------
# Stream snapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StreamSnapshot:
    """Typed snapshot of the SDK stream cache.

    NOTE: StreamData has no velocity field. velocity_um_s is computed
    externally from position delta if needed.
    """
    t_epoch_s: float
    position_um: int
    force_mN: int
    power: int
    voltage: int
    temperature: int
    errors: int


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OrcaClient:
    """Thin wrapper around pyorcasdk.Actuator for the kicker application.

    Typical startup sequence:
        client = OrcaClient("OrcaKicker", port, baudrate, interframe_us)
        client.open()                    # open_serial_port + clear_errors + enable_stream
        client.set_mode(MotorMode.KinematicMode)
        client.start_autozero(exit_mode=MotorMode.KinematicMode)
        # poll get_mode() until != MotorMode.AutoZeroMode
        # program motions, trigger kicks, etc.
        client.close()
    """

    def __init__(self, name: str, port: str, baudrate: int, interframe_us: int) -> None:
        self._name = name
        self._port = port
        self._baudrate = baudrate
        self._interframe_us = interframe_us
        self._motor = Actuator(name)
        self._is_open = False
        logger.info("OrcaClient '%s' created for %s @ %d baud", name, port, baudrate)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open serial port, clear errors, enable stream."""
        err = self._motor.open_serial_port(self._port, self._baudrate, self._interframe_us)
        if err:
            raise OrcaCommError(f"open_serial_port failed: {_err_to_str(err)}")
        self._is_open = True
        logger.info("Serial port opened: %s", self._port)
        self.clear_errors()
        self.enable_stream()

    def close(self) -> None:
        """Close the serial port."""
        if self._is_open:
            try:
                self._motor.close_serial_port()
                logger.debug("Serial port closed.")
            except Exception as exc:
                logger.error("close_serial_port() raised (suppressed): %s", exc)
        self._is_open = False

    def clear_errors(self) -> None:
        """Clear device errors. Call after open."""
        try:
            self._motor.clear_errors()
            logger.debug("clear_errors() OK")
        except Exception as ex:
            raise OrcaDeviceError(f"clear_errors failed: {ex}") from ex

    def enable_stream(self) -> None:
        """Enable device streaming so get_stream_data() updates on run()."""
        try:
            self._motor.enable_stream()
            logger.debug("enable_stream() OK")
        except Exception as ex:
            raise OrcaDeviceError(f"enable_stream failed: {ex}") from ex

    # ------------------------------------------------------------------
    # Core loop step — MUST be called every tick
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Refresh SDK stream cache. Must be called every control loop tick."""
        try:
            self._motor.run()
        except Exception as ex:
            raise OrcaCommError(f"motor.run() failed: {ex}") from ex

    # ------------------------------------------------------------------
    # Stream cache reads (non-blocking)
    # ------------------------------------------------------------------

    def read_stream_cache(self) -> StreamSnapshot:
        """Read cached stream values. Assumes run() is called regularly."""
        sd = self._motor.get_stream_data()
        return StreamSnapshot(
            t_epoch_s=time.time(),
            position_um=int(sd.position),
            force_mN=int(sd.force),
            power=int(sd.power),
            voltage=int(sd.voltage),
            temperature=int(sd.temperature),
            errors=int(sd.errors),
        )

    def _stream_tuple(self) -> Tuple:
        sd = self._motor.get_stream_data()
        return (int(sd.position), int(sd.force), int(sd.power),
                int(sd.voltage), int(sd.temperature), int(sd.errors))

    def wait_for_stream_alive(self, timeout_s: float = 2.0, sleep_s: float = 0.002) -> bool:
        """Warm-up helper: call run() until stream cache changes at least once."""
        t0 = time.time()
        last = None
        while (time.time() - t0) < timeout_s:
            self.run()
            cur = self._stream_tuple()
            if last is None:
                last = cur
            elif cur != last:
                logger.debug("Stream alive after %.3f s", time.time() - t0)
                return True
            time.sleep(sleep_s)
        logger.warning("Stream did not show updates within %.1f s warmup window", timeout_s)
        return False

    @property
    def position_um(self) -> Optional[int]:
        """Current motor position in µm from stream cache. None on error."""
        try:
            return int(self._motor.get_stream_data().position)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Mode control
    # ------------------------------------------------------------------

    def set_mode(self, mode: MotorMode) -> None:
        self._motor.set_mode(mode)
        logger.debug("Mode set to %s", mode)

    def get_mode(self) -> Optional[MotorMode]:
        """Read current motor mode. Unwraps OrcaResultMotorMode wrapper."""
        try:
            return self._motor.get_mode().value
        except Exception:
            return None

    def safe_stop(self) -> None:
        """Request safe stop by entering SleepMode. Must never raise."""
        try:
            self._motor.set_mode(MotorMode.SleepMode)
            logger.warning("safe_stop() called — motor set to SleepMode.")
        except Exception as exc:
            logger.error("safe_stop() raised (suppressed): %s", exc)

    # ------------------------------------------------------------------
    # AutoZero
    # ------------------------------------------------------------------

    def start_autozero(self) -> None:
        """Trigger AutoZero sequence.

        Configures AutoZero via registers then sets AutoZeroMode.
        Motor retracts shaft to hard stop and sets zero there.
        Exits to SleepMode by default — caller must set KinematicMode after.

        Poll get_mode() until it changes away from AutoZeroMode.
        Check autozero_failed() each tick to detect error bit 8192.
        """
        ZERO_MODE           = 171
        AUTO_ZERO_FORCE_N   = 172
        AUTO_ZERO_EXIT_MODE = 173
        AUTO_ZERO_SPEED_MMPS = 177
        ZERO_MODE_ENABLED   = 2   # AutoZero enabled, triggered by mode change
        EXIT_TO_SLEEP       = 1   # integer literal — avoids enum conversion issues

        self._motor.write_register_blocking(ZERO_MODE, ZERO_MODE_ENABLED)
        self._motor.write_register_blocking(AUTO_ZERO_FORCE_N, 30)
        self._motor.write_register_blocking(AUTO_ZERO_SPEED_MMPS, 50)
        self._motor.write_register_blocking(AUTO_ZERO_EXIT_MODE, EXIT_TO_SLEEP)
        self._motor.set_mode(MotorMode.AutoZeroMode)
        logger.info("AutoZero started — polling for completion...")

    def get_errors(self) -> int:
        """Return current error bitmask. 0 means no errors."""
        try:
            return int(self._motor.get_errors().value)
        except Exception:
            return 0

    def autozero_failed(self) -> bool:
        """True if the AutoZero error bit (8192) is set."""
        return bool(self.get_errors() & AUTO_ZERO_ERROR)

    # ------------------------------------------------------------------
    # Kinematic motion programming
    # ------------------------------------------------------------------

    def set_kinematic_motion(
        self,
        id: int,
        position: int,
        time_ms: int,
        delay: int = 0,
        type: int = 1,
        auto_next: bool = False,
        next_id: int = -1,
    ) -> None:
        err = self._motor.set_kinematic_motion(
            id,
            int(position),
            int(time_ms),
            int(delay),
            int(type),
            int(auto_next),
            int(next_id),
        )
        _raise_if_err(err, f"set_kinematic_motion(id={id})")

    def trigger_kinematic_motion(self, id: int) -> None:
        _raise_if_err(
            self._motor.trigger_kinematic_motion(int(id)),
            f"trigger_kinematic_motion({id})"
        )

    def get_position_um_blocking(self) -> int:
        """Blocking read of current position in µm.

        Used after AutoZero where an accurate position read is needed
        before relying on stream cache. Falls back to stream cache on error.
        """
        try:
            result = self._motor.get_position_um()
            return int(result.value)
        except Exception:
            logger.warning("get_position_um_blocking() failed — falling back to stream cache")
            return self.position_um or 0

    def wait_until_not_mode(self, mode: MotorMode, timeout_s: float, poll_s: float = 0.05) -> None:
        """Wait until device mode changes away from `mode`."""
        t0 = time.time()
        while (time.time() - t0) < timeout_s:
            m = self._motor.get_mode()
            if (not m.error) and (m.value != mode):
                return
            time.sleep(poll_s)
        raise OrcaDeviceError(f"Timeout: still in {mode} after {timeout_s}s")