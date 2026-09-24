"""KISS framing and an async KISS-over-TCP client for Direwolf.

Direwolf exposes KISS on TCP (default port 8001). Each KISS frame carries one
raw AX.25 frame; see ax25.py for converting those to/from TNC2 text.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD

CMD_DATA = 0x00


def encode_frame(data: bytes, port: int = 0) -> bytes:
    """Wrap an AX.25 frame in a KISS data frame for the given TNC port."""
    out = bytearray([FEND, (port << 4) | CMD_DATA])
    for b in data:
        if b == FEND:
            out += bytes([FESC, TFEND])
        elif b == FESC:
            out += bytes([FESC, TFESC])
        else:
            out.append(b)
    out.append(FEND)
    return bytes(out)


class KissDecoder:
    """Incremental decoder: feed arbitrary byte chunks, get (port, frame) tuples.

    Only data frames (command 0) are returned; other KISS commands are ignored.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._in_frame = False
        self._escape = False

    def feed(self, chunk: bytes) -> list[tuple[int, bytes]]:
        frames: list[tuple[int, bytes]] = []
        for b in chunk:
            if b == FEND:
                if self._in_frame and self._buf:
                    frame = self._finish()
                    if frame is not None:
                        frames.append(frame)
                self._buf.clear()
                self._in_frame = True
                self._escape = False
            elif not self._in_frame:
                continue
            elif self._escape:
                self._escape = False
                if b == TFEND:
                    self._buf.append(FEND)
                elif b == TFESC:
                    self._buf.append(FESC)
                # Invalid escape sequences are dropped, per the KISS spec.
            elif b == FESC:
                self._escape = True
            else:
                self._buf.append(b)
        return frames

    def _finish(self) -> tuple[int, bytes] | None:
        cmd = self._buf[0]
        if cmd & 0x0F != CMD_DATA:
            return None
        return cmd >> 4, bytes(self._buf[1:])


FrameHandler = Callable[[int, bytes], Awaitable[None] | None]
StateHandler = Callable[[bool], None]


class KissTcpClient:
    """Maintains a KISS TCP connection to Direwolf, reconnecting on failure.

    Received AX.25 frames are passed to ``on_frame(port, frame)``.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_frame: FrameHandler,
        reconnect_delay: float = 5.0,
        on_state: StateHandler | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.on_frame = on_frame
        self.reconnect_delay = reconnect_delay
        self.on_state = on_state
        self._writer: asyncio.StreamWriter | None = None
        self._connected = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def wait_connected(self) -> None:
        await self._connected.wait()

    async def run(self) -> None:
        """Connect and read forever; cancel the task to stop."""
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
            except OSError as e:
                log.warning("KISS connect to %s:%s failed: %s", self.host, self.port, e)
                await asyncio.sleep(self.reconnect_delay)
                continue

            log.info("KISS connected to %s:%s", self.host, self.port)
            self._writer = writer
            self._connected.set()
            self._notify(True)
            try:
                await self._read_loop(reader)
            except (OSError, asyncio.IncompleteReadError) as e:
                log.warning("KISS connection lost: %s", e)
            finally:
                self._connected.clear()
                self._writer = None
                writer.close()
                self._notify(False)
            await asyncio.sleep(self.reconnect_delay)

    def _notify(self, connected: bool) -> None:
        if self.on_state is not None:
            try:
                self.on_state(connected)
            except Exception:
                log.exception("KISS state handler failed")

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        decoder = KissDecoder()
        while chunk := await reader.read(4096):
            for port, frame in decoder.feed(chunk):
                try:
                    result = self.on_frame(port, frame)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    log.exception("KISS frame handler failed")
        log.warning("KISS connection closed by peer")

    def set_address(self, host: str, port: int) -> None:
        """Connect somewhere else: drops the current connection, which reconnects."""
        if (host, port) == (self.host, self.port):
            return
        self.host, self.port = host, port
        if self._writer is not None:
            self._writer.close()

    def write(self, frame: bytes, port: int = 0) -> None:
        """Queue one AX.25 frame without waiting for the socket to drain.

        Raises ConnectionError if not connected.
        """
        if self._writer is None:
            raise ConnectionError("KISS not connected")
        self._writer.write(encode_frame(frame, port))

    async def send(self, frame: bytes, port: int = 0) -> None:
        """Send one AX.25 frame. Raises ConnectionError if not connected."""
        self.write(frame, port)
        await self._writer.drain()
