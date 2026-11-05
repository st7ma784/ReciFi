"""Logo storage, scaling, placement on a label, and the upload route."""

import asyncio
import json
import os

from recifi import api, config, logos, store
from recifi.printers import Manager
from recifi.render import render
from recifi.server import Request

CFG = {"width_dots": 384, "qr_level": "M", "max_height": 800}


def _clear():
    for name in logos.names():
        logos.delete(name)


def _square(side):
    """A solid black square, packed the way the browser uploads it."""
    stride = (side + 7) // 8
    row = bytearray(stride)
    for x in range(side):
        row[x >> 3] |= 0x80 >> (x & 7)
    return bytes(row) * side


def _save(name, side):
    data = _square(side)
    pos = [0]

    async def read(n):
        chunk = data[pos[0] : pos[0] + n]
        pos[0] += len(chunk)
        return chunk

    return asyncio.run(logos.save_stream(name, side, side, read))


def _black(label, x, y):
    return label.fb.pixel(x, y) == 1


def test_save_load_round_trip():
    _clear()
    saved = _save("Acme Logo!", 20)
    assert saved == {"name": "acme-logo", "width": 20, "height": 20}
    bitmap = logos.load("acme-logo")
    assert bitmap.width == 20 and bitmap.height == 20
    assert bitmap.fb.pixel(19, 19) == 1
    assert logos.listing() == [saved]
    assert logos.delete("acme-logo")
    assert logos.load("acme-logo") is None


def test_check_upload_rejects_bad_sizes():
    assert logos.check_upload("a", 601, 10, logos.data_size(601, 10))
    assert logos.check_upload("a", 10, 10, 5)
    assert logos.check_upload("!!!", 10, 10, logos.data_size(10, 10))
    assert logos.check_upload("a", 600, 600, 45000) is None


def test_fit_keeps_aspect_and_only_shrinks():
    _clear()
    _save("sq", 40)
    bitmap = logos.load("sq")
    assert logos.fit(bitmap, 100, 100) is bitmap
    small = logos.fit(bitmap, 10, 20)
    assert (small.width, small.height) == (10, 10)
    assert small.fb.pixel(9, 9) == 1


def test_top_logo_sits_above_the_title():
    _clear()
    _save("top", 32)
    tpl = store.normalise({"title": "Hi", "logo": {"name": "top", "position": "top"}})
    label = render(tpl, {}, CFG)
    # Centred: margin 8 + (368 - 32) // 2 = 176.
    assert _black(label, 176, 8) and _black(label, 207, 39)
    assert not _black(label, 175, 8)
    assert label.height >= 8 + 32 + 4 + 24


def test_side_logo_takes_a_column_and_respects_size():
    _clear()
    _save("side", 64)
    tpl = store.normalise({"title": "Hi", "logo": {"name": "side", "position": "left", "size": 32}})
    label = render(tpl, {}, CFG)
    assert _black(label, 8, label.height // 2)
    assert not _black(label, 8 + 32, label.height // 2)


def test_logo_name_can_be_a_placeholder_and_missing_is_skipped():
    _clear()
    _save("brand-a", 16)
    tpl = store.normalise({"title": "x", "logo": {"name": "{{brand}}"}})
    with_logo = render(tpl, {"brand": "brand-a"}, CFG)
    without = render(tpl, {"brand": "nope"}, CFG)
    assert with_logo.height > without.height


def test_logo_and_side_qr_share_a_label():
    _clear()
    _save("l", 24)
    tpl = store.normalise({"title": "x", "qr": {"value": "hello", "position": "right"},
                           "logo": {"name": "l", "position": "left"}})
    label = render(tpl, {}, CFG)
    assert _black(label, 8, label.height // 2)


def test_upload_route_streams_to_flash():
    _clear()
    for name in (store.PATH, config.PATH):
        try:
            os.remove(name)
        except OSError:
            pass
    cfg = config.load()
    cfg["transport"] = "null"
    router = api.build(cfg, Manager(cfg))

    data = _square(16)
    handler, params = router.match("POST", "/api/logos/up")
    assert handler in router.raw
    request = Request("POST", "/api/logos/up", {"w": "16", "h": "16"}, {}, data)
    request.params = params
    response = asyncio.run(handler(request))
    assert response.status == 201, response.body
    assert json.loads(response.body)["name"] == "up"

    handler, params = router.match("GET", "/api/logos/up")
    request = Request("GET", "/api/logos/up", {}, {}, b"")
    request.params = params
    response = asyncio.run(handler(request))
    assert response.content_type == "image/bmp"

    bad = Request("POST", "/api/logos/up", {"w": "16", "h": "16"}, {}, b"\x00")
    bad.params = {"name": "up"}
    assert asyncio.run(router.match("POST", "/api/logos/up")[0](bad)).status == 400
    _clear()
