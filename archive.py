#!/usr/bin/env python3
"""Archive the E88 Grafana measurements as daily ROOT TTrees."""

import argparse
from array import array
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from fcntl import LOCK_EX, LOCK_NB, flock
import json
import logging
from math import ceil, isfinite, nan
import os
from pathlib import Path
from socket import gethostname
import tempfile
from time import sleep, time as unix_now
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
MINUTE = 60
INGEST_DELAY = 5
DATA_DIR = Path("/e88/u/e88daq/slow_control/Grafana_archive/data")
CONFIG = Path(__file__).with_name("metrics.json")
PROMETHEUS_URL = (
    "http://127.0.0.1:13000/api/datasources/proxy/uid/e88-lvmodule-prometheus"
    if gethostname().partition(".")[0] == "e88evb01"
    else "http://127.0.0.1:9091"
)
TREE_NAME = "Grafana"
METADATA_NAME = "branch_metadata"


@dataclass(frozen=True)
class Query:
    expression: str
    labels: tuple
    branches: dict


def load_config(path):
    config = json.loads(path.read_text())
    queries = []
    branches = {}
    for item in config["queries"]:
        series = config["series"][item["series"]]
        labels = tuple(sorted(next(iter(series.values()))))
        query_branches = {}
        for prefix, match in series.items():
            name = f"{prefix}_{item['field']}"
            key = tuple(match[label] for label in labels)
            if name in branches or key in query_branches or set(match) != set(labels):
                raise ValueError(f"Conflicting series configuration for {name}")
            query_branches[key] = name
            branches[name] = {"unit": item["unit"], "labels": match, "promql": item["expr"]}
        queries.append(Query(item["expr"], labels, query_branches))
    metadata = {
        "version": 1,
        "time_zone": "Asia/Tokyo",
        "step_seconds": MINUTE,
        "unix_time_unit": "Unix seconds at the start of each minute",
        "branches": branches,
    }
    return queries, metadata


def fetch_minutes(url, queries, start, end):
    opener = build_opener(ProxyHandler({}))
    rows = {timestamp: {} for timestamp in range(start, end + 1, MINUTE)}
    for query in queries:
        parameters = urlencode({"query": query.expression, "start": start, "end": end,
                                "step": MINUTE, "timeout": "8s"})
        with opener.open(f"{url.rstrip('/')}/api/v1/query_range?{parameters}", timeout=12) as response:
            payload = json.load(response)
        if payload["status"] != "success" or payload["data"]["resultType"] != "matrix":
            raise ValueError(f"Prometheus query did not return a matrix: {query.expression}")
        for warning in payload.get("warnings", []):
            logging.warning("Prometheus warning: %s", warning)
        for result in payload["data"]["result"]:
            key = tuple(result["metric"].get(label) for label in query.labels)
            branch = query.branches.get(key)
            if branch is None:
                continue
            for timestamp, value in result["values"]:
                row = rows[timestamp]
                if branch in row:
                    raise ValueError(f"Ambiguous series for {branch} at {timestamp}")
                row[branch] = float(value)
    return rows


def daily_path(directory, timestamp):
    date = datetime.fromtimestamp(timestamp, JST).strftime("%Y%m%d")
    return directory / f"Grafana_{date}.root"


def day_end(timestamp):
    tomorrow = datetime.fromtimestamp(timestamp, JST).date() + timedelta(days=1)
    return int(datetime.combine(tomorrow, time.min, JST).timestamp()) - MINUTE


@contextmanager
def open_tree(root, path):
    output = root.TFile.Open(str(path), "READ")
    if not output or output.IsZombie():
        raise OSError(f"Cannot open ROOT file {path}")
    try:
        tree = output.Get(TREE_NAME)
        if not tree or not tree.InheritsFrom("TTree"):
            raise ValueError(f"Missing TTree {TREE_NAME} in {path}")
        time_leaf = tree.GetLeaf("unix_time")
        if not time_leaf or time_leaf.GetTypeName() != "Long64_t":
            raise ValueError(f"Missing Long64_t unix_time branch in {path}")
        yield output, tree
    finally:
        output.Close()


def latest_minute(root, path):
    with open_tree(root, path) as (_, tree):
        entries = tree.GetEntries()
        if not entries or tree.GetEntry(entries - 1) <= 0:
            raise ValueError(f"Cannot read the last entry in {path}")
        return int(tree.unix_time)


def read_day(root, path, metadata):
    if not path.exists():
        return {}
    with open_tree(root, path) as (source, tree):
        schema = source.Get(METADATA_NAME)
        if not schema or json.loads(str(schema.GetString())) != metadata:
            raise ValueError(f"Existing file uses a different schema: {path}")
        timestamp_buffer = array("q", [0])
        data_buffers = {name: array("d", [nan]) for name in metadata["branches"]}
        for name, buffer in {"unix_time": timestamp_buffer, **data_buffers}.items():
            if tree.SetBranchAddress(name, buffer) < 0:
                raise ValueError(f"Cannot bind {name} in {path}")
        rows = {}
        for entry in range(tree.GetEntries()):
            if tree.GetEntry(entry) <= 0:
                raise ValueError(f"Cannot read entry {entry} in {path}")
            timestamp = timestamp_buffer[0]
            if timestamp % MINUTE or timestamp in rows or daily_path(path.parent, timestamp) != path:
                raise ValueError(f"Invalid or duplicate unix_time {timestamp} in {path}")
            rows[timestamp] = {name: buffer[0] for name, buffer in data_buffers.items()}
        return rows


def write_day(root, path, metadata, rows):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    try:
        with root.TFile.Open(temporary, "RECREATE") as output:
            if not output or output.IsZombie():
                raise OSError(f"Cannot create ROOT file at {temporary}")
            tree = root.TTree(TREE_NAME, "E88 Grafana measurements at one-minute intervals")
            timestamp_buffer = array("q", [0])
            tree.Branch("unix_time", timestamp_buffer, "unix_time/L")
            data_buffers = {name: array("d", [nan]) for name in metadata["branches"]}
            for name, buffer in data_buffers.items():
                tree.Branch(name, buffer, f"{name}/D")
            for timestamp, row in sorted(rows.items()):
                timestamp_buffer[0] = timestamp
                for name, buffer in data_buffers.items():
                    buffer[0] = row.get(name, nan)
                if tree.Fill() < 0:
                    raise OSError(f"TTree.Fill() failed for {timestamp}")
            if tree.Write() <= 0 or root.TObjString(json.dumps(metadata)).Write(METADATA_NAME) <= 0:
                raise OSError(f"Failed to write ROOT objects at {temporary}")
        # Give readers of the archive directory read access to its published files.
        os.chmod(temporary, 0o600 | (path.parent.stat().st_mode & 0o044))
        with open(temporary, "rb") as data:
            os.fsync(data.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def automatic_start(root, directory, now):
    files = sorted(directory.glob("Grafana_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].root"))
    if files:
        return latest_minute(root, files[-1]) + MINUTE
    first = directory / ".first_minute"
    if not first.exists():
        first.write_text(f"{now}\n")
    timestamp = int(first.read_text())
    if timestamp % MINUTE:
        raise ValueError(f"Invalid first minute in {first}")
    return timestamp


def record(root, options, queries, metadata, start, end):
    while start <= end:
        path = daily_path(options.output_dir, start)
        through = min(day_end(start), end)
        rows = read_day(root, path, metadata)
        missing = set(range(start, through + 1, MINUTE)).difference(rows)
        if missing:
            values = fetch_minutes(options.prometheus_url, queries, min(missing), max(missing))
            rows.update({timestamp: values[timestamp] for timestamp in missing})
            write_day(root, path, metadata, rows)
            recent = rows[max(missing)]
            available = sum(isfinite(value) for value in recent.values())
            latest = datetime.fromtimestamp(max(missing), JST).strftime("%Y-%m-%d %H:%M JST")
            logging.info("%s: added %d minute(s), %d rows, %s has %d/%d values",
                         path.name, len(missing), len(rows), latest, available, len(metadata["branches"]))
        start = through + MINUTE


def record_live(root, options, queries, metadata):
    start = ceil(unix_now() / MINUTE) * MINUTE
    logging.info("Recording started. First minute: %s",
                 datetime.fromtimestamp(start, JST).strftime("%Y-%m-%d %H:%M JST"))
    next_poll = start + INGEST_DELAY
    while True:
        sleep(max(0, next_poll - unix_now()))
        end = (int(unix_now()) - INGEST_DELAY) // MINUTE * MINUTE
        if end < start:
            continue
        try:
            record(root, options, queries, metadata, start, end)
        except OSError as error:
            logging.warning("Minute update failed. Retrying during this session: %s", error)
        else:
            start = end + MINUTE
        next_poll = end + MINUTE + INGEST_DELAY


def minute_timestamp(value):
    timestamp = int(value)
    if timestamp < 0 or timestamp % MINUTE:
        raise argparse.ArgumentTypeError("Use non-negative Unix seconds on a minute boundary")
    return timestamp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--prometheus-url", default=PROMETHEUS_URL)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--start", type=minute_timestamp, help="first minute to record, as Unix seconds")
    parser.add_argument("--end", type=minute_timestamp, help="last minute to record, inclusive")
    parser.add_argument("--live", action="store_true", help="record each minute until interrupted")
    parser.add_argument("--log-file", type=Path, help="also write status messages to this file")
    options = parser.parse_args()
    if options.live and (options.start is not None or options.end is not None):
        parser.error("--live cannot be combined with a fixed time range")
    now = int(datetime.now(JST).timestamp()) // MINUTE * MINUTE
    end = now if options.end is None else options.end
    if end > now or (options.start is not None and options.start > end):
        parser.error("The requested time range must end in the current minute or earlier")

    handlers = [logging.StreamHandler()]
    if options.log_file:
        handlers.append(logging.FileHandler(options.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", handlers=handlers)
    try:
        import ROOT

        ROOT.gROOT.SetBatch(True)
        queries, metadata = load_config(options.config)
        options.output_dir.mkdir(parents=True, exist_ok=True)
        with (options.output_dir / ".grafana_archive.lock").open("a") as lock:
            if options.live:
                flock(lock, LOCK_EX | LOCK_NB)
                record_live(ROOT, options, queries, metadata)
                return 0
            flock(lock, LOCK_EX)
            start = options.start
            if start is None:
                start = automatic_start(ROOT, options.output_dir, now)
            record(ROOT, options, queries, metadata, start, end)
    except KeyboardInterrupt:
        logging.info("Recording stopped.")
    except (ImportError, OSError, ValueError, KeyError) as error:
        logging.error("Archive update failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
