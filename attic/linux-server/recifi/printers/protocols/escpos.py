"""ESC/POS raster bit-image (GS v 0).

The default guess for an unknown thermal printer: nearly every Chinese 58mm
class device accepts it, including most rebadged label printers.
"""

from __future__ import annotations

from PIL import Image

from ...render import image_to_raster
from ..base import Protocol

ESC = 0x1B
GS = 0x1D

# Printers have a small line buffer; sending the whole label as one GS v 0 can
# overflow it. 128 rows per band is conservative and universally safe.
BAND_ROWS = 128


class ESCPOSProtocol(Protocol):
    name = "escpos"
    description = "ESC/POS raster (GS v 0). Safe default for generic thermal printers."

    def preamble(self) -> list[bytes]:
        out = [bytes([ESC, 0x40])]  # ESC @ -- initialise
        # DC2 # n: print density on the common Chinese firmware. Harmless
        # (ignored) on printers that don't implement it.
        out.append(bytes([0x12, 0x23, min(15, max(0, self.cfg.density))]))
        return out

    def encode(self, image: Image.Image) -> list[bytes]:
        data, row_bytes, rows = image_to_raster(image)
        blocks: list[bytes] = [bytes([ESC, 0x40]), bytes([ESC, 0x61, 0x00])]  # init, align left

        for top in range(0, rows, BAND_ROWS):
            height = min(BAND_ROWS, rows - top)
            chunk = data[top * row_bytes : (top + height) * row_bytes]
            header = bytes(
                [
                    GS,
                    0x76,
                    0x30,
                    0x00,  # m=0, normal density
                    row_bytes & 0xFF,
                    (row_bytes >> 8) & 0xFF,
                    height & 0xFF,
                    (height >> 8) & 0xFF,
                ]
            )
            blocks.append(header + chunk)

        feed = self.cfg.feed_dots
        while feed > 0:
            step = min(255, feed)
            blocks.append(bytes([ESC, 0x4A, step]))  # ESC J -- feed n dots
            feed -= step
        return blocks
