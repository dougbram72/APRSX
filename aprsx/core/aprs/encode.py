"""Build APRS info fields (the part after ':' in TNC2)."""

from __future__ import annotations

MAX_MESSAGE_TEXT = 67
_MSG_FORBIDDEN = set("|~{")


class EncodeError(ValueError):
    pass


def _format_coord(value: float, is_lat: bool) -> str:
    limit = 90 if is_lat else 180
    if not -limit <= value <= limit:
        raise EncodeError(f"{'latitude' if is_lat else 'longitude'} out of range: {value}")
    # Round once on hundredths of a minute so 59.999' carries into the degree.
    total = round(abs(value) * 6000)
    deg, hmin = divmod(total, 6000)
    minutes = f"{hmin // 100:02d}.{hmin % 100:02d}"
    if is_lat:
        return f"{deg:02d}{minutes}{'N' if value >= 0 else 'S'}"
    return f"{deg:03d}{minutes}{'E' if value >= 0 else 'W'}"


def position(
    lat: float,
    lon: float,
    symbol_table: str = "/",
    symbol: str = ">",
    comment: str = "",
    course: int | None = None,
    speed_knots: float | None = None,
    altitude_ft: float | None = None,
    messaging: bool = True,
) -> str:
    """Uncompressed position without timestamp ('=' when messaging-capable)."""
    if len(symbol_table) != 1 or len(symbol) != 1:
        raise EncodeError("symbol table and symbol must be single characters")
    out = "=" if messaging else "!"
    out += _format_coord(lat, True) + symbol_table + _format_coord(lon, False) + symbol
    if course is not None and speed_knots is not None:
        # APRS uses 360 for north; 000 means "unknown".
        cse = course % 360 or 360
        out += f"{cse:03d}/{min(round(speed_knots), 999):03d}"
    if altitude_ft is not None:
        out += f"/A={max(-99999, min(999999, round(altitude_ft))):06d}"
    return out + comment


def _addressee(call: str) -> str:
    call = call.upper().strip()
    if not call or len(call) > 9:
        raise EncodeError(f"invalid addressee: {call!r}")
    return call.ljust(9)


def message(
    addressee: str, text: str, msg_id: str | None = None, reply_ack: str | None = None
) -> str:
    """Message info field. reply_ack (even "") uses the reply-ack form {MM}AA."""
    if len(text) > MAX_MESSAGE_TEXT:
        raise EncodeError(f"message text longer than {MAX_MESSAGE_TEXT} characters")
    if bad := _MSG_FORBIDDEN & set(text):
        raise EncodeError(f"message text contains forbidden characters: {''.join(bad)}")
    if not all(" " <= c <= "~" for c in text):
        raise EncodeError("message text must be printable ASCII")
    out = f":{_addressee(addressee)}:{text}"
    if msg_id is not None:
        if not 1 <= len(msg_id) <= 5 or not msg_id.isalnum():
            raise EncodeError(f"invalid message id: {msg_id!r}")
        out += "{" + msg_id
        if reply_ack is not None:
            ack_ok = reply_ack == "" or (len(reply_ack) == 2 and reply_ack.isalnum())
            if len(msg_id) != 2 or not ack_ok:
                raise EncodeError("reply-acks need two-character message ids")
            out += "}" + reply_ack
    return out


def ack(addressee: str, msg_id: str) -> str:
    return f":{_addressee(addressee)}:ack{msg_id}"


def rej(addressee: str, msg_id: str) -> str:
    return f":{_addressee(addressee)}:rej{msg_id}"


def status(text: str) -> str:
    return ">" + text
