"""Stations-heard helpers: packet -> station record, distance/bearing."""

from __future__ import annotations

import math
from typing import Any


EARTH_RADIUS_KM = 6371.0088


def distance_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Great-circle distance (km) and initial bearing (degrees true) from 1 to 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    dist = 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dlon) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    return dist, (math.degrees(math.atan2(y, x)) + 360) % 360


def station_record(source: str, path: list[str], pkt: dict[str, Any] | None, ts: float,
                   channel: str = "rf") -> dict[str, Any]:
    """Build the upsert_station() input for one received packet.

    ``path`` is the digipeater path as TNC2 text elements ("WIDE1*"). Objects
    and items are tracked under their own name, not the sender's. Only an RF
    packet that no digipeater repeated counts as heard directly.
    """
    pkt = pkt or {}
    record: dict[str, Any] = {
        "name": pkt.get("object_name", "").strip() or source,
        "is_object": "object_name" in pkt,
        "ts": ts,
        "last_format": pkt.get("format"),
        "heard_direct": channel == "rf" and not any(p.endswith("*") for p in path),
        "path": ",".join(path),
        "channel": channel,
    }
    if "latitude" in pkt and "longitude" in pkt:
        record.update(
            lat=pkt["latitude"],
            lon=pkt["longitude"],
            symbol_table=pkt.get("symbol_table"),
            symbol=pkt.get("symbol"),
            comment=pkt.get("comment") or None,
            speed_kmh=pkt.get("speed"),
            course=pkt.get("course"),
        )
    return record


def with_distance(station: dict[str, Any], lat: float | None, lon: float | None) -> dict[str, Any]:
    """Add distance_km/bearing relative to our position (None if either is unknown)."""
    out = dict(station)
    if None in (lat, lon, station.get("lat"), station.get("lon")):
        out["distance_km"] = out["bearing"] = None
    else:
        d, b = distance_bearing(lat, lon, station["lat"], station["lon"])
        out["distance_km"], out["bearing"] = round(d, 2), round(b)
    return out
