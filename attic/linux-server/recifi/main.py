"""HTTP API + web UI."""

from __future__ import annotations

import binascii
import io
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from . import __version__, store
from .models import ConnectRequest, LabelTemplate, PrintRequest, RawRequest
from .printers.base import TransportError
from .printers.manager import manager
from .render import render_label

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(title="ReciFi", version=__version__, lifespan=lifespan)


@app.exception_handler(TransportError)
async def _transport_error(request: Request, exc: TransportError):
    # 503: the request was fine, the printer wasn't.
    return JSONResponse(status_code=503, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")


# ---------------------------------------------------------------------------
# status / connection
# ---------------------------------------------------------------------------


@app.get("/api/status")
async def get_status():
    return manager.status()


@app.get("/healthz")
async def healthz():
    """Plain check for uptime monitors: 200 only when the printer is usable."""
    ok = manager.state == "ready"
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"ok": ok, "state": manager.state, "detail": manager.detail},
    )


@app.post("/api/config")
async def set_config(changes: dict[str, Any] = Body(...)):
    return (await manager.update_config(changes)).model_dump()


@app.get("/api/discover")
async def discover(transport: str | None = Query(default=None)):
    devices = await manager.discover(transport)
    return {"devices": [d.__dict__ for d in devices]}


@app.post("/api/connect")
async def connect(req: ConnectRequest):
    if req.protocol:
        await manager.update_config({"protocol": req.protocol})
    await manager.connect(req.transport, req.address)
    return manager.status()


@app.post("/api/disconnect")
async def disconnect():
    await manager.disconnect()
    return manager.status()


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------


@app.get("/api/templates")
async def list_templates():
    return {
        "templates": [
            {**t.model_dump(), "builtin": store.is_builtin(t.id)} for t in store.list_templates()
        ]
    }


@app.get("/api/templates/{template_id}")
async def get_template(template_id: str):
    tpl = store.get_template(template_id)
    if tpl is None:
        raise HTTPException(404, f"no template {template_id!r}")
    return tpl.model_dump()


@app.post("/api/templates")
async def create_template(tpl: LabelTemplate):
    return store.save_template(tpl).model_dump()


@app.put("/api/templates/{template_id}")
async def update_template(template_id: str, tpl: LabelTemplate):
    tpl.id = template_id
    return store.save_template(tpl).model_dump()


@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: str):
    if not store.delete_template(template_id):
        raise HTTPException(404, f"no saved template {template_id!r}")
    return {"deleted": template_id, "builtin_restored": store.is_builtin(template_id)}


# ---------------------------------------------------------------------------
# printing
# ---------------------------------------------------------------------------


def _resolve_template(req: PrintRequest) -> LabelTemplate:
    if req.inline is not None:
        return req.inline
    if req.template:
        tpl = store.get_template(req.template)
        if tpl is None:
            raise HTTPException(404, f"no template {req.template!r}")
        return tpl
    # No template named: dump whatever arrived as key/value rows.
    tpl = store.get_template("auto-kv")
    return tpl or LabelTemplate(id="auto-kv", name="Auto", auto_fields=True)


def _build(req: PrintRequest) -> Image.Image:
    tpl = _resolve_template(req)
    try:
        return render_label(tpl, req.data, manager.cfg)
    except Exception as exc:
        raise HTTPException(400, f"render failed: {exc}") from exc


@app.post("/api/preview")
async def preview(req: PrintRequest, scale: int = Query(default=1, ge=1, le=4)):
    image = _build(req)
    if scale > 1:
        image = image.resize((image.width * scale, image.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    image.convert("L").save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store", "X-Label-Height": str(image.height)},
    )


@app.post("/api/print")
async def print_label(req: PrintRequest):
    image = _build(req)
    result = await manager.print_image(image, req.copies)
    return {"ok": True, **result, "at": time.time()}


@app.post("/api/print/{template_id}")
async def print_with_template(
    template_id: str,
    data: Any = Body(default=None),
    copies: int = Query(default=1, ge=1, le=20),
):
    """Post a bare JSON document (or string) straight at a named template.

        curl -X POST http://recifi.local/api/print/asset-tag \\
             -H 'content-type: application/json' \\
             -d '{"id":"A-1042","owner":"lab 3"}'
    """
    req = PrintRequest(template=template_id, data=data, copies=copies)
    image = _build(req)
    result = await manager.print_image(image, copies)
    return {"ok": True, **result, "at": time.time()}


@app.post("/api/print/text/plain")
async def print_plain(request: Request, copies: int = Query(default=1, ge=1, le=20)):
    """text/plain in, label out -- the simplest thing that can work."""
    body = (await request.body()).decode("utf-8", errors="replace")
    image = _build(PrintRequest(data=body, copies=copies))
    result = await manager.print_image(image, copies)
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# protocol spelunking
# ---------------------------------------------------------------------------


@app.post("/api/debug/raw")
async def debug_raw(req: RawRequest):
    """Push arbitrary bytes at the printer to work out what it speaks."""
    payload = b""
    if req.hex:
        cleaned = "".join(req.hex.split()).replace("0x", "").replace(",", "")
        try:
            payload += binascii.unhexlify(cleaned)
        except binascii.Error as exc:
            raise HTTPException(400, f"bad hex: {exc}") from exc
    if req.text:
        payload += req.text.encode("utf-8")
    if not payload:
        raise HTTPException(400, "nothing to send")
    reply = await manager.send_raw(payload, req.read_reply, req.read_timeout)
    return {
        "sent": len(payload),
        "reply_hex": reply.hex(" ") if reply else "",
        "reply_ascii": reply.decode("ascii", errors="replace") if reply else "",
    }


@app.post("/api/debug/encode")
async def debug_encode(req: PrintRequest):
    """Show the exact bytes a print would produce, without printing."""
    from .printers.registry import make_protocol

    image = _build(req)
    blocks = make_protocol(manager.cfg.protocol, manager.cfg).encode(image)
    joined = b"".join(blocks)
    return {
        "protocol": manager.cfg.protocol,
        "blocks": len(blocks),
        "bytes": len(joined),
        "height": image.height,
        "width": image.width,
        "head_hex": joined[:256].hex(" "),
    }
