"""WiFi: join the configured network, or fall back to an access point.

The AP fallback matters more here than on a Linux box -- there is no console to
log into. If the credentials are wrong or the network is gone, the Pico brings
up "ReciFi-setup" and serves the same UI, so the settings can be fixed from a
phone rather than by reflashing.
"""

import asyncio
import time

try:
    import network
except ImportError:  # the unix port used for tests has no network module
    network = None

STATE = {
    "mode": "down",  # down | sta | ap
    "connected": False,
    "ssid": "",
    "ip": "",
    "rssi": None,
    "error": "",
    "since": 0,
}

_sta = None
_ap = None

# network.STAT_* values, spelled out because they differ between ports.
_ERRORS = {
    -3: "wrong password",
    -2: "no access point with that name in range",
    -1: "connection failed",
}


def available():
    return network is not None


def status():
    out = dict(STATE)
    if _sta is not None and STATE["mode"] == "sta":
        try:
            if _sta.isconnected():
                out["ip"] = _sta.ifconfig()[0]
                out["rssi"] = _sta.status("rssi")
            else:
                out["connected"] = False
        except (OSError, ValueError):
            pass
    return out


async def scan():
    """Nearby networks, strongest first. Used by the setup UI."""
    if network is None:
        return []
    global _sta
    if _sta is None:
        _sta = network.WLAN(network.STA_IF)
    _sta.active(True)
    try:
        found = _sta.scan()
    except OSError as exc:
        return [{"ssid": "", "error": str(exc)}]
    seen = {}
    for entry in found:
        ssid = entry[0].decode("utf-8", "replace") if isinstance(entry[0], bytes) else str(entry[0])
        if not ssid:
            continue
        rssi = entry[3]
        # A network can answer on several channels; keep the strongest.
        if ssid not in seen or rssi > seen[ssid]["rssi"]:
            seen[ssid] = {"ssid": ssid, "rssi": rssi, "channel": entry[2], "secure": entry[4] > 0}
    out = list(seen.values())
    out.sort(key=lambda n: -n["rssi"])
    return out


async def connect(cfg, timeout=20):
    """Join the configured network. Returns True once an IP is assigned."""
    global _sta
    if network is None:
        STATE["error"] = "no network module on this port"
        return False

    wifi = cfg.get("wifi", {})
    ssid = wifi.get("ssid", "")
    if not ssid:
        STATE["error"] = "no SSID configured"
        return False

    stop_ap()
    _sta = network.WLAN(network.STA_IF)
    _sta.active(True)
    try:
        # Makes the board resolvable as <hostname>.local via mDNS.
        network.hostname(wifi.get("hostname", "recifi"))
    except (AttributeError, OSError, ValueError):
        pass

    if not _sta.isconnected():
        _sta.connect(ssid, wifi.get("password", ""))

    STATE["mode"] = "sta"
    STATE["ssid"] = ssid
    deadline = time.ticks_add(time.ticks_ms(), int(timeout * 1000))
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if _sta.isconnected():
            STATE["connected"] = True
            STATE["ip"] = _sta.ifconfig()[0]
            STATE["error"] = ""
            STATE["since"] = time.time()
            return True
        state = _sta.status()
        if state in _ERRORS:
            STATE["error"] = _ERRORS[state]
            STATE["connected"] = False
            return False
        await asyncio.sleep_ms(250)

    STATE["error"] = "timed out joining %s" % ssid
    STATE["connected"] = False
    return False


def start_ap(cfg):
    """Bring up the setup access point."""
    global _ap
    if network is None:
        return False
    wifi = cfg.get("wifi", {})
    _ap = network.WLAN(network.AP_IF)
    _ap.config(essid=wifi.get("ap_ssid", "ReciFi-setup"),
               password=wifi.get("ap_password", "recifi-setup"))
    _ap.active(True)
    STATE["mode"] = "ap"
    STATE["connected"] = False
    STATE["ssid"] = wifi.get("ap_ssid", "ReciFi-setup")
    try:
        STATE["ip"] = _ap.ifconfig()[0]
    except (OSError, ValueError):
        STATE["ip"] = "192.168.4.1"
    return True


def stop_ap():
    global _ap
    if _ap is not None:
        try:
            _ap.active(False)
        except OSError:
            pass
        _ap = None


async def keep_connected(cfg):
    """Background task: join, fall back to AP, and recover if the link drops."""
    attempt = 0
    while True:
        if await connect(cfg):
            attempt = 0
            # Watch the link rather than polling hard; reconnect if it drops.
            while _sta is not None and _sta.isconnected():
                await asyncio.sleep(5)
            STATE["connected"] = False
            STATE["error"] = "link dropped"
            continue

        attempt += 1
        # Three failures is enough to conclude the network isn't coming back
        # on its own; put up the setup AP so there is a way in.
        if attempt >= 3 and STATE["mode"] != "ap":
            start_ap(cfg)
        await asyncio.sleep(min(30, 5 * attempt))
