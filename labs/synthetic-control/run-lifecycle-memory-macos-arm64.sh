#!/bin/sh
set -eu

lab_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH='' cd -- "$lab_dir/../.." && pwd)
host_target="$lab_dir/target"
host="$host_target/release/minicon-surf-synthetic-control"
sampler_target="$repo_dir/target/labs/process-tree-sampler"
sampler="$sampler_target/release/process-tree-sampler"
warmup_ms=300
hold_ms=1200

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "synthetic lifecycle memory court: requires macOS arm64" >&2
    exit 69
fi
for command in cargo python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "synthetic lifecycle memory court: missing required command $command" >&2
        exit 69
    }
done

cargo build --release --locked --manifest-path "$lab_dir/Cargo.toml" \
    --target-dir "$host_target" >/dev/null
cargo build --release --locked \
    --manifest-path "$repo_dir/labs/court/process-tree-sampler/Cargo.toml" \
    --target-dir "$sampler_target" >/dev/null

court_tmp=$(mktemp -d)
cleanup() { rm -R "$court_tmp"; }
trap cleanup EXIT HUP INT TERM

sample() {
    mode=$1
    name=$2
    "$sampler" --deadline-ms 5000 --interval-ms 10 --warmup-ms "$warmup_ms" \
        --exclude-root -- python3 "$lab_dir/lifecycle-state.py" \
        --mode "$mode" --binary "$host" --hold-ms "$hold_ms" \
        --observation "$court_tmp/$name-observation.json" >"$court_tmp/$name-sampler.json"
}

for mode in empty live headed post-hide post-close; do sample "$mode" "$mode-warmup"; done
i=1
while [ "$i" -le 7 ]; do
    if [ $((i % 2)) -eq 1 ]; then
        order="empty live headed post-hide post-close"
    else
        order="post-close post-hide headed live empty"
    fi
    for mode in $order; do sample "$mode" "$mode-$i"; done
    i=$((i + 1))
done

python3 - "$court_tmp" "$host" "$warmup_ms" "$hold_ms" <<'PY'
import hashlib
import json
import pathlib
import statistics
import sys

directory, binary, warmup_ms, hold_ms = sys.argv[1:]
directory = pathlib.Path(directory)
warmup_ms = int(warmup_ms)

def load(mode):
    samplers = [json.loads((directory / f"{mode}-{i}-sampler.json").read_text())["receipt"] for i in range(1, 8)]
    observations = [json.loads((directory / f"{mode}-{i}-observation.json").read_text()) for i in range(1, 8)]
    for sampler, observation in zip(samplers, observations):
        assert sampler["outcome"]["exit"] == {"kind": "code", "code": 0, "signal": None}
        assert not sampler["outcome"]["timed_out"]
        assert sampler["measurement"]["warmup_ms"] == warmup_ms
        assert sampler["measurement"]["first_sample_wall_time_ms"] >= warmup_ms
        assert sampler["measurement"]["peak_process_count"] == 1
        assert observation["mode"] == mode
        assert observation["setup_ms"] < warmup_ms
    rss = [item["measurement"]["peak_tree_resident_bytes"] for item in samplers]
    process_counts = [item["measurement"]["peak_process_count"] for item in samplers]
    sample_counts = [item["measurement"]["sample_count"] for item in samplers]
    first_samples = [item["measurement"]["first_sample_wall_time_ms"] for item in samplers]
    logical = [item["logical_accounted_bytes"] for item in observations]
    setup = [item["setup_ms"] for item in observations]
    return {
        "peak_steady_tree_resident_bytes": rss,
        "median_peak_steady_tree_resident_bytes": int(statistics.median(rss)),
        "maximum_peak_steady_tree_resident_bytes": max(rss),
        "peak_process_counts": process_counts,
        "sample_counts": sample_counts,
        "first_sample_wall_time_ms": first_samples,
        "logical_accounted_bytes": logical,
        "median_logical_accounted_bytes": int(statistics.median(logical)),
        "setup_ms": setup,
        "maximum_setup_ms": max(setup),
        "target_objects": observations[0]["target_objects"],
        "target_closed": observations[0]["target_closed"],
        "surface_objects": observations[0]["surface_objects"],
        "surface_hidden": observations[0]["surface_hidden"],
    }

states = {mode: load(mode) for mode in ("empty", "live", "headed", "post-hide", "post-close")}
empty = states["empty"]["median_peak_steady_tree_resident_bytes"]
live = states["live"]["median_peak_steady_tree_resident_bytes"]
headed = states["headed"]["median_peak_steady_tree_resident_bytes"]
post_hide = states["post-hide"]["median_peak_steady_tree_resident_bytes"]
post = states["post-close"]["median_peak_steady_tree_resident_bytes"]
receipt = {
    "schema": 1,
    "status": "incomplete",
    "technology": "synthetic-control",
    "technology_version": "control-0.0.1",
    "binary_sha256": hashlib.sha256(pathlib.Path(binary).read_bytes()).hexdigest(),
    "platform": {"os": "macos", "architecture": "arm64"},
    "workload": {
        "id": "synthetic-target-lifecycle",
        "modes": ["empty", "live", "headed", "post-hide", "post-close"],
        "warmups_per_mode": 1,
        "measured_repetitions_per_mode": 7,
        "order": "alternating forward/reverse",
        "setup_warmup_ms": warmup_ms,
        "steady_hold_ms": int(hold_ms),
    },
    "measurement": {
        "semantic": "peak 10ms sampled summed RSS of the host child during the post-setup steady window",
        "process_scope": "complete synthetic host tree; Python lifecycle wrapper excluded",
        "states": states,
        "median_live_minus_empty_resident_bytes": live - empty,
        "median_headed_minus_live_resident_bytes": headed - live,
        "median_post_hide_minus_live_resident_bytes": post_hide - live,
        "median_post_close_minus_empty_resident_bytes": post - empty,
    },
    "bounds": {
        "profiles": 8,
        "sessions": 16,
        "targets": 32,
        "surfaces": 8,
        "synthetic_presentation_bytes_per_surface": 65536,
        "nodes_per_target": 128,
        "request_bytes": 65536,
        "response_bytes": 4194304,
    },
    "limitations": [
        "summed RSS is neither private memory nor PSS and is page-granular",
        "each lifecycle mode uses a separate fresh process rather than marking stages in one process",
        "fixed warmup is accepted only because every recorded setup completed before it",
        "one synthetic two-node target and presentation buffer are not an HTML or native-window workload",
        "logical accounted bytes exclude allocator and map overhead",
        f"post-hide logical ownership returns to live but median RSS remains {post_hide - live} bytes above live",
        "capacity rejection is unit-tested but maximum-capacity RSS is not measured",
        "no external browser baseline is meaningful for this engine-neutral control state",
    ],
}
print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
PY
