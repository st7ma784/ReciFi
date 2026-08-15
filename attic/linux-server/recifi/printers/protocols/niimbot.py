"""Niimbot-family packet protocol.

Frame: 55 55 <type> <len> <payload...> <checksum> AA AA
where checksum = XOR of type, len and every payload byte.

Reconstructed from the public reverse-engineering of the D11/B21 line; a lot of
rebadged printers (this is the second thing to try if ESC/POS prints nothing)
speak the same thing. If the Z3 turns out to differ, this file is the one place
to change -- capture real traffic with the raw console first.
"""

from __future__ import annotations

from PIL import Image

from ...render import image_to_raster
from ..base import Protocol

SET_DENSITY = 0x21
SET_LABEL_TYPE = 0x23
START_PRINT = 0x01
START_PAGE = 0x03
SET_DIMENSION = 0x13
SET_QUANTITY = 0x15
IMAGE_LINE = 0x85
BLANK_LINES = 0x84
END_PAGE = 0xE3
END_PRINT = 0xF3


def packet(kind: int, payload: bytes) -> bytes:
    checksum = kind ^ len(payload)
    for byte in payload:
        checksum ^= byte
    return b"\x55\x55" + bytes([kind, len(payload)]) + payload + bytes([checksum]) + b"\xaa\xaa"


def _u16(value: int) -> bytes:
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


class NiimbotProtocol(Protocol):
    name = "niimbot"
    description = "Niimbot-style 55 55 packets. Try this if ESC/POS does nothing."

    def encode(self, image: Image.Image) -> list[bytes]:
        data, row_bytes, rows = image_to_raster(image)
        width = image.width

        blocks = [
            packet(SET_DENSITY, bytes([min(5, max(1, self.cfg.density))])),
            packet(SET_LABEL_TYPE, bytes([self.cfg.label_type])),
            packet(START_PRINT, b"\x01"),
            packet(START_PAGE, b"\x01"),
            # Dimension is rows then columns, big-endian.
            packet(SET_DIMENSION, _u16(rows) + _u16(width)),
            packet(SET_QUANTITY, b"\x00\x01"),
        ]

        blank_run = 0
        for y in range(rows):
            line = data[y * row_bytes : (y + 1) * row_bytes]
            if not any(line):
                blank_run += 1
                continue
            if blank_run:
                blocks.append(packet(BLANK_LINES, _u16(y - blank_run) + bytes([blank_run])))
                blank_run = 0
            # Header: row index, then the black-pixel count of each third of
            # the line (the firmware uses it to pace the head), then repeat=1.
            thirds = self._third_counts(line, width)
            blocks.append(packet(IMAGE_LINE, _u16(y) + thirds + b"\x01" + line))
        if blank_run:
            blocks.append(packet(BLANK_LINES, _u16(rows - blank_run) + bytes([blank_run])))

        blocks.append(packet(END_PAGE, b"\x01"))
        blocks.append(packet(END_PRINT, b"\x01"))
        return blocks

    @staticmethod
    def _third_counts(line: bytes, width: int) -> bytes:
        counts = [0, 0, 0]
        third = max(1, width // 3)
        for index, byte in enumerate(line):
            if not byte:
                continue
            for bit in range(8):
                if byte & (0x80 >> bit):
                    x = index * 8 + bit
                    if x >= width:
                        break
                    counts[min(2, x // third)] += 1
        return bytes(min(255, c) for c in counts)
