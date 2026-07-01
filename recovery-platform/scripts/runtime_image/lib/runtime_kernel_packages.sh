#!/usr/bin/env bash
# Kernel image/modules-extra install forensics for Recoverix runtime rootfs.

RUNTIME_LAST_APT_INSTALL_OUTPUT=""
RUNTIME_INSTALL_KERNEL_PACKAGES_EXECUTED="${RUNTIME_INSTALL_KERNEL_PACKAGES_EXECUTED:-0}"
RUNTIME_LAST_APT_INSTALL_FAILURE_REASON="${RUNTIME_LAST_APT_INSTALL_FAILURE_REASON:-}"
RUNTIME_KERNEL_PACKAGES_INSTALL_REPORT="${RUNTIME_KERNEL_PACKAGES_INSTALL_REPORT:-}"

runtime_kernel_package_report_line() {
  local report="${1:-}" line="${2:?}"
  log "$line"
  [[ -n "$report" ]] && printf '%s\n' "$line" >>"$report"
}

runtime_kernel_package_dpkg_installed_yes_no() {
  local pkg="${1:?}"
  if host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
    printf 'yes'
  else
    printf 'no'
  fi
}

runtime_kernel_package_append_install_report() {
  local kver="${1:?}" report="${2:-}"
  local img="linux-image-${kver}"
  local mod="linux-modules-${kver}"
  local extra="linux-modules-extra-${kver}"

  runtime_kernel_package_report_line "$report" "kernel_flavor=${kver}"
  runtime_kernel_package_report_line "$report" \
    "linux_image_installed=$(runtime_kernel_package_dpkg_installed_yes_no "$img")"
  runtime_kernel_package_report_line "$report" \
    "linux_modules_installed=$(runtime_kernel_package_dpkg_installed_yes_no "$mod")"
  runtime_kernel_package_report_line "$report" \
    "linux_modules_extra_installed=$(runtime_kernel_package_dpkg_installed_yes_no "$extra")"
}

runtime_kernel_package_log_provision_context() {
  local report="${1:-}"
  local marker="unknown"

  if marker="$(runtime_read_rootfs_source_marker 2>/dev/null)"; then
    :
  fi
  runtime_kernel_package_report_line "$report" "runtime_rootfs_source_marker=${marker}"
  runtime_kernel_package_report_line "$report" \
    "RECOVERIX_RUNTIME_BUILD_MODE=${RECOVERIX_RUNTIME_BUILD_MODE:-auto}"
  runtime_kernel_package_report_line "$report" \
    "RECOVERIX_FORCE_DEBOOTSTRAP=${RECOVERIX_FORCE_DEBOOTSTRAP:-0}"
}

runtime_kernel_package_log_host_target_kernel() {
  local kver="${KEEP_KERNEL_FLAVOR:?}" report="${1:-}"

  runtime_kernel_package_report_line "$report" "host_uname_r=$(uname -r 2>/dev/null || echo unknown)"
  runtime_kernel_package_report_line "$report" "KEEP_KERNEL_FLAVOR=${kver}"
  if [[ "$(uname -r 2>/dev/null)" != "$kver" ]]; then
    runtime_kernel_package_report_line "$report" \
      "WARN: host running kernel ($(uname -r 2>/dev/null)) differs from KEEP_KERNEL_FLAVOR (${kver})"
  fi
}

runtime_kernel_package_log_apt_policy() {
  local kver="${1:?}" report="${2:-}"
  local img="linux-image-${kver}"
  local extra="linux-modules-extra-${kver}"
  local policy_out

  log "=== Kernel package apt-cache policy (rootfs chroot) ==="
  runtime_kernel_package_report_line "$report" "=== apt-cache policy (kernel packages) ==="

  for policy_out in \
    "--- apt-cache policy ${img} ---" \
    "$(runtime_rootfs_chroot_exec "apt-cache policy '${img}'" 2>&1 || true)" \
    "--- apt-cache policy ${extra} ---" \
    "$(runtime_rootfs_chroot_exec "apt-cache policy '${extra}'" 2>&1 || true)"; do
    log "$policy_out"
    [[ -n "$report" ]] && printf '%s\n' "$policy_out" >>"$report"
  done
}

runtime_kernel_package_log_dpkg_query_w() {
  local kver="${1:?}" report="${2:-}"
  local pkg status

  log "=== Kernel package dpkg-query -W (rootfs) ==="
  runtime_kernel_package_report_line "$report" "=== dpkg-query -W (kernel packages) ==="

  for pkg in "linux-image-${kver}" "linux-modules-${kver}" "linux-modules-extra-${kver}"; do
    status="$(host_dpkg_query -W -f='${Package}\t${Status}' "$pkg" 2>/dev/null || echo "${pkg}	(not in dpkg database)")"
    log "$status"
    [[ -n "$report" ]] && printf '%s\n' "$status" >>"$report"
  done
}

runtime_kernel_package_log_chroot_dpkg_list() {
  local report="${1:-}"
  local out

  log "=== chroot dpkg -l | grep linux-modules-extra ==="
  runtime_kernel_package_report_line "$report" "=== chroot: dpkg -l | grep linux-modules-extra ==="
  out="$(runtime_rootfs_chroot_exec "dpkg -l 2>/dev/null | grep linux-modules-extra || echo '(no linux-modules-extra lines in dpkg -l)'" 2>&1 || true)"
  log "$out"
  [[ -n "$report" ]] && printf '%s\n' "$out" >>"$report"
}

# Classify apt install failure from captured output (printed to log + report).
runtime_apt_classify_install_failure() {
  local pkg="${1:?}" output="${2:-}" report="${3:-}"
  local reason="unknown_install_failure"
  local detail=""

  if [[ -z "$output" ]]; then
    reason="unknown_install_failure"
    detail="empty apt output (check ${RUNTIME_APT_INSTALL_LOG:-runtime_apt_install log})"
  elif grep -qiE 'Unable to locate package' <<<"$output"; then
    reason="package_not_found"
    detail="package not in apt index or wrong name"
  elif grep -qi 'has no installation candidate' <<<"$output"; then
    reason="package_not_found"
    detail="no installation candidate (repository/component or wrong kernel flavor)"
  elif grep -qiE 'unmet dependencies|broken packages|dependency problems|Depends:' <<<"$output"; then
    reason="dependency_failure"
    detail="unmet dependencies or broken package set"
  elif grep -qiE '404 Not Found|Failed to fetch|Temporary failure resolving|Could not handshake' <<<"$output"; then
    reason="apt_cache_missing_or_mirror_error"
    detail="mirror/network failure — run apt-get update in rootfs"
  elif grep -qiE 'E:.*update|The list of sources could not be read' <<<"$output"; then
    reason="apt_cache_missing_or_mirror_error"
    detail="apt sources/index problem"
  elif grep -qiE 'refusing install.*purity|simulate.*forbidden|would Inst forbidden' <<<"$output"; then
    reason="runtime_purity_policy_refusal"
    detail="recoverix apt simulate or purity policy blocked install"
  else
    local cand
    cand="$(runtime_rootfs_chroot_exec "apt-cache policy '${pkg}' 2>/dev/null" 2>&1 || true)"
    if grep -qE 'Candidate: \(none\)' <<<"$cand"; then
      reason="wrong_kernel_flavor_or_missing_repo"
      detail="apt Candidate is (none) for ${pkg} — check KEEP_KERNEL_FLAVOR and enabled apt components"
    fi
  fi

  runtime_kernel_package_report_line "$report" "apt_install_failure_package=${pkg}"
  runtime_kernel_package_report_line "$report" "apt_install_failure_reason=${reason}"
  runtime_kernel_package_report_line "$report" "apt_install_failure_detail=${detail}"
  RUNTIME_LAST_APT_INSTALL_FAILURE_REASON="${reason}"
  export RUNTIME_LAST_APT_INSTALL_FAILURE_REASON
  log_fail "apt install failure classified: package=${pkg} reason=${reason} (${detail})"
}

runtime_apt_report_install_failure() {
  local pkg="${1:?}" report="${2:-}"
  local output="${RUNTIME_LAST_APT_INSTALL_OUTPUT:-}"

  log_fail "FAIL: recoverix_runtime_apt_install failed for ${pkg}"
  runtime_apt_classify_install_failure "$pkg" "$output" "$report"
  if [[ -n "$output" ]]; then
    log "--- apt install captured output (tail) ---"
    printf '%s\n' "$output" | tail -40
    [[ -n "$report" ]] && {
      echo "--- apt install captured output (tail) ---" >>"$report"
      printf '%s\n' "$output" | tail -40 >>"$report"
    }
  fi
}

# RECOVERIX_FORCE_DEBOOTSTRAP=1 contract: install must run and modules-extra must be present.
runtime_kernel_package_assert_force_debootstrap_contract() {
  local report="${1:-}"
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  local extra="linux-modules-extra-${kver}"
  local mounted=0

  [[ "${RECOVERIX_FORCE_DEBOOTSTRAP:-0}" == "1" ]] || return 0

  runtime_kernel_package_report_line "$report" "force_debootstrap_contract_check=1"

  if [[ "${RUNTIME_INSTALL_KERNEL_PACKAGES_EXECUTED:-0}" != "1" ]]; then
    runtime_kernel_package_report_line "$report" \
      "FAIL: RECOVERIX_FORCE_DEBOOTSTRAP=1 but runtime_install_kernel_packages was not executed"
    log_fail "FAIL: RECOVERIX_FORCE_DEBOOTSTRAP=1 but runtime_install_kernel_packages was not executed"
    return 1
  fi

  runtime_kernel_package_append_install_report "$kver" "$report"

  if [[ "$(runtime_kernel_package_dpkg_installed_yes_no "$extra")" == "yes" ]]; then
    runtime_kernel_package_report_line "$report" \
      "PASS: force_debootstrap contract — linux_modules_extra_installed=yes"
    return 0
  fi

  runtime_kernel_package_report_line "$report" \
    "FAIL: RECOVERIX_FORCE_DEBOOTSTRAP=1 but linux_modules_extra_installed=no"

  if [[ -n "${RUNTIME_LAST_APT_INSTALL_FAILURE_REASON:-}" ]]; then
    runtime_kernel_package_report_line "$report" \
      "apt_install_failure_reason=${RUNTIME_LAST_APT_INSTALL_FAILURE_REASON}"
  else
    runtime_kernel_package_report_line "$report" \
      "apt_install_failure_reason=not_recorded"
  fi

  if declare -f runtime_debootstrap_mount >/dev/null 2>&1 && runtime_debootstrap_mount; then
    mounted=1
    runtime_kernel_package_log_apt_policy "$kver" "$report"
    runtime_kernel_package_log_dpkg_query_w "$kver" "$report"
    runtime_kernel_package_log_chroot_dpkg_list "$report"
    runtime_debootstrap_umount || true
  fi

  log_fail "FAIL: RECOVERIX_FORCE_DEBOOTSTRAP=1 but linux_modules_extra_installed=no"
  return 1
}

# Install linux-image + linux-modules-extra with full forensic logging.
runtime_install_kernel_packages() {
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  local report="${1:-}"
  local mounted=0
  local pkg img="linux-image-${kver}" extra="linux-modules-extra-${kver}"

  RUNTIME_INSTALL_KERNEL_PACKAGES_EXECUTED=1
  export RUNTIME_INSTALL_KERNEL_PACKAGES_EXECUTED
  RUNTIME_KERNEL_PACKAGES_INSTALL_REPORT="${report}"
  export RUNTIME_KERNEL_PACKAGES_INSTALL_REPORT

  runtime_apt_install_log_init
  log_step "runtime_install_kernel_packages: kernel ${kver}"
  runtime_kernel_package_report_line "$report" "kernel_install_function=runtime_install_kernel_packages"
  runtime_kernel_package_report_line "$report" "runtime_install_kernel_packages_executed=yes"

  runtime_kernel_package_log_host_target_kernel "$report"
  runtime_kernel_package_log_provision_context "$report"

  runtime_debootstrap_mount && mounted=1

  runtime_debootstrap_chroot 'DEBIAN_FRONTEND=noninteractive apt-get update' || {
    log_fail "FAIL: runtime_install_kernel_packages — apt-get update failed in rootfs"
    runtime_kernel_package_log_apt_policy "$kver" "$report"
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  }

  runtime_kernel_package_log_apt_policy "$kver" "$report"

  if recoverix_runtime_apt_install "$img"; then
    runtime_kernel_package_log_dpkg_query_w "$kver" "$report"
  else
    runtime_apt_report_install_failure "$img" "$report"
    log_fail "FAIL: runtime_install_kernel_packages (package ${img})"
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  fi

  if recoverix_runtime_apt_install "$extra"; then
    runtime_kernel_package_log_dpkg_query_w "$kver" "$report"
  else
    runtime_apt_report_install_failure "$extra" "$report"
    log_fail "FAIL: runtime_install_kernel_packages (package ${extra})"
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  fi

  runtime_debootstrap_chroot 'DEBIAN_FRONTEND=noninteractive apt-get clean' || true
  runtime_kernel_package_log_chroot_dpkg_list "$report"
  runtime_kernel_package_append_install_report "$kver" "$report"

  if declare -f runtime_verify_rootfs_amdgpu_kernel_module >/dev/null 2>&1; then
    if ! runtime_verify_rootfs_amdgpu_kernel_module "$report"; then
      log_fail "FAIL: runtime_install_kernel_packages — runtime_verify_rootfs_amdgpu_kernel_module failed"
      [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
      return 1
    fi
  fi

  [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
  log_pass "runtime_install_kernel_packages: ${kver} + linux-modules-extra OK"

  if declare -f runtime_log_kernel_artifact_forensic >/dev/null 2>&1; then
    runtime_log_kernel_artifact_forensic "kernel_install_post" "$report"
  fi

  if [[ "${RECOVERIX_FORCE_DEBOOTSTRAP:-0}" == "1" ]]; then
    runtime_kernel_package_assert_force_debootstrap_contract "$report" || return 1
  fi
  return 0
}
