#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    echo "lightpanda court: requires macOS arm64" >&2
    exit 69
fi

command -v gh >/dev/null 2>&1 || {
    echo "lightpanda court: gh is required to fetch the pinned release" >&2
    exit 69
}
command -v curl >/dev/null 2>&1 || {
    echo "lightpanda court: curl is required for the CDP discovery probe" >&2
    exit 69
}

version=0.4.0
asset=lightpanda-aarch64-macos
expected_sha=840547bb7b98743a3e32618a4d120ac4a75e7c3c2d227ecf5ce8d508ddc118b7
artifact_dir=target/labs/lightpanda/$version
artifact=$artifact_dir/$asset
fixture=labs/court/fixtures/semantic-static.html
expected_heading="Memory and Agent Court"
expected_button="Continue"

mkdir -p "$artifact_dir"
if [ ! -f "$artifact" ]; then
    gh release download "$version" -R lightpanda-io/browser -p "$asset" -D "$artifact_dir"
fi

actual_sha=$(shasum -a 256 "$artifact" | awk '{print $1}')
if [ "$actual_sha" != "$expected_sha" ]; then
    echo "lightpanda court: pinned artifact digest mismatch" >&2
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
        "$artifact" fetch --wait-ms 0 --dump semantic_tree_text "$fixture_uri" \
        >"$court_tmp/output-$run_id.txt"

    grep -F "$expected_heading" "$court_tmp/output-$run_id.txt" >/dev/null
    grep -F "$expected_button" "$court_tmp/output-$run_id.txt" >/dev/null
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

output_sha=$(shasum -a 256 "$court_tmp/output-1.txt" | awk '{print $1}')
os_version=$(sw_vers -productVersion)

cdp_port=19222
env LIGHTPANDA_DISABLE_TELEMETRY=true LIGHTPANDA_DISABLE_CORE_DUMP=1 \
    "$artifact" serve --host 127.0.0.1 --port "$cdp_port" \
    >"$court_tmp/cdp-stdout.txt" 2>"$court_tmp/cdp-stderr.txt" &
server_pid=$!

cdp_ready=false
i=0
while [ "$i" -lt 50 ]; do
    if curl -fsS "http://127.0.0.1:$cdp_port/json/version" \
        >"$court_tmp/cdp-version.json" 2>/dev/null; then
        cdp_ready=true
        break
    fi
    sleep 0.1
    i=$((i + 1))
done

if [ "$cdp_ready" != true ]; then
    echo "lightpanda court: CDP discovery endpoint did not become ready" >&2
    exit 70
fi

python3 - "$court_tmp/cdp-version.json" "$version" <<'PY'
import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert document["Protocol-Version"] == "1.3"
assert document["Lightpanda-Version"] == sys.argv[2]
assert document["webSocketDebuggerUrl"].startswith("ws://127.0.0.1:")
PY
kill "$server_pid" 2>/dev/null || true
wait "$server_pid" 2>/dev/null || true
server_pid=""

printf '%s\n' "{\"schema\":1,\"status\":\"incomplete\",\"technology\":\"lightpanda\",\"technology_version\":\"$version\",\"artifact_sha256\":\"$actual_sha\",\"platform\":{\"os\":\"macos\",\"os_version\":\"$os_version\",\"architecture\":\"arm64\"},\"workload\":{\"id\":\"W1\",\"transport\":\"data-url\",\"fixture_sha256\":\"$fixture_sha\",\"repetitions\":7},\"measurement\":{\"memory_semantic\":\"bsd-time-process-maximum-resident-set-size\",\"process_scope\":\"root-process-only; no child process observed during this short fetch, stronger sampler pending\",\"samples\":[$samples]},\"agent_observation\":{\"interface\":\"semantic_tree_text+cdp-1.3-discovery\",\"expected_present\":true,\"output_sha256\":\"$output_sha\"},\"limitations\":[\"same-machine browser baseline pending\",\"complete process-tree sampler pending\",\"single short hermetic document only\",\"CDP target/action journey pending\",\"macos-arm64 only\"]}"
