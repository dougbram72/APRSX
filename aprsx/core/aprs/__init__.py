"""APRS packet encoding and parsing."""

from . import encode
from .parse import ParseError, parse

# Registered "experimental" tocall range (APZxxx) until we get our own.
TOCALL = "APZAPX"

__all__ = ["TOCALL", "ParseError", "encode", "parse"]
