---
title: Hardware notes
layout: default
nav_order: 5
---

# Hardware notes
{: .no_toc }

What the ORGBRO Z3 actually speaks, and how to work out a different printer.
{: .fs-5 .fw-300 }

1. TOC
{:toc}

---

## The Pico 2 W can only do BLE

MicroPython on the Pico 2 W exposes **Bluetooth Low Energy only**. The CYW43439
radio supports Classic (BR/EDR) in hardware, but the firmware does not surface
it.

This matters more than it sounds. Plenty of cheap label printers are Classic
SPP devices — they pair the way a speaker does. Those **cannot be reached from
this board at all**, whatever you send. If your printer pairs from the phone's
Bluetooth settings rather than appearing only inside its own app, that is the
likely explanation.

Options if you land there: the wired `uart` transport, or a Linux SBC instead
(there is a working FastAPI implementation in `attic/linux-server/`).

## The ORGBRO Z3

Everything below was established against real hardware.

| | |
|---|---|
| Advertises as | `Z3-<last 4 of MAC>`, e.g. `Z3-E1D5` |
| Transport | BLE, ATT MTU **247** once negotiated |
| Command language | **TSPL/TSPL2** |
| Resolution | 203 dpi |
| Head width | 72 mm max |
| Media sizes | 30×20, 40×30, 50×30, 50×50, 50×80 mm |
| Feed direction | `DIRECTION 1` — with 0 every label prints upside down |

### GATT table

| Service | Characteristic | Properties |
|---|---|---|
| `0x18F0` | `0x2AF1` | write, write-without-response |
| `0x18F0` | `0x2AF0` | notify |
| `0x1800` Generic Access | `0x2A00`, `0x2A01` | read/write |
| `e7810a71-73ae-499d-8c15-faa9aef0c3f2` | `bef8d6c9-…` | write, write-without-response |
| `49535343-fe7d-…` ISSC transparent UART | `49535343-8841-…` | write-without-response |
| `49535343-fe7d-…` | `49535343-1e4d-…` | notify |

`0x18F0`/`0x2AF1` is the de-facto printing service on this class of printer, and
is what the driver selects.

> `0x1800`/`0x2A00` is writable, but it is the device **name**. A naive "first
> writable characteristic" driver renames your printer instead of printing to
> it. Standard SIG services are skipped explicitly for this reason.

### The TSPL job

```
SET PEEL OFF
SET TEAR ON
SIZE 50 mm,30 mm
DENSITY 8
DIRECTION 1,0
CLS
BITMAP 0,0,<widthBytes>,<height>,0,<packed rows>
PRINT 1,1
```

Commands are CRLF-terminated text; the bitmap is raw bytes appended straight
after the `BITMAP` header.

**In TSPL, bit 1 means white.** That is inverted from the framebuffer, where 1
means burn, and it is the one place the raster gets copied rather than passed
through untouched.

### Printable area is smaller than the label

The head's origin sits slightly inside the media, so a bitmap covering the full
label loses its right edge and bottom rows. Measured on 50 × 30 mm stock:

| | Dots |
|---|---|
| Media | 400 × 240 |
| **Printable** | **392 × 216** |

Expect to re-measure after changing media. `GET /api/debug/calibrate` prints a
pattern that makes it obvious which edge is over.

## Bringing up a different printer

**Look for a vendor driver before doing anything else.** ORGBRO ships a ChromeOS
one, which is a Chrome extension — unzip it and the command language, the BLE
UUIDs and the media table are all there in readable JavaScript:

- `TsplCommand` — the full command vocabulary
- `BleDevice` — `serviceUUID = 6384` (0x18F0), `characteristicUUID = 10993` (0x2AF1)
- `printer-mapping.json` — dpi, head width, media sizes, feed direction

That turned "capture and reverse-engineer the protocol" into "read it". Windows
and Android drivers are often equally revealing.

Failing that:

1. **Get the transport connected first.** A green Connection tab means bytes
   have somewhere to go, independently of whether the printer understands them.
2. **Try each protocol.** `tspl` first for label printers, `escpos` for receipt
   printers, then `niimbot`. Print `qr-only` — a QR is obviously right or
   obviously wrong.
3. **Probe instead of guessing.** `tools\snippets\probe.py` sends a paper-feed
   command and a status query down every writable characteristic while
   listening on the notify ones. A reply identifies the protocol without
   printing anything at all.
4. **Capture the vendor app.** On Android, enable *Developer options → Bluetooth
   HCI snoop log*, print one label, then `adb bugreport bug.zip`. Open
   `btsnoop_hci.log` in Wireshark and filter
   `btatt.opcode == 0x52 || btatt.opcode == 0x12`.
5. **Compare.** `POST /api/debug/encode` shows what we would have sent.

Adding a protocol is one generator function in
`device/recifi/printers/protocols.py`, registered in `ENCODERS`.

### Symptoms worth knowing

| Symptom | Usual cause |
|---|---|
| Right height, blank paper | Raster wrapper wrong, feed commands right |
| Diagonal smearing | `width_dots` does not match the head |
| Upside down | `DIRECTION` |
| Edges clipped | Printable area set to the media size |
| Bytes accepted, nothing happens | Wrong command language entirely |
| `EALREADY` on BLE write | Unacknowledged writes outrunning the stack — use acknowledged writes |
