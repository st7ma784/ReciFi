"""Test runner for the MicroPython unix port -- there is no pytest here.

    docker run --rm -v "$PWD:/app" -w /app/tests \
      -e MICROPYPATH=/app/device:/app/tests micropython/unix:latest micropython run.py

Collects every test_* function in each module, runs it in a scratch directory
(the store and config modules write real files), and reports.
"""

import gc
import os
import sys

MODULES = ("test_qr", "test_render", "test_store", "test_logos", "test_server", "test_api")

SCRATCH = "/tmp/recifi-test"


def _prepare():
    try:
        os.mkdir(SCRATCH)
    except OSError:
        pass
    os.chdir(SCRATCH)
    for name in os.listdir("."):
        try:
            os.remove(name)
        except OSError:
            pass


def main():
    _prepare()
    passed = []
    failed = []

    for module_name in MODULES:
        try:
            module = __import__(module_name)
        except Exception as exc:
            failed.append(("%s (import)" % module_name, exc))
            sys.print_exception(exc)
            continue

        for name in dir(module):
            if not name.startswith("test_"):
                continue
            fn = getattr(module, name)
            if not callable(fn):
                continue
            gc.collect()
            try:
                fn()
                passed.append(name)
            except Exception as exc:
                failed.append(("%s.%s" % (module_name, name), exc))
                print("\nFAIL %s.%s" % (module_name, name))
                sys.print_exception(exc)

    print("\n%d passed, %d failed" % (len(passed), len(failed)))
    for name, exc in failed:
        print("  FAILED %s: %r" % (name, exc))
    sys.exit(1 if failed else 0)


main()
