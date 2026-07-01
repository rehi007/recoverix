#!/usr/bin/env bash
# Standalone strict layout validator for runtime.squashfs.
# Usage: sudo ./13_verify_squashfs_layout.sh [path/to/runtime.squashfs]

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/integrity_check.sh
source "${DIR}/lib/integrity_check.sh"

require_root
require_build_tools
runtime_safety_assert_no_host_boot_mutation "verify_squashfs_layout" || die "safety check"

if [[ $# -gt 0 ]]; then
  RUNTIME_SQUASHFS="$(readlink -f "$1")"
fi

log "=== Verify runtime.squashfs layout (strict) ==="
log "squashfs: ${RUNTIME_SQUASHFS}"

if runtime_verify_squashfs_layout_strict; then
  log "Layout check: OK"
  exit 0
fi

log "Layout check: FAILED"
exit 1

