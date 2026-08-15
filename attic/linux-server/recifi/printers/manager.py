"""Owns the live printer connection, the health poller and the print queue."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from PIL import Image

from ..config import load_config, save_config
from ..models import PrinterConfig
from .base import Device, Transport, TransportError
from .registry import PROTOCOLS, TRANSPORTS, make_protocol, make_transport


class PrinterManager:
    def __init__(self) -> None:
        self.cfg: PrinterConfig = load_config()
        self.transport: Transport | None = None
        # One job at a time: these printers have no command queueing at all.
        self._print_lock = asyncio.Lock()
        self._health_task: asyncio.Task | None = None

        self.state = "disconnected"  # disconnected | connecting | ready | error
        self.detail = "not connected"
        self.last_error = ""
        self.last_ok: float | None = None
        self.last_print: float | None = None
        self.jobs_printed = 0
        self.bytes_sent = 0
        self.events: deque[dict[str, Any]] = deque(maxlen=60)

    # -- bookkeeping ------------------------------------------------------

    def log(self, level: str, message: str) -> None:
        self.events.appendleft({"at": time.time(), "level": level, "message": message})

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "last_error": self.last_error,
            "last_ok": self.last_ok,
            "last_print": self.last_print,
            "jobs_printed": self.jobs_printed,
            "bytes_sent": self.bytes_sent,
            "connected": bool(self.transport and self.transport.connected),
            "config": self.cfg.model_dump(),
            "transports": [
                {
                    "name": name,
                    "available": cls.available,
                    "reason": cls.unavailable_reason,
                    "hint": cls.address_hint,
                }
                for name, cls in TRANSPORTS.items()
            ],
            "protocols": [
                {"name": name, "description": cls.description} for name, cls in PROTOCOLS.items()
            ],
            "events": list(self.events),
        }

    # -- connection -------------------------------------------------------

    async def connect(self, transport: str | None = None, address: str | None = None) -> None:
        async with self._print_lock:
            await self._connect_locked(transport, address)

    async def _connect_locked(self, transport: str | None, address: str | None) -> None:
        await self._disconnect_locked()
        if transport:
            self.cfg.transport = transport  # type: ignore[assignment]
        if address is not None:
            self.cfg.address = address

        self.state = "connecting"
        self.detail = f"opening {self.cfg.transport} {self.cfg.address}".strip()
        try:
            link = make_transport(self.cfg.transport, self.cfg.address)
            await link.connect()
        except (TransportError, ValueError, OSError) as exc:
            self.state = "error"
            self.last_error = str(exc)
            self.detail = str(exc)
            self.log("error", f"connect failed: {exc}")
            raise TransportError(str(exc)) from exc

        self.transport = link
        # A transport may resolve a fuzzy address (RFCOMM channel, BLE handle).
        self.cfg.address = link.address
        save_config(self.cfg)

        try:
            protocol = make_protocol(self.cfg.protocol, self.cfg)
            for block in protocol.preamble():
                await link.write(block)
                self.bytes_sent += len(block)
        except (TransportError, ValueError) as exc:
            self.log("warn", f"preamble failed: {exc}")

        self.state = "ready"
        self.last_ok = time.time()
        self.detail = f"{self.cfg.transport} {self.cfg.address}"
        self.last_error = ""
        self.log("info", f"connected to {self.detail}")

    async def disconnect(self) -> None:
        async with self._print_lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        if self.transport is not None:
            try:
                await self.transport.disconnect()
            except Exception as exc:
                self.log("warn", f"disconnect: {exc}")
            self.log("info", "disconnected")
        self.transport = None
        self.state = "disconnected"
        self.detail = "not connected"

    async def discover(self, transport: str | None = None) -> list[Device]:
        names = [transport] if transport else list(TRANSPORTS)
        results: list[Device] = []
        for name in names:
            cls = TRANSPORTS.get(name)
            if cls is None or not cls.available:
                continue
            try:
                results.extend(await cls.discover())
            except Exception as exc:
                self.log("warn", f"{name} scan failed: {exc}")
        results.sort(key=lambda d: (not d.likely, d.transport, d.name.lower()))
        return results

    async def update_config(self, changes: dict[str, Any]) -> PrinterConfig:
        merged = self.cfg.model_dump()
        merged.update({k: v for k, v in changes.items() if k in PrinterConfig.model_fields})
        self.cfg = save_config(PrinterConfig(**merged))
        # Address/transport changes only take effect on the next connect.
        if self.transport is not None and (
            self.transport.name != self.cfg.transport or self.transport.address != self.cfg.address
        ):
            self.log("info", "connection settings changed -- reconnecting")
            try:
                await self.connect()
            except TransportError:
                pass
        return self.cfg

    # -- printing ---------------------------------------------------------

    async def _ensure_ready(self) -> Transport:
        if self.transport is not None and self.transport.connected:
            return self.transport
        if not self.cfg.auto_connect:
            raise TransportError("printer not connected (auto-connect is off)")
        self.log("info", "not connected -- attempting auto-connect")
        await self._connect_locked(None, None)
        assert self.transport is not None
        return self.transport

    async def print_image(self, image: Image.Image, copies: int = 1) -> dict[str, Any]:
        async with self._print_lock:
            link = await self._ensure_ready()
            protocol = make_protocol(self.cfg.protocol, self.cfg)
            blocks = protocol.encode(image)
            total = 0
            try:
                for copy in range(copies):
                    for block in blocks:
                        await link.write(block)
                        total += len(block)
                    if copy + 1 < copies:
                        # Let the head finish before the next label starts.
                        await asyncio.sleep(0.4)
            except TransportError as exc:
                self.state = "error"
                self.last_error = str(exc)
                self.detail = str(exc)
                self.log("error", f"print failed: {exc}")
                raise

            self.jobs_printed += copies
            self.bytes_sent += total
            self.last_print = self.last_ok = time.time()
            self.state = "ready"
            self.log("info", f"printed {copies} label(s), {total} bytes, {image.height} dots tall")
            return {"copies": copies, "bytes": total, "height": image.height}

    async def send_raw(self, data: bytes, read_reply: bool = False, timeout: float = 1.0) -> bytes:
        async with self._print_lock:
            link = await self._ensure_ready()
            await link.write(data)
            self.bytes_sent += len(data)
            self.log("info", f"raw write, {len(data)} bytes")
            if read_reply:
                return await link.read(256, timeout)
            return b""

    # -- health poller ----------------------------------------------------

    async def start(self) -> None:
        if self.cfg.auto_connect and self.cfg.transport:
            try:
                await self.connect()
            except TransportError:
                pass  # the poller keeps retrying
        self._health_task = asyncio.create_task(self._health_loop())

    async def stop(self) -> None:
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        await self.disconnect()

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.health_interval)
            # Never fight a running job for the link.
            if self._print_lock.locked():
                continue
            try:
                await self._health_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - poller must not die
                self.log("warn", f"health check error: {exc}")

    async def _health_tick(self) -> None:
        if self.transport is None:
            if self.cfg.auto_connect and self.cfg.transport:
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
            self.log("warn", f"health check failed: {detail}")
            await self.disconnect()


manager = PrinterManager()
