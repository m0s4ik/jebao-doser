"""High-level client for Jebao (Gizwits) dosing pumps."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .protocol import CMD_PING, DeviceInfo, ProtocolError, Session, TCP_PORT, read_packet, status_payload
from .schema import DataPoints, SchemaError, async_get_schema

_LOGGER = logging.getLogger(__name__)

PING_INTERVAL = 20
SILENCE_LIMIT = 60
SCHEDULE_SLOTS = 24

ERRORS = (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, ProtocolError, SchemaError)


@dataclass
class Dose:
    """A head ran from ``start`` for ``duration`` seconds."""

    head: int
    start: datetime
    duration: float


def schedule(status: dict[str, Any], head: int) -> list[dict[str, Any]]:
    """Programmed doses of a head: 24 slots of hour, minute, amount (2 bytes, big-endian)."""
    raw = status.get(f"CH{head}SWTime") or b""
    doses = []
    for i in range(0, min(len(raw), SCHEDULE_SLOTS * 4), 4):
        amount = int.from_bytes(raw[i + 2 : i + 4], "big")
        if amount:
            doses.append({"time": f"{raw[i]:02d}:{raw[i + 1]:02d}", "amount": amount})
    return sorted(doses, key=lambda d: d["time"])


def interval(status: dict[str, Any], head: int) -> int | None:
    """Days between dosing days (0 or 1 = every day)."""
    return status.get(f"IntervalT{head}")


def dosing(status: dict[str, Any], head: int) -> bool:
    return bool(status.get(f"channe{head}"))


class Doser:
    """Keeps a local connection open and reports every status change.

    The pump pushes its full status after any change (schedule, app, cloud or
    the buttons on the pump), so there is no polling: only a heartbeat.
    Reconnects by itself until ``stop()``.
    """

    def __init__(
        self,
        host: str,
        *,
        port: int = TCP_PORT,
        schema: DataPoints | None = None,
        cache_dir: Path | str | None = None,
        on_status: Callable[[dict[str, Any]], None] | None = None,
        on_dose: Callable[[Dose], None] | None = None,
        on_connection: Callable[[bool], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.schema = schema
        self.cache_dir = cache_dir
        self.info: DeviceInfo | None = None
        self.status: dict[str, Any] | None = None
        self.connected = False
        self._on_status = on_status
        self._on_dose = on_dose
        self._on_connection = on_connection
        self._started: dict[int, tuple[datetime, float]] = {}
        self._task: asyncio.Task | None = None
        self._session: Session | None = None

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def read(self) -> dict[str, Any]:
        """Connect, read the status once and disconnect."""
        session = await Session.open(self.host, self.port)
        try:
            await self._prepare(session)
            session.send_read()
            await session.writer.drain()

            async def wait() -> dict[str, Any]:
                while True:
                    cmd, payload = await read_packet(session.reader)
                    if (data := status_payload(cmd, payload, self.schema.length)) is not None:
                        return self.schema.decode(data)

            self.status = await asyncio.wait_for(wait(), 10)
            return self.status
        finally:
            session.close()

    # ---- internals -------------------------------------------------------

    async def _prepare(self, session: Session) -> None:
        self.info = await session.info(self.host)
        if self.schema is None or self.schema.product_key != self.info.product_key:
            self.schema = await async_get_schema(self.info.product_key, self.cache_dir)

    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            if self._on_connection:
                self._on_connection(connected)

    async def _run(self) -> None:
        delay = 5
        while True:
            try:
                await self._loop()
            except asyncio.CancelledError:
                raise
            except ERRORS as err:
                _LOGGER.warning("Pump %s: connection lost (%s)", self.host, err or type(err).__name__)
                delay = min(delay * 2, 120) if not self.connected else 5
            finally:
                if self._session:
                    self._session.close()
                    self._session = None
                self._set_connected(False)
            await asyncio.sleep(delay)

    async def _loop(self) -> None:
        session = self._session = await Session.open(self.host, self.port)
        await self._prepare(session)
        session.send_read()
        await session.writer.drain()
        self._set_connected(True)
        loop = asyncio.get_running_loop()
        next_ping = last_rx = loop.time()
        while True:
            try:
                cmd, payload = await asyncio.wait_for(
                    read_packet(session.reader), max(0.1, next_ping - loop.time())
                )
            except asyncio.TimeoutError:
                if loop.time() - last_rx > SILENCE_LIMIT:
                    raise ProtocolError("no answer to heartbeat") from None
                session.send(CMD_PING)
                await session.writer.drain()
                next_ping = loop.time() + PING_INTERVAL
                continue
            last_rx = loop.time()
            if (data := status_payload(cmd, payload, self.schema.length)) is not None:
                self._handle(self.schema.decode(data))

    def _handle(self, status: dict[str, Any]) -> None:
        old = self.status or {}
        self.status = status
        for name, value in status.items():
            if not name.startswith("channe") or not name[6:].isdigit():
                continue
            head = int(name[6:])
            if value and not old.get(name):
                self._started[head] = (datetime.now(timezone.utc), time.monotonic())
            elif old.get(name) and not value and head in self._started:
                start, t0 = self._started.pop(head)
                if self._on_dose:
                    self._on_dose(Dose(head, start, round(time.monotonic() - t0, 1)))
        if self._on_status:
            self._on_status(status)
