import pytest

from aprsx.core import ax25


def test_encode_known_bytes():
    raw = ax25.encode(ax25.Frame.from_tnc2("N0CALL-9>APRS:>hi"))
    assert raw == (
        bytes([0x82, 0xA0, 0xA4, 0xA6, 0x40, 0x40, 0xE0])  # APRS, C bit set
        + bytes([0x9C, 0x60, 0x86, 0x82, 0x98, 0x98, 0x73])  # N0CALL-9, last
        + bytes([0x03, 0xF0])
        + b">hi"
    )


@pytest.mark.parametrize(
    "line",
    [
        "N0CALL-9>APZAPX,WIDE1-1,WIDE2-1:=4903.50N/07201.75W>test",
        "K1ABC>APRS,DIGI1*,WIDE2-1::N0CALL-9 :hello{12",
        "W1AW-15>APRS,A,B*,C:>status",
        "AB1>APRS:",
    ],
)
def test_roundtrip(line):
    assert ax25.decode(ax25.encode(ax25.Frame.from_tnc2(line))).to_tnc2() == line


def test_star_marks_all_previous_digis_repeated():
    f = ax25.Frame.from_tnc2("A>B,D1,D2*,D3:x")
    assert [d.repeated for d in f.path] == [True, True, False]
    assert f.to_tnc2() == "A>B,D1,D2*,D3:x"


def test_info_bytes_preserved():
    f = ax25.Frame(ax25.Address("N0CALL"), ax25.Address("APRS"), info=bytes(range(256)))
    assert ax25.decode(ax25.encode(f)).info == bytes(range(256))


@pytest.mark.parametrize("bad", ["TOOLONGCALL", "N0CALL-16", "N0-CALL", ""])
def test_invalid_addresses(bad):
    with pytest.raises(ax25.AX25Error):
        ax25.Address.parse(bad)


def test_decode_rejects_non_ui_and_truncated():
    raw = ax25.encode(ax25.Frame.from_tnc2("A>B:x"))
    with pytest.raises(ax25.AX25Error):
        ax25.decode(raw[:10])
    with pytest.raises(ax25.AX25Error):
        ax25.decode(raw[:14] + bytes([0x3F, 0xF0]))
