"""Config loader. Reads YAML and exposes typed config sections.

Drop-in from V1 with haptics/contact removed and sleep/extend groups added.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Sub-config dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SerialConfig:
    port: str
    baudrate: int
    interframe_us: int


@dataclass(frozen=True)
class LoopConfig:
    control_hz: float


@dataclass(frozen=True)
class MotionConfig:
    home_offset_um: int
    motion_home_id: int
    motion_extend_id: int
    motion_return_id: int
    kin_type_min_jerk: int
    motion_home_time_ms: int
    motion_extend_time_ms: int
    motion_return_time_ms: int
    extended_position_um: int


@dataclass(frozen=True)
class TimeoutsConfig:
    autozero_timeout_s: float
    home_timeout_s: float
    kick_timeout_s: float


@dataclass(frozen=True)
class TolerancesConfig:
    home_tol_um: int


@dataclass(frozen=True)
class LoggingConfig:
    log_dir: str
    csv_prefix: str
    enabled: bool
    file_enabled: bool = True
    file_name: str = "orca_kicker.log"
    file_level: str = "INFO"
    max_bytes: int = 5_000_000
    backup_count: int = 3


@dataclass(frozen=True)
class StartupConfig:
    boot_autozero_delay_s: float


@dataclass(frozen=True)
class GpioPinConfig:
    pin: Optional[int]          # TBD pins stored as None until assigned
    pull_up: bool
    bounce_time_s: Optional[float]
    lockout_ms: int


@dataclass(frozen=True)
class GpioOutputPinConfig:
    pin: Optional[int]
    active_high: bool


@dataclass(frozen=True)
class GpioConfig:
    enabled: bool
    backend: str
    pigpio_host: str
    autozero: GpioPinConfig
    kick: GpioPinConfig
    sleep_toggle: GpioPinConfig
    sleep_indicator: GpioOutputPinConfig


@dataclass(frozen=True)
class KickerConfig:
    serial: SerialConfig
    loop: LoopConfig
    motion: MotionConfig
    timeouts: TimeoutsConfig
    tolerances: TolerancesConfig
    logging: LoggingConfig
    gpio: GpioConfig
    startup: StartupConfig


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _parse_pin(value: object) -> Optional[int]:
    """Parse a pin value — returns None if 'TBD' or None."""
    if value is None or str(value).upper() == "TBD":
        return None
    return int(value)  # type: ignore[arg-type]


def load_config(path: str | Path) -> KickerConfig:
    """Load and parse the YAML config file."""
    raw = yaml.safe_load(Path(path).read_text())

    gpio_raw = raw["gpio"]

    def _input_pin(key: str) -> GpioPinConfig:
        p = gpio_raw[key]
        return GpioPinConfig(
            pin=_parse_pin(p["pin"]),
            pull_up=bool(p["pull_up"]),
            bounce_time_s=p.get("bounce_time_s"),
            lockout_ms=int(p["lockout_ms"]),
        )

    return KickerConfig(
        serial=SerialConfig(**raw["serial"]),
        loop=LoopConfig(**raw["loop"]),
        motion=MotionConfig(**raw["motion"]),
        timeouts=TimeoutsConfig(**raw["timeouts"]),
        tolerances=TolerancesConfig(**raw["tolerances"]),
        logging=LoggingConfig(**raw["logging"]),
        gpio=GpioConfig(
            enabled=bool(gpio_raw["enabled"]),
            backend=gpio_raw["backend"],
            pigpio_host=gpio_raw["pigpio_host"],
            autozero=_input_pin("autozero"),
            kick=_input_pin("kick"),
            sleep_toggle=_input_pin("sleep_toggle"),
            sleep_indicator=GpioOutputPinConfig(
                pin=_parse_pin(gpio_raw["sleep_indicator"]["pin"]),
                active_high=bool(gpio_raw["sleep_indicator"]["active_high"]),
            ),
        ),
        startup=StartupConfig(
            boot_autozero_delay_s=float(
                (raw.get("startup") or {}).get("boot_autozero_delay_s", 0.0)
            ),
        ),
    )