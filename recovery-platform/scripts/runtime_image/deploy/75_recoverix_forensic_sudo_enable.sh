#!/usr/bin/env bash
# Enable/disable Recoverix forensic limited sudo based on kernel cmdline.
# Installed as: /usr/local/sbin/recoverix-forensic-sudo-enable
set -u

STAGING="/etc/recoverix/staging/sudoers-recoverix-forensic"
ACTIVE="/etc/sudoers.d/99-recoverix-forensic"
MARKER="/run/recoverix/forensic-sudo-enabled"
LOG="${RECOVERIX_FORENSIC_SUDO_LOG:-/var/log/recoverix-forensic-sudo.log}"

forensic_log() {
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
  printf '[%s] %s\n' "$ts" "$*" >>"$LOG" 2>/dev/null || true
  printf '[recoverix-forensic-sudo] %s\n' "$*" >&2
}

forensic_cmdline_raw() {
  tr '\0' ' ' </proc/cmdline 2>/dev/null || true
}

recoverix_root_detected() {
  grep -qE '(^| )recoverix\.root=1($| )|(^| )recoverix\.root=yes($| )' <<<"$(forensic_cmdline_raw)"
}

recoverix_debug_xorg_detected() {
  grep -qE '(^| )recoverix\.debug\.xorg=1($| )' <<<"$(forensic_cmdline_raw)"
}

# Regression-isolation safe mode: recoverix.safe=1 disables every forensic
# early-boot / getty hook so the runtime can reach console autologin unimpeded.
recoverix_safe_mode_detected() {
  grep -qE '(^| )recoverix\.safe=1($| )' <<<"$(forensic_cmdline_raw)"
}

# GUI isolation boot: recoverix.isolation=1 skips forensic sudo (GDM drop-in guard).
recoverix_isolation_mode_detected() {
  grep -qE '(^| )recoverix\.isolation=1($| )' <<<"$(forensic_cmdline_raw)"
}

recoverix_forensic_cmdline_active() {
  recoverix_root_detected || recoverix_debug_xorg_detected
}

forensic_trace_cmdline() {
  local cmd
  cmd="$(forensic_cmdline_raw)"
  forensic_log "forensic cmdline detect: $(recoverix_forensic_cmdline_active && echo active || echo inactive)"
  forensic_log "cmdline_raw: ${cmd}"
  forensic_log "recoverix.root detect: $(recoverix_root_detected && echo yes || echo no)"
  forensic_log "recoverix.debug.xorg detect: $(recoverix_debug_xorg_detected && echo yes || echo no)"
}

forensic_trace_sudoers_file() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    forensic_log "sudoers file path: ${path} (missing)"
    return 1
  fi
  forensic_log "sudoers file path: ${path}"
  forensic_log "sudoers file mode/owner: $(stat -c '%a %U:%G' "$path" 2>/dev/null || echo unknown)"
  if visudo -cf "$path" >/dev/null 2>&1; then
    forensic_log "visudo validation result: PASS (${path})"
    return 0
  fi
  forensic_log "visudo validation result: FAIL (${path})"
  visudo -cf "$path" 2>&1 | while IFS= read -r line; do forensic_log "visudo: ${line}"; done
  return 1
}

recoverix_forensic_sudo_run_as_recoverix() {
  local cmd="$1"
  if command -v runuser >/dev/null 2>&1; then
    runuser -u recoverix -- /bin/sh -c "$cmd"
    return $?
  fi
  su - recoverix -s /bin/bash -c "$cmd"
}

recoverix_forensic_sudo_runtime_test_sudo_cmd() {
  if [[ -x /usr/bin/true ]]; then
    printf 'sudo -n /usr/bin/true'
    return 0
  fi
  if [[ -x /bin/true ]]; then
    printf 'sudo -n /bin/true'
    return 0
  fi
  printf 'sudo -n /usr/bin/journalctl --version'
}

recoverix_forensic_sudo_verify() {
  local out rc=0 cmd wrap_cmd sudo_cmd failures=0

  if ! recoverix_forensic_cmdline_active; then
    forensic_log "forensic sudo runtime test: SKIP (non-forensic cmdline)"
    return 0
  fi

  if [[ ! -f "$ACTIVE" ]]; then
    forensic_log "forensic sudo runtime test: FAIL (missing ${ACTIVE})"
    return 1
  fi

  if ! id recoverix >/dev/null 2>&1; then
    forensic_log "forensic sudo runtime test: FAIL (recoverix user missing)"
    return 1
  fi

  sudo_cmd="$(recoverix_forensic_sudo_runtime_test_sudo_cmd)"
  if command -v runuser >/dev/null 2>&1; then
    wrap_cmd="runuser -u recoverix -- ${sudo_cmd}"
  else
    wrap_cmd="su - recoverix -c '${sudo_cmd}'"
  fi
  forensic_log "forensic_sudo_runtime_test_cmd: ${wrap_cmd}"
  set +e
  out="$(recoverix_forensic_sudo_run_as_recoverix "$sudo_cmd" 2>&1)"
  rc=$?
  if [[ $rc -ne 0 ]] && [[ "$sudo_cmd" == 'sudo -n /usr/bin/true' ]] && [[ -x /bin/true ]]; then
    sudo_cmd='sudo -n /bin/true'
    forensic_log "forensic_sudo_runtime_test_cmd: fallback ${sudo_cmd}"
    out="$(recoverix_forensic_sudo_run_as_recoverix "$sudo_cmd" 2>&1)"
    rc=$?
  fi
  set -e
  forensic_log "forensic_sudo_runtime_test_rc: ${rc}"
  if [[ $rc -ne 0 ]] || grep -qiE 'a password is required|sudo:.*password|^\[sudo\] password|not allowed' <<<"$out"; then
    forensic_log "forensic_sudo_runtime_test: FAIL"
    while IFS= read -r line; do forensic_log "  ${line}"; done <<<"$out"
    failures=$((failures + 1))
  else
    forensic_log "forensic_sudo_runtime_test: PASS"
  fi

  sudo_cmd='sudo -n /usr/bin/journalctl --version'
  if command -v runuser >/dev/null 2>&1; then
    wrap_cmd="runuser -u recoverix -- ${sudo_cmd}"
  else
    wrap_cmd="su - recoverix -c '${sudo_cmd}'"
  fi
  forensic_log "forensic_sudo_allowed_cmd_test_cmd: ${wrap_cmd}"
  set +e
  out="$(recoverix_forensic_sudo_run_as_recoverix "$sudo_cmd" 2>&1)"
  rc=$?
  set -e
  forensic_log "forensic_sudo_allowed_cmd_test_rc: ${rc}"
  if [[ $rc -ne 0 ]] || grep -qiE 'a password is required|sudo:.*password|^\[sudo\] password|not allowed' <<<"$out"; then
    forensic_log "forensic_sudo_allowed_cmd_test: FAIL"
    while IFS= read -r line; do forensic_log "  ${line}"; done <<<"$out"
    failures=$((failures + 1))
  else
    forensic_log "forensic_sudo_allowed_cmd_test: PASS"
  fi

  [[ $failures -eq 0 ]] || return 1
  return 0
}

recoverix_forensic_sudo_enable() {
  mkdir -p "$(dirname "$LOG")" "$(dirname "$MARKER")" /etc/sudoers.d /run/recoverix 2>/dev/null || true
  : >>"$LOG" 2>/dev/null || LOG="/run/recoverix/forensic-sudo.log"

  forensic_log "=== recoverix-forensic-sudo-enable start ==="
  forensic_trace_cmdline

  if recoverix_safe_mode_detected; then
    rm -f "$ACTIVE" "$MARKER" 2>/dev/null || true
    forensic_log "forensic sudo activation: SKIP (recoverix.safe=1 regression-isolation mode)"
    return 0
  fi

  if recoverix_isolation_mode_detected; then
    rm -f "$ACTIVE" "$MARKER" 2>/dev/null || true
    forensic_log "forensic sudo activation: SKIP (recoverix.isolation=1 GUI isolation mode)"
    return 0
  fi

  if ! recoverix_forensic_cmdline_active; then
    rm -f "$ACTIVE" "$MARKER" 2>/dev/null || true
    forensic_log "forensic sudo activation: SKIP (non-forensic boot)"
    return 0
  fi

  if [[ ! -f "$STAGING" ]]; then
    forensic_log "FAIL forensic sudo activation: missing staging ${STAGING}"
    return 1
  fi

  forensic_trace_sudoers_file "$STAGING" || true

  if ! install -m 0440 -o root -g root "$STAGING" "$ACTIVE"; then
    forensic_log "FAIL forensic sudo activation: install to ${ACTIVE} failed"
    return 1
  fi

  chmod 0440 "$ACTIVE" 2>/dev/null || true
  chown root:root "$ACTIVE" 2>/dev/null || true

  if ! forensic_trace_sudoers_file "$ACTIVE"; then
    rm -f "$ACTIVE" 2>/dev/null || true
    forensic_log "FAIL forensic sudo activation: visudo rejected active policy"
    return 1
  fi

  forensic_log "/etc/sudoers.d/99-recoverix-forensic exists: yes"
  forensic_log "parsed successfully: yes"

  if ! recoverix_forensic_sudo_verify; then
    forensic_log "FAIL forensic sudo activation"
    return 1
  fi

  date -u +%Y%m%dT%H%M%SZ >"$MARKER" 2>/dev/null || true
  forensic_log "forensic sudo activation: PASS (marker=${MARKER})"
  return 0
}

recoverix_forensic_sudo_disable() {
  rm -f "$ACTIVE" "$MARKER" 2>/dev/null || true
  forensic_log "forensic sudo disabled (active policy removed)"
}

recoverix_forensic_sudo_selftest() {
  mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
  : >>"$LOG" 2>/dev/null || true
  forensic_log "=== recoverix-forensic-sudo selftest ==="
  forensic_trace_cmdline
  if recoverix_safe_mode_detected; then
    forensic_log "selftest: SKIP (recoverix.safe=1 regression-isolation mode)"
    return 0
  fi
  if recoverix_isolation_mode_detected; then
    forensic_log "selftest: SKIP (recoverix.isolation=1 GUI isolation mode)"
    return 0
  fi
  if ! recoverix_forensic_cmdline_active; then
    forensic_log "selftest: SKIP (non-forensic)"
    return 0
  fi
  if recoverix_forensic_sudo_verify; then
    forensic_log "selftest: PASS"
    return 0
  fi
  forensic_log "FAIL forensic sudo activation (selftest)"
  return 1
}

recoverix_forensic_sudo_status() {
  forensic_trace_cmdline
  if [[ -f "$ACTIVE" ]]; then
    forensic_trace_sudoers_file "$ACTIVE" || true
    echo "forensic_sudo: active (${ACTIVE})"
  else
    echo "forensic_sudo: inactive"
  fi
  if [[ -f "$MARKER" ]]; then
    echo "forensic_sudo_marker: $(cat "$MARKER" 2>/dev/null || echo unknown)"
  fi
  if command -v systemctl >/dev/null 2>&1; then
    systemctl is-enabled recoverix-forensic-sudo.service 2>/dev/null | sed 's/^/systemctl_enabled: /' || true
    systemctl show recoverix-forensic-sudo.service -p ActiveState,SubState,Result,ConditionResult 2>/dev/null | sed 's/^/systemctl: /' || true
  fi
}

case "${1:-start}" in
  start) recoverix_forensic_sudo_enable ;;
  stop) recoverix_forensic_sudo_disable ;;
  selftest) recoverix_forensic_sudo_selftest ;;
  status) recoverix_forensic_sudo_status ;;
  *)
    echo "usage: recoverix-forensic-sudo-enable {start|stop|selftest|status}" >&2
    exit 2
    ;;
esac
