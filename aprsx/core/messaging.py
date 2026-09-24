"""APRS messaging: acks, retries, duplicate suppression, reply-acks.

Outgoing messages always carry a two-character message number in the
reply-ack form ``{MM}``, which tells the peer we understand reply-acks
(aprs11/replyacks.txt). When the peer has used that form too, its latest
message number rides along as ``{MM}AA``. We still send a separate ack for
every numbered message we receive.

Retry state lives in the messages table (``tries``, ``next_try``) and is
driven by ``tick()``, so pending messages survive a restart.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from . import aprs

if TYPE_CHECKING:
    from .service import Core

log = logging.getLogger(__name__)

# Wait after each try before the next one; after the last wait the message fails.
RETRY_DELAYS_S = (30, 60, 120, 120, 120)
MAX_TRIES = len(RETRY_DELAYS_S)
# How soon to try again when the TNC isn't connected (doesn't count as a try).
TX_WAIT_S = 10
# A copy of a message already received within this window is a duplicate.
DUPE_WINDOW_S = 1800
DUPE_WINDOW_UNNUMBERED_S = 60
# Digipeated copies of one transmission arrive within seconds; ack only once.
# Must stay below the sender's retry interval so a lost ack gets repeated.
ACK_HOLDOFF_S = 20

_ADDRESSEE_RE = re.compile(r"^[A-Z0-9-]{1,9}$")
_MSGNO_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class MessageError(ValueError):
    pass


def msgno_from_seq(seq: int) -> str:
    """Two-character message number (reply-acks allow exactly two)."""
    hi, lo = divmod(seq % len(_MSGNO_CHARS) ** 2, len(_MSGNO_CHARS))
    return _MSGNO_CHARS[hi] + _MSGNO_CHARS[lo]


class Messenger:
    def __init__(self, core: Core) -> None:
        self.core = core
        self.store = core.store
        # peer -> last message number it sent us in reply-ack form.
        self._reply_ack: dict[str, str] = {}
        # (peer, msgno) -> when we last acked it.
        self._acked: dict[tuple[str, str], float] = {}

    # --- outgoing ----------------------------------------------------------

    def send(self, to: str, text: str) -> dict[str, Any]:
        """Queue a message and transmit the first try right away."""
        to = to.upper().strip()
        if not _ADDRESSEE_RE.match(to):
            raise MessageError(f"invalid addressee: {to!r}")
        text = text.strip()
        if not text:
            raise MessageError("message text is empty")
        msgno = msgno_from_seq(self.store.next_seq("msg_seq"))
        try:
            aprs.encode.message(to, text, msgno, reply_ack="")  # validate before storing
        except aprs.encode.EncodeError as e:
            raise MessageError(str(e)) from e

        now = self.core.clock()
        msg = self.store.add_message(
            ts=now, direction="out", peer=to, text=text, msgno=msgno,
            state="pending", next_try=now,
        )
        self.core.bus.publish("message", msg)
        return self._try(msg, now)

    def tick(self) -> None:
        """Send retries that are due and fail messages that ran out of tries."""
        now = self.core.clock()
        for msg in self.store.due_messages(now):
            self._try(msg, now)

    def _try(self, msg: dict[str, Any], now: float) -> dict[str, Any]:
        if msg["tries"] >= MAX_TRIES:
            return self._set_state(msg, "failed")
        info = aprs.encode.message(
            msg["peer"], msg["text"], msg["msgno"],
            reply_ack=self._reply_ack.get(msg["peer"], ""),
        )
        if not self.core.transmit(info):
            return self.store.update_message(msg["id"], next_try=now + TX_WAIT_S)
        tries = msg["tries"] + 1
        msg = self.store.update_message(
            msg["id"], tries=tries, next_try=now + RETRY_DELAYS_S[tries - 1]
        )
        self.core.bus.publish("ack", msg)
        return msg

    def _set_state(self, msg: dict[str, Any], state: str) -> dict[str, Any]:
        msg = self.store.update_message(
            msg["id"], state=state, next_try=None,
            acked_ts=self.core.clock() if state in ("acked", "rejected") else None,
        )
        log.info("message %s to %s: %s", msg["msgno"], msg["peer"], state)
        self.core.bus.publish("ack", msg)
        return msg

    def _acknowledge(self, peer: str, msgno: str, state: str) -> None:
        msg = self.store.find_pending(peer, msgno)
        if msg is not None:
            self._set_state(msg, state)

    # --- incoming ----------------------------------------------------------

    def handle(self, peer: str, pkt: dict[str, Any], tnc2: str, ts: float) -> None:
        """Handle a received message packet (aprslib format 'message').

        ``peer`` is the sender. For a third-party packet it is the inner
        sender, and ``pkt``/``tnc2`` are the inner packet.
        """
        me = self.core.config.station
        if pkt.get("addresse", "").strip().upper() != me or peer == me:
            return

        msgno = pkt.get("msgNo")
        if reply_ack := pkt.get("ackMsgNo"):
            self._acknowledge(peer, reply_ack, "acked")
        if resp := pkt.get("response"):
            if msgno:
                self._acknowledge(peer, msgno.rstrip("}"), "acked" if resp == "ack" else "rejected")
            return

        text = pkt.get("message_text", "")
        if msgno:
            if "{" + msgno + "}" in tnc2 and len(msgno) == 2 and msgno.isalnum():
                self._reply_ack[peer] = msgno
            self._send_ack(peer, msgno, ts)

        window = DUPE_WINDOW_S if msgno else DUPE_WINDOW_UNNUMBERED_S
        if self.store.find_duplicate(peer, msgno, text, ts - window):
            log.debug("duplicate message from %s: %s", peer, text)
            return
        msg = self.store.add_message(
            ts=ts, direction="in", peer=peer, text=text, msgno=msgno, state="received",
        )
        self.core.bus.publish("message", msg)

    def _send_ack(self, peer: str, msgno: str, now: float) -> None:
        key = (peer, msgno)
        if now - self._acked.get(key, -ACK_HOLDOFF_S) < ACK_HOLDOFF_S:
            return
        if self.core.transmit(aprs.encode.ack(peer, msgno)):
            self._acked[key] = now
        # Keep the holdoff table from growing forever.
        if len(self._acked) > 500:
            self._acked = {k: t for k, t in self._acked.items() if now - t < ACK_HOLDOFF_S}
