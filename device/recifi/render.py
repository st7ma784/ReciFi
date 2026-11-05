"""Template + payload -> a 1-bit label bitmap, using framebuf.

The buffer is MONO_HLSB with 1 = burn, which is byte-for-byte what the raster
protocols want, so the printer path never copies or inverts the bitmap.

Text is the built-in 8x8 framebuf font, drawn at integer scales (1-4). That is
the only font MicroPython gives us for free, and scaling it keeps the flash
budget for templates instead of glyph tables.
"""

import framebuf

from . import logos as logomod
from . import qr as qrmod

FONT_W = 8
FONT_H = 8

# Layout defaults, also the fallbacks for a half-filled template.
DEFAULTS = {
    "title": "",
    "fields": (),
    "qr": None,
    "footer": "",
    "auto_fields": False,
    "title_scale": 3,
    "text_scale": 2,
    "margin": 8,
    "line_gap": 4,
    "align": "left",
    "min_height": 0,
    "logo": None,
}


def _opt(tpl, key):
    value = tpl.get(key, None)
    return DEFAULTS[key] if value is None else value


# ---------------------------------------------------------------- payloads


def _stringify(value):
    if value is None:
        return ""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(_stringify(v) for v in value)
    if isinstance(value, dict):
        return ", ".join("%s: %s" % (k, _stringify(v)) for k, v in value.items())
    if isinstance(value, float):
        text = "%f" % value
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value)


def normalise_payload(data):
    """Coerce whatever arrived on the wire into a flat mapping.

    A bare string lands under "value" and "text"; a list of pairs or of
    {label, value} objects becomes real keys; nested objects are flattened with
    dotted keys so {{addr.city}} resolves.
    """
    if data is None:
        return {}
    if isinstance(data, str):
        return {"value": data, "text": data}
    if isinstance(data, list):
        out = {}
        for index, item in enumerate(data):
            if isinstance(item, dict) and "label" in item:
                out[_stringify(item["label"])] = item.get("value", "")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                out[_stringify(item[0])] = item[1]
            else:
                out["item%d" % (index + 1)] = item
        return out
    if isinstance(data, dict):
        flat = {}
        for key, value in data.items():
            flat[str(key)] = value
            if isinstance(value, dict):
                for sub, subval in value.items():
                    flat["%s.%s" % (key, sub)] = subval
        return flat
    return {"value": data, "text": data}


def substitute(text, data, used=None):
    """Expand {{key}} against `data`, case-insensitively.

    Hand-rolled rather than regex-based: MicroPython's `re` has no replacement
    callback, and this is a hot enough path to not want one anyway.
    """
    if not text or "{{" not in text:
        return text or ""
    lowered = None
    out = []
    pos = 0
    while True:
        start = text.find("{{", pos)
        if start < 0:
            out.append(text[pos:])
            break
        end = text.find("}}", start)
        if end < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:start])
        key = text[start + 2 : end].strip()
        if key in data:
            match = key
        else:
            if lowered is None:
                lowered = {}
                for k in data:
                    lowered[k.lower()] = k
            match = lowered.get(key.lower())
        if match is not None:
            out.append(_stringify(data[match]))
            if used is not None:
                used.add(match)
        pos = end + 2
    return "".join(out).strip()


def _placeholder_names(text, into):
    pos = 0
    while text:
        start = text.find("{{", pos)
        if start < 0:
            return
        end = text.find("}}", start)
        if end < 0:
            return
        into.add(text[start + 2 : end].strip().lower())
        pos = end + 2


def resolve(tpl, data):
    """Apply the payload to the template, producing plain rendered strings."""
    values = normalise_payload(data)
    used = set()

    title = substitute(_opt(tpl, "title"), values, used)
    footer = substitute(_opt(tpl, "footer"), values, used)

    rows = []
    for field in _opt(tpl, "fields"):
        label_tpl = field.get("label", "")
        value_tpl = field.get("value", "")
        label = substitute(label_tpl, values, used)
        value = substitute(value_tpl, values, used)
        # Drop a row whose placeholders all went missing, so one template can
        # cover partial payloads without printing bare labels with no value.
        if not label and not value:
            continue
        if not value and "{{" in value_tpl:
            continue
        rows.append((label, value, bool(field.get("emphasis"))))

    qr_value = ""
    qr_caption = ""
    spec = _opt(tpl, "qr")
    if spec:
        qr_value = substitute(spec.get("value", ""), values, used)
        qr_caption = substitute(spec.get("caption", ""), values, used)
        if not qr_value and isinstance(data, str):
            qr_value = data

    if _opt(tpl, "auto_fields"):
        skip = set()
        _placeholder_names(_opt(tpl, "title"), skip)
        _placeholder_names(_opt(tpl, "footer"), skip)
        for field in _opt(tpl, "fields"):
            _placeholder_names(field.get("label", ""), skip)
            _placeholder_names(field.get("value", ""), skip)
        if spec:
            _placeholder_names(spec.get("value", ""), skip)
            _placeholder_names(spec.get("caption", ""), skip)
        for key in used:
            skip.add(key.lower())
        # Sorted, because MicroPython dicts do not preserve insertion order --
        # unsorted here means the row order changes between identical prints.
        # Send a list of {label, value} pairs to control the order explicitly.
        for key in sorted(values.keys()):
            value = values[key]
            if key.lower() in skip or "." in key or isinstance(value, dict):
                continue
            rows.append((key, _stringify(value), False))

    logo_spec = _opt(tpl, "logo")
    logo = substitute(logo_spec.get("name", ""), values) if logo_spec else ""

    return {
        "title": title,
        "rows": rows,
        "footer": footer,
        "qr_value": qr_value,
        "qr_caption": qr_caption,
        "logo": logo,
    }


# ------------------------------------------------------------------ canvas


class Label:
    """A 1-bit bitmap. `buf` is raster-ready: MSB first, 1 = burn."""

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.stride = (width + 7) // 8
        self.buf = bytearray(self.stride * height)
        self.fb = framebuf.FrameBuffer(self.buf, width, height, framebuf.MONO_HLSB)

    def row(self, y):
        return self.buf[y * self.stride : (y + 1) * self.stride]

    def is_blank_row(self, y):
        base = y * self.stride
        for i in range(base, base + self.stride):
            if self.buf[i]:
                return False
        return True


def _wrap(text, max_chars):
    """Greedy word wrap, hard-breaking any word wider than the label."""
    if max_chars < 1:
        max_chars = 1
    lines = []
    for paragraph in _stringify(text).split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            if len(current) + 1 + len(word) <= max_chars:
                current += " " + word
            else:
                lines.append(current)
                current = word
        lines.append(current)
    out = []
    for line in lines:
        while len(line) > max_chars:
            out.append(line[:max_chars])
            line = line[max_chars:]
        out.append(line)
    return out


def _draw_text(fb, text, x, y, scale):
    """Draw with the 8x8 font, scaled by an integer factor."""
    if not text:
        return
    if scale <= 1:
        fb.text(text, x, y, 1)
        return
    width = len(text) * FONT_W
    stride = (width + 7) // 8
    tmp = bytearray(stride * FONT_H)
    tfb = framebuf.FrameBuffer(tmp, width, FONT_H, framebuf.MONO_HLSB)
    tfb.text(text, 0, 0, 1)
    for sy in range(FONT_H):
        py = y + sy * scale
        for sx in range(width):
            if tfb.pixel(sx, sy):
                fb.fill_rect(x + sx * scale, py, scale, scale, 1)


def _draw_qr(fb, matrix, x, y, scale):
    for my in range(matrix.size):
        py = y + my * scale
        for mx in range(matrix.size):
            if matrix.get(mx, my):
                fb.fill_rect(x + mx * scale, py, scale, scale, 1)


# ------------------------------------------------------------------ layout


def _qr_matrix(value, cfg):
    return qrmod.encode(
        value,
        cfg.get("qr_level", "M"),
        mask=cfg.get("qr_mask", None),
    )


def _fit_qr(matrix, requested, limit, min_scale=1):
    """Module scale for a QR: the request or `min_scale`, shrunk to fit `limit`.

    min_scale exists because a QR that is too small simply will not scan. At
    203dpi one dot is 0.125mm, so scale 4 gives 0.5mm modules -- about the floor
    for a phone camera once thermal bleed has closed the gaps a little.
    """
    modules = matrix.size + 8  # 4-module quiet zone each side
    scale = max(1, int(requested), int(min_scale))
    while scale > 1 and modules * scale > limit:
        scale -= 1
    return scale, modules * scale


def _build(tpl, parts, width, cfg, include_qr, top_logo=None):
    """Produce a display list plus its total height, without drawing."""
    title_scale = max(1, int(_opt(tpl, "title_scale")))
    text_scale = max(1, int(_opt(tpl, "text_scale")))
    small_scale = max(1, text_scale - 1)
    gap = int(_opt(tpl, "line_gap"))
    align = _opt(tpl, "align")
    spec = _opt(tpl, "qr")

    items = []
    height = 0

    def add_gap():
        nonlocal height
        items.append(("gap", None, gap, 0))
        height += gap

    def add_lines(text, scale, how):
        nonlocal height
        lines = _wrap(text, width // (FONT_W * scale))
        for line in lines:
            items.append(("text", line, scale, how))
            height += FONT_H * scale
        add_gap()

    if top_logo is not None:
        items.append(("logo", top_logo, 0, 0))
        height += top_logo.height
        add_gap()

    matrix = None
    if include_qr and parts["qr_value"]:
        matrix = _qr_matrix(parts["qr_value"], cfg)
        scale, size = _fit_qr(matrix, spec.get("scale", 3) if spec else 3, width,
                              cfg.get("qr_min_scale", 4))
        position = spec.get("position", "bottom") if spec else "bottom"
        if position == "top":
            items.append(("qr", matrix, scale, size))
            height += size
            add_gap()
            if parts["qr_caption"]:
                add_lines(parts["qr_caption"], small_scale, "center")

    if parts["title"]:
        # Shrink a title that would otherwise wrap mid-word, but not below the
        # body text size -- past that, wrapping reads better than tiny.
        ts = title_scale
        while ts > text_scale and len(parts["title"]) * FONT_W * ts > width:
            ts -= 1
        add_lines(parts["title"], ts, align)
        items.append(("rule", None, 0, 0))
        height += gap + 2

    # One shared value column so rows line up, unless the longest label eats
    # more than half the label -- then every row falls back to "label: value".
    label_col = 0
    for label, value, _ in parts["rows"]:
        if label and value:
            label_col = max(label_col, (len(label) + 2) * FONT_W * text_scale)
    if label_col > width * 55 // 100:
        label_col = 0

    for label, value, emphasis in parts["rows"]:
        scale = min(4, text_scale + 1) if emphasis else text_scale
        if label and value and label_col:
            lines = _wrap(value, max(1, (width - label_col) // (FONT_W * scale)))
            items.append(("row", (label, lines), scale, label_col))
            height += FONT_H * scale * len(lines)
            add_gap()
        else:
            add_lines("%s: %s" % (label, value) if label and value else (label or value),
                      scale, align)

    if matrix is not None and spec and spec.get("position", "bottom") == "bottom":
        scale, size = _fit_qr(matrix, spec.get("scale", 3), width,
                              cfg.get("qr_min_scale", 4))
        items.append(("qr", matrix, scale, size))
        height += size
        add_gap()
        if parts["qr_caption"]:
            add_lines(parts["qr_caption"], small_scale, "center")

    if parts["footer"]:
        items.append(("rule", None, 0, 0))
        height += gap + 2
        add_lines(parts["footer"], small_scale, align)

    return items, height


def _draw(fb, items, width, x0, y0, gap):
    y = y0
    for kind, payload, scale, extra in items:
        if kind == "text":
            x = x0
            if extra == "center":
                x = x0 + max(0, (width - len(payload) * FONT_W * scale) // 2)
            _draw_text(fb, payload, x, y, scale)
            y += FONT_H * scale
        elif kind == "rule":
            y += gap
            fb.fill_rect(x0, y, width, 2, 1)
            y += 2
        elif kind == "qr":
            _draw_qr(fb, payload, x0 + max(0, (width - extra) // 2) + 4 * scale, y + 4 * scale, scale)
            y += extra
        elif kind == "logo":
            # key=0: white logo pixels are transparent, so only black burns.
            fb.blit(payload.fb, x0 + max(0, (width - payload.width) // 2), y, 0)
            y += payload.height
        elif kind == "row":
            label, lines = payload
            _draw_text(fb, label, x0, y, scale)
            for line in lines:
                _draw_text(fb, line, x0 + extra, y, scale)
                y += FONT_H * scale
        elif kind == "gap":
            y += scale
    return y


def dots_for_mm(mm, dpi):
    """Millimetres to printer dots, rounded up to a whole byte."""
    dots = int(float(mm) * float(dpi) / 25.4 + 0.5)
    if dots % 8:
        dots += 8 - (dots % 8)
    return dots


def calibration(cfg):
    """A test pattern that makes the label geometry self-evident.

    Prints a border at the exact bitmap bounds, corner markers, and a tick
    every 20 dots with a number every 100. Whatever is missing off an edge
    tells you which way the media settings are wrong.
    """
    width = int(cfg.get("width_dots", 320))
    height = dots_for_mm(cfg.get("label_height_mm", 30), cfg.get("dpi", 203))
    limit = int(cfg.get("max_height", 800))
    if height > limit:
        height = limit - (limit % 8)

    label = Label(width, height)
    fb = label.fb

    fb.rect(0, 0, width, height, 1)
    fb.rect(1, 1, width - 2, height - 2, 1)

    # Solid 8x8 blocks in each corner: if one is missing, that edge is off.
    for cx, cy in ((0, 0), (width - 8, 0), (0, height - 8), (width - 8, height - 8)):
        fb.fill_rect(cx, cy, 8, 8, 1)

    for x in range(0, width, 20):
        long_tick = x % 100 == 0
        fb.fill_rect(x, 0, 1, 12 if long_tick else 6, 1)
        if long_tick and x:
            _draw_text(fb, str(x), x + 2, 14, 1)
    for y in range(0, height, 20):
        long_tick = y % 100 == 0
        fb.fill_rect(0, y, 12 if long_tick else 6, 1, 1)
        if long_tick and y:
            _draw_text(fb, str(y), 14, y + 2, 1)

    lines = [
        "%dx%d dots" % (width, height),
        "%sx%s mm" % (cfg.get("label_width_mm", "?"), cfg.get("label_height_mm", "?")),
        "%s dpi" % cfg.get("dpi", 203),
    ]
    y = height // 2 - len(lines) * 8
    for line in lines:
        _draw_text(fb, line, max(16, (width - len(line) * FONT_W * 2) // 2), y, 2)
        y += FONT_H * 2 + 2
    return label


def render(tpl, data, cfg):
    """Full pipeline: template + payload -> Label sized to its content."""
    parts = resolve(tpl, data)
    width = int(cfg.get("width_dots", 384))
    margin = int(_opt(tpl, "margin"))
    gap = int(_opt(tpl, "line_gap"))
    inner = max(FONT_W, width - margin * 2)
    spec = _opt(tpl, "qr")
    limit = int(cfg.get("max_height", 1200))
    avail_h = max(FONT_H, limit - margin * 2)

    # Side columns: position -> (kind, thing, width, height). A QR and a logo
    # can share a label as long as they take different sides.
    sides = {}
    side_qr = False
    if spec and parts["qr_value"] and spec.get("position", "bottom") in ("left", "right"):
        matrix = _qr_matrix(parts["qr_value"], cfg)
        scale, size = _fit_qr(matrix, spec.get("scale", 3), inner // 2,
                              cfg.get("qr_min_scale", 4))
        sides[spec["position"]] = ("qr", (matrix, scale), size, size)
        side_qr = True

    logo_spec = _opt(tpl, "logo")
    logo = logomod.load(parts["logo"]) if logo_spec and parts["logo"] else None
    want = int(logo_spec.get("size", 0) or 0) if logo else 0
    top_logo = None
    if logo is not None:
        position = logo_spec.get("position", "top")
        if position in ("left", "right") and position not in sides:
            box_w = min(want, inner // 2) if want else inner // 2
            logo = logomod.fit(logo, box_w, min(want, avail_h) if want else avail_h)
            sides[position] = ("logo", logo, logo.width, logo.height)
        else:
            top_logo = logo  # sized below, once the text column is known

    text_width = inner
    for kind, thing, w, h in sides.values():
        text_width -= w + margin
    text_width = max(FONT_W, text_width)

    if top_logo is not None:
        top_logo = logomod.fit(top_logo, min(want, text_width) if want else text_width,
                               min(want, avail_h) if want else avail_h)

    items, body = _build(tpl, parts, text_width, cfg, include_qr=not side_qr,
                         top_logo=top_logo)
    for kind, thing, w, h in sides.values():
        body = max(body, h)

    height = body + margin * 2
    height = max(height, int(_opt(tpl, "min_height")))
    if height > limit:
        height = limit
    # Raster protocols address memory in whole bytes down the page.
    if height % 8:
        height += 8 - (height % 8)

    label = Label(width, height)
    x0 = margin
    if "left" in sides:
        x0 = margin + sides["left"][2] + margin
    _draw(label.fb, items, text_width, x0, margin, gap)

    for position, (kind, thing, w, h) in sides.items():
        sx = margin if position == "left" else width - margin - w
        if kind == "qr":
            matrix, scale = thing
            _draw_qr(label.fb, matrix, sx + 4 * scale, margin + 4 * scale, scale)
        else:
            # Centred down the label, so a short logo sits beside the text block.
            sy = margin + max(0, (height - margin * 2 - h) // 2)
            label.fb.blit(thing.fb, sx, sy, 0)

    return label
