"""Bluetooth Low Energy transport (bleak).

Many recent pocket label printers are BLE-only: no SPP, just a vendor service
with one writable characteristic that swallows raster data. If the write
characteristic is not given explicitly we pick the first writable one on a
vendor (128-bit) service, which is right on every printer tested so far.

Address form: ``AA:BB:CC:DD:EE:FF`` or ``AA:BB:CC:DD:EE:FF@<char-uuid>``.
"""

from __future__ import annotations

import asyncio

from ..base import Device, Transport, TransportError

try:
    from bleak import BleakClient, BleakScanner

    _HAVE_BLEAK = True
except Exception:  # pragma: no cover - import guard
    _HAVE_BLEAK = False

_PRINTER_HINTS = ("print", "orgbro", "z3", "label", "niimbot", "phomemo")
# Standard 16-bit-derived UUIDs; a vendor characteristic won't match this.
_BLUETOOTH_BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"


class BLETransport(Transport):
    name = "ble"
    address_hint = "AA:BB:CC:DD:EE:FF  (append @<char-uuid> to pin the write handle)"
    available = _HAVE_BLEAK
    unavailable_reason = "" if _HAVE_BLEAK else "bleak is not installed -- pip install bleak"

    def __init__(self, address: str = "") -> None:
        super().__init__(address)
        self._client: BleakClient | None = None
        self._char = None
        self._write_response = False

    @staticmethod
    async def discover() -> list[Device]:
        if not _HAVE_BLEAK:
            return []
        try:
            found = await BleakScanner.discover(timeout=6.0)
        except Exception as exc:
            return [Device(transport="ble", address="", name="BLE scan failed", detail=str(exc))]
        devices = []
        for dev in found:
            name = dev.name or "(unnamed)"
            devices.append(
                Device(
                    transport="ble",
                    address=dev.address,
                    name=name,
                    detail="BLE advertisement",
                    likely=any(hint in name.lower() for hint in _PRINTER_HINTS),
                )
            )
        return devices

    def _split_address(self) -> tuple[str, str]:
        if "@" in self.address:
            mac, char = self.address.split("@", 1)
            return mac.strip(), char.strip()
        return self.address.strip(), ""

    def _pick_char(self, wanted: str):
        assert self._client is not None
        services = self._client.services
        if wanted:
            char = services.get_characteristic(wanted)
            if char is None:
                raise TransportError(f"characteristic {wanted} not found on device")
            return char
        best = None
        for service in services:
            vendor = not str(service.uuid).lower().endswith(_BLUETOOTH_BASE_SUFFIX)
            for char in service.characteristics:
                props = set(char.properties)
                if not props & {"write", "write-without-response"}:
                    continue
                if vendor:
                    return char
                best = best or char
        if best is None:
            raise TransportError("no writable characteristic found -- pass one with @uuid")
        return best

    async def connect(self) -> None:
        if not _HAVE_BLEAK:
            raise TransportError(self.unavailable_reason)
        mac, wanted = self._split_address()
        if not mac:
            raise TransportError("no BLE address set")
        client = BleakClient(mac, timeout=15.0)
        try:
            await client.connect()
        except Exception as exc:
            raise TransportError(f"BLE connect to {mac} failed: {exc}") from exc
        self._client = client
        try:
            self._char = self._pick_char(wanted)
        except TransportError:
            await client.disconnect()
            self._client = None
            raise
        self._write_response = "write" in set(self._char.properties)
        self.address = f"{mac}@{self._char.uuid}"
        self._connected = True

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        self._char = None
        self._connected = False
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def write(self, data: bytes) -> None:
        if self._client is None or self._char is None:
            raise TransportError("not connected")
        # Respect the negotiated MTU; oversized writes are silently dropped.
        mtu = getattr(self._client, "mtu_size", 23) or 23
        chunk = max(20, mtu - 3)
        for offset in range(0, len(data), chunk):
            try:
                await self._client.write_gatt_char(
                    self._char, data[offset : offset + chunk], response=self._write_response
                )
            except Exception as exc:
                raise TransportError(f"BLE write failed: {exc}") from exc
            if not self._write_response:
                # Unacknowledged writes need pacing or the printer's buffer
                # overruns and the label comes out truncated.
                await asyncio.sleep(0.01)

    async def probe(self) -> tuple[bool, str]:
        if self._client is None:
            return False, "not connected"
        if not self._client.is_connected:
            self._connected = False
            return False, "BLE link dropped"
        return True, f"BLE {self.address}"
