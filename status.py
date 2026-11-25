#!/usr/bin/env python3
"""Show the recorder's tmux status, latest file and recent log messages."""

import argparse
from collections import deque
import sys

from start import DATA, LOG, attach_command, recorder_pane


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        running = recorder_pane() is not None
        print("Recording: running" if running else "Recording: stopped")
        files = sorted(DATA.glob("Grafana_????????.root"))
        if files:
            print(f"Latest file: {files[-1]}")
        if LOG.is_file():
            with LOG.open(encoding="utf-8") as source:
                for line in deque(source, maxlen=5):
                    print(line, end="")
        if running:
            print(f"Attach: {attach_command()}")
    except OSError as error:
        print(f"Could not read recorder status: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
