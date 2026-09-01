#!/bin/sh
set -eu

lab_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
target_dir="$lab_dir/target"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "this receipt cell is defined for macOS arm64" >&2
  exit 2
fi

mkdir -p "$target_dir"
cargo check \
  --locked \
  --manifest-path "$lab_dir/Cargo.toml" \
  --target-dir "$target_dir"
cargo test \
  --locked \
  --manifest-path "$lab_dir/Cargo.toml" \
  --target-dir "$target_dir"
cargo clippy \
  --locked \
  --manifest-path "$lab_dir/Cargo.toml" \
  --target-dir "$target_dir" \
  -- -D warnings
