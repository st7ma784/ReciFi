# Contributing

Thanks for looking. This is a small project with a specific shape, so a bit of
context will save you time.

## Reporting a printer

The most useful contribution is support for another printer. To add one, three
things are needed:

1. **A GATT dump.** Edit the address in `tools/snippets/gatt.py` and run:

   ```powershell
   .\tools\repl.ps1 -File tools\snippets\gatt.py -TimeoutSec 90
   ```

2. **The command language.** Check for a vendor driver first — ORGBRO's ChromeOS
   driver is a Chrome extension with the entire protocol in readable JavaScript,
   which saved a day of guessing. Windows and Android drivers are often as
   revealing. Failing that, `tools/snippets/probe.py` sends probes down every
   writable characteristic while listening for replies.

3. **Media details** — dpi, head width, label sizes, feed direction.

Open an issue with those and it is usually a short job from there.

## Development setup

You need Docker. You do not need Python, or a Pico, to work on most of it.

```powershell
.\tools\test.ps1                 # the suite, under real MicroPython
.\tools\test.ps1 -Qr -Preview    # + QR cross-check (needs CPython in Docker), + sample labels
```

`-Preview` writes `.bmp` files to `preview/` using the same renderer and BMP
writer the device serves, so you can see label changes without hardware.

For hardware work, the `null` transport spools to flash — the whole stack runs
with no printer attached.

## House rules

- **Tests run under MicroPython, not CPython.** If you add a test, make sure it
  passes under `micropython/unix`. CPython would hide the bugs worth catching:
  no `int.bit_length()`, dictionaries with no insertion order, and so on.
- **Watch allocations.** The board has ~400 KB of heap and a label can be tens
  of KB. Protocol encoders are generators for this reason — do not accumulate a
  whole command stream in a buffer.
- **Do not break the framebuffer contract.** `MONO_HLSB`, 1 = burn. It is the
  wire format for most raster protocols, and the reason the print path does not
  copy.
- **Bound anything that reads from the network.** Header lengths, header counts,
  body sizes. A microcontroller cannot afford an unbounded allocation.
- **Match the surrounding style.** Comments explain *why*, not *what*.

## Adding a protocol

Write a generator in `device/recifi/printers/protocols.py` taking
`(label, cfg)` and yielding byte blocks, register it in `ENCODERS`, and add a
line to `DESCRIPTIONS`. It appears in the UI automatically. Add tests pinning
the byte sequence — see the TSPL ones in `tests/test_api.py` for the pattern.

## Docs

The site under `docs/` is Jekyll with the just-the-docs theme. It is built and
deployed by `.github/workflows/pages.yml` on any push to `main` that touches
`docs/`, so the theme is pinned in `docs/Gemfile` rather than fetched remotely.
To preview locally:

```bash
cd docs && bundle install && bundle exec jekyll serve
```

Watch out for one hazard: ReciFi's own template syntax uses double-brace
placeholders, and Liquid will evaluate those and render them blank — even inside
a fenced code block. Any such example in `docs/` must be wrapped in Liquid `raw`
tags. There is a check for this in the test tooling.

## Pull requests

Run the tests, keep commits focused, and describe what you observed on hardware
if the change touches a printer. "Prints correctly on <model>" is worth more
than any amount of review.
