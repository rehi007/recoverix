#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

UUID_DEFAULT="$(findmnt -no UUID / 2>/dev/null || true)"
RECOVERY_UUID="${RECOVERY_LINUX_UUID:-${UUID_DEFAULT}}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${DIR}/logs/gui-test-cycle/${TS}"
SUMMARY="${RUN_DIR}/summary.txt"

mkdir -p "$RUN_DIR"

log() {
  printf '[gui-test-cycle] %s\n' "$*"
}

run_step() {
  local name="$1"
  shift
  local log_file="${RUN_DIR}/${name}.log"
  local rc=0

  log "STEP ${name}: $*"
  {
    printf 'step=%s\n' "$name"
    printf 'timestamp=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'command='
    printf '%q ' "$@"
    printf '\n\n'
  } >"$log_file"

  if "$@" 2>&1 | tee -a "$log_file"; then
    printf 'PASS %s %s\n' "$name" "$log_file" | tee -a "$SUMMARY"
  else
    rc=$?
    if [[ "$name" == "verify_esp" ]] && grep -q 'summary: .*FAIL=0' "$log_file" 2>/dev/null; then
      printf 'PASS %s %s (warn-only rc=%s)\n' "$name" "$log_file" "$rc" | tee -a "$SUMMARY"
      log "WARN-ONLY ${name}; accepting because verify_esp reported FAIL=0"
      return 0
    fi
    printf 'FAIL %s rc=%s %s\n' "$name" "$rc" "$log_file" | tee -a "$SUMMARY"
    log "FAILED ${name}; see ${log_file}"
    exit "$rc"
  fi
}

{
  printf 'run_timestamp=%s\n' "$TS"
  printf 'runtime_image_dir=%s\n' "$DIR"
  printf 'recovery_linux_uuid=%s\n' "${RECOVERY_UUID:-unset}"
  printf 'summary_generated_at=%s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$SUMMARY"

if [[ -z "${RECOVERY_UUID}" ]]; then
  printf 'FAIL preflight missing recovery uuid\n' | tee -a "$SUMMARY"
  log "RECOVERY_LINUX_UUID is empty and root UUID auto-detection failed"
  exit 2
fi

run_step build sudo ./00_build_runtime.sh
run_step stage_host sudo env "RECOVERY_LINUX_UUID=${RECOVERY_UUID}" ./deploy/30_stage_host_boot.sh
run_step stage_esp sudo env "RECOVERY_LINUX_UUID=${RECOVERY_UUID}" ./deploy/40_stage_esp_runtime.sh
run_step verify_esp sudo ./12_verify_esp_layout.sh

log "COMPLETE"
log "Logs: ${RUN_DIR}"
printf 'COMPLETE logs_dir=%s\n' "$RUN_DIR" | tee -a "$SUMMARY"
