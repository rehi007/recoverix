#!/usr/bin/env bash
# Installed as: /usr/local/sbin/recoverix-boot-diagnostics
# Collect boot and GUI startup timing after the runtime has reached the UI path.
# This script is diagnostic-only. It writes logs under the Recovery Linux boot
# partition when possible and falls back to volatile logs if persistent storage
# is not writable.
set -u

BOOT_MOUNT="${RECOVERIX_BOOT_DIAG_BOOT_MOUNT:-/run/recoverix-boot}"
PRIMARY_BASE="${RECOVERIX_BOOT_DIAG_PRIMARY_BASE:-${BOOT_MOUNT}/boot/recoverix/boot-diagnostics}"
FALLBACK_BASE="${RECOVERIX_BOOT_DIAG_FALLBACK_BASE:-/var/log/recoverix/boot-diagnostics}"
DELAY_SEC="${RECOVERIX_BOOT_DIAG_DELAY_SEC:-35}"
KEEP_RUNS="${RECOVERIX_BOOT_DIAG_KEEP_RUNS:-8}"

BOOT_REMOUNTED_RW=0
OUT_BASE=""
OUT_DIR=""
HOST_OUT_DIR=""
LOG_FILE=""

now_utc() {
  date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date 2>/dev/null || printf 'unknown-time'
}

safe_name_stamp() {
  date -u +%Y%m%dT%H%M%SZ 2>/dev/null || date +%Y%m%dT%H%M%S 2>/dev/null || printf 'unknown'
}

log_line() {
  local msg="$*"
  if [[ -n "${LOG_FILE}" ]]; then
    printf '%s %s\n' "$(now_utc)" "$msg" >>"${LOG_FILE}" 2>/dev/null || true
  fi
}

host_visible_path() {
  local path="$1"
  case "${path}" in
    "${BOOT_MOUNT}/boot/recoverix/boot-diagnostics/"*)
      printf '/boot/recoverix/boot-diagnostics/%s' "${path##*/}"
      ;;
    "${BOOT_MOUNT}/boot/recoverix/boot-diagnostics")
      printf '/boot/recoverix/boot-diagnostics'
      ;;
    *)
      printf '%s' "${path}"
      ;;
  esac
}

cleanup() {
  sync 2>/dev/null || true
  if [[ "${BOOT_REMOUNTED_RW}" == "1" ]] && mountpoint -q "${BOOT_MOUNT}" 2>/dev/null; then
    mount -o remount,ro "${BOOT_MOUNT}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

try_writable_dir() {
  local dir="$1"
  mkdir -p "${dir}" 2>/dev/null || return 1
  if touch "${dir}/.write-test" >/dev/null 2>&1; then
    rm -f "${dir}/.write-test" 2>/dev/null || true
    printf '%s' "${dir}"
    return 0
  fi
  return 1
}

prepare_persistent_base() {
  if mountpoint -q "${BOOT_MOUNT}" 2>/dev/null; then
    if ! try_writable_dir "${PRIMARY_BASE}" >/dev/null; then
      mount -o remount,rw "${BOOT_MOUNT}" 2>/dev/null && BOOT_REMOUNTED_RW=1 || true
    fi
    if try_writable_dir "${PRIMARY_BASE}"; then
      return 0
    fi
  fi

  if try_writable_dir "/boot/recoverix/boot-diagnostics"; then
    return 0
  fi

  if try_writable_dir "${FALLBACK_BASE}"; then
    return 0
  fi

  try_writable_dir "/tmp/recoverix-boot-diagnostics"
}

section() {
  printf '\n===== %s =====\n' "$1" >>"${LOG_FILE}" 2>&1 || true
}

run_cmd() {
  local title="$1" timeout_sec="$2"
  shift 2
  section "${title}"
  {
    printf 'cmd:'
    printf ' %q' "$@"
    printf '\n'
    if command -v timeout >/dev/null 2>&1; then
      timeout "${timeout_sec}" "$@" 2>&1 || printf '(exit %s)\n' "$?"
    else
      "$@" 2>&1 || printf '(exit %s)\n' "$?"
    fi
  } >>"${LOG_FILE}" 2>&1 || true
}

run_shell() {
  local title="$1" timeout_sec="$2" snippet="$3"
  section "${title}"
  {
    printf 'cmd: %s\n' "${snippet}"
    if command -v timeout >/dev/null 2>&1; then
      timeout "${timeout_sec}" bash -lc "${snippet}" 2>&1 || printf '(exit %s)\n' "$?"
    else
      bash -lc "${snippet}" 2>&1 || printf '(exit %s)\n' "$?"
    fi
  } >>"${LOG_FILE}" 2>&1 || true
}

copy_if_present() {
  local src="$1"
  local dest_dir="${OUT_DIR}/files"
  [[ -e "${src}" ]] || return 0
  mkdir -p "${dest_dir}" 2>/dev/null || return 0
  local dest_name
  dest_name="$(printf '%s' "${src}" | sed 's#^/##; s#[^A-Za-z0-9._-]#_#g')"
  if [[ -d "${src}" ]]; then
    tar -C / -czf "${dest_dir}/${dest_name}.tar.gz" "${src#/}" >/dev/null 2>&1 || true
  else
    cp -a "${src}" "${dest_dir}/${dest_name}" 2>/dev/null || true
  fi
}

write_command_file() {
  local file="$1" timeout_sec="$2"
  shift 2
  {
    printf 'cmd:'
    printf ' %q' "$@"
    printf '\n'
    if command -v timeout >/dev/null 2>&1; then
      timeout "${timeout_sec}" "$@" 2>&1 || printf '(exit %s)\n' "$?"
    else
      "$@" 2>&1 || printf '(exit %s)\n' "$?"
    fi
  } >"${OUT_DIR}/${file}" 2>&1 || true
}

prune_old_runs() {
  local base="$1" keep="$2"
  [[ "${keep}" =~ ^[0-9]+$ ]] || keep=8
  [[ "${keep}" -gt 0 ]] || keep=8
  find "${base}" -mindepth 1 -maxdepth 1 -type d -name '20*T*Z' -printf '%T@ %p\n' 2>/dev/null \
    | sort -n \
    | awk -v keep="${keep}" 'BEGIN { n=0 } { paths[++n]=$2 } END { for (i=1; i<=n-keep; i++) print paths[i] }' \
    | while IFS= read -r old_dir; do
        [[ -n "${old_dir}" && "${old_dir}" == "${base}/"* ]] || continue
        rm -rf "${old_dir}" 2>/dev/null || true
      done
}

wait_for_systemd_analyze_ready() {
  local i
  for i in $(seq 1 20); do
    if command -v systemd-analyze >/dev/null 2>&1 && timeout 5 systemd-analyze >/dev/null 2>&1; then
      log_line "systemd-analyze ready after ${i}s"
      return 0
    fi
    sleep 1 2>/dev/null || true
  done
  log_line "systemd-analyze still not ready after wait window"
  return 1
}

main() {
  local stamp
  stamp="$(safe_name_stamp)"
  OUT_BASE="$(prepare_persistent_base || true)"
  if [[ -z "${OUT_BASE}" ]]; then
    exit 0
  fi

  OUT_DIR="${OUT_BASE}/${stamp}"
  HOST_OUT_DIR="$(host_visible_path "${OUT_DIR}")"
  mkdir -p "${OUT_DIR}" 2>/dev/null || exit 0
  LOG_FILE="${OUT_DIR}/summary.txt"

  {
    printf 'Recoverix boot diagnostics\n'
    printf 'started_at: %s\n' "$(now_utc)"
    printf 'delay_sec: %s\n' "${DELAY_SEC}"
    printf 'output_dir: %s\n' "${OUT_DIR}"
    printf 'host_output_dir: %s\n' "${HOST_OUT_DIR}"
    printf 'cmdline: %s\n' "$(tr '\0' ' ' </proc/cmdline 2>/dev/null || true)"
  } >"${LOG_FILE}" 2>/dev/null || true

  ln -sfn "${stamp}" "${OUT_BASE}/latest" 2>/dev/null || true
  printf '%s\n' "${HOST_OUT_DIR}" >"${OUT_BASE}/latest.path" 2>/dev/null || true

  log_line "waiting before collection"
  if [[ "${DELAY_SEC}" =~ ^[0-9]+$ ]] && [[ "${DELAY_SEC}" -gt 0 ]]; then
    sleep "${DELAY_SEC}" 2>/dev/null || true
  fi
  wait_for_systemd_analyze_ready || true
  log_line "collection started"

  run_cmd "systemd-analyze time" 20 systemd-analyze
  run_cmd "systemd-analyze blame" 25 systemd-analyze blame
  run_cmd "systemd-analyze critical-chain" 25 systemd-analyze critical-chain
  run_cmd "systemctl failed units" 15 systemctl --failed --no-pager
  run_cmd "systemctl recoverix units" 20 systemctl list-units 'recoverix*' --all --no-pager
  run_cmd "systemctl gui launch status" 20 systemctl status recoverix-gui-launch.service --no-pager
  run_cmd "systemctl boot diagnostics status" 10 systemctl status recoverix-boot-diagnostics.service --no-pager
  run_cmd "systemctl default target" 10 systemctl get-default
  run_cmd "findmnt" 15 findmnt
  run_cmd "lsblk" 15 lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS
  run_cmd "ps elapsed" 15 ps -eo pid,ppid,stat,etimes,cmd --sort=etimes
  run_cmd "kernel cmdline" 5 cat /proc/cmdline
  run_cmd "recoverix boot dir" 10 ls -la /run/recoverix-boot/boot/recoverix
  run_cmd "tmp recoverix gui dir" 10 ls -la /tmp/recoverix-gui
  run_shell "journal recoverix gui launch" 25 "journalctl -b -u recoverix-gui-launch.service --no-pager -o short-monotonic"
  run_shell "journal recoverix boot diagnostics" 15 "journalctl -b -u recoverix-boot-diagnostics.service --no-pager -o short-monotonic"
  run_shell "journal xorg/openbox/recoverix snippets" 25 "journalctl -b --no-pager -o short-monotonic | grep -Ei 'recoverix|xorg|xinit|openbox|gtk|gdk|display|drm|tty1' || true"

  write_command_file "journal-current-boot.log" 35 journalctl -b --no-pager -o short-monotonic
  write_command_file "dmesg.log" 20 dmesg -T
  if command -v gzip >/dev/null 2>&1; then
    gzip -f "${OUT_DIR}/journal-current-boot.log" "${OUT_DIR}/dmesg.log" 2>/dev/null || true
  fi

  if command -v systemd-analyze >/dev/null 2>&1; then
    timeout 30 systemd-analyze plot >"${OUT_DIR}/systemd-analyze-plot.svg" 2>"${OUT_DIR}/systemd-analyze-plot.err" || true
  fi

  copy_if_present /tmp/recoverix-gui-service.log
  copy_if_present /tmp/recoverix-gui-session.log
  copy_if_present /tmp/recoverix-gui
  copy_if_present /var/log/Xorg.0.log
  copy_if_present /var/log/recoverix-rootfs-debug.log
  copy_if_present /recovery-rootfs-debug.log
  copy_if_present /run/recoverix

  {
    printf '\nfinished_at: %s\n' "$(now_utc)"
    printf 'persistent_target: %s\n' "${OUT_BASE}"
    printf 'boot_mount_remounted_rw: %s\n' "${BOOT_REMOUNTED_RW}"
  } >>"${LOG_FILE}" 2>/dev/null || true

  prune_old_runs "${OUT_BASE}" "${KEEP_RUNS}"
  sync 2>/dev/null || true
}

main "$@"
