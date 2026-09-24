"""aprsx-monitor: connect to Direwolf over KISS and print decoded packets.

A bring-up tool for checking the radio/Digirig/Direwolf chain before the full
service exists. Usage: aprsx-monitor [--host 127.0.0.1] [--port 8001]
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from . import aprs, ax25
from .kiss import KissTcpClient


def _handle(port: int, raw: bytes) -> None:
    try:
        tnc2 = ax25.decode(raw).to_tnc2()
    except ax25.AX25Error as e:
        print(f"[{port}] undecodable frame ({e}): {raw.hex()}", flush=True)
        return
    print(f"[{port}] {tnc2}", flush=True)
    try:
        pkt = aprs.parse(tnc2)
    except aprs.ParseError as e:
        print(f"      (not parsed: {e})", flush=True)
        return
    fields = {k: pkt[k] for k in ("format", "latitude", "longitude", "addresse",
                                  "message_text", "msgNo", "response", "comment")
              if k in pkt}
    print(f"      {fields}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        asyncio.run(KissTcpClient(args.host, args.port, _handle).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
