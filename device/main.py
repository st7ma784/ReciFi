"""ReciFi entry point -- runs automatically on boot.

Order matters: the printer manager and HTTP server come up whether or not WiFi
joins, so a board that can't reach the network still serves the UI on its
fallback access point instead of sitting there dead.
"""

import asyncio
import gc

from recifi import api, config, server, wifi
from recifi.printers import Manager

PORT = 80


async def main():
    gc.collect()
    cfg = config.load()
    print("ReciFi starting, %d bytes free" % gc.mem_free())

    # Network in the background: never block the server on it.
    asyncio.create_task(wifi.keep_connected(cfg))

    manager = Manager(cfg)
    await manager.start()

    await server.serve(api.build(cfg, manager), "0.0.0.0", PORT)
    print("HTTP server listening on port %d" % PORT)

    while True:
        await asyncio.sleep(30)
        gc.collect()
        state = wifi.status()
        if state["connected"]:
            print("up: http://%s/  (%d bytes free)" % (state["ip"], gc.mem_free()))
        elif state["mode"] == "ap":
            print("setup AP %s at http://%s/" % (state["ssid"], state["ip"]))


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("stopped")
finally:
    # Leave the loop clean so Ctrl-D at the REPL restarts cleanly.
    asyncio.new_event_loop()
