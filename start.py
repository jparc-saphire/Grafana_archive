#!/usr/bin/env python3
"""Start the ROOT and device CSV recorders in a dedicated tmux session."""

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
DEVICE_DATA = BASE / "device_log" if OVERRIDE else SOURCE / "device_log"
RUNTIME = BASE / "grafana_archive"
SOCKET = RUNTIME / "tmux.sock"
LOG = RUNTIME / "recording.log"
DEVICE_LOG = RUNTIME / "device-recording.log"
CONTROL_LOCK = RUNTIME / ".control.lock"
RECORDER = RUNTIME / "archive.py"
DEVICE_RECORDER = RUNTIME / "device_log.py"
SESSION = "grafana-archive"


def tmux_command():
    local = RUNTIME / "bin" / "tmux"
    binary = str(local) if local.is_file() else shutil.which("tmux")
    return [binary, "-S", str(SOCKET)] if binary else []


def recording_panes():
    command = tmux_command()
    if not command or not SOCKET.exists():
        return {}
    panes = subprocess.run(command + ["list-panes", "-a", "-F", "#{pane_id}|#{pane_dead}|#{pane_start_command}"],
                           capture_output=True, text=True)
    found = {}
    for line in panes.stdout.splitlines():
        pane, dead, launched = line.split("|", 2)
        if dead == "0":
            for kind, script in (("root", RECORDER), ("device", DEVICE_RECORDER)):
                if str(script) in launched:
                    found[kind] = pane
    return found


def attach_command():
    return shlex.join(tmux_command() + ["attach-session", "-t", SESSION])


def start_recorders(active):
    subprocess.run(["/usr/bin/bash", str(SOURCE / "install.sh"), str(RUNTIME)], check=True)
    command = tmux_command()
    if not command:
        raise RuntimeError("tmux is unavailable")
    python = RUNTIME / ".venv" / "bin" / "python"
    workers = {
        "root": [str(RECORDER), "--live", "--output-dir", str(DATA), "--log-file", str(LOG)],
        "device": [str(DEVICE_RECORDER), "--output-dir", str(DEVICE_DATA), "--log-file", str(DEVICE_LOG)],
    }
    target = next(iter(active.values()), None)
    started = []
    try:
        for kind, args in workers.items():
            if kind in active:
                continue
            if target is None:
                launch = ["-f", "/dev/null", "new-session", "-d", "-s", SESSION]
            else:
                launch = ["split-window", "-h", "-d", "-t", target]
            worker = subprocess.run(command + launch + ["-P", "-F", "#{pane_id}", "-c", str(RUNTIME),
                                                         str(python), "-B", *args],
                                    check=True, capture_output=True, text=True)
            target = worker.stdout.strip()
            started.append(target)
        sleep(0.4)
        if recording_panes().keys() != workers.keys():
            raise RuntimeError(f"A recorder exited during startup. See {LOG} and {DEVICE_LOG}")
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        for pane in started:
            subprocess.run(command + ["send-keys", "-t", pane, "C-c"], capture_output=True)
        raise


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    os.umask(0o022)
    try:
        for directory in (DATA, DEVICE_DATA):
            directory.mkdir(parents=True, exist_ok=True)
            if not os.access(directory, os.W_OK | os.X_OK):
                raise PermissionError(f"No write access to {directory}")
        RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
        with CONTROL_LOCK.open("a") as lock:
            flock(lock, LOCK_EX)
            panes = recording_panes()
            if panes.keys() == {"root", "device"}:
                print("ROOT and device CSV recording are already running.")
            else:
                start_recorders(panes)
                print(f"ROOT recording: {DATA}")
                print(f"Device CSV recording: {DEVICE_DATA}")
            print(f"Attach: {attach_command()}")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Could not start recording: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
