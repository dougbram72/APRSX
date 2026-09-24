"""aprsx-core entry point: python -m aprsx.core [--db PATH] [--listen HOST]"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from .api import create_app
from .direwolf import DEFAULT_CONF, DEFAULT_UNIT, DirewolfManager
from .service import Core
from .store import Store


def main() -> None:
    ap = argparse.ArgumentParser(description="APRS-X core service")
    ap.add_argument("--db", type=Path, default=Path.home() / ".local/share/aprsx/aprsx.db")
    ap.add_argument("--listen", default="0.0.0.0", help="HTTP listen address")
    ap.add_argument("--port", type=int, help="HTTP port (default: web_port from config)")
    ap.add_argument("--direwolf-conf", type=Path, default=DEFAULT_CONF,
                    help="where to write direwolf.conf when APRS-X manages Direwolf")
    ap.add_argument("--direwolf-unit", default=DEFAULT_UNIT,
                    help="systemd user unit to restart after writing it")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args.db.parent.mkdir(parents=True, exist_ok=True)
    core = Core(Store(args.db), direwolf=DirewolfManager(args.direwolf_conf, args.direwolf_unit))
    uvicorn.run(
        create_app(core, tiles_dir=args.db.parent / "tiles"),
        host=args.listen,
        port=args.port or core.config.web_port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
