"""GPIO input handlers — kick, autozero, sleep toggle buttons.

Callbacks are fast and non-blocking: they only enqueue TriggerEvents.
Never call pyorcasdk from here.
"""

from __future__ import annotations

import time
import logging
from typing import Optional

from gpiozero import Button
from gpiozero.pins.pigpio import PiGPIOFactory

from orca_kicker.triggers import TriggerEvent, TriggerQueue, TriggerType
from orca_kicker.config import GpioConfig

logger = logging.getLogger(__name__)


class _DebouncedButton:
    """Wraps a gpiozero Button with a software lockout guard."""

    def __init__(
        self,
        pin: int,
        pull_up: bool,
        bounce_time_s: Optional[float],
        lockout_ms: int,
        trigger_type: TriggerType,
        queue: TriggerQueue,
        factory: PiGPIOFactory,
    ) -> None:
        self._lockout_s = lockout_ms / 1000.0
        self._last_trigger_s: float = 0.0
        self._trigger_type = trigger_type
        self._queue = queue

        self._button = Button(
            pin=pin,
            pull_up=pull_up,
            bounce_time=bounce_time_s,
            pin_factory=factory,
        )
        self._button.when_pressed = self._on_pressed

    def _on_pressed(self) -> None:
        now = time.monotonic()
        if now - self._last_trigger_s < self._lockout_s:
            return  # Within lockout — discard
        self._last_trigger_s = now
        self._queue.enqueue(
            TriggerEvent(trigger_type=self._trigger_type, timestamp_s=now)
        )
        logger.debug("GPIO input: %s", self._trigger_type.name)

    def close(self) -> None:
        self._button.close()

class _LevelInput:
    """GPIO input that fires events on both press and release.
    Used for level-triggered inputs where both edges matter.
    Lockout applies to press only — release always fires.
    """

    def __init__(
        self,
        pin: int,
        pull_up: bool,
        bounce_time_s: Optional[float],
        lockout_ms: int,
        press_type: TriggerType,
        release_type: TriggerType,
        queue: TriggerQueue,
        factory: PiGPIOFactory,
    ) -> None:
        self._lockout_s = lockout_ms / 1000.0
        self._last_press_s: float = 0.0
        self._press_type = press_type
        self._release_type = release_type
        self._queue = queue

        self._button = Button(
            pin=pin,
            pull_up=pull_up,
            bounce_time=bounce_time_s,
            pin_factory=factory,
        )
        self._button.when_pressed = self._on_pressed
        self._button.when_released = self._on_released

    def _on_pressed(self) -> None:
        now = time.monotonic()
        if now - self._last_press_s < self._lockout_s:
            return
        self._last_press_s = now
        self._queue.enqueue(TriggerEvent(trigger_type=self._press_type, timestamp_s=now))
        logger.debug("GPIO level input PRESSED: %s", self._press_type.name)

    def _on_released(self) -> None:
        now = time.monotonic()
        self._queue.enqueue(TriggerEvent(trigger_type=self._release_type, timestamp_s=now))
        logger.debug("GPIO level input RELEASED: %s", self._release_type.name)

    def close(self) -> None:
        self._button.close()

class GpioInputs:
    """Manages all GPIO input buttons for the kicker."""

    def __init__(self, config: GpioConfig, queue: TriggerQueue) -> None:
        self._buttons: list = []

        if not config.enabled:
            logger.warning("GPIO disabled — no input buttons will be active.")
            return

        factory = PiGPIOFactory(host=config.pigpio_host)

        if config.autozero.pin is not None:
            self._buttons.append(
                _DebouncedButton(
                    pin=config.autozero.pin,
                    pull_up=config.autozero.pull_up,
                    bounce_time_s=config.autozero.bounce_time_s,
                    lockout_ms=config.autozero.lockout_ms,
                    trigger_type=TriggerType.AUTOZERO,
                    queue=queue,
                    factory=factory,
                )
            )
            logger.info("Autozero button on BCM pin %d", config.autozero.pin)
        else:
            logger.warning("Autozero pin is TBD — button not registered.")

        if config.kick.pin is not None:
            self._buttons.append(
                _LevelInput(
                    pin=config.kick.pin,
                    pull_up=config.kick.pull_up,
                    bounce_time_s=config.kick.bounce_time_s,
                    lockout_ms=config.kick.lockout_ms,
                    press_type=TriggerType.KICK,
                    release_type=TriggerType.KICK_RELEASED,
                    queue=queue,
                    factory=factory,
                )
            )
            logger.info("Kick level input on BCM pin %d", config.kick.pin)
        else:
            logger.warning("Kick pin is TBD — level input not registered.")

        if config.sleep_toggle.pin is not None:
            self._buttons.append(
                _DebouncedButton(
                    pin=config.sleep_toggle.pin,
                    pull_up=config.sleep_toggle.pull_up,
                    bounce_time_s=config.sleep_toggle.bounce_time_s,
                    lockout_ms=config.sleep_toggle.lockout_ms,
                    trigger_type=TriggerType.SLEEP_TOGGLE,
                    queue=queue,
                    factory=factory,
                )
            )
            logger.info("Sleep toggle button on BCM pin %d", config.sleep_toggle.pin)
        else:
            logger.warning("Sleep toggle pin is TBD — button not registered.")

    def close(self) -> None:
        for btn in self._buttons:
            btn.close()
        self._buttons.clear()
        logger.debug("GPIO inputs closed.")
