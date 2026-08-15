"""QR encoder behaviour that must hold on-device.

Byte-exactness against the reference implementation is checked separately under
CPython (tools/qrcheck.py) -- that comparison needs the `qrcode` library, which
does not exist here. These tests cover the properties we can assert alone.
"""

from recifi import qr


def _finder_ok(m, ox, oy):
    for dy in range(7):
        for dx in range(7):
            ring = dx in (0, 6) or dy in (0, 6) or (2 <= dx <= 4 and 2 <= dy <= 4)
            if m.get(ox + dx, oy + dy) != (1 if ring else 0):
                return False
    return True


def test_version_scales_with_payload():
    assert qr.encode("A").version == 1
    assert qr.encode("x" * 100).version > 1
    # v10-L is the ceiling we support.
    assert qr.encode("x" * 260, qr.L).version == 10


def test_too_long_is_rejected():
    try:
        qr.encode("x" * 400)
    except ValueError:
        return
    raise AssertionError("expected ValueError for an oversized payload")


def test_size_matches_version():
    for text in ("A", "hello world", "x" * 60):
        m = qr.encode(text)
        assert m.size == m.version * 4 + 17


def test_finder_patterns_in_all_three_corners():
    m = qr.encode("https://example.com")
    assert _finder_ok(m, 0, 0)
    assert _finder_ok(m, m.size - 7, 0)
    assert _finder_ok(m, 0, m.size - 7)


def test_timing_patterns_alternate():
    m = qr.encode("hello")
    for i in range(8, m.size - 8):
        assert m.get(i, 6) == (1 - (i & 1))
        assert m.get(6, i) == (1 - (i & 1))


def test_dark_module_is_set():
    m = qr.encode("hello")
    assert m.get(8, m.size - 8) == 1


def test_every_mask_produces_a_valid_symbol():
    for mask in range(8):
        m = qr.encode("A-1042", qr.M, mask=mask)
        assert _finder_ok(m, 0, 0)
        assert m.get(8, m.size - 8) == 1


def test_auto_mask_beats_the_worst_fixed_mask():
    from recifi.qr import _penalty

    auto = qr.encode("https://example.com/a/1042")
    scores = []
    for mask in range(8):
        qr._cache_key = None
        scores.append(_penalty(qr.encode("https://example.com/a/1042", qr.M, mask=mask).modules,
                               auto.size))
    qr._cache_key = None
    assert _penalty(auto.modules, auto.size) <= min(scores)


def test_ec_levels_change_the_symbol():
    seen = set()
    for level in (qr.L, qr.M, qr.Q, qr.H):
        m = qr.encode("hello world", level)
        seen.add(bytes(m.modules))
    assert len(seen) == 4


def test_cache_returns_the_same_object():
    qr._cache_key = None
    first = qr.encode("cache me")
    assert qr.encode("cache me") is first
    assert qr.encode("something else") is not first
