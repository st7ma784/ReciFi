"""Turn a template + payload into a 1-bit label bitmap.

The output is always exactly `width_dots` wide and a multiple of 8 dots tall,
which is what every raster protocol here wants to be handed.
"""

from __future__ import annotations

import re
from typing import Any

import qrcode
from PIL import Image, ImageDraw, ImageFont

from .models import LabelTemplate, PrinterConfig, TemplateField

_PLACEHOLDER = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")

_FONT_CANDIDATES = {
    False: [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
    True: [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
}

_font_cache: dict[tuple[int, bool], ImageFont.ImageFont] = {}


def get_font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    font = None
    for path in _FONT_CANDIDATES[bold]:
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size)
        except TypeError:  # Pillow < 10.1 has no sized default font
            font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# --------------------------------------------------------------------------
# payload -> text
# --------------------------------------------------------------------------


def normalise_payload(data: Any) -> dict[str, Any]:
    """Coerce whatever arrived on the wire into a flat mapping.

    A bare string lands under both "value" and "text"; a list of pairs or of
    {label, value} objects becomes real keys; nested objects are flattened with
    dotted keys so `{{addr.city}}` resolves.
    """
    if data is None:
        return {}
    if isinstance(data, str):
        return {"value": data, "text": data}
    if isinstance(data, list):
        out: dict[str, Any] = {}
        for index, item in enumerate(data):
            if isinstance(item, dict) and "label" in item:
                out[str(item["label"])] = item.get("value", "")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                out[str(item[0])] = item[1]
            else:
                out[f"item{index + 1}"] = item
        return out
    if isinstance(data, dict):
        flat: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                flat[str(key)] = value
                for sub, subval in value.items():
                    flat[f"{key}.{sub}"] = subval
            else:
                flat[str(key)] = value
        return flat
    return {"value": data, "text": data}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple)):
        return ", ".join(_stringify(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_stringify(v)}" for k, v in value.items())
    return str(value)


def substitute(text: str, data: dict[str, Any]) -> tuple[str, set[str]]:
    """Expand {{key}} against `data`. Returns the text and the keys consumed."""
    used: set[str] = set()
    if not text:
        return "", used
    lowered = {k.lower(): k for k in data}

    def repl(match: re.Match[str]) -> str:
        raw = match.group(1)
        key = lowered.get(raw.lower(), raw if raw in data else None)
        if key is None:
            return ""
        used.add(key)
        return _stringify(data[key])

    return _PLACEHOLDER.sub(repl, text).strip(), used


def _template_keys(tpl: LabelTemplate) -> set[str]:
    """Every placeholder name mentioned anywhere in the template."""
    blobs = [tpl.title, tpl.footer]
    for field in tpl.fields:
        blobs += [field.label, field.value]
    if tpl.qr:
        blobs += [tpl.qr.value, tpl.qr.caption]
    keys: set[str] = set()
    for blob in blobs:
        keys.update(m.group(1).strip().lower() for m in _PLACEHOLDER.finditer(blob or ""))
    return keys


def resolve(tpl: LabelTemplate, data: Any) -> dict[str, Any]:
    """Apply the payload to the template, producing plain rendered strings."""
    values = normalise_payload(data)
    consumed: set[str] = set()

    title, used = substitute(tpl.title, values)
    consumed |= used
    footer, used = substitute(tpl.footer, values)
    consumed |= used

    rows: list[tuple[str, str, bool]] = []
    for field in tpl.fields:
        label, used_l = substitute(field.label, values)
        value, used_v = substitute(field.value, values)
        consumed |= used_l | used_v
        # A row whose only content came from a missing placeholder is dropped.
        if not label and not value:
            continue
        rows.append((label, value, field.emphasis))

    qr_value = ""
    qr_caption = ""
    if tpl.qr:
        qr_value, used = substitute(tpl.qr.value, values)
        consumed |= used
        qr_caption, used = substitute(tpl.qr.caption, values)
        consumed |= used
        if not qr_value and isinstance(data, str):
            qr_value = data

    if tpl.auto_fields:
        skip = _template_keys(tpl) | {k.lower() for k in consumed}
        for key, value in values.items():
            if key.lower() in skip or "." in key:
                continue
            if isinstance(value, dict):
                continue
            rows.append((key, _stringify(value), False))

    return {
        "title": title,
        "rows": rows,
        "footer": footer,
        "qr_value": qr_value,
        "qr_caption": qr_caption,
    }


# --------------------------------------------------------------------------
# text -> pixels
# --------------------------------------------------------------------------


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    if not text:
        return 0, 0
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Greedy word wrap that also breaks words longer than the label."""
    lines: list[str] = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if _text_size(draw, trial, font)[0] <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)

    out: list[str] = []
    for line in lines:
        while line and _text_size(draw, line, font)[0] > max_width:
            cut = len(line)
            while cut > 1 and _text_size(draw, line[:cut], font)[0] > max_width:
                cut -= 1
            out.append(line[:cut])
            line = line[cut:]
        out.append(line)
    return out


def _make_qr(value: str, scale: int, max_width: int) -> Image.Image | None:
    if not value:
        return None
    qr = qrcode.QRCode(box_size=max(1, scale), border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(value)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    if img.width > max_width:
        # NEAREST keeps module edges crisp, which matters at 203dpi.
        modules = qr.modules_count + qr.border * 2
        box = max(1, max_width // modules)
        side = box * modules
        img = img.resize((side, side), Image.NEAREST)
    return img


def _draw_column(
    tpl: LabelTemplate,
    parts: dict[str, Any],
    width: int,
    include_qr: bool,
) -> Image.Image:
    """Render the text stack (and inline QR when it sits top/bottom)."""
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    title_font = get_font(tpl.title_size, bold=True)
    label_font = get_font(tpl.text_size, bold=True)
    value_font = get_font(tpl.text_size)
    small_font = get_font(max(8, int(tpl.text_size * 0.8)))

    blocks: list[tuple[str, Any]] = []

    qr_img = None
    if include_qr and parts["qr_value"]:
        qr_img = _make_qr(parts["qr_value"], tpl.qr.scale if tpl.qr else 4, width)

    if qr_img is not None and tpl.qr and tpl.qr.position == "top":
        blocks.append(("image", qr_img))
        if parts["qr_caption"]:
            blocks.append(("lines", (_wrap(probe, parts["qr_caption"], small_font, width), small_font, "center")))

    if parts["title"]:
        blocks.append(("lines", (_wrap(probe, parts["title"], title_font, width), title_font, tpl.align)))
        blocks.append(("rule", None))

    # One shared value column so rows line up, unless the longest label eats
    # more than half the label -- then every row falls back to "label: value".
    paired = [(l, v) for l, v, _ in parts["rows"] if l and v]
    label_col = max((_text_size(probe, f"{l}  ", label_font)[0] for l, _ in paired), default=0)
    if label_col > width * 0.55:
        label_col = 0

    for label, value, emphasis in parts["rows"]:
        font = get_font(int(tpl.text_size * 1.25), bold=True) if emphasis else value_font
        if label and value:
            if label_col:
                blocks.append(("row", (label, value, label_font, font, label_col)))
                continue
            text = f"{label}: {value}"
        else:
            text = label or value
        blocks.append(("lines", (_wrap(probe, text, font, width), font, tpl.align)))

    if qr_img is not None and tpl.qr and tpl.qr.position == "bottom":
        blocks.append(("image", qr_img))
        if parts["qr_caption"]:
            blocks.append(("lines", (_wrap(probe, parts["qr_caption"], small_font, width), small_font, "center")))

    if parts["footer"]:
        blocks.append(("rule", None))
        blocks.append(("lines", (_wrap(probe, parts["footer"], small_font, width), small_font, tpl.align)))

    # Measure.
    gap = tpl.line_gap
    height = 0
    for kind, payload in blocks:
        if kind == "image":
            height += payload.height + gap
        elif kind == "rule":
            height += gap * 2 + 2
        elif kind == "row":
            _, value, _, font, label_w = payload
            lines = _wrap(probe, value, font, max(8, width - label_w))
            height += sum(_line_height(font) for _ in lines) + gap
        else:
            lines, font, _ = payload
            height += sum(_line_height(font) for _ in lines) + gap
    height = max(height, 1)

    canvas = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(canvas)
    y = 0
    for kind, payload in blocks:
        if kind == "image":
            x = (width - payload.width) // 2
            canvas.paste(payload, (x, y))
            y += payload.height + gap
        elif kind == "rule":
            y += gap
            draw.line([(0, y), (width - 1, y)], fill=0, width=2)
            y += gap + 2
        elif kind == "row":
            label, value, lfont, vfont, label_w = payload
            lines = _wrap(probe, value, vfont, max(8, width - label_w))
            draw.text((0, y), label, font=lfont, fill=0)
            for line in lines:
                draw.text((label_w, y), line, font=vfont, fill=0)
                y += _line_height(vfont)
            y += gap
        else:
            lines, font, align = payload
            for line in lines:
                x = 0
                if align == "center":
                    x = max(0, (width - _text_size(draw, line, font)[0]) // 2)
                draw.text((x, y), line, font=font, fill=0)
                y += _line_height(font)
            y += gap

    return canvas.crop((0, 0, width, max(1, y)))


def _line_height(font) -> int:
    try:
        ascent, descent = font.getmetrics()
        return ascent + descent + 2
    except AttributeError:
        return 14


def render_label(tpl: LabelTemplate, data: Any, cfg: PrinterConfig) -> Image.Image:
    """Full pipeline: template + payload -> 1-bit image `cfg.width_dots` wide."""
    parts = resolve(tpl, data)
    margin = tpl.margin
    inner = max(32, cfg.width_dots - margin * 2)

    side_qr = None
    if tpl.qr and parts["qr_value"] and tpl.qr.position in ("left", "right"):
        side_qr = _make_qr(parts["qr_value"], tpl.qr.scale, inner // 2)

    if side_qr is not None:
        text_width = max(32, inner - side_qr.width - margin)
        text_img = _draw_column(tpl, parts, text_width, include_qr=False)
        body_height = max(text_img.height, side_qr.height)
        body = Image.new("L", (inner, body_height), 255)
        if tpl.qr.position == "left":
            body.paste(side_qr, (0, 0))
            body.paste(text_img, (side_qr.width + margin, 0))
        else:
            body.paste(text_img, (0, 0))
            body.paste(side_qr, (inner - side_qr.width, 0))
    else:
        body = _draw_column(tpl, parts, inner, include_qr=True)

    height = body.height + margin * 2
    height = max(height, tpl.min_height)
    # Raster protocols address memory in bytes, so pad to a whole 8 rows.
    if height % 8:
        height += 8 - (height % 8)

    label = Image.new("L", (cfg.width_dots, height), 255)
    label.paste(body, (margin, margin))
    # Threshold last so anti-aliased glyph edges become real dots.
    return label.point(lambda px: 0 if px < 160 else 255, mode="1")


def image_to_raster(img: Image.Image) -> tuple[bytes, int, int]:
    """Pack a 1-bit image MSB-first, 1 = burn. Returns (data, row_bytes, rows)."""
    if img.mode != "1":
        img = img.convert("1")
    width, height = img.size
    row_bytes = (width + 7) // 8
    # Pillow's "1" raw packing is already MSB-first with 0 = black, so invert.
    packed = bytearray(img.tobytes("raw", "1"))
    for i, byte in enumerate(packed):
        packed[i] = ~byte & 0xFF
    return bytes(packed), row_bytes, height
