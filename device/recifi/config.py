"""Settings, persisted as JSON on the Pico's flash filesystem.

Defaults are merged *over* whatever is on disk, so a truncated or half-written
config can never stop the board booting -- the worst case is that it comes up
with defaults and an AP you can reach to fix it.
"""

import json
import os

PATH = "config.json"

DEFAULTS = {
    # --- network -------------------------------------------------------
    "wifi": {
        "ssid": "",
        "password": "",
        "hostname": "recifi",
        # Used for the fallback access point when the network won't join.
        "ap_ssid": "ReciFi-setup",
        "ap_password": "recifi-setup",
    },
    # --- printer link --------------------------------------------------
    "transport": "null",  # null | ble | uart
    "address": "",  # BLE MAC (optionally @char-uuid), or UART id
    "protocol": "tspl",  # tspl | escpos | niimbot | raw
    "auto_connect": True,
    "health_interval": 15,
    # --- media ---------------------------------------------------------
    # Physical label size, used for the TSPL SIZE command. The Z3 is 203dpi
    # with a 72mm head. The vendor default is 40x30mm, but the media actually
    # loaded here is 50x30mm -- getting this wrong clips the label.
    "label_width_mm": 50,
    "label_height_mm": 30,
    # Printable area, which is deliberately smaller than the media: the head's
    # origin sits slightly inside the label, so a bitmap the full 400x240 has
    # its right edge and bottom pushed off. 392x216 measured as the largest
    # that lands entirely on a 50x30mm label -- see /api/debug/calibrate.
    "width_dots": 392,
    "dpi": 203,
    "density": 8,  # TSPL DENSITY is 0-15; 0 omits the command entirely
    "speed": 0,  # TSPL SPEED; the Z3 reports isSpeed=false, so left off
    # TSPL DIRECTION. 1, not 0: the Z3 feeds the other way round, so 0 comes
    # out upside down. The vendor's mapping calls this feedDirection: 1.
    "direction": 1,
    "post_print": "tear",  # tear | peel | none
    "gap_mm": None,  # None omits GAP, matching the vendor driver
    "label_type": 1,  # niimbot only: 1 gap, 2 black mark, 3 continuous
    "feed_dots": 48,  # escpos only
    # Tallest label to allocate, and the vertical half of the printable area
    # above. Raise it for continuous media; a 400-wide label costs width/8
    # bytes per dot row, so 800 would be ~38KB of heap.
    "max_height": 216,
    # --- rendering -----------------------------------------------------
    "qr_level": "M",
    "qr_mask": None,  # None = pick the best of the 8 (slower, better symbol)
    # Smallest QR module size in dots. At 203dpi a dot is 0.125mm, so 4 gives
    # 0.5mm modules -- below that a phone camera struggles, especially once
    # thermal bleed has closed the gaps between modules.
    "qr_min_scale": 4,
    "uart_id": 0,
    "uart_tx": 0,
    "uart_rx": 1,
    "uart_baud": 9600,
}


def _merge(base, over):
    out = {}
    for key, value in base.items():
        if isinstance(value, dict):
            supplied = over.get(key)
            out[key] = _merge(value, supplied if isinstance(supplied, dict) else {})
        else:
            out[key] = over[key] if key in over else value
    # Keep unknown keys so a newer UI can round-trip settings we don't know.
    for key, value in over.items():
        if key not in out:
            out[key] = value
    return out


def load():
    try:
        with open(PATH) as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    return _merge(DEFAULTS, stored)


def save(cfg):
    """Write via a temp file so a power cut can't leave a half-written config."""
    tmp = PATH + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(cfg, handle)
    try:
        os.remove(PATH)
    except OSError:
        pass
    os.rename(tmp, PATH)
    return cfg


def patch(cfg, changes):
    """Apply a partial update in place, one level deep for nested sections."""
    for key, value in changes.items():
        if key in cfg and isinstance(cfg[key], dict) and isinstance(value, dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return save(cfg)


def redacted(cfg):
    """A copy safe to hand to the browser -- no WiFi password."""
    out = dict(cfg)
    wifi = dict(out.get("wifi", {}))
    wifi["password"] = "********" if wifi.get("password") else ""
    wifi["ap_password"] = "********" if wifi.get("ap_password") else ""
    out["wifi"] = wifi
    return out
