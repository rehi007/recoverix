#!/usr/bin/env bash
# Full Recoverix immutable runtime image build (no host boot modification).
# Usage: sudo ./00_build_runtime.sh
#
# Phases:
#   1. rootfs integrity
#   2. squashfs (readonly base)
#   3. initramfs overlay hooks + initrd rebuild (rootfs chroot)
#   4. boot validation
#   5. consolidated report

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

# Defer ROOTFS resolution so this script can create it when missing.
export RECOVERIX_DEFER_ROOTFS_RESOLVE=1

# Make runtime install helpers deploy into this build tree.
export RUNTIME_IMAGE_DIR="${DIR}"

# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/initrd_recoverix.sh
source "${DIR}/lib/initrd_recoverix.sh"

# shellcheck source=lib/runtime_tools_install.sh
source "${DIR}/lib/runtime_tools_install.sh"
# shellcheck source=lib/runtime_python_install.sh
source "${DIR}/lib/runtime_python_install.sh"
# shellcheck source=lib/recovery_ui_install.sh
source "${DIR}/lib/recovery_ui_install.sh"
# shellcheck source=lib/runtime_gui_stack_install.sh
source "${DIR}/lib/runtime_gui_stack_install.sh"

runtime_build_gui_enabled() {
  [[ "${RECOVERIX_RUNTIME_ENABLE_GUI:-1}" == "1" ]]
}

require_root
runtime_safety_assert_no_host_boot_mutation "pipeline" || die "safety blocked"

trap 'rc=$?; log_fail "FAIL: 00_build_runtime.sh aborted rc=${rc} line=${BASH_LINENO[0]:-?} cmd=${BASH_COMMAND:-?}"; exit "${rc}"' ERR

log "=========================================="
log " Recoverix Runtime Image Build Pipeline"
log " ROOTFS (raw)=${ROOTFS}"
log " RUNTIME_DIR=${RUNTIME_DIR}"
log " runtime_build_mode=${RECOVERIX_RUNTIME_BUILD_MODE:-debootstrap} (production: debootstrap)"
log " host_rsync_bootstrap=${RECOVERIX_DEV_HOST_RSYNC_BOOTSTRAP:-0} (dev emergency only)"
log " keep_runtime_rootfs=${KEEP_RUNTIME_ROOTFS:-1}"
log " rootfs_size_target=${RUNTIME_ROOTFS_SIZE_TARGET_GB:-3}GiB"
log " NO host boot / EFI modification"
log "=========================================="

log_step "preflight: disk safety and build tree"
runtime_ensure_build_tree_directories
runtime_assert_build_disk_safety
runtime_cleanup_stale_build_mounts
runtime_cleanup_old_build_reports

runtime_provision_rootfs_primary || die "rootfs provision failed — see logs above"

if ! ROOTFS_RESOLVED="$(resolve_rootfs "$ROOTFS")"; then
  die "ROOTFS resolution failed after provision"
fi
export ROOTFS_RESOLVED
log_info "ROOTFS_RESOLVED=${ROOTFS_RESOLVED}"
log_pass "runtime rootfs source mode: ${RECOVERIX_RUNTIME_ROOTFS_SOURCE:-unknown}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL_REPORT="${REPORT_DIR}/runtime_build_${TS}.txt"

log_step "initialize runtime build report"
log " ROOTFS_RESOLVED=${ROOTFS_RESOLVED}"
log " RUNTIME_SQUASHFS=${RUNTIME_SQUASHFS}"

{
  echo "=== Recoverix Runtime Build Report ==="
  echo "timestamp: ${TS}"
  echo "rootfs: ${ROOTFS_RESOLVED}"
  echo "runtime_dir: ${RUNTIME_DIR}"
  echo "kernel: ${KEEP_KERNEL_FLAVOR}"
  echo "runtime_build_mode: ${RECOVERIX_RUNTIME_BUILD_MODE:-debootstrap}"
  echo "runtime_rootfs_source: ${RECOVERIX_RUNTIME_ROOTFS_SOURCE:-unknown}"
  echo "runtime_user: ${RECOVERIX_RUNTIME_USER:-recoverix}"
  echo "runtime_user_uid: ${RECOVERIX_RUNTIME_UID:-2000}"
  echo "runtime_user_gid: ${RECOVERIX_RUNTIME_GID:-2000}"
  echo "runtime_user_home: ${RECOVERIX_RUNTIME_HOME:-/home/recoverix}"
  echo "runtime_autologin: ${RECOVERIX_RUNTIME_AUTOLOGIN:-1}"
  echo "runtime_allow_sudo: ${RECOVERIX_RUNTIME_ALLOW_SUDO:-0}"
  echo "keep_runtime_rootfs: ${KEEP_RUNTIME_ROOTFS:-1}"
  echo "host_boot_modified: false"
  echo
} > "$FINAL_REPORT"

if declare -f runtime_log_kernel_artifact_forensic >/dev/null 2>&1; then
  runtime_log_kernel_artifact_forensic "debootstrap_provision_post" "$FINAL_REPORT"
fi

chroot_run() {
  # $1: shell snippet
  chroot "${ROOTFS_RESOLVED}" /bin/sh -c "$1"
}

chroot_mount_runtime_deps() {
  runtime_chroot_mount_deps "${ROOTFS_RESOLVED}" "" "00_build_runtime" || return 1
  # DNS helper for apt-get when needed.
  mkdir -p "${ROOTFS_RESOLVED}/etc" 2>/dev/null || true
  cp -f /etc/resolv.conf "${ROOTFS_RESOLVED}/etc/resolv.conf" 2>/dev/null || true
  return 0
}

install_base_runtime_packages_if_missing() {
  log_step "install base runtime packages"
  # Integrity-first: only install missing tools; never purge/minimize during runtime build.

  local mounted=0
  if chroot_mount_runtime_deps; then
    mounted=1
  else
    log_warn "chroot mounts (dev/proc/sys) failed; continuing with file-based checks only"
  fi

  # partclone/ntfs-3g are needed for runtime backup execution; install only if missing.
  local partclone_ok=0 ntfs_ok=0
  if chroot_run "command -v partclone.ntfs >/dev/null 2>&1"; then partclone_ok=1; fi
  if chroot_run "command -v ntfs-3g >/dev/null 2>&1"; then ntfs_ok=1; fi

  # Python/GTK dependencies (gi + Gtk import) are validated separately below.
  if [[ $partclone_ok -eq 0 || $ntfs_ok -eq 0 ]]; then
    log "Installing missing filesystem tools inside rootfs: partclone/ntfs-3g"
    chroot_run 'apt-get update' || true
    recoverix_runtime_apt_install partclone || true
    recoverix_runtime_apt_install ntfs-3g || true
  fi

  if [[ $mounted -eq 1 ]]; then
    runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
  fi

  log_pass "install base runtime packages: checks complete"
  return 0
}

install_python_runtime_dependencies_if_missing() {
  log_step "install python runtime dependencies"

  local mounted=0
  if chroot_mount_runtime_deps; then
    mounted=1
  else
    log_warn "chroot mounts (dev/proc/sys) failed; continuing with best-effort checks only"
  fi

  local python_ok=0
  if runtime_build_gui_enabled; then
    if chroot_run 'python3 -c "import sys; import gi; print(\"gi ok\")" >/dev/null 2>&1'; then
      python_ok=1
    fi
  else
    if chroot_run 'python3 -c "import sys; print(sys.version)" >/dev/null 2>&1'; then
      python_ok=1
    fi
  fi

  if [[ $python_ok -eq 0 ]]; then
    log "Installing missing python runtime deps inside rootfs"
    chroot_run 'apt-get update' || true
    recoverix_runtime_apt_install python3 || true
    if runtime_build_gui_enabled; then
      recoverix_runtime_apt_install python3-gi || true
      recoverix_runtime_apt_install python3-gi-cairo || true
    fi
  fi

  if [[ $mounted -eq 1 ]]; then
    runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
  fi

  log_pass "install python runtime dependencies: checks complete"
  return 0
}

install_gtk_ui_dependencies_if_missing() {
  if ! runtime_build_gui_enabled; then
    log_step "install GTK UI dependencies"
    log_info "RECOVERIX_RUNTIME_ENABLE_GUI=0 — skipping GTK UI dependency install"
    return 0
  fi

  log_step "install GTK UI dependencies"

  local mounted=0
  if chroot_mount_runtime_deps; then
    mounted=1
  else
    log_warn "chroot mounts (dev/proc/sys) failed; continuing with best-effort checks only"
  fi

  local gtk_ok=0
  if chroot_run 'python3 -c "import gi; gi.require_version(\"Gtk\",\"3.0\"); from gi.repository import Gtk; print(\"Gtk ok\")" >/dev/null 2>&1'; then
    gtk_ok=1
  fi

  if [[ $gtk_ok -eq 0 ]]; then
    log "Installing missing GTK 3 (gi/Gtk) deps inside rootfs"
    chroot_run 'apt-get update' || true
    recoverix_runtime_apt_install gir1.2-gtk-3.0 || true
    recoverix_runtime_apt_install libgtk-3-0 || true
  fi

  if [[ $mounted -eq 1 ]]; then
    runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
  fi

  log_pass "install GTK UI dependencies: checks complete"
  return 0
}

install_recoverix_runtime_tools_into_rootfs() {
  log_step "install recoverix runtime tools"

  log_step "install health-check tools"
  runtime_install_recoverix_health_check || die "failed to install recoverix-health-check"

  log_step "install backup admin tools"
  runtime_install_recoverix_backup_admin || die "failed to install recoverix-backup-admin"

  log_step "install backup finalize tools"
  runtime_install_recoverix_backup_finalize_check || die "failed to install recoverix-backup-finalize-check"

  log_step "install recoverix CLI tools"
  runtime_install_recoverix_image_status || die "failed to install recoverix-image-status"

  log_pass "runtime tools installation complete"
}

install_gui_session_stack_into_rootfs() {
  if ! runtime_build_gui_enabled; then
    log_step "install runtime session stack (TUI-only)"
    runtime_install_recoverix_runtime_tui_only || die "failed to install TUI-only runtime stack"
    runtime_verify_rootfs_recoverix_runtime_tui || die "TUI-only rootfs verification failed"
    return 0
  fi

  log_step "install GUI session stack (gdm3, Xorg, openbox, graphical.target)"
  runtime_install_gui_session_stack || die "failed to install GUI session stack"
  runtime_verify_gui_stack_rootfs || die "GUI stack rootfs verification failed"
  runtime_verify_recovery_ui_launcher_rootfs || die "recovery-ui launcher verification failed"
  runtime_append_gui_stack_build_report "$FINAL_REPORT"
}

verify_runtime_rootfs_contents() {
  log_step "verify runtime rootfs contents"

  local failures=0
  local report_pass
  report_pass() {
    local msg="$1"
    log_pass "$msg"
    if [[ -n "${FINAL_REPORT:-}" ]]; then
      echo "$msg" >>"$FINAL_REPORT"
    fi
  }
  check_exec() {
    local p="$1"
    if [[ ! -f "$p" ]]; then
      log_fail "FAIL: missing required runtime file: ${p}"
      failures=$((failures + 1))
      return 0
    fi
    if [[ ! -x "$p" ]]; then
      log_fail "FAIL: required runtime file not executable: ${p}"
      failures=$((failures + 1))
      return 0
    fi
    local mode
    mode="$(stat -c '%a' "$p" 2>/dev/null || echo '?')"
    if [[ "$mode" != "755" ]]; then
      log_warn "WARN: ${p} executable mode=${mode} (expected 755)"
    fi
    return 0
  }

  if [[ ! -f "${ROOTFS_RESOLVED}/etc/os-release" ]]; then
    log_fail "FAIL: missing required runtime file: /etc/os-release"
    failures=$((failures + 1))
  fi

  if [[ ! -x "${ROOTFS_RESOLVED}/bin/sh" && ! -x "${ROOTFS_RESOLVED}/usr/bin/sh" ]]; then
    log_fail "FAIL: missing required shell (/bin/sh or /usr/bin/sh)"
    failures=$((failures + 1))
  fi

  if [[ ! -x "${ROOTFS_RESOLVED}/usr/bin/python3" ]]; then
    log_fail "FAIL: missing python3 inside rootfs"
    failures=$((failures + 1))
  fi

  # GTK python dependency validation via chroot import only (no display needed).
  local mounted=0
  if runtime_build_gui_enabled; then
    if chroot_mount_runtime_deps; then
      mounted=1
      if ! chroot_run 'python3 -c "import gi; gi.require_version(\"Gtk\",\"3.0\"); from gi.repository import Gtk" >/dev/null 2>&1'; then
        log_fail "FAIL: missing GTK python dependency inside rootfs (gi/Gtk import failed)"
        failures=$((failures + 1))
      fi
    else
      log_warn "WARN: could not mount chroot deps for GTK import check; skipping GTK import validation"
    fi
    if [[ $mounted -eq 1 ]]; then
      runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
    fi
  fi

  runtime_assert_runtime_filesystem_binaries "${ROOTFS_RESOLVED}" || failures=$((failures + 1))
  if runtime_build_gui_enabled; then
    runtime_verify_gui_stack_rootfs || failures=$((failures + 1))
    runtime_verify_recovery_ui_launcher_rootfs || failures=$((failures + 1))
  else
    runtime_verify_rootfs_recoverix_runtime_tui || failures=$((failures + 1))
  fi

  if [[ "${RECOVERIX_FORCE_DEBOOTSTRAP:-0}" == "1" ]] && \
     declare -f runtime_kernel_package_assert_force_debootstrap_contract >/dev/null 2>&1; then
    if ! runtime_kernel_package_assert_force_debootstrap_contract "${FINAL_REPORT:-}"; then
      log_fail "FAIL: runtime_kernel_package_assert_force_debootstrap_contract"
      failures=$((failures + 1))
    fi
  fi

  if declare -f runtime_verify_rootfs_amdgpu_kernel_module >/dev/null 2>&1; then
    if runtime_verify_rootfs_amdgpu_kernel_module "${FINAL_REPORT:-}"; then
      report_pass "PASS: amdgpu kernel module present"
    else
      log_fail "FAIL: runtime_verify_rootfs_amdgpu_kernel_module"
      failures=$((failures + 1))
    fi
  else
    log_fail "FAIL: runtime_verify_rootfs_amdgpu_kernel_module unavailable"
    failures=$((failures + 1))
  fi

  if declare -f runtime_report_rootfs_account_state >/dev/null 2>&1; then
    runtime_report_rootfs_account_state "${FINAL_REPORT:-}"
  fi
  if declare -f runtime_report_forensic_sudo_policy >/dev/null 2>&1; then
    runtime_report_forensic_sudo_policy "${FINAL_REPORT:-}"
  fi
  if declare -f runtime_verify_rootfs_recoverix_forensic_sudo >/dev/null 2>&1; then
    if runtime_verify_rootfs_recoverix_forensic_sudo; then
      report_pass "PASS recoverix forensic sudo policy"
    else
      log_fail "FAIL recoverix forensic sudo policy"
      failures=$((failures + 1))
    fi
  fi
  if declare -f runtime_verify_rootfs_recoverix_console_access >/dev/null 2>&1; then
    if runtime_verify_rootfs_recoverix_console_access; then
      report_pass "PASS recoverix console access (login/autologin)"
    else
      log_fail "FAIL recoverix console access (login/autologin)"
      failures=$((failures + 1))
    fi
  fi

  # Runtime user/group forensic state (for report/debug)
  report_pass "PASS runtime identity files present check"
  for f in /etc/passwd /etc/group /etc/shadow /home/recoverix; do
    if [[ -e "${ROOTFS_RESOLVED}${f}" ]]; then
      log_info "runtime_identity_path: ${f} present"
      [[ -n "${FINAL_REPORT:-}" ]] && echo "runtime_identity_path: ${f} present" >>"$FINAL_REPORT"
    else
      log_fail "FAIL: runtime identity path missing: ${f}"
      [[ -n "${FINAL_REPORT:-}" ]] && echo "FAIL runtime_identity_path_missing: ${f}" >>"$FINAL_REPORT"
      failures=$((failures + 1))
    fi
  done

  # Execute requested identity checks inside rootfs and log output.
  if chroot_mount_runtime_deps; then
    local _id_out
    _id_out="$(chroot "${ROOTFS_RESOLVED}" getent passwd recoverix 2>&1 || true)"
    log_info "chroot getent passwd recoverix: ${_id_out:-<empty>}"
    [[ -n "${FINAL_REPORT:-}" ]] && echo "chroot_getent_passwd_recoverix: ${_id_out:-<empty>}" >>"$FINAL_REPORT"

    _id_out="$(chroot "${ROOTFS_RESOLVED}" getent group recoverix 2>&1 || true)"
    log_info "chroot getent group recoverix: ${_id_out:-<empty>}"
    [[ -n "${FINAL_REPORT:-}" ]] && echo "chroot_getent_group_recoverix: ${_id_out:-<empty>}" >>"$FINAL_REPORT"

    _id_out="$(chroot "${ROOTFS_RESOLVED}" id recoverix 2>&1 || true)"
    log_info "chroot id recoverix: ${_id_out:-<empty>}"
    [[ -n "${FINAL_REPORT:-}" ]] && echo "chroot_id_recoverix: ${_id_out:-<empty>}" >>"$FINAL_REPORT"

    runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
  else
    log_warn "WARN: could not mount chroot deps for identity getent/id checks"
  fi

  if [[ -f "${DIR}/deploy/grub/recoverix-esp.cfg.template" ]]; then
    # shellcheck source=deploy/lib/grub_boot_chain.sh
    source "${DIR}/deploy/lib/grub_boot_chain.sh"
    if recoverix_grub_validate_core_menuentries "${DIR}/deploy/grub/recoverix-esp.cfg.template"; then
      report_pass "PASS Recoverix core GRUB menu template"
    else
      log_fail "FAIL Recoverix core GRUB menu template"
      failures=$((failures + 1))
    fi
  fi
  if [[ -f "${ROOTFS_RESOLVED}/etc/systemd/system/recoverix-forensic-sudo.service" && \
        -x "${ROOTFS_RESOLVED}/usr/local/sbin/recoverix-forensic-sudo-enable" && \
        -f "${ROOTFS_RESOLVED}/etc/sudoers.d/99-recoverix-forensic" ]]; then
    report_pass "PASS forensic sudo service/helper/sudoers in runtime rootfs"
  else
    log_fail "FAIL forensic sudo service/helper/sudoers missing in runtime rootfs"
    failures=$((failures + 1))
  fi
  if [[ -L "${ROOTFS_RESOLVED}/etc/systemd/system/sysinit.target.wants/recoverix-forensic-sudo.service" || \
        -L "${ROOTFS_RESOLVED}/etc/systemd/system/multi-user.target.wants/recoverix-forensic-sudo.service" ]]; then
    report_pass "PASS forensic sudo service enabled in rootfs"
  else
    log_fail "FAIL forensic sudo service not enabled (wants symlink missing)"
    failures=$((failures + 1))
  fi
  # Home ownership/mode policy (numeric + name resolution inside rootfs).
  local home_uid_gid home_mode chroot_user_line chroot_group_line chroot_id_line
  home_uid_gid="$(stat -c '%u:%g' "${ROOTFS_RESOLVED}/home/recoverix" 2>/dev/null || echo missing)"
  home_mode="$(stat -c '%a' "${ROOTFS_RESOLVED}/home/recoverix" 2>/dev/null || echo missing)"
  chroot_user_line="$(chroot "${ROOTFS_RESOLVED}" getent passwd recoverix 2>/dev/null || true)"
  chroot_group_line="$(chroot "${ROOTFS_RESOLVED}" getent group recoverix 2>/dev/null || true)"
  chroot_id_line="$(chroot "${ROOTFS_RESOLVED}" id recoverix 2>/dev/null || true)"
  log_info "stat /home/recoverix: $(stat -c '%u:%g %U:%G %a %n' "${ROOTFS_RESOLVED}/home/recoverix" 2>/dev/null || echo missing)"
  [[ -n "${FINAL_REPORT:-}" ]] && echo "stat_home_recoverix: $(stat -c '%u:%g %U:%G %a %n' "${ROOTFS_RESOLVED}/home/recoverix" 2>/dev/null || echo missing)" >>"$FINAL_REPORT"

  local chroot_shell
  chroot_shell="$(awk -F: '/^recoverix:/{print $7}' <<<"${chroot_user_line}" 2>/dev/null || true)"
  if [[ "$home_uid_gid" == "2000:2000" && "$home_mode" == "755" && \
        "$chroot_user_line" == recoverix:x:2000:2000:* && \
        "$chroot_group_line" == recoverix:x:2000:* && \
        -n "${chroot_shell}" ]]; then
    report_pass "PASS recoverix home ownership valid"
  else
    log_fail "FAIL recoverix home ownership valid (uidgid=${home_uid_gid} mode=${home_mode})"
    [[ -n "${FINAL_REPORT:-}" ]] && {
      echo "FAIL recoverix_home_uidgid: ${home_uid_gid}"
      echo "FAIL recoverix_home_mode: ${home_mode}"
      echo "FAIL chroot_getent_passwd_recoverix: ${chroot_user_line:-<empty>}"
      echo "FAIL chroot_getent_group_recoverix: ${chroot_group_line:-<empty>}"
      echo "FAIL chroot_id_recoverix: ${chroot_id_line:-<empty>}"
    } >>"$FINAL_REPORT"

    # Required automatic dumps on failure.
    log_fail "DUMP ls -ln ${ROOTFS_RESOLVED}/home"
    ls -ln "${ROOTFS_RESOLVED}/home" 2>/dev/null || true
    log_fail "DUMP ls -ln ${ROOTFS_RESOLVED}/home/recoverix"
    ls -ln "${ROOTFS_RESOLVED}/home/recoverix" 2>/dev/null || true
    log_fail "DUMP passwd/group recoverix lines"
    rg '^recoverix:' "${ROOTFS_RESOLVED}/etc/passwd" 2>/dev/null || true
    rg '^recoverix:' "${ROOTFS_RESOLVED}/etc/group" 2>/dev/null || true
    failures=$((failures + 1))
  fi
  if runtime_build_gui_enabled; then
    if grep -q '^WaylandEnable=false' "${ROOTFS_RESOLVED}/etc/gdm3/custom.conf" 2>/dev/null; then
      report_pass "PASS gdm Wayland disabled"
    else
      log_fail "FAIL gdm Wayland disabled"
      failures=$((failures + 1))
    fi

    if [[ -x "${ROOTFS_RESOLVED}/usr/lib/xorg/Xorg" ]]; then
      log_pass "PASS: /usr/lib/xorg/Xorg present"
    else
      log_fail "FAIL: missing /usr/lib/xorg/Xorg"
      failures=$((failures + 1))
    fi
    if [[ -d "${ROOTFS_RESOLVED}/usr/lib/xorg/modules" ]]; then
      log_pass "PASS: /usr/lib/xorg/modules present"
    else
      log_fail "FAIL: missing /usr/lib/xorg/modules"
      failures=$((failures + 1))
    fi
    if chroot_run "dpkg-query -W -f='\${Status}' xserver-xorg-core 2>/dev/null | grep -q 'install ok installed'"; then
      log_pass "PASS: xserver-xorg-core installed"
    else
      log_fail "FAIL: xserver-xorg-core not installed"
      failures=$((failures + 1))
    fi
    if compgen -G "${ROOTFS_RESOLVED}/usr/lib/*/libdrm.so*" >/dev/null; then
      log_pass "PASS: libdrm present"
    else
      log_fail "FAIL: libdrm missing"
      failures=$((failures + 1))
    fi
    if compgen -G "${ROOTFS_RESOLVED}/usr/lib/*/libEGL_mesa.so*" >/dev/null || \
       compgen -G "${ROOTFS_RESOLVED}/usr/lib/*/libGLX_mesa.so*" >/dev/null; then
      log_pass "PASS: mesa libraries present"
    else
      log_fail "FAIL: mesa libraries missing"
      failures=$((failures + 1))
    fi
    if [[ -e "${ROOTFS_RESOLVED}/usr/lib/udev/rules.d/71-seat.rules" || -e "${ROOTFS_RESOLVED}/lib/udev/rules.d/71-seat.rules" ]]; then
      log_pass "PASS: /dev/dri support readiness (udev seat rules present)"
    else
      log_warn "WARN: /dev/dri readiness rules not found in rootfs"
    fi
  else
    report_pass "PASS TUI-only runtime mode"
  fi

  # Required runtime CLIs (from runtime_image install scripts)
  check_exec "${ROOTFS_RESOLVED}/usr/local/sbin/recoverix-health-check"
  check_exec "${ROOTFS_RESOLVED}/usr/local/sbin/recoverix-backup-finalize-check"
  check_exec "${ROOTFS_RESOLVED}/usr/local/sbin/recoverix-image-status"
  if runtime_build_gui_enabled; then
    check_exec "${ROOTFS_RESOLVED}/usr/local/sbin/recoverix-recovery-ui"
  else
    check_exec "${ROOTFS_RESOLVED}/usr/local/sbin/recoverix-runtime-tui"
  fi

  if [[ $failures -gt 0 ]]; then
    log_fail "FAIL: runtime rootfs content validation failed (${failures} issue(s))"
    return 1
  fi

  log_pass "PASS: runtime rootfs populated and contents validation complete"
  return 0
}

log_step "bootstrap rootfs"
log_pass "bootstrap rootfs: provision complete (source=${RECOVERIX_RUNTIME_ROOTFS_SOURCE:-unknown})"

install_base_runtime_packages_if_missing
install_python_runtime_dependencies_if_missing
install_gtk_ui_dependencies_if_missing

install_gui_session_stack_into_rootfs
install_recoverix_runtime_tools_into_rootfs

verify_runtime_rootfs_contents || die "rootfs content validation failed"
if declare -f runtime_remove_leaked_host_homes_from_rootfs >/dev/null 2>&1; then
  runtime_remove_leaked_host_homes_from_rootfs "${FINAL_REPORT:-}"
fi
if declare -f runtime_assert_no_leaked_host_homes_in_rootfs >/dev/null 2>&1; then
  runtime_assert_no_leaked_host_homes_in_rootfs "${FINAL_REPORT:-}" || die "leaked host home in rootfs (/home/for or /home/ubuntu)"
fi
if declare -f runtime_log_kernel_artifact_forensic >/dev/null 2>&1; then
  runtime_log_kernel_artifact_forensic "rootfs_content_validation_post" "${FINAL_REPORT:-}"
  runtime_check_kernel_artifact_path_mismatch "${FINAL_REPORT:-}"
fi
log_pass "PASS: runtime tools installed"
log_pass "PASS: rootfs content validation complete"

_run() {
  local name="$1"
  shift
  log_step "phase: ${name}"
  set +e
  "$@"
  local rc=$?
  set -e
  echo "phase_${name}_rc: ${rc}" >> "$FINAL_REPORT"
  return "$rc"
}

FAIL=0

log_step "pre-squashfs artifact rotation"
runtime_cleanup_old_squashfs_artifacts

_run "squashfs" "${DIR}/10_build_squashfs.sh" || FAIL=1
_run "initramfs" "${DIR}/20_install_initramfs_hook.sh" || FAIL=1
_run "verify_boot" "${DIR}/11_verify_runtime_boot.sh" || FAIL=1

{
  echo
  echo "=== Artifacts ==="
  echo "squashfs: ${RUNTIME_SQUASHFS}"
  echo "vmlinuz: ${ROOTFS_RESOLVED}/boot/vmlinuz-${KEEP_KERNEL_FLAVOR}"
  echo "initrd_recoverix: $(recoverix_initrd_runtime_path "${KEEP_KERNEL_FLAVOR}" 2>/dev/null || echo n/a)"
  echo "grub_template: ${DIR}/deploy/grub/recoverix-esp.cfg.template"
  echo "overlay_paths: ${DIR}/overlay/recoverix-overlay-paths.conf"
  echo
  echo "=== Runtime architecture ==="
  if runtime_build_gui_enabled; then
    echo "EFI -> grub -> kernel -> initramfs -> squashfs(ro) + tmpfs overlay -> graphical.target -> GDM -> openbox -> GTK UI"
  else
    echo "EFI -> grub -> kernel -> initramfs -> squashfs(ro) + tmpfs overlay -> multi-user.target -> recoverix-runtime-tui.service -> Recovery Runtime TUI"
  fi
  echo "runtime_session: user=${RECOVERIX_RUNTIME_USER:-recoverix} uid=${RECOVERIX_RUNTIME_UID:-2000} gid=${RECOVERIX_RUNTIME_GID:-2000} autologin=${RECOVERIX_RUNTIME_AUTOLOGIN:-1}"
  if runtime_build_gui_enabled; then
    echo "runtime_ui_autostart: /etc/xdg/autostart/recoverix-recovery-ui.desktop"
  else
    echo "runtime_ui_autostart: tty1 systemd service (/etc/systemd/system/recoverix-runtime-tui.service)"
  fi
  echo
} >> "$FINAL_REPORT"

if [[ -f "${RUNTIME_SQUASHFS}" ]]; then
  echo "squashfs_size_bytes: $(stat -c '%s' "${RUNTIME_SQUASHFS}")" >> "$FINAL_REPORT"
fi

BUILD_STAMP_FILE="${RUNTIME_DIR}/latest_runtime_build.stamp"
{
  echo "build_timestamp_utc: ${TS}"
  echo "build_epoch: $(date -u +%s)"
  echo "runtime_squashfs: ${RUNTIME_SQUASHFS}"
  echo "runtime_squashfs_mtime: $(stat -c '%Y' "${RUNTIME_SQUASHFS}" 2>/dev/null || echo unknown)"
  echo "runtime_initrd: $(recoverix_initrd_runtime_path "${KEEP_KERNEL_FLAVOR}")"
  echo "runtime_initrd_mtime: $(stat -c '%Y' "$(recoverix_initrd_runtime_path "${KEEP_KERNEL_FLAVOR}")" 2>/dev/null || echo unknown)"
} > "${BUILD_STAMP_FILE}"
echo "build_stamp: ${BUILD_STAMP_FILE}" >> "$FINAL_REPORT"

INVENTORY_REPORT="${REPORT_DIR}/runtime_artifact_inventory_${TS}.txt"
runtime_write_build_artifact_inventory "$INVENTORY_REPORT"
echo "artifact_inventory: ${INVENTORY_REPORT}" >> "$FINAL_REPORT"

if [[ $FAIL -ne 0 ]]; then
  echo "pipeline_status: FAILED" >> "$FINAL_REPORT"
  die "runtime build pipeline failed — see ${FINAL_REPORT}"
fi

echo "pipeline_status: OK" >> "$FINAL_REPORT"
echo "runtime_rootfs_source: ${RECOVERIX_RUNTIME_ROOTFS_SOURCE:-unknown}" >> "$FINAL_REPORT"

runtime_maybe_cleanup_rootfs_after_build || log_warn "WARN: optional rootfs cleanup skipped or failed"

log_pass "Pipeline complete: ${FINAL_REPORT}"
log "runtime rootfs source: ${RECOVERIX_RUNTIME_ROOTFS_SOURCE:-unknown}"
log "Next (deployment phase, not this script): install grub cfg + ESP — host boot still unchanged"
exit 0
