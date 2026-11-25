#!/usr/bin/env python3
"""Interrupt only the Grafana archive recorder and wait for its exit."""

import argparse
from fcntl import LOCK_EX, flock
import subprocess
import sys
from time import monotonic, sleep

from start import CONTROL_LOCK, RUNTIME, recorder_pane, tmux_command


def stop():
    with CONTROL_LOCK.open("a") as lock:
        flock(lock, LOCK_EX)
        pane = recorder_pane()
        if pane is None:
            return
        signal = subprocess.run(tmux_command() + ["send-keys", "-t", pane, "C-c"], capture_output=True, text=True)
        if signal.returncode and recorder_pane() is not None:
            raise RuntimeError(signal.stderr.strip())
        deadline = monotonic() + 10
        while recorder_pane() is not None:
            if monotonic() >= deadline:
                raise RuntimeError("The recorder did not exit after the interrupt")
            sleep(0.2)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        if RUNTIME.is_dir():
            stop()
        print("Recording: stopped")
    except (OSError, RuntimeError) as error:
        print(f"Could not stop recording: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
