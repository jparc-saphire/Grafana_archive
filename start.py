#!/usr/bin/env python3
"""Start the Grafana archive recorder in its dedicated tmux session."""

import argparse
from fcntl import LOCK_EX, flock
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from time import sleep

from archive import DATA_DIR


SOURCE = Path(__file__).resolve().parent
OVERRIDE = os.environ.get("GRAFANA_ARCHIVE_ROOT")
BASE = Path(OVERRIDE).resolve() if OVERRIDE else Path.home() / "slowdash"
DATA = BASE / "data" if OVERRIDE else DATA_DIR
RUNTIME = BASE / "grafana_archive"
SOCKET = RUNTIME / "tmux.sock"
LOG = RUNTIME / "recording.log"
CONTROL_LOCK = RUNTIME / ".control.lock"
RECORDER = RUNTIME / "archive.py"
SESSION = "grafana-archive"


def tmux_command():
    local = RUNTIME / "bin" / "tmux"
    binary = str(local) if local.is_file() else shutil.which("tmux")
    return [binary, "-S", str(SOCKET)] if binary else []


def recorder_pane():
    command = tmux_command()
    if not command or not SOCKET.exists():
        return None
    panes = subprocess.run(command + ["list-panes", "-a", "-F", "#{pane_id}|#{pane_dead}|#{pane_start_command}"],
                           capture_output=True, text=True)
    for line in panes.stdout.splitlines():
        pane, dead, launched = line.split("|", 2)
        if dead == "0" and str(RECORDER) in launched:
            return pane
    return None


def attach_command():
    return shlex.join(tmux_command() + ["attach-session", "-t", SESSION])


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    os.umask(0o022)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        if not os.access(DATA, os.W_OK | os.X_OK):
            raise PermissionError(f"No write access to {DATA}")
        RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
        with CONTROL_LOCK.open("a") as lock:
            flock(lock, LOCK_EX)
            if recorder_pane():
                print("Recording is already running.")
            else:
                subprocess.run(["/usr/bin/bash", str(SOURCE / "install.sh"), str(RUNTIME)], check=True)
                command = tmux_command()
                if not command:
                    raise RuntimeError("tmux is unavailable")
                python = RUNTIME / ".venv" / "bin" / "python"
                subprocess.run(command + ["-f", "/dev/null", "new-session", "-d", "-s", SESSION,
                                          "-c", str(RUNTIME), str(python), "-B", str(RECORDER), "--live",
                                          "--output-dir", str(DATA), "--log-file", str(LOG)], check=True)
                sleep(0.4)
                if not recorder_pane():
                    raise RuntimeError(f"The recorder exited during startup. See {LOG}")
                print(f"Recording started. Output directory: {DATA}")
            print(f"Attach: {attach_command()}")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Could not start recording: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
