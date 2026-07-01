#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

export RECOVERIX_FORCE_DEBOOTSTRAP=1
export RECOVERIX_RUNTIME_ENABLE_GUI=0
export KEEP_RUNTIME_ROOTFS="${KEEP_RUNTIME_ROOTFS:-1}"

exec ./run_full_gui_test_cycle.sh
