---
title: Templates
layout: default
nav_order: 3
---

# Templates
{: .no_toc }

How a JSON payload becomes a label.
{: .fs-5 .fw-300 }

1. TOC
{:toc}

---

## The idea

A template is a title, a list of key/value fields, an optional QR code and a
footer. Any text in it may contain placeholders, which are filled from the
payload you POST.

{% raw %}
```json
{
  "name": "Asset tag",
  "title": "{{name}}",
  "fields": [
    {"label": "ID", "value": "{{id}}", "emphasis": true},
    {"label": "Owner", "value": "{{owner}}"}
  ],
  "qr": {"value": "https://assets.example.com/a/{{id}}", "position": "right"},
  "footer": "property of the lab"
}
```
{% endraw %}

Print through it by name:

```bash
curl -X POST http://recifi.local/api/print/asset-tag \
  -H 'content-type: application/json' \
  -d '{"name":"Spectrometer","id":"A-1042","owner":"sam"}'
```

## Payload shapes

`data` accepts three shapes:

| Shape | Example | Behaviour |
|---|---|---|
| Object | `{"id": "A-1042"}` | Keys fill placeholders of the same name |
| List of pairs | `[{"label":"ID","value":"A-1042"}]` | Becomes rows **in the order given** |
| Bare string | `"FRAGILE"` | Available as `value` and `text`; also used as the QR payload if the template has one |

Nested objects are flattened with dotted keys, so a payload of
`{"addr": {"city": "Lancaster"}}` fills {% raw %}`{{addr.city}}`{% endraw %}.

Placeholder matching is case-insensitive.

## Missing values drop their row

A field whose placeholders all resolve to nothing is **left out entirely**,
rather than printing a bare label with no value. One template can therefore
cover payloads that only sometimes carry a field:

{% raw %}
```json
{"fields": [{"label": "Owner", "value": "{{owner}}"}]}
```
{% endraw %}

Sending `{"owner": "sam"}` prints `Owner  sam`. Sending `{}` prints nothing for
that row. A field with a literal value and no placeholders always prints.

## auto_fields

Set `auto_fields` and every payload key that no field already consumed is
appended as a row. Handy for logging arbitrary data with no template work at
all — the built-in **auto-kv** does exactly this.

Those rows come out **alphabetically**, not in payload order, because
MicroPython dictionaries do not preserve insertion order. If the order matters,
send a list of `{label, value}` pairs or declare the fields explicitly.

## QR codes

```json
{"qr": {"value": "...", "position": "right", "scale": 5, "caption": "..."}}
```

| Field | |
|---|---|
| `value` | The payload. Placeholders work here too. |
| `position` | `top`, `bottom`, `left` or `right`. Side positions share vertical space with the text; stacked ones add to it. |
| `scale` | Module size in dots. Shrunk automatically if it will not fit. |
| `caption` | Small text under the code. |

### Make it do something when scanned

A QR holding a bare string like `A-1042` scans perfectly, but the phone has
nothing to *do* with it — Google Lens will offer "Search Barcode" rather than
opening anything. Native actions need a recognised URI scheme:

{% raw %}
```json
{"qr": {"value": "https://assets.example.com/a/{{id}}"}}
```
{% endraw %}

`https://`, `tel:`, `mailto:`, `geo:` and `WIFI:SSID:name;T:WPA;P:pass;;` are all
handled natively. This is a template change, not a setting — the encoder does
not care what the bytes mean.

### Size it so it scans

At 203 dpi one dot is 0.125 mm, so `scale` 4 gives 0.5 mm modules. Below about
that, phone cameras struggle once thermal bleed has thickened each module.
`qr_min_scale` (default 4) stops a template asking for something unreadable.

Bigger is always better if the label can afford it. A short payload at scale 6
(0.75 mm modules) is comfortably scannable across a room.

## Logos

Upload a black-and-white logo (up to 600×600 px) on the dashboard's **Logos**
tab, then reference it by name:

```json
{"logo": {"name": "acme", "position": "left", "size": 96}}
```

| Field | |
|---|---|
| `name` | The uploaded logo's name. Placeholders work, so one template can print a different logo per payload; an unknown name just prints without one. |
| `position` | `top` (centred above everything), `left` or `right` (a column beside the text, centred vertically). If a side QR already holds that side, the logo goes on top. |
| `size` | Longest side in dots; `0` keeps the uploaded size. Logos are only ever shrunk, and always to fit the label. |

The browser turns the image into 1-bit pixels before upload: transparent areas
become paper, and there's a threshold slider, an invert option and a dither
option for photos. Shrinking happens on the Pico with nearest-neighbour
scaling, which looks rougher than the browser's resize. For the cleanest
result, upload the logo at the size you will print it.

## Layout

| Field | Default | |
|---|---|---|
| `title_scale` | 3 | Title size |
| `text_scale` | 2 | Body size |
| `margin` | 8 | Blank dots around the content |
| `line_gap` | 4 | Dots between lines |
| `align` | `left` | `left` or `center` |
| `min_height` | 0 | Pad the label to at least this many dots |

Text is the built-in 8×8 font at whole-number scales, 1 to 4, so sizes are
8/16/24/32 dots tall. That is the only font MicroPython provides, and scaling it
keeps the flash free for templates instead of glyph tables.

Two conveniences fall out of the layout pass: rows share a common value column
so they line up, and a title that would wrap mid-word shrinks a step first.

## Built-in templates

| Name | |
|---|---|
| `auto-kv` | Every payload key as a row. No setup at all. |
| `qr-only` | A QR with a caption. |
| `asset-tag` | Title, QR on the right, and id/owner/location rows. |

Editing a built-in saves a copy that shadows it. Deleting your copy brings the
original back, so they cannot be lost.
