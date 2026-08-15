"""HTTP routes. Mirrors the API of the original Linux build."""

import gc
import time

from . import config as configmod
from . import render, store, wifi
from .printers.transports import TransportError
from .server import Response, Router, error, file_response, json_response

WWW = "www"


# ----------------------------------------------------------------- BMP

def bmp_stream(label):
    """1-bit BMP of a label. BMP because it needs no compression -- the pixel
    data is our framebuffer as-is, where PNG would mean zlib and CRC32."""
    width, height = label.width, label.height
    row = (width + 7) // 8
    padded = (row + 3) & ~3  # BMP rows are 4-byte aligned
    pixels = padded * height
    offset = 14 + 40 + 8
    size = offset + pixels

    def le32(v):
        return bytes([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF])

    def le16(v):
        return bytes([v & 0xFF, (v >> 8) & 0xFF])

    header = (
        b"BM" + le32(size) + le32(0) + le32(offset)
        + le32(40) + le32(width)
        # Negative height = top-down, so rows go out in the order we hold them.
        + le32((~height + 1) & 0xFFFFFFFF)
        + le16(1) + le16(1) + le32(0) + le32(pixels)
        + le32(2835) + le32(2835) + le32(2) + le32(2)
        # Palette: index 0 = white, index 1 = black, matching 1 = burn.
        + b"\xff\xff\xff\x00" + b"\x00\x00\x00\x00"
    )

    def chunks():
        yield header
        pad = b"\x00" * (padded - row)
        buf = label.buf
        for y in range(height):
            yield buf[y * row : (y + 1) * row]
            if pad:
                yield pad

    return Response(status=200, content_type="image/bmp", stream=chunks())


# ----------------------------------------------------------------- routes


def build(cfg, manager):
    router = Router()

    def template_for(body):
        if not isinstance(body, dict):
            return store.get("auto-kv"), None
        inline = body.get("inline")
        if isinstance(inline, dict):
            return store.normalise(inline), None
        name = body.get("template")
        if name:
            tpl = store.get(name)
            if tpl is None:
                return None, error(404, "no template %r" % name)
            return tpl, None
        return store.get("auto-kv"), None

    def build_label(body):
        tpl, err = template_for(body)
        if err:
            return None, err
        data = body.get("data") if isinstance(body, dict) else None
        try:
            gc.collect()
            return render.render(tpl, data, cfg), None
        except MemoryError:
            return None, error(503, "out of memory rendering this label -- "
                                    "reduce max_height or the payload size")
        except Exception as exc:
            return None, error(400, "render failed: %s" % exc)

    # -- UI ------------------------------------------------------------

    @router.get("/")
    async def index(request):
        return file_response(WWW + "/index.html")

    @router.get("/static/{name}")
    async def static(request):
        name = request.params["name"]
        # No path traversal: only a bare filename from the www directory.
        if "/" in name or ".." in name:
            return error(404, "not found")
        return file_response(WWW + "/" + name)

    # -- status --------------------------------------------------------

    @router.get("/api/status")
    async def status(request):
        gc.collect()
        body = manager.status()
        body["config"] = configmod.redacted(cfg)
        body["wifi"] = wifi.status()
        body["mem_free"] = gc.mem_free()
        body["uptime"] = time.time()
        return json_response(body)

    @router.get("/healthz")
    async def healthz(request):
        ok = manager.state == "ready"
        return json_response(
            {"ok": ok, "state": manager.state, "detail": manager.detail},
            200 if ok else 503,
        )

    @router.post("/api/config")
    async def set_config(request):
        changes = request.json()
        if not isinstance(changes, dict):
            return error(400, "expected a JSON object")
        changes.pop("wifi", None)  # WiFi has its own route, so it can reconnect
        configmod.patch(cfg, changes)
        return json_response(configmod.redacted(cfg))

    # -- wifi ----------------------------------------------------------

    @router.get("/api/wifi/scan")
    async def wifi_scan(request):
        return json_response({"networks": await wifi.scan()})

    @router.post("/api/wifi")
    async def wifi_set(request):
        body = request.json()
        if not isinstance(body, dict) or not body.get("ssid"):
            return error(400, "expected {ssid, password}")
        section = dict(cfg.get("wifi", {}))
        section["ssid"] = body["ssid"]
        # An unchanged password comes back from the UI redacted; keep the real one.
        if body.get("password") and not body["password"].startswith("***"):
            section["password"] = body["password"]
        if body.get("hostname"):
            section["hostname"] = body["hostname"]
        configmod.patch(cfg, {"wifi": section})
        ok = await wifi.connect(cfg)
        return json_response({"ok": ok, "wifi": wifi.status()}, 200 if ok else 503)

    # -- connection ----------------------------------------------------

    @router.get("/api/discover")
    async def discover(request):
        which = request.query.get("transport") or None
        return json_response({"devices": await manager.discover(which)})

    @router.post("/api/connect")
    async def connect(request):
        body = request.json() or {}
        if body.get("protocol"):
            configmod.patch(cfg, {"protocol": body["protocol"]})
        try:
            await manager.connect(body.get("transport"), body.get("address"))
        except TransportError as exc:
            configmod.save(cfg)
            return error(503, str(exc))
        configmod.save(cfg)
        return json_response(manager.status())

    @router.post("/api/disconnect")
    async def disconnect(request):
        return json_response(await manager.disconnect())

    # -- templates -----------------------------------------------------

    @router.get("/api/templates")
    async def templates(request):
        return json_response({"templates": store.listing()})

    @router.post("/api/templates")
    async def create_template(request):
        body = request.json()
        if not isinstance(body, dict):
            return error(400, "expected a template object")
        body.pop("id", None)
        return json_response(store.save(body), 201)

    @router.get("/api/templates/{id}")
    async def get_template(request):
        tpl = store.get(request.params["id"])
        return json_response(tpl) if tpl else error(404, "no such template")

    @router.put("/api/templates/{id}")
    async def put_template(request):
        body = request.json()
        if not isinstance(body, dict):
            return error(400, "expected a template object")
        body["id"] = request.params["id"]
        return json_response(store.save(body))

    @router.delete("/api/templates/{id}")
    async def delete_template(request):
        ident = request.params["id"]
        if not store.delete(ident):
            return error(404, "no saved template %r" % ident)
        return json_response({"deleted": ident, "builtin_restored": store.is_builtin(ident)})

    # -- printing ------------------------------------------------------

    @router.post("/api/preview")
    async def preview(request):
        label, err = build_label(request.json() or {})
        return err or bmp_stream(label)

    @router.post("/api/print")
    async def print_label(request):
        body = request.json() or {}
        label, err = build_label(body)
        if err:
            return err
        copies = min(20, max(1, int(body.get("copies", 1) or 1)))
        try:
            result = await manager.print_label(label, copies)
        except TransportError as exc:
            return error(503, str(exc))
        result["ok"] = True
        result["at"] = time.time()
        return json_response(result)

    @router.post("/api/print/{id}")
    async def print_with_template(request):
        """Post a bare JSON document straight at a named template."""
        label, err = build_label({"template": request.params["id"], "data": request.json()})
        if err:
            return err
        copies = min(20, max(1, int(request.query.get("copies", 1) or 1)))
        try:
            result = await manager.print_label(label, copies)
        except TransportError as exc:
            return error(503, str(exc))
        result["ok"] = True
        result["at"] = time.time()
        return json_response(result)

    # -- protocol spelunking -------------------------------------------

    @router.post("/api/debug/raw")
    async def debug_raw(request):
        body = request.json() or {}
        payload = b""
        text = (body.get("hex") or "").replace("0x", "").replace(",", "")
        cleaned = "".join(text.split())
        if cleaned:
            if len(cleaned) % 2:
                return error(400, "hex needs an even number of digits")
            try:
                payload += bytes(int(cleaned[i:i + 2], 16) for i in range(0, len(cleaned), 2))
            except ValueError:
                return error(400, "bad hex")
        if body.get("text"):
            payload += body["text"].encode("utf-8")
        if not payload:
            return error(400, "nothing to send")
        try:
            reply = await manager.send_raw(payload, bool(body.get("read_reply")))
        except TransportError as exc:
            return error(503, str(exc))
        return json_response({
            "sent": len(payload),
            "reply_hex": " ".join("%02x" % b for b in reply) if reply else "",
            "reply_ascii": reply.decode("ascii", "replace") if reply else "",
        })

    @router.post("/api/debug/calibrate-media")
    async def calibrate_media(request):
        """Run the printer's own media calibration (TSPL GAPDETECT).

        The printer feeds a couple of labels while it measures where the gaps
        are. Worth doing whenever the media changes -- if its stored
        calibration is wrong, prints come out offset or clipped no matter what
        SIZE says.
        """
        head = "SIZE %s mm,%s mm\r\n" % (
            cfg.get("label_width_mm", 50), cfg.get("label_height_mm", 30))
        gap = cfg.get("gap_mm", None)
        if gap is not None:
            head += "GAP %s mm,0 mm\r\n" % gap
        head += "GAPDETECT\r\n"
        try:
            await manager.send_raw(head.encode())
        except TransportError as exc:
            return error(503, str(exc))
        return json_response({"ok": True, "sent": head})

    @router.get("/api/debug/calibrate")
    async def calibrate(request):
        """Print (or preview) a geometry test pattern.

        ?preview=1 returns it as a BMP instead of printing.
        """
        label = render.calibration(cfg)
        if request.query.get("preview"):
            return bmp_stream(label)
        try:
            result = await manager.print_label(label, 1)
        except TransportError as exc:
            return error(503, str(exc))
        result["ok"] = True
        return json_response(result)

    @router.post("/api/debug/encode")
    async def debug_encode(request):
        from .printers import protocols

        label, err = build_label(request.json() or {})
        if err:
            return err
        blocks = 0
        total = 0
        head = []
        for block in protocols.encode(cfg["protocol"], label, cfg):
            blocks += 1
            total += len(block)
            if len(head) < 64:
                head.extend(block[: 64 - len(head)])
        return json_response({
            "protocol": cfg["protocol"],
            "blocks": blocks,
            "bytes": total,
            "width": label.width,
            "height": label.height,
            "head_hex": " ".join("%02x" % b for b in head),
        })

    return router
