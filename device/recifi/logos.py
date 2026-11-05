"""Black-and-white logos for labels, stored on flash as binary PBM (P4).

P4 is the one image format whose pixel data is already our framebuffer: rows
packed MSB first, 1 = black, each row padded to a whole byte. So a logo is
loaded with a single readinto and blitted, never decoded.

The browser does the hard part -- decoding PNG/JPEG, resizing, thresholding
to 1 bit -- because it has a real image stack and the Pico does not. The upload
is just the packed bits plus width and height.
"""

import os

DIR = "logos"
MAX_SIDE = 600
MAX_LOGOS = 16


def stride(width):
    return (width + 7) // 8


def data_size(width, height):
    return stride(width) * height


def clean_name(name):
    """Logo names become file names, so keep them to [a-z0-9-_]."""
    out = []
    for ch in str(name or "").lower():
        if "a" <= ch <= "z" or "0" <= ch <= "9" or ch in "-_":
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:32]


def _path(name):
    return "%s/%s.pbm" % (DIR, name)


def _ensure_dir():
    try:
        os.mkdir(DIR)
    except OSError:
        pass


def _read_header(handle):
    magic = handle.readline().strip()
    if magic != b"P4":
        raise ValueError("not a P4 bitmap")
    dims = handle.readline().split()
    width, height = int(dims[0]), int(dims[1])
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        raise ValueError("bad logo size %dx%d" % (width, height))
    return width, height


def names():
    try:
        entries = os.listdir(DIR)
    except OSError:
        return []
    out = [e[:-4] for e in entries if e.endswith(".pbm")]
    out.sort()
    return out


def info(name):
    name = clean_name(name)
    if not name:
        return None
    try:
        with open(_path(name), "rb") as handle:
            width, height = _read_header(handle)
    except (OSError, ValueError, IndexError):
        return None
    return {"name": name, "width": width, "height": height}


def listing():
    out = []
    for name in names():
        item = info(name)
        if item:
            out.append(item)
    return out


def check_upload(name, width, height, length):
    """Validate an upload before reading a byte of it. Returns an error or None."""
    if not clean_name(name):
        return "logo name must contain letters or digits"
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        return "logo must be 1-%d px on each side, got %dx%d" % (MAX_SIDE, width, height)
    if length != data_size(width, height):
        return "expected %d bytes of packed 1-bit pixels for %dx%d, got %d" % (
            data_size(width, height), width, height, length)
    if clean_name(name) not in names() and len(names()) >= MAX_LOGOS:
        return "already %d logos stored; delete one first" % MAX_LOGOS
    return None


async def save_stream(name, width, height, read):
    """Write an upload to flash as it arrives. `read(n)` is an async reader.

    Streamed through a small buffer: a 600x600 logo is 45KB, which the Pico can
    hold, but there is no reason to make it.
    """
    name = clean_name(name)
    _ensure_dir()
    path = _path(name)
    tmp = path + ".tmp"
    remaining = data_size(width, height)
    with open(tmp, "wb") as handle:
        handle.write(b"P4\n%d %d\n" % (width, height))
        while remaining:
            chunk = await read(min(512, remaining))
            if not chunk:
                break
            handle.write(chunk)
            remaining -= len(chunk)
    if remaining:
        os.remove(tmp)
        raise ValueError("upload ended %d bytes short" % remaining)
    try:
        os.remove(path)
    except OSError:
        pass
    os.rename(tmp, path)
    return {"name": name, "width": width, "height": height}


def delete(name):
    try:
        os.remove(_path(clean_name(name)))
        return True
    except OSError:
        return False


class Bitmap:
    """A loaded logo. Shaped like render.Label so bmp_stream can serve it."""

    def __init__(self, width, height, buf):
        import framebuf

        self.width = width
        self.height = height
        self.stride = stride(width)
        self.buf = buf
        self.fb = framebuf.FrameBuffer(buf, width, height, framebuf.MONO_HLSB)


def load(name):
    """The logo as a Bitmap, or None if there is no such logo."""
    name = clean_name(name)
    if not name:
        return None
    try:
        with open(_path(name), "rb") as handle:
            width, height = _read_header(handle)
            buf = bytearray(data_size(width, height))
            handle.readinto(buf)
    except (OSError, ValueError, IndexError):
        return None
    return Bitmap(width, height, buf)


def fit(bitmap, max_w, max_h):
    """Scale to fit inside max_w x max_h, keeping the aspect ratio.

    Nearest-neighbour, so a 1-bit logo stays crisp rather than going grey. The
    browser already sizes uploads sensibly, so this only runs when a template
    asks for a smaller logo than was stored.
    """
    max_w = max(1, int(max_w))
    max_h = max(1, int(max_h))
    w, h = bitmap.width, bitmap.height
    if w <= max_w and h <= max_h:
        return bitmap
    # Integer arithmetic throughout: scale = min(max_w/w, max_h/h).
    if max_w * h <= max_h * w:
        nw, nh = max_w, max(1, h * max_w // w)
    else:
        nw, nh = max(1, w * max_h // h), max_h
    out = Bitmap(nw, nh, bytearray(data_size(nw, nh)))
    src = bitmap.fb
    dst = out.fb
    xs = [x * w // nw for x in range(nw)]
    for y in range(nh):
        sy = y * h // nh
        for x in range(nw):
            if src.pixel(xs[x], sy):
                dst.pixel(x, y, 1)
    return out
