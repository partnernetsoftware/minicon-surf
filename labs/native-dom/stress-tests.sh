#!/bin/sh
# Repeat the native-dom unit-test binary many times, in the default parallel
# mode and with --test-threads=1, and count failing runs. Used to chase the
# arena unmap-counter flake (a process-global counter read by a test while
# other tests dropped arenas). Usage: stress-tests.sh [runs] [single-runs]
set -eu
cd "$(dirname "$0")"
runs="${1:-300}"
single="${2:-100}"
exe="$(cargo test --offline --no-run 2>&1 | grep -o 'target/debug/deps/native_dom_control-[a-z0-9]*' | head -1)"
count() { # exe threads runs
  fail=0; i=0
  while [ "$i" -lt "$3" ]; do
    i=$((i + 1))
    if [ "$2" = 1 ]; then out="$("$1" --test-threads=1 2>&1 || true)"; else out="$("$1" 2>&1 || true)"; fi
    case "$out" in *"test result: ok"*) ;; *) fail=$((fail + 1)); printf '%s\n' "$out" | grep -E 'FAILED|panicked|left:|right:' | head -6;; esac
  done
  echo "threads=$2 failures=$fail/$3"
}
count "$exe" auto "$runs"
count "$exe" 1 "$single"
