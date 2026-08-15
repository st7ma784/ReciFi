"""Cross-check the on-device QR encoder against the reference implementation.

Runs under CPython (not MicroPython) because it needs the `qrcode` library and
OpenCV. This is the test that actually proves the encoder correct: every
version x EC level x mask is compared byte-for-byte, and the auto-masked output
is put through a real decoder.

    docker run --rm -v "%CD%:/app" -w /app python:3.12-slim sh -lc \
      "pip install -q qrcode opencv-python-headless numpy && python tools/qrcheck.py"
"""

import random
import sys

sys.path.insert(0, "device")

import qrcode
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_L, ERROR_CORRECT_M, ERROR_CORRECT_Q

from recifi import qr

REF = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M, "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}


def ref_matrix(text, version, level, mask):
    q = qrcode.QRCode(version=version, error_correction=REF[level], border=0, mask_pattern=mask)
    q.add_data(text)
    q.make(fit=False)
    return [[1 if c else 0 for c in row] for row in q.modules]


def mine(text, version, level, mask):
    m = qr.encode(text, qr.LEVELS[level], min_version=version, mask=mask)
    return [[m.get(x, y) for x in range(m.size)] for y in range(m.size)]


def compare():
    random.seed(7)
    checked = fails = 0
    for version in range(1, qr.MAX_VERSION + 1):
        for level in ("L", "M", "Q", "H"):
            limit = qr._capacity(version, qr.LEVELS[level]) - (2 if version < 10 else 3)
            if limit < 1:
                continue
            payloads = [
                "https://example.com/a/1042"[:limit],
                "".join(chr(random.randint(33, 126)) for _ in range(limit)),
            ]
            for text in payloads:
                if not text:
                    continue
                for mask in range(8):
                    checked += 1
                    if mine(text, version, level, mask) != ref_matrix(text, version, level, mask):
                        fails += 1
                        if fails <= 3:
                            print("  MISMATCH v%d-%s mask%d len=%d" % (version, level, mask, len(text)))
    print("matrix comparison: %d/%d identical" % (checked - fails, checked))
    return fails


def decode_check():
    import cv2
    import numpy as np

    detector = cv2.QRCodeDetector()
    samples = [
        "https://example.com/a/1042", "A-1042", "hello world", "x" * 100,
        "https://recifi.local/label/00000001?token=abcdef0123456789",
    ]
    ok = 0
    for text in samples:
        m = qr.encode(text, qr.M)
        scale, border = 8, 4
        side = (m.size + border * 2) * scale
        img = np.full((side, side), 255, np.uint8)
        for y in range(m.size):
            for x in range(m.size):
                if m.get(x, y):
                    py, px = (y + border) * scale, (x + border) * scale
                    img[py:py + scale, px:px + scale] = 0
        data, _, _ = detector.detectAndDecode(img)
        good = data == text
        ok += good
        print("  decode v%-2d %s %r" % (m.version, "OK  " if good else "FAIL", text[:40]))
    print("opencv decode: %d/%d" % (ok, len(samples)))
    return len(samples) - ok


if __name__ == "__main__":
    failures = compare() + decode_check()
    sys.exit(1 if failures else 0)
