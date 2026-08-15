"""Name -> class lookup for transports and protocols."""

from __future__ import annotations

from ..models import PrinterConfig
from .base import Protocol, Transport
from .protocols import ESCPOSProtocol, NiimbotProtocol, RawRasterProtocol
from .transports import BLETransport, BluetoothTransport, NullTransport, USBTransport

TRANSPORTS: dict[str, type[Transport]] = {
    "null": NullTransport,
    "usb": USBTransport,
    "bluetooth": BluetoothTransport,
    "ble": BLETransport,
}

PROTOCOLS: dict[str, type[Protocol]] = {
    "escpos": ESCPOSProtocol,
    "niimbot": NiimbotProtocol,
    "raw": RawRasterProtocol,
}


def make_transport(name: str, address: str) -> Transport:
    try:
        cls = TRANSPORTS[name]
    except KeyError:
        raise ValueError(f"unknown transport {name!r}") from None
    return cls(address)


def make_protocol(name: str, cfg: PrinterConfig) -> Protocol:
    try:
        cls = PROTOCOLS[name]
    except KeyError:
        raise ValueError(f"unknown protocol {name!r}") from None
    return cls(cfg)
