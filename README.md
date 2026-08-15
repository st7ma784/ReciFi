# ReciFi

**A label print server that runs entirely on a Raspberry Pi Pico 2 W.**

[![tests](https://github.com/st7ma784/ReciFi/actions/workflows/tests.yml/badge.svg)](https://github.com/st7ma784/ReciFi/actions/workflows/tests.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![docs](https://img.shields.io/badge/docs-github%20pages-informational)](https://st7ma784.github.io/ReciFi/)

POST some JSON at it and a label comes out. Templates lay the payload out as a
key/value list, a QR code, or both; the board renders the bitmap itself and
sends it to a Bluetooth LE thermal label printer. A web UI served off the Pico
shows printer and WiFi health and manages templates and media settings.

```bash
curl -X POST http://recifi.local/api/print/asset-tag \
  -H 'content-type: application/json' \
  -d '{"id":"A-1042","owner":"lab 3"}'
```

No host machine, no cloud service, no phone app — a £7 microcontroller running
128 KB of MicroPython, with ~370 KB of RAM still free while it prints.

📖 **[Full documentation](https://st7ma784.github.io/ReciFi/)**

---

## Quick start

Hold **BOOTSEL**, plug the board in, then:

```powershell
.\tools\flash.ps1                                        # install MicroPython (erases the board)
.\tools\deploy.ps1 -Ssid YOUR_WIFI -Password 'secret'    # copy the code + join WiFi
.\tools\console.ps1                                      # watch it boot; it prints its IP
```

Open the address it reports, connect the printer on the *Connection* tab, set
your label size, and print. The [getting started
guide](https://st7ma784.github.io/ReciFi/getting-started/) covers each step,
including getting the geometry right.

The tools are PowerShell and need nothing installed — not even Python. They talk
to the board over its USB serial REPL directly.

## What's in the box

- **On-device rendering** — text with word wrap, aligned key/value columns, QR
  codes, all drawn into a 1-bit framebuffer sized to the content.
- **Three command languages** — TSPL, ESC/POS and Niimbot. Transports and
  protocols are independent, so an unfamiliar printer can be worked out one axis
  at a time.
- **A QR encoder written from scratch**, because MicroPython has none. Verified
  byte-for-byte against the reference implementation across all 640 combinations
  of version, error-correction level and mask, and decoded back with OpenCV.
- **Self-recovering WiFi** — if the network will not join, the board raises its
  own access point serving the same UI, so a typo'd password never means
  reflashing.
- **Debug tooling that earns its place** — a printable calibration pattern, a
  raw-bytes console, and a protocol prober that identifies a printer without
  wasting labels.

## Requirements

- **Raspberry Pi Pico 2 W** — the RP2350 with WiFi/BLE. The non-W Pico 2 will
  not work.
- A **BLE** thermal label printer. Developed against an ORGBRO Z3.
- Windows/PowerShell for the tools, or any machine with `mpremote`.

> ⚠️ **Bluetooth Classic will not work.** MicroPython on the Pico 2 W exposes
> BLE only. A printer that pairs the way a speaker does cannot be reached from
> this board at all. See [hardware
> notes](https://st7ma784.github.io/ReciFi/hardware/).

## Repository layout

| Path | |
|---|---|
| `device/` | Everything that goes on the Pico |
| `tools/` | Flash, deploy, console, REPL and test scripts |
| `tests/` | Test suite, run under real MicroPython in Docker |
| `docs/` | The documentation site |
| `attic/linux-server/` | An earlier Raspberry Pi (Linux/FastAPI) build, kept for reference |

## Development

```powershell
.\tools\test.ps1                 # 85 tests under real MicroPython 1.28
.\tools\test.ps1 -Qr -Preview    # + QR cross-check against CPython, + sample labels
```

Tests run against the MicroPython unix port rather than CPython, deliberately —
the gaps that bite are MicroPython-only. See
[development](https://st7ma784.github.io/ReciFi/development/) for the
architecture and the memory constraints that shaped it.

## Contributing

Issues and pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Protocol reports for other printers are especially useful: a GATT dump and a
working command sequence is enough to add support.

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with ORGBRO. The Z3 protocol notes are the result of independent
work with hardware the author owns.
