"""Smoke tests: run the whole stack against the null (spool-to-disk) transport.

    pip install pytest httpx && pytest
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Point the store at a scratch directory before anything imports config.
os.environ.setdefault("RECIFI_DATA", tempfile.mkdtemp(prefix="recifi-test-"))

from fastapi.testclient import TestClient  # noqa: E402

from recifi import store  # noqa: E402
from recifi.config import data_dir  # noqa: E402
from recifi.main import app  # noqa: E402
from recifi.models import LabelTemplate, PrinterConfig, QRSpec, TemplateField  # noqa: E402
from recifi.printers.protocols import ESCPOSProtocol, NiimbotProtocol  # noqa: E402
from recifi.render import image_to_raster, normalise_payload, render_label, resolve  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/api/config", json={"transport": "null", "address": "spool", "auto_connect": True})
        c.post("/api/connect", json={"transport": "null", "address": "spool"})
        yield c


CFG = PrinterConfig(width_dots=384)


# -- rendering --------------------------------------------------------------


def test_payload_shapes_all_normalise():
    assert normalise_payload({"a": 1})["a"] == 1
    assert normalise_payload("hi")["value"] == "hi"
    assert normalise_payload([{"label": "k", "value": "v"}])["k"] == "v"
    assert normalise_payload([["k", "v"]])["k"] == "v"
    # Nested objects are reachable by dotted key.
    assert normalise_payload({"addr": {"city": "Lancaster"}})["addr.city"] == "Lancaster"


def test_missing_placeholders_drop_their_row():
    tpl = LabelTemplate(
        fields=[TemplateField(label="Owner", value="{{owner}}"), TemplateField(value="{{nope}}")]
    )
    parts = resolve(tpl, {"owner": "sam"})
    assert parts["rows"] == [("Owner", "sam", False)]


def test_auto_fields_appends_only_unconsumed_keys():
    tpl = LabelTemplate(title="{{title}}", auto_fields=True)
    parts = resolve(tpl, {"title": "T", "sku": "X-1", "qty": 3})
    labels = {label for label, _, _ in parts["rows"]}
    assert labels == {"sku", "qty"}
    assert parts["title"] == "T"


def test_render_is_head_width_and_byte_aligned():
    tpl = LabelTemplate(title="Hello", fields=[TemplateField(label="SKU", value="ABC-123")])
    img = render_label(tpl, {}, CFG)
    assert img.mode == "1"
    assert img.width == 384
    assert img.height % 8 == 0
    # Something was actually drawn.
    assert img.convert("L").getextrema()[0] == 0


def test_long_text_wraps_instead_of_overflowing():
    tpl = LabelTemplate(fields=[TemplateField(value="word " * 200)])
    short = render_label(LabelTemplate(fields=[TemplateField(value="word")]), {}, CFG)
    assert render_label(tpl, {}, CFG).height > short.height


def test_unbroken_string_longer_than_the_label_is_hard_broken():
    tpl = LabelTemplate(fields=[TemplateField(value="X" * 400)])
    assert render_label(tpl, {}, CFG).width == 384


def test_qr_renders_in_every_position():
    heights = {}
    for position in ("top", "bottom", "left", "right"):
        tpl = LabelTemplate(
            title="Asset",
            fields=[TemplateField(label="ID", value="{{id}}")],
            qr=QRSpec(value="{{id}}", position=position),
        )
        img = render_label(tpl, {"id": "A-1042"}, CFG)
        assert img.convert("L").getextrema()[0] == 0
        heights[position] = img.height
    # A side QR shares vertical space with the text; a stacked one adds to it.
    assert heights["right"] < heights["top"]


def test_bare_string_payload_becomes_the_qr_value():
    tpl = LabelTemplate(qr=QRSpec(value="{{url}}"))
    assert resolve(tpl, "https://example.com")["qr_value"] == "https://example.com"


def test_raster_packing_is_msb_first_and_burn_is_one():
    tpl = LabelTemplate(min_height=64)
    img = render_label(tpl, {}, CFG)  # blank label
    data, row_bytes, rows = image_to_raster(img)
    assert row_bytes == 48 and rows == img.height
    assert len(data) == row_bytes * rows
    assert not any(data), "blank label must encode as all-zero (no burn)"


# -- protocols --------------------------------------------------------------


def test_escpos_emits_gs_v_0_bands():
    img = render_label(LabelTemplate(title="x"), {}, CFG)
    blocks = ESCPOSProtocol(CFG).encode(img)
    joined = b"".join(blocks)
    assert joined.startswith(b"\x1b\x40")
    assert b"\x1dv0" in joined
    assert joined.rstrip(b"\x00").endswith(bytes([0x1B, 0x4A, CFG.feed_dots]))


def test_niimbot_packets_are_well_formed():
    img = render_label(LabelTemplate(title="x"), {}, CFG)
    blocks = NiimbotProtocol(CFG).encode(img)
    for block in blocks:
        assert block[:2] == b"\x55\x55" and block[-2:] == b"\xaa\xaa"
        kind, length = block[2], block[3]
        payload = block[4:-3]
        assert len(payload) == length
        checksum = kind ^ length
        for byte in payload:
            checksum ^= byte
        assert checksum == block[-3]


# -- HTTP -------------------------------------------------------------------


def test_status_reports_transports_and_protocols(client):
    body = client.get("/api/status").json()
    assert {t["name"] for t in body["transports"]} == {"null", "usb", "bluetooth", "ble"}
    assert {p["name"] for p in body["protocols"]} == {"escpos", "niimbot", "raw"}
    assert body["state"] == "ready"


def test_healthz_follows_connection_state(client):
    assert client.get("/healthz").status_code == 200


def test_preview_returns_a_png(client):
    res = client.post("/api/preview", json={"template": "auto-kv", "data": {"a": "1"}})
    assert res.status_code == 200
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert int(res.headers["X-Label-Height"]) > 0


def test_print_writes_to_the_spool(client):
    before = sum(f.stat().st_size for f in (data_dir() / "spool").glob("*.bin"))
    res = client.post("/api/print", json={"data": {"Batch": "B-77"}, "copies": 2})
    assert res.status_code == 200 and res.json()["copies"] == 2
    after = sum(f.stat().st_size for f in (data_dir() / "spool").glob("*.bin"))
    assert after > before


def test_bare_json_body_against_a_named_template(client):
    res = client.post("/api/print/asset-tag", json={"id": "A-1042", "owner": "lab 3"})
    assert res.status_code == 200 and res.json()["ok"]


def test_plain_text_endpoint(client):
    res = client.post("/api/print/text/plain", content=b"FRAGILE")
    assert res.status_code == 200


def test_unknown_template_is_404(client):
    assert client.post("/api/print", json={"template": "nope"}).status_code == 404


def test_template_crud_round_trip(client):
    created = client.post(
        "/api/templates",
        json={"name": "Shelf tag", "fields": [{"label": "Bin", "value": "{{bin}}"}]},
    ).json()
    assert created["id"] == "shelf-tag"

    created["title"] = "{{title}}"
    client.put(f"/api/templates/{created['id']}", json=created)
    assert client.get(f"/api/templates/{created['id']}").json()["title"] == "{{title}}"

    assert client.post("/api/print/shelf-tag", json={"bin": "B12"}).status_code == 200
    assert client.delete(f"/api/templates/{created['id']}").status_code == 200
    assert client.get(f"/api/templates/{created['id']}").status_code == 404


def test_deleting_a_shadowed_builtin_restores_it(client):
    original = client.get("/api/templates/qr-only").json()
    edited = {**original, "title": "mine"}
    client.put("/api/templates/qr-only", json=edited)
    assert client.get("/api/templates/qr-only").json()["title"] == "mine"

    body = client.delete("/api/templates/qr-only").json()
    assert body["builtin_restored"] is True
    assert client.get("/api/templates/qr-only").json()["title"] == original["title"]
    assert store.is_builtin("qr-only")


def test_config_patch_survives_a_reload(client):
    client.post("/api/config", json={"width_dots": 576, "density": 7})
    assert client.get("/api/status").json()["config"]["width_dots"] == 576
    from recifi.config import load_config

    assert load_config().density == 7
    client.post("/api/config", json={"width_dots": 384, "density": 3})


def test_raw_console_sends_bytes_and_rejects_bad_hex(client):
    assert client.post("/api/debug/raw", json={"hex": "1b 40"}).json()["sent"] == 2
    assert client.post("/api/debug/raw", json={"hex": "zz"}).status_code == 400
    assert client.post("/api/debug/raw", json={}).status_code == 400


def test_encode_dump_describes_the_job(client):
    body = client.post("/api/debug/encode", json={"template": "qr-only", "data": "hello"}).json()
    assert body["protocol"] == "escpos" and body["bytes"] > 0 and body["head_hex"]


def test_discover_finds_the_virtual_printer(client):
    devices = client.get("/api/discover?transport=null").json()["devices"]
    assert any(d["address"] == "spool" for d in devices)


def test_print_while_disconnected_is_503(client):
    client.post("/api/config", json={"auto_connect": False})
    client.post("/api/disconnect")
    try:
        assert client.post("/api/print", json={"data": "x"}).status_code == 503
    finally:
        client.post("/api/config", json={"auto_connect": True})
        client.post("/api/connect", json={"transport": "null", "address": "spool"})
