"""Template storage: three built-ins plus a JSON file of user templates.

A user template with the same id as a built-in shadows it; deleting that copy
brings the original back, so the built-ins can be customised without being
lost.
"""

import json
import os

PATH = "templates.json"

BUILTINS = [
    {
        "id": "auto-kv",
        "name": "Auto key/value",
        "description": "Prints every key of the payload as a row. No setup needed.",
        "title": "{{title}}",
        "auto_fields": True,
        "fields": [],
    },
    {
        "id": "qr-only",
        "name": "QR code",
        "description": "A QR of {{url}} (or of a bare string payload) with a caption.",
        "fields": [],
        "qr": {"value": "{{url}}", "position": "top", "scale": 6, "caption": "{{caption}}"},
    },
    {
        "id": "asset-tag",
        "name": "Asset tag",
        "description": "Name and QR, with id, owner and location rows.",
        "title": "{{name}}",
        "fields": [
            {"label": "ID", "value": "{{id}}", "emphasis": True},
            {"label": "Owner", "value": "{{owner}}"},
            {"label": "Location", "value": "{{location}}"},
        ],
        "qr": {"value": "{{id}}", "position": "right", "scale": 5},
        "footer": "{{footer}}",
    },
]

_BUILTIN_IDS = set(t["id"] for t in BUILTINS)


def _read_user():
    try:
        with open(PATH) as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    out = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                out[item["id"]] = item
    return out


def _write_user(items):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(list(items.values()), handle)
    try:
        os.remove(PATH)
    except OSError:
        pass
    os.rename(tmp, PATH)


def _slug(name):
    out = []
    for ch in name.lower():
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "template"


def _clamp(value, low, high, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return low if value < low else (high if value > high else value)


def normalise(tpl):
    """Coerce a template from the wire into something render.py can trust."""
    out = {
        "id": str(tpl.get("id", "") or ""),
        "name": str(tpl.get("name", "") or "Untitled"),
        "description": str(tpl.get("description", "") or ""),
        "title": str(tpl.get("title", "") or ""),
        "footer": str(tpl.get("footer", "") or ""),
        "auto_fields": bool(tpl.get("auto_fields")),
        "title_scale": _clamp(tpl.get("title_scale"), 1, 4, 3),
        "text_scale": _clamp(tpl.get("text_scale"), 1, 4, 2),
        "margin": _clamp(tpl.get("margin"), 0, 64, 8),
        "line_gap": _clamp(tpl.get("line_gap"), 0, 32, 4),
        "align": "center" if tpl.get("align") == "center" else "left",
        "min_height": _clamp(tpl.get("min_height"), 0, 2000, 0),
    }

    fields = []
    for field in tpl.get("fields") or ():
        if not isinstance(field, dict):
            continue
        label = str(field.get("label", "") or "")
        value = str(field.get("value", "") or "")
        if not label and not value:
            continue
        fields.append({"label": label, "value": value, "emphasis": bool(field.get("emphasis"))})
    out["fields"] = fields

    qr = tpl.get("qr")
    if isinstance(qr, dict) and (qr.get("value") or qr.get("caption")):
        position = qr.get("position", "bottom")
        out["qr"] = {
            "value": str(qr.get("value", "") or ""),
            "caption": str(qr.get("caption", "") or ""),
            "scale": _clamp(qr.get("scale"), 1, 12, 3),
            "position": position if position in ("top", "bottom", "left", "right") else "bottom",
        }
    else:
        out["qr"] = None
    return out


def load_all():
    """Built-ins first, then user templates; a user id shadows a built-in."""
    merged = {}
    for tpl in BUILTINS:
        merged[tpl["id"]] = normalise(tpl)
    for ident, tpl in _read_user().items():
        merged[ident] = normalise(tpl)
    return merged


def listing():
    out = []
    for tpl in load_all().values():
        item = dict(tpl)
        item["builtin"] = tpl["id"] in _BUILTIN_IDS
        out.append(item)
    out.sort(key=lambda t: t["name"].lower())
    return out


def get(ident):
    """By id, then by name (exact, then case-insensitive)."""
    if not ident:
        return None
    items = load_all()
    if ident in items:
        return items[ident]
    lowered = ident.lower()
    for tpl in items.values():
        if tpl["name"] == ident:
            return tpl
    for tpl in items.values():
        if tpl["name"].lower() == lowered:
            return tpl
    return None


def is_builtin(ident):
    return ident in _BUILTIN_IDS


def save(tpl):
    tpl = normalise(tpl)
    user = _read_user()
    if not tpl["id"]:
        existing = load_all()
        base = _slug(tpl["name"])
        ident = base
        suffix = 2
        while ident in existing:
            ident = "%s-%d" % (base, suffix)
            suffix += 1
        tpl["id"] = ident
    user[tpl["id"]] = tpl
    _write_user(user)
    return tpl


def delete(ident):
    user = _read_user()
    if ident not in user:
        return False
    del user[ident]
    _write_user(user)
    return True
