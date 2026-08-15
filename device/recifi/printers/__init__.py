"""Printer connection manager: state, health polling and the print queue."""

import asyncio
import time

from . import protocols
from .transports import TRANSPORTS, TransportError, make

MAX_EVENTS = 25


class Manager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.transport = None
        # These printers have no command queueing, so one job at a time.
        self.lock = asyncio.Lock()
        self.state = "disconnected"  # disconnected | connecting | ready | error
        self.detail = "not connected"
        self.last_error = ""
        self.last_ok = 0
        self.last_print = 0
        self.jobs = 0
        self.bytes_sent = 0
        self.events = []
        self._task = None

    # -- bookkeeping ------------------------------------------------------

    def log(self, level, message):
        self.events.insert(0, {"at": time.time(), "level": level, "message": message})
        if len(self.events) > MAX_EVENTS:
            self.events.pop()

    def status(self):
        return {
            "state": self.state,
            "detail": self.detail,
            "last_error": self.last_error,
            "last_ok": self.last_ok,
            "last_print": self.last_print,
            "jobs_printed": self.jobs,
            "bytes_sent": self.bytes_sent,
            "connected": bool(self.transport and self.transport.connected),
            "transports": [
                {"name": name, "available": cls.available,
                 "reason": cls.unavailable_reason, "hint": cls.address_hint}
                for name, cls in TRANSPORTS.items()
            ],
            "protocols": [
                {"name": name, "description": protocols.DESCRIPTIONS[name]}
                for name in protocols.ENCODERS
            ],
            "events": self.events,
        }

    # -- connection -------------------------------------------------------

    async def connect(self, transport=None, address=None):
        async with self.lock:
            await self._disconnect()
            if transport:
                self.cfg["transport"] = transport
            if address is not None:
                self.cfg["address"] = address

            self.state = "connecting"
            self.detail = "opening %s %s" % (self.cfg["transport"], self.cfg["address"])
            try:
                link = make(self.cfg["transport"], self.cfg)
                await link.connect()
            except (TransportError, ValueError, OSError) as exc:
                self.state = "error"
                self.last_error = str(exc)
                self.detail = str(exc)
                self.log("error", "connect failed: %s" % exc)
                raise TransportError(str(exc))

            self.transport = link
            # A transport may resolve a fuzzy address (BLE characteristic).
            self.cfg["address"] = link.address

            try:
                for block in protocols.preamble(self.cfg["protocol"], self.cfg):
                    await link.write(block)
                    self.bytes_sent += len(block)
            except (TransportError, OSError) as exc:
                self.log("warn", "preamble failed: %s" % exc)

            self.state = "ready"
            self.last_ok = time.time()
            self.last_error = ""
            self.detail = "%s %s" % (self.cfg["transport"], self.cfg["address"])
            self.log("info", "connected to %s" % self.detail)
            return self.status()

    async def disconnect(self):
        async with self.lock:
            await self._disconnect()
        return self.status()

    async def _disconnect(self):
        if self.transport is not None:
            try:
                await self.transport.disconnect()
            except Exception as exc:
                self.log("warn", "disconnect: %s" % exc)
            self.log("info", "disconnected")
        self.transport = None
        self.state = "disconnected"
        self.detail = "not connected"

    async def discover(self, transport=None):
        names = [transport] if transport else list(TRANSPORTS)
        out = []
        for name in names:
            cls = TRANSPORTS.get(name)
            if cls is None or not cls.available:
                continue
            try:
                out.extend(await cls.discover())
            except Exception as exc:
                self.log("warn", "%s scan failed: %s" % (name, exc))
        out.sort(key=lambda d: (not d.get("likely"), d.get("name", "").lower()))
        return out

    # -- printing ---------------------------------------------------------

    async def _ensure_ready(self):
        if self.transport is not None and self.transport.connected:
            return self.transport
        if not self.cfg.get("auto_connect", True):
            raise TransportError("printer not connected (auto-connect is off)")
        self.log("info", "not connected -- attempting auto-connect")
        link = make(self.cfg["transport"], self.cfg)
        await link.connect()
        self.transport = link
        self.state = "ready"
        return link

    async def print_label(self, label, copies=1):
        async with self.lock:
            link = await self._ensure_ready()
            total = 0
            try:
                for copy in range(copies):
                    for block in protocols.encode(self.cfg["protocol"], label, self.cfg):
                        await link.write(block)
                        total += len(block)
                        # Keep the HTTP server responsive during a long job.
                        await asyncio.sleep_ms(0)
                    if copy + 1 < copies:
                        await asyncio.sleep_ms(400)
            except (TransportError, OSError) as exc:
                self.state = "error"
                self.last_error = str(exc)
                self.detail = str(exc)
                self.log("error", "print failed: %s" % exc)
                raise TransportError(str(exc))

            self.jobs += copies
            self.bytes_sent += total
            self.last_print = self.last_ok = time.time()
            self.state = "ready"
            self.log("info", "printed %d label(s), %d bytes, %d dots tall"
                     % (copies, total, label.height))
            return {"copies": copies, "bytes": total, "height": label.height}

    async def send_raw(self, data, read_reply=False, timeout=1.0):
        async with self.lock:
            link = await self._ensure_ready()
            await link.write(data)
            self.bytes_sent += len(data)
            self.log("info", "raw write, %d bytes" % len(data))
            if read_reply:
                return await link.read(256, timeout)
            return b""

    # -- health -----------------------------------------------------------

    async def start(self):
        if self.cfg.get("auto_connect", True) and self.cfg.get("transport"):
            try:
                await self.connect()
            except TransportError:
                pass  # the poller keeps retrying
        self._task = asyncio.create_task(self._health_loop())

    async def _health_loop(self):
        while True:
            await asyncio.sleep(self.cfg.get("health_interval", 15))
            if self.lock.locked():
                continue  # never fight a running job for the link
            try:
                await self._health_tick()
            except Exception as exc:
                self.log("warn", "health check error: %s" % exc)

    async def _health_tick(self):
        if self.transport is None:
            if self.cfg.get("auto_connect", True) and self.cfg.get("transport"):
                try:
                    await self.connect()
                except TransportError:
                    pass
            return
        ok, detail = await self.transport.probe()
        self.detail = detail
        if ok:
            self.state = "ready"
            self.last_ok = time.time()
            self.last_error = ""
        else:
            self.state = "error"
            self.last_error = detail
            self.log("warn", "health check failed: %s" % detail)
            await self.disconnect()
