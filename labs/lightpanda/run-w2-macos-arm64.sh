#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    echo "lightpanda W2 court: requires macOS arm64" >&2
    exit 69
fi

for command in curl gh perl python3 shasum; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "lightpanda W2 court: missing required command $command" >&2
        exit 69
    }
done

version=0.4.0
asset=lightpanda-aarch64-macos
expected_sha=840547bb7b98743a3e32618a4d120ac4a75e7c3c2d227ecf5ce8d508ddc118b7
artifact_dir=target/labs/lightpanda/$version
artifact=$artifact_dir/$asset
fixture=labs/court/fixtures/semantic-scripted.html
expected_heading="After script"
expected_button="Agent visible action"

mkdir -p "$artifact_dir"
if [ ! -f "$artifact" ]; then
    gh release download "$version" -R lightpanda-io/browser -p "$asset" -D "$artifact_dir"
fi

actual_sha=$(shasum -a 256 "$artifact" | awk '{print $1}')
if [ "$actual_sha" != "$expected_sha" ]; then
    echo "lightpanda W2 court: pinned artifact digest mismatch" >&2
    exit 65
fi
chmod 755 "$artifact"

fixture_sha=$(shasum -a 256 "$fixture" | awk '{print $1}')
fixture_uri=$(python3 - "$fixture" <<'PY'
import pathlib
import sys
import urllib.parse

data = pathlib.Path(sys.argv[1]).read_bytes()
print("data:text/html," + urllib.parse.quote_from_bytes(data, safe=""))
PY
)

court_tmp=$(mktemp -d)
server_pid=""
cleanup() {
    if [ -n "$server_pid" ]; then
        kill "$server_pid" 2>/dev/null || true
        wait "$server_pid" 2>/dev/null || true
    fi
    rm -R "$court_tmp"
}
trap cleanup EXIT HUP INT TERM

run_one() {
    run_id=$1
    /usr/bin/time -lp -o "$court_tmp/time-$run_id.txt" \
        env LIGHTPANDA_DISABLE_TELEMETRY=true LIGHTPANDA_DISABLE_CORE_DUMP=1 \
        perl -e 'alarm shift; exec @ARGV or die "exec failed\n"' 15 \
        "$artifact" fetch --wait-ms 0 --terminate-ms 10000 \
        --dump semantic_tree_text "$fixture_uri" >"$court_tmp/output-$run_id.txt"

    grep -F "$expected_heading" "$court_tmp/output-$run_id.txt" >/dev/null
    grep -F "$expected_button" "$court_tmp/output-$run_id.txt" >/dev/null
    if grep -F "Before script" "$court_tmp/output-$run_id.txt" >/dev/null; then
        echo "lightpanda W2 court: pre-mutation heading remained" >&2
        exit 70
    fi
}

run_one warmup

samples=""
i=1
while [ "$i" -le 7 ]; do
    run_one "$i"
    wall=$(awk '$1 == "real" { print $2 }' "$court_tmp/time-$i.txt")
    rss=$(awk '/maximum resident set size/ { print $1 }' "$court_tmp/time-$i.txt")
    sample=$(printf '{"wall_seconds":%s,"memory_bytes":%s}' "$wall" "$rss")
    if [ -n "$samples" ]; then
        samples="$samples,$sample"
    else
        samples=$sample
    fi
    i=$((i + 1))
done

semantic_sha=$(shasum -a 256 "$court_tmp/output-1.txt" | awk '{print $1}')
os_version=$(sw_vers -productVersion)

# Use a per-run ephemeral port assigned by the OS; the endpoint never leaves
# loopback and only sanitized facts are promoted to evidence.
cdp_port=$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)
env LIGHTPANDA_DISABLE_TELEMETRY=true LIGHTPANDA_DISABLE_CORE_DUMP=1 \
    "$artifact" serve --host 127.0.0.1 --port "$cdp_port" --disable-metrics \
    --watchdog-ms 10000 >"$court_tmp/cdp-stdout.txt" 2>"$court_tmp/cdp-stderr.txt" &
server_pid=$!

cdp_ready=false
i=0
while [ "$i" -lt 100 ]; do
    if curl --max-time 1 -fsS "http://127.0.0.1:$cdp_port/json/version" \
        >"$court_tmp/cdp-version.json" 2>/dev/null; then
        cdp_ready=true
        break
    fi
    sleep 0.1
    i=$((i + 1))
done
if [ "$cdp_ready" != true ]; then
    echo "lightpanda W2 court: CDP discovery endpoint did not become ready" >&2
    exit 70
fi

cdp_endpoint=$(python3 - "$court_tmp/cdp-version.json" "$version" <<'PY'
import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert document["Protocol-Version"] == "1.3"
assert document["Lightpanda-Version"] == sys.argv[2]
endpoint = document["webSocketDebuggerUrl"]
assert endpoint.startswith("ws://127.0.0.1:")
print(endpoint)
PY
)

python3 labs/lightpanda/cdp-w2-journey.py --endpoint "$cdp_endpoint" \
    --fixture "$fixture" --output "$court_tmp/cdp-observation.json"
cdp_sha=$(python3 - "$court_tmp/cdp-observation.json" <<'PY'
import json
import pathlib
import sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text())["observation_sha256"])
PY
)

kill "$server_pid" 2>/dev/null || true
wait "$server_pid" 2>/dev/null || true
server_pid=""

printf '%s\n' "{\"schema\":1,\"status\":\"incomplete\",\"technology\":\"lightpanda\",\"technology_version\":\"$version\",\"artifact_sha256\":\"$actual_sha\",\"platform\":{\"os\":\"macos\",\"os_version\":\"$os_version\",\"architecture\":\"arm64\"},\"workload\":{\"id\":\"W2\",\"transport\":\"data-url\",\"fixture_sha256\":\"$fixture_sha\",\"repetitions\":7},\"measurement\":{\"memory_semantic\":\"bsd-time-process-maximum-resident-set-size\",\"process_scope\":\"root-process-only; child processes were not sampled or excluded\",\"samples\":[$samples]},\"agent_observation\":{\"interface\":\"semantic_tree_text+cdp-1.3-target-page-runtime-dom\",\"expected_present\":true,\"output_sha256\":\"$cdp_sha\"},\"limitations\":[\"same-machine browser baseline pending\",\"complete process-tree sampler pending\",\"CDP journey is one target in one short-lived server session\",\"CDP Input domain and external client qualification pending\",\"semantic and CDP observations are separate executions of the same hermetic fixture\",\"macos-arm64 only\"]}"
