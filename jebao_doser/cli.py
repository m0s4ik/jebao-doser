"""Command line: jebao-doser discover | status <ip> | listen <ip> | schema <product_key>."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from . import Doser, discover, dosing, get_schema, interval, schedule

CACHE = Path.home() / ".cache" / "jebao-doser"


def _heads(doser: Doser) -> int:
    return doser.schema.heads or sum(1 for n in doser.schema.names if n.startswith("CH") and n.endswith("SWTime"))


def _summary(doser: Doser) -> str:
    s = doser.status
    lines = [f"{doser.schema.product_name}  ({doser.info.module}, firmware {doser.info.firmware})",
             f"power {'on' if s.get('switch') else 'off'}"]
    for h in range(1, _heads(doser) + 1):
        doses = schedule(s, h)
        plan = ", ".join(f"{d['time']} {d['amount']} ml" for d in doses) or "no schedule"
        every = interval(s, h)
        every = "" if not doses else (" every day" if (every or 0) <= 1 else f" every {every} days")
        lines.append(f"head {h}: {'DOSING ' if dosing(s, h) else ''}{plan}{every}")
    for alarm in ("OpenCircuit", "Fault_UART"):
        if s.get(alarm):
            lines.append(f"ALARM {alarm}")
    return "\n".join(lines)


def _jsonable(status: dict) -> dict:
    return {k: (v.hex() if isinstance(v, bytes) else v) for k, v in status.items()}


async def _status(args) -> None:
    doser = Doser(args.host, cache_dir=CACHE)
    status = await doser.read()
    print(json.dumps(_jsonable(status), ensure_ascii=False, indent=1) if args.json else _summary(doser))


async def _listen(args) -> None:
    shown = False

    def on_status(status):
        nonlocal shown
        if args.json:
            print(json.dumps(_jsonable(status), ensure_ascii=False), flush=True)
        elif not shown:
            print(_summary(doser), flush=True)
            shown = True

    def on_dose(dose):
        print(f"{dose.start.astimezone():%H:%M:%S} head {dose.head} dosed for {dose.duration} s", flush=True)

    def on_connection(up):
        print(f"{datetime.now():%H:%M:%S} {'connected' if up else 'disconnected'}", flush=True)

    doser = Doser(args.host, cache_dir=CACHE, on_status=on_status, on_dose=on_dose, on_connection=on_connection)
    doser.start()
    await asyncio.Event().wait()


async def _discover(args) -> None:
    devices = await discover(args.timeout, args.target)
    for d in devices:
        print(f"{d.host}  mac {d.mac}  product_key {d.product_key}  firmware {d.firmware}")
    if not devices:
        print("no Gizwits devices answered")


def main() -> None:
    p = argparse.ArgumentParser(prog="jebao-doser", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="read the status once")
    s.add_argument("host")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("listen", help="stay connected and print every dose")
    s.add_argument("host")
    s.add_argument("--json", action="store_true", help="also print every status as JSON")
    s = sub.add_parser("discover", help="find pumps on the LAN (UDP broadcast)")
    s.add_argument("--timeout", type=float, default=3)
    s.add_argument("--target", default="255.255.255.255", help="broadcast or device address")
    s = sub.add_parser("schema", help="download and print a product's data point schema")
    s.add_argument("product_key")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    if args.cmd == "schema":
        print(json.dumps(get_schema(args.product_key, CACHE).schema, ensure_ascii=False, indent=1))
        return
    try:
        asyncio.run({"status": _status, "listen": _listen, "discover": _discover}[args.cmd](args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
