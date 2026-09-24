import pytest

from aprsx.core import aprs
from aprsx.core.aprs import encode


def test_position_basic():
    assert (
        encode.position(49.058333, -72.029167, "/", ">", comment="hi")
        == "=4903.50N/07201.75W>hi"
    )


def test_position_rounding_carries_into_degree():
    assert encode.position(44.99999999, 0.0).startswith("=4500.00N/00000.00E")


def test_position_southern_eastern_with_course_speed_alt():
    s = encode.position(-33.8688, 151.2093, "\\", "k", course=0, speed_knots=35.4,
                        altitude_ft=120, messaging=False)
    assert s == "!3352.13S\\15112.56Ek360/035/A=000120"


def test_position_range_check():
    with pytest.raises(encode.EncodeError):
        encode.position(91, 0)


def test_message_ack_rej_status():
    assert encode.message("n0call-9", "hello", "12") == ":N0CALL-9 :hello{12"
    assert encode.ack("K1ABC", "12") == ":K1ABC    :ack12"
    assert encode.rej("K1ABC", "12") == ":K1ABC    :rej12"
    assert encode.status("mobile") == ">mobile"


@pytest.mark.parametrize("text", ["x" * 68, "a|b", "a~b", "a{b"])
def test_message_text_validation(text):
    with pytest.raises(encode.EncodeError):
        encode.message("K1ABC", text)


def test_encoded_packets_parse_back():
    pos = aprs.parse("N0CALL-9>APZAPX:" + encode.position(49.0583, -72.0292, comment="c"))
    assert pos["latitude"] == pytest.approx(49.0583, abs=1e-3)
    assert pos["longitude"] == pytest.approx(-72.0292, abs=1e-3)
    assert pos["messagecapable"] is True

    msg = aprs.parse("K1ABC>APZAPX:" + encode.message("N0CALL-9", "hello", "7"))
    assert msg["format"] == "message"
    assert msg["addresse"] == "N0CALL-9"
    assert msg["message_text"] == "hello"
    assert msg["msgNo"] == "7"

    ack = aprs.parse("K1ABC>APZAPX:" + encode.ack("N0CALL-9", "7"))
    assert ack["response"] == "ack"
    assert ack["msgNo"] == "7"


def test_parse_error():
    with pytest.raises(aprs.ParseError):
        aprs.parse("garbage")
