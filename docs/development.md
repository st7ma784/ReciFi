---
title: Development
layout: default
nav_order: 6
---

# Development
{: .no_toc }

Architecture, tests, and the constraints that shaped both.
{: .fs-5 .fw-300 }

1. TOC
{:toc}

---

## Layout

| Path | |
|---|---|
| `device/main.py` | Boot entry point |
| `device/recifi/server.py` | asyncio HTTP server, hand-rolled |
| `device/recifi/api.py` | Routes, and the 1-bit BMP writer |
| `device/recifi/render.py` | Template + payload → framebuffer |
| `device/recifi/qr.py` | QR encoder, written from scratch |
| `device/recifi/store.py`, `config.py`, `wifi.py` | Persistence and network |
| `device/recifi/printers/` | Transports and protocols |
| `device/www/` | Web UI — three files, no build step |
| `tools/` | Flash, deploy, console, REPL, tests |
| `tests/` | Test suite |
| `attic/linux-server/` | Earlier Raspberry Pi (FastAPI) build, kept for reference |

## Running the tests

```powershell
.\tools\test.ps1                 # the suite
.\tools\test.ps1 -Qr -Preview    # + QR cross-check, + sample labels as .bmp
```

Tests run against the **MicroPython unix port in Docker**, not CPython. That is
deliberate: the gaps that bite are MicroPython-only, and running them under
CPython would hide exactly the bugs worth catching. Two real examples, both
found this way:

- `int.bit_length()` does not exist in MicroPython.
- Dictionaries do not preserve insertion order, so `auto_fields` rows came out
  in a different order on every print until they were sorted explicitly.

`network`, `machine` and `aioble` are absent on the unix port, so the modules
that use them degrade gracefully rather than failing to import.

## How the QR encoder is verified

MicroPython has no QR library, so `device/recifi/qr.py` implements byte-mode
encoding for versions 1–10 across all four error-correction levels: GF(256)
arithmetic, Reed–Solomon, the BCH format and version bits, data interleaving,
all eight mask patterns and the ISO penalty rules.

That is a lot of surface to get subtly wrong, so it is checked two ways:

1. **Byte-exact comparison.** `tools/qrcheck.py` builds every combination of
   version × EC level × mask — 640 in all — with both this encoder and CPython's
   `qrcode` library, and compares the matrices module by module.
2. **A real decoder.** The auto-masked output is rendered and read back with
   OpenCV, which proves the thing actually scans rather than merely matching.

This caught a genuine bug: the Reed–Solomon generator polynomial multiplied its
coefficients in the wrong order, corrupting every symbol past the data/ECC
boundary. The failure was invisible in the first ~17 codewords, which is exactly
the kind of thing eyeballing a printed QR would not find.

Mask selection is the expensive part — eight full penalty evaluations. The
scoring loops are hand-unrolled with short-circuiting and allocate nothing,
which took a typical encode from 140 ms to 19 ms, and results are memoised
because previewing then printing encodes the same QR twice.

## Constraints worth knowing

The board has roughly 400 KB of usable heap. Measured: ~73 KB of module
bytecode, ~31 KB for a worst-case label buffer, leaving comfortable headroom.

- **Encoders are generators.** A large label is tens of KB of raster; building
  the whole command stream as one buffer would double that for no reason.
- **The framebuffer is the wire format.** `MONO_HLSB` with 1 = burn is exactly
  what raster protocols want, so the printer path never copies or inverts it —
  except for TSPL, which wants the opposite polarity.
- **Static files stream** off flash in 512-byte chunks; nothing reads a whole
  file into RAM.
- **One print job at a time.** These printers have no command queueing, so jobs
  are serialised behind a lock, and the health poller never interrupts one.
- **Requests are bounded** in header length, header count and body size.

## Deployment without Python

`tools/deploy.ps1` reimplements what `mpremote fs cp` does, in PowerShell,
because the development machine had no Python and Docker on Windows cannot pass
a COM port through to a container. Files are base64-encoded and pushed through
the raw REPL, which sidesteps every quoting and binary-safety problem.

`tools/repl.ps1` runs a snippet on the board and prints the output — the fastest
way to poke at hardware the API does not expose. `tools/snippets/` has ready-made
ones for dumping a GATT table and probing an unknown protocol.

## Adding a protocol

Write a generator in `device/recifi/printers/protocols.py` that takes
`(label, cfg)` and yields byte blocks, then register it in `ENCODERS` and add a
line to `DESCRIPTIONS`. It shows up in the UI automatically.

`label.buf` is the packed 1-bit raster, `label.stride` bytes per row,
`label.height` rows.
