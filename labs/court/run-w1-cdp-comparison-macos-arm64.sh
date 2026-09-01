#!/bin/sh
set -eu

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "W1 CDP comparison: requires macOS arm64" >&2
    exit 69
fi

lightpanda_version=0.4.0
lightpanda=target/labs/lightpanda/$lightpanda_version/lightpanda-aarch64-macos
lightpanda_sha=840547bb7b98743a3e32618a4d120ac4a75e7c3c2d227ecf5ce8d508ddc118b7
chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
fixture=labs/court/fixtures/semantic-static.html
sampler=target/labs/process-tree-sampler/release/process-tree-sampler
hold_ms=2000

for command in cargo gh python3 shasum; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "W1 CDP comparison: missing required command $command" >&2
        exit 69
    }
done
if [ ! -x "$chrome" ]; then
    echo "W1 CDP comparison: installed Google Chrome is unavailable" >&2
    exit 69
fi
mkdir -p "$(dirname "$lightpanda")" "$(dirname "$sampler")"
if [ ! -f "$lightpanda" ]; then
    gh release download "$lightpanda_version" -R lightpanda-io/browser \
        -p lightpanda-aarch64-macos -D "$(dirname "$lightpanda")"
fi
if [ "$(shasum -a 256 "$lightpanda" | awk '{print $1}')" != "$lightpanda_sha" ]; then
    echo "W1 CDP comparison: Lightpanda digest mismatch" >&2
    exit 65
fi
chmod 755 "$lightpanda"
cargo build --release --manifest-path labs/court/process-tree-sampler/Cargo.toml \
    --target-dir target/labs/process-tree-sampler >/dev/null

court_tmp=$(mktemp -d)
cleanup() { rm -R "$court_tmp"; }
trap cleanup EXIT HUP INT TERM

sample() {
    engine=$1
    browser=$2
    output=$3
    "$sampler" --deadline-ms 20000 --interval-ms 10 --exclude-root -- \
        python3 labs/court/cdp-live-target.py --engine "$engine" \
        --browser "$browser" --fixture "$fixture" --hold-ms "$hold_ms" >"$output"
}

# Warm both executable/profile paths before measured, cold-profile repetitions.
sample lightpanda "$lightpanda" "$court_tmp/lightpanda-warmup.json"
sample chrome "$chrome" "$court_tmp/chrome-warmup.json"

i=1
while [ "$i" -le 7 ]; do
    if [ $((i % 2)) -eq 1 ]; then
        sample lightpanda "$lightpanda" "$court_tmp/lightpanda-$i.json"
        sample chrome "$chrome" "$court_tmp/chrome-$i.json"
    else
        sample chrome "$chrome" "$court_tmp/chrome-$i.json"
        sample lightpanda "$lightpanda" "$court_tmp/lightpanda-$i.json"
    fi
    i=$((i + 1))
done

python3 - "$court_tmp" "$chrome" "$fixture" "$hold_ms" "$lightpanda_sha" <<'PY'
import hashlib
import json
import pathlib
import statistics
import subprocess
import sys

directory, chrome, fixture, hold_ms, lightpanda_sha = sys.argv[1:]
directory = pathlib.Path(directory)

def load(engine):
    envelopes = [json.loads((directory / f"{engine}-{i}.json").read_text()) for i in range(1, 8)]
    receipts = [item["receipt"] for item in envelopes]
    for receipt in receipts:
        assert receipt["outcome"]["exit"] == {"kind": "code", "code": 0, "signal": None}
        assert not receipt["outcome"]["timed_out"]
    values = [item["measurement"]["peak_tree_resident_bytes"] for item in receipts]
    return values, {
        "median_peak_tree_resident_bytes": int(statistics.median(values)),
        "maximum_peak_tree_resident_bytes": max(values),
        "median_peak_process_count": int(statistics.median(
            item["measurement"]["peak_process_count"] for item in receipts
        )),
    }

lp_values, lp_summary = load("lightpanda")
chrome_values, chrome_summary = load("chrome")
version_text = subprocess.check_output([chrome, "--version"], text=True).strip()
chrome_version = version_text.removeprefix("Google Chrome ")
chrome_sha = hashlib.sha256(pathlib.Path(chrome).read_bytes()).hexdigest()
fixture_sha = hashlib.sha256(pathlib.Path(fixture).read_bytes()).hexdigest()
receipt = {
    "schema": 1,
    "status": "incomplete",
    "court": "same-machine-live-target-cdp-w1",
    "platform": {"os": "macos", "architecture": "arm64"},
    "workload": {
        "id": "W1",
        "transport": "data-url",
        "fixture_sha256": fixture_sha,
        "cdp_domains": ["Target", "Page", "Runtime"],
        "ready_condition": "named semantic h1/input/button/link observed",
        "steady_hold_ms": int(hold_ms),
        "warmups_per_candidate": 1,
        "measured_repetitions": 7,
        "order": "alternating by repetition",
        "profile": "fresh temporary profile per repetition",
    },
    "measurement": {
        "semantic": "10ms sampled sum of BSD ps RSS over attributable descendant tree; orchestrator root excluded",
        "lightpanda_peak_tree_resident_bytes": lp_values,
        "chrome_peak_tree_resident_bytes": chrome_values,
    },
    "candidates": {
        "lightpanda": {"version": "0.4.0", "artifact_sha256": lightpanda_sha, **lp_summary},
        "google_chrome": {"version": chrome_version, "executable_sha256": chrome_sha, **chrome_summary},
    },
    "observed_median_ratio_chrome_to_lightpanda": round(
        chrome_summary["median_peak_tree_resident_bytes"] / lp_summary["median_peak_tree_resident_bytes"], 3
    ),
    "limitations": [
        "summed RSS is neither private memory nor PSS and can double-count shared pages",
        "10ms process-table sampling can miss short-lived or reparented processes",
        "one synthetic data URL, one operating system and one architecture only",
        "installed Chrome is identified by version and digest but is not a pinned downloadable court artifact",
        "browser feature sets and engine capabilities are not equivalent",
        "the observed ratio is descriptive for this court and is not a universal product claim",
        "post-close retained memory, navigation soak and per-target growth are not measured",
    ],
}
print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
PY
