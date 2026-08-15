"""Bitmap -> wire bytes, one function per printer command language.

Each encoder is a *generator* yielding blocks. A 384x800 label is ~38KB of
raster; building the whole command stream as one buffer would double that on a
board with roughly 200KB of heap, so nothing here accumulates.

The Label buffer is already MSB-first with 1 = burn, so the raster payload is
handed out as memoryview slices with no copy and no inversion.
"""

ESC = 0x1B
GS = 0x1D

# Printers have a small line buffer; one huge GS v 0 can overflow it.
BAND_ROWS = 64


def escpos(label, cfg):
    """ESC/POS raster (GS v 0). The safe default for generic thermal printers."""
    view = memoryview(label.buf)
    stride = label.stride

    yield bytes([ESC, 0x40])  # initialise
    yield bytes([ESC, 0x61, 0x00])  # align left
    # DC2 # n: print density on the common Chinese firmware; ignored elsewhere.
    yield bytes([0x12, 0x23, min(15, max(0, int(cfg.get("density", 3))))])

    for top in range(0, label.height, BAND_ROWS):
        rows = min(BAND_ROWS, label.height - top)
        yield bytes([GS, 0x76, 0x30, 0x00,
                     stride & 0xFF, (stride >> 8) & 0xFF,
                     rows & 0xFF, (rows >> 8) & 0xFF])
        yield view[top * stride : (top + rows) * stride]

    feed = int(cfg.get("feed_dots", 48))
    while feed > 0:
        step = min(255, feed)
        yield bytes([ESC, 0x4A, step])  # ESC J -- feed n dots
        feed -= step


def escpos_preamble(cfg):
    return [bytes([ESC, 0x40]),
            bytes([0x12, 0x23, min(15, max(0, int(cfg.get("density", 3))))])]


# ---------------------------------------------------------------- niimbot

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


def packet(kind, payload):
    """55 55 <type> <len> <payload> <xor checksum> AA AA"""
    checksum = kind ^ len(payload)
    for byte in payload:
        checksum ^= byte
    return b"\x55\x55" + bytes([kind, len(payload)]) + bytes(payload) + bytes([checksum]) + b"\xaa\xaa"


def _u16(value):
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


def _third_counts(line, width):
    """Black pixels in each third of a row; the firmware paces the head by it."""
    counts = [0, 0, 0]
    third = max(1, width // 3)
    for index in range(len(line)):
        byte = line[index]
        if not byte:
            continue
        for bit in range(8):
            if byte & (0x80 >> bit):
                x = index * 8 + bit
                if x >= width:
                    break
                counts[min(2, x // third)] += 1
    return bytes([min(255, c) for c in counts])


def niimbot(label, cfg):
    """Niimbot-style packets. Try this if ESC/POS does nothing."""
    buf = label.buf
    stride = label.stride
    width = label.width

    yield packet(SET_DENSITY, bytes([min(5, max(1, int(cfg.get("density", 3))))]))
    yield packet(SET_LABEL_TYPE, bytes([int(cfg.get("label_type", 1))]))
    yield packet(START_PRINT, b"\x01")
    yield packet(START_PAGE, b"\x01")
    yield packet(SET_DIMENSION, _u16(label.height) + _u16(width))
    yield packet(SET_QUANTITY, b"\x00\x01")

    blank = 0
    for y in range(label.height):
        line = buf[y * stride : (y + 1) * stride]
        if not any(line):
            blank += 1
            continue
        if blank:
            yield packet(BLANK_LINES, _u16(y - blank) + bytes([blank]))
            blank = 0
        yield packet(IMAGE_LINE, _u16(y) + _third_counts(line, width) + b"\x01" + line)
    if blank:
        yield packet(BLANK_LINES, _u16(label.height - blank) + bytes([blank]))

    yield packet(END_PAGE, b"\x01")
    yield packet(END_PRINT, b"\x01")


# ------------------------------------------------------------------ tspl

# How many bytes of inverted bitmap to build at a time. TSPL wants 1 = white,
# the opposite of our buffer, so the raster is the one thing we cannot hand
# over as a zero-copy view.
_INVERT_CHUNK = 256


def _mm(value):
    """TSPL takes decimals; keep them short and avoid a trailing '.0'."""
    if isinstance(value, float) and value != int(value):
        return "%.1f" % value
    return "%d" % int(value)


def tspl(label, cfg):
    """TSPL/TSPL2, as spoken by the ORGBRO Z3.

    Reconstructed from the vendor's ChromeOS driver, which builds exactly this
    sequence: media setup, CLS, one BITMAP, then PRINT.
    """
    post = cfg.get("post_print", "tear")
    if post == "peel":
        yield b"SET TEAR OFF\r\nSET PEEL ON\r\n"
    elif post == "none":
        yield b"SET PEEL OFF\r\nSET TEAR OFF\r\n"
    else:
        yield b"SET PEEL OFF\r\nSET TEAR ON\r\n"

    head = "SIZE %s mm,%s mm\r\n" % (
        _mm(cfg.get("label_width_mm", 40)),
        _mm(cfg.get("label_height_mm", 30)),
    )
    gap = cfg.get("gap_mm", None)
    if gap is not None:
        head += "GAP %s mm,0 mm\r\n" % _mm(gap)
    speed = int(cfg.get("speed", 0))
    if speed:
        head += "SPEED %d\r\n" % speed
    density = int(cfg.get("density", 0))
    if density:
        head += "DENSITY %d\r\n" % density
    head += "DIRECTION %d,0\r\n" % int(cfg.get("direction", 0))
    head += "CLS\r\n"
    yield head.encode()

    yield b"BITMAP 0,0,%d,%d,0," % (label.stride, label.height)

    buf = label.buf
    total = len(buf)
    for start in range(0, total, _INVERT_CHUNK):
        end = start + _INVERT_CHUNK
        if end > total:
            end = total
        chunk = bytearray(buf[start:end])
        for i in range(len(chunk)):
            chunk[i] = ~chunk[i] & 0xFF
        yield bytes(chunk)

    yield b"\r\nPRINT 1,1\r\n"


def raw(label, cfg):
    """Bare packed rows, no framing. For working out an unknown protocol."""
    view = memoryview(label.buf)
    for y in range(label.height):
        yield view[y * label.stride : (y + 1) * label.stride]


ENCODERS = {"tspl": tspl, "escpos": escpos, "niimbot": niimbot, "raw": raw}

DESCRIPTIONS = {
    "tspl": "TSPL/TSPL2 label commands. What the ORGBRO Z3 speaks.",
    "escpos": "ESC/POS raster (GS v 0). Common on receipt printers.",
    "niimbot": "Niimbot-style 55 55 packets. Used by the D11/B21 family.",
    "raw": "Packed 1-bit rows with no framing. For protocol spelunking.",
}


def encode(name, label, cfg):
    try:
        return ENCODERS[name](label, cfg)
    except KeyError:
        raise ValueError("unknown protocol %r" % name)


def preamble(name, cfg):
    """Sent once when a connection opens.

    TSPL has no init to send -- every job carries its own media setup -- and
    sending stray bytes to a TSPL printer just provokes a command error.
    """
    return escpos_preamble(cfg) if name == "escpos" else []
