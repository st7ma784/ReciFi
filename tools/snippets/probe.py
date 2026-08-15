# Work out which characteristic and which protocol the printer actually listens
# on, without wasting a roll of labels.
#
# Two probes per writable characteristic:
#   * an ESC/POS feed  -- if the paper moves, that path is live and speaks ESC/POS
#   * a Niimbot heartbeat -- if a 55 55 ... packet comes back on a notify
#     characteristic, that path is live and speaks Niimbot
#
# The notify channel is the useful part: a reply identifies the protocol with no
# printing at all.
#
#   tools\repl.ps1 -File tools\snippets\probe.py -TimeoutSec 180

import asyncio

import aioble
import bluetooth

# Your printer's address, from the Connection tab or GET /api/discover.
ADDR = "50:0b:66:1b:e1:d5"

WRITABLE = (
    ("18f0/2af1  print service", bluetooth.UUID(0x18F0), bluetooth.UUID(0x2AF1)),
    ("e7810a71   vendor", bluetooth.UUID("e7810a71-73ae-499d-8c15-faa9aef0c3f2"),
     bluetooth.UUID("bef8d6c9-9c21-4c9e-b632-bd58c1009f9f")),
    ("49535343   issc transparent uart",
     bluetooth.UUID("49535343-fe7d-4ae5-8fa9-9fafd205e455"),
     bluetooth.UUID("49535343-8841-43f4-a8d4-ecbe34729bb3")),
)

NOTIFY = (
    (bluetooth.UUID(0x18F0), bluetooth.UUID(0x2AF0)),
    (bluetooth.UUID("49535343-fe7d-4ae5-8fa9-9fafd205e455"),
     bluetooth.UUID("49535343-1e4d-4bd9-ba61-23c647249616")),
)


def niimbot_packet(kind, payload):
    checksum = kind ^ len(payload)
    for byte in payload:
        checksum ^= byte
    return b"\x55\x55" + bytes([kind, len(payload)]) + payload + bytes([checksum]) + b"\xaa\xaa"


PROBES = (
    ("escpos-feed", b"\x1b\x40" + b"\x1b\x64\x04"),      # init, feed 4 lines
    ("escpos-lf", b"\n" * 6),
    ("niimbot-heartbeat", niimbot_packet(0xDC, b"\x01")),
    ("niimbot-status", niimbot_packet(0x40, b"\x01")),   # get rfid/info
)


async def collect(char, label, seen, ms=1200):
    try:
        data = await char.notified(timeout_ms=ms)
    except asyncio.TimeoutError:
        return
    except Exception:
        return
    seen.append((label, bytes(data)))


async def main():
    addr = bytes(int(p, 16) for p in ADDR.split(":"))
    conn = await aioble.Device(aioble.ADDR_PUBLIC, addr).connect(timeout_ms=10000)
    print("connected")

    # A larger MTU is worth having before any bulk write: the default 23 makes
    # a label take seconds longer than it needs to.
    try:
        print("negotiated MTU:", await conn.exchange_mtu(247))
    except Exception as exc:
        print("MTU exchange unsupported:", exc)

    services = []
    async for service in conn.services():
        services.append(service)
    by_uuid = {}
    for service in services:
        chars = []
        async for char in service.characteristics():
            chars.append(char)
        for char in chars:
            by_uuid[(service.uuid, char.uuid)] = char

    notifiers = []
    for key in NOTIFY:
        char = by_uuid.get(key)
        if char is not None:
            try:
                await char.subscribe(notify=True)
                notifiers.append((str(key[1]), char))
                print("subscribed to", key[1])
            except Exception as exc:
                print("could not subscribe to", key[1], exc)

    for name, service_uuid, char_uuid in WRITABLE:
        char = by_uuid.get((service_uuid, char_uuid))
        if char is None:
            print("\n%s -- not present" % name)
            continue
        print("\n=== %s ===" % name)
        for probe_name, payload in PROBES:
            seen = []
            tasks = [asyncio.create_task(collect(c, n, seen)) for n, c in notifiers]
            try:
                for offset in range(0, len(payload), 20):
                    await char.write(payload[offset:offset + 20], response=True)
            except Exception as exc:
                print("  %-18s write failed: %s" % (probe_name, exc))
                for task in tasks:
                    task.cancel()
                continue
            for task in tasks:
                await task
            if seen:
                for source, data in seen:
                    print("  %-18s REPLY on %s: %s" % (probe_name, source, data.hex(" ")))
            else:
                print("  %-18s sent %d bytes, no reply" % (probe_name, len(payload)))
            await asyncio.sleep_ms(600)

    await conn.disconnect()
    print("\ndone -- check whether the paper moved at any point")


asyncio.run(main())
