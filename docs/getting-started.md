---
title: Getting started
layout: default
nav_order: 2
---

# Getting started
{: .no_toc }

From a bare Pico 2 W to a printed label.
{: .fs-5 .fw-300 }

1. TOC
{:toc}

---

## What you need

- A **Raspberry Pi Pico 2 W**. The RP2350 chip with the wireless module — the
  plain Pico 2 has no radio and cannot run this.
- A USB cable that carries data. A charge-only cable will power the board and
  nothing else, which looks exactly like a dead board.
- A BLE thermal label printer, and labels for it.

The tools in `tools/` are PowerShell and need nothing installed — not even
Python. They talk to the board over its USB serial REPL directly.

## 1. Flash MicroPython

Hold the **BOOTSEL** button while plugging the board in. It appears as a
removable drive called `RP2350`.

```powershell
.\tools\flash.ps1
```

This downloads the `RPI_PICO2_W` MicroPython UF2 (v1.28.0, cached under
`tools\firmware`), checks the drive really is an RP2350, and copies it across.
The board reboots into MicroPython by itself and reappears as a COM port.

> **This erases everything on the board.** Run `.\tools\flash.ps1 -WhatIf` first
> if you want to see what it would do.

Add `-RiscV` to use the RISC-V build instead of ARM. There is no reason to
unless you specifically want it.

## 2. Copy the code across and join WiFi

```powershell
.\tools\deploy.ps1 -Ssid MANDER84 -Password 'your-password'
```

That copies the 15 files under `device/` (about 128 KB), writes the WiFi
credentials into `config.json` **on the board**, and resets it. The password is
built on-device from base64 so it never appears in a shell history file.

Useful flags:

| Flag | |
|---|---|
| `-List` | Show candidate COM ports and exit |
| `-Port COM17` | Pick the port explicitly |
| `-Files "recifi/render.py"` | Copy only some files — much faster when iterating |
| `-NoReset` | Leave the board at the REPL instead of restarting |

## 3. Find it on the network

```powershell
.\tools\console.ps1
```

The boot log prints the address it got:

```
ReciFi starting, 393376 bytes free
HTTP server listening on port 80
up: http://192.168.1.19/  (388944 bytes free)
```

Open that in a browser. Where mDNS works, `http://recifi.local/` also resolves.

### If WiFi does not join

After three failed attempts the board raises its own access point —
**`ReciFi-setup`**, password `recifi-setup`. Join it and open
`http://192.168.4.1/`; the *Connection* tab has a WiFi panel that will scan and
let you re-enter the password. No reflashing required.

## 4. Connect the printer

Power the printer on, then on the **Connection** tab press **Scan**. Devices
that look like printers are marked with a ★.

Pick yours, then **Save & connect**. The BLE write characteristic is detected
automatically; pin it explicitly as `MAC@uuid` if the guess is wrong.

## 5. Set the media size

This is the step that decides whether labels come out right, and it is worth
doing carefully.

1. Set **Label width/height (mm)** to your actual labels.
2. Press **Fit width** to compute the render width, then subtract a little —
   the printable area is slightly smaller than the label, because the head's
   origin sits inside the media.
3. Press **Calibrate media** so the printer measures the label gaps itself.
4. Visit `/api/debug/calibrate` to print a test pattern: a border, a block in
   each corner, and a tick every 20 dots.

**All four corner blocks must be on the label.** Whichever ones are missing tell
you which edge is over:

| Symptom | Fix |
|---|---|
| Right-hand blocks clipped | Reduce **Width (dots)** |
| Bottom blocks clipped | Reduce **Max height** |
| Whole thing upside down | **Direction** 0 ↔ 1 |
| Diagonal smearing | Width is badly wrong — recompute from mm × dpi |

For reference, a 50 × 30 mm label on an ORGBRO Z3 at 203 dpi is 400 × 240 dots
of media and **392 × 216 dots** of usable printable area.

## 6. Print something

The **Print** tab has a template picker, a JSON payload box, and a preview that
renders on the board. Or from anywhere on the network:

```bash
curl -X POST http://recifi.local/api/print \
  -H 'content-type: application/json' \
  -d '{"data": {"Batch": "B-77", "Baked": "2026-08-15", "By": "sam"}}'
```

## Optional: install aioble

Recent MicroPython builds for the Pico 2 W include `aioble` already, and BLE
works out of the box. If your build does not have it:

```powershell
.\tools\deploy.ps1 -InstallBle
```

The board fetches it over WiFi with `mip`, so it needs to be on the network
first.
