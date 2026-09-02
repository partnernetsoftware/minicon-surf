#!/bin/sh
set -eu

lab_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH='' cd -- "$lab_dir/../.." && pwd)
servo_target="$lab_dir/target"
servo_binary="$servo_target/release/servo-w3-runtime"
fixture="$repo_dir/labs/court/fixtures/semantic-static.html"

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "Servo W3 memory court: requires macOS arm64" >&2
    exit 69
fi
for command in cargo python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Servo W3 memory court: missing required command $command" >&2
        exit 69
    }
done

cargo build --release --locked --manifest-path "$lab_dir/Cargo.toml" \
    --bin servo-w3-runtime --target-dir "$servo_target" >/dev/null

exec python3 "$lab_dir/staged-w3-memory-macos-arm64.py" \
    --binary "$servo_binary" --fixture "$fixture" "$@"
