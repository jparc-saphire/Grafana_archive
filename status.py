#!/usr/bin/env python3
"""Show the recorder's tmux status, latest file and recent log messages."""

import argparse
from collections import deque
import sys

from start import DATA, DEVICE_DATA, DEVICE_LOG, LOG, attach_command, recording_panes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus", action="store_true", help="emit the recorder status as a gauge")
    options = parser.parse_args()
    try:
        panes = recording_panes()
        running = "root" in panes
        if options.prometheus:
            print("# HELP e88_grafana_archive_recording Whether the Grafana ROOT archive recorder is running.")
            print("# TYPE e88_grafana_archive_recording gauge")
            print(f"e88_grafana_archive_recording {int(running)}")
            return 0
        print("Recording: running" if running else "Recording: stopped")
        print("Device CSV: running" if "device" in panes else "Device CSV: stopped")
        files = sorted(DATA.glob("Grafana_????????.root"))
        if files:
            print(f"Latest file: {files[-1]}")
        csv_files = sorted(DEVICE_DATA.glob("Grafana_device_????????.csv"))
        if csv_files:
            print(f"Latest device CSV: {csv_files[-1]}")
        for name, log in (("ROOT", LOG), ("Device CSV", DEVICE_LOG)):
            if log.is_file():
                print(f"{name} log")
                with log.open(encoding="utf-8") as source:
                    for line in deque(source, maxlen=5):
                        print(line, end="")
        if panes:
            print(f"Attach: {attach_command()}")
    except OSError as error:
        print(f"Could not read recorder status: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
