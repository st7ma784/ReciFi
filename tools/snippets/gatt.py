# Dump the printer's GATT table: services, characteristics and their flags.
# Run with tools\repl.ps1 -File tools\snippets\gatt.py -TimeoutSec 90
import asyncio

import aioble

# Your printer's address, from the Connection tab or GET /api/discover.
ADDR = "50:0b:66:1b:e1:d5"

FLAGS = ((0x02, "read"), (0x04, "write-no-resp"), (0x08, "write"),
         (0x10, "notify"), (0x20, "indicate"))


async def main():
    addr = bytes(int(p, 16) for p in ADDR.split(":"))
    device = aioble.Device(aioble.ADDR_PUBLIC, addr)
    conn = await device.connect(timeout_ms=10000)
    print("connected, mtu", getattr(conn, "mtu", "?"))

    services = []
    async for service in conn.services():
        services.append(service)

    for service in services:
        print("service", service.uuid)
        chars = []
        async for char in service.characteristics():
            chars.append(char)
        for char in chars:
            props = getattr(char, "properties", 0)
            names = [name for bit, name in FLAGS if props & bit]
            print("   char", char.uuid, "props=0x%02x" % props, ",".join(names))

    await conn.disconnect()
    print("done")


asyncio.run(main())
