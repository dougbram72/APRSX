"""aprsx-config: show or change the stored settings from the command line.

aprsx-config [--db PATH]                      print the settings as JSON
aprsx-config [--db PATH] KEY=VALUE [...]      change settings, e.g.
    aprsx-config callsign=KF0KBP ssid=7 direwolf_managed=true 'ptt=/dev/ttyUSB0 RTS'
    aprsx-config aprsis.enabled=true

Values are read as JSON when they parse (numbers, true/false, lists), otherwise
as text. Dotted keys reach nested settings. The running core keeps its own copy
of the settings, so stop aprsx-core first (the installer does this); while it
runs, use the web settings page instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .config import Config
from .store import Store

DEFAULT_DB = Path.home() / ".local/share/aprsx/aprsx.db"


def _value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def apply(config: Config, assignments: list[str]) -> Config:
    """Return ``config`` with KEY=VALUE assignments applied (validated)."""
    data = config.model_dump()
    for a in assignments:
        key, sep, text = a.partition("=")
        if not sep or not key:
            raise ValueError(f"expected KEY=VALUE, got {a!r}")
        *parents, leaf = key.split(".")
        node = data
        for p in parents:
            if not isinstance(node.get(p), dict):
                raise ValueError(f"unknown setting: {key}")
            node = node[p]
        if leaf not in node:
            raise ValueError(f"unknown setting: {key}")
        node[leaf] = _value(text)
    return Config.model_validate(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Show or change APRS-X settings")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("set", nargs="*", metavar="KEY=VALUE")
    args = ap.parse_args(argv)

    args.db.parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)
    try:
        config = store.load_config()
        if args.set:
            try:
                config = apply(config, args.set)
            except (ValueError, ValidationError) as e:
                print(f"aprsx-config: {e}", file=sys.stderr)
                return 1
            store.save_config(config)
        shown = config.model_dump(exclude={"admin_password_hash"})
        print(json.dumps(shown, indent=2))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
