# SPDX-License-Identifier: MIT
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["export"]:
        from .export import main as export_main
        return export_main(args[1:])
    if args[:1] == ["trace"]:
        from .trace import main as trace_main
        return trace_main(args[1:])
    from .cli import main as validate_main
    return validate_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
