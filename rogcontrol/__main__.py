"""Dispatch headless commands before importing a desktop toolkit."""

import sys

def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--hardware-report" in args:
        from .cli import main as cli_main
        return cli_main(["report"])
    if args and args[0] in ("profile", "keyboard", "report", "decky", "--help", "-h"):
        from .cli import main as cli_main
        return cli_main(args)
    from .qt.application import main as qt_main
    return qt_main([arg for arg in args if arg != "--qt"])

if __name__ == "__main__":
    sys.exit(main())
