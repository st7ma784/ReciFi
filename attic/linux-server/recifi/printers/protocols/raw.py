"""Bare raster: just the packed bitmap, no command wrapper.

Useful in two situations -- a printer whose protocol you have already worked out
and are replaying by hand through the raw console, and confirming that bytes are
reaching the device at all (the head usually makes a noise even for garbage).
"""

from __future__ import annotations

from PIL import Image

from ...render import image_to_raster
from ..base import Protocol


class RawRasterProtocol(Protocol):
    name = "raw"
    description = "Packed 1-bit rows with no framing. For protocol spelunking."

    def encode(self, image: Image.Image) -> list[bytes]:
        data, row_bytes, rows = image_to_raster(image)
        return [data[y * row_bytes : (y + 1) * row_bytes] for y in range(rows)]
