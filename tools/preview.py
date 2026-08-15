"""Render sample labels to .bmp without a printer or a board.

Runs under the MicroPython unix port, using the same renderer and the same BMP
writer the device serves from /api/preview -- so what you look at here is
exactly what the Pico would produce.

    docker run --rm -v "%CD%:/app" -w /app/device \
      -e MICROPYPATH=".frozen:/app/device" micropython/unix:latest \
      micropython /app/tools/preview.py /out
"""

import sys
import time

from recifi import store
from recifi.api import bmp_stream
from recifi.render import render

CFG = {"width_dots": 384, "qr_level": "M", "qr_mask": None, "max_height": 800}

JOBS = (
    ("asset-tag", {"name": "Spectrometer", "id": "A-1042", "owner": "sam",
                   "location": "Lab 3, bench 2", "footer": "property of ReciFi"}),
    ("auto-kv", {"title": "Batch record", "Batch": "B-77", "Baked": "2026-08-15",
                 "Operator": "sam",
                 "Notes": "second proof, a long line that should wrap neatly"}),
    ("qr-only", {"url": "https://example.com/a/1042", "caption": "asset 1042"}),
    ("asset-tag", {"name": "Partial payload", "id": "B-7"}),
)


def main(outdir):
    for index, (template_id, data) in enumerate(JOBS):
        tpl = store.get(template_id)
        start = time.ticks_ms()
        label = render(tpl, data, CFG)
        ms = time.ticks_diff(time.ticks_ms(), start)
        path = "%s/label%d-%s.bmp" % (outdir, index + 1, template_id)
        with open(path, "wb") as handle:
            for chunk in bmp_stream(label).stream:
                handle.write(chunk)
        print("%-10s %dx%d  %d ms  -> %s" % (template_id, label.width, label.height, ms, path))


main(sys.argv[1] if len(sys.argv) > 1 else ".")
