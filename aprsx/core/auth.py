"""Optional settings password.

When ``Config.admin_password_hash`` is set, changing settings and sending
messages from the web need a logged-in session. Loopback clients (the head
unit on the same Pi) are always trusted.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import time

COOKIE = "aprsx_session"
SESSION_TTL_S = 30 * 86400
_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(got.hex(), digest)


def is_loopback(host: str | None) -> bool:
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return host == "localhost"


class Sessions:
    """In-memory session tokens; a core restart logs everyone out."""

    def __init__(self) -> None:
        self._tokens: dict[str, float] = {}

    def create(self) -> str:
        now = time.time()
        self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
        token = secrets.token_urlsafe(32)
        self._tokens[token] = now + SESSION_TTL_S
        return token

    def valid(self, token: str | None) -> bool:
        return bool(token) and self._tokens.get(token, 0) > time.time()

    def revoke(self, token: str | None) -> None:
        self._tokens.pop(token or "", None)

    def clear(self) -> None:
        self._tokens.clear()
