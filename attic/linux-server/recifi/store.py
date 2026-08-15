"""JSON-file template store. Small enough that a whole-file rewrite is fine."""

from __future__ import annotations

import json
import re
import threading
import uuid

from .config import data_dir
from .models import LabelTemplate, QRSpec, TemplateField

_LOCK = threading.RLock()

BUILTINS = [
    LabelTemplate(
        id="auto-kv",
        name="Auto key/value",
        description="Prints every key of the payload as a row. No setup needed.",
        title="{{title}}",
        auto_fields=True,
    ),
    LabelTemplate(
        id="qr-only",
        name="QR code",
        description="A QR of {{url}} (or of the raw string payload) with a caption.",
        qr=QRSpec(value="{{url}}", position="top", caption="{{caption}}"),
    ),
    LabelTemplate(
        id="asset-tag",
        name="Asset tag",
        description="Name + QR on the right, with owner and asset id rows.",
        title="{{name}}",
        fields=[
            TemplateField(label="ID", value="{{id}}", emphasis=True),
            TemplateField(label="Owner", value="{{owner}}"),
            TemplateField(label="Location", value="{{location}}"),
        ],
        qr=QRSpec(value="{{id}}", position="right", scale=3),
        footer="{{footer}}",
    ),
]


def _path():
    return data_dir() / "templates.json"


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return base or uuid.uuid4().hex[:8]


def _read_user() -> dict[str, LabelTemplate]:
    path = _path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, LabelTemplate] = {}
    for item in raw if isinstance(raw, list) else []:
        try:
            tpl = LabelTemplate(**item)
        except ValueError:
            continue
        if tpl.id:
            out[tpl.id] = tpl
    return out


def _write_user(items: dict[str, LabelTemplate]) -> None:
    payload = [t.model_dump() for t in items.values()]
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(_path())


def list_templates() -> list[LabelTemplate]:
    """Built-ins first, then user templates. A user id shadows a built-in."""
    with _LOCK:
        user = _read_user()
        merged = {t.id: t for t in BUILTINS}
        merged.update(user)
        return list(merged.values())


def get_template(ident: str) -> LabelTemplate | None:
    """Look up by id, then by exact name, then case-insensitively by name."""
    if not ident:
        return None
    items = list_templates()
    for tpl in items:
        if tpl.id == ident:
            return tpl
    for tpl in items:
        if tpl.name == ident:
            return tpl
    lowered = ident.lower()
    for tpl in items:
        if tpl.name.lower() == lowered:
            return tpl
    return None


def is_builtin(ident: str) -> bool:
    return any(t.id == ident for t in BUILTINS)


def save_template(tpl: LabelTemplate) -> LabelTemplate:
    """Create or overwrite. Saving over a built-in id shadows it locally."""
    with _LOCK:
        user = _read_user()
        if not tpl.id:
            candidate = _slug(tpl.name)
            existing = {t.id for t in list_templates()}
            tpl.id = candidate
            suffix = 2
            while tpl.id in existing:
                tpl.id = f"{candidate}-{suffix}"
                suffix += 1
        user[tpl.id] = tpl
        _write_user(user)
        return tpl


def delete_template(ident: str) -> bool:
    """Removes the user copy. A shadowed built-in reappears afterwards."""
    with _LOCK:
        user = _read_user()
        if ident not in user:
            return False
        del user[ident]
        _write_user(user)
        return True
