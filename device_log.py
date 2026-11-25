#!/usr/bin/env python3
"""Record E88 Operations audit events as one daily CSV row per device operation."""

import argparse
from collections import defaultdict
import csv
from datetime import datetime
from fcntl import LOCK_EX, LOCK_NB, flock
import json
import logging
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from time import sleep, time
from urllib.request import ProxyHandler, build_opener

from archive import DATA_DIR, JST


OUTPUT_DIR = DATA_DIR.parent / "device_log"
OPERATIONS_API = "http://127.0.0.1:18011/api/state"
VM = "e88daq@e88vm-dash01.e88.internal"
SSH = ["/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=4", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]
POLL_SECONDS = 2
AUDIT_LIMIT = 100
PENDING = {"intent", "accepted"}
FIELDS = ("unix_time", "time_jst", "device", "action", "value", "result", "confirmed",
          "result_time_jst", "revision", "error")


def read_audit(socket, url):
    if url:
        with build_opener(ProxyHandler({})).open(url, timeout=4) as response:
            payload = json.load(response)
    else:
        request = shlex.join(["/usr/bin/curl", "--noproxy", "*", "--max-time", "4", "-fsS", OPERATIONS_API])
        result = subprocess.run(SSH + ["-S", str(socket), "-o", "ControlMaster=auto", "-o", "ControlPersist=10",
                                       VM, request], stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=8)
        if result.returncode:
            raise OSError(result.stderr.strip() or "Operations SSH request failed")
        payload = json.loads(result.stdout)
    audit = payload["audit"]
    if not isinstance(audit, list):
        raise ValueError("Operations API returned an invalid audit list")
    return audit


def local_time(timestamp):
    return datetime.fromtimestamp(timestamp, JST).isoformat(timespec="milliseconds")


def csv_path(directory, timestamp):
    day = datetime.fromtimestamp(timestamp, JST).strftime("%Y%m%d")
    return directory / f"Grafana_device_{day}.csv"


def row_key(row):
    return row["unix_time"], row["device"], row["action"], row["revision"]


def update_csv(path, changes):
    rows = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != list(FIELDS):
                raise ValueError(f"Unexpected CSV columns in {path}")
            rows.update((row_key(row), row) for row in reader)
    added = len(changes.keys() - rows.keys())
    rows.update(changes)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                                     prefix=f".{path.stem}.", delete=False) as output:
        temporary = Path(output.name)
        try:
            writer = csv.DictWriter(output, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(sorted(rows.values(), key=lambda row: float(row["unix_time"])))
            output.flush()
            os.fsync(output.fileno())
            temporary.chmod(0o600 | (path.parent.stat().st_mode & 0o044))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return added


class OperationLog:
    def __init__(self, directory, started):
        self.directory = directory
        self.started = started
        self.seen = set()
        self.previous = set()
        self.pending = {}
        self.changes = defaultdict(dict)

    def ingest(self, events, cutoff=None):
        keys = [(float(event["time"]), (event["device"], event["action"], str(event["revision"])), event["result"])
                for event in events]
        snapshot = set(keys)
        overlap = self.previous & snapshot
        if not snapshot or (self.previous and not overlap and len(snapshot) < AUDIT_LIMIT):
            self.pending.clear()
        for event, key in zip(events, keys):
            timestamp, identity, phase = key
            if key in self.seen or (cutoff is not None and timestamp > cutoff):
                continue
            operation = self.pending.get(identity) if phase != "intent" else None
            if operation is None:
                operation = dict(unix_time=str(event["time"]), time_jst=local_time(timestamp),
                                 device=identity[0], action=identity[1], revision=identity[2],
                                 value=json.dumps(event["value"], ensure_ascii=False, sort_keys=True))
            operation.update(result=phase, result_time_jst=local_time(timestamp),
                             confirmed=str(bool(event.get("confirmed"))).lower(), error=event.get("error", ""))
            if phase in PENDING:
                self.pending[identity] = operation
            else:
                self.pending.pop(identity, None)
            started = float(operation["unix_time"])
            if started >= self.started:
                self.changes[csv_path(self.directory, started)][row_key(operation)] = operation.copy()
            self.seen.add(key)

        if len(snapshot) >= AUDIT_LIMIT and not overlap:
            if self.previous or min(key[0] for key in snapshot) > self.started:
                logging.warning("Operations history reached its %d-event window. Older events may be missing.", AUDIT_LIMIT)
        self.previous = snapshot

    def flush(self, now):
        today = csv_path(self.directory, now)
        if not today.exists():
            self.changes[today]
        for path, changes in list(self.changes.items()):
            added = update_csv(path, changes)
            del self.changes[path]
            if changes:
                logging.info("%s: %d new operation(s), %d updated", path.name, added, len(changes) - added)


def record(options, socket):
    history = OperationLog(options.output_dir, time())
    connected = None
    history.flush(time())
    logging.info("Device operation recording started.")
    try:
        while True:
            try:
                history.ingest(read_audit(socket, options.operations_url))
                history.flush(time())
            except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as error:
                if connected is not False:
                    logging.warning("Device audit unavailable. Retrying: %s", error)
                connected = False
            else:
                if not connected:
                    logging.info("Device audit connected.")
                connected = True
            sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        cutoff = time()
        try:
            history.ingest(read_audit(socket, options.operations_url), cutoff=cutoff)
        except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as error:
            logging.warning("Final Operations check failed: %s", error)
        history.flush(cutoff)
        logging.info("Device operation recording stopped.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--operations-url", help="read a local Operations audit URL instead of using SSH")
    options = parser.parse_args()
    handlers = [logging.StreamHandler()]
    if options.log_file:
        handlers.append(logging.FileHandler(options.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", handlers=handlers)
    try:
        options.output_dir.mkdir(parents=True, exist_ok=True)
        with (options.output_dir / ".device_log.lock").open("a") as lock, tempfile.TemporaryDirectory(prefix="e88-audit-") as runtime:
            flock(lock, LOCK_EX | LOCK_NB)
            socket = Path(runtime) / "ssh.sock"
            try:
                record(options, socket)
            finally:
                if socket.exists():
                    subprocess.run(SSH + ["-S", str(socket), "-O", "exit", VM], capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        logging.error("Device operation recording failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
