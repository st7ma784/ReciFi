"""USB transport.

Two ways in, tried in this order:

  1. A kernel line-printer node -- ``/dev/usb/lp0``. Works when the printer
     declares USB class 07 (printer). Nothing to install.
  2. Raw bulk endpoints via pyusb -- ``usb:VID:PID``. Needed for the many
     cheap label printers that expose a vendor-specific class instead.

For (2) on Raspberry Pi OS you need a udev rule so the pi user can claim the
device -- see README.
"""

from __future__ import annotations

import asyncio
import glob
import os
from pathlib import Path

from ..base import Device, Transport, TransportError

try:  # pyusb is optional; the /dev node path does not need it.
    import usb.core
    import usb.util

    _HAVE_PYUSB = True
except Exception:  # pragma: no cover - import guard
    _HAVE_PYUSB = False

_PRINTER_CLASS = 0x07


def _sysfs_name(node: str) -> str:
    """Pull a human name for /dev/usb/lpN out of sysfs, if it is there."""
    base = Path(node).name
    for candidate in glob.glob(f"/sys/class/usbmisc/{base}/device/../*"):
        for attr in ("product", "manufacturer"):
            path = Path(candidate) / attr
            try:
                if path.exists():
                    return path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
    return ""


def _scan_sync() -> list[Device]:
    found: list[Device] = []

    for node in sorted(glob.glob("/dev/usb/lp*") + glob.glob("/dev/lp*")):
        writable = os.access(node, os.W_OK)
        found.append(
            Device(
                transport="usb",
                address=node,
                name=_sysfs_name(node) or node,
                detail="kernel printer node" + ("" if writable else " (no write permission)"),
                likely=writable,
            )
        )

    if _HAVE_PYUSB:
        try:
            devices = list(usb.core.find(find_all=True))
        except Exception as exc:  # pragma: no cover - libusb missing
            devices = []
            found.append(
                Device(
                    transport="usb",
                    address="",
                    name="libusb unavailable",
                    detail=str(exc),
                )
            )
        for dev in devices:
            classes = {dev.bDeviceClass}
            try:
                for cfg in dev:
                    for intf in cfg:
                        classes.add(intf.bInterfaceClass)
            except Exception:
                pass
            if 0x09 in classes and len(classes) == 1:  # skip root/hubs
                continue
            name = ""
            try:
                name = " ".join(
                    p for p in (dev.manufacturer, dev.product) if p
                ).strip()
            except Exception:
                pass  # reading strings needs permission we may not have
            found.append(
                Device(
                    transport="usb",
                    address=f"usb:{dev.idVendor:04x}:{dev.idProduct:04x}",
                    name=name or f"USB {dev.idVendor:04x}:{dev.idProduct:04x}",
                    detail=f"bus {dev.bus} addr {dev.address}, classes {sorted(classes)}",
                    likely=_PRINTER_CLASS in classes,
                )
            )
    return found


class USBTransport(Transport):
    name = "usb"
    address_hint = "/dev/usb/lp0  or  usb:VVVV:PPPP"

    def __init__(self, address: str = "") -> None:
        super().__init__(address)
        self._fd: int | None = None
        self._dev = None
        self._out_ep = None
        self._in_ep = None

    @staticmethod
    async def discover() -> list[Device]:
        try:
            return await asyncio.to_thread(_scan_sync)
        except Exception as exc:  # pragma: no cover - defensive
            return [Device(transport="usb", address="", name="scan failed", detail=str(exc))]

    # -- connect ----------------------------------------------------------

    def _connect_node(self) -> None:
        try:
            self._fd = os.open(self.address, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as exc:
            raise TransportError(
                f"{self.address}: permission denied. Add your user to the 'lp' group "
                "and re-login, or use a udev rule."
            ) from exc
        except OSError as exc:
            raise TransportError(f"{self.address}: {exc}") from exc

    def _connect_libusb(self) -> None:
        if not _HAVE_PYUSB:
            raise TransportError("pyusb is not installed -- pip install pyusb")
        try:
            _, vid, pid = self.address.split(":")
            vendor, product = int(vid, 16), int(pid, 16)
        except ValueError as exc:
            raise TransportError(f"bad USB address {self.address!r}, expected usb:VVVV:PPPP") from exc

        dev = usb.core.find(idVendor=vendor, idProduct=product)
        if dev is None:
            raise TransportError(f"no USB device {vid}:{pid} attached")

        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except Exception:
            pass  # not supported on every platform, and not always needed
        try:
            dev.set_configuration()
        except usb.core.USBError as exc:
            raise TransportError(f"cannot configure device: {exc}") from exc

        intf = dev.get_active_configuration()[(0, 0)]
        self._out_ep = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_OUT,
        )
        self._in_ep = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_IN,
        )
        if self._out_ep is None:
            raise TransportError("device has no bulk OUT endpoint")
        self._dev = dev

    async def connect(self) -> None:
        if not self.address:
            raise TransportError("no USB address set")
        if self.address.startswith("usb:"):
            await asyncio.to_thread(self._connect_libusb)
        else:
            await asyncio.to_thread(self._connect_node)
        self._connected = True

    async def disconnect(self) -> None:
        fd, dev = self._fd, self._dev
        self._fd = self._dev = self._out_ep = self._in_ep = None
        self._connected = False
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if dev is not None and _HAVE_PYUSB:
            try:
                usb.util.dispose_resources(dev)
            except Exception:
                pass

    # -- io ---------------------------------------------------------------

    def _write_sync(self, data: bytes) -> None:
        if self._fd is not None:
            view = memoryview(data)
            while view:
                try:
                    written = os.write(self._fd, view[:4096])
                except BlockingIOError:
                    continue  # printer buffer full, retry
                except OSError as exc:
                    raise TransportError(f"USB write failed: {exc}") from exc
                view = view[written:]
        elif self._out_ep is not None:
            try:
                self._out_ep.write(data, timeout=5000)
            except Exception as exc:
                raise TransportError(f"USB bulk write failed: {exc}") from exc
        else:
            raise TransportError("not connected")

    async def write(self, data: bytes) -> None:
        await asyncio.to_thread(self._write_sync, data)

    def _read_sync(self, size: int, timeout: float) -> bytes:
        if self._in_ep is not None:
            try:
                return bytes(self._in_ep.read(size, timeout=int(timeout * 1000)))
            except Exception:
                return b""
        if self._fd is not None:
            try:
                return os.read(self._fd, size)
            except OSError:
                return b""
        return b""

    async def read(self, size: int = 64, timeout: float = 1.0) -> bytes:
        return await asyncio.to_thread(self._read_sync, size, timeout)

    async def probe(self) -> tuple[bool, str]:
        if not self._connected:
            return False, "not connected"
        if self._fd is not None:
            if not Path(self.address).exists():
                return False, "device node disappeared (unplugged?)"
            return True, f"{self.address} open"
        if self._dev is not None and _HAVE_PYUSB:
            try:
                if usb.core.find(idVendor=self._dev.idVendor, idProduct=self._dev.idProduct) is None:
                    return False, "device no longer enumerated"
            except Exception as exc:
                return False, str(exc)
            return True, f"{self.address} claimed"
        return False, "no handle"
