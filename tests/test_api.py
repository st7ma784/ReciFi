"""End-to-end over the router, with the null transport standing in for a printer."""

import asyncio
import json
import os

from recifi import api, config, store
from recifi.printers import Manager
from recifi.printers import protocols
from recifi.render import render


def _fresh():
    for name in (store.PATH, config.PATH, "spool.bin"):
        try:
            os.remove(name)
        except OSError:
            pass
    cfg = config.load()
    cfg["transport"] = "null"
    cfg["address"] = "spool"
    manager = Manager(cfg)
    asyncio.run(manager.connect())
    return cfg, manager, api.build(cfg, manager)


def _call(router, method, path, body=None, query=None):
    from recifi.server import Request

    handler, params = router.match(method, path)
    assert handler is not None and handler != 405, "no route for %s %s" % (method, path)
    raw = b"" if body is None else json.dumps(body).encode()
    request = Request(method, path, query or {}, {}, raw)
    request.params = params
    return asyncio.run(handler(request))


def _json(response):
    return json.loads(response.body)


def test_status_reports_everything_the_ui_needs():
    _, _, router = _fresh()
    body = _json(_call(router, "GET", "/api/status"))
    assert body["state"] == "ready"
    assert {t["name"] for t in body["transports"]} == {"null", "uart", "ble"}
    assert {p["name"] for p in body["protocols"]} == {"tspl", "escpos", "niimbot", "raw"}
    assert "mem_free" in body and "wifi" in body
    assert body["config"]["width_dots"] == config.DEFAULTS["width_dots"]


def test_healthz_is_200_when_ready():
    _, _, router = _fresh()
    assert _call(router, "GET", "/healthz").status == 200


def test_preview_returns_a_bmp():
    _, _, router = _fresh()
    response = _call(router, "POST", "/api/preview", {"template": "auto-kv", "data": {"a": "1"}})
    assert response.content_type == "image/bmp"
    blob = b"".join(response.stream)
    assert blob[:2] == b"BM"
    # Header says 1bpp and the pixel data is the size the header claims.
    assert blob[28] == 1
    assert len(blob) == int.from_bytes(blob[2:6], "little")


def test_print_writes_to_the_spool():
    _, manager, router = _fresh()
    body = _json(_call(router, "POST", "/api/print", {"data": {"Batch": "B-77"}, "copies": 2}))
    assert body["ok"] and body["copies"] == 2
    assert manager.jobs == 2
    assert os.stat("spool.bin")[6] > 0


def test_bare_json_against_a_named_template():
    _, _, router = _fresh()
    body = _json(_call(router, "POST", "/api/print/asset-tag", {"id": "A-1042", "owner": "lab 3"}))
    assert body["ok"] and body["height"] > 0


def test_unknown_template_is_404():
    _, _, router = _fresh()
    assert _call(router, "POST", "/api/print", {"template": "nope"}).status == 404


def test_inline_template_needs_no_saving():
    _, _, router = _fresh()
    body = _json(_call(router, "POST", "/api/print",
                       {"inline": {"name": "t", "fields": [{"value": "hi"}]}, "data": {}}))
    assert body["ok"]


def test_template_crud_round_trip():
    _, _, router = _fresh()
    created = _json(_call(router, "POST", "/api/templates",
                          {"name": "Shelf tag", "fields": [{"label": "Bin", "value": "{{bin}}"}]}))
    assert created["id"] == "shelf-tag"

    created["title"] = "{{title}}"
    _call(router, "PUT", "/api/templates/shelf-tag", created)
    assert _json(_call(router, "GET", "/api/templates/shelf-tag"))["title"] == "{{title}}"

    assert _json(_call(router, "POST", "/api/print/shelf-tag", {"bin": "B12"}))["ok"]
    assert _call(router, "DELETE", "/api/templates/shelf-tag").status == 200
    assert _call(router, "GET", "/api/templates/shelf-tag").status == 404


def test_deleting_a_shadowed_builtin_restores_it():
    _, _, router = _fresh()
    original = _json(_call(router, "GET", "/api/templates/qr-only"))
    edited = dict(original)
    edited["title"] = "mine"
    _call(router, "PUT", "/api/templates/qr-only", edited)
    assert _json(_call(router, "GET", "/api/templates/qr-only"))["title"] == "mine"

    body = _json(_call(router, "DELETE", "/api/templates/qr-only"))
    assert body["builtin_restored"] is True
    assert _json(_call(router, "GET", "/api/templates/qr-only"))["title"] == original["title"]


def test_config_patch_persists():
    cfg, _, router = _fresh()
    _call(router, "POST", "/api/config", {"width_dots": 576, "density": 7})
    assert config.load()["width_dots"] == 576
    assert cfg["width_dots"] == 576


def test_config_route_will_not_set_wifi():
    _, _, router = _fresh()
    _call(router, "POST", "/api/config", {"wifi": {"ssid": "evil"}})
    assert config.load()["wifi"]["ssid"] != "evil"


def test_status_never_leaks_the_wifi_password():
    cfg, _, router = _fresh()
    config.patch(cfg, {"wifi": {"ssid": "MANDER84", "password": "hunter2"}})
    assert "hunter2" not in json.dumps(_json(_call(router, "GET", "/api/status")))


def test_raw_console_sends_and_validates():
    _, _, router = _fresh()
    assert _json(_call(router, "POST", "/api/debug/raw", {"hex": "1b 40"}))["sent"] == 2
    assert _call(router, "POST", "/api/debug/raw", {"hex": "zz"}).status == 400
    assert _call(router, "POST", "/api/debug/raw", {"hex": "1b4"}).status == 400
    assert _call(router, "POST", "/api/debug/raw", {}).status == 400


def test_encode_dump_describes_the_job():
    _, _, router = _fresh()
    body = _json(_call(router, "POST", "/api/debug/encode",
                       {"template": "qr-only", "data": "hello"}))
    assert body["protocol"] == "tspl" and body["bytes"] > 0 and body["head_hex"]


def test_discover_finds_the_virtual_printer():
    _, _, router = _fresh()
    devices = _json(_call(router, "GET", "/api/discover", None, {"transport": "null"}))["devices"]
    assert any(d["address"] == "spool" for d in devices)


def test_print_while_disconnected_is_503():
    cfg, manager, router = _fresh()
    cfg["auto_connect"] = False
    asyncio.run(manager.disconnect())
    assert _call(router, "POST", "/api/print", {"data": "x"}).status == 503


def test_static_route_refuses_path_traversal():
    _, _, router = _fresh()
    assert _call(router, "GET", "/static/..%2fconfig.json").status == 404


# --------------------------------------------------------------- protocols

CFG = {"width_dots": 384, "density": 3, "feed_dots": 48, "label_type": 1, "max_height": 800}


def test_escpos_emits_raster_bands_and_a_feed():
    label = render({"title": "x"}, {}, CFG)
    blocks = list(protocols.encode("escpos", label, CFG))
    joined = b"".join(bytes(b) for b in blocks)
    assert joined.startswith(b"\x1b\x40")
    assert b"\x1dv0" in joined
    assert joined.endswith(bytes([0x1B, 0x4A, 48]))


def test_escpos_band_headers_describe_the_payload():
    label = render({"fields": [{"value": "word " * 40}]}, {}, CFG)
    blocks = [bytes(b) for b in protocols.encode("escpos", label, CFG)]
    rows_declared = 0
    for i, block in enumerate(blocks):
        if block[:3] == b"\x1dv0":
            width = block[4] | (block[5] << 8)
            rows = block[6] | (block[7] << 8)
            assert width == label.stride
            assert len(blocks[i + 1]) == width * rows
            rows_declared += rows
    assert rows_declared == label.height


TSPL_CFG = {"width_dots": 320, "label_width_mm": 40, "label_height_mm": 30,
            "density": 8, "speed": 0, "direction": 0, "post_print": "tear",
            "gap_mm": None, "max_height": 800}


def test_tspl_has_the_sequence_the_vendor_driver_sends():
    label = render({"title": "x"}, {}, TSPL_CFG)
    joined = b"".join(bytes(b) for b in protocols.encode("tspl", label, TSPL_CFG))
    head, _, _ = joined.partition(b"BITMAP")
    assert head == (b"SET PEEL OFF\r\nSET TEAR ON\r\n"
                    b"SIZE 40 mm,30 mm\r\nDENSITY 8\r\nDIRECTION 0,0\r\nCLS\r\n")
    assert joined.endswith(b"\r\nPRINT 1,1\r\n")


def test_tspl_bitmap_header_matches_the_raster():
    label = render({"fields": [{"value": "hello"}]}, {}, TSPL_CFG)
    joined = b"".join(bytes(b) for b in protocols.encode("tspl", label, TSPL_CFG))
    marker = b"BITMAP 0,0,%d,%d,0," % (label.stride, label.height)
    start = joined.find(marker)
    assert start >= 0
    payload = joined[start + len(marker):-len(b"\r\nPRINT 1,1\r\n")]
    assert len(payload) == label.stride * label.height


def test_tspl_inverts_the_bitmap_because_1_means_white():
    label = render({"min_height": 64}, {}, TSPL_CFG)  # blank -> all zero bits
    joined = b"".join(bytes(b) for b in protocols.encode("tspl", label, TSPL_CFG))
    marker = b"BITMAP 0,0,%d,%d,0," % (label.stride, label.height)
    payload = joined[joined.find(marker) + len(marker):-len(b"\r\nPRINT 1,1\r\n")]
    # A blank label must reach the printer as all-ones (all white), not zeros.
    assert payload == b"\xff" * (label.stride * label.height)


def test_tspl_omits_zero_speed_and_density():
    cfg = dict(TSPL_CFG)
    cfg["density"] = 0
    cfg["speed"] = 0
    label = render({"title": "x"}, {}, cfg)
    joined = b"".join(bytes(b) for b in protocols.encode("tspl", label, cfg))
    assert b"DENSITY" not in joined
    assert b"SPEED" not in joined


def test_tspl_gap_and_peel_are_configurable():
    cfg = dict(TSPL_CFG)
    cfg["gap_mm"] = 2
    cfg["post_print"] = "peel"
    cfg["speed"] = 4
    label = render({"title": "x"}, {}, cfg)
    joined = b"".join(bytes(b) for b in protocols.encode("tspl", label, cfg))
    assert b"GAP 2 mm,0 mm\r\n" in joined
    assert joined.startswith(b"SET TEAR OFF\r\nSET PEEL ON\r\n")
    assert b"SPEED 4\r\n" in joined


def test_niimbot_packets_are_well_formed():
    label = render({"title": "x"}, {}, CFG)
    for block in protocols.encode("niimbot", label, CFG):
        block = bytes(block)
        assert block[:2] == b"\x55\x55" and block[-2:] == b"\xaa\xaa"
        kind, length = block[2], block[3]
        payload = block[4:-3]
        assert len(payload) == length
        checksum = kind ^ length
        for byte in payload:
            checksum ^= byte
        assert checksum == block[-3]


def test_raw_protocol_is_exactly_the_bitmap():
    label = render({"title": "x"}, {}, CFG)
    joined = b"".join(bytes(b) for b in protocols.encode("raw", label, CFG))
    assert joined == bytes(label.buf)


def test_unknown_protocol_raises():
    label = render({"title": "x"}, {}, CFG)
    try:
        list(protocols.encode("nope", label, CFG))
    except ValueError:
        return
    raise AssertionError("expected ValueError")
