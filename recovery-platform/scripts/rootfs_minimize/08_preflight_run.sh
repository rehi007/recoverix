#!/usr/bin/env bash
# Tier1 preflight runner — read-only validation pipeline (no apt purge apply).
# Usage: sudo ./08_preflight_run.sh [tier1|tier2|all]
#
# Exit codes:
#   0 = PASS only — purge may proceed (after human review)
#   1 = WARN only — default block; use FORCE_WARN=1 on 03_minimize_apply to override
#   2 = FAIL present — purge FORBIDDEN

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_DIR="$DIR"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/preflight_analyze.sh
source "${DIR}/lib/preflight_analyze.sh"
# shellcheck source=lib/exit_codes.sh
source "${DIR}/lib/exit_codes.sh"

require_root

TIER="${1:-tier1}"
SNAPSHOT_TAG="preflight-before-tier1"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
SUMMARY_TXT="${REPORT_DIR}/preflight_summary_${TS}.txt"
SUMMARY_JSON="${REPORT_DIR}/preflight_summary_${TS}.json"
STEPS_LOG="${REPORT_DIR}/preflight_steps_${TS}.log"

# Preflight: minimize writes inside rootfs (no initramfs/grub regen during gate)
export RUN_BOOTABILITY="${RUN_BOOTABILITY:-0}"
export APT_DRY_RUN=1

log "=== Preflight run (tier=${TIER}, read-only policy) ==="
log "ROOTFS=${ROOTFS_RESOLVED}"
: > "$STEPS_LOG"

_run_step() {
  local name="$1"
  shift
  log "STEP: ${name}"
  local rc=0
  set +e
  "$@"
  rc=$?
  set -u
  local kind
  kind="$(rootfs_rc_classify "$rc")"
  case "$kind" in
    pass)
      printf 'OK\t%s\texit=%s\n' "$name" "$rc" >> "$STEPS_LOG"
      ;;
    warn)
      printf 'WARN_STEP\t%s\texit=%s\n' "$name" "$rc" >> "$STEPS_LOG"
      ;;
    internal)
      printf 'OK\t%s\texit=%s\tnote=runtime_internal\n' "$name" "$rc" >> "$STEPS_LOG"
      rc=0
      ;;
    fail)
      printf 'FAIL_STEP\t%s\texit=%s\n' "$name" "$rc" >> "$STEPS_LOG"
      ;;
  esac
  return "$rc"
}

# --- 1) Snapshot (host path /recovery/build/snapshots, not rootfs mutation except read) ---
SNAP_PATH="/recovery/build/snapshots/rootfs_${SNAPSHOT_TAG}.tar.zst"
if [[ "${SKIP_SNAPSHOT:-0}" == "1" ]]; then
  log "SKIP_SNAPSHOT=1 — snapshot step skipped"
  printf 'SKIP\tsnapshot\n' >> "$STEPS_LOG"
elif [[ -f "$SNAP_PATH" ]]; then
  snap_age=$(( $(date +%s) - $(stat -c %Y "$SNAP_PATH" 2>/dev/null || echo 0) ))
  if [[ $snap_age -lt 86400 ]]; then
    log "NOTICE: recent snapshot exists (${SNAP_PATH}, age ${snap_age}s) — skipping duplicate (set SKIP_SNAPSHOT=0 to force)"
    printf 'SKIP\tsnapshot\trecent %s\n' "$SNAP_PATH" >> "$STEPS_LOG"
  else
    log "Existing snapshot is old (${snap_age}s) — creating fresh ${SNAPSHOT_TAG}"
    _run_step "06_snapshot" "${DIR}/06_snapshot_rootfs.sh" "$SNAPSHOT_TAG" || true
  fi
else
  log "Creating snapshot ${SNAPSHOT_TAG}"
  _run_step "06_snapshot" "${DIR}/06_snapshot_rootfs.sh" "$SNAPSHOT_TAG" || true
fi

# --- 2–4) Analysis pipeline (non-fatal step failures; gate log required) ---
_run_step "01_analyze" "${DIR}/01_analyze_rootfs.sh" || true
_run_step "02_purge_plan" "${DIR}/02_generate_purge_plan.sh" "$TIER" || true

if _run_step "07_safety_gate" "${DIR}/07_pre_purge_safety_gate.sh" "$TIER"; then
  :
else
  log "07_pre_purge_safety_gate failed (continuing to analyze latest log)"
fi

# --- 5) Analyze latest gate log ---
GATE_LOG="$(find_latest_gate_log "$TIER" "$REPORT_DIR")"
if [[ -z "$GATE_LOG" || ! -f "$GATE_LOG" ]]; then
  die "no pre_purge_gate log found under ${REPORT_DIR} — run 07 manually"
fi
log "Analyzing gate log: ${GATE_LOG}"

analyze_gate_log "$GATE_LOG" "$SUMMARY_TXT" "$SUMMARY_JSON" "$TIER"

# Pipeline step notes: only when gate validation did not allow purge
if grep -q '^FAIL_STEP' "$STEPS_LOG" 2>/dev/null; then
  if [[ "${PREFLIGHT_PURGE_ALLOWED}" != "true" ]] || [[ "${PREFLIGHT_EXIT_CODE:-0}" -ne 0 ]]; then
    {
      echo ""
      echo "=== Pipeline step failures ==="
      grep '^FAIL_STEP' "$STEPS_LOG" || true
      echo "조치: 실패한 단계 스크립트 로그를 확인하고 수정 후 preflight 재실행"
    } >> "$SUMMARY_TXT"
    if [[ "${PREFLIGHT_EXIT_CODE:-0}" -eq 0 ]]; then
      PREFLIGHT_EXIT_CODE=$RC_FAIL
      PREFLIGHT_PURGE_ALLOWED=false
    fi
  else
    log "NOTICE: FAIL_STEP in steps log ignored — gate validation PASS (purge_allowed=true)"
  fi
elif grep -q '^WARN_STEP' "$STEPS_LOG" 2>/dev/null && [[ "${PREFLIGHT_EXIT_CODE:-0}" -eq 0 ]]; then
  {
    echo ""
    echo "=== Pipeline step warnings (non-blocking) ==="
    grep '^WARN_STEP' "$STEPS_LOG" || true
  } >> "$SUMMARY_TXT"
fi

if [[ "${PREFLIGHT_PURGE_ALLOWED}" == "true" ]] && [[ "${PREFLIGHT_EXIT_CODE:-2}" -eq 0 ]]; then
  {
    echo ""
    echo "pipeline_overall: PASS"
  } >> "$SUMMARY_TXT"
elif grep -q 'squashfs_readiness' "$GATE_LOG" 2>/dev/null && grep '^WARN' "$GATE_LOG" | grep -q squashfs_readiness; then
  {
    echo ""
    echo "=== Rootfs integrity repair (recommended) ==="
    echo "sudo ${DIR}/09_repair_rootfs_integrity.sh"
    echo "sudo SKIP_SNAPSHOT=1 ${DIR}/08_preflight_run.sh ${TIER}"
  } >> "$SUMMARY_TXT"
fi

# Surface latest repair run status (if any)
_LATEST_REPAIR="$(ls -t "${REPORT_DIR}"/rootfs_integrity_repair_*.txt 2>/dev/null | head -1 || true)"
if [[ -n "${_LATEST_REPAIR}" && -f "${_LATEST_REPAIR}" ]]; then
  {
    echo ""
    echo "=== Last repair run ==="
    echo "report: ${_LATEST_REPAIR}"
    grep -E '^(repair_status|repair_aborted|repair_fatal_stage|repair_incomplete|broken_symlinks_remaining|recommended_exit_code):' \
      "$_LATEST_REPAIR" 2>/dev/null || true
    if grep -q 'repair_status: REPAIR_ABORTED' "$_LATEST_REPAIR" 2>/dev/null; then
      echo "REPAIR_ABORTED: fix errors in rootfs_integrity_repair_error_*.log and re-run 09"
    fi
    if grep -q 'repair_status: REPAIR_FATAL_STAGE=' "$_LATEST_REPAIR" 2>/dev/null \
       || grep -q 'repair_status: REPAIR_INCOMPLETE' "$_LATEST_REPAIR" 2>/dev/null; then
      echo "REPAIR_INCOMPLETE: complete 09_repair_rootfs_integrity.sh before purge"
    fi
    if grep -qE 'repair_fatal_stage: (host_safety|boot_critical)' "$_LATEST_REPAIR" 2>/dev/null; then
      echo "REPAIR_FATAL_STAGE: host/boot safety — do not purge until resolved"
    fi
  } >> "$SUMMARY_TXT"
fi

log "Preflight summary: ${SUMMARY_TXT}"
log "Preflight JSON:    ${SUMMARY_JSON}"
log "Steps log:           ${STEPS_LOG}"
log "PASS=${PREFLIGHT_PASS} WARN=${PREFLIGHT_WARN} FAIL=${PREFLIGHT_FAIL} purge_allowed=${PREFLIGHT_PURGE_ALLOWED}"

cat "$SUMMARY_TXT"

exit "${PREFLIGHT_EXIT_CODE:-2}"
