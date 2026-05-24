from __future__ import annotations

import argparse

import scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Market graph consistency diagnostics CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run-fixtures", help="Run bundled fixture diagnostics only.")
    diff_parser = subparsers.add_parser("diff-relative-value-hints", help="Compare two saved relative-value hint reports.")
    diff_parser.add_argument("--old", required=True)
    diff_parser.add_argument("--new", required=True)
    diff_parser.add_argument("--json-output", required=True)
    diff_parser.add_argument("--markdown-output", required=True)
    args = parser.parse_args(argv)

    if args.command == "run-fixtures":
        return scan.main([])
    if args.command == "diff-relative-value-hints":
        return scan.main(
            [
                "diff-relative-value-hints",
                "--old",
                args.old,
                "--new",
                args.new,
                "--json-output",
                args.json_output,
                "--markdown-output",
                args.markdown_output,
            ]
        )
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
