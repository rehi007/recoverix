#!/usr/bin/env bash
# Shared paths and guards for runtime image build (rootfs-only writes).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/config.env"
# shellcheck source=runtime_safety.sh
source "${SCRIPT_DIR}/lib/runtime_safety.sh"
# shellcheck source=runtime_build_safety.sh
source "${SCRIPT_DIR}/lib/runtime_build_safety.sh"
# shellcheck source=runtime_package_validation.sh
source "${SCRIPT_DIR}/lib/runtime_package_validation.sh"
# shellcheck source=runtime_apt_repositories.sh
source "${SCRIPT_DIR}/lib/runtime_apt_repositories.sh"
# shellcheck source=runtime_purity.sh
source "${SCRIPT_DIR}/lib/runtime_purity.sh"
# shellcheck source=runtime_apt_install.sh
source "${SCRIPT_DIR}/lib/runtime_apt_install.sh"
# shellcheck source=runtime_kernel_packages.sh
source "${SCRIPT_DIR}/lib/runtime_kernel_packages.sh"
# shellcheck source=runtime_debootstrap.sh
source "${SCRIPT_DIR}/lib/runtime_debootstrap.sh"

log() { printf '[runtime-image] %s\n' "$*"; }
log_info() { log "INFO: $*"; }
log_warn() { printf '[runtime-image] WARN: %s\n' "$*" >&2; }
log_pass() { log "PASS: $*"; }
log_fail() { printf '[runtime-image] FAIL: %s\n' "$*" >&2; }
log_step() { log "STEP: $*"; }
die() { log_fail "$*"; exit 2; }

# Resolve ROOTFS + ensure build output directories exist (explicit errors; never silent set -e exit).
runtime_resolve_rootfs_paths() {
  log_step "resolve rootfs and build directories"
  log_info "ROOTFS=${ROOTFS}"
  log_info "RUNTIME_DIR=${RUNTIME_DIR}"
  log_info "REPORT_DIR=${REPORT_DIR}"

  if ! ROOTFS_RESOLVED="$(resolve_rootfs "$ROOTFS")"; then
    die "ROOTFS resolution failed"
  fi
  log_info "ROOTFS_RESOLVED=${ROOTFS_RESOLVED}"

  if ! mkdir -p "$RUNTIME_DIR" "$REPORT_DIR"; then
    die "cannot create build directories (check /recovery mount and permissions): RUNTIME_DIR=${RUNTIME_DIR} REPORT_DIR=${REPORT_DIR}"
  fi
  log_pass "build directories ready"
  return 0
}

runtime_chroot_mountpoint_status() {
  local path="${1:?}"
  if mountpoint -q "$path" 2>/dev/null; then
    printf 'mounted'
  elif [[ -e "$path" ]]; then
    printf 'exists_not_mountpoint'
  else
    printf 'missing'
  fi
}

runtime_chroot_mount_append_report() {
  local report="${1:-}" stage="${2:?}" target="${3:?}" rc="${4:?}" err="${5:-}"

  [[ -n "$report" ]] || return 0
  {
    echo "gui_chroot_mount_stage=${stage}"
    echo "gui_chroot_mount_target=${target}"
    echo "gui_chroot_mount_rc=${rc}"
    echo "gui_chroot_mount_error=${err}"
  } >>"$report"
}

runtime_chroot_mount_label_slug() {
  printf '%s' "${1:?}" | tr '/' '_'
}

# Single non-negative integer on stdout (first digit run, else 0).
runtime_normalize_uint() {
  local raw="${1:-}" digits

  raw="${raw//$'\n'/}"
  raw="${raw//$'\r'/}"
  raw="${raw//[[:space:]]/}"
  if [[ "$raw" =~ ^[0-9]+$ ]]; then
    printf '%s' "$raw"
    return 0
  fi
  digits="$(printf '%s' "${1:-}" | grep -Eo '[0-9]+' | head -n1 || true)"
  if [[ -z "$digits" ]]; then
    if [[ -n "${1:-}" ]]; then
      log_warn "WARN: non-numeric uint normalized to 0: ${1}"
    fi
    printf '0'
    return 0
  fi
  printf '%s' "$digits"
}

runtime_chroot_rootfs_mount_count() {
  local root="${1:?}" count

  count="$(grep -F -c -- "$root" /proc/self/mountinfo 2>/dev/null || true)"
  count="${count//$'\n'/}"
  count="${count:-0}"
  runtime_normalize_uint "$count"
}

runtime_chroot_safe_uint_delta() {
  local before="${1:?}" after="${2:?}" _warn_label="${3:-chroot mount count}"

  before="$(runtime_normalize_uint "$before")"
  after="$(runtime_normalize_uint "$after")"
  if [[ ! "$before" =~ ^[0-9]+$ || ! "$after" =~ ^[0-9]+$ ]]; then
    log_warn "WARN: ${_warn_label} delta skipped (before=${before} after=${after})"
    printf '0 0 0\n'
    return 0
  fi
  printf '%s %s %s\n' "$before" "$after" "$((after - before))"
}

runtime_chroot_mount_append_label_state_report() {
  local report="${1:-}" label="${2:?}" already_present="${3:?}" skipped="${4:?}"

  [[ -n "$report" ]] || return 0
  local slug
  slug="$(runtime_chroot_mount_label_slug "$label")"
  {
    echo "chroot_mount_already_present_${slug}=${already_present}"
    echo "chroot_mount_skipped_${slug}=${skipped}"
  } >>"$report"
}

# Log findmnt/mountinfo sample lines (never full dump).
runtime_chroot_log_mount_detail_sample() {
  local root="${1:?}" max_lines="${2:?}" source_name="${3:?}" cmd_output total logged truncated

  total=0
  logged=0
  truncated=no

  case "$source_name" in
    findmnt)
      if ! command -v findmnt >/dev/null 2>&1; then
        printf '[runtime-image] %s\n' "  findmnt not available" >&2
        printf '%s %s %s\n' "0" "0" "no"
        return 0
      fi
      cmd_output="$(findmnt -R "$root" 2>&1 || true)"
      ;;
    mountinfo)
      cmd_output="$(grep "$root" /proc/self/mountinfo 2>/dev/null || true)"
      ;;
    *)
      log_fail "unknown mount detail source: ${source_name}"
      printf '%s %s %s\n' "0" "0" "no"
      return 1
      ;;
  esac

  if [[ -z "$cmd_output" ]]; then
    printf '[runtime-image] %s\n' "  (${source_name}: no lines for ${root})" >&2
    printf '%s %s %s\n' "0" "0" "no"
    return 0
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    total=$((total + 1))
    if [[ $logged -lt $max_lines ]]; then
      printf '[runtime-image] %s\n' "  ${line}" >&2
      logged=$((logged + 1))
    fi
  done <<<"$cmd_output"

  if [[ $total -gt $logged ]]; then
    truncated=yes
    printf '[runtime-image] %s\n' "  ... truncated (${source_name}: ${total} total, logged ${logged})" >&2
  fi

  printf '%s %s %s\n' "$total" "$logged" "$truncated"
}

# Mount namespace forensics (counts + capped detail only).
runtime_log_chroot_mount_infrastructure_forensic() {
  local root="${1:?}" report="${2:-}" context="${3:-before_mount}"
  local mount_count findmnt_count rootfs_mount_count mount_max
  local max_detail_lines=50
  local findmnt_total findmnt_logged findmnt_truncated
  local mountinfo_total mountinfo_logged mountinfo_truncated
  local mountinfo_truncated_flag=no mountinfo_logged_lines=0
  local -a report_lines=()

  if [[ "$context" == *rc32* || "$context" == *enospc* ]]; then
    max_detail_lines=100
  fi

  mount_count="$(runtime_normalize_uint "$(mount 2>/dev/null | wc -l)")"
  findmnt_count="$(runtime_normalize_uint "$(findmnt 2>/dev/null | wc -l)")"
  rootfs_mount_count="$(runtime_chroot_rootfs_mount_count "$root")"
  mount_max="$(cat /proc/sys/fs/mount-max 2>/dev/null || echo unknown)"

  log "=== chroot mount infrastructure forensic (${context}) ==="
  log "mount_count=${mount_count}"
  log "findmnt_count=${findmnt_count}"
  log "rootfs_mount_count=${rootfs_mount_count}"
  log "mount_max=${mount_max}"

  report_lines+=(
    "mount_count=${mount_count}"
    "rootfs_mount_count=${rootfs_mount_count}"
    "mount_max=${mount_max}"
  )

  log "forensic: mount | wc -l = ${mount_count}"
  log "forensic: findmnt | wc -l = ${findmnt_count}"

  log "forensic: findmnt -R ${root} (count=${findmnt_count}, max_detail=${max_detail_lines})"
  read -r findmnt_total findmnt_logged findmnt_truncated < <(
    runtime_chroot_log_mount_detail_sample "$root" "$max_detail_lines" findmnt
  )

  log "forensic: grep ${root} /proc/self/mountinfo (count=${rootfs_mount_count}, max_detail=${max_detail_lines})"
  read -r mountinfo_total mountinfo_logged mountinfo_truncated < <(
    runtime_chroot_log_mount_detail_sample "$root" "$max_detail_lines" mountinfo
  )
  mountinfo_logged_lines="${mountinfo_logged:-0}"
  if [[ "${mountinfo_truncated:-no}" == yes || "${findmnt_truncated:-no}" == yes ]]; then
    mountinfo_truncated_flag=yes
  fi

  if [[ -d "${root}/dev" ]]; then
    log "forensic: stat -f ${root}/dev (bind mount target before dev)"
    stat -f "${root}/dev" 2>&1 | head -n 5 | while IFS= read -r line; do
      log "  ${line}"
    done
  else
    log "forensic: stat -f ${root}/dev — path missing"
  fi

  if [[ "$context" == *rc32* || "$context" == *enospc* ]]; then
    log "forensic: df -h (rc=32 / ENOSPC investigation — disk space vs mount limit)"
    df -h 2>&1 | head -n 20 | while IFS= read -r line; do
      log "  ${line}"
    done
    log "forensic: df -i (inode exhaustion check)"
    df -i 2>&1 | head -n 20 | while IFS= read -r line; do
      log "  ${line}"
    done
  fi

  report_lines+=(
    "mountinfo_truncated=${mountinfo_truncated_flag}"
    "mountinfo_logged_lines=${mountinfo_logged_lines}"
    "rootfs_mount_count=${rootfs_mount_count}"
    "forensic_context=${context}"
    "forensic_max_detail_lines=${max_detail_lines}"
  )

  if [[ -n "$report" ]]; then
    printf '%s\n' "${report_lines[@]}" >>"$report"
  fi
}

# Single mount attempt with forensic logging (returns mount rc). Idempotent when already mounted.
runtime_chroot_mount_attempt() {
  local root="${1:?}" stage="${2:?}" label="${3:?}"
  local dst="${root}/${label}"
  local report="${4:-}" required="${5:-1}"
  local mp_before mp_after rc=0 stderr_file err_line

  shift 5

  if [[ ! -e "$dst" ]]; then
    if ! mkdir -p "$dst" 2>/dev/null; then
      log_fail "missing mount path: ${dst} (mkdir failed)"
      runtime_chroot_mount_append_report "$report" "$stage" "$label" "127" "missing mount path: ${dst}"
      runtime_chroot_mount_append_label_state_report "$report" "$label" no no
      return 127
    fi
  fi

  mp_before="$(runtime_chroot_mountpoint_status "$dst")"
  log "mount attempt: ${label} -> ${dst}"
  log "mountpoint before: ${mp_before}"

  if mountpoint -q "$dst" 2>/dev/null; then
    log_pass "chroot mount already present: ${label} -> ${dst}"
    runtime_chroot_mount_append_report "$report" "$stage" "$label" "0" ""
    runtime_chroot_mount_append_label_state_report "$report" "$label" yes yes
    return 0
  fi

  runtime_chroot_mount_append_label_state_report "$report" "$label" no no

  if [[ "$label" == "dev" ]]; then
    runtime_log_chroot_mount_infrastructure_forensic "$root" "$report" "before_dev_bind"
    if [[ -d "${root}/dev" ]]; then
      log "forensic: stat -f ${root}/dev (immediately before bind /dev)"
      stat -f "${root}/dev" 2>&1 | head -n 5 | while IFS= read -r line; do
        log "  ${line}"
      done
    fi
  fi

  stderr_file="$(mktemp)"
  set +e
  mount "$@" 2>"$stderr_file"
  rc=$?
  set -e

  mp_after="$(runtime_chroot_mountpoint_status "$dst")"
  err_line="$(tr '\n' ' ' <"$stderr_file" 2>/dev/null | sed 's/[[:space:]]*$//' || true)"
  rm -f "$stderr_file"

  log "mount rc=${rc}"
  log "mount stderr=${err_line:-<empty>}"
  log "mountpoint after: ${mp_after}"
  runtime_chroot_mount_append_report "$report" "$stage" "$label" "$rc" "${err_line:-}"

  if [[ -n "$report" ]]; then
    {
      echo "mount_failure_target=${label}"
      echo "mount_failure_errno=${rc}"
    } >>"$report"
  fi

  if [[ $rc -eq 32 ]]; then
    log "mount_failure_errno=32 (ENOSPC — often mount table limit, not disk full)"
    runtime_log_chroot_mount_infrastructure_forensic "$root" "$report" "after_rc32_enospc"
  fi

  if [[ $rc -ne 0 ]]; then
    log_fail "mount failed: ${label} -> ${dst} (rc=${rc})"
    [[ -n "$err_line" ]] && log_fail "mount stderr: ${err_line}"
    [[ $required -eq 1 ]] && return "$rc"
    log_warn "WARN: optional mount failed: ${label} (continuing)"
    return 0
  fi
  return 0
}

runtime_chroot_prep_mount_tree() {
  local root="${1:?}"

  log_step "chroot mount prep: clear stale rootfs mounts"
  if declare -f runtime_debootstrap_unmount_rootfs >/dev/null 2>&1; then
    runtime_debootstrap_unmount_rootfs
  else
    runtime_chroot_umount_deps "$root"
  fi
  mkdir -p "${root}/dev/pts" "${root}/dev/shm" "${root}/proc" "${root}/sys" "${root}/run"
}

# Mount minimal bind mounts for rootfs chroot (apt/systemd helpers).
runtime_chroot_mount_deps() {
  local root="${1:?}"
  local report="${2:-}"
  local stage="${3:-chroot}"
  local rc=0
  local rootfs_mount_before rootfs_mount_after rootfs_mount_delta

  log "=== chroot mount deps (stage=${stage}) ==="
  log "ROOTFS_RESOLVED=${root}"

  rootfs_mount_before="$(runtime_chroot_rootfs_mount_count "$root")"
  rootfs_mount_before="$(runtime_normalize_uint "$rootfs_mount_before")"
  log "chroot_mount_rootfs_count_before=${rootfs_mount_before}"

  runtime_log_chroot_mount_infrastructure_forensic "$root" "$report" "before_mount"

  runtime_chroot_prep_mount_tree "$root"

  runtime_log_chroot_mount_infrastructure_forensic "$root" "$report" "after_prep_before_bind"

  runtime_chroot_mount_attempt "$root" "$stage" "dev" "$report" 1 \
    -o bind /dev "${root}/dev" || rc=$?

  if [[ $rc -eq 0 ]]; then
    runtime_chroot_mount_attempt "$root" "$stage" "dev/pts" "$report" 0 \
      -o bind /dev/pts "${root}/dev/pts" || true
  fi

  if [[ $rc -eq 0 ]]; then
    runtime_chroot_mount_attempt "$root" "$stage" "proc" "$report" 1 \
      -t proc proc "${root}/proc" || rc=$?
  fi

  if [[ $rc -eq 0 ]]; then
    runtime_chroot_mount_attempt "$root" "$stage" "sys" "$report" 0 \
      -t sysfs sysfs "${root}/sys" || true
  fi

  if [[ $rc -eq 0 ]]; then
    runtime_chroot_mount_attempt "$root" "$stage" "run" "$report" 0 \
      -o bind /run "${root}/run" || true
  fi

  read -r rootfs_mount_before rootfs_mount_after rootfs_mount_delta < <(
    runtime_chroot_safe_uint_delta \
      "$rootfs_mount_before" \
      "$(runtime_chroot_rootfs_mount_count "$root")" \
      "chroot_mount_rootfs_count"
  )
  log "chroot_mount_rootfs_count_after=${rootfs_mount_after}"
  log "chroot_mount_rootfs_count_delta=${rootfs_mount_delta}"

  if [[ -n "$report" ]]; then
    {
      echo "chroot_mount_rootfs_count_before=${rootfs_mount_before}"
      echo "chroot_mount_rootfs_count_after=${rootfs_mount_after}"
      echo "chroot_mount_rootfs_count_delta=${rootfs_mount_delta}"
      echo "rootfs_mount_count=${rootfs_mount_after}"
    } >>"$report"
  fi

  # Block stale duplicate explosion; allow a small fresh mount set after prep (<=16 entries).
  if [[ $rootfs_mount_after -gt $rootfs_mount_before ]]; then
    if [[ $rootfs_mount_before -gt 32 ]] || [[ $rootfs_mount_delta -gt 16 ]]; then
      log_fail "chroot mount deps increased rootfs_mount_count: ${rootfs_mount_before} -> ${rootfs_mount_after} (delta=${rootfs_mount_delta}, stage=${stage})"
      return 1
    fi
  fi

  if [[ $rc -eq 0 ]]; then
    log_pass "chroot mount deps ready (stage=${stage})"
    return 0
  fi

  log_fail "chroot mount deps failed (stage=${stage})"
  return "$rc"
}

runtime_chroot_umount_deps() {
  local root="${1:?}"
  umount "${root}/run" 2>/dev/null || true
  umount "${root}/sys" 2>/dev/null || true
  umount "${root}/proc" 2>/dev/null || true
  umount "${root}/dev/pts" 2>/dev/null || true
  umount "${root}/dev" 2>/dev/null || true
  if declare -f runtime_debootstrap_unmount_rootfs >/dev/null 2>&1; then
    runtime_debootstrap_unmount_rootfs
  fi
}

# Create systemd .wants symlinks (chroot systemctl enable is unreliable without dbus).
runtime_systemd_enable_unit_wants() {
  local unit="$1"
  shift
  local root="${ROOTFS_RESOLVED:?}"
  local target want_dir link

  for target in "$@"; do
    want_dir="${root}/etc/systemd/system/${target}.wants"
    mkdir -p "$want_dir"
    link="${want_dir}/${unit}"
    ln -sfn "../${unit}" "$link"
    log "PASS: enabled ${unit} for ${target} (${link})"
  done
}

resolve_rootfs() {
  # Never call die() here — this function is used inside $(...) and die/log output
  # would be captured into a variable instead of the terminal (silent build failure).
  local candidate="${1:-$ROOTFS}"
  if [[ ! -d "$candidate" ]]; then
    log_fail "ROOTFS does not exist: $candidate"
    return 1
  fi
  candidate="$(readlink -f "$candidate")"
  if [[ "$candidate" == "/" ]]; then
    log_fail "ROOTFS cannot be /"
    return 1
  fi
  if [[ ! -f "$candidate/etc/os-release" ]]; then
    log_fail "missing etc/os-release in ROOTFS: $candidate"
    return 1
  fi
  local host_root
  host_root="$(readlink -f /)"
  if [[ "$candidate" == "$host_root" ]]; then
    log_fail "ROOTFS resolves to host root — refusing"
    return 1
  fi
  printf '%s' "$candidate"
  return 0
}

if [[ "${RECOVERIX_DEFER_ROOTFS_RESOLVE:-0}" != 1 ]]; then
  runtime_resolve_rootfs_paths
fi

require_root() {
  [[ "$(id -u)" -eq 0 ]] || die "run as root (sudo)"
}

require_build_tools() {
  command -v mksquashfs >/dev/null || die "mksquashfs not found (install squashfs-tools)"
  command -v unsquashfs >/dev/null || die "unsquashfs not found"
}

host_dpkg_query() {
  dpkg-query --admindir="${ROOTFS_RESOLVED}/var/lib/dpkg" --root="${ROOTFS_RESOLVED}" "$@"
}

# shellcheck source=firmware_provision.sh
source "${SCRIPT_DIR}/lib/firmware_provision.sh"
# shellcheck source=runtime_kernel_artifact_forensic.sh
source "${SCRIPT_DIR}/lib/runtime_kernel_artifact_forensic.sh"
