#!/usr/bin/env bash
# Verify squashfs/overlay/initramfs/kernel without host boot modification.
# Usage: sudo ./11_verify_runtime_boot.sh
#
# Policy: squashfs/kernel/initrd/hook failures are FAIL.
# Overlay verify-mnt diagnostic failures are WARN only (do not invalidate artifact).

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
export RUNTIME_IMAGE_DIR="${DIR}"

# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/integrity_check.sh
source "${DIR}/lib/integrity_check.sh"
# shellcheck source=lib/initrd_recoverix.sh
source "${DIR}/lib/initrd_recoverix.sh"

: "${VERIFY_BOOT_WATCHDOG_SEC:=120}"
SCRIPT_START_TS="$(date +%s)"

KVER="${KEEP_KERNEL_FLAVOR}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/boot_validation_${TS}.txt"
SQ_RAW="${REPORT_DIR}/boot_validation_${TS}.raw_listing.txt"
SQ_INDEX="${REPORT_DIR}/boot_validation_${TS}.index.txt"
TEST_MNT="${RUNTIME_DIR}/verify-mnt"
TEST_BASE="${TEST_MNT}/rootfs-base"
TEST_UPPER="${TEST_MNT}/overlay-upper"
TEST_WORK="${TEST_MNT}/overlay-work"
TEST_MERGED="${TEST_MNT}/rootfs-root"

PASS=0
WARN=0
FAIL=0

check_pass() { log_pass "$*"; echo "PASS	$*" >> "$REPORT"; PASS=$((PASS + 1)); }
check_warn() { log_warn "$*"; echo "WARN	$*" >> "$REPORT"; WARN=$((WARN + 1)); }
check_fail() { log_fail "$*"; echo "FAIL	$*" >> "$REPORT"; FAIL=$((FAIL + 1)); }

_verify_boot_report_line() {
  echo "$*" >> "$REPORT"
}

_verify_boot_watchdog() {
  local now elapsed
  now="$(date +%s)"
  elapsed=$((now - SCRIPT_START_TS))
  if [[ $elapsed -gt $VERIFY_BOOT_WATCHDOG_SEC ]]; then
    log_fail "FAIL: verification watchdog timeout (${elapsed}s > ${VERIFY_BOOT_WATCHDOG_SEC}s)"
    exit 1
  fi
}

_verify_boot_phase_enter() {
  _verify_boot_watchdog
  log_step "STEP ENTER: $*"
}

_verify_boot_phase_exit() {
  log_step "STEP EXIT : $*"
  _verify_boot_watchdog
}

_verify_boot_on_signal() {
  log_fail "verification aborted"
  _verify_boot_cleanup_verify_mnt || true
  exit 130
}

# Unmount verify-mnt stack and remove temp dirs (best-effort).
_verify_boot_cleanup_verify_mnt() {
  local m
  for m in "${TEST_MERGED}" "${TEST_WORK}" "${TEST_UPPER}" "${TEST_BASE}"; do
    if mountpoint -q "$m" 2>/dev/null; then
      umount -l "$m" 2>/dev/null || umount "$m" 2>/dev/null || true
    fi
  done
  if [[ -d "${TEST_MNT}" ]]; then
    rm -rf "${TEST_MNT}" 2>/dev/null || true
  fi
}

_verify_boot_log_overlay_preconditions() {
  log_step "overlay diagnostic preconditions"
  _verify_boot_report_line "--- overlay diagnostic preconditions ---"

  if [[ -d "${TEST_BASE}" ]]; then
    log_info "lowerdir exists: ${TEST_BASE}"
    _verify_boot_report_line "lowerdir_exists: yes path=${TEST_BASE}"
  else
    log_warn "WARN: lowerdir missing: ${TEST_BASE}"
    _verify_boot_report_line "lowerdir_exists: no"
  fi

  if [[ -d "${TEST_UPPER}" ]]; then
    log_info "upperdir exists: ${TEST_UPPER}"
    _verify_boot_report_line "upperdir_exists: yes path=${TEST_UPPER}"
  else
    log_warn "WARN: upperdir missing: ${TEST_UPPER}"
    _verify_boot_report_line "upperdir_exists: no"
  fi

  if [[ -d "${TEST_WORK}" ]]; then
    log_info "workdir exists: ${TEST_WORK}"
    _verify_boot_report_line "workdir_exists: yes path=${TEST_WORK}"
  else
    log_warn "WARN: workdir missing: ${TEST_WORK}"
    _verify_boot_report_line "workdir_exists: no"
  fi

  if [[ -d "${TEST_MERGED}" ]]; then
    log_info "merged target exists: ${TEST_MERGED}"
    _verify_boot_report_line "merged_target_exists: yes path=${TEST_MERGED}"
  else
    log_warn "WARN: merged target missing: ${TEST_MERGED}"
    _verify_boot_report_line "merged_target_exists: no"
  fi

  if grep -q '^overlay$' /proc/filesystems 2>/dev/null || lsmod 2>/dev/null | grep -qE '(^| )overlay( |$)'; then
    log_info "overlay module/filesystem available"
    _verify_boot_report_line "overlay_available: yes"
  else
    log_warn "WARN: overlay not available on build host (modprobe may be required)"
    _verify_boot_report_line "overlay_available: no"
  fi

  if mountpoint -q "${TEST_UPPER}" 2>/dev/null; then
    log_info "upperdir mounted: $(findmnt -no FSTYPE "${TEST_UPPER}" 2>/dev/null || echo unknown)"
    _verify_boot_report_line "upperdir_mounted: yes"
  else
    _verify_boot_report_line "upperdir_mounted: no"
  fi

  if mountpoint -q "${TEST_WORK}" 2>/dev/null; then
    log_info "workdir mounted: $(findmnt -no FSTYPE "${TEST_WORK}" 2>/dev/null || echo unknown)"
    _verify_boot_report_line "workdir_mounted: yes"
  else
    _verify_boot_report_line "workdir_mounted: no"
  fi

  if mountpoint -q "${TEST_UPPER}" 2>/dev/null && mountpoint -q "${TEST_WORK}" 2>/dev/null; then
    local upper_fs work_fs
    upper_fs="$(findmnt -no FSTYPE "${TEST_UPPER}" 2>/dev/null || echo '')"
    work_fs="$(findmnt -no FSTYPE "${TEST_WORK}" 2>/dev/null || echo '')"
    if [[ -n "$upper_fs" && "$upper_fs" == "$work_fs" ]]; then
      _verify_boot_report_line "upper_work_same_fs: yes fstype=${upper_fs}"
    else
      _verify_boot_report_line "upper_work_same_fs: no upper=${upper_fs:-none} work=${work_fs:-none}"
    fi
  else
    _verify_boot_report_line "upper_work_same_fs: not_checked"
  fi

  if mountpoint -q "${TEST_BASE}" 2>/dev/null; then
    _verify_boot_report_line "lowerdir_mounted: yes"
  else
    _verify_boot_report_line "lowerdir_mounted: no"
  fi
}

# Diagnostic only — never increments FAIL.
_verify_boot_run_overlay_diagnostic() {
  local sq_mnt_rc=0 upper_rc=0 work_rc=0 overlay_rc=0
  local mount_err=""

  _verify_boot_phase_enter "overlay diagnostic mount"
  _verify_boot_report_line "--- overlay diagnostic ---"

  set +e
  modprobe squashfs 2>/dev/null
  modprobe overlay 2>/dev/null
  _verify_boot_cleanup_verify_mnt
  mkdir -p "${TEST_BASE}" "${TEST_UPPER}" "${TEST_WORK}" "${TEST_MERGED}"

  if [[ ! -f "${RUNTIME_SQUASHFS}" ]]; then
    check_warn "overlay diagnostic skipped — squashfs missing"
    _verify_boot_cleanup_verify_mnt
    set -e
    _verify_boot_phase_exit "overlay diagnostic mount"
    return 0
  fi

  if ! mount -t squashfs -o loop,ro "${RUNTIME_SQUASHFS}" "${TEST_BASE}" 2>/dev/null; then
    sq_mnt_rc=$?
    check_fail "squashfs loop mount failed (squashfs read failure)"
    _verify_boot_report_line "squashfs_loop_mount: fail rc=${sq_mnt_rc}"
    _verify_boot_cleanup_verify_mnt
    set -e
    _verify_boot_phase_exit "overlay diagnostic mount"
    return 0
  fi

  check_pass "squashfs loop mount (diagnostic)"
  _verify_boot_log_overlay_preconditions

  upper_rc=0
  work_rc=0
  if ! mount -t tmpfs tmpfs "${TEST_UPPER}" 2>/dev/null; then
    upper_rc=1
  fi
  if ! mount -t tmpfs tmpfs "${TEST_WORK}" 2>/dev/null; then
    work_rc=1
  fi

  if [[ $upper_rc -ne 0 || $work_rc -ne 0 ]]; then
    check_warn "overlay diagnostic: could not mount upper/work tmpfs on build host"
    check_warn "WARN: overlay diagnostic mount failed"
    check_warn "WARN: squashfs artifact remains valid because read/layout checks passed"
    _verify_boot_cleanup_verify_mnt
    set -e
    _verify_boot_phase_exit "overlay diagnostic mount"
    return 0
  fi

  _verify_boot_log_overlay_preconditions

  mount_err="$(mount -t overlay overlay \
    -o "lowerdir=${TEST_BASE},upperdir=${TEST_UPPER},workdir=${TEST_WORK}" \
    "${TEST_MERGED}" 2>&1)"
  overlay_rc=$?

  if [[ $overlay_rc -eq 0 ]]; then
    check_pass "overlay diagnostic mount (lower=squashfs upper=tmpfs)"
    if [[ -f "${TEST_MERGED}/etc/os-release" ]] && \
       { [[ -x "${TEST_MERGED}/usr/sbin/init" ]] || [[ -x "${TEST_MERGED}/sbin/init" ]]; }; then
      check_pass "merged root has init and os-release (diagnostic)"
    else
      check_warn "overlay diagnostic: merged root missing init or os-release"
    fi
    if chroot "${TEST_MERGED}" /bin/bash -c \
      'python3 -c "import gi; gi.require_version(\"Gtk\",\"3.0\"); from gi.repository import Gtk; Gtk.init_check(None)"' \
      2>/dev/null; then
      check_pass "GTK import in merged overlay root (diagnostic)"
    else
      check_warn "GTK import failed in merged overlay root (diagnostic)"
    fi
  else
    check_warn "WARN: overlay diagnostic mount failed"
    check_warn "WARN: squashfs artifact remains valid because read/layout checks passed"
    if [[ -n "$mount_err" ]]; then
      log_warn "WARN: mount error: ${mount_err}"
      _verify_boot_report_line "overlay_mount_error: ${mount_err}"
    fi
    _verify_boot_report_line "overlay_mount: fail rc=${overlay_rc}"
  fi

  _verify_boot_cleanup_verify_mnt
  set -e
  _verify_boot_phase_exit "overlay diagnostic mount"
  return 0
}

trap '_verify_boot_on_signal' INT TERM
trap '_verify_boot_cleanup_verify_mnt' EXIT

require_root
require_build_tools
runtime_safety_assert_no_host_boot_mutation "verify" || die "safety blocked"

log "=========================================="
log " Verify Recoverix runtime boot artifacts"
log " ROOTFS=${ROOTFS_RESOLVED}"
log " SQUASHFS=${RUNTIME_SQUASHFS}"
log " watchdog=${VERIFY_BOOT_WATCHDOG_SEC}s"
log "=========================================="

{
  echo "=== Runtime boot validation ==="
  echo "timestamp: ${TS}"
  echo "kernel: ${KVER}"
  echo "rootfs: ${ROOTFS_RESOLVED}"
  echo "squashfs: ${RUNTIME_SQUASHFS}"
  echo "policy: overlay verify-mnt diagnostic is WARN-only"
  echo "watchdog_seconds: ${VERIFY_BOOT_WATCHDOG_SEC}"
  echo
} > "$REPORT"

_verify_boot_phase_enter "rootfs integrity"
runtime_verify_rootfs_integrity "$REPORT" && check_pass "rootfs integrity" || check_fail "rootfs integrity"
_verify_boot_phase_exit "rootfs integrity"

_verify_boot_phase_enter "recoverix-backup-finalize-check rootfs"
if [[ -x "$(recoverix_backup_finalize_check_path)" ]]; then
  if runtime_assert_rootfs_recoverix_backup_finalize_check; then
    check_pass "rootfs contains recoverix-backup-finalize-check"
  else
    check_fail "rootfs recoverix-backup-finalize-check validation failed"
  fi
else
  check_fail "rootfs missing recoverix-backup-finalize-check — run sudo ${DIR}/10_build_squashfs.sh"
fi
_verify_boot_phase_exit "recoverix-backup-finalize-check rootfs"

_verify_boot_phase_enter "recoverix-backup-admin rootfs"
if [[ -x "$(recoverix_backup_admin_path)" ]]; then
  if runtime_assert_rootfs_recoverix_backup_admin; then
    check_pass "rootfs contains recoverix-backup-admin"
  else
    check_fail "rootfs recoverix-backup-admin validation failed"
  fi
else
  check_fail "rootfs missing recoverix-backup-admin — run sudo ${DIR}/10_build_squashfs.sh"
fi
_verify_boot_phase_exit "recoverix-backup-admin rootfs"

_verify_boot_phase_enter "squashfs artifact checks"
if [[ -f "${RUNTIME_SQUASHFS}" ]]; then
  check_pass "squashfs image exists: ${RUNTIME_SQUASHFS}"
  echo "squashfs_bytes: $(stat -c '%s' "${RUNTIME_SQUASHFS}")" >> "$REPORT"
else
  check_fail "missing ${RUNTIME_SQUASHFS} — run 10_build_squashfs.sh"
fi

if [[ -f "${RUNTIME_SQUASHFS}" ]]; then
  if unsquashfs -s "${RUNTIME_SQUASHFS}" &>/dev/null; then
    check_pass "unsquashfs -s can read squashfs metadata"
  else
    check_fail "unsquashfs -s cannot read squashfs (squashfs read failure)"
  fi

  _verify_boot_phase_enter "squashfs listing and index"
  if runtime_verify_unsquashfs_listing "${RUNTIME_SQUASHFS}" "$SQ_RAW" "$SQ_INDEX"; then
    check_pass "unsquashfs listing readable"
    check_pass "squashfs listing verification complete"
    _verify_boot_report_line "squashfs_raw_listing: ${SQ_RAW}"
    _verify_boot_report_line "squashfs_index: ${SQ_INDEX}"
  else
    check_fail "squashfs listing verification failed (unsquashfs -ll rc, timeout, or required paths)"
  fi
  _verify_boot_phase_exit "squashfs listing and index"

  _verify_boot_phase_enter "squashfs recoverix-backup-finalize-check"
  if runtime_assert_squashfs_recoverix_backup_finalize_check; then
    check_pass "squashfs contains recoverix-backup-finalize-check"
  else
    check_fail "squashfs missing recoverix-backup-finalize-check — rebuild with 10_build_squashfs.sh"
  fi
  _verify_boot_phase_exit "squashfs recoverix-backup-finalize-check"

  _verify_boot_phase_enter "squashfs recoverix-backup-admin"
  if runtime_assert_squashfs_recoverix_backup_admin; then
    check_pass "squashfs contains recoverix-backup-admin"
  else
    check_fail "squashfs missing recoverix-backup-admin — rebuild with 10_build_squashfs.sh"
  fi
  _verify_boot_phase_exit "squashfs recoverix-backup-admin"
fi
_verify_boot_phase_exit "squashfs artifact checks"

_verify_boot_phase_enter "kernel artifacts"
if declare -f runtime_log_kernel_artifact_forensic >/dev/null 2>&1; then
  runtime_log_kernel_artifact_forensic "verify_boot" "$REPORT"
  runtime_check_kernel_artifact_path_mismatch "$REPORT"
fi
for f in \
  "${ROOTFS_RESOLVED}/boot/vmlinuz-${KVER}" \
  "${ROOTFS_RESOLVED}/boot/initrd.img-${KVER}" \
  "${ROOTFS_RESOLVED}/lib/modules/${KVER}"; do
  [[ -e "$f" ]] && check_pass "kernel artifact ${f}" || check_fail "missing kernel artifact ${f}"
done
_verify_boot_phase_exit "kernel artifacts"

_verify_boot_phase_enter "initramfs recoverix hooks"
if [[ -f "${ROOTFS_RESOLVED}/etc/initramfs-tools/hooks/recoverix-overlay" ]]; then
  check_pass "initramfs recoverix-overlay hook installed in rootfs"
else
  check_fail "missing initramfs recoverix-overlay hook — run 20_install_initramfs_hook.sh"
fi

INITRD_CHECK="$(recoverix_initrd_resolve_source "$KVER" || true)"
if [[ -z "$INITRD_CHECK" ]]; then
  check_fail "missing initrd.img-${KVER}-recoverix — run 20_install_initramfs_hook.sh"
else
  check_pass "recoverix initrd: ${INITRD_CHECK}"
  if recoverix_verify_initrd_hooks "$INITRD_CHECK" "$REPORT"; then
    check_pass "recoverix initrd hooks verified"
  else
    check_fail "recoverix initrd hook verification failed"
  fi
fi
_verify_boot_phase_exit "initramfs recoverix hooks"

_verify_boot_run_overlay_diagnostic

_verify_boot_phase_enter "host switch_root probe"
if command -v switch_root >/dev/null; then
  check_pass "switch_root binary available on build host"
else
  check_warn "switch_root not on build host (present in initramfs hook)"
fi
_verify_boot_phase_exit "host switch_root probe"

{
  echo
  echo "summary_pass: ${PASS}"
  echo "summary_warn: ${WARN}"
  echo "summary_fail: ${FAIL}"
  echo "validation_result: $([[ ${FAIL} -eq 0 ]] && echo PASS || echo FAIL)"
  echo "elapsed_seconds: $(($(date +%s) - SCRIPT_START_TS))"
} >> "$REPORT"

log "validation summary: PASS=${PASS} WARN=${WARN} FAIL=${FAIL}"
log "report: ${REPORT}"

trap - EXIT
_verify_boot_cleanup_verify_mnt || true

if [[ $FAIL -gt 0 ]]; then
  log_fail "verification failed (${FAIL} failure(s))"
  exit 1
fi

if [[ $WARN -gt 0 ]]; then
  log_warn "verification completed with ${WARN} warning(s) — artifact valid (FAIL=0)"
fi

log_pass "verification completed successfully (FAIL=0)"
exit 0
