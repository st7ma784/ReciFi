from recifi import render
from recifi.render import FONT_H, FONT_W, normalise_payload, resolve, substitute

CFG = {"width_dots": 384, "qr_level": "M", "max_height": 800}


def _blank(label):
    return not any(label.buf)


def test_payload_shapes_all_normalise():
    assert normalise_payload({"a": 1})["a"] == 1
    assert normalise_payload("hi")["value"] == "hi"
    assert normalise_payload([{"label": "k", "value": "v"}])["k"] == "v"
    assert normalise_payload([["k", "v"]])["k"] == "v"
    assert normalise_payload({"addr": {"city": "Lancaster"}})["addr.city"] == "Lancaster"


def test_substitute_is_case_insensitive_and_drops_unknowns():
    assert substitute("{{Name}}", {"name": "sam"}) == "sam"
    assert substitute("x{{nope}}y", {}) == "xy"
    assert substitute("no placeholders", {}) == "no placeholders"
    # An unterminated placeholder must not eat the rest of the string.
    assert substitute("a{{b", {}) == "a{{b"


def test_missing_value_placeholder_drops_the_row():
    tpl = {"fields": [{"label": "Owner", "value": "{{owner}}"},
                      {"label": "Kind", "value": "literal"}]}
    rows = resolve(tpl, {})["rows"]
    assert rows == [("Kind", "literal", False)]


def test_auto_fields_are_sorted_and_skip_consumed_keys():
    tpl = {"title": "{{title}}", "auto_fields": True}
    rows = resolve(tpl, {"title": "T", "zebra": 1, "apple": 2, "mango": 3})["rows"]
    assert [r[0] for r in rows] == ["apple", "mango", "zebra"]


def test_render_is_head_width_and_byte_aligned():
    label = render.render({"title": "Hello", "fields": [{"label": "SKU", "value": "ABC"}]}, {}, CFG)
    assert label.width == 384
    assert label.stride == 48
    assert label.height % 8 == 0
    assert len(label.buf) == label.stride * label.height
    assert not _blank(label)


def test_taller_content_makes_a_taller_label():
    short = render.render({"fields": [{"value": "one"}]}, {}, CFG)
    tall = render.render({"fields": [{"value": "word " * 60}]}, {}, CFG)
    assert tall.height > short.height


def test_long_unbroken_string_does_not_overflow_the_width():
    label = render.render({"fields": [{"value": "X" * 400}]}, {}, CFG)
    assert label.width == 384
    assert label.height <= CFG["max_height"] + 8


def test_max_height_is_respected():
    cfg = dict(CFG)
    cfg["max_height"] = 200
    label = render.render({"fields": [{"value": "word " * 400}]}, {}, cfg)
    assert label.height <= 200 + 8


def _title_lines(tpl, width):
    items, _ = render._build(tpl, render.resolve(tpl, {}), width, CFG, False)
    return [i for i in items if i[0] == "text"]


def test_title_shrinks_rather_than_wrapping_mid_word():
    # "Spectrometer" is 12 chars: 288 dots at scale 3, too wide for a 200-dot
    # column, but it fits at scale 2 -- so it should shrink, not wrap.
    lines = _title_lines({"title": "Spectrometer", "title_scale": 3, "text_scale": 1}, 200)
    assert len(lines) == 1
    assert lines[0][2] == 2


def test_title_still_wraps_when_it_cannot_shrink_enough():
    # At the text_scale floor it still does not fit, so wrapping takes over
    # rather than shrinking the title below body text.
    lines = _title_lines({"title": "A very long title indeed", "title_scale": 3,
                          "text_scale": 3}, 200)
    assert len(lines) > 1
    assert all(line[2] == 3 for line in lines)


def test_qr_renders_in_every_position():
    heights = {}
    for position in ("top", "bottom", "left", "right"):
        tpl = {"title": "Asset", "fields": [{"label": "ID", "value": "{{id}}"}],
               "qr": {"value": "{{id}}", "position": position, "scale": 3}}
        label = render.render(tpl, {"id": "A-1042"}, CFG)
        assert not _blank(label)
        heights[position] = label.height
    # A side QR shares vertical space with the text; a stacked one adds to it.
    assert heights["right"] < heights["top"]


def test_qr_never_drawn_below_the_minimum_module_size():
    # A QR too small to scan is worse than no QR, so the template's scale is a
    # request that the configured floor can raise.
    from recifi import qr as qrmod
    from recifi.render import _fit_qr

    matrix = qrmod.encode("A-1042")
    scale, _ = _fit_qr(matrix, 1, 400, 4)
    assert scale == 4


def test_qr_still_shrinks_when_the_box_is_too_small():
    # The floor must not push a QR outside the space available for it.
    from recifi import qr as qrmod
    from recifi.render import _fit_qr

    matrix = qrmod.encode("A-1042")
    modules = matrix.size + 8
    scale, size = _fit_qr(matrix, 8, modules * 2, 4)
    assert scale == 2
    assert size <= modules * 2


def test_qr_min_scale_applies_through_a_render():
    tpl = {"qr": {"value": "{{u}}", "position": "top", "scale": 1}}
    small_cfg = CFG.copy()
    small_cfg["qr_min_scale"] = 1
    big_cfg = CFG.copy()
    big_cfg["qr_min_scale"] = 5
    small = render.render(tpl, {"u": "A-1042"}, small_cfg)
    big = render.render(tpl, {"u": "A-1042"}, big_cfg)
    assert big.height > small.height


def test_bare_string_payload_becomes_the_qr_value():
    tpl = {"qr": {"value": "{{url}}", "position": "top"}}
    assert resolve(tpl, "https://example.com")["qr_value"] == "https://example.com"


def test_blank_label_encodes_as_no_burn():
    label = render.render({"min_height": 64}, {}, CFG)
    assert _blank(label), "an empty label must have no set bits"


def test_min_height_pads_the_label():
    label = render.render({"fields": [{"value": "x"}], "min_height": 300}, {}, CFG)
    assert label.height >= 300


def test_scaled_text_is_taller_than_unscaled():
    one = render.render({"fields": [{"value": "abc"}], "text_scale": 1}, {}, CFG)
    four = render.render({"fields": [{"value": "abc"}], "text_scale": 4}, {}, CFG)
    assert four.height > one.height


def test_emphasis_does_not_break_layout():
    tpl = {"fields": [{"label": "ID", "value": "A-1", "emphasis": True},
                      {"label": "Owner", "value": "sam"}]}
    label = render.render(tpl, {}, CFG)
    assert not _blank(label)
