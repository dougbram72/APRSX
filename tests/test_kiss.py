import asyncio

from aprsx.core.kiss import FEND, FESC, TFEND, TFESC, KissDecoder, KissTcpClient, encode_frame


def test_encode_escapes_special_bytes():
    assert encode_frame(bytes([0x01, FEND, FESC, 0x02])) == bytes(
        [FEND, 0x00, 0x01, FESC, TFEND, FESC, TFESC, 0x02, FEND]
    )


def test_encode_port_in_high_nibble():
    assert encode_frame(b"x", port=2)[1] == 0x20


def test_roundtrip_split_across_chunks():
    payload = bytes(range(256))
    wire = encode_frame(payload, port=1) + encode_frame(b"second")
    dec = KissDecoder()
    frames = []
    for i in range(0, len(wire), 7):
        frames += dec.feed(wire[i : i + 7])
    assert frames == [(1, payload), (0, b"second")]


def test_ignores_non_data_commands_and_empty_frames():
    dec = KissDecoder()
    assert dec.feed(bytes([FEND, FEND, 0x01, 0x10, FEND])) == []  # TXDELAY command
    assert dec.feed(bytes([0x55, 0x55]) + encode_frame(b"ok")) == [(0, b"ok")]


async def test_client_receives_and_sends():
    received_by_server = asyncio.Queue()

    async def serve(reader, writer):
        writer.write(encode_frame(b"from-tnc"))
        await writer.drain()
        dec = KissDecoder()
        while chunk := await reader.read(100):
            for f in dec.feed(chunk):
                await received_by_server.put(f)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    got = asyncio.Queue()
    client = KissTcpClient("127.0.0.1", port, lambda p, f: got.put_nowait((p, f)))
    task = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(client.wait_connected(), 2)
        assert await asyncio.wait_for(got.get(), 2) == (0, b"from-tnc")
        await client.send(b"to-tnc")
        assert await asyncio.wait_for(received_by_server.get(), 2) == (0, b"to-tnc")
    finally:
        task.cancel()
        server.close()
