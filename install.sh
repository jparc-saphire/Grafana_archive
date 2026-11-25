#!/usr/bin/bash
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

if (( $# != 1 )); then
    printf 'Usage: bash %s <archive runtime directory>\n' "$0" >&2
    exit 2
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
archive_dir=$1
archive_python=$archive_dir/.venv/bin/python
umask 027

mkdir -p "$archive_dir"
if [[ ! -x $archive_python ]]; then
    /usr/bin/python3 -m venv --system-site-packages "$archive_dir/.venv"
fi
if ! "$archive_python" -B -c 'import ROOT; ROOT.gROOT.GetVersion()' 2>/dev/null; then
    "$archive_python" -m pip --disable-pip-version-check \
        install --no-cache-dir --only-binary=:all: --index-url https://pypi.org/simple \
        root==0.1a12 numpy==2.5.5
fi

if ! command -v tmux >/dev/null && [[ ! -x $archive_dir/bin/tmux ]]; then
    # Extract the signed AlmaLinux package into the user's runtime directory.
    stage=$(mktemp -d)
    trap 'rm -rf -- "$stage"' EXIT
    package=tmux-3.3a-14.20260304gitb202a2f.el10.x86_64.rpm
    curl -fLSs --max-time 30 -o "$stage/$package" \
        "https://repo.almalinux.org/almalinux/10.3/BaseOS/x86_64/os/Packages/$package"
    printf '%s  %s\n' fc054c334680ad9d8adaa0900461d2cf9546acdfa69a4649e32c852f1d96a58e "$stage/$package" | sha256sum -c --status
    rpmkeys --checksig "$stage/$package"
    (cd "$stage" && rpm2cpio "$package" | cpio -id --quiet ./usr/bin/tmux)
    "$stage/usr/bin/tmux" -V
    install -D -m 0755 "$stage/usr/bin/tmux" "$archive_dir/bin/tmux"
fi

install -m 0644 "$source_dir/archive.py" "$source_dir/metrics.json" "$archive_dir/"
