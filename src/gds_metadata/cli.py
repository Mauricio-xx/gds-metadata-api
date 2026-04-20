"""CLI entry point: run the API server or extract metadata directly."""

from __future__ import annotations

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="gds-metadata-api",
        description="GDSII metadata extraction - API server or CLI mode",
    )
    sub = parser.add_subparsers(dest="command")

    # Server mode
    srv = sub.add_parser("serve", help="Start the API server")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8042)

    # CLI extraction mode
    ext = sub.add_parser("extract", help="Extract metadata from a GDS file")
    ext.add_argument("source", help="Local path or GitHub URL")
    ext.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    ext.add_argument("--max-text-labels", type=int, default=10_000)
    ext.add_argument("--max-properties", type=int, default=5_000)

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        from .api import app
        uvicorn.run(app, host=args.host, port=args.port)

    elif args.command == "extract":
        from .parser import parse_gds_metadata
        from .sources import SourceError, resolve_source

        try:
            path, is_temp = resolve_source(args.source)
        except SourceError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        import os
        try:
            meta = parse_gds_metadata(
                path,
                max_text_labels=args.max_text_labels,
                max_properties=args.max_properties,
            )
            indent = 2 if args.pretty else None
            print(meta.model_dump_json(indent=indent))
        finally:
            if is_temp:
                os.unlink(path)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
