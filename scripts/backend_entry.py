from __future__ import annotations

import sys

from aisc2commander.cli import main


if __name__ == "__main__":
    raise SystemExit(main(["run", *sys.argv[1:]]))
