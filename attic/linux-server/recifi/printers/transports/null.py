"""Virtual printer: spools to disk instead of paper.

Lets the whole stack (templates, render, protocol encoding) be exercised on a
laptop with no hardware, and gives you .bin dumps to diff against captures from
the vendor app.
"""

from __future__ import annotations

import time
from pathlib import Path

from ...config import data_dir
from ..base import Device, Transport


def spool_dir() -> Path:
    path = data_dir() / "spool"
    path.mkdir(parents=True, exist_ok=True)
    return path


class NullTransport(Transport):
    name = "null"
    address_hint = "ignored -- output goes to the spool directory"

    def __init__(self, address: str = "") -> None:
        super().__init__(address)
        self._path: Path | None = None

    @staticmethod
    async def discover() -> list[Device]:
        return [
            Device(
                transport="null",
                address="spool",
                name="Virtual printer",
                detail=f"writes to {spool_dir()}",
                likely=True,
            )
        ]

    async def connect(self) -> None:
        self._path = spool_dir() / f"job-{time.strftime('%Y%m%d-%H%M%S')}.bin"
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        self._path = None

    async def write(self, data: bytes) -> None:
        if self._path is None:
            raise RuntimeError("not connected")
        with self._path.open("ab") as handle:
            handle.write(data)

    async def probe(self) -> tuple[bool, str]:
        if not self._connected:
            return False, "not connected"
        return True, f"spooling to {self._path.name if self._path else '?'}"
