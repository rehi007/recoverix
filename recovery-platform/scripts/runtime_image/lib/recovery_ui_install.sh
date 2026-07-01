#!/usr/bin/env bash
# Install GTK Recovery UI into rootfs (Python package + launcher + autostart).

recoverix_repo_root() {
  if [[ -n "${RECOVERIX_REPO_ROOT:-}" ]]; then
    printf '%s' "${RECOVERIX_REPO_ROOT}"
    return 0
  fi
  printf '%s' "$(cd "$(runtime_image_dir)/../../.." && pwd)"
}

recoverix_recovery_runtime_src() {
  printf '%s/recovery-platform/recovery_runtime' "$(recoverix_repo_root)"
}

recoverix_recovery_ui_lib_root() {
  printf '%s/usr/local/lib/recoverix' "${ROOTFS_RESOLVED}"
}

recoverix_recovery_ui_launcher_path() {
  printf '%s/usr/local/sbin/recoverix-recovery-ui' "${ROOTFS_RESOLVED}"
}

recoverix_runtime_tui_launcher_path() {
  printf '%s/usr/local/sbin/recoverix-runtime-tui' "${ROOTFS_RESOLVED}"
}

recoverix_runtime_tui_prepare_path() {
  printf '%s/usr/local/sbin/recoverix-runtime-prepare' "${ROOTFS_RESOLVED}"
}

recoverix_runtime_tui_autostart_profile_path() {
  printf '%s/etc/profile.d/recoverix-runtime-tui-autostart.sh' "${ROOTFS_RESOLVED}"
}

recoverix_runtime_tui_service_path() {
  printf '%s/etc/systemd/system/recoverix-runtime-tui.service' "${ROOTFS_RESOLVED}"
}

recoverix_recovery_ui_autostart_path() {
  printf '%s/etc/xdg/autostart/recoverix-recovery-ui.desktop' "${ROOTFS_RESOLVED}"
}

recoverix_recovery_ui_openbox_autostart_path() {
  printf '%s/etc/xdg/openbox/autostart' "${ROOTFS_RESOLVED}"
}

recoverix_recovery_ui_openbox_session_path() {
  printf '%s/usr/local/sbin/recoverix-openbox-session' "${ROOTFS_RESOLVED}"
}

recoverix_recovery_ui_launch_helper_path() {
  printf '%s/usr/local/sbin/recoverix-gui-launch' "${ROOTFS_RESOLVED}"
}

recoverix_recovery_ui_launch_service_path() {
  printf '%s/etc/systemd/system/recoverix-gui-launch.service' "${ROOTFS_RESOLVED}"
}

recoverix_boot_diagnostics_service_path() {
  printf '%s/etc/systemd/system/recoverix-boot-diagnostics.service' "${ROOTFS_RESOLVED}"
}

recoverix_runtime_user() { printf '%s' "${RECOVERIX_RUNTIME_USER:-recoverix}"; }
recoverix_runtime_group() { printf '%s' "${RECOVERIX_RUNTIME_GROUP:-recoverix}"; }
recoverix_runtime_uid() { printf '%s' "${RECOVERIX_RUNTIME_UID:-2000}"; }
recoverix_runtime_gid() { printf '%s' "${RECOVERIX_RUNTIME_GID:-2000}"; }
recoverix_runtime_home() { printf '%s' "${RECOVERIX_RUNTIME_HOME:-/home/recoverix}"; }

recoverix_runtime_gdm_custom_conf_path() {
  printf '%s/etc/gdm3/custom.conf' "${ROOTFS_RESOLVED}"
}

runtime_install_recoverix_runtime_user_session() {
  local user group uid gid home mounted=0 console_pw supp_groups_csv
  user="$(recoverix_runtime_user)"
  group="$(recoverix_runtime_group)"
  uid="$(recoverix_runtime_uid)"
  gid="$(recoverix_runtime_gid)"
  home="$(recoverix_runtime_home)"
  console_pw="${RECOVERIX_RUNTIME_CONSOLE_PASSWORD:-recoverix}"
  supp_groups_csv="${RECOVERIX_RUNTIME_SUPP_GROUPS:-video,render,input,tty}"

  log "=== Configure recoverix runtime user/session ==="
  log "user=${user} uid=${uid} group=${group} gid=${gid} home=${home}"
  log "console_password_policy=recoverix login enabled (RECOVERIX_RUNTIME_CONSOLE_PASSWORD)"

  if runtime_chroot_mount_deps "${ROOTFS_RESOLVED}"; then
    mounted=1
  fi

if ! chroot "${ROOTFS_RESOLVED}" /bin/bash -eu -c "
set -o pipefail
if getent group '${group}' >/dev/null 2>&1; then
  current_gid=\$(getent group '${group}' | cut -d: -f3)
  if [[ \"\$current_gid\" != '${gid}' ]]; then
    groupmod -g '${gid}' '${group}'
  fi
else
  groupadd -g '${gid}' '${group}'
fi

if id -u '${user}' >/dev/null 2>&1; then
  usermod -u '${uid}' -g '${gid}' -d '${home}' -s /bin/bash '${user}'
else
  useradd -m -u '${uid}' -g '${gid}' -d '${home}' -s /bin/bash '${user}'
fi

mkdir -p '${home}'
chown -R '${uid}:${gid}' '${home}'
chmod 0755 /home
chmod 0755 '${home}'
echo '${user}:${console_pw}' | chpasswd
passwd -u '${user}' 2>/dev/null || true

supp_groups=''
IFS=',' read -r -a _recoverix_groups <<'GROUPS'
${supp_groups_csv}
GROUPS
for _grp in \"\${_recoverix_groups[@]}\"; do
  [[ -n \"\${_grp}\" ]] || continue
  if getent group \"\${_grp}\" >/dev/null 2>&1; then
    if [[ -n \"\${supp_groups}\" ]]; then
      supp_groups=\"\${supp_groups},\${_grp}\"
    else
      supp_groups=\"\${_grp}\"
    fi
  fi
done
if [[ -n \"\${supp_groups}\" ]]; then
  usermod -a -G \"\${supp_groups}\" '${user}'
fi
"; then
    [[ $mounted -eq 1 ]] && runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
    log "FAIL: failed to configure recoverix runtime user"
    return 1
  fi

  if [[ "${RECOVERIX_RUNTIME_ALLOW_SUDO:-0}" == "1" ]]; then
    mkdir -p "${ROOTFS_RESOLVED}/etc/sudoers.d"
    printf '%s ALL=(ALL:ALL) NOPASSWD:ALL\n' "$user" >"${ROOTFS_RESOLVED}/etc/sudoers.d/99-recoverix-runtime"
    chmod 0440 "${ROOTFS_RESOLVED}/etc/sudoers.d/99-recoverix-runtime"
    log "WARN: runtime sudo enabled by RECOVERIX_RUNTIME_ALLOW_SUDO=1"
  else
    rm -f "${ROOTFS_RESOLVED}/etc/sudoers.d/99-recoverix-runtime" 2>/dev/null || true
    log "PASS: runtime sudo disabled by default"
  fi

  [[ $mounted -eq 1 ]] && runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
  log "PASS: recoverix runtime user/session configured (password login enabled)"
  return 0
}

runtime_install_recoverix_console_access() {
  local user issue_dir getty_drop serial_drop
  user="$(recoverix_runtime_user)"
  issue_dir="${ROOTFS_RESOLVED}/etc/recoverix"
  getty_drop="${ROOTFS_RESOLVED}/etc/systemd/system/getty@.service.d"
  serial_drop="${ROOTFS_RESOLVED}/etc/systemd/system/serial-getty@.service.d"

  log "=== Configure runtime console access (tty autologin + forensic banner) ==="

  mkdir -p "$issue_dir" "$getty_drop" "$serial_drop"
  cat >"${issue_dir}/forensic-console.issue" <<'EOF'
Recoverix forensic console mode

EOF
  chmod 0644 "${issue_dir}/forensic-console.issue"

  if [[ "${RECOVERIX_RUNTIME_CONSOLE_AUTOLOGIN:-1}" == "1" ]]; then
    cat >"${getty_drop}/recoverix-console-autologin.conf" <<EOF
[Unit]
ConditionKernelCommandLine=recoverix.root=1

[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${user} --issue-file /etc/recoverix/forensic-console.issue --noclear %I \$TERM
EOF
    install -m 0644 "${getty_drop}/recoverix-console-autologin.conf" \
      "${serial_drop}/recoverix-console-autologin.conf"
    log "PASS: tty autologin enabled for ${user} (recoverix.root=1)"
  else
    rm -f "${getty_drop}/recoverix-console-autologin.conf" \
      "${serial_drop}/recoverix-console-autologin.conf" \
      "${getty_drop}/recoverix-xorg-forensic-console.conf" \
      "${serial_drop}/recoverix-xorg-forensic-console.conf" 2>/dev/null || true
    log "PASS: tty autologin disabled by RECOVERIX_RUNTIME_CONSOLE_AUTOLOGIN=0"
  fi

  return 0
}

# Diagnostic-only: records real rootfs/tty state once, ordered after local-fs
# and before multi-user.target. Independent of forensic units (no cmdline gate).
runtime_install_recoverix_rootfs_debug() {
  local svc="${ROOTFS_RESOLVED}/etc/systemd/system/recoverix-rootfs-debug.service"

  log "=== Configure Recoverix rootfs/tty state diagnostics ==="

  runtime_install_rootfs_tool "76_recoverix_rootfs_debug.sh" "recoverix-rootfs-debug" || return 1

  cat >"$svc" <<'EOF'
[Unit]
Description=Recoverix rootfs/tty state diagnostics (post-handoff)
DefaultDependencies=no
After=local-fs.target multi-user.target
ConditionKernelCommandLine=recoverix.debug.xorg=1

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/mkdir -p /var/log
ExecStart=/usr/local/sbin/recoverix-rootfs-debug
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "$svc"

  runtime_systemd_enable_unit_wants recoverix-rootfs-debug.service multi-user.target || return 1
  log "PASS: recoverix-rootfs-debug.service installed (After=local-fs.target multi-user.target)"
  return 0
}

runtime_verify_rootfs_recoverix_rootfs_debug() {
  local root="${ROOTFS_RESOLVED}"
  local svc="${root}/etc/systemd/system/recoverix-rootfs-debug.service"
  local tool="${root}/usr/local/sbin/recoverix-rootfs-debug"
  local link="${root}/etc/systemd/system/multi-user.target.wants/recoverix-rootfs-debug.service"
  local failures=0

  log "=== Rootfs/tty debug diagnostics verification ==="

  if [[ -x "$tool" ]]; then
    log "PASS: recoverix-rootfs-debug tool present and executable"
  else
    log "FAIL: recoverix-rootfs-debug tool missing or not executable"
    failures=$((failures + 1))
  fi

  if [[ -f "$svc" ]]; then
    log "PASS: recoverix-rootfs-debug.service present"
    if grep -qE '^After=.*\bmulti-user\.target\b' "$svc" && \
       ! grep -qE '^Before=.*\bmulti-user\.target\b' "$svc"; then
      log "PASS: recoverix-rootfs-debug.service ordered After=multi-user.target (not Before)"
    else
      log "FAIL: recoverix-rootfs-debug.service must be After=multi-user.target and not Before=multi-user.target"
      failures=$((failures + 1))
    fi
    if grep -q 'recoverix-rootfs-debug$' "$svc" || grep -q 'ExecStart=/usr/local/sbin/recoverix-rootfs-debug' "$svc"; then
      log "PASS: recoverix-rootfs-debug.service ExecStart wired"
    else
      log "FAIL: recoverix-rootfs-debug.service ExecStart missing"
      failures=$((failures + 1))
    fi
  else
    log "FAIL: recoverix-rootfs-debug.service missing"
    failures=$((failures + 1))
  fi

  if [[ -L "$link" ]]; then
    log "PASS: recoverix-rootfs-debug.service enabled (multi-user.target.wants)"
  else
    log "FAIL: recoverix-rootfs-debug.service not enabled for multi-user.target"
    failures=$((failures + 1))
  fi

  # GUI-path forensic collection (Xorg/GDM/seat/DRM) must be present in the tool.
  if [[ -x "$tool" ]]; then
    if grep -q 'display-manager.service' "$tool"; then
      log "PASS: display-manager forensic collection enabled"
    else
      log "FAIL: display-manager forensic collection missing"
      failures=$((failures + 1))
    fi
    if grep -q 'gdm.service' "$tool"; then
      log "PASS: gdm forensic collection enabled"
    else
      log "FAIL: gdm forensic collection missing"
      failures=$((failures + 1))
    fi
    if grep -q 'seat-status seat0' "$tool"; then
      log "PASS: seat forensic collection enabled"
    else
      log "FAIL: seat forensic collection missing"
      failures=$((failures + 1))
    fi
    if grep -q '/dev/dri' "$tool" && grep -q '/sys/class/drm' "$tool"; then
      log "PASS: drm forensic collection enabled"
    else
      log "FAIL: drm forensic collection missing"
      failures=$((failures + 1))
    fi
    if grep -q 'GDM deep forensic' "$tool" && \
       grep -q 'systemctl cat gdm.service' "$tool" && \
       grep -q '/etc/gdm3/custom.conf' "$tool" && \
       grep -q 'list-dependencies display-manager.service' "$tool"; then
      log "PASS: gdm deep forensic enabled"
    else
      log "FAIL: gdm deep forensic missing"
      failures=$((failures + 1))
    fi
    if grep -q 'DRM seat deep forensic' "$tool" && \
       grep -q 'journalctl -b -u systemd-logind.service' "$tool" && \
       grep -q '/dev/dri/by-path' "$tool" && \
       grep -q 'udevadm info -q all -n /dev/dri/card0' "$tool"; then
      log "PASS: drm seat deep forensic enabled"
    else
      log "FAIL: drm seat deep forensic missing"
      failures=$((failures + 1))
    fi
    if grep -q 'AMD GPU deep forensic' "$tool" && \
       grep -q 'modinfo amdgpu' "$tool" && \
       grep -q "find /lib/firmware -iname '\*amdgpu\*'" "$tool" && \
       grep -q 'lsinitramfs' "$tool"; then
      log "PASS: amd gpu deep forensic enabled"
    else
      log "FAIL: amd gpu deep forensic missing"
      failures=$((failures + 1))
    fi
  fi

  [[ $failures -eq 0 ]] || return 1
  log "PASS: rootfs/tty debug diagnostics validated"
  return 0
}

runtime_install_recoverix_boot_diagnostics() {
  local svc
  svc="$(recoverix_boot_diagnostics_service_path)"

  log "=== Configure Recoverix boot diagnostics capture ==="

  runtime_install_rootfs_tool "77_recoverix_boot_diagnostics.sh" "recoverix-boot-diagnostics" || return 1

  mkdir -p "${ROOTFS_RESOLVED}/etc/systemd/system"
  cat >"$svc" <<'EOF'
[Unit]
Description=Recoverix boot and GUI startup diagnostics
After=multi-user.target recoverix-gui-launch.service
ConditionKernelCommandLine=recoverix.root=1
ConditionKernelCommandLine=recoverix.gui=1

[Service]
Type=simple
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=120
ExecStart=/usr/local/sbin/recoverix-boot-diagnostics
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "$svc"

  runtime_systemd_enable_unit_wants recoverix-boot-diagnostics.service multi-user.target || return 1
  log "PASS: recoverix-boot-diagnostics.service installed (post-GUI delayed collection)"
  return 0
}

runtime_verify_rootfs_recoverix_boot_diagnostics() {
  local root="${ROOTFS_RESOLVED}"
  local svc="${root}/etc/systemd/system/recoverix-boot-diagnostics.service"
  local tool="${root}/usr/local/sbin/recoverix-boot-diagnostics"
  local link="${root}/etc/systemd/system/multi-user.target.wants/recoverix-boot-diagnostics.service"
  local failures=0

  log "=== Rootfs boot diagnostics verification ==="

  if [[ -x "$tool" ]]; then
    log "PASS: recoverix-boot-diagnostics tool present and executable"
  else
    log "FAIL: recoverix-boot-diagnostics tool missing or not executable"
    failures=$((failures + 1))
  fi

  if [[ -f "$svc" ]]; then
    log "PASS: recoverix-boot-diagnostics.service present"
    if grep -q '^After=.*recoverix-gui-launch\.service' "$svc" && \
       grep -q '^After=.*multi-user\.target' "$svc"; then
      log "PASS: boot diagnostics service ordered after GUI launch path"
    else
      log "FAIL: boot diagnostics service ordering invalid"
      failures=$((failures + 1))
    fi
    if grep -q '^ConditionKernelCommandLine=recoverix.root=1$' "$svc" && \
       grep -q '^ConditionKernelCommandLine=recoverix.gui=1$' "$svc"; then
      log "PASS: boot diagnostics service gated to Recoverix GUI boot"
    else
      log "FAIL: boot diagnostics service missing Recoverix GUI cmdline guards"
      failures=$((failures + 1))
    fi
    if grep -q '^ExecStart=/usr/local/sbin/recoverix-boot-diagnostics$' "$svc"; then
      log "PASS: boot diagnostics service ExecStart wired"
    else
      log "FAIL: boot diagnostics service ExecStart missing"
      failures=$((failures + 1))
    fi
    if grep -q '^Type=simple$' "$svc"; then
      log "PASS: boot diagnostics service does not block boot completion"
    else
      log "FAIL: boot diagnostics service must use Type=simple"
      failures=$((failures + 1))
    fi
  else
    log "FAIL: recoverix-boot-diagnostics.service missing"
    failures=$((failures + 1))
  fi

  if [[ -L "$link" ]]; then
    log "PASS: recoverix-boot-diagnostics.service enabled (multi-user.target.wants)"
  else
    log "FAIL: recoverix-boot-diagnostics.service not enabled for multi-user.target"
    failures=$((failures + 1))
  fi

  if [[ -x "$tool" ]]; then
    if grep -q 'systemd-analyze blame' "$tool" && \
       grep -q 'journal-current-boot.log' "$tool" && \
       grep -q '/run/recoverix-boot' "$tool" && \
       grep -q 'ln -sfn "${stamp}"' "$tool" && \
       grep -q 'wait_for_systemd_analyze_ready' "$tool"; then
      log "PASS: boot diagnostics timing/journal/persistent collection enabled"
    else
      log "FAIL: boot diagnostics tool missing expected timing or persistent collection"
      failures=$((failures + 1))
    fi
  fi

  [[ $failures -eq 0 ]] || return 1
  log "PASS: boot diagnostics validated"
  return 0
}

recoverix_forensic_sudo_service_relpath() {
  printf '%s' 'etc/systemd/system/recoverix-forensic-sudo.service'
}

recoverix_forensic_sudo_helper_relpath() {
  printf '%s' 'usr/local/sbin/recoverix-forensic-sudo-enable'
}

recoverix_forensic_sudoers_relpath() {
  printf '%s' 'etc/sudoers.d/99-recoverix-forensic'
}

recoverix_forensic_sudo_staging_relpath() {
  printf '%s' 'etc/recoverix/staging/sudoers-recoverix-forensic'
}

recoverix_forensic_sudo_service_path() {
  printf '%s/%s' "${ROOTFS_RESOLVED}" "$(recoverix_forensic_sudo_service_relpath)"
}

recoverix_forensic_sudo_helper_path() {
  printf '%s/%s' "${ROOTFS_RESOLVED}" "$(recoverix_forensic_sudo_helper_relpath)"
}

recoverix_forensic_sudoers_path() {
  printf '%s/%s' "${ROOTFS_RESOLVED}" "$(recoverix_forensic_sudoers_relpath)"
}

recoverix_forensic_sudo_assets_source() {
  printf '%s/assets/sudoers-recoverix-forensic' "$(runtime_image_dir)"
}

recoverix_forensic_sudo_helper_source() {
  runtime_tool_deploy_source "75_recoverix_forensic_sudo_enable.sh"
}

runtime_install_recoverix_forensic_sudo() {
  local staging_dir staging_src enable_svc selftest_svc user active_sudoers assets_src
  staging_dir="${ROOTFS_RESOLVED}/etc/recoverix/staging"
  staging_src="${ROOTFS_RESOLVED}/$(recoverix_forensic_sudo_staging_relpath)"
  enable_svc="$(recoverix_forensic_sudo_service_path)"
  active_sudoers="$(recoverix_forensic_sudoers_path)"
  selftest_svc="${ROOTFS_RESOLVED}/etc/systemd/system/recoverix-forensic-sudo-selftest.service"
  assets_src="$(recoverix_forensic_sudo_assets_source)"
  user="$(recoverix_runtime_user)"

  log "=== Configure Recoverix forensic limited sudo ==="

  if [[ "${RECOVERIX_RUNTIME_FORENSIC_SUDO:-1}" != "1" ]]; then
    rm -f "${ROOTFS_RESOLVED}/etc/sudoers.d/99-recoverix-forensic" \
      "${staging_src}" "${enable_svc}" "${selftest_svc}" \
      "${ROOTFS_RESOLVED}/etc/systemd/system/multi-user.target.wants/recoverix-forensic-sudo.service" \
      "${ROOTFS_RESOLVED}/etc/systemd/system/sysinit.target.wants/recoverix-forensic-sudo.service" \
      "${ROOTFS_RESOLVED}/etc/systemd/system/multi-user.target.wants/recoverix-forensic-sudo-selftest.service" \
      2>/dev/null || true
    log "PASS: forensic sudo disabled by RECOVERIX_RUNTIME_FORENSIC_SUDO=0"
    return 0
  fi

  mkdir -p "$staging_dir" "${ROOTFS_RESOLVED}/etc/sudoers.d"
  install -m 0440 -o root -g root "$assets_src" "$staging_src"
  install -m 0440 -o root -g root "$assets_src" "$active_sudoers"
  chmod 0440 "$staging_src" "$active_sudoers"
  chown root:root "$staging_src" "$active_sudoers" 2>/dev/null || true

  log "forensic_sudo_service_source: ${enable_svc} (generated at install)"
  log "forensic_sudo_service_target: /$(recoverix_forensic_sudo_service_relpath)"
  log "forensic_sudo_helper_source: $(recoverix_forensic_sudo_helper_source)"
  log "forensic_sudo_helper_target: /$(recoverix_forensic_sudo_helper_relpath)"
  log "forensic_sudoers_source: ${assets_src}"
  log "forensic_sudoers_target: /$(recoverix_forensic_sudoers_relpath)"

  runtime_install_rootfs_tool "75_recoverix_forensic_sudo_enable.sh" "recoverix-forensic-sudo-enable" || return 1

  cat >"$enable_svc" <<'EOF'
[Unit]
Description=Recoverix forensic limited sudo (recoverix.root / recoverix.debug.xorg)
DefaultDependencies=no
After=local-fs.target systemd-remount-fs.service
Before=getty-pre.target getty.target getty@.service multi-user.target graphical.target
ConditionKernelCommandLine=!recoverix.safe=1
ConditionKernelCommandLine=!recoverix.isolation=1

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/mkdir -p /var/log /run/recoverix
ExecStart=/usr/local/sbin/recoverix-forensic-sudo-enable start
ExecStartPost=/usr/local/sbin/recoverix-forensic-sudo-enable selftest
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

  cat >"$selftest_svc" <<'EOF'
[Unit]
Description=Recoverix forensic sudo boot self-test
DefaultDependencies=no
After=recoverix-forensic-sudo.service
Before=getty-pre.target getty.target getty@.service
ConditionPathExists=/run/recoverix/forensic-sudo-enabled
ConditionKernelCommandLine=!recoverix.safe=1
ConditionKernelCommandLine=!recoverix.isolation=1

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/recoverix-forensic-sudo-enable selftest
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

  chmod 0644 "$enable_svc" "$selftest_svc"

  runtime_systemd_enable_unit_wants recoverix-forensic-sudo.service \
    sysinit.target multi-user.target || return 1
  runtime_systemd_enable_unit_wants recoverix-forensic-sudo-selftest.service \
    multi-user.target || return 1

  local gdm_drop gdm3_drop gdm_conf
  gdm_drop="${ROOTFS_RESOLVED}/etc/systemd/system/gdm.service.d"
  gdm3_drop="${ROOTFS_RESOLVED}/etc/systemd/system/gdm3.service.d"
  mkdir -p "$gdm_drop" "$gdm3_drop"
  gdm_conf="${gdm_drop}/recoverix-forensic-sudo.conf"
  cat >"$gdm_conf" <<'EOF'
[Unit]
ConditionKernelCommandLine=recoverix.root
ConditionKernelCommandLine=!recoverix.isolation=1

[Service]
ExecStartPre=-/usr/local/sbin/recoverix-forensic-sudo-enable start
EOF
  install -m 0644 "$gdm_conf" "${gdm3_drop}/recoverix-forensic-sudo.conf"

  if runtime_chroot_systemd_available && runtime_chroot_mount_deps "${ROOTFS_RESOLVED}"; then
    chroot "${ROOTFS_RESOLVED}" systemctl is-enabled recoverix-forensic-sudo.service 2>/dev/null \
      | sed 's/^/systemctl_enable_result: /' || log "WARN: systemctl is-enabled check skipped"
    runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
  fi

  runtime_assert_rootfs_recoverix_forensic_sudo || return 1
  log "PASS: forensic sudo installed for ${user} (service + helper + sudoers in rootfs)"
  return 0
}

runtime_chroot_systemd_available() {
  [[ -d "${ROOTFS_RESOLVED}/etc/systemd/system" && -x "${ROOTFS_RESOLVED}/usr/bin/systemctl" ]]
}

# Run a shell command as recoverix inside rootfs chroot (for sudo runtime tests).
runtime_chroot_exec_as_recoverix() {
  local root="${1:?}" cmd="${2:?}" user
  user="$(recoverix_runtime_user)"
  if chroot "$root" command -v runuser >/dev/null 2>&1; then
    chroot "$root" runuser -u "$user" -- /bin/sh -c "$cmd"
    return $?
  fi
  chroot "$root" su - "$user" -s /bin/bash -c "$cmd"
}

# NOPASSWD-safe sudo command for runtime self-test (absolute /usr/bin/true, fallback /bin/true).
recoverix_forensic_sudo_runtime_test_sudo_cmd() {
  local root="${1:-}"
  if [[ -n "$root" ]]; then
    [[ -x "${root}/usr/bin/true" ]] && { printf 'sudo -n /usr/bin/true'; return 0; }
    [[ -x "${root}/bin/true" ]] && { printf 'sudo -n /bin/true'; return 0; }
  else
    [[ -x /usr/bin/true ]] && { printf 'sudo -n /usr/bin/true'; return 0; }
    [[ -x /bin/true ]] && { printf 'sudo -n /bin/true'; return 0; }
  fi
  printf 'sudo -n /usr/bin/journalctl --version'
}

recoverix_forensic_sudo_allowed_test_sudo_cmd() {
  printf 'sudo -n /usr/bin/journalctl --version'
}

# B) sudo -n /usr/bin/true  C) sudo -n journalctl --version (no sudo -l).
runtime_chroot_forensic_sudo_runtime_verify() {
  local root="${ROOTFS_RESOLVED}" user sudo_cmd wrap_cmd out rc failures=0
  user="$(recoverix_runtime_user)"

  if ! chroot "$root" id "$user" >/dev/null 2>&1; then
    log "FAIL: forensic sudo runtime execution test (user ${user} missing in chroot)"
    log "forensic_sudo_runtime_test: FAIL"
    return 1
  fi

  sudo_cmd="$(recoverix_forensic_sudo_runtime_test_sudo_cmd "$root")"
  if chroot "$root" command -v runuser >/dev/null 2>&1; then
    wrap_cmd="runuser -u ${user} -- ${sudo_cmd}"
  else
    wrap_cmd="su - ${user} -c '${sudo_cmd}'"
  fi
  log "forensic_sudo_runtime_test_cmd: chroot ${root} ${wrap_cmd}"
  set +e
  out="$(runtime_chroot_exec_as_recoverix "$root" "$sudo_cmd" 2>&1)"
  rc=$?
  if [[ $rc -ne 0 ]] && [[ "$sudo_cmd" == 'sudo -n /usr/bin/true' ]] && chroot "$root" test -x /bin/true; then
    sudo_cmd='sudo -n /bin/true'
    log "forensic_sudo_runtime_test_cmd: fallback ${sudo_cmd}"
    out="$(runtime_chroot_exec_as_recoverix "$root" "$sudo_cmd" 2>&1)"
    rc=$?
  fi
  set -e
  log "forensic_sudo_runtime_test_rc: ${rc}"
  if [[ $rc -ne 0 ]] || grep -qiE 'a password is required|sudo:.*password|^\[sudo\] password|not allowed' <<<"$out"; then
    log "forensic_sudo_runtime_test: FAIL"
    log "FAIL: forensic sudo runtime execution test (rc=${rc} ${out})"
    failures=$((failures + 1))
  else
    log "forensic_sudo_runtime_test: PASS"
    log "PASS: forensic sudo runtime execution test (${sudo_cmd})"
  fi

  sudo_cmd="$(recoverix_forensic_sudo_allowed_test_sudo_cmd)"
  if chroot "$root" command -v runuser >/dev/null 2>&1; then
    wrap_cmd="runuser -u ${user} -- ${sudo_cmd}"
  else
    wrap_cmd="su - ${user} -c '${sudo_cmd}'"
  fi
  log "forensic_sudo_allowed_cmd_test_cmd: chroot ${root} ${wrap_cmd}"
  set +e
  out="$(runtime_chroot_exec_as_recoverix "$root" "$sudo_cmd" 2>&1)"
  rc=$?
  set -e
  log "forensic_sudo_allowed_cmd_test_rc: ${rc}"
  if [[ $rc -ne 0 ]] || grep -qiE 'a password is required|sudo:.*password|^\[sudo\] password|not allowed' <<<"$out"; then
    log "forensic_sudo_allowed_cmd_test: FAIL"
    log "FAIL: forensic sudo allowed command test (rc=${rc} ${out})"
    failures=$((failures + 1))
  else
    log "forensic_sudo_allowed_cmd_test: PASS"
    log "PASS: forensic sudo allowed command test (${sudo_cmd})"
  fi

  [[ $failures -eq 0 ]] || return 1
  return 0
}

runtime_assert_rootfs_recoverix_forensic_sudo() {
  local root="${ROOTFS_RESOLVED}"
  local svc helper sudoers wants_ok=0

  if [[ "${RECOVERIX_RUNTIME_FORENSIC_SUDO:-1}" != "1" ]]; then
    return 0
  fi

  svc="$(recoverix_forensic_sudo_service_path)"
  helper="$(recoverix_forensic_sudo_helper_path)"
  sudoers="$(recoverix_forensic_sudoers_path)"

  [[ -f "$svc" ]] || { log "FAIL: missing ${svc}"; return 1; }
  [[ -x "$helper" ]] || { log "FAIL: missing or not executable ${helper}"; return 1; }
  [[ -f "$sudoers" ]] || { log "FAIL: missing ${sudoers}"; return 1; }

  if [[ -L "${root}/etc/systemd/system/sysinit.target.wants/recoverix-forensic-sudo.service" ]] || \
     [[ -L "${root}/etc/systemd/system/multi-user.target.wants/recoverix-forensic-sudo.service" ]]; then
    wants_ok=1
  fi
  [[ $wants_ok -eq 1 ]] || { log "FAIL: recoverix-forensic-sudo.service wants symlink missing"; return 1; }

  return 0
}

runtime_verify_rootfs_recoverix_forensic_sudo() {
  local root="${ROOTFS_RESOLVED}"
  local staging svc helper sudoers failures=0 mounted=0
  local unit_cat mode_own

  log "=== Rootfs forensic sudo verification ==="

  if [[ "${RECOVERIX_RUNTIME_FORENSIC_SUDO:-1}" != "1" ]]; then
    log "PASS: forensic sudo policy disabled by config"
    return 0
  fi

  staging="${root}/$(recoverix_forensic_sudo_staging_relpath)"
  svc="$(recoverix_forensic_sudo_service_path)"
  helper="$(recoverix_forensic_sudo_helper_path)"
  sudoers="$(recoverix_forensic_sudoers_path)"

  if [[ ! -f "$svc" ]]; then
    log "FAIL: service file missing (${svc})"
    failures=$((failures + 1))
  else
    log "PASS: $(recoverix_forensic_sudo_service_relpath) present"
    if grep -q 'recoverix-forensic-sudo-enable start' "$svc" 2>/dev/null; then
      log "PASS: service ExecStart references forensic sudo helper"
    else
      log "FAIL: service missing recoverix-forensic-sudo-enable ExecStart"
      failures=$((failures + 1))
    fi
  fi

  if [[ ! -x "$helper" ]]; then
    log "FAIL: helper missing or not executable (${helper})"
    failures=$((failures + 1))
  else
    log "PASS: $(recoverix_forensic_sudo_helper_relpath) present and executable"
  fi

  if [[ ! -f "$sudoers" ]]; then
    log "FAIL: sudoers missing (${sudoers})"
    failures=$((failures + 1))
  else
    mode_own="$(stat -c '%a %U:%G' "$sudoers" 2>/dev/null || echo unknown)"
    if [[ "$mode_own" == "440 root:root" || "$mode_own" == "0440 root:root" ]]; then
      log "PASS: sudoers mode/owner ${mode_own}"
    else
      log "FAIL: sudoers mode/owner ${mode_own} (expected 0440 root:root)"
      failures=$((failures + 1))
    fi
    if visudo -cf "$sudoers" >/dev/null 2>&1; then
      log "PASS: forensic sudoers syntax verify (visudo -cf active)"
    else
      log "FAIL: forensic sudoers syntax verify (visudo -cf active)"
      visudo -cf "$sudoers" 2>&1 | while IFS= read -r line; do log "  ${line}"; done
      failures=$((failures + 1))
    fi
  fi

  if [[ ! -f "$staging" ]]; then
    log "FAIL: missing forensic sudo staging ${staging}"
    failures=$((failures + 1))
  elif visudo -cf "$staging" >/dev/null 2>&1; then
    log "PASS: forensic sudoers syntax verify (visudo -cf staging)"
  else
    log "FAIL: forensic sudoers syntax verify (visudo -cf staging)"
    failures=$((failures + 1))
  fi

  if [[ -L "${root}/etc/systemd/system/sysinit.target.wants/recoverix-forensic-sudo.service" ]]; then
    log "PASS: enabled via sysinit.target.wants"
  elif [[ -L "${root}/etc/systemd/system/multi-user.target.wants/recoverix-forensic-sudo.service" ]]; then
    log "PASS: enabled via multi-user.target.wants"
  else
    log "FAIL: recoverix-forensic-sudo.service wants symlink missing (sysinit and multi-user)"
    failures=$((failures + 1))
  fi

  if ! grep -q 'WantedBy=multi-user.target' "$svc" 2>/dev/null; then
    log "FAIL: recoverix-forensic-sudo.service missing WantedBy=multi-user.target"
    failures=$((failures + 1))
  fi

  if grep -q 'ConditionKernelCommandLine=!recoverix.safe=1' "$svc" 2>/dev/null; then
    log "PASS: recoverix-forensic-sudo.service skips on recoverix.safe=1"
  else
    log "FAIL: recoverix-forensic-sudo.service missing ConditionKernelCommandLine=!recoverix.safe=1"
    failures=$((failures + 1))
  fi
  if grep -q 'ConditionKernelCommandLine=!recoverix.isolation=1' "$svc" 2>/dev/null; then
    log "PASS: recoverix-forensic-sudo.service skips on recoverix.isolation=1"
  else
    log "FAIL: recoverix-forensic-sudo.service missing ConditionKernelCommandLine=!recoverix.isolation=1"
    failures=$((failures + 1))
  fi

  local selftest_svc="${root}/etc/systemd/system/recoverix-forensic-sudo-selftest.service"
  if [[ -f "$selftest_svc" ]]; then
    if grep -q 'ConditionKernelCommandLine=!recoverix.safe=1' "$selftest_svc" 2>/dev/null; then
      log "PASS: recoverix-forensic-sudo-selftest.service skips on recoverix.safe=1"
    else
      log "FAIL: recoverix-forensic-sudo-selftest.service missing ConditionKernelCommandLine=!recoverix.safe=1"
      failures=$((failures + 1))
    fi
    if grep -q 'ConditionKernelCommandLine=!recoverix.isolation=1' "$selftest_svc" 2>/dev/null; then
      log "PASS: recoverix-forensic-sudo-selftest.service skips on recoverix.isolation=1"
    else
      log "FAIL: recoverix-forensic-sudo-selftest.service missing ConditionKernelCommandLine=!recoverix.isolation=1"
      failures=$((failures + 1))
    fi
  fi

  local gdm_drop="${root}/etc/systemd/system/gdm.service.d/recoverix-forensic-sudo.conf"
  if [[ -f "$gdm_drop" ]]; then
    if grep -q 'ConditionKernelCommandLine=!recoverix.isolation=1' "$gdm_drop" 2>/dev/null; then
      log "PASS: gdm recoverix-forensic-sudo drop-in skips on recoverix.isolation=1"
    else
      log "FAIL: gdm recoverix-forensic-sudo drop-in missing ConditionKernelCommandLine=!recoverix.isolation=1"
      failures=$((failures + 1))
    fi
  fi

  if grep -q 'recoverix_safe_mode_detected' "$helper" 2>/dev/null; then
    log "PASS: forensic sudo helper has recoverix.safe=1 early-exit guard"
  else
    log "FAIL: forensic sudo helper missing recoverix.safe=1 early-exit guard"
    failures=$((failures + 1))
  fi
  if grep -q 'recoverix_isolation_mode_detected' "$helper" 2>/dev/null; then
    log "PASS: forensic sudo helper has recoverix.isolation=1 early-exit guard"
  else
    log "FAIL: forensic sudo helper missing recoverix.isolation=1 early-exit guard"
    failures=$((failures + 1))
  fi

  if runtime_chroot_mount_deps "${ROOTFS_RESOLVED}"; then
    mounted=1
    unit_cat="$(chroot "${ROOTFS_RESOLVED}" systemctl cat recoverix-forensic-sudo.service 2>&1 || true)"
    if grep -q 'recoverix-forensic-sudo-enable' <<<"$unit_cat"; then
      log "PASS: chroot systemctl cat recoverix-forensic-sudo.service"
    elif grep -q 'Description=Recoverix forensic limited sudo' "$svc" 2>/dev/null; then
      log "PASS: chroot unit file inspection (systemctl cat unavailable offline)"
    else
      log "FAIL: chroot cannot resolve recoverix-forensic-sudo.service unit"
      failures=$((failures + 1))
    fi
    if ! chroot "${ROOTFS_RESOLVED}" visudo -cf "/$(recoverix_forensic_sudoers_relpath)" >/dev/null 2>&1; then
      log "FAIL: chroot forensic sudoers syntax verify (visudo -cf)"
      failures=$((failures + 1))
    else
      log "PASS: chroot forensic sudoers syntax verify (visudo -cf)"
      if ! runtime_chroot_forensic_sudo_runtime_verify; then
        failures=$((failures + 1))
      fi
    fi
    runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
  else
    log "WARN: chroot forensic sudo self-test skipped (no mount deps)"
  fi

  [[ $failures -eq 0 ]] || return 1
  log "PASS: forensic sudo policy validated in rootfs"
  return 0
}

runtime_verify_squashfs_recoverix_forensic_sudo() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local tmpdir failures=0 extracted mode
  local -a rel_paths=(
    "$(recoverix_forensic_sudo_service_relpath)"
    "$(recoverix_forensic_sudo_helper_relpath)"
    "$(recoverix_forensic_sudoers_relpath)"
    "etc/systemd/system/sysinit.target.wants/recoverix-forensic-sudo.service"
    "etc/systemd/system/multi-user.target.wants/recoverix-forensic-sudo.service"
  )
  local rel

  if [[ "${RECOVERIX_RUNTIME_FORENSIC_SUDO:-1}" != "1" ]]; then
    return 0
  fi

  log "=== Squashfs forensic sudo verification ==="
  [[ -f "$sq" ]] || { log "FAIL: squashfs missing: ${sq}"; return 1; }

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/recoverix-forensic-sudo-sq.XXXXXX")"
  for rel in "${rel_paths[@]}"; do
    if ! unsquashfs -f -d "${tmpdir}" "$sq" "$rel" >>"${tmpdir}/extract.log" 2>&1; then
      if [[ "$rel" == *sysinit.target.wants* ]]; then
        log "WARN: squashfs missing /${rel} (multi-user wants may suffice)"
        continue
      fi
      log "FAIL: squashfs does not contain /${rel}"
      failures=$((failures + 1))
      continue
    fi
    extracted="${tmpdir}/${rel}"
    if [[ ! -e "$extracted" ]]; then
      log "FAIL: /${rel} missing after squashfs extract"
      failures=$((failures + 1))
      continue
    fi
    if [[ "$rel" == usr/local/sbin/* && ! -x "$extracted" ]]; then
      log "FAIL: /${rel} not executable in squashfs"
      failures=$((failures + 1))
      continue
    fi
    if [[ "$rel" == etc/sudoers.d/* ]]; then
      mode="$(stat -c '%a' "$extracted" 2>/dev/null || echo ?)"
      if [[ "$mode" != "440" && "$mode" != "400" ]]; then
        log "FAIL: /${rel} mode ${mode} (expected 0440)"
        failures=$((failures + 1))
        continue
      fi
    fi
    log "PASS: squashfs contains /${rel}"
  done

  if [[ ! -L "${tmpdir}/etc/systemd/system/sysinit.target.wants/recoverix-forensic-sudo.service" && \
        ! -L "${tmpdir}/etc/systemd/system/multi-user.target.wants/recoverix-forensic-sudo.service" ]]; then
    log "FAIL: squashfs missing recoverix-forensic-sudo.service wants symlink"
    failures=$((failures + 1))
  fi

  rm -rf "${tmpdir}"
  [[ $failures -eq 0 ]] || return 1
  log "PASS: forensic sudo artifacts embedded in squashfs"
  return 0
}

runtime_report_forensic_sudo_policy() {
  local report="${1:-}"
  local staging="${ROOTFS_RESOLVED}/$(recoverix_forensic_sudo_staging_relpath)"

  [[ -n "$report" ]] || return 0
  {
    echo "forensic_sudo_policy: recoverix.root=1 or recoverix.debug.xorg=1"
    echo "forensic_sudo_service_source: runtime_install_recoverix_forensic_sudo (inline unit)"
    echo "forensic_sudo_service_target: /$(recoverix_forensic_sudo_service_relpath)"
    echo "forensic_sudo_helper_source: $(recoverix_forensic_sudo_helper_source)"
    echo "forensic_sudo_helper_target: /$(recoverix_forensic_sudo_helper_relpath)"
    echo "forensic_sudoers_source: $(recoverix_forensic_sudo_assets_source)"
    echo "forensic_sudoers_target: /$(recoverix_forensic_sudoers_relpath)"
    echo "forensic_sudo_staging: ${staging}"
    echo "forensic_sudo_log: ${RECOVERIX_FORENSIC_SUDO_LOG:-/var/log/recoverix-forensic-sudo.log}"
    echo "forensic_sudo_service_enabled_sysinit: $(
      [[ -L "${ROOTFS_RESOLVED}/etc/systemd/system/sysinit.target.wants/recoverix-forensic-sudo.service" ]] && echo yes || echo no
    )"
    echo "forensic_sudo_service_enabled_multi_user: $(
      [[ -L "${ROOTFS_RESOLVED}/etc/systemd/system/multi-user.target.wants/recoverix-forensic-sudo.service" ]] && echo yes || echo no
    )"
    echo "forensic_sudo_service_safe_condition: $(
      grep -q 'ConditionKernelCommandLine=!recoverix.safe=1' "$(recoverix_forensic_sudo_service_path)" 2>/dev/null && echo present || echo MISSING
    )"
    echo "forensic_sudo_selftest_safe_condition: $(
      grep -q 'ConditionKernelCommandLine=!recoverix.safe=1' "${ROOTFS_RESOLVED}/etc/systemd/system/recoverix-forensic-sudo-selftest.service" 2>/dev/null && echo present || echo MISSING
    )"
    echo "grub_getty_debug_persistent_cmdline: $(
      awk '/menuentry "Recoverix Runtime \(getty debug persistent\)"/{f=1} f&&/linux/{c=1} c{gsub(/\\$/,"");gsub(/^[[:space:]]+/,"");printf "%s ",$0} f&&/initrd/{exit}' \
        "$(runtime_image_dir)/deploy/grub/recoverix-esp.cfg.template" 2>/dev/null | sed 's/  */ /g' || echo unavailable
    )"
    echo "sudo_allowed_commands:"
    grep -E '^Cmnd_Alias RECOVERIX_FORENSIC_|^recoverix ALL=' "$staging" 2>/dev/null | sed 's/^/  /' || true
    echo "sudoers_validation: $(visudo -cf "$(recoverix_forensic_sudoers_path)" >/dev/null 2>&1 && echo PASS || echo FAIL)"
    echo "sudoers_staging_mode_owner: $(stat -c '%a %U:%G' "$staging" 2>/dev/null || echo unknown)"
    echo "sudoers_active_mode_owner: $(stat -c '%a %U:%G' "$(recoverix_forensic_sudoers_path)" 2>/dev/null || echo unknown)"
  } >>"$report"
}

runtime_report_rootfs_account_state() {
  local report="${1:-}"
  local root="${ROOTFS_RESOLVED}"
  local mounted=0

  [[ -n "$report" ]] || return 0
  local rootfs_debug_svc="${root}/etc/systemd/system/recoverix-rootfs-debug.service"
  {
    echo "rootfs_debug_unit: /etc/systemd/system/recoverix-rootfs-debug.service ($(
      [[ -L "${root}/etc/systemd/system/multi-user.target.wants/recoverix-rootfs-debug.service" ]] && echo enabled:multi-user.target.wants || echo NOT-enabled
    ))"
    echo "rootfs_debug_execstart: $(grep -E '^ExecStart=' "$rootfs_debug_svc" 2>/dev/null | tail -n1 || echo missing)"
    echo "rootfs_debug_after: $(grep -E '^After=' "$rootfs_debug_svc" 2>/dev/null | tail -n1 || echo missing)"
    echo "rootfs_debug_log_paths: /var/log/recoverix-rootfs-debug.log /recovery-rootfs-debug.log ESP:EFI/RecoveryBoot/recoverix-rootfs-debug.log"
  } >>"$report"
  local autologin_conf="${root}/etc/systemd/system/getty@.service.d/recoverix-console-autologin.conf"
  {
    echo "runtime_account_state: begin"
    echo "runtime_console_password: set (not logged)"
    echo "runtime_console_autologin: ${RECOVERIX_RUNTIME_CONSOLE_AUTOLOGIN:-1}"
    if [[ -f "$autologin_conf" ]]; then
      echo "getty_autologin_execstart: $(grep -E '^ExecStart=-?/sbin/agetty' "$autologin_conf" 2>/dev/null | tail -n1)"
      echo "recoverix_console_autologin_conf: begin"
      sed 's/^/  /' "$autologin_conf" 2>/dev/null || true
      echo "recoverix_console_autologin_conf: end"
    else
      echo "getty_autologin_execstart: (drop-in missing: ${autologin_conf})"
    fi
    echo "xorg_forensic_console_dropin_condition_removed: $(
      _xc="${root}/etc/systemd/system/getty@.service.d/recoverix-xorg-forensic-console.conf"
      if [[ -f "$_xc" ]]; then
        grep -q 'ConditionKernelCommandLine=recoverix.debug.xorg=1' "$_xc" && echo no || echo yes
      else
        echo no-dropin
      fi
    )"
    echo "getty_autologin_has_no_execstartpre: $(
      if [[ -f "$autologin_conf" ]]; then
        { grep -q '^ExecStartPre=' "$autologin_conf" || grep -q 'recoverix-forensic-sudo-enable' "$autologin_conf"; } && echo no || echo yes
      else
        echo no-dropin
      fi
    )"
    echo "getty_autologin_mode: $(
      if [[ -f "$autologin_conf" ]] && grep -q -- '--autologin' "$autologin_conf" && ! grep -qF -- "-o '" "$autologin_conf"; then
        echo standard_ubuntu
      else
        echo non-standard
      fi
    )"
  } >>"$report"

  for f in passwd group shadow; do
    if [[ -f "${root}/etc/${f}" ]]; then
      echo "etc_${f}:"
      sed 's/^/  /' "${root}/etc/${f}" >>"$report" 2>/dev/null || true
    else
      echo "etc_${f}: missing" >>"$report"
    fi
  done

  if [[ -f "${root}/etc/passwd" ]]; then
    echo "passwd_recoverix: $(grep '^recoverix:' "${root}/etc/passwd" 2>/dev/null || echo missing)" >>"$report"
    echo "passwd_root: $(grep '^root:' "${root}/etc/passwd" 2>/dev/null || echo missing)" >>"$report"
  fi
  if [[ -f "${root}/etc/shadow" ]]; then
    echo "shadow_recoverix_present: $(grep -q '^recoverix:' "${root}/etc/shadow" && echo yes || echo no)" >>"$report"
    echo "shadow_root_locked: $(grep '^root:' "${root}/etc/shadow" | awk -F: '{print $2}' | grep -q '^[!*]' && echo yes || echo no)" >>"$report"
  fi

  if runtime_chroot_mount_deps "${ROOTFS_RESOLVED}"; then
    mounted=1
    echo "passwd_status_recoverix: $(chroot "${ROOTFS_RESOLVED}" passwd -S recoverix 2>/dev/null || echo unknown)" >>"$report"
    echo "passwd_status_root: $(chroot "${ROOTFS_RESOLVED}" passwd -S root 2>/dev/null || echo unknown)" >>"$report"
    echo "chroot_id_recoverix: $(chroot "${ROOTFS_RESOLVED}" id recoverix 2>/dev/null || echo unknown)" >>"$report"
    runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
  fi

  echo "runtime_account_state: end" >>"$report"
}

runtime_verify_rootfs_recoverix_console_access() {
  local root="${ROOTFS_RESOLVED}"
  local user failures=0
  local shell_line status_line
  user="$(recoverix_runtime_user)"

  log "=== Rootfs console access verification ==="

  shell_line="$(grep "^${user}:" "${root}/etc/passwd" 2>/dev/null || true)"
  if [[ -z "$shell_line" ]]; then
    log "FAIL: recoverix passwd entry missing"
    failures=$((failures + 1))
  else
    if grep -q "^${user}:.*:/bin/bash\$" <<<"$shell_line" || grep -q ":/bin/bash" <<<"$shell_line"; then
      log "PASS: recoverix shell is /bin/bash"
    else
      log "FAIL: recoverix shell is not /bin/bash (${shell_line})"
      failures=$((failures + 1))
    fi
  fi

  if [[ ! -f "${root}/etc/shadow" ]] || ! grep -q "^${user}:" "${root}/etc/shadow" 2>/dev/null; then
    log "FAIL: recoverix shadow entry missing"
    failures=$((failures + 1))
  else
    log "PASS: recoverix shadow entry present"
  fi

  if runtime_chroot_mount_deps "${ROOTFS_RESOLVED}"; then
    status_line="$(chroot "${ROOTFS_RESOLVED}" passwd -S "$user" 2>/dev/null || true)"
    if grep -qE '^recoverix[[:space:]]+L' <<<"$status_line"; then
      log "FAIL: recoverix account locked (passwd -S)"
      failures=$((failures + 1))
    else
      log "PASS: recoverix account not locked (${status_line:-unknown})"
    fi
    runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
  else
    log "WARN: could not chroot for passwd -S recoverix"
  fi

  if [[ "${RECOVERIX_RUNTIME_CONSOLE_AUTOLOGIN:-1}" == "1" ]]; then
    local _autologin_conf="${root}/etc/systemd/system/getty@.service.d/recoverix-console-autologin.conf"
    if [[ -f "$_autologin_conf" ]] && \
       grep -q -- '--autologin' "$_autologin_conf" && \
       grep -qF "${user}" "$_autologin_conf"; then
      log "PASS: getty tty autologin drop-in present"
    else
      log "FAIL: getty tty autologin drop-in missing"
      failures=$((failures + 1))
    fi
    if [[ -f "$_autologin_conf" ]] && grep -qF -- "--autologin ${user}" "$_autologin_conf"; then
      log "PASS: agetty standard autologin (--autologin ${user})"
    else
      log "FAIL: agetty --autologin ${user} missing in drop-in"
      failures=$((failures + 1))
    fi
    if [[ -f "$_autologin_conf" ]] && grep -qF -- "-o '-p -- \\u'" "$_autologin_conf"; then
      log "FAIL: agetty still uses -o '-p -- \\u' (-- neutralizes login -f autologin)"
      failures=$((failures + 1))
    else
      log "PASS: agetty has no -o login-options (-- force-login trap removed)"
    fi
    if [[ -f "${root}/etc/recoverix/forensic-console.issue" ]] && \
       grep -q 'Recoverix forensic console mode' "${root}/etc/recoverix/forensic-console.issue"; then
      log "PASS: forensic console banner issue file present"
    else
      log "FAIL: forensic console banner issue file missing"
      failures=$((failures + 1))
    fi

    # getty@tty1 must only require recoverix.root=1 (autologin drop-in); the
    # xorg forensic-console drop-in MUST NOT gate the whole getty unit.
    if grep -q 'ConditionKernelCommandLine=recoverix.root=1' "$_autologin_conf" 2>/dev/null; then
      log "PASS: console-autologin drop-in keeps ConditionKernelCommandLine=recoverix.root=1"
    else
      log "FAIL: console-autologin drop-in missing ConditionKernelCommandLine=recoverix.root=1"
      failures=$((failures + 1))
    fi

    # autologin must never depend on forensic sudo activation: no ExecStartPre
    # and no recoverix-forensic-sudo-enable hook in the getty drop-in.
    if [[ -f "$_autologin_conf" ]] && \
       { grep -q '^ExecStartPre=' "$_autologin_conf" || grep -q 'recoverix-forensic-sudo-enable' "$_autologin_conf"; }; then
      log "FAIL: console-autologin drop-in still couples getty to forensic sudo (ExecStartPre/recoverix-forensic-sudo-enable)"
      failures=$((failures + 1))
    else
      log "PASS: console-autologin drop-in has no ExecStartPre / forensic-sudo hook (autologin independent)"
    fi
  fi

  [[ $failures -eq 0 ]] || return 1
  log "PASS: runtime console access policy validated"
  return 0
}

runtime_install_recoverix_runtime_autologin() {
  local user conf
  user="$(recoverix_runtime_user)"
  conf="$(recoverix_runtime_gdm_custom_conf_path)"

  log "=== Configure runtime autologin policy (GDM) ==="
  mkdir -p "${ROOTFS_RESOLVED}/etc/gdm3"
  if [[ "${RECOVERIX_RUNTIME_AUTOLOGIN:-1}" == "1" ]]; then
    cat >"$conf" <<EOF
[daemon]
WaylandEnable=false
AutomaticLoginEnable=True
AutomaticLogin=${user}
TimedLoginEnable=False
EOF
    log "PASS: GDM autologin enabled for ${user}"
  else
    cat >"$conf" <<EOF
[daemon]
WaylandEnable=false
AutomaticLoginEnable=False
TimedLoginEnable=False
EOF
    log "PASS: GDM autologin disabled by config flag"
  fi
  chmod 0644 "$conf"
  return 0
}

runtime_install_recoverix_runtime_tui_launcher() {
  local src dest
  src="$(runtime_tool_deploy_source "61_recoverix_runtime_tui.sh")"
  dest="$(recoverix_runtime_tui_launcher_path)"

  log "=== Install recoverix-runtime-tui launcher ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: TUI launcher source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed recoverix-runtime-tui (mode 0755)"
  return 0
}

runtime_install_recoverix_runtime_tui_prepare() {
  local src dest
  src="$(runtime_tool_deploy_source "60_recoverix_runtime_prepare.sh")"
  dest="$(recoverix_runtime_tui_prepare_path)"

  log "=== Install recoverix-runtime-prepare helper ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: TUI prepare helper source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed recoverix-runtime-prepare (mode 0755)"
  return 0
}

runtime_install_recoverix_runtime_tui_autostart() {
  local dest user
  dest="$(recoverix_runtime_tui_autostart_profile_path)"
  user="$(recoverix_runtime_user)"

  log "=== Install Recovery Runtime TUI autostart profile ==="
  log "target: ${dest}"

  mkdir -p "${ROOTFS_RESOLVED}/etc/profile.d"
  cat >"$dest" <<EOF
#!/bin/sh
# Auto-start the Recoverix TUI from the stable console path.

case "\$-" in
  *i*) ;;
  *) return 0 2>/dev/null || exit 0 ;;
esac

[ "\$(id -un 2>/dev/null || true)" = "${user}" ] || return 0 2>/dev/null || exit 0
[ -r /proc/cmdline ] || return 0 2>/dev/null || exit 0
grep -qw 'recoverix.root=1' /proc/cmdline 2>/dev/null || return 0 2>/dev/null || exit 0
grep -qw 'recoverix.safe=1' /proc/cmdline 2>/dev/null || return 0 2>/dev/null || exit 0
grep -qw 'root=tmpfs' /proc/cmdline 2>/dev/null || return 0 2>/dev/null || exit 0
grep -qw 'systemd.unit=multi-user.target' /proc/cmdline 2>/dev/null || return 0 2>/dev/null || exit 0
grep -qw 'recoverix.gui=1' /proc/cmdline 2>/dev/null && return 0 2>/dev/null || true
grep -qw 'recoverix.debug=1' /proc/cmdline 2>/dev/null && return 0 2>/dev/null || true
grep -qw 'recoverix.debug.xorg=1' /proc/cmdline 2>/dev/null && return 0 2>/dev/null || true

tty_path="\$(tty 2>/dev/null || true)"
[ "\$tty_path" = "/dev/tty1" ] || return 0 2>/dev/null || exit 0
[ -x /usr/local/sbin/recoverix-runtime-tui ] || return 0 2>/dev/null || exit 0
[ -f /etc/systemd/system/recoverix-runtime-tui.service ] && return 0 2>/dev/null || exit 0

guard_file="/tmp/recoverix-runtime-tui-autostarted"
[ -e "\$guard_file" ] && return 0 2>/dev/null || exit 0
touch "\$guard_file" 2>/dev/null || true

exec /usr/local/sbin/recoverix-runtime-tui
EOF
  chmod 0755 "$dest"
  log "PASS: installed Recovery Runtime TUI autostart profile"
  return 0
}

runtime_install_recoverix_runtime_tty_guard_service() {
  local svc="${ROOTFS_RESOLVED}/etc/systemd/system/recoverix-runtime-tty-guard.service"

  log "=== Install Recoverix early tty input guard service ==="

  mkdir -p "$(dirname "$svc")"
  cat >"$svc" <<'EOF'
[Unit]
Description=Recoverix early console input guard
DefaultDependencies=no
After=dev-tty0.device dev-tty1.device
Before=local-fs.target getty@tty1.service recoverix-runtime-tui.service recoverix-gui-launch.service
ConditionKernelCommandLine=recoverix.root=1
ConditionKernelCommandLine=recoverix.safe=1
ConditionKernelCommandLine=root=tmpfs
ConditionKernelCommandLine=systemd.unit=multi-user.target
ConditionKernelCommandLine=!recoverix.debug=1
ConditionKernelCommandLine=!recoverix.debug.xorg=1

[Service]
Type=oneshot
StandardInput=null
StandardOutput=null
StandardError=null
ExecStart=/bin/sh -c 'for t in /dev/console /dev/tty0 /dev/tty1; do [ -e "$t" ] || continue; stty -F "$t" -echo -icanon min 0 time 0 2>/dev/null || true; done; printf "\033[H\033[2J\033[3J" >/dev/tty1 2>/dev/null || true'

[Install]
WantedBy=sysinit.target
EOF
  chmod 0644 "$svc"
  runtime_systemd_enable_unit_wants recoverix-runtime-tty-guard.service sysinit.target || return 1
  log "PASS: installed Recoverix early tty input guard service"
  return 0
}

runtime_install_recoverix_runtime_tui_service() {
  local svc
  svc="$(recoverix_runtime_tui_service_path)"

  log "=== Install Recovery Runtime TUI service ==="

  cat >"$svc" <<'EOF'
[Unit]
Description=Recoverix Runtime TUI on tty1
After=local-fs.target
Conflicts=getty@tty1.service
ConditionKernelCommandLine=recoverix.root=1
ConditionKernelCommandLine=recoverix.safe=1
ConditionKernelCommandLine=root=tmpfs
ConditionKernelCommandLine=systemd.unit=multi-user.target
ConditionKernelCommandLine=!recoverix.gui=1
ConditionKernelCommandLine=!recoverix.debug=1
ConditionKernelCommandLine=!recoverix.debug.xorg=1

[Service]
Type=simple
PermissionsStartOnly=true
User=recoverix
Group=recoverix
WorkingDirectory=/home/recoverix
Environment=HOME=/home/recoverix
Environment=USER=recoverix
Environment=LOGNAME=recoverix
Environment=TERM=linux
StandardInput=tty-force
StandardOutput=tty
StandardError=tty
TTYPath=/dev/tty1
TTYReset=no
TTYVHangup=no
TTYVTDisallocate=no
ExecStartPre=/usr/local/sbin/recoverix-runtime-prepare
ExecStart=/usr/local/sbin/recoverix-runtime-tui
Restart=no

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "$svc"
  runtime_systemd_enable_unit_wants recoverix-runtime-tui.service multi-user.target || return 1
  log "PASS: installed Recovery Runtime TUI service"
  return 0
}

runtime_install_recoverix_recovery_ui_python() {
  # Python modules are installed by runtime_install_recoverix_python_platform().
  log "=== Recovery UI Python modules (via platform packages) ==="
  runtime_verify_rootfs_recoverix_python_platform
}

runtime_install_recoverix_recovery_ui_launcher() {
  local src dest
  src="$(runtime_tool_deploy_source "62_recoverix_recovery_ui.sh")"
  dest="$(recoverix_recovery_ui_launcher_path)"

  log "=== Install recoverix-recovery-ui launcher ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: launcher source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed recoverix-recovery-ui (mode 0755)"
  return 0
}

runtime_install_recoverix_recovery_ui_autostart() {
  local src dest
  src="$(runtime_image_dir)/assets/recoverix-recovery-ui.desktop"
  dest="$(recoverix_recovery_ui_autostart_path)"

  log "=== Install Recovery UI autostart desktop entry ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: autostart desktop source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/etc/xdg/autostart"
  install -m 0644 "$src" "$dest"
  log "PASS: installed autostart entry (mode 0644)"
  return 0
}

runtime_install_recoverix_recovery_ui_openbox_autostart() {
  local src dest
  src="$(runtime_image_dir)/assets/recoverix-openbox-autostart"
  dest="$(recoverix_recovery_ui_openbox_autostart_path)"

  log "=== Install Recovery UI openbox autostart hook ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: openbox autostart source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/etc/xdg/openbox"
  install -m 0755 "$src" "$dest"
  log "PASS: installed openbox autostart hook (mode 0755)"
  return 0
}

runtime_install_recoverix_recovery_ui_launch_helper() {
  local src dest
  src="$(runtime_image_dir)/assets/recoverix-gui-launch.sh"
  dest="$(recoverix_recovery_ui_launch_helper_path)"

  log "=== Install Recovery UI launch helper ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: GUI launch helper source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed GUI launch helper (mode 0755)"
  return 0
}

runtime_install_recoverix_recovery_ui_launch_service() {
  local svc xwrapper
  svc="$(recoverix_recovery_ui_launch_service_path)"
  xwrapper="${ROOTFS_RESOLVED}/etc/X11/Xwrapper.config"

  log "=== Install Recovery UI launch service ==="

  mkdir -p "${ROOTFS_RESOLVED}/etc/X11"
  cat >"${xwrapper}" <<'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF
  chmod 0644 "${xwrapper}"

  cat >"$svc" <<'EOF'
[Unit]
Description=Recoverix GUI launch on tty1
After=local-fs.target recoverix-runtime-tty-guard.service
Conflicts=getty@tty1.service
ConditionKernelCommandLine=recoverix.gui=1
ConditionKernelCommandLine=recoverix.root=1

[Service]
Type=simple
PermissionsStartOnly=true
User=recoverix
Group=recoverix
WorkingDirectory=/home/recoverix
Environment=HOME=/home/recoverix
Environment=USER=recoverix
Environment=LOGNAME=recoverix
Environment=XDG_RUNTIME_DIR=/run/user/2000
StandardInput=tty-force
StandardOutput=journal
StandardError=journal
TTYPath=/dev/tty1
TTYReset=no
TTYVHangup=no
TTYVTDisallocate=no
ExecStartPre=/usr/local/sbin/recoverix-runtime-prepare
ExecStart=/bin/sh -lc 'exec /usr/local/sbin/recoverix-gui-launch >>/tmp/recoverix-gui-service.log 2>&1'
Restart=on-failure
RestartSec=1

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "$svc"
  runtime_systemd_enable_unit_wants recoverix-gui-launch.service multi-user.target || return 1
  log "PASS: installed GUI launch service"
  return 0
}

runtime_install_recoverix_recovery_ui_openbox_session() {
  local src dest
  src="$(runtime_image_dir)/assets/recoverix-openbox-session.sh"
  dest="$(recoverix_recovery_ui_openbox_session_path)"

  log "=== Install Recovery UI openbox session wrapper ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: openbox session source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed openbox session wrapper (mode 0755)"
  return 0
}

runtime_verify_rootfs_recoverix_runtime_tui() {
  local launcher prepare profile service guard_service guard_wants failures=0
  launcher="$(recoverix_runtime_tui_launcher_path)"
  prepare="$(recoverix_runtime_tui_prepare_path)"
  profile="$(recoverix_runtime_tui_autostart_profile_path)"
  service="$(recoverix_runtime_tui_service_path)"
  guard_service="${ROOTFS_RESOLVED}/etc/systemd/system/recoverix-runtime-tty-guard.service"
  guard_wants="${ROOTFS_RESOLVED}/etc/systemd/system/sysinit.target.wants/recoverix-runtime-tty-guard.service"

  log "=== Rootfs Recovery Runtime TUI verification ==="

  if [[ -f "$launcher" && -x "$launcher" ]]; then
    log "PASS: recoverix-runtime-tui present and executable"
  else
    log "FAIL: recoverix-runtime-tui missing or not executable"
    failures=$((failures + 1))
  fi

  if [[ -f "$prepare" && -x "$prepare" ]]; then
    log "PASS: recoverix-runtime-prepare present and executable"
  else
    log "FAIL: recoverix-runtime-prepare missing or not executable"
    failures=$((failures + 1))
  fi

  if [[ -f "$profile" ]]; then
    log "PASS: TUI autostart profile present"
    if grep -q '/usr/local/sbin/recoverix-runtime-tui' "$profile" && \
       grep -q 'recoverix.gui=1' "$profile" && \
       grep -q 'recoverix.debug=1' "$profile" && \
       grep -q 'recoverix.debug.xorg=1' "$profile"; then
      log "PASS: TUI autostart profile guards GUI/debug paths"
    else
      log "FAIL: TUI autostart profile missing runtime/debug guards"
      failures=$((failures + 1))
    fi
  else
    log "FAIL: TUI autostart profile missing"
    failures=$((failures + 1))
  fi

  if [[ -f "$service" ]]; then
    log "PASS: TUI service present"
    if grep -q '^Conflicts=getty@tty1.service$' "$service" && \
       grep -q '^PermissionsStartOnly=true$' "$service" && \
       grep -q '^ExecStartPre=/usr/local/sbin/recoverix-runtime-prepare$' "$service" && \
       grep -q '^TTYReset=no$' "$service" && \
       grep -q '^TTYVHangup=no$' "$service" && \
       grep -q '/usr/local/sbin/recoverix-runtime-tui' "$service" && \
       grep -q 'ConditionKernelCommandLine=!recoverix.gui=1' "$service"; then
      log "PASS: TUI service tty1/guard configuration valid"
    else
      log "FAIL: TUI service missing tty1 or cmdline guards"
      failures=$((failures + 1))
    fi
  else
    log "FAIL: TUI service missing"
    failures=$((failures + 1))
  fi

  if [[ -f "$guard_service" && -e "$guard_wants" ]]; then
    if grep -q '^Before=local-fs.target getty@tty1.service recoverix-runtime-tui.service recoverix-gui-launch.service$' "$guard_service" && \
       grep -q '/dev/console /dev/tty0 /dev/tty1' "$guard_service" && \
       grep -q 'stty -F "$t" -echo -icanon min 0 time 0' "$guard_service" && \
       ! grep -q 'ConditionKernelCommandLine=!recoverix.gui=1' "$guard_service"; then
      log "PASS: early tty input guard service present and enabled"
    else
      log "FAIL: early tty input guard service missing expected tty policy"
      failures=$((failures + 1))
    fi
  else
    log "FAIL: early tty input guard service missing or not enabled"
    failures=$((failures + 1))
  fi

  runtime_verify_rootfs_recoverix_boot_diagnostics || failures=$((failures + 1))

  [[ $failures -eq 0 ]] || return 1
  log "PASS: Recovery Runtime TUI launcher/autostart validated"
  return 0
}

runtime_verify_rootfs_recoverix_recovery_ui() {
  local launcher autostart openbox_autostart openbox_session launch_helper launch_service lib_main failures=0

  launcher="$(recoverix_recovery_ui_launcher_path)"
  autostart="$(recoverix_recovery_ui_autostart_path)"
  openbox_autostart="$(recoverix_recovery_ui_openbox_autostart_path)"
  openbox_session="$(recoverix_recovery_ui_openbox_session_path)"
  launch_helper="$(recoverix_recovery_ui_launch_helper_path)"
  launch_service="$(recoverix_recovery_ui_launch_service_path)"
  lib_main="$(recoverix_recovery_ui_lib_root)/recovery_runtime/gtk_ui/main.py"

  log "=== Rootfs Recovery UI verification ==="

  for path in "$launcher" "$autostart" "$openbox_autostart" "$openbox_session" "$launch_helper" "$launch_service" "$lib_main"; do
    if [[ ! -f "$path" ]]; then
      log "FAIL: missing ${path}"
      failures=$((failures + 1))
    fi
  done

  if [[ -f "$launcher" && ! -x "$launcher" ]]; then
    log "FAIL: ${launcher} is not executable"
    failures=$((failures + 1))
  fi
  if [[ -f "$openbox_session" && ! -x "$openbox_session" ]]; then
    log "FAIL: ${openbox_session} is not executable"
    failures=$((failures + 1))
  fi
  if [[ -f "$openbox_session" ]]; then
    if grep -q 'RECOVERIX_UI_SESSION_MANAGED=1' "$openbox_session" && \
       grep -q '/usr/local/sbin/recoverix-recovery-ui' "$openbox_session"; then
      :
    else
      log "FAIL: ${openbox_session} missing foreground UI session policy"
      failures=$((failures + 1))
    fi
  fi
  if [[ -f "$launch_helper" && ! -x "$launch_helper" ]]; then
    log "FAIL: ${launch_helper} is not executable"
    failures=$((failures + 1))
  fi
  if [[ -f "$launch_helper" ]]; then
    if grep -q 'wait_for_graphics_ready' "$launch_helper" && \
       grep -q '/sys/class/drm/card' "$launch_helper" && \
       grep -q '/dev/dri/' "$launch_helper" && \
       grep -q 'udevadm settle' "$launch_helper" && \
       grep -q 'xinit exited rc=' "$launch_helper"; then
      :
    else
      log "FAIL: ${launch_helper} missing DRM readiness wait or xinit failure reporting"
      failures=$((failures + 1))
    fi
  fi
  if [[ -f "$launch_service" ]] && ! grep -q 'ConditionKernelCommandLine=recoverix.gui=1' "$launch_service"; then
    log "FAIL: ${launch_service} missing recoverix.gui=1 condition"
    failures=$((failures + 1))
  fi
  if [[ -f "$launch_service" ]]; then
    if grep -q '^PermissionsStartOnly=true$' "$launch_service" && \
       grep -q '^ExecStartPre=/usr/local/sbin/recoverix-runtime-prepare$' "$launch_service" && \
       grep -q '^Conflicts=getty@tty1.service$' "$launch_service" && \
       grep -q '^TTYReset=no$' "$launch_service" && \
       grep -q '^TTYVHangup=no$' "$launch_service" && \
       grep -q '^Restart=on-failure$' "$launch_service" && \
       ! grep -q 'systemd-user-sessions\.service' "$launch_service" && \
       ! grep -q 'network\.target' "$launch_service" && \
       ! grep -q '^Wants=getty@tty1.service$' "$launch_service"; then
      :
    else
      log "FAIL: ${launch_service} missing GUI tty prepare policy"
      failures=$((failures + 1))
    fi
  fi
  if [[ -f "${ROOTFS_RESOLVED}/etc/X11/Xwrapper.config" ]] && \
     grep -q '^needs_root_rights=yes$' "${ROOTFS_RESOLVED}/etc/X11/Xwrapper.config"; then
    :
  else
    log "FAIL: /etc/X11/Xwrapper.config missing needs_root_rights=yes"
    failures=$((failures + 1))
  fi

  runtime_verify_rootfs_recoverix_boot_diagnostics || failures=$((failures + 1))
  runtime_verify_rootfs_recoverix_forensic_sudo || failures=$((failures + 1))
  runtime_verify_rootfs_recoverix_runtime_tui || failures=$((failures + 1))

  if [[ $failures -eq 0 ]]; then
    log "PASS: Recovery UI launcher, XDG/openbox autostart, GUI launch service, and python entry present"
    return 0
  fi
  return 1
}

runtime_verify_squashfs_recoverix_recovery_ui() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local tmpdir extracted failures=0
  local -a rel_paths=(
    usr/local/sbin/recoverix-runtime-prepare
    usr/local/sbin/recoverix-runtime-tui
    etc/profile.d/recoverix-runtime-tui-autostart.sh
    etc/systemd/system/recoverix-runtime-tty-guard.service
    etc/systemd/system/recoverix-runtime-tui.service
    usr/local/sbin/recoverix-recovery-ui
    usr/local/sbin/recoverix-openbox-session
    usr/local/sbin/recoverix-gui-launch
    usr/local/sbin/recoverix-boot-diagnostics
    etc/xdg/autostart/recoverix-recovery-ui.desktop
    etc/xdg/openbox/autostart
    etc/systemd/system/recoverix-gui-launch.service
    etc/systemd/system/recoverix-boot-diagnostics.service
    etc/X11/Xwrapper.config
    usr/local/lib/recoverix/recovery_runtime/gtk_ui/main.py
  )
  local rel

  log "=== Squashfs Recovery UI verification ==="

  if [[ ! -f "$sq" ]]; then
    log "FAIL: squashfs missing: ${sq}"
    return 1
  fi

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/recoverix-ui-verify.XXXXXX")"
  for rel in "${rel_paths[@]}"; do
    if ! unsquashfs -f -d "${tmpdir}" "$sq" "$rel" >>"${tmpdir}/extract.log" 2>&1; then
      log "FAIL: squashfs does not contain /${rel}"
      failures=$((failures + 1))
      continue
    fi
    extracted="${tmpdir}/${rel}"
    if [[ ! -f "$extracted" ]]; then
      log "FAIL: /${rel} missing after extract"
      failures=$((failures + 1))
      continue
    fi
    if [[ "$rel" == usr/local/sbin/* && ! -x "$extracted" ]]; then
      log "FAIL: /${rel} is not executable in squashfs"
      failures=$((failures + 1))
      continue
    fi
    log "PASS: squashfs contains /${rel}"
  done

  runtime_verify_squashfs_recoverix_forensic_sudo || failures=$((failures + 1))

  rm -rf "${tmpdir}"
  [[ $failures -eq 0 ]] && return 0
  return 1
}

runtime_verify_squashfs_recoverix_runtime_tui_only() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local tmpdir extracted failures=0
  local -a rel_paths=(
    usr/local/sbin/recoverix-runtime-prepare
    usr/local/sbin/recoverix-runtime-tui
    etc/profile.d/recoverix-runtime-tui-autostart.sh
    etc/systemd/system/recoverix-runtime-tty-guard.service
    etc/systemd/system/recoverix-runtime-tui.service
    usr/local/sbin/recoverix-boot-diagnostics
    etc/systemd/system/recoverix-boot-diagnostics.service
    usr/local/lib/recoverix/recovery_runtime/main.py
  )
  local rel

  log "=== Squashfs Recovery Runtime TUI-only verification ==="

  if [[ ! -f "$sq" ]]; then
    log "FAIL: squashfs missing: ${sq}"
    return 1
  fi

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/recoverix-tui-verify.XXXXXX")"
  for rel in "${rel_paths[@]}"; do
    if ! unsquashfs -f -d "${tmpdir}" "$sq" "$rel" >>"${tmpdir}/extract.log" 2>&1; then
      log "FAIL: squashfs does not contain /${rel}"
      failures=$((failures + 1))
      continue
    fi
    extracted="${tmpdir}/${rel}"
    if [[ ! -f "$extracted" ]]; then
      log "FAIL: /${rel} missing after extract"
      failures=$((failures + 1))
      continue
    fi
    if [[ "$rel" == usr/local/sbin/* && ! -x "$extracted" ]]; then
      log "FAIL: /${rel} is not executable in squashfs"
      failures=$((failures + 1))
      continue
    fi
    log "PASS: squashfs contains /${rel}"
  done

  rm -rf "${tmpdir}"
  [[ $failures -eq 0 ]] && return 0
  return 1
}

runtime_install_recoverix_recovery_ui() {
  local rc=0
  runtime_install_recoverix_python_platform || rc=1
  runtime_install_recoverix_recovery_ui_python || rc=1
  runtime_install_recoverix_runtime_user_session || rc=1
  runtime_install_recoverix_console_access || rc=1
  runtime_install_recoverix_rootfs_debug || rc=1
  runtime_install_recoverix_boot_diagnostics || rc=1
  runtime_install_recoverix_forensic_sudo || rc=1
  runtime_install_recoverix_runtime_autologin || rc=1
  runtime_install_recoverix_runtime_tui_prepare || rc=1
  runtime_install_recoverix_runtime_tui_launcher || rc=1
  runtime_install_recoverix_runtime_tui_autostart || rc=1
  runtime_install_recoverix_runtime_tty_guard_service || rc=1
  runtime_install_recoverix_runtime_tui_service || rc=1
  runtime_install_recoverix_recovery_ui_launcher || rc=1
  runtime_install_recoverix_recovery_ui_autostart || rc=1
  runtime_install_recoverix_recovery_ui_openbox_session || rc=1
  runtime_install_recoverix_recovery_ui_openbox_autostart || rc=1
  runtime_install_recoverix_recovery_ui_launch_helper || rc=1
  runtime_install_recoverix_recovery_ui_launch_service || rc=1
  return "$rc"
}

runtime_install_recoverix_runtime_tui_only() {
  local rc=0
  runtime_install_recoverix_python_platform || rc=1
  runtime_install_recoverix_runtime_user_session || rc=1
  runtime_install_recoverix_console_access || rc=1
  runtime_install_recoverix_rootfs_debug || rc=1
  runtime_install_recoverix_boot_diagnostics || rc=1
  runtime_install_recoverix_forensic_sudo || rc=1
  runtime_install_recoverix_runtime_tui_prepare || rc=1
  runtime_install_recoverix_runtime_tui_launcher || rc=1
  runtime_install_recoverix_runtime_tui_autostart || rc=1
  runtime_install_recoverix_runtime_tty_guard_service || rc=1
  runtime_install_recoverix_runtime_tui_service || rc=1
  return "$rc"
}

runtime_verify_rootfs_recoverix_runtime_user_session() {
  local user group uid gid home autologin conf failures=0
  local root="${ROOTFS_RESOLVED}"
  user="$(recoverix_runtime_user)"
  group="$(recoverix_runtime_group)"
  uid="$(recoverix_runtime_uid)"
  gid="$(recoverix_runtime_gid)"
  home="$(recoverix_runtime_home)"
  autologin="${RECOVERIX_RUNTIME_AUTOLOGIN:-1}"
  conf="$(recoverix_runtime_gdm_custom_conf_path)"

  log "=== Rootfs runtime user/session verification ==="
  if [[ ! -f "${root}/etc/passwd" ]] || ! grep -q "^${user}:x:${uid}:${gid}:" "${root}/etc/passwd"; then
    log "FAIL: runtime user missing or UID/GID mismatch (${user}:${uid}:${gid})"
    failures=$((failures + 1))
  else
    log "PASS: runtime user exists with deterministic UID/GID (${user}:${uid}:${gid})"
  fi
  if [[ ! -f "${root}/etc/group" ]] || ! grep -q "^${group}:x:${gid}:" "${root}/etc/group"; then
    log "FAIL: runtime group missing or GID mismatch (${group}:${gid})"
    failures=$((failures + 1))
  fi
  if [[ ! -d "${root}${home}" ]]; then
    log "FAIL: runtime home missing (${home})"
    failures=$((failures + 1))
  else
    log "PASS: runtime home exists (${home})"
    local owner
    owner="$(stat -c '%u:%g' "${root}${home}" 2>/dev/null || echo unknown)"
    if [[ "$owner" == "${uid}:${gid}" ]]; then
      log "PASS: runtime home ownership ${owner}"
    else
      log "FAIL: runtime home ownership ${owner} (expected ${uid}:${gid})"
      failures=$((failures + 1))
    fi
  fi
  local home_mode root_home_mode
  root_home_mode="$(stat -c '%a' "${root}/home" 2>/dev/null || echo unknown)"
  if [[ "$root_home_mode" == "755" ]]; then
    log "PASS: /home mode 755"
  else
    log "FAIL: /home mode ${root_home_mode} (expected 755)"
    failures=$((failures + 1))
  fi
  home_mode="$(stat -c '%a' "${root}${home}" 2>/dev/null || echo unknown)"
  if [[ "$home_mode" == "755" ]]; then
    log "PASS: ${home} mode 755"
  else
    log "FAIL: ${home} mode ${home_mode} (expected 755)"
    failures=$((failures + 1))
  fi
  # AccountsService Session=openbox: validated only by runtime_verify_accountsservice_user_file
  # during GUI stack verification (runtime_run_accountsservice_validation).
  if [[ ! -f "$conf" ]]; then
    log "FAIL: missing GDM autologin config (${conf})"
    failures=$((failures + 1))
  elif [[ "$autologin" == "1" ]]; then
    if grep -q '^AutomaticLoginEnable=True' "$conf" && grep -q "^AutomaticLogin=${user}" "$conf"; then
      log "PASS: autologin config valid for ${user}"
    else
      log "FAIL: autologin config invalid for ${user}"
      failures=$((failures + 1))
    fi
  else
    if grep -q '^AutomaticLoginEnable=False' "$conf"; then
      log "PASS: autologin disabled by policy"
    else
      log "FAIL: autologin disable config invalid"
      failures=$((failures + 1))
    fi
  fi
  if [[ ! -x "${root}/usr/local/sbin/recoverix-recovery-ui" ]]; then
    log "FAIL: recovery UI launcher missing for session startup"
    failures=$((failures + 1))
  fi
  runtime_verify_rootfs_recoverix_console_access || failures=$((failures + 1))
  runtime_verify_rootfs_recoverix_rootfs_debug || failures=$((failures + 1))
  runtime_verify_rootfs_recoverix_forensic_sudo || failures=$((failures + 1))
  [[ $failures -eq 0 ]] || return 1
  log "PASS: runtime user/session policy validated"
  return 0
}

# ---- recoverix-backup-admin CLI -----------------------------------------

recoverix_backup_admin_path() {
  printf '%s/usr/local/sbin/recoverix-backup-admin' "${ROOTFS_RESOLVED}"
}

runtime_install_recoverix_backup_admin() {
  local src dest
  src="$(runtime_tool_deploy_source "69_recoverix_backup_admin.sh")"
  dest="$(recoverix_backup_admin_path)"

  log "=== Install recoverix-backup-admin CLI ==="
  if [[ ! -f "$src" ]]; then
    log "FAIL: backup-admin source missing: ${src}"
    return 1
  fi
  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed recoverix-backup-admin"
  return 0
}

runtime_verify_rootfs_recoverix_backup_admin() {
  local cli
  cli="$(recoverix_backup_admin_path)"

  log "=== Rootfs recoverix-backup-admin verification ==="
  if [[ ! -f "$cli" ]]; then
    log "FAIL: missing ${cli}"
    return 1
  fi
  if [[ ! -x "$cli" ]]; then
    log "FAIL: not executable ${cli}"
    return 1
  fi
  if ! grep -q 'recovery_runtime.backup_admin' "$cli"; then
    log "FAIL: ${cli} missing recovery_runtime.backup_admin entrypoint"
    return 1
  fi
  log_pass "rootfs contains recoverix-backup-admin"
  return 0
}

runtime_assert_rootfs_recoverix_backup_admin() {
  log "=== Assert rootfs: recoverix-backup-admin ==="
  if runtime_verify_rootfs_recoverix_backup_admin; then
    return 0
  fi
  log_fail "rootfs missing recoverix-backup-admin"
  return 1
}

runtime_verify_squashfs_recoverix_backup_admin() {
  if runtime_verify_squashfs_tool "recoverix-backup-admin"; then
    log_pass "squashfs contains recoverix-backup-admin"
    return 0
  fi
  log_fail "squashfs missing recoverix-backup-admin"
  return 1
}

runtime_assert_squashfs_recoverix_backup_admin() {
  runtime_verify_squashfs_recoverix_backup_admin
}

# ---- recoverix-backup-finalize-check CLI --------------------------------

recoverix_backup_finalize_check_path() {
  printf '%s/usr/local/sbin/recoverix-backup-finalize-check' "${ROOTFS_RESOLVED}"
}

runtime_install_recoverix_backup_finalize_check() {
  local src dest
  src="$(runtime_tool_deploy_source "68_recoverix_backup_finalize_check.sh")"
  dest="$(recoverix_backup_finalize_check_path)"

  log "=== Install recoverix-backup-finalize-check CLI ==="
  if [[ ! -f "$src" ]]; then
    log "FAIL: backup-finalize-check source missing: ${src}"
    return 1
  fi
  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed recoverix-backup-finalize-check"
  return 0
}

runtime_verify_rootfs_recoverix_backup_finalize_check() {
  local cli mode
  cli="$(recoverix_backup_finalize_check_path)"
  if [[ ! -f "$cli" ]]; then
    log "FAIL: missing ${cli}"
    return 1
  fi
  if [[ ! -x "$cli" ]]; then
    log "FAIL: not executable ${cli}"
    return 1
  fi
  mode="$(stat -c '%a' "$cli" 2>/dev/null || echo "?")"
  if [[ "$mode" != "755" ]]; then
    log "WARN: ${cli} mode=${mode} (expected 0755)"
  fi
  if ! grep -q 'PYTHONPATH="/usr/local/lib/recoverix' "$cli"; then
    log "FAIL: ${cli} missing PYTHONPATH=/usr/local/lib/recoverix"
    return 1
  fi
  if ! grep -q 'backup_engine.backup_finalize' "$cli"; then
    log "FAIL: ${cli} missing backup_engine.backup_finalize entrypoint"
    return 1
  fi
  log_pass "rootfs contains recoverix-backup-finalize-check"
  return 0
}

runtime_assert_rootfs_recoverix_backup_finalize_check() {
  log "=== Assert rootfs: recoverix-backup-finalize-check ==="
  if runtime_verify_rootfs_recoverix_backup_finalize_check; then
    return 0
  fi
  log_fail "rootfs missing recoverix-backup-finalize-check"
  return 1
}

runtime_verify_squashfs_recoverix_backup_finalize_check() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local rel="usr/local/sbin/recoverix-backup-finalize-check"
  local list_file

  log "=== Squashfs recoverix-backup-finalize-check verification ==="

  if [[ ! -f "$sq" ]]; then
    log_fail "squashfs missing: ${sq}"
    return 1
  fi

  if [[ "${RUNTIME_BOOT_SQ_LISTING_VERIFIED:-0}" == "1" && -n "${RUNTIME_BOOT_SQ_INDEX:-}" && -f "${RUNTIME_BOOT_SQ_INDEX}" ]]; then
    if grep -qF 'recoverix-backup-finalize-check' "${RUNTIME_BOOT_SQ_INDEX}" 2>/dev/null; then
      log "INFO: squashfs index lists recoverix-backup-finalize-check (reuse boot validation index)"
    else
      log_fail "recoverix-backup-finalize-check not in squashfs index"
      return 1
    fi
  else
    local raw index
    raw="$(mktemp "${TMPDIR:-/tmp}/recoverix-sq-raw.XXXXXX")"
    index="$(mktemp "${TMPDIR:-/tmp}/recoverix-sq-idx.XXXXXX")"
    if ! runtime_verify_unsquashfs_listing "$sq" "$raw" "$index"; then
      rm -f "$raw" "$index"
      return 1
    fi
    if ! grep -qF 'recoverix-backup-finalize-check' "$index" 2>/dev/null; then
      log_fail "recoverix-backup-finalize-check not listed in ${sq}"
      rm -f "$raw" "$index"
      return 1
    fi
    rm -f "$raw" "$index"
  fi

  if runtime_verify_squashfs_tool "recoverix-backup-finalize-check"; then
    log_pass "squashfs contains recoverix-backup-finalize-check"
    return 0
  fi

  log_fail "squashfs missing recoverix-backup-finalize-check"
  return 1
}

runtime_assert_squashfs_recoverix_backup_finalize_check() {
  runtime_verify_squashfs_recoverix_backup_finalize_check
}

runtime_log_rootfs_recoverix_sbin_inventory() {
  local sbin="${ROOTFS_RESOLVED}/usr/local/sbin"
  log "=== rootfs inventory: ${sbin}/recoverix-* ==="
  if [[ ! -d "$sbin" ]]; then
    log_fail "missing directory ${sbin}"
    return 1
  fi
  local entry
  local found=0
  shopt -s nullglob
  for entry in "${sbin}"/recoverix-*; do
    found=1
    log "INFO: $(ls -la "$entry")"
  done
  shopt -u nullglob
  if [[ $found -eq 0 ]]; then
    log_fail "no recoverix-* CLIs under ${sbin}"
    return 1
  fi
  return 0
}

runtime_run_backup_finalize_check_rootfs_self_test() {
  local cli test_root out rc mounted=0

  cli="$(recoverix_backup_finalize_check_path)"
  log "=== Rootfs chroot self-test: recoverix-backup-finalize-check (non-fatal) ==="

  if [[ ! -x "$cli" ]]; then
    log_warn "chroot self-test skipped: CLI not installed in rootfs"
    return 0
  fi

  if [[ ! -x "${ROOTFS_RESOLVED}/usr/bin/python3" ]]; then
    log_warn "chroot self-test skipped: rootfs python3 missing"
    return 0
  fi

  test_root="${ROOTFS_RESOLVED}/tmp/recoverix-fc-selftest"
  mkdir -p "$test_root"

  if runtime_chroot_mount_deps "${ROOTFS_RESOLVED}"; then
    mounted=1
  else
    log_warn "chroot self-test skipped: could not bind-mount dev/proc/sys into rootfs"
    return 0
  fi

  set +e
  out="$(chroot "${ROOTFS_RESOLVED}" env \
    PYTHONPATH="/usr/local/lib/recoverix" \
    RECOVERIX_IMAGE_ROOT="/tmp/recoverix-fc-selftest" \
    /usr/local/sbin/recoverix-backup-finalize-check 2>&1)"
  rc=$?
  set -e

  if [[ $mounted -eq 1 ]]; then
    runtime_chroot_umount_deps "${ROOTFS_RESOLVED}"
  fi

  if [[ $rc -ne 1 ]]; then
    log_warn "chroot self-test: expected exit 1 for empty image root, got rc=${rc}"
    log_warn "chroot output: ${out}"
    return 0
  fi

  if ! grep -q '"finalize_ok"' <<<"$out"; then
    log_warn "chroot self-test: CLI output is not finalize JSON"
    log_warn "chroot output: ${out}"
    return 0
  fi

  log_pass "chroot self-test: recoverix-backup-finalize-check emitted JSON"
  return 0
}

# ---- recoverix-image-status CLI -----------------------------------------

recoverix_image_status_path() {
  printf '%s/usr/local/sbin/recoverix-image-status' "${ROOTFS_RESOLVED}"
}

runtime_install_recoverix_image_status() {
  local src dest
  src="$(runtime_tool_deploy_source "63_recoverix_image_status.sh")"
  dest="$(recoverix_image_status_path)"

  log "=== Install recoverix-image-status CLI ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: image-status source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed recoverix-image-status (mode 0755)"
  return 0
}

runtime_verify_rootfs_recoverix_image_status() {
  local cli failures=0
  cli="$(recoverix_image_status_path)"

  log "=== Rootfs Recovery image-status verification ==="

  if [[ ! -f "$cli" ]]; then
    log "FAIL: missing ${cli}"
    failures=$((failures + 1))
  elif [[ ! -x "$cli" ]]; then
    log "FAIL: ${cli} is not executable"
    failures=$((failures + 1))
  fi

  if [[ $failures -eq 0 ]]; then
    log "PASS: recoverix-image-status present and executable"
    return 0
  fi
  return 1
}

# ---- recoverix-restore-plan CLI -----------------------------------------

recoverix_restore_plan_path() {
  printf '%s/usr/local/sbin/recoverix-restore-plan' "${ROOTFS_RESOLVED}"
}

runtime_install_recoverix_restore_plan() {
  local src dest
  src="$(runtime_tool_deploy_source "65_recoverix_restore_plan.sh")"
  dest="$(recoverix_restore_plan_path)"

  log "=== Install recoverix-restore-plan CLI ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: restore-plan source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed recoverix-restore-plan (mode 0755)"
  return 0
}

runtime_verify_rootfs_recoverix_restore_plan() {
  local cli failures=0
  cli="$(recoverix_restore_plan_path)"

  log "=== Rootfs restore-plan verification ==="

  if [[ ! -f "$cli" ]]; then
    log "FAIL: missing ${cli}"
    failures=$((failures + 1))
  elif [[ ! -x "$cli" ]]; then
    log "FAIL: ${cli} is not executable"
    failures=$((failures + 1))
  fi

  if [[ $failures -eq 0 ]]; then
    log "PASS: recoverix-restore-plan present and executable"
    return 0
  fi
  return 1
}

runtime_verify_squashfs_recoverix_restore_plan() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local tmpdir extracted failures=0
  local rel="usr/local/sbin/recoverix-restore-plan"

  log "=== Squashfs restore-plan verification ==="

  if [[ ! -f "$sq" ]]; then
    log "FAIL: squashfs missing: ${sq}"
    return 1
  fi

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/recoverix-restore-plan-verify.XXXXXX")"
  if ! unsquashfs -f -d "${tmpdir}" "$sq" "$rel" >>"${tmpdir}/extract.log" 2>&1; then
    log "FAIL: squashfs does not contain /${rel}"
    failures=$((failures + 1))
  else
    extracted="${tmpdir}/${rel}"
    if [[ -f "$extracted" && -x "$extracted" ]]; then
      log "PASS: squashfs contains /${rel}"
    else
      log "FAIL: /${rel} missing or not executable in squashfs"
      failures=$((failures + 1))
    fi
  fi

  rm -rf "${tmpdir}"
  [[ $failures -eq 0 ]] && return 0
  return 1
}

# ---- recoverix-restore-preflight CLI ------------------------------------

recoverix_restore_preflight_path() {
  printf '%s/usr/local/sbin/recoverix-restore-preflight' "${ROOTFS_RESOLVED}"
}

runtime_install_recoverix_restore_preflight() {
  local src dest
  src="$(runtime_tool_deploy_source "64_recoverix_restore_preflight.sh")"
  dest="$(recoverix_restore_preflight_path)"

  log "=== Install recoverix-restore-preflight CLI ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: restore-preflight source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed recoverix-restore-preflight (mode 0755)"
  return 0
}

runtime_verify_rootfs_recoverix_restore_preflight() {
  local cli failures=0
  cli="$(recoverix_restore_preflight_path)"

  log "=== Rootfs restore-preflight verification ==="

  if [[ ! -f "$cli" ]]; then
    log "FAIL: missing ${cli}"
    failures=$((failures + 1))
  elif [[ ! -x "$cli" ]]; then
    log "FAIL: ${cli} is not executable"
    failures=$((failures + 1))
  fi

  if [[ $failures -eq 0 ]]; then
    log "PASS: recoverix-restore-preflight present and executable"
    return 0
  fi
  return 1
}

runtime_verify_squashfs_recoverix_restore_preflight() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local tmpdir extracted failures=0
  local rel="usr/local/sbin/recoverix-restore-preflight"

  log "=== Squashfs restore-preflight verification ==="

  if [[ ! -f "$sq" ]]; then
    log "FAIL: squashfs missing: ${sq}"
    return 1
  fi

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/recoverix-restore-preflight-verify.XXXXXX")"
  if ! unsquashfs -f -d "${tmpdir}" "$sq" "$rel" >>"${tmpdir}/extract.log" 2>&1; then
    log "FAIL: squashfs does not contain /${rel}"
    failures=$((failures + 1))
  else
    extracted="${tmpdir}/${rel}"
    if [[ -f "$extracted" && -x "$extracted" ]]; then
      log "PASS: squashfs contains /${rel}"
    else
      log "FAIL: /${rel} missing or not executable in squashfs"
      failures=$((failures + 1))
    fi
  fi

  rm -rf "${tmpdir}"
  [[ $failures -eq 0 ]] && return 0
  return 1
}

runtime_verify_squashfs_recoverix_image_status() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local tmpdir rel extracted failures=0
  local -a rel_paths=(
    usr/local/sbin/recoverix-image-status
  )

  log "=== Squashfs Recovery image-status verification ==="

  if [[ ! -f "$sq" ]]; then
    log "FAIL: squashfs missing: ${sq}"
    return 1
  fi

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/recoverix-image-status-verify.XXXXXX")"
  for rel in "${rel_paths[@]}"; do
    if ! unsquashfs -f -d "${tmpdir}" "$sq" "$rel" >>"${tmpdir}/extract.log" 2>&1; then
      log "FAIL: squashfs does not contain /${rel}"
      failures=$((failures + 1))
      continue
    fi
    extracted="${tmpdir}/${rel}"
    if [[ ! -f "$extracted" ]]; then
      log "FAIL: /${rel} missing after extract"
      failures=$((failures + 1))
      continue
    fi
    if [[ ! -x "$extracted" ]]; then
      log "FAIL: /${rel} is not executable in squashfs"
      failures=$((failures + 1))
      continue
    fi
    log "PASS: squashfs contains /${rel}"
  done

  rm -rf "${tmpdir}"
  [[ $failures -eq 0 ]] && return 0
  return 1
}
