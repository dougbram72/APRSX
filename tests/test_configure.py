import json

import pytest
from pydantic import ValidationError

from aprsx.core import configure
from aprsx.core.config import Config
from aprsx.core.store import Store


def test_apply_parses_values_and_nested_keys():
    c = configure.apply(Config(), [
        "callsign=kf0kbp", "ssid=7", "direwolf_managed=true",
        "ptt=/dev/ttyUSB0 RTS", "aprsis.enabled=true", 'path=["WIDE2-1"]',
    ])
    assert c.callsign == "KF0KBP"
    assert c.ssid == 7
    assert c.direwolf_managed is True
    assert c.ptt == "/dev/ttyUSB0 RTS"
    assert c.aprsis.enabled is True
    assert c.path == ["WIDE2-1"]


@pytest.mark.parametrize("bad", ["nosuch=1", "aprsis.nosuch=1", "callsign.x=1", "callsign"])
def test_apply_rejects_unknown_or_malformed(bad):
    with pytest.raises(ValueError):
        configure.apply(Config(), [bad])


def test_apply_validates():
    with pytest.raises(ValidationError):
        configure.apply(Config(), ["ssid=99"])


def test_main_saves_and_hides_password(tmp_path, capsys):
    db = tmp_path / "a.db"
    assert configure.main(["--db", str(db), "callsign=KF0KBP", "ssid=7"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["callsign"] == "KF0KBP"
    assert "admin_password_hash" not in shown
    store = Store(db)
    assert store.load_config().ssid == 7
    store.close()
    assert configure.main(["--db", str(db), "ssid=99"]) == 1
