#!/usr/bin/env bash
# Verify recoverix-backup-finalize-check in rootfs and squashfs (read-only).
# Usage: sudo ./14_verify_backup_finalize_cli.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
export RUNTIME_IMAGE_DIR="${DIR}"

# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/integrity_check.sh
source "${DIR}/lib/integrity_check.sh"

trap 'log_fail "verify aborted at line ${LINENO} (exit $?)"; exit 1' ERR

require_root
require_build_tools

FAILURES=0

log "=========================================="
log " recoverix-backup-finalize-check verify"
log " ROOTFS=${ROOTFS_RESOLVED}"
log " SQUASHFS=${RUNTIME_SQUASHFS}"
log "=========================================="

if [[ -f "$(recoverix_backup_finalize_check_path)" ]]; then
  if runtime_assert_rootfs_recoverix_backup_finalize_check; then
    :
  else
    FAILURES=$((FAILURES + 1))
  fi
else
  log_fail "rootfs missing $(recoverix_backup_finalize_check_path)"
  log "INFO: run: sudo ${DIR}/10_build_squashfs.sh"
  FAILURES=$((FAILURES + 1))
fi

if [[ -f "${RUNTIME_SQUASHFS}" ]]; then
  if runtime_assert_squashfs_recoverix_backup_finalize_check; then
    :
  else
    FAILURES=$((FAILURES + 1))
  fi
else
  log_fail "squashfs missing: ${RUNTIME_SQUASHFS}"
  FAILURES=$((FAILURES + 1))
fi

if [[ $FAILURES -gt 0 ]]; then
  log_fail "verification failed (${FAILURES} check(s))"
  exit 1
fi

log_pass "all recoverix-backup-finalize-check verification checks passed"
exit 0
