"""Gizwits LAN protocol: TCP 12416 (session) and UDP 12414 (discovery).

Packet: ``00 00 00 03`` + length (varint) + flag ``00`` + command (2 bytes) + payload.
"""

from __future__ import annotations

import asyncio
import re
import socket
import struct
from dataclasses import dataclass

HEADER = b"\x00\x00\x00\x03"
TCP_PORT = 12416
UDP_PORT = 12414

CMD_DISCOVER = 0x0003
CMD_DISCOVER_REPLY = 0x0004
CMD_PASSCODE_REQ = 0x0006
CMD_PASSCODE_RESP = 0x0007
CMD_LOGIN_REQ = 0x0008
CMD_LOGIN_RESP = 0x0009
CMD_INFO_REQ = 0x0013
CMD_INFO_RESP = 0x0014
CMD_PING = 0x0015
CMD_PONG = 0x0016
CMD_P0_REPORT = 0x0091  # status pushed by the device
CMD_P0_SEND = 0x0093  # client -> device, with sequence number
CMD_P0_REPLY = 0x0094  # device -> client, with sequence number

ACTION_WRITE = 0x01
ACTION_READ = 0x02
ACTION_READ_REPLY = 0x03
ACTION_REPORT = 0x04

TIMEOUT = 10

_PRODUCT_KEY = re.compile(rb"[0-9a-f]{32}")


class ProtocolError(Exception):
    """Unexpected answer from the device."""


@dataclass
class DeviceInfo:
    host: str
    product_key: str
    did: str | None = None
    mac: str | None = None
    firmware: str | None = None
    module: str | None = None


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def build_packet(cmd: int, payload: bytes = b"") -> bytes:
    body = b"\x00" + struct.pack(">H", cmd) + payload
    return HEADER + _varint(len(body)) + body


def parse_packet(data: bytes) -> tuple[int, bytes]:
    """Parse one complete packet held in a buffer (UDP)."""
    if data[:4] != HEADER:
        raise ProtocolError("unexpected header")
    length, shift, i = 0, 0, 4
    while True:
        b = data[i]
        i += 1
        length |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            break
    body = data[i : i + length]
    return struct.unpack(">H", body[1:3])[0], body[3:]


async def read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    if await reader.readexactly(4) != HEADER:
        raise ProtocolError("unexpected header")
    length, shift = 0, 0
    while True:
        b = (await reader.readexactly(1))[0]
        length |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            break
    body = await reader.readexactly(length)
    return struct.unpack(">H", body[1:3])[0], body[3:]


class Session:
    """One logged-in TCP connection. Not thread safe; one reader at a time."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._sn = 0

    @classmethod
    async def open(cls, host: str, port: int = TCP_PORT) -> Session:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), TIMEOUT)
        session = cls(reader, writer)
        try:
            await session._login()
        except BaseException:
            session.close()
            raise
        return session

    def close(self) -> None:
        self.writer.close()

    def send(self, cmd: int, payload: bytes = b"") -> None:
        self.writer.write(build_packet(cmd, payload))

    async def request(self, cmd: int, payload: bytes, expect: int) -> bytes:
        """Send and wait for one answer, skipping anything else the device sends."""
        self.send(cmd, payload)
        await self.writer.drain()

        async def wait() -> bytes:
            while True:
                got, data = await read_packet(self.reader)
                if got == expect:
                    return data

        return await asyncio.wait_for(wait(), TIMEOUT)

    async def _login(self) -> None:
        # The device hands out its passcode to anyone who asks.
        data = await self.request(CMD_PASSCODE_REQ, b"", CMD_PASSCODE_RESP)
        passcode = data[2 : 2 + struct.unpack(">H", data[:2])[0]]
        data = await self.request(
            CMD_LOGIN_REQ, struct.pack(">H", len(passcode)) + passcode, CMD_LOGIN_RESP
        )
        if data[:1] != b"\x00":
            raise ProtocolError("login refused")

    async def info(self, host: str = "") -> DeviceInfo:
        data = await self.request(CMD_INFO_REQ, b"", CMD_INFO_RESP)
        m = _PRODUCT_KEY.search(data)
        if not m:
            raise ProtocolError("no product key in device info")
        text = lambda b: b.rstrip(b"\x00").decode("ascii", "replace") or None  # noqa: E731
        return DeviceInfo(host=host, product_key=m.group().decode(), module=text(data[0:8]), firmware=text(data[8:16]))

    def send_read(self) -> None:
        self._sn = (self._sn + 1) & 0xFFFFFFFF
        self.send(CMD_P0_SEND, struct.pack(">I", self._sn) + bytes([ACTION_READ]))

    def send_write(self, flags: bytes, data: bytes) -> None:
        self._sn = (self._sn + 1) & 0xFFFFFFFF
        self.send(CMD_P0_SEND, struct.pack(">I", self._sn) + bytes([ACTION_WRITE]) + flags + data)


def status_payload(cmd: int, payload: bytes, length: int) -> bytes | None:
    """Status data out of a P0 packet, or None if it is not a status.

    0x0094 carries a 4-byte sequence number before the action byte; 0x0091
    may or may not, so it is found by the expected length.
    """
    if cmd not in (CMD_P0_REPORT, CMD_P0_REPLY):
        return None
    for off in (4, 0):
        body = payload[off:]
        if len(body) == 1 + length and body[0] in (ACTION_READ_REPLY, ACTION_REPORT):
            return body[1:]
    return None


def _parse_discovery(host: str, payload: bytes) -> DeviceInfo | None:
    try:
        i = 2
        n = struct.unpack(">H", payload[0:2])[0]
        did = payload[i : i + n].decode("ascii", "replace")
        i += n
        n = struct.unpack(">H", payload[i : i + 2])[0]
        mac = payload[i + 2 : i + 2 + n].hex(":")
        i += 2 + n
        n = struct.unpack(">H", payload[i : i + 2])[0]
        firmware = payload[i + 2 : i + 2 + n].decode("ascii", "replace")
        i += 2 + n
        n = struct.unpack(">H", payload[i : i + 2])[0]
        product_key = payload[i + 2 : i + 2 + n].decode("ascii")
    except (struct.error, UnicodeDecodeError, IndexError):
        return None
    return DeviceInfo(host=host, product_key=product_key, did=did, mac=mac, firmware=firmware)


async def discover(timeout: float = 3.0, target: str = "255.255.255.255") -> list[DeviceInfo]:
    """Find Gizwits devices on the LAN (UDP broadcast, as the vendor app does)."""
    loop = asyncio.get_running_loop()
    found: dict[str, DeviceInfo] = {}

    class Listener(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            try:
                cmd, payload = parse_packet(data)
            except (ProtocolError, IndexError, struct.error):
                return
            if cmd == CMD_DISCOVER_REPLY and (info := _parse_discovery(addr[0], payload)):
                found[addr[0]] = info

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("", 0))
    transport, _ = await loop.create_datagram_endpoint(Listener, sock=sock)
    try:
        transport.sendto(build_packet(CMD_DISCOVER), (target, UDP_PORT))
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return list(found.values())
