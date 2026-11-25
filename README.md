# E88 Grafana Archive

This recorder exports the Prometheus measurements used by the E88 Grafana dashboards to daily ROOT files and the device operation history to daily CSV files. The ROOT tree receives one entry per minute during a recording session.

## Start, check, and stop a recording

Run the commands on **e88evb01** as **e88daq**. The recorder uses Python 3, PyROOT, tmux, and the existing Grafana data-source proxy at `127.0.0.1:13000`. The first start prepares a runtime under `~/slowdash/grafana_archive` with your regular user permissions.

```bash
cd /e88/u/e88daq/slow_control/Grafana_archive
```

| Action | Command |
| --- | --- |
| Start both recorders in one tmux session | `python3 start.py` |
| Check both recorders, find the latest files, and see recent log messages | `python3 status.py` |
| Stop both recorders | `python3 kill.py` |

The first ROOT entry uses the next minute boundary as its timestamp. Use the attach command printed by `start.py` or `status.py` to view both tmux panes. Press `Ctrl+B`, then `D`, to detach and keep recording. Both recorders retry failed requests during the active session. Gaps between sessions are preserved in the archive.

The **ROOT Archive** panel in Grafana uses the `e88_grafana_archive_recording` gauge from `python3 status.py --prometheus` to show the ROOT recorder state.

## Device operation history

CSV files are written to `device_log/Grafana_device_YYYYMMDD.csv`, for example `device_log/Grafana_device_20261125.csv`. The file date is the operation date in Japan Standard Time. The recorder polls the **Operations** audit API about every two seconds over the existing non-interactive SSH connection to the monitoring VM.

Each operation gets one row. A later confirmation or failure updates that row in the original file, including when the result arrives after midnight. When an operation is in progress, its row shows the latest phase reported by Operations.

| Column | Meaning |
| --- | --- |
| `unix_time` | Time of the first available audit event for the operation, in Unix seconds with fractional precision when available |
| `time_jst` | The same time in JST as an ISO 8601 timestamp with `+09:00` |
| `device`, `action` | Device identifier and operation name, such as `set_voltage` or `turn_on` |
| `value` | Requested setting or settings encoded as JSON |
| `result`, `confirmed` | Latest phase and the `true` or `false` confirmation flag reported by Operations |
| `result_time_jst` | Time of the latest phase in JST |
| `revision` | Device revision reported with the operation |
| `error` | Error type when Operations reports one |

The Operations API returns its latest 100 audit events. If a full history window has no overlap with the previous poll, the recorder logs a warning about possible missed events.

### Read a device CSV

From the repository directory, substitute the operation date in this example.

```bash
python3 - <<'PY'
import csv

with open("device_log/Grafana_device_20261125.csv", newline="", encoding="utf-8") as source:
    for row in csv.DictReader(source):
        print(row["time_jst"], row["device"], row["action"], row["result"])
PY
```

## Output files

Files are written under `/e88/u/e88daq/slow_control/Grafana_archive/data/` and can be read from either e88evb01 or the monitoring VM at that path. Each file is named `Grafana_YYYYMMDD.root`, for example `Grafana_20261125.root`. File dates follow Japan Standard Time and roll over at midnight. A complete update replaces the previous version of the daily file so readers can open it while recording continues.

## ROOT tree contents

Each file contains a `TTree` named **`Grafana`** with **133 branches**. `unix_time` is a `Long64_t` containing the Unix timestamp in seconds at the start of the recorded minute. The other **132 branches** are `Double_t` measurements evaluated at that timestamp. Missing measurements are stored as `NaN`.

| Group | Branches | Contents |
| --- | ---: | --- |
| Detector power supplies | 90 | Nine fields per channel for 10 HV and low-voltage channels covering MRPC, AC, SC, and DISCRI. See the field list below. |
| e88evb01 host and CPU | 3 | `e88evb01_sampling_ok` for scrape status, `e88evb01_cpu_count` for online logical CPUs, and `e88evb01_cpu_usage_pct` for CPU utilization based on the two-minute idle rate. |
| e88evb01 filesystems | 36 | `capacity_bytes`, `used_bytes`, `available_bytes`, and `usage_pct` for each of nine mount points. Utilization is `100 × used / max(used + available, 1)`. |
| e88evb01 ZFS pool | 3 | `capacity_bytes`, `used_bytes`, and `usage_pct` for `e88pool01`. Utilization is `100 × used / max(capacity, 1)`. |

The filesystem branches cover `/`, `/boot`, `/boot/efi`, `/home`, `/var`, `/e88/share`, `/e88/u`, `/e88/w`, and `/e88/w/r01`.

### Power supply fields

Power branch names identify the detector, supply, and channel. For example, `mrpc_p4k02_hv_voltage_v` is the measured voltage of the MRPC HV channel on supply `p4k02`.

| Suffix | Value |
| --- | --- |
| `voltage_v`, `current_a` | Measured voltage in volts and current in amperes |
| `voltage_set_v`, `current_set_a` | Voltage and current setpoints in volts and amperes |
| `output_on` | Supply output state, `0` for off and `1` for on |
| `current_limit_status` | `0` up to 80% of the current setpoint, `1` above 80% through 90%, and `2` above 90% |
| `read_ok`, `comm_error` | Device read status and communication error flag. `read_ok = 1` means the read succeeded. `comm_error = 1` means a communication error occurred. |
| `last_success_unix_time` | Unix timestamp in seconds of the last successful device read |

Each ROOT file also stores a `TObjString` named `branch_metadata`. Its JSON records the branch units, Prometheus labels, and PromQL expressions used for that file. The series and queries are defined in the repository. [metrics.json](metrics.json)

### Read a ROOT file with PyROOT

From the repository directory, replace the example date with the day you want to inspect.

```bash
python3 - <<'PY'
import ROOT

with ROOT.TFile.Open("data/Grafana_20261125.root", "READ") as root_file:
    tree = root_file.Get("Grafana")
    for index in range(min(5, tree.GetEntries())):
        tree.GetEntry(index)
        print(tree.unix_time, tree.mrpc_p4k02_hv_voltage_v, tree.e88evb01_cpu_usage_pct)
PY
```
