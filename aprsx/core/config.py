"""Station configuration. Persisted as JSON in the SQLite settings table (see store.py)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_CALL_RE = re.compile(r"^[A-Z0-9]{3,6}$")


class SmartBeacon(BaseModel):
    """SmartBeaconing parameters (HamHUD/Direwolf naming)."""

    enabled: bool = True
    fast_speed_kmh: float = 90
    fast_rate_s: int = 180
    slow_speed_kmh: float = 5
    slow_rate_s: int = 1800
    turn_time_s: int = 15
    turn_angle_deg: float = 15
    turn_slope: float = 255


class AprsIs(BaseModel):
    enabled: bool = False
    server: str = "rotate.aprs2.net"
    port: int = 14580
    passcode: int = -1
    filter: str = "m/50"
    igate: bool = False  # gate RF -> IS when online


class Config(BaseModel):
    callsign: str = "N0CALL"
    ssid: int = Field(9, ge=0, le=15)
    symbol_table: str = Field("/", min_length=1, max_length=1)
    symbol: str = Field(">", min_length=1, max_length=1)
    path: list[str] = ["WIDE1-1", "WIDE2-1"]
    beacon_comment: str = "APRS-X"
    status_text: str = ""

    # Used when there is no GPS fix.
    fixed_lat: float | None = Field(None, ge=-90, le=90)
    fixed_lon: float | None = Field(None, ge=-180, le=180)

    direwolf_host: str = "127.0.0.1"
    direwolf_kiss_port: int = 8001
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947
    web_port: int = 8080
    units: Literal["imperial", "metric"] = "imperial"

    digipeater: bool = False  # WIDE1-1 fill-in digi (implemented by Direwolf)
    smartbeacon: SmartBeacon = SmartBeacon()
    aprsis: AprsIs = AprsIs()

    canned_messages: list[str] = ["QSL", "On my way", "73"]
    favorites: list[str] = []

    @field_validator("callsign")
    @classmethod
    def _check_callsign(cls, v: str) -> str:
        v = v.upper().strip()
        if not _CALL_RE.match(v):
            raise ValueError("callsign must be 3-6 letters/digits, without SSID")
        return v

    @field_validator("favorites")
    @classmethod
    def _upper_favorites(cls, v: list[str]) -> list[str]:
        return [c.upper().strip() for c in v]

    @property
    def station(self) -> str:
        """Callsign with SSID as used on air, e.g. 'N0CALL-9'."""
        return self.callsign if self.ssid == 0 else f"{self.callsign}-{self.ssid}"
