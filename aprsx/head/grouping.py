"""Join multi-part replies into one carousel card.

Bots such as WXBOT cut long replies at the 67-character message limit and send
the parts a few seconds apart, often mid-word:

    'Today,Mostly Cloudy then Slight Chance Sho'  (67 chars)
    'wers 20% High 72'                             (5 s later)

This is display only; the core stores (and acks) every part separately.
"""

from __future__ import annotations

MAX_TEXT = 67
# A part continues the previous one only if that one was (nearly) full...
NEAR_FULL = 55
# ...and it arrives soon after it, from the same station.
WINDOW_S = 60


def group_messages(rows: list[dict]) -> list[dict]:
    """Turn message rows into cards, newest first.

    A card is the first part's row with the joined ``text`` plus ``parts``,
    ``last_id`` and ``last_ts``, and ``read`` only when every part is read. An
    incoming part joins the card before it from the same peer when that card is
    incoming, near full and at most WINDOW_S older. Anything else sent to or
    received from that peer in between ends the group. A part cut at exactly
    MAX_TEXT was split mid-word, so it joins without a space.
    """
    cards: list[dict] = []
    last: dict[str, tuple[dict, str]] = {}  # peer -> (open card, text of its last part)
    for m in sorted(rows, key=lambda r: r["id"]):
        prev = last.get(m["peer"])
        if prev and _continues(prev[0], prev[1], m):
            card, prev_text = prev
            card["text"] += ("" if len(prev_text) >= MAX_TEXT else " ") + m["text"]
            card["parts"] += 1
            card["last_id"], card["last_ts"] = m["id"], m["ts"]
            card["read"] = int(bool(card["read"]) and bool(m["read"]))
        else:
            card = {**m, "parts": 1, "last_id": m["id"], "last_ts": m["ts"]}
            cards.append(card)
        last[m["peer"]] = (card, m["text"])
    return sorted(cards, key=lambda c: -c["last_id"])


def _continues(card: dict, prev_text: str, m: dict) -> bool:
    return (card["direction"] == "in" and m["direction"] == "in"
            and len(prev_text) >= NEAR_FULL
            and 0 <= m["ts"] - card["last_ts"] <= WINDOW_S)
