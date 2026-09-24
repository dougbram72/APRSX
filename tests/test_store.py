import pytest
from pydantic import ValidationError

from aprsx.core.config import Config
from aprsx.core.store import Store


def test_defaults_when_empty(tmp_path):
    assert Store(tmp_path / "a.db").load_config() == Config()


def test_config_persists_across_reopen(tmp_path):
    db = tmp_path / "a.db"
    s = Store(db)
    cfg = Config(callsign="k1abc", ssid=7, favorites=["w1aw"])
    cfg.smartbeacon.fast_rate_s = 120
    s.save_config(cfg)
    s.save_config(cfg)  # upsert
    s.close()

    loaded = Store(db).load_config()
    assert loaded.station == "K1ABC-7"
    assert loaded.favorites == ["W1AW"]
    assert loaded.smartbeacon.fast_rate_s == 120


@pytest.mark.parametrize("call", ["K1ABC-9", "TOOLONG1", "K1"])
def test_callsign_validation(call):
    with pytest.raises(ValidationError):
        Config(callsign=call)


def test_store_is_safe_across_threads(tmp_path):
    """FastAPI runs sync endpoints on worker threads sharing the one connection."""
    import threading

    store = Store(tmp_path / "t.db")
    errors = []

    def hammer():
        try:
            for i in range(100):
                store.add_packet(float(i), f"K1ABC>APRS:>{i}")
                assert store.unread_count() == 0
                store.recent_packets(5)
        except Exception as e:  # pragma: no cover - only on failure
            errors.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(store.recent_packets(5000)) == 600
