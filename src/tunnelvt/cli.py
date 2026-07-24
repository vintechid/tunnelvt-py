"""CLI entry point for ``tunnelvt``."""

from __future__ import annotations

import argparse
import logging
import sys

from .client import TunnelVT


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="tunnelvt",
        description="Expose a local port through gotunnel.",
    )
    p.add_argument("-t", "--token", default="", help="auth token")
    p.add_argument("-a", "--app", required=True, help="app name for this tunnel")
    p.add_argument("-p", "--port", type=int, required=True, help="local port to expose")
    p.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[tunnelvt] %(levelname)s %(message)s",
    )

    client = TunnelVT(token=args.token, app=args.app, port=args.port)
    try:
        client.connect()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        sys.exit(0)
    except Exception as exc:
        logging.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
