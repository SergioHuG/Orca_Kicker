"""GPIO output handlers — sleep indicator pin.

SleepIndicator drives an output pin HIGH while the motor is in SLEEP state.
Must be driven LOW on any exception or clean shutdown.

Rules:
- Sleep output pin MUST be driven LOW on any exception or shutdown.
- Uses gpiozero.OutputDevice with lgpio backend.
"""

from __future__ import annotations

import logging

from gpiozero import OutputDevice
from gpiozero.pins.lgpio import LGPIOFactory

from orca_kicker.config import GpioConfig

logger = logging.getLogger(__name__)


class SleepIndicator:
    """Controls the sleep indicator output pin.

    Usage:
        indicator = SleepIndicator(config.gpio)
        indicator.on()    # sleep active
        indicator.off()   # sleep inactive
        indicator.close() # always call on shutdown
    """

    def __init__(self, config: GpioConfig) -> None:
        self._device: OutputDevice | None = None

        if not config.enabled:
            logger.warning("GPIO disabled — sleep indicator will not be active.")
            return

        if config.sleep_indicator.pin is None:
            logger.warning("Sleep indicator pin is TBD — output not active.")
            return

        factory = LGPIOFactory()

        self._device = OutputDevice(
            pin=config.sleep_indicator.pin,
            active_high=config.sleep_indicator.active_high,
            initial_value=False,  # Start LOW — not sleeping
            pin_factory=factory,
        )
        logger.info(
            "Sleep indicator on BCM pin %d (active_high=%s)",
            config.sleep_indicator.pin,
            config.sleep_indicator.active_high,
        )

    def on(self) -> None:
        """Drive pin HIGH — signal that motor is in sleep mode."""
        if self._device is not None:
            self._device.on()
            logger.debug("Sleep indicator ON")

    def off(self) -> None:
        """Drive pin LOW — signal that motor is not in sleep mode."""
        if self._device is not None:
            self._device.off()
            logger.debug("Sleep indicator OFF")

    def close(self) -> None:
        """Drive LOW and release the pin. Always call on shutdown."""
        if self._device is not None:
            try:
                self._device.off()
            except Exception:
                pass  # Best-effort — never raise on shutdown
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
            logger.debug("Sleep indicator closed.")