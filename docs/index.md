---
title: Home
layout: default
nav_order: 1
---

# ReciFi

A label print server that runs entirely on a **Raspberry Pi Pico 2 W**.

POST some JSON at it and a label comes out. Templates lay the payload out as a
key/value list, a QR code, or both; the board renders the bitmap itself and
sends it to a Bluetooth LE thermal label printer. A web UI served off the Pico
shows printer and WiFi health and manages templates and media settings.

```bash
curl -X POST http://recifi.local/api/print/asset-tag \
  -H 'content-type: application/json' \
  -d '{"id":"A-1042","owner":"lab 3"}'
```

No host machine, no cloud service, no phone app. A £7 microcontroller, 128 KB of
MicroPython, and roughly 370 KB of RAM still free while it prints.

---

## Start here

| | |
|---|---|
| [Getting started]({{ site.baseurl }}/getting-started/) | Flash the Pico, copy the code across, join WiFi, connect the printer |
| [Templates]({{ site.baseurl }}/templates/) | Lay out a label; placeholders, QR codes, fonts |
| [HTTP API]({{ site.baseurl }}/api/) | Every endpoint, with curl examples |
| [Hardware notes]({{ site.baseurl }}/hardware/) | The ORGBRO Z3, TSPL, BLE, and adapting to a different printer |
| [Development]({{ site.baseurl }}/development/) | Architecture, running the tests, how the QR encoder is verified |

## What it does

- **Renders on-device.** Text, word wrap, key/value columns and QR codes are
  drawn into a 1-bit framebuffer sized to the content.
- **Speaks TSPL, ESC/POS and Niimbot.** Transports and command languages are
  independent, so an unfamiliar printer can be worked out one axis at a time.
- **Recovers on its own.** If WiFi will not join, the board raises its own
  access point serving the same UI, so it never needs reflashing to fix a
  typo'd password.
- **Has a QR encoder written from scratch**, because MicroPython has none —
  verified byte-for-byte against the reference implementation across every
  version, error-correction level and mask.

## Requirements

- Raspberry Pi Pico 2 W (RP2350 with WiFi/BLE — the non-W Pico 2 will not work)
- A BLE thermal label printer. Developed against an **ORGBRO Z3**; anything
  speaking TSPL or ESC/POS over a writable BLE characteristic should work.
- Windows with PowerShell for the deployment tools, or any machine with
  [`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html).

> **Bluetooth Classic will not work.** MicroPython on the Pico 2 W exposes BLE
> only. A printer that pairs the way a speaker does, rather than advertising as
> a BLE device, cannot be reached from this board at all — see
> [Hardware notes]({{ site.baseurl }}/hardware/).
