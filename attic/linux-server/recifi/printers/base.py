"""Transport and protocol interfaces.

A *transport* moves bytes to the printer (USB, Bluetooth, a file). A *protocol*
turns a bitmap into the bytes that particular printer understands. They are
independent so an unknown printer can be worked out one axis at a time.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from ..models import PrinterConfig


class TransportError(RuntimeError):
    """Anything that stops bytes reaching the printer."""


@dataclass
class Device:
    """One candidate printer found by a transport's discovery scan."""

    transport: str
    address: str
    name: str = ""
    detail: str = ""
    # True when we are reasonably sure this is a label printer, not e.g. a
    # keyboard that happened to show up in the same scan.
    likely: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class Transport(abc.ABC):
    """Byte pipe to the printer. Implementations must be safe to re-connect."""

    name: str = "base"
    #: Shown in the UI next to the address field.
    address_hint: str = ""
    #: False when the transport can't run here (missing module, wrong OS).
    available: bool = True
    unavailable_reason: str = ""

    def __init__(self, address: str = "") -> None:
        self.address = address
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @staticmethod
    async def discover() -> list[Device]:
        """Best-effort scan. Never raises; returns [] when it can't look."""
        return []

    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def disconnect(self) -> None: ...

    @abc.abstractmethod
    async def write(self, data: bytes) -> None: ...

    async def read(self, size: int = 64, timeout: float = 1.0) -> bytes:
        """Read a reply if the link is bidirectional; b"" if it isn't."""
        return b""

    async def probe(self) -> tuple[bool, str]:
        """Cheap liveness check for the health poller.

        Returns (ok, detail). Default: trust the connection flag, since many
        of these printers have no status channel at all.
        """
        if not self._connected:
            return False, "not connected"
        return True, "link open"


class Protocol(abc.ABC):
    """Bitmap -> wire bytes for one printer command language."""

    name: str = "base"
    description: str = ""
    #: Bytes per write when the transport prefers small chunks (BLE MTU etc).
    chunk_size: int = 512

    def __init__(self, cfg: PrinterConfig) -> None:
        self.cfg = cfg

    @abc.abstractmethod
    def encode(self, image: Image.Image) -> list[bytes]:
        """Return the byte blocks to send, in order, to print `image` once."""

    def preamble(self) -> list[bytes]:
        """Sent once when a connection opens (init, density, media type)."""
        return []
