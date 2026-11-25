# E88 Grafana Archive

This recorder exports the Prometheus measurements used by the E88 Grafana dashboards to daily ROOT files. It writes one entry per minute while the recording session is running.

## Start, check, and stop a recording

Run the commands on **e88evb01** as **e88daq**. The recorder uses Python 3, PyROOT, tmux, and the existing Grafana data-source proxy at `127.0.0.1:13000`. The first start prepares a runtime under `~/slowdash/grafana_archive` with your regular user permissions.

```bash
cd /e88/u/e88daq/slow_control/Grafana_archive
```

| Action | Command |
| --- | --- |
| Start recording in a dedicated tmux session | `python3 start.py` |
| Check whether the recorder is running, find the latest file, and see recent log messages | `python3 status.py` |
| Stop the recorder | `python3 kill.py` |

The first entry uses the next minute boundary as its timestamp. Use the attach command printed by `start.py` or `status.py` to view the tmux session. Press `Ctrl+B`, then `D`, to detach and keep recording. The recorder retries failed requests during the active session. A new session starts at its own minute boundary, leaving gaps between sessions in the archive.

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
