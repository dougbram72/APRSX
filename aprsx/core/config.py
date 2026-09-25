"""Station configuration. Persisted as JSON in the SQLite settings table (see store.py)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_CALL_RE = re.compile(r"^[A-Z0-9]{3,6}$")
_DIGI_RE = re.compile(r"^[A-Z0-9]{1,6}(-([0-9]|1[0-5]))?$")
# Values written into direwolf.conf: one line, no characters Direwolf treats specially.
_DW_VALUE_RE = re.compile(r"^[A-Za-z0-9_:=,./+-]+( [A-Za-z0-9_:=,./+-]+)*$")


class SmartBeacon(BaseModel):
    """SmartBeaconing parameters (HamHUD/Direwolf naming)."""

    enabled: bool = True
    fast_speed_kmh: float = Field(90, gt=0)
    fast_rate_s: int = Field(180, ge=30)
    slow_speed_kmh: float = Field(5, gt=0)
    slow_rate_s: int = Field(1800, ge=60)
    turn_time_s: int = Field(15, ge=5)
    turn_angle_deg: float = Field(15, gt=0, le=180)
    turn_slope: float = Field(255, ge=0)


class AprsIs(BaseModel):
    enabled: bool = False
    server: str = "rotate.aprs2.net"
    port: int = Field(14580, ge=1, le=65535)
    passcode: int = Field(-1, ge=-1, le=32767)
    filter: str = "m/50"
    igate: bool = False  # gate RF -> IS when online
    is_to_rf: bool = False  # gate IS messages to stations heard directly on RF (transmits)


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
    # Timed beacons when there's no GPS fix or SmartBeaconing is off; 0 = manual only.
    beacon_interval_s: int = Field(1800, ge=0)

    direwolf_host: str = "127.0.0.1"
    direwolf_kiss_port: int = 8001
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947
    web_port: int = 8080
    units: Literal["imperial", "metric"] = "imperial"

    digipeater: bool = False  # WIDE1-1 fill-in digi (implemented by Direwolf)

    # Direwolf, when APRS-X manages it (see direwolf.py): the core writes its
    # config from these settings and restarts it through a systemd user unit.
    direwolf_managed: bool = False
    audio_device: str = "plughw:CARD=Device,DEV=0"  # Direwolf ADEVICE
    ptt: str = ""  # Direwolf PTT arguments, e.g. "/dev/ttyUSB0 RTS" or "CM108"; "" = none

    # Map tiles: fetch from OpenStreetMap (and cache) when online.
    tiles_online: bool = True

    # Hash of the settings password (see auth.py); "" = no password.
    admin_password_hash: str = ""
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
        out = [c.upper().strip() for c in v if c.strip()]
        for c in out:
            if not re.match(r"^[A-Z0-9-]{1,9}$", c):
                raise ValueError(f"invalid callsign: {c}")
        return out

    @field_validator("canned_messages")
    @classmethod
    def _check_canned(cls, v: list[str]) -> list[str]:
        out = [t.strip() for t in v if t.strip()]
        for t in out:
            if len(t) > 67 or set(t) & set("|~{") or not all(" " <= c <= "~" for c in t):
                raise ValueError(f"not a valid APRS message: {t!r}")
        return out

    @field_validator("path")
    @classmethod
    def _check_path(cls, v: list[str]) -> list[str]:
        out = [p.upper().strip() for p in v if p.strip()]
        if len(out) > 8:
            raise ValueError("at most 8 digipeaters")
        for p in out:
            if not _DIGI_RE.match(p):
                raise ValueError(f"invalid path element: {p}")
        return out

    @field_validator("symbol_table")
    @classmethod
    def _check_symbol_table(cls, v: str) -> str:
        if not re.match(r"^[/\\0-9A-Z]$", v):
            raise ValueError("symbol table must be /, \\ or an overlay 0-9/A-Z")
        return v

    @field_validator("symbol")
    @classmethod
    def _check_symbol(cls, v: str) -> str:
        if not "!" <= v <= "~":
            raise ValueError("symbol must be a printable character")
        return v

    @field_validator("audio_device", "ptt")
    @classmethod
    def _check_direwolf_value(cls, v: str) -> str:
        v = v.strip()
        if v and not _DW_VALUE_RE.match(v):
            raise ValueError("letters, digits and _:=,./+- only, on one line")
        return v

    @field_validator("beacon_interval_s")
    @classmethod
    def _check_interval(cls, v: int) -> int:
        if 0 < v < 60:
            raise ValueError("at least 60 seconds, or 0 for manual beacons only")
        return v

    @field_validator("beacon_comment", "status_text")
    @classmethod
    def _check_text(cls, v: str) -> str:
        if not all(" " <= c <= "~" for c in v):
            raise ValueError("printable ASCII only")
        return v

    @property
    def station(self) -> str:
        """Callsign with SSID as used on air, e.g. 'N0CALL-9'."""
        return self.callsign if self.ssid == 0 else f"{self.callsign}-{self.ssid}"
