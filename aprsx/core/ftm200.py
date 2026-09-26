"""Yaesu FTM-200D radio backend: the radio's own APRS modem, read through its
DATA jack (66 COM PORT → OUTPUT = PACKET) over an SCU-66 USB serial cable.

The radio prints each decoded packet as two lines (see docs/FTM200.md):

    KC0BS-10>3XUW9Q,TNGNXI,WIDE1,STJOHN,W0NH-1,WIDE2* [09/25/26 12:32:27] <UI R>:
    `zJ^l"Ik\\`"7D}kc0bs.com_%

``LineParser`` turns that into TNC2 text; ``Ftm200Reader`` reads the port with
reconnects. The port only outputs, so this backend can't transmit: ``write()``
raises ConnectionError like a disconnected KISS link, and ``can_transmit`` is False.

The port is opened with termios directly (Linux only) to avoid a pyserial
dependency. RTS and DTR are held low, as in tools/ftm200_capture.py.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import re
import struct
import termios
from collections.abc import Callable

log = logging.getLogger(__name__)

LineHandler = Callable[[str], None]
StateHandler = Callable[[bool], None]

# "SRC>DEST,PATH [date time] <UI C>:" -- the timestamp is the radio's clock, which
# may be wrong, so it's dropped; the <...> tag (AX.25 command/response) too.
HEADER_RE = re.compile(r"^([^ >\r\n]+>[^\s\[]+)\s+\[[^\]]*\](?:\s+<[^>]*>)?\s*:\s*$")

# The radio writes each line at once. A partial line with no more bytes for this
# long was cut off (seen: an info line of just "4P" and no CR/LF, with the next
# header glued on 36 s later), so it's dropped along with its header.
IDLE_FLUSH_S = 1.0

BAUDS = {4800: termios.B4800, 9600: termios.B9600, 19200: termios.B19200,
         38400: termios.B38400, 57600: termios.B57600}


class LineParser:
    """Bytes from the data port in, TNC2 packets out."""

    def __init__(self) -> None:
        self._buf = b""
        self._header: str | None = None

    def feed(self, data: bytes) -> list[str]:
        self._buf += data
        out = []
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            if tnc2 := self._line(raw.rstrip(b"\r").decode("latin-1")):
                out.append(tnc2)
        return out

    @property
    def pending(self) -> bool:
        return bool(self._buf)

    def flush(self) -> None:
        """Drop a line that was never finished, and the packet it belonged to."""
        if self._buf:
            log.debug("FTM-200 dropped unfinished line %r after %r", self._buf, self._header)
        self._buf = b""
        self._header = None

    def _line(self, line: str) -> str | None:
        if m := HEADER_RE.match(line):
            if self._header:
                log.debug("FTM-200 header without an info line: %s", self._header)
            self._header = m.group(1)
            return None
        if not line or self._header is None:
            # Blank lines follow packets whose info ended in CR; anything else
            # outside a packet (partial line at connect) is ignored.
            return None
        header, self._header = self._header, None
        return f"{header}:{line}"


class Ftm200Reader:
    """Reads the FTM-200's data port forever, reconnecting when it goes away."""

    can_transmit = False

    def __init__(self, device: str, baud: int, on_line: LineHandler,
                 reconnect_delay: float = 5.0, on_state: StateHandler | None = None,
                 idle_flush: float = IDLE_FLUSH_S) -> None:
        self.device = device
        self.idle_flush = idle_flush
        self.baud = baud
        self.on_line = on_line
        self.reconnect_delay = reconnect_delay
        self.on_state = on_state
        self.error: str | None = None
        self._connected = False
        self._reopen = False
        # Set by the reader callback when bytes are waiting, and by set_device().
        self._wake: asyncio.Event | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def run(self) -> None:
        """Open and read forever; cancel the task to stop."""
        self._wake = asyncio.Event()
        while True:
            self._reopen = False
            try:
                fd = open_port(self.device, self.baud)
            except (OSError, ValueError) as e:
                if str(e) != self.error:
                    log.warning("FTM-200 open %s failed: %s", self.device or "(no device)", e)
                self.error = str(e)
                await self._wait(self.reconnect_delay)
                continue
            log.info("FTM-200 data port open: %s at %d baud", self.device, self.baud)
            self.error = None
            self._set(True)
            try:
                await self._read(fd)
            except OSError as e:
                log.warning("FTM-200 data port lost: %s", e)
                self.error = str(e)
            finally:
                close_port(fd)
                self._set(False)
            if not self._reopen:
                await self._wait(self.reconnect_delay)

    async def _wait(self, delay: float) -> None:
        """Sleep, or stop early when set_device() asks for a reopen."""
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), delay)
        except asyncio.TimeoutError:
            pass

    async def _read(self, fd: int) -> None:
        loop = asyncio.get_running_loop()
        parser = LineParser()
        loop.add_reader(fd, self._wake.set)
        try:
            while not self._reopen:
                try:
                    await asyncio.wait_for(self._wake.wait(),
                                           self.idle_flush if parser.pending else None)
                except asyncio.TimeoutError:
                    parser.flush()
                    continue
                self._wake.clear()
                if self._reopen:
                    break
                try:
                    data = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                if not data:
                    # With VMIN=0 an empty read can just mean nothing is waiting
                    # (another wake-up got there first), but a port that was
                    # unplugged reads empty for ever, and tcgetattr() fails on it.
                    try:
                        termios.tcgetattr(fd)
                    except termios.error as e:
                        raise OSError(f"device closed: {e}") from None
                    await asyncio.sleep(0.05)
                    continue
                for tnc2 in parser.feed(data):
                    try:
                        self.on_line(tnc2)
                    except Exception:
                        log.exception("FTM-200 packet handler failed")
        finally:
            loop.remove_reader(fd)

    def _set(self, connected: bool) -> None:
        if connected == self._connected:
            return
        self._connected = connected
        if self.on_state is not None:
            try:
                self.on_state(connected)
            except Exception:
                log.exception("FTM-200 state handler failed")

    def set_device(self, device: str, baud: int) -> None:
        """Read another port or speed: closes the current one, which reopens."""
        if (device, baud) == (self.device, self.baud):
            return
        self.device, self.baud = device, baud
        self._reopen = True
        if self._wake is not None:
            self._wake.set()

    def write(self, frame: bytes, port: int = 0) -> None:
        raise ConnectionError("the FTM-200 data port is receive-only")


def close_port(fd: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCNXCL)
    except OSError:
        pass  # already gone
    os.close(fd)


def open_port(device: str, baud: int) -> int:
    """Open a serial port raw, 8N1, non-blocking, with RTS and DTR low. It's
    exclusive: a second reader would get only part of the bytes. TIOCEXCL keeps
    later opens out; the flock() is the lock pyserial takes (e.g. the capture
    tool), so an earlier reader keeps us out too."""
    if not device:
        raise ValueError("no serial device set")
    if baud not in BAUDS:
        raise ValueError(f"unsupported baud rate {baud}")
    fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OSError(f"{device} is in use by another program") from None
        fcntl.ioctl(fd, termios.TIOCEXCL)
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0  # raw: no input, output or line processing
        attrs[2] = (attrs[2] & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB
                                 | termios.CRTSCTS)) | termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = BAUDS[baud]
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        try:
            fcntl.ioctl(fd, termios.TIOCMBIC,
                        struct.pack("I", termios.TIOCM_RTS | termios.TIOCM_DTR))
        except OSError:
            pass  # a pty (tests) has no modem lines
        termios.tcflush(fd, termios.TCIFLUSH)
    except BaseException:
        os.close(fd)
        raise
    return fd
