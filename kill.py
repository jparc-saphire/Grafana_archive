#!/usr/bin/env python3
"""Stop both Grafana archive recorders and wait for them to exit."""

import argparse
from fcntl import LOCK_EX, flock
import subprocess
import sys
from time import monotonic, sleep

from start import CONTROL_LOCK, RUNTIME, recording_panes, tmux_command


def stop():
    with CONTROL_LOCK.open("a") as lock:
        flock(lock, LOCK_EX)
        for pane in recording_panes().values():
            signal = subprocess.run(tmux_command() + ["send-keys", "-t", pane, "C-c"], capture_output=True, text=True)
            if signal.returncode and pane in recording_panes().values():
                raise RuntimeError(signal.stderr.strip())
        deadline = monotonic() + 12
        while recording_panes():
            if monotonic() >= deadline:
                raise RuntimeError("A recorder did not exit after the interrupt")
            sleep(0.2)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        if RUNTIME.is_dir():
            stop()
        print("Recording: stopped")
        print("Device CSV: stopped")
    except (OSError, RuntimeError) as error:
        print(f"Could not stop recording: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
