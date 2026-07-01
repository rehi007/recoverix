#!/usr/bin/env bash
# Repair rootfs integrity before Tier1 purge (no apt purge).
# Usage:
#   sudo ./09_repair_rootfs_integrity.sh
#   sudo DRY_RUN=1 ./09_repair_rootfs_integrity.sh
#   sudo DEBUG=1 ./09_repair_rootfs_integrity.sh
#   sudo DRY_RUN=1 DEBUG=1 ./09_repair_rootfs_integrity.sh
#
# Exit: 0=success, 1=warnings, 2=fatal, 3=host safety, 4=boot critical

# NOTE: no `set -e` in main — stages are non-fatal except host/boot checks.
set -Euo pipefail

if [[ "${DEBUG:-0}" == "1" ]]; then
  PS4='+ [${BASH_SOURCE##*/}:${LINENO}] '
  set -x
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_DIR="$DIR"

# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/exit_codes.sh
source "${DIR}/lib/exit_codes.sh"
# shellcheck source=lib/symlink_classifier.sh
source "${DIR}/lib/symlink_classifier.sh"
# shellcheck source=lib/repair_integrity.sh
source "${DIR}/lib/repair_integrity.sh"

# --- Repair session state ---
REPAIR_TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPAIR_STAGE="init"
REPAIR_START_EPOCH="$(date +%s)"
REPAIR_EXIT_CODE="${REPAIR_RC_SUCCESS}"
REPAIR_ABORTED=0
REPAIR_FATAL_STAGE=""
REPAIR_INCOMPLETE=0
DRY_RUN="${DRY_RUN:-0}"
DEBUG="${DEBUG:-0}"

REPORT_DIR="${REPORT_DIR:-/recovery/build/reports/rootfs-minimize}"
mkdir -p "$REPORT_DIR" || true

REPAIR_REPORT="${REPORT_DIR}/broken_symlink_repair_${REPAIR_TS}.txt"
DEBUG_REPORT="${REPORT_DIR}/broken_symlink_debug_${REPAIR_TS}.txt"
CLASSIFICATION_REPORT="${REPORT_DIR}/broken_symlink_classification_${REPAIR_TS}.txt"
SUMMARY_REPORT="${REPORT_DIR}/rootfs_integrity_repair_${REPAIR_TS}.txt"
ERROR_LOG="${REPORT_DIR}/rootfs_integrity_repair_error_${REPAIR_TS}.log"

repair_log() { printf '[rootfs-minimize] %s\n' "$*"; }

repair_stage_begin() {
  REPAIR_STAGE="$1"
  repair_log "[STAGE] ${REPAIR_STAGE} BEGIN"
}

repair_stage_end() {
  local rc="${1:-0}"
  repair_log "[STAGE] ${REPAIR_STAGE} END (rc=${rc})"
}

repair_on_error() {
  local line="$1"
  local cmd="$2"
  local code="$3"
  REPAIR_ABORTED=1
  REPAIR_FATAL_STAGE="${REPAIR_STAGE}"
  REPAIR_STAT_ERRORS=$((REPAIR_STAT_ERRORS + 1))
  {
    echo "=== REPAIR FATAL/TRAP ERROR ==="
    echo "timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "stage: ${REPAIR_STAGE}"
    echo "line: ${line}"
    echo "command: ${cmd}"
    echo "exit_code: ${code}"
    echo "function_stack:"
    local i=0
    while caller $i; do
      i=$((i + 1))
    done 2>/dev/null || true
    echo "ROOTFS: ${ROOTFS_RESOLVED:-unset}"
  } >> "$ERROR_LOG"
  repair_log "ERROR at stage=${REPAIR_STAGE} line=${line} rc=${code} (see ${ERROR_LOG})"
}

trap 'repair_on_error ${LINENO} "$BASH_COMMAND" "$?"' ERR

repair_init_reports() {
  repair_stage_begin "init_reports"
  {
    echo "=== Recoverix Rootfs Integrity Repair ==="
    echo "timestamp: ${REPAIR_TS}"
    echo "rootfs: ${ROOTFS_RESOLVED}"
    echo "dry_run: ${DRY_RUN}"
    echo "debug: ${DEBUG}"
    echo
  } > "$SUMMARY_REPORT" 2>/dev/null || echo "WARN: cannot write ${SUMMARY_REPORT}" >&2

  echo "=== Broken symlink repair log ===" > "$REPAIR_REPORT" 2>/dev/null || true
  echo "=== Broken symlink debug trace ===" > "$DEBUG_REPORT" 2>/dev/null || true
  : > "$ERROR_LOG" 2>/dev/null || true
  repair_stage_end 0
}

repair_write_final_summary() {
  local end_epoch elapsed warns fails remaining
  end_epoch="$(date +%s)"
  elapsed=$((end_epoch - REPAIR_START_EPOCH))
  remaining="$(count_broken_symlinks 2>/dev/null || echo 0)"
  warns="${REPAIR_STAT_WARNINGS}"
  fails="${REPAIR_STAT_ERRORS}"

  {
    echo ""
    echo "=== FINAL SUMMARY ==="
    echo "stage_last: ${REPAIR_STAGE}"
    echo "repair_aborted: ${REPAIR_ABORTED}"
    echo "repair_fatal_stage: ${REPAIR_FATAL_STAGE:-none}"
    echo "repair_incomplete: ${REPAIR_INCOMPLETE}"
    echo "elapsed_seconds: ${elapsed}"
    echo "total_scanned: ${REPAIR_STAT_SCANNED}"
    echo "repaired_removed: ${REPAIR_STAT_REMOVED}"
    echo "protected: ${REPAIR_STAT_PROTECTED}"
    echo "skipped_manual_review: ${REPAIR_STAT_MANUAL}"
    echo "warnings: ${warns}"
    echo "errors: ${fails}"
    echo "broken_symlinks_remaining: ${remaining}"
    echo "dry_run: ${DRY_RUN}"
    echo "reports:"
    echo "  summary: ${SUMMARY_REPORT}"
    echo "  symlink_repair: ${REPAIR_REPORT}"
    echo "  symlink_debug: ${DEBUG_REPORT}"
    echo "  error_log: ${ERROR_LOG}"
    echo "recommended_exit_code: ${REPAIR_EXIT_CODE}"
    if [[ "${REPAIR_EXIT_CODE}" -eq "${REPAIR_RC_SUCCESS}" && "${remaining}" -eq 0 && "${REPAIR_ABORTED}" -eq 0 ]]; then
      echo "repair_status: SUCCESS"
    elif [[ "${REPAIR_ABORTED}" -eq 1 ]]; then
      echo "repair_status: REPAIR_ABORTED"
    elif [[ "${REPAIR_INCOMPLETE}" -eq 1 ]]; then
      echo "repair_status: REPAIR_INCOMPLETE"
    elif [[ "${REPAIR_FATAL_STAGE}" != "" ]]; then
      echo "repair_status: REPAIR_FATAL_STAGE=${REPAIR_FATAL_STAGE}"
    else
      echo "repair_status: REPAIR_WARNINGS"
    fi
    echo ""
    echo "Next: sudo SKIP_SNAPSHOT=1 ${DIR}/08_preflight_run.sh tier1"
  } >> "$SUMMARY_REPORT" 2>/dev/null || true

  repair_log "Final summary: ${SUMMARY_REPORT}"
}

trap 'repair_write_final_summary' EXIT

repair_run_stage() {
  local stage="$1"
  shift
  repair_stage_begin "$stage"
  local rc=0
  set +e
  "$@"
  rc=$?
  set -u
  if [[ $rc -ne 0 ]]; then
    REPAIR_INCOMPLETE=1
    REPAIR_STAT_WARNINGS=$((REPAIR_STAT_WARNINGS + 1))
    printf 'WARN\tstage_%s\tstage exited rc=%s\n' "$stage" "$rc" >> "$SUMMARY_REPORT" 2>/dev/null || true
  fi
  repair_stage_end "$rc"
  return 0
}

# --- Fatal prechecks (only these stop the pipeline) ---
repair_fatal_validate_rootfs() {
  repair_stage_begin "validate_rootfs"
  if [[ ! -d "${ROOTFS_RESOLVED}" ]]; then
    repair_log "FATAL: ROOTFS does not exist: ${ROOTFS_RESOLVED}"
    REPAIR_EXIT_CODE=$REPAIR_RC_FATAL
    REPAIR_FATAL_STAGE="validate_rootfs"
    repair_stage_end 2
    exit "$REPAIR_EXIT_CODE"
  fi
  if [[ ! -f "${ROOTFS_RESOLVED}/etc/os-release" ]]; then
    repair_log "FATAL: not a rootfs (missing etc/os-release)"
    REPAIR_EXIT_CODE=$REPAIR_RC_FATAL
    REPAIR_FATAL_STAGE="validate_rootfs"
    repair_stage_end 2
    exit "$REPAIR_EXIT_CODE"
  fi
  repair_stage_end 0
}

repair_fatal_host_safety() {
  repair_stage_begin "host_safety"
  set +e
  (
    validate_host_safety_lock "$ROOTFS_RESOLVED" "$SUMMARY_REPORT"
  )
  local rc=$?
  set -u
  if [[ $rc -ne 0 ]]; then
    repair_log "FATAL: host safety lock failed (rc=${rc})"
    REPAIR_EXIT_CODE=$REPAIR_RC_HOST_SAFETY
    REPAIR_FATAL_STAGE="host_safety"
    printf 'FAIL\thost_safety\tHOST_SAFETY_VIOLATION\n' >> "$SUMMARY_REPORT"
    repair_stage_end "$rc"
    exit "$REPAIR_EXIT_CODE"
  fi
  printf 'PASS\thost_safety\tROOTFS isolated from host\n' >> "$SUMMARY_REPORT"
  repair_stage_end 0
}

repair_fatal_boot_critical() {
  repair_stage_begin "boot_critical_check"
  set +e
  repair_validate_boot_critical_artifacts
  local rc=$?
  set -u
  if [[ $rc -eq "$REPAIR_RC_BOOT_CRITICAL" ]]; then
    repair_log "FATAL: boot critical artifacts missing for KEEP_KERNEL_FLAVOR=${KEEP_KERNEL_FLAVOR}"
    REPAIR_EXIT_CODE=$REPAIR_RC_BOOT_CRITICAL
    REPAIR_FATAL_STAGE="boot_critical_check"
    printf 'FAIL\tboot_critical\tBOOT_CRITICAL_CORRUPTION\n' >> "$SUMMARY_REPORT"
    repair_stage_end "$rc"
    exit "$REPAIR_EXIT_CODE"
  fi
  printf 'PASS\tboot_critical\tkeep kernel artifacts present\n' >> "$SUMMARY_REPORT"
  repair_stage_end 0
}

repair_stage_mount_chroot() {
  set +e
  repair_mount_chroot
  local rc=$?
  set -u
  if [[ $rc -ne 0 ]]; then
    printf 'WARN\tmount_chroot\tmount partial/failed rc=%s (continuing symlink repair)\n' "$rc" >> "$SUMMARY_REPORT"
    REPAIR_STAT_WARNINGS=$((REPAIR_STAT_WARNINGS + 1))
  else
    printf 'PASS\tmount_chroot\tvirtual fs mounted under rootfs\n' >> "$SUMMARY_REPORT"
  fi
  return 0
}

repair_stage_scan_symlinks() {
  local before
  before="$(count_broken_symlinks)"
  repair_log "Broken symlinks before repair: ${before}"
  {
    echo "before_broken_symlinks: ${before}"
  } >> "$SUMMARY_REPORT"
  find_broken_symlinks > "${REPORT_DIR}/broken_symlinks_${REPAIR_TS}.txt" 2>/dev/null || true
  return 0
}

repair_stage_classify_symlinks() {
  set +e
  repair_classify_broken_symlinks "$CLASSIFICATION_REPORT"
  set -u
  symlink_write_stats_to_summary "$SUMMARY_REPORT"
  repair_log "Classification: total=${SYMLINK_STAT_TOTAL} warnable=${SYMLINK_STAT_WARNABLE} report=${CLASSIFICATION_REPORT}"
  {
    echo "classification_report: ${CLASSIFICATION_REPORT}"
    echo "before_warnable: ${SYMLINK_STAT_WARNABLE}"
  } >> "$SUMMARY_REPORT"
  return 0
}

repair_stage_repair_symlinks() {
  set +e
  repair_broken_symlinks "$REPAIR_REPORT" "$DEBUG_REPORT" "$DRY_RUN"
  set -u
  local after warnable
  after="$(count_broken_symlinks)"
  warnable="$(count_broken_symlinks_warnable)"
  repair_log "After symlink repair: removed=${REPAIR_STAT_REMOVED} kept=${REPAIR_STAT_PROTECTED} unknown_skipped=${REPAIR_STAT_SKIPPED} total=${after} warnable=${warnable}"
  symlink_write_stats_to_summary "$SUMMARY_REPORT"
  {
    echo "after_broken_symlinks: ${after}"
    echo "after_warnable: ${warnable}"
    echo "symlink_repair_report: ${REPAIR_REPORT}"
    echo "symlink_debug_report: ${DEBUG_REPORT}"
    echo "symlink_classification_report: ${CLASSIFICATION_REPORT}"
  } >> "$SUMMARY_REPORT"
  return 0
}

repair_stage_ldconfig() {
  repair_run_ldconfig "$SUMMARY_REPORT" || true
  return 0
}

repair_stage_dpkg_audit() {
  repair_run_dpkg_audit "$SUMMARY_REPORT" || true
  return 0
}

repair_stage_initramfs() {
  repair_initramfs_sanity "$SUMMARY_REPORT" || true
  return 0
}

repair_stage_post_validation() {
  local warnable total
  warnable="$(repair_post_validation "$SUMMARY_REPORT")"
  total="$(count_broken_symlinks)"
  if [[ "${warnable:-0}" -eq 0 ]]; then
    printf 'PASS\tsquashfs_readiness\tpost-repair: %s broken symlinks, 0 warnable\n' "$total" >> "$SUMMARY_REPORT"
  else
    printf 'WARN\tsquashfs_readiness\tpost-repair: %s warnable broken symlinks (%s total)\n' "$warnable" "$total" >> "$SUMMARY_REPORT"
  fi
  return 0
}

# --- Main ---
main() {
  require_root

  repair_log "=== Rootfs integrity repair (no purge) ==="
  repair_log "ROOTFS=${ROOTFS_RESOLVED}"
  repair_log "DRY_RUN=${DRY_RUN} DEBUG=${DEBUG}"
  repair_log "Reports under: ${REPORT_DIR}"

  repair_init_reports

  repair_fatal_validate_rootfs
  repair_fatal_host_safety
  repair_fatal_boot_critical

  repair_run_stage "mount_chroot" repair_stage_mount_chroot
  repair_run_stage "scan_broken_symlinks" repair_stage_scan_symlinks
  repair_run_stage "classify_symlinks" repair_stage_classify_symlinks
  repair_run_stage "repair_symlinks" repair_stage_repair_symlinks
  repair_run_stage "ldconfig" repair_stage_ldconfig
  repair_run_stage "dpkg_audit" repair_stage_dpkg_audit
  repair_run_stage "initramfs_check" repair_stage_initramfs
  repair_run_stage "gtk_validation" repair_stage_post_validation
  repair_run_stage "final_summary" true

  # Determine exit code (warnable count matters, not total broken symlinks)
  local remaining warnable
  remaining="$(count_broken_symlinks)"
  warnable="$(count_broken_symlinks_warnable)"
  if [[ "${REPAIR_ABORTED}" -eq 1 && -n "${REPAIR_FATAL_STAGE}" ]]; then
    REPAIR_EXIT_CODE=$REPAIR_RC_FATAL
  elif [[ "${REPAIR_STAT_ERRORS}" -gt 0 ]]; then
    REPAIR_EXIT_CODE=$REPAIR_RC_FATAL
  elif [[ "${REPAIR_STAT_WARNINGS}" -gt 0 || "${warnable:-0}" -gt 0 ]]; then
    REPAIR_EXIT_CODE=$REPAIR_RC_WARN
  else
    REPAIR_EXIT_CODE=$REPAIR_RC_SUCCESS
  fi

  repair_log "Repair complete exit=${REPAIR_EXIT_CODE} total_broken=${remaining} warnable=${warnable}"
  echo ""
  echo "Next: sudo SKIP_SNAPSHOT=1 ${DIR}/08_preflight_run.sh tier1"
}

main "$@"
exit "${REPAIR_EXIT_CODE:-2}"
