"""A stand-in for meshlink.MeshLink: records what the core sends, no device."""

from aprsx.core.config import Config
from aprsx.core.gps import Fix
from aprsx.core.meshlink import MeshError
from aprsx.core.service import Core
from aprsx.core.store import Store

ME = "aa" * 32
REPEATER = "c3" + "11" * 31
FRIEND = "f1e2d3c4b5a6" + "00" * 26


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeMeshLink:
    def __init__(self) -> None:
        self.connected = True
        self.error = None
        self.sent: list[tuple] = []
        self.channels = [{"idx": 0, "name": "Public"}, {"idx": 1, "name": "#wardriving"}]
        self.contacts: list[dict] = []
        self.fail = False
        self._ack = 0

    def _go(self, *what) -> None:
        if self.fail or not self.connected:
            raise MeshError("device refused")
        self.sent.append(what)

    async def send_dm(self, pubkey, text, attempt, ts):
        self._go("dm", pubkey, text, attempt, ts)
        self._ack += 1
        return {"expected_ack": f"{self._ack:08x}", "timeout_s": 4.0}

    async def reset_path(self, pubkey):
        self._go("reset_path", pubkey)

    async def send_chan(self, idx, text, ts):
        self._go("chan", idx, text, ts)

    async def send_advert(self, flood=False):
        self._go("advert", flood)

    async def discover(self):
        self._go("discover")
        return f"{len(self.sent):08x}"

    async def set_coords(self, lat, lon):
        self._go("coords", lat, lon)

    async def set_name(self, name):
        self._go("name", name)

    async def set_time(self, ts):
        self._go("time", ts)

    async def get_contacts(self):
        return self.contacts

    async def get_channels(self):
        return self.channels

    def kinds(self) -> list[str]:
        return [s[0] for s in self.sent]


def make_core(tmp_path, clock, **mesh) -> Core:
    store = Store(tmp_path / "a.db")
    store.save_config(Config(callsign="KF0KBP", ssid=7,
                             meshcore={"enabled": True, "device": "/dev/ttyACM0", **mesh}))
    core = Core(store, clock=clock)
    core.mesh.link = FakeMeshLink()
    core.events = []
    publish = core.bus.publish
    core.bus.publish = lambda type, data: (core.events.append((type, data)), publish(type, data))
    return core


def set_fix(core, clock, lat=39.5, lon=-95.25) -> None:
    core.gps.fix = Fix(lat=lat, lon=lon, mode=3, ts=clock())


def events(core, type: str) -> list:
    return [d for t, d in core.events if t == type]
