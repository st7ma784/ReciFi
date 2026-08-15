"""Byte-mode QR encoder, versions 1-10, all four EC levels.

Written from scratch because nothing suitable ships with MicroPython. Tables
are kept to the minimum the label use case needs (a URL or an asset id fits in
v1-10 comfortably; v10-L holds 271 bytes).

Everything else -- GF(256) tables, RS generator polynomials, BCH format and
version bits -- is computed at import/encode time rather than tabulated, which
trades a few ms of startup for a lot of flash.

    m = encode("https://example.com")
    m.size          # modules per side
    m.get(x, y)     # 1 = dark
"""

# EC level -> the two bits used in the format information.
L, M, Q, H = 0, 1, 2, 3
_LEVEL_BITS = (1, 0, 3, 2)
LEVELS = {"L": L, "M": M, "Q": Q, "H": H}

# (ec codewords per block, blocks in group 1, data cw each,
#                          blocks in group 2, data cw each) indexed [ver-1][lvl]
_ECC = (
    ((7, 1, 19, 0, 0), (10, 1, 16, 0, 0), (13, 1, 13, 0, 0), (17, 1, 9, 0, 0)),
    ((10, 1, 34, 0, 0), (16, 1, 28, 0, 0), (22, 1, 22, 0, 0), (28, 1, 16, 0, 0)),
    ((15, 1, 55, 0, 0), (26, 1, 44, 0, 0), (18, 2, 17, 0, 0), (22, 2, 13, 0, 0)),
    ((20, 1, 80, 0, 0), (18, 2, 32, 0, 0), (26, 2, 24, 0, 0), (16, 4, 9, 0, 0)),
    ((26, 1, 108, 0, 0), (24, 2, 43, 0, 0), (18, 2, 15, 2, 16), (22, 2, 11, 2, 12)),
    ((18, 2, 68, 0, 0), (16, 4, 27, 0, 0), (24, 4, 19, 0, 0), (28, 4, 15, 0, 0)),
    ((20, 2, 78, 0, 0), (18, 4, 31, 0, 0), (18, 2, 14, 4, 15), (26, 4, 13, 1, 14)),
    ((24, 2, 97, 0, 0), (22, 2, 38, 2, 39), (22, 4, 18, 2, 19), (26, 4, 14, 2, 15)),
    ((30, 2, 116, 0, 0), (22, 3, 36, 2, 37), (20, 4, 16, 4, 17), (24, 4, 12, 4, 13)),
    ((18, 2, 68, 2, 69), (26, 4, 43, 1, 44), (24, 6, 19, 2, 20), (28, 6, 15, 2, 16)),
)

# Alignment pattern centre coordinates per version (v1 has none).
_ALIGN = (
    (),
    (6, 18),
    (6, 22),
    (6, 26),
    (6, 30),
    (6, 34),
    (6, 22, 38),
    (6, 24, 42),
    (6, 26, 46),
    (6, 28, 50),
)

MAX_VERSION = 10

_EXP = None
_LOG = None


def _init_gf():
    """log/antilog tables for GF(256) with the QR primitive polynomial 0x11d."""
    global _EXP, _LOG
    if _EXP is not None:
        return
    exp = bytearray(512)
    log = bytearray(256)
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    _EXP, _LOG = exp, log


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(n):
    """Reed-Solomon generator polynomial of degree n."""
    poly = bytearray(1)
    poly[0] = 1
    for i in range(n):
        # multiply by (x + a^i)
        # poly is in descending degree, so multiplying by (x + a^i) shifts the
        # existing coefficients up a degree and scales a copy of them by a^i.
        nxt = bytearray(len(poly) + 1)
        for j in range(len(poly)):
            nxt[j] ^= poly[j]
            nxt[j + 1] ^= _mul(poly[j], _EXP[i])
        poly = nxt
    return poly


def _rs_encode(data, n):
    """n error-correction codewords for `data`."""
    gen = _generator(n)
    rem = bytearray(n)
    for byte in data:
        factor = byte ^ rem[0]
        rem[:-1] = rem[1:]
        rem[-1] = 0
        if factor:
            lf = _LOG[factor]
            for i in range(n):
                g = gen[i + 1]
                if g:
                    rem[i] ^= _EXP[_LOG[g] + lf]
    return rem


def _bit_length(value):
    # MicroPython's int has no .bit_length().
    n = 0
    while value:
        n += 1
        value >>= 1
    return n


def _bch(data, poly, poly_bits):
    """Remainder of `data` under `poly`, used for format/version bits."""
    rem = data
    n = _bit_length(rem)
    while n >= poly_bits:
        rem ^= poly << (n - poly_bits)
        n = _bit_length(rem)
    return rem


def _format_bits(level, mask):
    data = (_LEVEL_BITS[level] << 3) | mask
    return ((data << 10) | _bch(data << 10, 0x537, 11)) ^ 0x5412


def _version_bits(version):
    return (version << 12) | _bch(version << 12, 0x1F25, 13)


def _capacity(version, level):
    ec, b1, d1, b2, d2 = _ECC[version - 1][level]
    return b1 * d1 + b2 * d2


def _pick_version(length, level, min_version):
    for version in range(max(1, min_version), MAX_VERSION + 1):
        # 4 mode bits + count indicator + payload, in codewords.
        count_bits = 8 if version < 10 else 16
        needed = (4 + count_bits + length * 8 + 7) // 8
        if needed <= _capacity(version, level):
            return version
    raise ValueError("payload too long for a version-10 QR")


def _encode_data(text, version, level):
    """Mode + length + payload + padding, then interleave with EC codewords."""
    data = text.encode("utf-8") if isinstance(text, str) else text
    count_bits = 8 if version < 10 else 16
    total = _capacity(version, level)

    bits = bytearray()  # one entry per bit; simple and fast enough at this size

    def push(value, n):
        for i in range(n - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)  # byte mode
    push(len(data), count_bits)
    for byte in data:
        push(byte, 8)

    # Terminator, then pad to a byte boundary.
    for _ in range(min(4, total * 8 - len(bits))):
        bits.append(0)
    while len(bits) % 8:
        bits.append(0)

    codewords = bytearray(len(bits) // 8)
    for i in range(len(codewords)):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i * 8 + j]
        codewords[i] = byte

    # Alternating pad codewords fill the rest, starting with 0xEC.
    pad = 0
    while len(codewords) < total:
        codewords.append(0xEC if pad % 2 == 0 else 0x11)
        pad += 1

    ec_count, b1, d1, b2, d2 = _ECC[version - 1][level]
    blocks = []
    offset = 0
    for _ in range(b1):
        blocks.append(codewords[offset : offset + d1])
        offset += d1
    for _ in range(b2):
        blocks.append(codewords[offset : offset + d2])
        offset += d2
    ec_blocks = [_rs_encode(b, ec_count) for b in blocks]

    out = bytearray()
    for i in range(max(d1, d2)):
        for block in blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ec_count):
        for block in ec_blocks:
            out.append(block[i])
    return out


class Matrix:
    """A finished QR symbol. `get(x, y)` returns 1 for a dark module."""

    __slots__ = ("size", "modules", "version", "level")

    def __init__(self, size):
        self.size = size
        self.modules = bytearray(size * size)

    def get(self, x, y):
        return self.modules[y * self.size + x]


def _setup_function_patterns(grid, reserved, size, version):
    def finder(ox, oy):
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                x, y = ox + dx, oy + dy
                if not (0 <= x < size and 0 <= y < size):
                    continue
                edge = dx in (-1, 7) or dy in (-1, 7)
                ring = 0 <= dx <= 6 and 0 <= dy <= 6 and (
                    dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4)
                )
                grid[y * size + x] = 0 if edge else (1 if ring else 0)
                reserved[y * size + x] = 1

    finder(0, 0)
    finder(size - 7, 0)
    finder(0, size - 7)

    # Timing patterns.
    for i in range(8, size - 8):
        bit = 1 - (i & 1)
        grid[6 * size + i] = bit
        reserved[6 * size + i] = 1
        grid[i * size + 6] = bit
        reserved[i * size + 6] = 1

    # Alignment patterns, skipping the three that collide with finders.
    centres = _ALIGN[version - 1]
    for cy in centres:
        for cx in centres:
            if (cx < 8 and cy < 8) or (cx < 8 and cy > size - 9) or (cx > size - 9 and cy < 8):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    dark = max(abs(dx), abs(dy)) != 1
                    grid[(cy + dy) * size + cx + dx] = 1 if dark else 0
                    reserved[(cy + dy) * size + cx + dx] = 1

    # Reserve the format information areas (written later, per mask).
    for i in range(9):
        if not reserved[8 * size + i]:
            reserved[8 * size + i] = 1
        if not reserved[i * size + 8]:
            reserved[i * size + 8] = 1
    for i in range(8):
        reserved[8 * size + size - 1 - i] = 1
        reserved[(size - 1 - i) * size + 8] = 1
    grid[(size - 8) * size + 8] = 1  # always-dark module
    reserved[(size - 8) * size + 8] = 1

    if version >= 7:
        bits = _version_bits(version)
        for i in range(18):
            bit = (bits >> i) & 1
            r, c = i // 3, i % 3 + size - 11
            grid[r * size + c] = bit
            reserved[r * size + c] = 1
            grid[c * size + r] = bit
            reserved[c * size + r] = 1


def _mask(pattern, row, col):
    if pattern == 0:
        return (row + col) % 2 == 0
    if pattern == 1:
        return row % 2 == 0
    if pattern == 2:
        return col % 3 == 0
    if pattern == 3:
        return (row + col) % 3 == 0
    if pattern == 4:
        return (row // 2 + col // 3) % 2 == 0
    if pattern == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if pattern == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    return ((row + col) % 2 + (row * col) % 3) % 2 == 0


def _place_data(grid, reserved, size, data, pattern):
    index = 0
    total = len(data) * 8
    row = size - 1
    up = True
    col = size - 1
    while col > 0:
        if col == 6:  # the vertical timing pattern is not a data column
            col -= 1
        while True:
            for c in (col, col - 1):
                if reserved[row * size + c]:
                    continue
                bit = 0
                if index < total:
                    bit = (data[index >> 3] >> (7 - (index & 7))) & 1
                    index += 1
                if _mask(pattern, row, c):
                    bit ^= 1
                grid[row * size + c] = bit
            row = row - 1 if up else row + 1
            if row < 0 or row >= size:
                row = 0 if row < 0 else size - 1
                up = not up
                break
        col -= 2


def _place_format(grid, size, level, pattern):
    bits = _format_bits(level, pattern)
    for i in range(15):
        bit = (bits >> i) & 1
        # copy 1: down the left of the top-left finder, then right along row 8
        if i < 6:
            grid[i * size + 8] = bit
        elif i < 8:
            grid[(i + 1) * size + 8] = bit
        else:
            grid[(size - 15 + i) * size + 8] = bit
        # copy 2: right along row 8 from the far side, then down column 8
        if i < 8:
            grid[8 * size + size - 1 - i] = bit
        elif i < 9:
            grid[8 * size + 15 - i] = bit
        else:
            grid[8 * size + 14 - i] = bit


def _penalty(grid, size):
    """Sum of the four ISO penalty rules; lower is a better mask.

    This is the hot path -- it runs eight times per QR -- so the window tests
    are hand-unrolled with short-circuiting rather than built as tuples, and
    nothing in here allocates.
    """
    score = 0
    g = grid

    # Rules 1 and 3 in one pass per line, rows then columns. `step` walks
    # along the line, `start` moves between lines.
    for step, outer in ((1, size), (size, 1)):
        for line in range(size):
            base = line * outer if step == 1 else line
            # Rule 1: runs of five or more same-coloured modules.
            run = 1
            prev = g[base]
            for i in range(1, size):
                v = g[base + i * step]
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2  # 3 + (run - 5)
                    run = 1
                    prev = v
            if run >= 5:
                score += run - 2

            # Rule 3: the finder lookalike 1:1:3:1:1 with four light modules
            # to one side. The two orientations differ in their first module,
            # so only one chain is ever evaluated.
            for i in range(size - 10):
                p = base + i * step
                if g[p]:
                    if (
                        not g[p + step] and g[p + 2 * step] and g[p + 3 * step]
                        and g[p + 4 * step] and not g[p + 5 * step] and g[p + 6 * step]
                        and not g[p + 7 * step] and not g[p + 8 * step]
                        and not g[p + 9 * step] and not g[p + 10 * step]
                    ):
                        score += 40
                elif (
                    not g[p + step] and not g[p + 2 * step] and not g[p + 3 * step]
                    and g[p + 4 * step] and not g[p + 5 * step] and g[p + 6 * step]
                    and g[p + 7 * step] and g[p + 8 * step] and not g[p + 9 * step]
                    and g[p + 10 * step]
                ):
                    score += 40

    # Rule 2: 2x2 blocks of one colour.
    for y in range(size - 1):
        base = y * size
        for x in range(size - 1):
            v = g[base + x]
            if v == g[base + x + 1] == g[base + size + x] == g[base + size + x + 1]:
                score += 3

    # Rule 4: deviation from an even dark/light split.
    percent = sum(g) * 100 // (size * size)
    score += 10 * (abs(percent - 50) // 5)
    return score


_cache_key = None
_cache_value = None


def encode(text, level=M, min_version=1, mask=None):
    """Encode `text` and return a Matrix. `mask=None` picks the best of the 8.

    The last result is memoised: previewing and then printing the same label is
    the common case, and mask selection is by far the most expensive thing the
    renderer does.
    """
    global _cache_key, _cache_value
    _init_gf()
    if isinstance(level, str):
        level = LEVELS[level.upper()]
    key = (text, level, min_version, mask)
    if key == _cache_key:
        return _cache_value
    payload = text.encode("utf-8") if isinstance(text, str) else text
    version = _pick_version(len(payload), level, min_version)
    size = version * 4 + 17
    data = _encode_data(payload, version, level)

    base = bytearray(size * size)
    reserved = bytearray(size * size)
    _setup_function_patterns(base, reserved, size, version)

    best = None
    best_score = None
    for pattern in range(8) if mask is None else (mask,):
        grid = bytearray(base)
        _place_data(grid, reserved, size, data, pattern)
        _place_format(grid, size, level, pattern)
        if mask is not None:
            best, best_score = grid, 0
            break
        score = _penalty(grid, size)
        if best_score is None or score < best_score:
            best, best_score = grid, score

    matrix = Matrix(size)
    matrix.modules = best
    matrix.version = version
    matrix.level = level
    _cache_key, _cache_value = key, matrix
    return matrix
