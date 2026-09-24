"""AX.25 UI frame <-> TNC2 text ("SRC>DEST,PATH:info") conversion.

KISS carries raw AX.25; the APRS layer (and aprslib) works on TNC2 text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CONTROL_UI = 0x03
PID_NO_L3 = 0xF0

_CALL_RE = re.compile(r"^([A-Z0-9]{1,6})(?:-(\d{1,2}))?$")


class AX25Error(ValueError):
    pass


@dataclass(frozen=True)
class Address:
    call: str
    ssid: int = 0
    repeated: bool = False  # H bit ("has been repeated"), only meaningful on digis

    @classmethod
    def parse(cls, text: str) -> Address:
        repeated = text.endswith("*")
        m = _CALL_RE.match(text.rstrip("*").upper())
        if not m or int(m.group(2) or 0) > 15:
            raise AX25Error(f"invalid AX.25 address: {text!r}")
        return cls(m.group(1), int(m.group(2) or 0), repeated)

    def __str__(self) -> str:
        return self.call if self.ssid == 0 else f"{self.call}-{self.ssid}"


@dataclass
class Frame:
    source: Address
    dest: Address
    path: list[Address] = field(default_factory=list)
    info: bytes = b""

    @classmethod
    def from_tnc2(cls, line: str) -> Frame:
        try:
            header, info = line.split(":", 1)
            src, rest = header.split(">", 1)
        except ValueError:
            raise AX25Error(f"not a TNC2 packet: {line!r}") from None
        dest, *path = rest.split(",")
        digis = [Address.parse(p) for p in path]
        if len(digis) > 8:
            raise AX25Error("more than 8 digipeaters")
        # "A*" in TNC2 means A and every digi before it has repeated the frame.
        last = max((i for i, d in enumerate(digis) if d.repeated), default=-1)
        digis = [Address(d.call, d.ssid, i <= last) for i, d in enumerate(digis)]
        return cls(Address.parse(src), Address.parse(dest), digis, info.encode("latin-1"))

    def to_tnc2(self) -> str:
        parts = [str(self.dest)]
        last = max((i for i, d in enumerate(self.path) if d.repeated), default=-1)
        parts += [str(d) + ("*" if i == last else "") for i, d in enumerate(self.path)]
        return f"{self.source}>{','.join(parts)}:{self.info.decode('latin-1')}"


def _encode_address(addr: Address, *, last: bool, high_bit: bool) -> bytes:
    call = addr.call.ljust(6)
    out = bytearray((ord(c) << 1) & 0xFE for c in call)
    ssid_byte = 0x60 | ((addr.ssid & 0x0F) << 1)
    if high_bit:
        ssid_byte |= 0x80
    if last:
        ssid_byte |= 0x01
    out.append(ssid_byte)
    return bytes(out)


def _decode_address(raw: bytes) -> tuple[Address, bool]:
    call = "".join(chr(b >> 1) for b in raw[:6]).rstrip()
    if not call or not all(c.isalnum() for c in call):
        raise AX25Error(f"bad address bytes: {raw!r}")
    ssid_byte = raw[6]
    return (
        Address(call.upper(), (ssid_byte >> 1) & 0x0F, bool(ssid_byte & 0x80)),
        bool(ssid_byte & 0x01),
    )


def encode(frame: Frame) -> bytes:
    """Encode a UI frame as raw AX.25 bytes (no FCS; the TNC adds it)."""
    out = bytearray()
    # Command frame per AX.25 v2: C bit set on dest, clear on source.
    out += _encode_address(frame.dest, last=False, high_bit=True)
    out += _encode_address(frame.source, last=not frame.path, high_bit=False)
    for i, digi in enumerate(frame.path):
        out += _encode_address(
            digi, last=i == len(frame.path) - 1, high_bit=digi.repeated
        )
    out += bytes([CONTROL_UI, PID_NO_L3])
    out += frame.info
    return bytes(out)


def decode(data: bytes) -> Frame:
    """Decode raw AX.25 bytes. Raises AX25Error for non-UI or malformed frames."""
    addrs: list[Address] = []
    pos = 0
    while True:
        if pos + 7 > len(data):
            raise AX25Error("truncated address field")
        addr, last = _decode_address(data[pos : pos + 7])
        addrs.append(addr)
        pos += 7
        if last:
            break
        if len(addrs) >= 10:
            raise AX25Error("address field too long")
    if len(addrs) < 2:
        raise AX25Error("missing source address")
    if pos + 2 > len(data):
        raise AX25Error("missing control/PID")
    if data[pos] != CONTROL_UI or data[pos + 1] != PID_NO_L3:
        raise AX25Error("not an APRS UI frame")

    dest, src, *digis = addrs
    # H bit is meaningless on dest/source (those bits are C bits); drop it.
    return Frame(
        source=Address(src.call, src.ssid),
        dest=Address(dest.call, dest.ssid),
        path=digis,
        info=data[pos + 2 :],
    )
