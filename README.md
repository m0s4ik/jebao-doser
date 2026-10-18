# jebao-doser — local control for Jebao WiFi dosing pumps (no cloud)

[![Test](https://github.com/m0s4ik/jebao-doser/actions/workflows/test.yml/badge.svg)](https://github.com/m0s4ik/jebao-doser/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/jebao-doser)](https://pypi.org/project/jebao-doser/)
[![Downloads](https://img.shields.io/pypi/dm/jebao-doser)](https://pypi.org/project/jebao-doser/)
[![Python](https://img.shields.io/pypi/pyversions/jebao-doser)](https://pypi.org/project/jebao-doser/)
[![License: MIT](https://img.shields.io/github/license/m0s4ik/jebao-doser)](LICENSE)

<p align="center"><img src="images/jebao-doser-3.4.jpg" alt="Jebao DOSER 3.4 WiFi 4-head aquarium dosing pump" width="560"><br><sub>Jebao DOSER 3.4 WiFi (product image © Jebao)</sub></p>

Python library and command line tool to monitor and control **Jebao WiFi aquarium dosing pumps** (Jebao **DOSER 3.4**, and other Gizwits based Jebao dosers such as **DOSER 3.1**, **DOSER 3.2**, **MD 4.4**) directly over your **local network**. No Jebao/Gizwits cloud, no account, no app, no Home Assistant needed.

Useful for reef and planted aquarium automation: know when every head doses, read the schedule (time and ml of each dose, interval in days), get alarms, and build your own scripts, dashboards or bots.

> Home Assistant users: see **[ha-jebao-doser](https://github.com/m0s4ik/ha-jebao-doser)**, the HACS integration built on this library.

## Supported pumps

| Model | Heads | Status |
|---|---|---|
| Jebao **DOSER 3.4** WiFi | 4 | ✅ tested (firmware 04X3000R) |
| Jebao **DOSER 3.1** / **DOSER 3.2** WiFi | 1 / 2 | should work (same Gizwits platform): reports welcome |
| Jebao **MD 4.4** | 4 | should work (same Gizwits platform): reports welcome |

Jebao DOSER 3.4 WiFi at a glance:

| | |
|---|---|
| Heads | 4 peristaltic heads |
| Flow | 50 ml/min per head |
| Power | 12 V, 9 W |
| Size | 226 × 84 × 61 mm |
| Tubing | 1.5 m |
| Connectivity | Wi-Fi 2.4 GHz + Bluetooth (ESP32-C3 module, Gizwits platform) |
| Local protocol | TCP 12416, UDP 12414 (discovery) |

## Features

- **Local push**: one TCP connection, the pump reports every change by itself (scheduled doses, doses from the app, doses from the buttons on the pump). No polling.
- **Schedule** of every head: up to 24 doses per head, time and amount (ml), interval in days.
- **Dose events** with start time and duration.
- **Manual dosing**: run a head for a time or an amount (ml), with the stop always sent.
- **Alarms**: open circuit, Wi-Fi module fault.
- **Discovery** of pumps on the LAN (UDP broadcast, like the vendor app).
- **Any Gizwits product**: the pump reports its product key and the data point schema is downloaded once from the public Gizwits API and cached (the DOSER 3.4 schema is bundled).
- Pure Python 3.10+, asyncio, no dependencies.

## Install

```bash
pip install jebao-doser
```

## Command line

```bash
jebao-doser discover                 # find pumps on the LAN
jebao-doser status 192.168.1.50      # model, firmware and schedule of every head
jebao-doser listen 192.168.1.50      # stay connected and print every dose
jebao-doser status 192.168.1.50 --json
jebao-doser dose 192.168.1.50 4 3        # run head 4 for 3 seconds
jebao-doser dose 192.168.1.50 2 --ml 2   # about 2 ml (time based, 50 ml/min)
```

Example (`status`):

```
4头滴定泵WiFi_BLE  (0ESP32C3, firmware 04X3000R)
power on
head 1: no schedule
head 2: 17:30 4 ml every 2 days
head 3: 17:25 4 ml every 2 days
head 4: 22:15 10 ml every 3 days
```

Example (`listen`):

```
12:32:49 head 1 dosed for 1.6 s
12:32:53 head 2 dosed for 3.4 s
```

## Python

```python
import asyncio
from jebao_doser import Doser, schedule

async def main():
    doser = Doser("192.168.1.50")
    status = await doser.read()                      # connect, read once, disconnect
    print(doser.schema.product_name, schedule(status, 2))

    await doser.dose_ml(4, 2)                         # about 2 ml from head 4

    live = Doser("192.168.1.50", on_dose=print)      # stay connected, reconnect by itself
    live.start()
    await asyncio.sleep(3600)
    await live.stop()

asyncio.run(main())
```

## How it works

Jebao WiFi dosers use an ESP32 module running the **Gizwits** (机智云) IoT platform. Besides the cloud (MQTT), the module speaks the Gizwits **LAN protocol** on **TCP 12416** (and UDP 12414 for discovery). The pump hands out its local passcode on request, so the IP address is all that is needed to log in. Status and schedule are a fixed binary layout described by the product's public data point schema.

Packet: `00 00 00 03` + length (varint) + flag + command (2 bytes) + payload. Commands used: `0x0006/0x0007` passcode, `0x0008/0x0009` login, `0x0013/0x0014` device info (product key), `0x0015/0x0016` heartbeat, `0x0093/0x0094` read status, `0x0091` status pushed by the device, `0x0003/0x0004` UDP discovery.

## Status

Reading, dose events and manual dosing work. Writes change only the fields they name (one flag bit per data point, like the vendor app), so schedules are not touched. Note: the pump **does not report scheduled doses** (only manual ones, from the app, the buttons or this library); if you need to know they ran, watch the power draw of the pump's plug. Editing schedules is not exposed yet.

Tested with a Jebao DOSER 3.4 WiFi (4 heads, firmware 04X3000R). Reports for other models are welcome: open an issue with the output of `jebao-doser status <ip>`.

## Security

Anyone who can reach the pump on TCP 12416 can do the same: keep it on an isolated IoT network or VLAN and allow that port only from the machine that needs it.

## License

MIT. Not affiliated with Jebao or Gizwits.
