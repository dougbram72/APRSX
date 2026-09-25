"""When to send position beacons: SmartBeaconing, or a fixed interval.

SmartBeaconing follows HamHUD/Direwolf: below the slow speed, beacon every
slow rate; above the fast speed, every fast rate; in between, the interval
scales inversely with speed. Turning more than
``turn_angle + turn_slope / speed_mph`` degrees since the last beacon sends
one early ("corner pegging"), but no sooner than ``turn_time`` after it.
``turn_slope`` is in degrees x mph, as in HamHUD and Direwolf, so their
usual values carry over.
"""

from __future__ import annotations

from .config import SmartBeacon

KMH_PER_MPH = 1.609344


def smart_rate(sb: SmartBeacon, speed_kmh: float) -> float:
    """Seconds between beacons at this speed, ignoring turns."""
    if speed_kmh < sb.slow_speed_kmh:
        return sb.slow_rate_s
    if speed_kmh > sb.fast_speed_kmh:
        return sb.fast_rate_s
    return sb.fast_rate_s * sb.fast_speed_kmh / speed_kmh


def turn_threshold(sb: SmartBeacon, speed_kmh: float) -> float:
    """Heading change (degrees) that counts as a corner at this speed."""
    speed_mph = speed_kmh / KMH_PER_MPH
    if speed_mph <= 0:
        return 180.0
    return min(180.0, sb.turn_angle_deg + sb.turn_slope / speed_mph)


def heading_change(a: float, b: float) -> float:
    """Smallest angle between two headings, 0-180."""
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


class Scheduler:
    """Decides when the next automatic beacon is due. Call ``sent()`` after
    every beacon, manual ones too, so the timers restart from it."""

    def __init__(self) -> None:
        self.last_ts: float | None = None
        self.last_course: float | None = None
        self.not_before = float("-inf")  # no automatic beacon before this time

    def sent(self, now: float, course: float | None = None) -> None:
        self.last_ts = now
        self.last_course = course

    def hold(self, until: float) -> None:
        """No automatic beacon before ``until`` (startup, or after a failed send)."""
        self.not_before = until

    def due_smart(self, sb: SmartBeacon, now: float, speed_kmh: float | None,
                  course: float | None) -> bool:
        if now < self.not_before:
            return False
        if self.last_ts is None:
            return True
        elapsed = now - self.last_ts
        speed = speed_kmh or 0.0
        if elapsed >= smart_rate(sb, speed):
            return True
        # Corner pegging only when moving; a parked GPS's course wanders.
        return (speed >= sb.slow_speed_kmh and course is not None
                and self.last_course is not None
                and elapsed >= sb.turn_time_s
                and heading_change(course, self.last_course) > turn_threshold(sb, speed))

    def due_fixed(self, interval_s: int, now: float) -> bool:
        if interval_s <= 0 or now < self.not_before:
            return False
        return self.last_ts is None or now - self.last_ts >= interval_s
