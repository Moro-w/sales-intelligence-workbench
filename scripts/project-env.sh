#!/usr/bin/env bash
# Source this file; do not change the user's shell configuration.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT_ROOT
export CARGO_HOME="$PROJECT_ROOT/.tools/cargo"
export RUSTUP_HOME="$PROJECT_ROOT/.tools/rustup"
export CARGO_TARGET_DIR="$PROJECT_ROOT/.tools/cargo-target"
export BUN_INSTALL_CACHE_DIR="$PROJECT_ROOT/.tools/bun-cache"
export npm_config_cache="$PROJECT_ROOT/.tools/npm-cache"
export UV_CACHE_DIR="$PROJECT_ROOT/.tools/uv-cache"
export PATH="$PROJECT_ROOT/.tools/bun-darwin-aarch64:$CARGO_HOME/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHON="${PYTHON:-$(command -v python3.11)}"
