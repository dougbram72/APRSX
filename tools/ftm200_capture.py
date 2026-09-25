#!/usr/bin/env python3
"""Record the FTM-200D's data-port output byte for byte (WP9.1).

Read-only: it opens the SCU-66 port with RTS and DTR held low (as the user's
ftm200_aprs_bridge.py does) and never writes to it. Each run leaves two files:

  <out>.raw   the exact bytes received, for test fixtures (WP9.6)
  <out>.log   one line per received line: arrival time and Python repr()

Runs on the Pi's system python3 (Debian's python3-serial), not the aprsx venv:

  python3 tools/ftm200_capture.py                  # auto-detect port, 9600 baud
  python3 tools/ftm200_capture.py --baud 19200 --seconds 1800
  python3 tools/ftm200_capture.py --scan           # try each COM SPEED setting

Auto-detection picks the one /dev/serial/by-id entry that isn't the Digirig's
CP210x. Stop the ftm200-aprs-bridge unit first if it's running.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
from pathlib import Path

import serial

BAUDS = (4800, 9600, 19200, 38400, 57600)

# Packet header line as matched by ftm200_aprs_bridge.py:
#   SRC>DEST,PATH [date time] <UI ...>:
HEADER_RE = re.compile(rb"^([^ >\r\n]+>[^\s\[]+)\s+\[[^\]]+\](?:\s+<[^>]*>)?\s*:\s*$")


def find_device() -> str:
    by_id = Path("/dev/serial/by-id")
    found = sorted(str(p) for p in by_id.glob("*") if "cp210" not in p.name.lower()) \
        if by_id.is_dir() else []
    if len(found) != 1:
        sys.exit(f"Expected one non-Digirig serial device in {by_id}, found "
                 f"{len(found)}: {found}. Pass --device.")
    return found[0]


def open_port(device: str, baud: int) -> serial.Serial:
    port = serial.Serial()
    port.port = device
    port.baudrate = baud
    port.timeout = 0.2
    port.exclusive = True
    port.rts = False  # set before open() so the lines are never raised
    port.dtr = False
    port.open()
    return port


def capture(device: str, baud: int, out: Path, seconds: float | None) -> None:
    raw_path, log_path = out.with_suffix(".raw"), out.with_suffix(".log")
    lines = headers = high = 0
    buf = b""
    last_byte = time.monotonic()
    end = time.monotonic() + seconds if seconds else None
    print(f"Capturing {device} at {baud} baud to {raw_path} and {log_path}; "
          "Ctrl-C to stop", flush=True)
    with open_port(device, baud) as port, raw_path.open("ab") as raw, \
            log_path.open("a", encoding="ascii", errors="backslashreplace") as log:
        log.write(f"# {dt.datetime.now().astimezone().isoformat()} "
                  f"device={device} baud={baud}\n")

        def emit(line: bytes, note: str = "") -> None:
            nonlocal lines, headers, high
            lines += 1
            headers += bool(HEADER_RE.match(line.rstrip(b"\r\n")))
            high += sum(b > 0x7E or (b < 0x20 and b not in b"\r\n") for b in line)
            stamp = dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
            text = f"{stamp} {line!r}{note}"
            print(text, flush=True)
            log.write(text + "\n")
            log.flush()

        try:
            while end is None or time.monotonic() < end:
                chunk = port.read(4096)
                now = time.monotonic()
                if chunk:
                    raw.write(chunk)
                    raw.flush()
                    buf += chunk
                    last_byte = now
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        emit(line + b"\n")
                elif buf and now - last_byte > 2.0:
                    emit(buf, "  (no newline after 2 s)")
                    buf = b""
        except KeyboardInterrupt:
            pass
        if buf:
            emit(buf, "  (partial at exit)")
    print(f"{lines} lines, {headers} packet headers, {high} control/non-ASCII bytes",
          flush=True)


def scan(device: str, seconds: float) -> None:
    """Listen at each COM SPEED; the right one gives mostly printable text."""
    for baud in BAUDS:
        data = b""
        with open_port(device, baud) as port:
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                data += port.read(4096)
        ok = sum(0x20 <= b <= 0x7E or b in b"\r\n" for b in data)
        share = f"{100 * ok / len(data):.0f}% printable" if data else "no data"
        found = sum(bool(HEADER_RE.match(line.rstrip(b"\r"))) for line in data.split(b"\n"))
        print(f"{baud:>6} baud: {len(data):>5} bytes, {share}, {found} packet headers",
              flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", help="serial device (default: auto-detect)")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--seconds", type=float, help="stop after this long")
    ap.add_argument("--out", type=Path,
                    help="output path without extension "
                         "(default: ~/ftm200-capture-<date>-<time>)")
    ap.add_argument("--scan", action="store_true",
                    help=f"listen at each of {BAUDS} and report what arrives")
    ap.add_argument("--scan-seconds", type=float, default=60)
    args = ap.parse_args()
    device = args.device or find_device()
    if args.scan:
        scan(device, args.scan_seconds)
        return
    out = args.out or Path.home() / f"ftm200-capture-{dt.datetime.now():%Y%m%d-%H%M%S}"
    capture(device, args.baud, out, args.seconds)


if __name__ == "__main__":
    main()
