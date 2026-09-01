#!/bin/sh
set -eu

lab_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH='' cd -- "$lab_dir/../.." && pwd)
servo_target="$lab_dir/target"
servo_binary="$servo_target/release/servo-w1-runtime"
sampler_target="$repo_dir/target/labs/process-tree-sampler"
sampler="$sampler_target/release/process-tree-sampler"
fixture="$repo_dir/labs/court/fixtures/semantic-static.html"
hold_ms=2000

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "Servo W1 runtime court: requires macOS arm64" >&2
    exit 69
fi
for command in cargo python3 shasum; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Servo W1 runtime court: missing required command $command" >&2
        exit 69
    }
done

cargo build --release --locked --manifest-path "$lab_dir/Cargo.toml" \
    --target-dir "$servo_target" >/dev/null
cargo build --release --locked \
    --manifest-path "$repo_dir/labs/court/process-tree-sampler/Cargo.toml" \
    --target-dir "$sampler_target" >/dev/null

court_tmp=$(mktemp -d)
cleanup() { rm -R "$court_tmp"; }
trap cleanup EXIT HUP INT TERM

sample() {
    name=$1
    config="$court_tmp/config-$name"
    mkdir -p "$config"
    "$sampler" --deadline-ms 20000 --interval-ms 10 -- \
        "$servo_binary" "$fixture" "$config" "$hold_ms" >"$court_tmp/$name.json"
}

sample warmup
i=1
while [ "$i" -le 7 ]; do
    sample "$i"
    i=$((i + 1))
done

python3 - "$court_tmp" "$servo_binary" "$fixture" "$hold_ms" <<'PY'
import hashlib
import json
import pathlib
import statistics
import sys

directory, binary, fixture, hold_ms = sys.argv[1:]
directory = pathlib.Path(directory)
receipts = [json.loads((directory / f"{i}.json").read_text())["receipt"] for i in range(1, 8)]
for receipt in receipts:
    assert receipt["outcome"]["exit"] == {"kind": "code", "code": 0, "signal": None}
    assert not receipt["outcome"]["timed_out"]
values = [item["measurement"]["peak_tree_resident_bytes"] for item in receipts]
process_counts = [item["measurement"]["peak_process_count"] for item in receipts]
receipt = {
    "schema": 1,
    "status": "incomplete",
    "technology": "servo",
    "technology_version": "0.5.0",
    "crate_sha256": "331e15df72165ca15b3945970c6870c4b7367be116ded058fda4f41190b265b8",
    "binary_sha256": hashlib.sha256(pathlib.Path(binary).read_bytes()).hexdigest(),
    "platform": {"os": "macos", "architecture": "arm64"},
    "workload": {
        "id": "W1",
        "fixture_sha256": hashlib.sha256(pathlib.Path(fixture).read_bytes()).hexdigest(),
        "transport": "percent-encoded-data-url",
        "rendering_context": "SoftwareRenderingContext-800x600",
        "semantic_condition": "named h1/input/button/link observed through evaluate_javascript",
        "render_condition": "800x600 screenshot callback succeeded",
        "steady_hold_ms": int(hold_ms),
        "warmups": 1,
        "measured_repetitions": 7,
        "profile": "fresh temporary config and temporary_storage per repetition",
    },
    "measurement": {
        "semantic": "10ms sampled sum of BSD ps RSS over complete attributable process tree",
        "peak_tree_resident_bytes": values,
        "median_peak_tree_resident_bytes": int(statistics.median(values)),
        "maximum_peak_tree_resident_bytes": max(values),
        "peak_process_counts": process_counts,
        "median_peak_process_count": int(statistics.median(process_counts)),
    },
    "agent_observation": {
        "interface": "direct Rust WebView evaluate_javascript and screenshot callbacks",
        "semantic_expected_present": True,
        "render_expected_present": True,
    },
    "limitations": [
        "summed RSS is neither private memory nor PSS and can double-count shared pages",
        "10ms process-table sampling can miss short-lived or reparented processes",
        "one synthetic data URL, one operating system and one architecture only",
        "software rendering is not a headed or GPU-offscreen comparison",
        "no same-court Chrome or Lightpanda comparison was run in this execution",
        "direct Rust callbacks do not prove native CLI, stable semantic nodes or CDP compatibility",
        "post-close retained memory, navigation soak and per-target growth are not measured",
        "the local release binary digest can vary across toolchains even with the pinned lockfile",
    ],
}
print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
PY
