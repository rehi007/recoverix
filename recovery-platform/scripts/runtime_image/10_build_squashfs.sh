#!/usr/bin/env bash
# Build readonly Recovery Runtime squashfs from minimized rootfs.
# Usage: sudo ./10_build_squashfs.sh
# Does NOT modify host boot chain.

# Bootstrap logging before set -e / source (must always print to stdout).
_rt_boot_log() {
  printf '[runtime-image] %s\n' "$*"
}

_rt_boot_log "START 10_build_squashfs.sh pid=$$ shell=${BASH_VERSION:-unknown}"

DIR="$(cd "$(dirname "$0")" && pwd)"
export RUNTIME_IMAGE_DIR="${DIR}"
export RECOVERIX_DEFER_ROOTFS_RESOLVE=1

_rt_on_exit() {
  local rc=$?
  if [[ $rc -eq 0 ]]; then
    _rt_boot_log "END 10_build_squashfs.sh (ok)"
  else
    _rt_boot_log "EXIT FAIL: 10_build_squashfs.sh status=${rc}"
  fi
}

_rt_on_err() {
  local rc=$?
  _rt_boot_log "FAIL: command error exit=${rc} line=${BASH_LINENO[0]:-?} cmd=${BASH_COMMAND:-?}"
}

set -euo pipefail
trap _rt_on_exit EXIT
trap _rt_on_err ERR

_rt_boot_log "STEP: source lib/common.sh"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"

_rt_boot_log "STEP: source lib/integrity_check.sh"
# shellcheck source=lib/integrity_check.sh
source "${DIR}/lib/integrity_check.sh"
# shellcheck source=lib/runtime_gui_stack_install.sh
source "${DIR}/lib/runtime_gui_stack_install.sh"

runtime_build_gui_enabled() {
  [[ "${RECOVERIX_RUNTIME_ENABLE_GUI:-1}" == "1" ]]
}

main() {
  log "=== Build squashfs (immutable runtime base) ==="

  log_step "preflight (root, tools, paths)"
  require_root
  require_build_tools
  runtime_safety_assert_no_host_boot_mutation "build" || die "safety check blocked"

  runtime_assert_build_disk_safety

  if ! runtime_resolve_rootfs_paths; then
    log_fail "failed to resolve ROOTFS or create build directories"
    return 1
  fi

  export RECOVERIX_SQUASHFS_STAGE=1

  local TS BUILD_REPORT
  TS="$(date -u +%Y%m%dT%H%M%SZ)"
  BUILD_REPORT="${REPORT_DIR}/squashfs_build_${TS}.txt"

  log_step "initialize build report"
  log "source: ${ROOTFS_RESOLVED}"
  log "output: ${RUNTIME_SQUASHFS}"

  {
    echo "=== Squashfs build report ==="
    echo "timestamp: ${TS}"
    echo "source_rootfs: ${ROOTFS_RESOLVED}"
    echo "runtime_rootfs_source: ${RECOVERIX_RUNTIME_ROOTFS_SOURCE:-$(runtime_read_rootfs_source_marker 2>/dev/null || echo unknown)}"
    echo "output: ${RUNTIME_SQUASHFS}"
    echo "compression: xz"
    echo
  } >"$BUILD_REPORT"

  if ! runtime_assert_rootfs_prepared_for_squashfs "$BUILD_REPORT"; then
    log_fail "FAIL: rootfs not prepared before squashfs build"
    log_fail "hint: run 00_build_runtime.sh with RECOVERIX_FORCE_DEBOOTSTRAP=1"
    return 1
  fi

  log_step "verify rootfs integrity"
  if ! runtime_verify_rootfs_integrity "$BUILD_REPORT"; then
    die "rootfs integrity check failed — fix before squashfs build"
  fi

  log_step "normalize init symlinks"
  if ! runtime_fix_init_symlinks; then
    die "init symlink normalize failed — fix rootfs before squashfs build"
  fi

  log_step "fix recovery fstab"
  if ! runtime_fix_recovery_fstab; then
    die "recovery fstab fix failed — fix rootfs /etc/fstab before squashfs build"
  fi

  log_step "ensure amdgpu firmware in rootfs"
  if ! runtime_ensure_amdgpu_firmware; then
    die "amdgpu firmware missing in rootfs — copy from host ${RUNTIME_HOST_FIRMWARE_AMDGPU:-/usr/lib/firmware/amdgpu} failed"
  fi

  log_step "install runtime CLI tools"
  if ! runtime_install_recoverix_runtime_tools; then
    die "failed to install recoverix runtime tools into rootfs"
  fi
  if ! runtime_verify_rootfs_recoverix_runtime_tools; then
    die "recoverix runtime tools missing or not executable in rootfs"
  fi

  if runtime_build_gui_enabled; then
    log_step "install GUI session stack + recovery UI"
    if ! runtime_install_gui_session_stack; then
      die "failed to install GUI session stack into rootfs"
    fi
    if ! runtime_verify_gui_stack_rootfs; then
      die "GUI stack verification failed in rootfs"
    fi
    if declare -f runtime_assert_no_leaked_host_homes_in_rootfs >/dev/null 2>&1; then
      runtime_remove_leaked_host_homes_from_rootfs
      runtime_assert_no_leaked_host_homes_in_rootfs "$BUILD_REPORT" || \
        die "leaked host home in rootfs before squashfs — fix GUI chroot HOME policy"
    fi
    if ! runtime_verify_rootfs_recoverix_recovery_ui; then
      die "recoverix recovery UI missing in rootfs"
    fi
    if ! runtime_verify_recovery_ui_launcher_rootfs; then
      die "recoverix-recovery-ui launcher/python import check failed"
    fi
    runtime_run_gui_stack_chroot_selftest || log_warn "GUI chroot selftest reported issues (non-fatal for squashfs)"
    if declare -f runtime_assert_no_leaked_host_homes_in_rootfs >/dev/null 2>&1; then
      runtime_remove_leaked_host_homes_from_rootfs
      runtime_assert_no_leaked_host_homes_in_rootfs "$BUILD_REPORT" || \
        die "leaked host home in rootfs after GUI selftest"
    fi
  else
    log_step "install runtime TUI stack"
    if ! runtime_install_recoverix_runtime_tui_only; then
      die "failed to install TUI-only runtime stack into rootfs"
    fi
    if ! runtime_verify_rootfs_recoverix_runtime_tui; then
      die "recoverix runtime TUI missing in rootfs"
    fi
  fi

  log_step "install recoverix-image-status CLI"
  if ! runtime_install_recoverix_image_status; then
    die "failed to install recoverix-image-status CLI into rootfs"
  fi
  if ! runtime_verify_rootfs_recoverix_image_status; then
    die "recoverix-image-status missing or not executable in rootfs"
  fi

  log_step "install recoverix-restore-preflight CLI"
  if ! runtime_install_recoverix_restore_preflight; then
    die "failed to install recoverix-restore-preflight CLI into rootfs"
  fi
  if ! runtime_verify_rootfs_recoverix_restore_preflight; then
    die "recoverix-restore-preflight missing or not executable in rootfs"
  fi

  log_step "install recoverix-restore-plan CLI"
  if ! runtime_install_recoverix_restore_plan; then
    die "failed to install recoverix-restore-plan CLI into rootfs"
  fi
  if ! runtime_verify_rootfs_recoverix_restore_plan; then
    die "recoverix-restore-plan missing or not executable in rootfs"
  fi

  log_step "install recoverix-backup-admin CLI"
  if ! runtime_install_recoverix_backup_admin; then
    die "failed to install recoverix-backup-admin CLI into rootfs"
  fi
  if ! runtime_assert_rootfs_recoverix_backup_admin; then
    log_fail "recoverix-backup-admin missing or not executable in rootfs"
    return 1
  fi

  log_step "install recoverix-backup-finalize-check CLI"
  if ! runtime_install_recoverix_backup_finalize_check; then
    die "failed to install recoverix-backup-finalize-check CLI into rootfs"
  fi
  if ! runtime_assert_rootfs_recoverix_backup_finalize_check; then
    log_fail "recoverix-backup-finalize-check missing or not executable in rootfs"
    return 1
  fi
  if ! runtime_log_rootfs_recoverix_sbin_inventory; then
    log_fail "rootfs /usr/local/sbin recoverix CLI inventory check failed"
    return 1
  fi

  log_step "python import self-test"
  if ! runtime_run_python_import_self_test; then
    die "python import self-test failed — backup_engine or gtk_ui modules missing"
  fi

  log_step "verify rootfs layout"
  if ! runtime_verify_rootfs_layout_strict; then
    die "rootfs layout check failed — init/systemd or usrmerge broken"
  fi

  log_step "purge staged boot artifacts from rootfs"
  runtime_purge_rootfs_boot_staging
  if ! runtime_verify_rootfs_no_staged_artifacts; then
    die "rootfs contains staged boot artifacts — remove before squashfs (see boot/recoverix, *.squashfs, initrd-*-recoverix)"
  fi

  local ROOTFS_BYTES
  ROOTFS_BYTES="$(du -sb "${ROOTFS_RESOLVED}" | awk '{print $1}')"
  log "rootfs size: ${ROOTFS_BYTES} bytes"

  log_step "pre-mksquashfs finalize-check assert"
  if ! runtime_assert_rootfs_recoverix_backup_finalize_check; then
    log_fail "pre-mksquashfs: recoverix-backup-finalize-check not in rootfs — aborting squashfs build"
    return 1
  fi

  log_step "pre-mksquashfs backup-admin assert"
  if ! runtime_assert_rootfs_recoverix_backup_admin; then
    log_fail "pre-mksquashfs: recoverix-backup-admin not in rootfs — aborting squashfs build"
    return 1
  fi

  log_step "pre-mksquashfs forensic sudo assert"
  if ! runtime_assert_rootfs_recoverix_forensic_sudo; then
    log_fail "pre-mksquashfs: recoverix forensic sudo (service/helper/sudoers) not in rootfs — aborting squashfs build"
    return 1
  fi
  if ! runtime_verify_rootfs_recoverix_forensic_sudo; then
    die "rootfs forensic sudo verification failed — rebuild GUI stack / recovery UI install"
  fi

  if declare -f runtime_log_kernel_artifact_forensic >/dev/null 2>&1; then
    runtime_log_kernel_artifact_forensic "pre_mksquashfs" "$BUILD_REPORT"
    runtime_check_kernel_artifact_path_mismatch "$BUILD_REPORT"
  fi

  log_step "verify rootfs amdgpu kernel module (pre-mksquashfs)"
  if ! runtime_verify_rootfs_amdgpu_kernel_module "$BUILD_REPORT"; then
    die "FAIL: runtime_verify_rootfs_amdgpu_kernel_module (pre-mksquashfs) — install linux-modules-extra or RECOVERIX_FORCE_DEBOOTSTRAP=1"
  fi

  local -a REPRO_ARGS=()
  if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
    REPRO_ARGS=(-mkfs-time "${SOURCE_DATE_EPOCH}" -all-time "${SOURCE_DATE_EPOCH}")
    log "reproducible: SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}"
  fi

  log_step "mksquashfs"
  log "Running mksquashfs (xz, noappend)..."
  set +e
  mksquashfs "${ROOTFS_RESOLVED}" "${RUNTIME_SQUASHFS}" \
    -comp xz \
    -Xbcj x86 \
    -b 1M \
    -noappend \
    -no-recovery \
    -no-exports \
    -all-root \
    -e boot/efi boot/recoverix EFI proc sys dev run tmp var/tmp var/cache var/log lost+found \
    "${REPRO_ARGS[@]}" \
    -info \
    2>&1 | tee -a "$BUILD_REPORT"
  local SQ_RC=${PIPESTATUS[0]}
  set -e

  if [[ $SQ_RC -ne 0 ]]; then
    die "mksquashfs failed (rc=${SQ_RC})"
  fi
  log_pass "squashfs generated: ${RUNTIME_SQUASHFS}"

  if declare -f runtime_log_kernel_artifact_forensic >/dev/null 2>&1; then
    runtime_log_kernel_artifact_forensic "post_mksquashfs" "$BUILD_REPORT"
    runtime_check_kernel_artifact_path_mismatch "$BUILD_REPORT"
  fi

  log_step "verify squashfs layout"
  if ! runtime_verify_squashfs_layout_strict; then
    die "FAIL: runtime integrity validation failed (squashfs layout) — see log above"
  fi

  log_step "verify squashfs firmware"
  if ! runtime_verify_squashfs_firmware; then
    die "squashfs firmware check failed — amdgpu firmware not in image"
  fi

  log_step "verify squashfs amdgpu kernel module"
  if ! runtime_verify_squashfs_amdgpu_kernel_module "$BUILD_REPORT"; then
    die "FAIL: runtime_verify_squashfs_amdgpu_kernel_module — rebuild rootfs with linux-modules-extra"
  fi

  log_step "verify squashfs has no nested boot artifacts"
  if ! runtime_verify_squashfs_no_nested_boot_artifacts; then
    die "squashfs embeds boot/recoverix artifacts — rebuild after purging rootfs staging"
  fi

  log_step "verify squashfs recoverix CLIs"
  if ! runtime_verify_squashfs_recoverix_runtime_tools; then
    die "squashfs missing executable recoverix runtime tools under /usr/local/sbin"
  fi
  if runtime_build_gui_enabled; then
    if ! runtime_verify_squashfs_recoverix_recovery_ui; then
      die "squashfs missing recoverix recovery UI launcher, autostart, or python package"
    fi
  else
    if ! runtime_verify_squashfs_recoverix_runtime_tui_only; then
      die "squashfs missing recoverix runtime TUI files"
    fi
  fi
  if ! runtime_verify_squashfs_recoverix_forensic_sudo; then
    die "squashfs missing recoverix forensic sudo service, helper, sudoers, or wants symlink"
  fi
  if ! runtime_verify_squashfs_recoverix_image_status; then
    die "squashfs missing recoverix-image-status CLI under /usr/local/sbin"
  fi
  if ! runtime_verify_squashfs_recoverix_restore_preflight; then
    die "squashfs missing recoverix-restore-preflight CLI under /usr/local/sbin"
  fi
  if ! runtime_verify_squashfs_recoverix_restore_plan; then
    die "squashfs missing recoverix-restore-plan CLI under /usr/local/sbin"
  fi
  if ! runtime_verify_squashfs_recoverix_python_platform; then
    die "squashfs missing recovery-platform python packages (backup_engine, etc.)"
  fi
  if ! runtime_assert_squashfs_recoverix_backup_finalize_check; then
    log_fail "squashfs missing recoverix-backup-finalize-check CLI under /usr/local/sbin"
    return 1
  fi
  if ! runtime_assert_squashfs_recoverix_backup_admin; then
    log_fail "squashfs missing recoverix-backup-admin CLI under /usr/local/sbin"
    return 1
  fi

  log_step "optional chroot self-test (non-fatal)"
  runtime_run_backup_finalize_check_rootfs_self_test || log_warn "chroot self-test skipped or warned"

  if command -v setfattr >/dev/null 2>&1; then
    setfattr -n user.recoverix -v "runtime-${TS}" "${RUNTIME_SQUASHFS}" 2>/dev/null || true
  fi

  local SQUASH_BYTES RATIO
  SQUASH_BYTES="$(stat -c '%s' "${RUNTIME_SQUASHFS}")"
  RATIO="$(awk -v s="$SQUASH_BYTES" -v r="$ROOTFS_BYTES" 'BEGIN { if (r>0) printf "%.2f", (1-s/r)*100; else print "0" }')"

  {
    echo
    echo "rootfs_bytes: ${ROOTFS_BYTES}"
    echo "squashfs_bytes: ${SQUASH_BYTES}"
    echo "compression_ratio_percent: ${RATIO}"
    echo "squashfs_label: ${RUNTIME_SQUASHFS_LABEL}"
    echo "status: OK"
  } >>"$BUILD_REPORT"

  log_pass "squashfs build complete"
  log "squashfs: ${RUNTIME_SQUASHFS} ($(numfmt --to=iec "${SQUASH_BYTES}" 2>/dev/null || echo "${SQUASH_BYTES}"))"
  log "report: ${BUILD_REPORT}"
  return 0
}

_rt_boot_log "STEP: enter main"
if main; then
  exit 0
fi
log_fail "10_build_squashfs.sh failed"
exit 1
