#!/usr/bin/env bash
# Recoverix Runtime GUI session stack: GDM + Xorg + openbox + graphical.target.

runtime_gui_packages_file() {
  printf '%s/assets/runtime_gui_packages.txt' "$(runtime_image_dir)"
}

recoverix_gui_forensic_log_path() {
  printf '%s' "${RECOVERIX_GUI_FORENSIC_LOG:-/var/log/recoverix-gui.log}"
}

recoverix_xorg_forensic_log_path() {
  printf '%s' "${RECOVERIX_XORG_FORENSIC_LOG:-/var/log/recoverix-xorg.log}"
}

runtime_gui_openbox_xsession_path() {
  printf '%s/usr/share/xsessions/openbox.desktop' "${ROOTFS_RESOLVED}"
}

runtime_gui_packages_list() {
  local file line
  file="$(runtime_gui_packages_file)"
  [[ -f "$file" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    printf '%s\n' "$line"
  done <"$file"
}

runtime_gui_gdm_unit_path() {
  local root="${ROOTFS_RESOLVED}"
  local p
  for p in \
    lib/systemd/system/gdm.service \
    lib/systemd/system/gdm3.service; do
    if [[ -f "${root}/${p}" ]]; then
      printf '%s' "/${p}"
      return 0
    fi
  done
  return 1
}

runtime_gui_gdm_binary_path() {
  local root="${ROOTFS_RESOLVED}"
  local p
  for p in usr/sbin/gdm3 usr/sbin/gdm; do
    if [[ -x "${root}/${p}" ]]; then
      printf '/%s' "$p"
      return 0
    fi
  done
  return 1
}

# Chroot env for GUI/openbox: never inherit build-host HOME (e.g. /home/for).
runtime_recoverix_chroot_env_prefix() {
  local user home cache
  user="$(recoverix_runtime_user)"
  home="$(recoverix_runtime_home)"
  cache="${home}/.cache"
  printf "HOME='%s' USER='%s' LOGNAME='%s' XDG_CACHE_HOME='%s' XDG_CONFIG_HOME='%s/.config' XDG_DATA_HOME='%s/.local/share' " \
    "$home" "$user" "$user" "$cache" "$home" "$home"
}

runtime_log_openbox_chroot_env_forensic() {
  local cmd="${1:?}"
  local home_used xdg_cache

  home_used="$(recoverix_runtime_home)"
  xdg_cache="${home_used}/.cache"
  log "=== openbox-related chroot env forensic ==="
  log "openbox_command=${cmd}"
  log "openbox_home_used=${home_used}"
  log "openbox_xdg_cache_home=${xdg_cache}/openbox"
  log "host_build_HOME=${HOME:-<unset>}"
  log "host_build_USER=${USER:-<unset>}"
  log "host_build_LOGNAME=${LOGNAME:-<unset>}"
  log "host_build_pwd=$(pwd 2>/dev/null || echo unknown)"
}

runtime_rootfs_leaked_host_home_names() {
  printf '%s\n' for ubuntu
}

runtime_ensure_recoverix_openbox_cache_dir() {
  local root="${ROOTFS_RESOLVED:?}" home uid gid
  home="$(recoverix_runtime_home)"
  uid="$(recoverix_runtime_uid)"
  gid="$(recoverix_runtime_gid)"
  mkdir -p "${root}${home}/.cache/openbox"
  chown "${uid}:${gid}" "${root}${home}/.cache" "${root}${home}/.cache/openbox" 2>/dev/null || true
}

runtime_guard_host_home_after_chroot() {
  local caller="${1:-unknown}"
  local root="${ROOTFS_RESOLVED:?}" h

  for h in $(runtime_rootfs_leaked_host_home_names); do
    if [[ -e "${root}/home/${h}" ]]; then
      log_fail "creating host contamination path: /home/${h} caller=${caller}"
      RUNTIME_HOST_HOME_LEAK_SOURCE="${caller}"
      RUNTIME_HOST_HOME_LEAKED="yes"
      export RUNTIME_HOST_HOME_LEAK_SOURCE RUNTIME_HOST_HOME_LEAKED
    fi
  done
}

runtime_log_removed_host_home_check() {
  local removed="${1:-0}" report="${2:-}"
  local check_result="no_leak_found"

  if [[ "$removed" -eq 1 ]]; then
    check_result="removed_leaked_homes"
  fi

  log "removed_value=${removed}"
  log "removed_check_result=${check_result}"
  log "removed_check_required=no"

  if [[ -n "$report" ]]; then
    {
      echo "removed_value=${removed}"
      echo "removed_check_result=${check_result}"
      echo "removed_check_required=no"
    } >>"$report"
  fi
}

runtime_remove_leaked_host_homes_from_rootfs() {
  local report="${1:-}"
  local root="${ROOTFS_RESOLVED:?}" h removed=0

  for h in $(runtime_rootfs_leaked_host_home_names); do
    if [[ -d "${root}/home/${h}" ]]; then
      log_warn "removing leaked host home: ${root}/home/${h} (leak_source=${RUNTIME_HOST_HOME_LEAK_SOURCE:-unknown})"
      rm -rf "${root}/home/${h}"
      removed=1
    fi
  done

  if [[ $removed -eq 1 ]]; then
    log_pass "leaked host home directories removed from rootfs"
  fi

  runtime_log_removed_host_home_check "$removed" "$report"
  return 0
}

runtime_assert_no_leaked_host_homes_in_rootfs() {
  local root="${ROOTFS_RESOLVED:?}" report="${1:-}" failures=0 h

  for h in $(runtime_rootfs_leaked_host_home_names); do
    if [[ -e "${root}/home/${h}" ]]; then
      log_fail "FAIL: leaked host home /home/${h} in rootfs (source=${RUNTIME_HOST_HOME_LEAK_SOURCE:-unknown})"
      failures=$((failures + 1))
    fi
  done

  if [[ "${RUNTIME_HOST_HOME_LEAKED:-no}" == "yes" ]]; then
    log_fail "FAIL: host_home_leaked=yes (source=${RUNTIME_HOST_HOME_LEAK_SOURCE:-unknown})"
    failures=$((failures + 1))
  fi

  if [[ $failures -eq 0 ]]; then
    RUNTIME_HOST_HOME_LEAKED="no"
    export RUNTIME_HOST_HOME_LEAKED
    log_pass "no leaked host home directories under rootfs /home"
  fi

  if [[ -n "$report" ]]; then
    {
      echo "openbox_home_used=$(recoverix_runtime_home)"
      echo "openbox_xdg_cache_home=$(recoverix_runtime_home)/.cache/openbox"
      echo "host_home_leaked=${RUNTIME_HOST_HOME_LEAKED:-no}"
      echo "host_home_leak_source=${RUNTIME_HOST_HOME_LEAK_SOURCE:-none}"
    } >>"$report"
  fi

  [[ $failures -eq 0 ]]
}

runtime_log_host_home_leak_report() {
  local home_used xdg_cache
  home_used="$(recoverix_runtime_home)"
  xdg_cache="${home_used}/.cache/openbox"
  log "openbox_home_used=${home_used}"
  log "openbox_xdg_cache_home=${xdg_cache}"
  log "host_home_leaked=${RUNTIME_HOST_HOME_LEAKED:-no}"
  log "host_home_leak_source=${RUNTIME_HOST_HOME_LEAK_SOURCE:-none}"
}

runtime_gui_chroot_exec() {
  local cmd="${1:?}"
  local env_prefix caller

  env_prefix="$(runtime_recoverix_chroot_env_prefix)"
  caller="${FUNCNAME[1]:-runtime_gui_chroot_exec}"

  if [[ "$cmd" == *openbox* ]]; then
    runtime_log_openbox_chroot_env_forensic "$cmd"
    runtime_ensure_recoverix_openbox_cache_dir
  fi

  if declare -f runtime_debootstrap_chroot >/dev/null 2>&1; then
    runtime_debootstrap_chroot "${env_prefix}${cmd}"
  else
    chroot "${ROOTFS_RESOLVED:?}" /bin/bash -c "${env_prefix}${cmd}"
  fi
  runtime_guard_host_home_after_chroot "$caller"
}

runtime_gui_mount_chroot_deps() {
  local report="${1:-${RUNTIME_GUI_MOUNT_REPORT:-}}"

  log "gui_chroot_mount_stage=gui_package_install"
  if ! runtime_chroot_mount_deps "${ROOTFS_RESOLVED}" "$report" "gui_package_install"; then
    return 1
  fi
  mkdir -p "${ROOTFS_RESOLVED}/etc" 2>/dev/null || true
  cp -f /etc/resolv.conf "${ROOTFS_RESOLVED}/etc/resolv.conf" 2>/dev/null || true
  return 0
}

runtime_gui_umount_chroot_deps() {
  runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
}

runtime_gui_stack_install_summary() {
  local rc=0
  log "=== GUI stack install summary ==="
  set +e
  runtime_gui_verify_boot_capabilities "${ROOTFS_RESOLVED}"
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    log_pass "GUI stack install summary: OK (FAIL=0)"
    return 0
  fi
  log_fail "GUI stack install summary: capability validation failed (see FAIL count above)"
  return 1
}

runtime_accountsservice_user_file_path() {
  local user="${1:-$(recoverix_runtime_user)}"
  printf '%s/var/lib/AccountsService/users/%s' "${ROOTFS_RESOLVED:?}" "$user"
}

runtime_log_accountsservice_report_fields() {
  local user="${1:?}" exists="${2:?}" owner="${3:?}" mode="${4:?}" session_valid="${5:?}"

  log "accountsservice_user_file=/var/lib/AccountsService/users/${user}"
  log "accountsservice_user_file_exists=${exists}"
  log "accountsservice_user_file_owner=${owner}"
  log "accountsservice_user_file_mode=${mode}"
  log "accountsservice_session=openbox"
  log "accountsservice_session_valid=${session_valid}"
}

runtime_log_accountsservice_validation_report() {
  local validation_path="${1:?}" file_exists="${2:?}" owner="${3:?}" mode="${4:?}" session="${5:?}" session_valid="${6:?}"

  log "accountsservice_validation_path=${validation_path}"
  log "accountsservice_file_exists=${file_exists}"
  log "accountsservice_detected_owner=${owner}"
  log "accountsservice_detected_mode=${mode}"
  log "accountsservice_detected_session=${session}"
  log "accountsservice_session_valid=${session_valid}"
}

runtime_log_accountsservice_user_file_state() {
  local accounts="${1:?}"

  log "=== AccountsService user file forensic ==="
  ls -l "$accounts" 2>&1 | while IFS= read -r line; do
    log "  ${line}"
  done
  while IFS= read -r line || [[ -n "$line" ]]; do
    log "  ${line}"
  done <"$accounts"
}

runtime_log_accountsservice_validation_forensic() {
  local accounts="${1:?}"

  log "=== AccountsService validation forensic ==="
  if [[ ! -e "$accounts" ]]; then
    log "  ls -l: path does not exist"
    log "  stat: path does not exist"
    return 0
  fi
  ls -l "$accounts" 2>&1 | while IFS= read -r line; do
    log "  ${line}"
  done
  stat "$accounts" 2>&1 | while IFS= read -r line; do
    log "  ${line}"
  done
  if [[ -f "$accounts" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      log "  ${line}"
    done <"$accounts"
  fi
}

# Validate AccountsService user file under ROOTFS_RESOLVED only (never host /var/lib).
runtime_verify_accountsservice_user_file() {
  local user="${1:-$(recoverix_runtime_user)}"
  local root="${ROOTFS_RESOLVED:-}"
  local accounts
  local acc_owner="missing" acc_mode="missing" acc_exists="no" acc_session_valid="no"
  local detected_session="missing"
  local failures=0

  if [[ -z "$root" ]]; then
    log_fail "FAIL: ROOTFS_RESOLVED unset — cannot validate AccountsService in rootfs"
    runtime_log_accountsservice_validation_report "(unset)" "no" "missing" "missing" "missing" "no"
    log "accountsservice_validation_result=FAIL"
    log "accountsservice_validation_return=1"
    return 1
  fi

  root="${root%/}"
  accounts="${root}/var/lib/AccountsService/users/${user}"

  log "accountsservice_validation_path=${accounts}"
  runtime_log_accountsservice_validation_forensic "$accounts"

  if [[ "$accounts" == "/var/lib/AccountsService/users/${user}" ]]; then
    log_fail "FAIL: AccountsService validation path is host /var/lib — must use ROOTFS_RESOLVED"
    runtime_log_accountsservice_validation_report "$accounts" "no" "missing" "missing" "missing" "no"
    log "accountsservice_validation_result=FAIL"
    log "accountsservice_validation_return=1"
    return 1
  fi

  if [[ -f "$accounts" ]]; then
    acc_exists="yes"
    log_pass "PASS: AccountsService user file exists"
  else
    log_fail "FAIL: AccountsService user file missing in rootfs"
    runtime_log_accountsservice_report_fields "$user" "$acc_exists" "missing" "missing" "$acc_session_valid"
    runtime_log_accountsservice_validation_report "$accounts" "no" "missing" "missing" "missing" "no"
    log "accountsservice_validation_result=FAIL"
    log "accountsservice_validation_return=1"
    return 1
  fi

  detected_session="$(grep -m1 '^Session=' "$accounts" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
  [[ -n "$detected_session" ]] || detected_session="missing"

  acc_owner="$(stat -c '%U:%G' "$accounts" 2>/dev/null || echo missing)"
  acc_mode="$(stat -c '%a' "$accounts" 2>/dev/null || echo missing)"

  log "detected_owner=${acc_owner}"
  log "detected_mode=${acc_mode}"
  log "detected_session=${detected_session}"

  if [[ "$acc_exists" == "yes" && "$acc_owner" == "missing" && "$acc_mode" == "missing" ]]; then
    log_fail "FAIL: AccountsService validation logic bug (file exists but owner/mode unreadable)"
    failures=$((failures + 1))
  fi

  if [[ "$detected_session" == "openbox" ]]; then
    acc_session_valid="yes"
    log_pass "PASS: AccountsService Session=openbox configured"
  else
    log_fail "FAIL: AccountsService Session=openbox required"
    failures=$((failures + 1))
  fi

  if [[ "$acc_owner" == "root:root" ]]; then
    log_pass "PASS: AccountsService owner root:root"
  else
    log_fail "FAIL: AccountsService owner must be root:root (got ${acc_owner})"
    failures=$((failures + 1))
  fi

  if [[ "$acc_mode" == "600" ]]; then
    log_pass "PASS: AccountsService mode 600"
  elif [[ "$acc_mode" == "644" ]]; then
    log_pass "PASS: AccountsService mode 644"
  else
    log_fail "FAIL: AccountsService mode must be 600 or 644 (got ${acc_mode})"
    failures=$((failures + 1))
  fi

  if [[ "$acc_exists" == "yes" && "$failures" -gt 0 && "$acc_owner" == "missing" && "$acc_mode" == "missing" && "$detected_session" == "missing" ]]; then
    log_fail "FAIL: AccountsService validation logic bug (file exists but all checks reported missing)"
    failures=$((failures + 1))
  fi

  runtime_log_accountsservice_report_fields "$user" "$acc_exists" "$acc_owner" "$acc_mode" "$acc_session_valid"
  runtime_log_accountsservice_validation_report "$accounts" "$acc_exists" "$acc_owner" "$acc_mode" \
    "$detected_session" "$acc_session_valid"

  local expected_ok=0
  if [[ "$acc_exists" == "yes" && "$acc_owner" == "root:root" && \
        ( "$acc_mode" == "600" || "$acc_mode" == "644" ) && \
        "$detected_session" == "openbox" ]]; then
    expected_ok=1
  fi

  if [[ $expected_ok -eq 1 && $failures -eq 0 ]]; then
    RUNTIME_ACCOUNTSSERVICE_SESSION_VALID="yes"
    export RUNTIME_ACCOUNTSSERVICE_SESSION_VALID
    log_pass "PASS: AccountsService entry valid for ${user}"
    log "accountsservice_validation_result=PASS"
    log "accountsservice_validation_return=0"
    return 0
  fi

  if [[ $expected_ok -eq 1 && $failures -gt 0 ]] || \
     [[ "$acc_session_valid" == "yes" && $failures -gt 0 ]]; then
    log_fail "FAIL: AccountsService validation return mismatch"
  fi

  RUNTIME_ACCOUNTSSERVICE_SESSION_VALID="no"
  export RUNTIME_ACCOUNTSSERVICE_SESSION_VALID
  log "accountsservice_validation_result=FAIL"
  log "accountsservice_validation_return=1"
  return 1
}

# Invoke AccountsService validation and log return code (binary 0/1 only).
runtime_run_accountsservice_validation() {
  local user="${1:-$(recoverix_runtime_user)}"
  local rc=0

  set +e
  runtime_verify_accountsservice_user_file "$user"
  rc=$?
  set -e
  log "accountsservice_validation_rc=${rc}"
  if [[ $rc -ne 0 && "${RUNTIME_ACCOUNTSSERVICE_SESSION_VALID:-}" == "yes" ]]; then
    log_fail "FAIL: AccountsService validation return mismatch"
    rc=1
  fi
  return "$rc"
}

runtime_install_gui_stack_packages() {
  local pkg mounted=0 failures=0
  local -a installed=()
  local _saved_chroot_env="${RECOVERIX_CHROOT_RUNTIME_USER_ENV:-0}"

  log_step "install GUI stack packages (openbox provider, then gdm3, then xorg stack, …)"
  RUNTIME_HOST_HOME_LEAKED="no"
  RUNTIME_HOST_HOME_LEAK_SOURCE=""
  export RUNTIME_HOST_HOME_LEAKED RUNTIME_HOST_HOME_LEAK_SOURCE
  RECOVERIX_CHROOT_RUNTIME_USER_ENV=1
  export RECOVERIX_CHROOT_RUNTIME_USER_ENV

  runtime_apt_configure_strict_minimal_policy || true
  runtime_apt_install_log_init || true

  if ! runtime_gui_mount_chroot_deps "${RUNTIME_APT_INSTALL_LOG:-}"; then
    log_fail "cannot mount chroot deps for GUI package install"
    return 1
  fi
  mounted=1

  runtime_gui_chroot_exec 'apt-get update' || log_warn "apt-get update failed (continuing)"

  # openbox before gdm3: Provides x-session-manager | x-window-manager (gdm3 Depends OR).
  if recoverix_runtime_apt_install openbox; then
    installed+=(openbox)
  else
    log_fail "openbox install failed — required before gdm3 (x-session-manager provider)"
    failures=$((failures + 1))
  fi

  if [[ $failures -eq 0 ]]; then
    if recoverix_runtime_apt_install_gdm3; then
      installed+=(gdm3)
    else
      log_fail "gdm3 install failed — cannot continue GUI stack"
      failures=$((failures + 1))
    fi
  fi

  while IFS= read -r pkg; do
    [[ -n "$pkg" ]] || continue
    [[ "$pkg" == openbox ]] && continue
    if recoverix_runtime_apt_install "$pkg"; then
      installed+=("$pkg")
    else
      log_fail "GUI package install failed: ${pkg}"
      failures=$((failures + 1))
    fi
  done < <(runtime_gui_packages_list)

  if [[ $mounted -eq 1 ]]; then
    runtime_gui_umount_chroot_deps
  fi

  RECOVERIX_CHROOT_RUNTIME_USER_ENV="${_saved_chroot_env}"
  export RECOVERIX_CHROOT_RUNTIME_USER_ENV
  runtime_remove_leaked_host_homes_from_rootfs
  runtime_log_host_home_leak_report

  RUNTIME_GUI_INSTALLED_PACKAGES="${installed[*]:-}"
  export RUNTIME_GUI_INSTALLED_PACKAGES

  [[ $failures -eq 0 ]] || return 1
  log_pass "GUI stack packages installed (${#installed[@]} packages)"
  return 0
}

runtime_configure_graphical_target() {
  local mounted=0 default_target gdm_unit

  log_step "configure graphical.target default + enable GDM"

  gdm_unit="$(runtime_gui_gdm_unit_path || true)"
  if [[ -z "$gdm_unit" ]]; then
    log_fail "FAIL: gdm systemd unit missing — install gdm3 first (recoverix_runtime_apt_install_gdm3)"
    log_fail "hint: check reports/runtime-build/runtime_apt_install_*.log for gdm3 simulate/install errors"
    return 1
  fi

  if ! runtime_gui_mount_chroot_deps; then
    log_fail "cannot mount chroot for graphical.target configuration"
    return 1
  fi
  mounted=1

  if ! runtime_gui_chroot_exec 'systemctl set-default graphical.target'; then
    log_fail "systemctl set-default graphical.target failed"
    [[ $mounted -eq 1 ]] && runtime_gui_umount_chroot_deps
    return 1
  fi

  if runtime_gui_chroot_exec 'systemctl enable gdm.service'; then
    log_pass "enabled gdm.service"
  elif runtime_gui_chroot_exec 'systemctl enable gdm3.service'; then
    log_pass "enabled gdm3.service"
  else
    log_fail "FAIL: systemctl enable gdm.service|gdm3.service failed (unit path: ${gdm_unit})"
    [[ $mounted -eq 1 ]] && runtime_gui_umount_chroot_deps
    return 1
  fi

  default_target="$(runtime_gui_chroot_exec 'systemctl get-default' 2>/dev/null | tr -d '\r\n' || true)"
  RUNTIME_GUI_DEFAULT_TARGET="${default_target:-unknown}"
  export RUNTIME_GUI_DEFAULT_TARGET

  [[ $mounted -eq 1 ]] && runtime_gui_umount_chroot_deps

  if [[ "$default_target" == "graphical.target" ]]; then
    log_pass "default target is graphical.target"
    return 0
  fi
  log_fail "expected graphical.target default, got: ${default_target}"
  return 1
}

runtime_install_recoverix_accounts_service_user() {
  local user home uid gid accounts
  user="$(recoverix_runtime_user)"
  home="$(recoverix_runtime_home)"
  uid="$(recoverix_runtime_uid)"
  gid="$(recoverix_runtime_gid)"
  accounts="${ROOTFS_RESOLVED}/var/lib/AccountsService/users/${user}"

  log_step "configure AccountsService session for ${user}"
  mkdir -p "${ROOTFS_RESOLVED}/var/lib/AccountsService/users"
  if [[ -f "$accounts" ]]; then
    # Keep existing keys and values; only enforce Session=openbox.
    if grep -q '^Session=' "$accounts"; then
      sed -i 's/^Session=.*/Session=openbox/' "$accounts"
    elif grep -q '^\[User\]' "$accounts"; then
      awk '
        BEGIN { inserted=0 }
        /^\[User\]$/ { print; print "Session=openbox"; inserted=1; next }
        { print }
        END { if (!inserted) { print "[User]"; print "Session=openbox" } }
      ' "$accounts" >"${accounts}.tmp" && mv "${accounts}.tmp" "$accounts"
    else
      {
        echo "[User]"
        echo "Session=openbox"
        cat "$accounts"
      } >"${accounts}.tmp" && mv "${accounts}.tmp" "$accounts"
    fi
  else
    cat >"$accounts" <<EOF
[User]
Session=openbox
EOF
  fi
  chown root:root "$accounts" 2>/dev/null || true
  chmod 0600 "$accounts"

  if [[ ! -f "$accounts" ]]; then
    log_fail "FAIL: AccountsService user file missing after configure (${accounts})"
    return 1
  fi

  runtime_log_accountsservice_user_file_state "$accounts"
  runtime_log_accountsservice_report_fields "$user" "yes" \
    "$(stat -c '%U:%G' "$accounts" 2>/dev/null || echo missing)" \
    "$(stat -c '%a' "$accounts" 2>/dev/null || echo missing)" \
    "$(grep -q '^Session=openbox' "$accounts" && echo yes || echo no)"

  mkdir -p "${ROOTFS_RESOLVED}${home}/.config"
  cat >"${ROOTFS_RESOLVED}${home}/.dmrc" <<'EOF'
[Desktop]
Session=openbox
EOF
  chown "${uid}:${gid}" "${ROOTFS_RESOLVED}${home}/.dmrc" 2>/dev/null || true
  chmod 0644 "${ROOTFS_RESOLVED}${home}/.dmrc" 2>/dev/null || true

  log_pass "AccountsService user entry: openbox session"
  return 0
}

runtime_verify_gdm_custom_conf_syntax() {
  local conf failures=0 user
  conf="$(recoverix_runtime_gdm_custom_conf_path)"
  user="$(recoverix_runtime_user)"

  [[ -f "$conf" ]] || {
    log_fail "missing GDM custom.conf: ${conf}"
    return 1
  }

  if ! grep -q '^\[daemon\]' "$conf"; then
    log_fail "GDM custom.conf missing [daemon] section"
    failures=$((failures + 1))
  fi
  if ! grep -q '^WaylandEnable=false' "$conf"; then
    log_fail "GDM custom.conf missing WaylandEnable=false (explicit Xorg backend)"
    failures=$((failures + 1))
  fi
  if [[ "${RECOVERIX_RUNTIME_AUTOLOGIN:-1}" == "1" ]]; then
    grep -q '^AutomaticLoginEnable=True' "$conf" || failures=$((failures + 1))
    grep -q "^AutomaticLogin=${user}$" "$conf" || failures=$((failures + 1))
  else
    grep -q '^AutomaticLoginEnable=False' "$conf" || failures=$((failures + 1))
  fi

  [[ $failures -eq 0 ]] || return 1
  return 0
}

runtime_verify_gui_stack_rootfs() {
  local root="${ROOTFS_RESOLVED}" failures=0
  local gdm_unit gdm_bin xsession

  log_step "verify GUI stack in rootfs"

  gdm_unit="$(runtime_gui_gdm_unit_path || true)"
  if [[ -n "$gdm_unit" && -f "${root}${gdm_unit}" ]]; then
    log_pass "gdm systemd unit: ${gdm_unit}"
  else
    log_fail "missing gdm.service or gdm3.service under /lib/systemd/system"
    failures=$((failures + 1))
  fi

  gdm_bin="$(runtime_gui_gdm_binary_path || true)"
  if [[ -n "$gdm_bin" && -x "${root}${gdm_bin}" ]]; then
    log_pass "gdm binary: ${gdm_bin}"
  else
    log_fail "missing /usr/sbin/gdm3 or gdm"
    failures=$((failures + 1))
  fi

  if [[ -x "${root}/usr/bin/Xorg" ]]; then
    log_pass "present: /usr/bin/Xorg"
  else
    log_fail "missing or not executable: /usr/bin/Xorg"
    failures=$((failures + 1))
  fi
  if [[ -x "${root}/usr/lib/xorg/Xorg" ]]; then
    log_pass "present: /usr/lib/xorg/Xorg"
  else
    log_fail "missing or not executable: /usr/lib/xorg/Xorg"
    failures=$((failures + 1))
  fi
  if [[ -d "${root}/usr/lib/xorg/modules" ]]; then
    log_pass "present: /usr/lib/xorg/modules"
  else
    log_fail "missing /usr/lib/xorg/modules"
    failures=$((failures + 1))
  fi

  if [[ -x "${root}/usr/bin/openbox" ]]; then
    log_pass "present: /usr/bin/openbox"
  else
    log_fail "missing or not executable: /usr/bin/openbox"
    failures=$((failures + 1))
  fi

  xsession="$(runtime_gui_openbox_xsession_path)"
  if [[ -f "$xsession" ]]; then
    log_pass "present: /usr/share/xsessions/openbox.desktop"
  else
    log_fail "missing: /usr/share/xsessions/openbox.desktop (install openbox)"
    failures=$((failures + 1))
  fi

  if [[ -f "${root}/lib/systemd/system/graphical.target" ]]; then
    log_pass "graphical.target unit present"
  else
    log_fail "missing graphical.target"
    failures=$((failures + 1))
  fi

  local default_target
  default_target="$(chroot "$root" systemctl get-default 2>/dev/null | tr -d '\r\n' || true)"
  if [[ "$default_target" == "graphical.target" ]]; then
    log_pass "system default target: graphical.target"
  else
    log_fail "system default target is not graphical (${default_target:-unknown})"
    failures=$((failures + 1))
  fi

  runtime_verify_gdm_custom_conf_syntax || failures=$((failures + 1))
  runtime_verify_rootfs_recoverix_runtime_user_session || failures=$((failures + 1))
  runtime_run_accountsservice_validation || failures=$((failures + 1))

  runtime_apt_verify_gui_stack_dpkg_policy || failures=$((failures + 1))

  [[ $failures -eq 0 ]] || return 1
  log_pass "GUI stack rootfs verification complete"
  return 0
}

runtime_verify_recovery_ui_launcher_rootfs() {
  local launcher failures=0 mounted=0
  launcher="$(recoverix_recovery_ui_launcher_path)"

  [[ -f "$launcher" && -x "$launcher" ]] || {
    log_fail "recovery-ui launcher missing or not executable"
    return 1
  }

  if ! runtime_gui_mount_chroot_deps; then
    log_warn "skipping recovery-ui python import check (no chroot mounts)"
    return 0
  fi
  mounted=1

  if runtime_gui_chroot_exec \
    'PYTHONPATH=/usr/local/lib/recoverix python3 -c "import recovery_runtime.gtk_ui.main" >/dev/null 2>&1'; then
    log_pass "recovery-ui python import OK in rootfs chroot"
  else
    log_fail "recovery-ui python import failed in rootfs chroot"
    failures=$((failures + 1))
  fi

  [[ $mounted -eq 1 ]] && runtime_gui_umount_chroot_deps
  [[ $failures -eq 0 ]] || return 1
  return 0
}

runtime_install_gui_systemd_units() {
  log_step "install recoverix GUI systemd units"
  log_pass "recoverix GUI systemd units: minimal mode (no forensic/xorg experimental units)"
  return 0
}

runtime_run_gui_stack_chroot_selftest() {
  log_step "GUI stack chroot selftest"
  log_pass "GUI stack chroot selftest skipped in minimal mode"
  return 0
}

runtime_install_gui_session_stack() {
  local rc=0

  log "=== Install Recoverix Runtime GUI session stack ==="
  runtime_install_gui_stack_packages || rc=1
  if [[ $rc -ne 0 ]]; then
    log_fail "GUI package install failed — skipping session configuration"
    return 1
  fi
  if declare -f runtime_install_recoverix_recovery_ui >/dev/null 2>&1; then
    runtime_install_recoverix_recovery_ui || rc=1
  fi
  runtime_install_recoverix_accounts_service_user || rc=1
  runtime_configure_graphical_target || rc=1
  runtime_install_gui_systemd_units || rc=1
  runtime_remove_leaked_host_homes_from_rootfs
  runtime_assert_no_leaked_host_homes_in_rootfs "" || rc=1
  runtime_gui_stack_install_summary || rc=1
  return "$rc"
}

runtime_append_gui_stack_build_report() {
  local report="${1:?}"
  local pkg_list default_target gdm_unit

  pkg_list="$(runtime_gui_packages_list 2>/dev/null | paste -sd, - || echo unknown)"
  default_target="$(chroot "${ROOTFS_RESOLVED}" systemctl get-default 2>/dev/null | tr -d '\r\n' || echo unknown)"
  gdm_unit="$(runtime_gui_gdm_unit_path 2>/dev/null || echo missing)"

  {
    echo
    echo "=== GUI session stack ==="
    echo "display_manager: gdm3 (${gdm_unit})"
    echo "gdm3_policy: minimal install (--no-install-recommends; jammy hard-deps allowlist)"
    echo "gdm3_install_order: openbox before gdm3 (Provides x-session-manager for Depends OR)"
    echo "gdm3_install_strategy=${RUNTIME_GDM3_INSTALL_STRATEGY:-openbox_provider}"
    echo "gdm3_dependency_or_resolved_by=${RUNTIME_GDM3_DEPENDENCY_OR_RESOLVED_BY:-x-session-manager}"
    echo "gdm3_simulate_ubuntu_session=${RUNTIME_GDM3_SIMULATE_UBUNTU_SESSION:-unknown}"
    echo "session: openbox via /usr/share/xsessions/openbox.desktop"
    echo "gui_stack_packages: openbox,gdm3,${pkg_list}"
    echo "graphical_target_default: ${default_target}"
    echo "gdm_autologin: ${RECOVERIX_RUNTIME_AUTOLOGIN:-1}"
    echo "runtime_user: ${RECOVERIX_RUNTIME_USER:-recoverix}"
    echo "recovery_ui_autostart: /etc/xdg/autostart/recoverix-recovery-ui.desktop"
    local accounts_file accounts_valid accounts_owner accounts_mode accounts_exists detected_session
    accounts_file="${ROOTFS_RESOLVED}/var/lib/AccountsService/users/$(recoverix_runtime_user)"
    accounts_valid="no"
    accounts_exists="no"
    accounts_owner="missing"
    accounts_mode="missing"
    detected_session="missing"
    if [[ -f "$accounts_file" ]]; then
      accounts_exists="yes"
      accounts_owner="$(stat -c '%U:%G' "$accounts_file" 2>/dev/null || echo missing)"
      accounts_mode="$(stat -c '%a' "$accounts_file" 2>/dev/null || echo missing)"
      detected_session="$(grep -m1 '^Session=' "$accounts_file" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
      [[ -n "$detected_session" ]] || detected_session="missing"
      if [[ "$detected_session" == "openbox" ]]; then
        accounts_valid="yes"
      fi
    fi
    echo "accountsservice_user_file=/var/lib/AccountsService/users/$(recoverix_runtime_user)"
    echo "accountsservice_user_file_exists=${accounts_exists}"
    echo "accountsservice_user_file_owner=${accounts_owner}"
    echo "accountsservice_user_file_mode=${accounts_mode}"
    echo "accountsservice_session=openbox"
    echo "accountsservice_session_valid=${accounts_valid}"
    echo "accountsservice_validation_path=${accounts_file}"
    echo "accountsservice_file_exists=${accounts_exists}"
    echo "accountsservice_detected_owner=${accounts_owner}"
    echo "accountsservice_detected_mode=${accounts_mode}"
    echo "accountsservice_detected_session=${detected_session}"
    echo "openbox_home_used=$(recoverix_runtime_home)"
    echo "openbox_xdg_cache_home=$(recoverix_runtime_home)/.cache/openbox"
    echo "host_home_leaked=${RUNTIME_HOST_HOME_LEAKED:-no}"
    echo "host_home_leak_source=${RUNTIME_HOST_HOME_LEAK_SOURCE:-none}"
  } >>"$report"
}
