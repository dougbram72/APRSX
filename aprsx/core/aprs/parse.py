"""Parse TNC2 APRS packets into dicts via aprslib."""

from __future__ import annotations

import logging
from typing import Any

import aprslib
from aprslib.exceptions import ParseError as _AprslibParseError
from aprslib.exceptions import UnknownFormat as _AprslibUnknownFormat

# aprslib logs every unparseable packet at warning level; RF is noisy.
logging.getLogger("aprslib").setLevel(logging.ERROR)


class ParseError(ValueError):
    pass


def parse(tnc2: str) -> dict[str, Any]:
    """Parse one packet. Raises ParseError if aprslib can't make sense of it.

    Useful keys in the result: from, to, path, format ('uncompressed',
    'compressed', 'mic-e', 'message', 'status', ...), latitude, longitude,
    symbol_table, symbol, comment, speed (km/h), course; for messages:
    addresse (sic), message_text, msgNo, and response ('ack'/'rej').
    """
    try:
        return aprslib.parse(tnc2)
    except (_AprslibParseError, _AprslibUnknownFormat) as e:
        raise ParseError(str(e)) from e
