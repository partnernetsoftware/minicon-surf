#!/bin/sh
set -eu

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "target retention court: requires macOS arm64" >&2
    exit 69
fi

lightpanda_version=0.4.0
lightpanda=target/labs/lightpanda/$lightpanda_version/lightpanda-aarch64-macos
lightpanda_sha=840547bb7b98743a3e32618a4d120ac4a75e7c3c2d227ecf5ce8d508ddc118b7
chrome='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
fixture=labs/court/fixtures/semantic-static.html

for command in gh python3 shasum; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "target retention court: missing required command $command" >&2
        exit 69
    }
done
if [ ! -x "$chrome" ]; then
    echo "target retention court: installed Google Chrome is unavailable" >&2
    exit 69
fi
mkdir -p "$(dirname "$lightpanda")"
if [ ! -f "$lightpanda" ]; then
    gh release download "$lightpanda_version" -R lightpanda-io/browser \
        -p lightpanda-aarch64-macos -D "$(dirname "$lightpanda")"
fi
if [ "$(shasum -a 256 "$lightpanda" | awk '{print $1}')" != "$lightpanda_sha" ]; then
    echo "target retention court: Lightpanda digest mismatch" >&2
    exit 65
fi
chmod 755 "$lightpanda"

exec python3 labs/court/cdp-target-retention-macos-arm64.py \
    --lightpanda "$lightpanda" \
    --chrome "$chrome" \
    --fixture "$fixture" \
    --lightpanda-sha256 "$lightpanda_sha" "$@"
