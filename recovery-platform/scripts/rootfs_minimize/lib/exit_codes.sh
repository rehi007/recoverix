#!/usr/bin/env bash
# Standard exit codes for rootfs_minimize scripts.
#
# 0       = PASS
# 1       = WARN
# 2       = FAIL
# 10–19   = internal recoverable warning
# 20–39   = tool/runtime internal state (non-fatal to pipeline when gate PASS)
# >=40    = hard failure

RC_PASS=0
RC_WARN=1
RC_FAIL=2
RC_INTERNAL_WARN=10
RC_RUNTIME_STATE=20
# util-linux: mount point does not exist (handled in mount_chroot_fs; kept for classify)
RC_MOUNT_MISSING=32
export RC_PASS RC_WARN RC_FAIL RC_INTERNAL_WARN RC_RUNTIME_STATE RC_MOUNT_MISSING

# Classify a subprocess exit code for preflight step logging.
# Prints: pass | warn | fail | internal
rootfs_rc_classify() {
  local rc="${1:-0}"
  case "$rc" in
    0) printf 'pass' ;;
    1) printf 'warn' ;;
    2) printf 'fail' ;;
    10|11|12|13|14|15|16|17|18|19) printf 'warn' ;;
    20|21|22|23|24|25|26|27|28|29|30|31|32|33|34|35|36|37|38|39) printf 'internal' ;;
    *) printf 'fail' ;;
  esac
}

# Map gate log FAIL/WARN counts to script exit (0/1/2).
rootfs_gate_exit_from_counts() {
  local fails="${1:-0}"
  local warns="${2:-0}"
  fails="${fails//$'\n'/}"
  warns="${warns//$'\n'/}"
  if [[ "${fails}" -gt 0 ]]; then
    return "$RC_FAIL"
  fi
  if [[ "${warns}" -gt 0 ]]; then
    return "$RC_WARN"
  fi
  return "$RC_PASS"
}

# Count PASS/WARN/FAIL/SKIP lines in a tab-separated gate log.
rootfs_count_gate_status() {
  local log="$1"
  local var_prefix="$2"
  local pass warn fail skip
  pass=$(grep -c '^PASS' "$log" 2>/dev/null) || pass=0
  warn=$(grep -c '^WARN' "$log" 2>/dev/null) || warn=0
  fail=$(grep -c '^FAIL' "$log" 2>/dev/null) || fail=0
  skip=$(grep -c '^SKIP' "$log" 2>/dev/null) || skip=0
  printf -v "${var_prefix}_PASS" '%s' "$pass"
  printf -v "${var_prefix}_WARN" '%s' "$warn"
  printf -v "${var_prefix}_FAIL" '%s' "$fail"
  printf -v "${var_prefix}_SKIP" '%s' "$skip"
}
