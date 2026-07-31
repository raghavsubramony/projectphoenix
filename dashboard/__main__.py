"""Entry point: ``python -m dashboard``."""

from __future__ import annotations

import argparse

from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Project Phoenix dashboard")
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="port to listen on (default 8000)")
    parser.add_argument("--no-open", action="store_true",
                        help="do not open a browser automatically")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow binding non-loopback hosts (exposes unauthenticated APIs)",
    )
    args = parser.parse_args()
    serve(
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        allow_remote=args.allow_remote,
    )


if __name__ == "__main__":
    main()
