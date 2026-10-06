"""Entry point of the packaged application.

Without arguments (e.g. started from Finder) it opens the GUI. With a command it works as
the command line tool, so ``ydiskarc.app/Contents/MacOS/ydiskarc sync URL -o DIR`` works too.
"""

import sys

CLI_COMMANDS = {"sync", "full", "version", "gui", "--help", "-h"}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    if args and args[0] in CLI_COMMANDS:
        from ydiskarc.__main__ import main as cli_main

        sys.argv = [sys.argv[0]] + args
        cli_main()
    else:
        from ydiskarc.gui import main as gui_main

        gui_main()


if __name__ == "__main__":
    main()
