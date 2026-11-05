---
title: HTTP API
layout: default
nav_order: 4
---

# HTTP API
{: .no_toc }

Everything the board serves, on port 80.
{: .fs-5 .fw-300 }

1. TOC
{:toc}

---

There is **no authentication**. Keep the board on a trusted network, or put a
reverse proxy in front of it.

Request bodies are capped at 12 KB and headers at 512 bytes per line — a
microcontroller must never allocate an unbounded request.

## Printing

### `POST /api/print`

```json
{"template": "asset-tag", "data": {...}, "copies": 1}
```

| Field | |
|---|---|
| `template` | Template id or name. Omitted, the payload is printed as a key/value list. |
| `data` | Object, list of `{label, value}` pairs, or a bare string. |
| `copies` | 1–20. |
| `inline` | A whole template supplied in the request, instead of `template`. |

Returns `{"ok": true, "copies": n, "bytes": n, "height": n}`.

### `POST /api/print/{template}`

The whole request body is the payload. Convenient for devices that can only
POST a fixed document:

```bash
curl -X POST http://recifi.local/api/print/asset-tag \
  -H 'content-type: application/json' \
  -d '{"id":"A-1042","owner":"lab 3"}'
```

Takes `?copies=n`.

### `POST /api/preview`

Same body as `/api/print`, but returns a **1-bit BMP** of the label instead of
printing it. BMP because it needs no compression — the pixel data is the
framebuffer as-is, where PNG would mean zlib and CRC32 on a microcontroller.

### Errors

| Status | Meaning |
|---|---|
| `400` | Bad JSON, or the label could not be rendered |
| `404` | No such template |
| `503` | The printer is unreachable — the request was fine, the hardware was not |

A `503` also comes back if a label is too large to render in the memory
available, with a message saying so.

## Status

### `GET /api/status`

Printer state, WiFi state, the full config, counters, the last 25 events, and
`mem_free`. This is what the UI polls.

The WiFi password is **never** included — it is replaced with asterisks.

### `GET /healthz`

`200` when the printer is ready, `503` otherwise. Suitable for an uptime
monitor.

```json
{"ok": true, "state": "ready", "detail": "BLE 50:0b:66:1b:e1:d5@UUID(0x2af1)"}
```

## Templates

| | |
|---|---|
| `GET /api/templates` | All templates, built-ins flagged with `"builtin": true` |
| `GET /api/templates/{id}` | One template |
| `POST /api/templates` | Create; the id is generated from the name |
| `PUT /api/templates/{id}` | Create or replace at a known id |
| `DELETE /api/templates/{id}` | Delete. Deleting a shadowed built-in restores the original. |

## Logos

| | |
|---|---|
| `GET /api/logos` | `{"logos": [{name, width, height}], "max_side": 600, "max_logos": 16}` |
| `POST /api/logos/{name}?w=W&h=H` | Body: raw 1-bit pixels, rows packed MSB first, each row padded to a whole byte, `1` = black (exactly `ceil(W/8)*H` bytes). Stored as `logos/{name}.pbm`. |
| `GET /api/logos/{name}` | The logo as a 1-bit BMP |
| `DELETE /api/logos/{name}` | Delete it |

## Connection

| | |
|---|---|
| `GET /api/discover?transport=ble` | Scan. Omit `transport` to scan everything. |
| `POST /api/connect` | `{transport, address, protocol}` |
| `POST /api/disconnect` | |
| `POST /api/config` | Patch any subset of the settings |

`POST /api/config` deliberately **ignores** a `wifi` key — network changes go
through the route below so the board can reconnect and report the result.

## WiFi

| | |
|---|---|
| `GET /api/wifi/scan` | Networks in range, strongest first |
| `POST /api/wifi` | `{ssid, password, hostname}` — joins immediately |

A password that comes back from the UI redacted is ignored, so saving the form
without retyping it does not wipe the stored one.

## Debugging

### `GET /api/debug/calibrate`

Prints a geometry test pattern: border, a block in each corner, a tick every 20
dots, numbered every 100. Add `?preview=1` to get it as a BMP without printing.

### `POST /api/debug/calibrate-media`

Runs the printer's own media calibration (TSPL `GAPDETECT`). It feeds a couple
of labels while measuring. Worth doing whenever the media changes.

### `POST /api/debug/encode`

Shows the bytes a print *would* send — block count, total size, and the first 64
bytes as hex. Use it to diff against a capture from a vendor app.

### `POST /api/debug/raw`

```json
{"hex": "1b 40", "text": "optional", "read_reply": false}
```

Sends arbitrary bytes to the printer untouched, and optionally waits for a
reply. This is how an unknown protocol gets worked out.
