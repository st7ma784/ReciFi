"""Entry point: python -m recifi [--host 0.0.0.0] [--port 8080]"""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="recifi", description="Label print server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true", help="dev autoreload")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    uvicorn.run(
        "recifi.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        # A Zero 2 W has four small cores; one worker is plenty and the printer
        # connection must not be duplicated across processes anyway.
        workers=1,
    )


if __name__ == "__main__":
    main()
