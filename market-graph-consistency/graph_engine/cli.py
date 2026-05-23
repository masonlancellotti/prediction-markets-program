from __future__ import annotations

import argparse

import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Market graph consistency diagnostics CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-fixtures", help="Run bundled fixture diagnostics only.")
    args = parser.parse_args(argv)

    if args.command == "run-fixtures":
        return scan.main([])
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
