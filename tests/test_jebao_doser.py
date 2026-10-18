"""Offline tests: framing, schema decoding, schedule parsing and dose events."""

import asyncio
import struct

from jebao_doser import Doser, dosing, interval, schedule
from jebao_doser.protocol import (
    CMD_P0_REPLY, CMD_P0_REPORT, DeviceInfo, _parse_discovery, build_packet, parse_packet, read_packet, status_payload,
)
from jebao_doser.schema import BUNDLED, load_file

PK = "1aa33c38ba9d4b78a9e7796705b2fad7"  # Jebao DOSER 3.4 WiFi
DP = load_file(BUNDLED / f"{PK}.json")


def status_bytes(bits=0x006E01, intervals=(0, 2, 2, 3), doses=None):
    data = bytearray(DP.length)
    data[0:3] = bits.to_bytes(3, "big")
    data[3] = 5
    for i, v in enumerate(intervals):
        data[4 + i] = v
    for head, slots in (doses or {}).items():
        off = 13 + 96 * (head - 1)
        for j, (h, m, amount) in enumerate(slots):
            data[off + 4 * j : off + 4 * j + 4] = bytes([h, m]) + struct.pack(">H", amount)
    return bytes(data)


def test_packet_roundtrip():
    pkt = build_packet(0x0093, b"\x00\x00\x00\x01\x02")
    assert pkt[:4] == b"\x00\x00\x00\x03"
    assert parse_packet(pkt) == (0x0093, b"\x00\x00\x00\x01\x02")


def test_varint_long_packet():
    payload = b"\xaa" * 300
    assert parse_packet(build_packet(0x0091, payload)) == (0x0091, payload)


def test_read_packet_stream():
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(build_packet(0x0016) + build_packet(0x0094, b"xyz"))
        reader.feed_eof()
        return [await read_packet(reader), await read_packet(reader)]

    assert asyncio.run(run()) == [(0x0016, b""), (0x0094, b"xyz")]


def test_schema():
    assert DP.length == 792
    assert DP.heads == 4
    assert DP.product_key == PK


def test_decode_status():
    s = DP.decode(status_bytes(doses={2: [(17, 30, 4)], 4: [(22, 15, 10)]}))
    assert s["switch"] is True
    assert [n for n in range(1, 9) if s[f"Timer{n}ON"]] == [1, 2, 3, 5, 6]
    assert s["channelTTL"] == 5
    assert schedule(s, 1) == []
    assert schedule(s, 2) == [{"time": "17:30", "amount": 4}]
    assert schedule(s, 4) == [{"time": "22:15", "amount": 10}]
    assert interval(s, 2) == 2 and interval(s, 4) == 3
    assert not dosing(s, 1)


def test_dosing_bit():
    s = DP.decode(status_bytes(bits=0x006E01 | 1 << 4))
    assert dosing(s, 4) and not dosing(s, 3)


def test_status_payload_with_and_without_sequence():
    data = status_bytes()
    assert status_payload(CMD_P0_REPLY, b"\x00\x00\x00\x07\x03" + data, DP.length) == data
    assert status_payload(CMD_P0_REPORT, b"\x04" + data, DP.length) == data
    assert status_payload(CMD_P0_REPORT, b"\x04" + data[:10], DP.length) is None
    assert status_payload(0x0016, b"", DP.length) is None


def test_discovery_reply():
    did, mac, fw = b"ABCDEFGHIJKLMNOPQRSTUV", bytes.fromhex("102030405060"), b"04X3000R"
    payload = b"".join(struct.pack(">H", len(x)) + x for x in (did, mac, fw, PK.encode())) + b"\x00" * 8
    info = _parse_discovery("10.0.0.5", payload)
    assert info == DeviceInfo(host="10.0.0.5", product_key=PK, did=did.decode(), mac="10:20:30:40:50:60", firmware="04X3000R")


def test_dose_events():
    doses = []
    d = Doser("10.0.0.5", schema=DP, on_dose=doses.append)
    d._handle(DP.decode(status_bytes()))
    d._handle(DP.decode(status_bytes(bits=0x006E01 | 1 << 2)))
    d._handle(DP.decode(status_bytes()))
    assert len(doses) == 1 and doses[0].head == 2 and doses[0].duration >= 0


def test_encode_write_like_the_app():
    # what the vendor app sent to start head 4: flag bit 4, data all zero except that bit
    flags, data = DP.encode_write({"channe4": True})
    assert flags == bytes.fromhex("0000000010")
    assert len(data) == 789 and data[:3] == bytes.fromhex("000010") and not any(data[3:])
    flags, data = DP.encode_write({"channe4": False})
    assert flags == bytes.fromhex("0000000010") and not any(data)
    flags, data = DP.encode_write({"channe1": True})
    assert flags == bytes.fromhex("0000000002") and data[:3] == bytes.fromhex("000002")


def test_encode_write_rejects_read_only():
    import pytest
    from jebao_doser import SchemaError
    with pytest.raises(SchemaError):
        DP.encode_write({"OpenCircuit": True})
