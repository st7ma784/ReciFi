import os

from recifi import config, store


def _clean():
    for name in (store.PATH, config.PATH):
        try:
            os.remove(name)
        except OSError:
            pass


def test_builtins_are_present_by_default():
    _clean()
    ids = set(store.load_all().keys())
    assert {"auto-kv", "qr-only", "asset-tag"} <= ids


def test_save_generates_a_slug_id():
    _clean()
    saved = store.save({"name": "Shelf tag!", "fields": [{"label": "Bin", "value": "{{bin}}"}]})
    assert saved["id"] == "shelf-tag"
    assert store.get("shelf-tag")["fields"][0]["label"] == "Bin"


def test_ids_do_not_collide():
    _clean()
    first = store.save({"name": "Tag"})
    second = store.save({"name": "Tag"})
    assert first["id"] != second["id"]


def test_lookup_by_name_and_case():
    _clean()
    store.save({"name": "Shelf tag"})
    assert store.get("Shelf tag")["id"] == "shelf-tag"
    assert store.get("shelf TAG")["id"] == "shelf-tag"
    assert store.get("nope") is None


def test_user_copy_shadows_builtin_and_delete_restores_it():
    _clean()
    original = store.get("qr-only")
    edited = dict(original)
    edited["title"] = "mine"
    store.save(edited)
    assert store.get("qr-only")["title"] == "mine"

    assert store.delete("qr-only") is True
    assert store.get("qr-only")["title"] == original["title"]
    assert store.is_builtin("qr-only")


def test_delete_unknown_is_false():
    _clean()
    assert store.delete("not-a-template") is False


def test_normalise_clamps_and_coerces():
    tpl = store.normalise({
        "name": "x", "title_scale": 99, "text_scale": 0, "margin": -5,
        "align": "diagonal", "fields": [{"label": "", "value": ""}, {"value": "keep"}],
        "qr": {"value": "u", "position": "sideways", "scale": 500},
    })
    assert tpl["title_scale"] == 4
    assert tpl["text_scale"] == 1
    assert tpl["margin"] == 0
    assert tpl["align"] == "left"
    assert tpl["fields"] == [{"label": "", "value": "keep", "emphasis": False}]
    assert tpl["qr"]["position"] == "bottom"
    assert tpl["qr"]["scale"] == 12


def test_qr_absent_when_empty():
    assert store.normalise({"name": "x", "qr": {"value": ""}})["qr"] is None


def test_corrupt_template_file_does_not_explode():
    _clean()
    with open(store.PATH, "w") as fh:
        fh.write("{not json at all")
    assert "auto-kv" in store.load_all()


# ------------------------------------------------------------------ config


def test_config_defaults_when_missing():
    _clean()
    cfg = config.load()
    assert cfg["width_dots"] == 392  # printable area of a 50mm label at 203dpi
    assert cfg["protocol"] == "tspl"
    # The Z3 feeds the other way round; DIRECTION 0 prints upside down.
    assert cfg["direction"] == 1
    assert cfg["wifi"]["ssid"] == ""


def test_printable_area_fits_inside_the_media():
    """The printable area must be no bigger than the label, but close to it.

    Bigger clips the print; much smaller wastes the label. The head's origin is
    inset slightly, so these are deliberately not equal.
    """
    from recifi.render import dots_for_mm

    _clean()
    cfg = config.load()
    media_w = dots_for_mm(cfg["label_width_mm"], cfg["dpi"])
    media_h = dots_for_mm(cfg["label_height_mm"], cfg["dpi"])
    assert cfg["width_dots"] <= media_w
    assert cfg["max_height"] <= media_h
    assert media_w - cfg["width_dots"] <= 24  # an inset, not a mismatch
    assert media_h - cfg["max_height"] <= 32


def test_dots_for_mm_rounds_up_to_a_whole_byte():
    from recifi.render import dots_for_mm

    assert dots_for_mm(50, 203) == 400
    assert dots_for_mm(40, 203) == 320
    assert dots_for_mm(30, 203) % 8 == 0


def test_config_round_trips():
    _clean()
    cfg = config.load()
    config.patch(cfg, {"width_dots": 576, "density": 7})
    assert config.load()["width_dots"] == 576
    assert config.load()["density"] == 7


def test_nested_patch_merges_rather_than_replaces():
    _clean()
    cfg = config.load()
    config.patch(cfg, {"wifi": {"ssid": "MANDER84"}})
    stored = config.load()
    assert stored["wifi"]["ssid"] == "MANDER84"
    # hostname must survive a partial wifi update
    assert stored["wifi"]["hostname"] == "recifi"


def test_corrupt_config_falls_back_to_defaults():
    _clean()
    with open(config.PATH, "w") as fh:
        fh.write("]]not json[[")
    assert config.load()["width_dots"] == config.DEFAULTS["width_dots"]


def test_unknown_keys_survive_a_round_trip():
    _clean()
    cfg = config.load()
    config.patch(cfg, {"future_setting": 42})
    assert config.load()["future_setting"] == 42


def test_redacted_hides_the_wifi_password():
    _clean()
    cfg = config.load()
    config.patch(cfg, {"wifi": {"ssid": "MANDER84", "password": "hunter2"}})
    safe = config.redacted(config.load())
    assert safe["wifi"]["ssid"] == "MANDER84"
    assert "hunter2" not in str(safe)
