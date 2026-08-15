"""Bluetooth Classic (RFCOMM / SPP) transport.

Uses the AF_BLUETOOTH socket family built into CPython on Linux, so there is no
extra dependency on the Pi. Discovery shells out to ``bluetoothctl`` because
that is what actually knows about paired devices on a BlueZ system.

Pair once out of band before connecting:

    bluetoothctl
    > scan on          # note the printer's MAC
    > pair AA:BB:CC:DD:EE:FF
    > trust AA:BB:CC:DD:EE:FF
"""

from __future__ import annotations

import asyncio
import re
import socket

from ..base import Device, Transport, TransportError

_HAVE_BT = hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")
_MAC = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
# Names that show up on rebadged thermal label printers.
_PRINTER_HINTS = ("print", "orgbro", "z3", "label", "niimbot", "phomemo", "pt-", "btp")


async def _run(cmd: list[str], timeout: float = 12.0) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, NotImplementedError, OSError):
        return ""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return ""
    return out.decode("utf-8", errors="replace")


def _parse_devices(text: str, detail: str) -> list[Device]:
    devices: list[Device] = []
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2 or parts[0] != "Device" or not _MAC.match(parts[1]):
            continue
        mac = parts[1]
        name = parts[2] if len(parts) > 2 else mac
        devices.append(
            Device(
                transport="bluetooth",
                address=mac,
                name=name,
                detail=detail,
                likely=any(hint in name.lower() for hint in _PRINTER_HINTS),
            )
        )
    return devices


class BluetoothTransport(Transport):
    name = "bluetooth"
    address_hint = "AA:BB:CC:DD:EE:FF  (append :N to force an RFCOMM channel)"
    available = _HAVE_BT
    unavailable_reason = "" if _HAVE_BT else "AF_BLUETOOTH sockets are Linux-only"

    def __init__(self, address: str = "") -> None:
        super().__init__(address)
        self._sock: socket.socket | None = None

    @staticmethod
    async def discover() -> list[Device]:
        if not _HAVE_BT:
            return []
        known = _parse_devices(await _run(["bluetoothctl", "devices"]), "paired/known")
        seen = {d.address for d in known}
        # A short live scan picks up a printer that has never been paired.
        await _run(["bluetoothctl", "--timeout", "6", "scan", "on"], timeout=10.0)
        for dev in _parse_devices(await _run(["bluetoothctl", "devices"]), "in range"):
            if dev.address not in seen:
                known.append(dev)
                seen.add(dev.address)
        return known

    def _split_address(self) -> tuple[str, int]:
        parts = self.address.strip().split(":")
        if len(parts) == 7:  # MAC plus explicit channel
            return ":".join(parts[:6]).upper(), int(parts[6])
        if len(parts) == 6:
            return self.address.strip().upper(), 0  # 0 = try each channel
        raise TransportError(f"bad Bluetooth address {self.address!r}")

    def _connect_sync(self) -> None:
        mac, channel = self._split_address()
        channels = [channel] if channel else [1, 2, 3, 4, 5, 6]
        last: Exception | None = None
        for chan in channels:
            sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            sock.settimeout(10.0)
            try:
                sock.connect((mac, chan))
            except OSError as exc:
                last = exc
                sock.close()
                continue
            self._sock = sock
            self.address = f"{mac}:{chan}"
            return
        raise TransportError(
            f"RFCOMM connect to {mac} failed on channels {channels}: {last}. "
            "Is the printer paired, powered on and not connected to a phone?"
        )

    async def connect(self) -> None:
        if not _HAVE_BT:
            raise TransportError(self.unavailable_reason)
        await asyncio.to_thread(self._connect_sync)
        self._connected = True

    async def disconnect(self) -> None:
        sock, self._sock = self._sock, None
        self._connected = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _write_sync(self, data: bytes) -> None:
        if self._sock is None:
            raise TransportError("not connected")
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise TransportError(f"RFCOMM write failed: {exc}") from exc

    async def write(self, data: bytes) -> None:
        await asyncio.to_thread(self._write_sync, data)

    def _read_sync(self, size: int, timeout: float) -> bytes:
        if self._sock is None:
            return b""
        self._sock.settimeout(max(0.05, timeout))
        try:
            return self._sock.recv(size)
        except OSError:
            return b""

    async def read(self, size: int = 64, timeout: float = 1.0) -> bytes:
        return await asyncio.to_thread(self._read_sync, size, timeout)

    async def probe(self) -> tuple[bool, str]:
        if not self._connected or self._sock is None:
            return False, "not connected"
        try:
            # Zero-length send costs nothing but fails once the link drops.
            await asyncio.to_thread(self._sock.send, b"")
        except OSError as exc:
            return False, f"link lost: {exc}"
        return True, f"RFCOMM {self.address}"
