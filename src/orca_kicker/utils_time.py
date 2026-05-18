"""Time utilities. Stub — kept for future use."""

import time


def monotonic_ms() -> float:
    """Return monotonic time in milliseconds."""
    return time.monotonic() * 1000.0


def monotonic_s() -> float:
    """Return monotonic time in seconds."""
    return time.monotonic()
