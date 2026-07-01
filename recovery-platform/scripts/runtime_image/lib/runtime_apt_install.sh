#!/usr/bin/env bash
# Strict minimal apt install policy for production debootstrap runtime rootfs.

: "${RECOVERIX_APT_INSTALL_RETRIES:=3}"
: "${RECOVERIX_APT_SIMULATE_BEFORE_INSTALL:=1}"
: "${RUNTIME_APT_INSTALL_LOG:=}"

# Enforce no recommends/suggests for all runtime rootfs apt operations.
runtime_apt_configure_strict_minimal_policy() {
  local root="${ROOTFS_RESOLVED:?}"
  local conf="${root}/etc/apt/apt.conf.d/99-recoverix-runtime-minimal"

  log_step "configure strict minimal apt policy (no recommends/suggests)"

  mkdir -p "${root}/etc/apt/apt.conf.d"
  cat >"$conf" <<'EOF'
# Recoverix production runtime — strict minimal dependency graph
APT::Install-Recommends "false";
APT::Install-Suggests "false";
APT::AutoRemove::RecommendsImportant "false";
EOF

  log_pass "apt strict policy: ${conf}"
  return 0
}

runtime_apt_install_log_init() {
  local ts
  [[ -n "${RUNTIME_APT_INSTALL_LOG:-}" && -f "${RUNTIME_APT_INSTALL_LOG}" ]] && return 0
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  RUNTIME_APT_INSTALL_LOG="${REPORT_DIR:-/recovery/build/reports/runtime-build}/runtime_apt_install_${ts}.log"
  mkdir -p "$(dirname "$RUNTIME_APT_INSTALL_LOG")"
  {
    echo "=== Recoverix runtime apt install log ==="
    echo "timestamp: ${ts}"
    echo "rootfs: ${ROOTFS_RESOLVED:-}"
    echo "policy: --no-install-recommends --no-install-suggests"
    echo
  } >"$RUNTIME_APT_INSTALL_LOG"
  export RUNTIME_APT_INSTALL_LOG
}

runtime_apt_install_log_append() {
  local msg="$1"
  [[ -n "${RUNTIME_APT_INSTALL_LOG:-}" ]] || return 0
  printf '%s\n' "$msg" >>"$RUNTIME_APT_INSTALL_LOG"
}

# Execute shell snippet in runtime rootfs (requires chroot mounts).
runtime_rootfs_chroot_exec() {
  local cmd="${1:?}"
  local env_prefix=""

  if [[ "${RECOVERIX_CHROOT_RUNTIME_USER_ENV:-0}" == "1" ]] && \
     declare -f runtime_recoverix_chroot_env_prefix >/dev/null 2>&1; then
    env_prefix="$(runtime_recoverix_chroot_env_prefix)"
    if [[ "$cmd" == *openbox* ]] && declare -f runtime_log_openbox_chroot_env_forensic >/dev/null 2>&1; then
      runtime_log_openbox_chroot_env_forensic "$cmd"
      runtime_ensure_recoverix_openbox_cache_dir
    fi
  fi

  if declare -f runtime_debootstrap_chroot >/dev/null 2>&1; then
    runtime_debootstrap_chroot "${env_prefix}${cmd}"
  else
    chroot "${ROOTFS_RESOLVED:?}" /bin/bash -c "${env_prefix}${cmd}"
  fi

  if declare -f runtime_guard_host_home_after_chroot >/dev/null 2>&1; then
    runtime_guard_host_home_after_chroot "${FUNCNAME[1]:-runtime_rootfs_chroot_exec}"
  fi
}

runtime_apt_simulate_allowlist_has() {
  local allow_file="${1:?}" name="${2:?}"
  local line
  [[ -f "$allow_file" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" == "$name" ]]; then
      return 0
    fi
  done <"$allow_file"
  return 1
}

runtime_desktop_environment_forbidden_file() {
  local base
  if declare -f runtime_image_dir >/dev/null 2>&1; then
    base="$(runtime_image_dir)"
  else
    base="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  fi
  printf '%s/assets/runtime_desktop_environment_forbidden.txt' "$base"
}

# Return 0 if simulate would Inst a forbidden package from exclude_file.
# allow_file: optional DM dependency allowlist (gdm3 chain).
# exclude_file: defaults to runtime_exclude_packages.txt; gdm3 uses desktop_environment_forbidden only.
runtime_apt_simulate_introduces_excluded() {
  local pkg="${1:?}"
  local allow_file="${2:-}"
  local exclude_file="${3:-}"
  local sim ex apt_opts

  [[ "${RECOVERIX_APT_SIMULATE_BEFORE_INSTALL:-1}" == "1" ]] || return 1

  if [[ -z "$exclude_file" ]]; then
    exclude_file="$(runtime_debootstrap_exclude_file 2>/dev/null || echo /dev/null)"
  fi
  [[ -f "$exclude_file" ]] || return 1

  apt_opts="-o APT::Install-Recommends=false -o APT::Install-Suggests=false"
  sim="$(runtime_rootfs_chroot_exec \
    "DEBIAN_FRONTEND=noninteractive apt-get ${apt_opts} install -sy --no-install-recommends --no-install-suggests '${pkg}'" 2>&1)" || true

  while IFS= read -r ex; do
    [[ -z "$ex" || "$ex" =~ ^# ]] && continue
    if [[ -n "$allow_file" ]] && runtime_apt_simulate_allowlist_has "$allow_file" "$ex"; then
      continue
    fi
    if printf '%s\n' "$sim" | grep -qE "(^|[[:space:]]|,)Inst[[:space:]]+${ex}([[:space:]]|,|$)"; then
      log_fail "FAIL: simulate: installing '${pkg}' would Inst forbidden desktop package: ${ex}"
      runtime_apt_install_log_append "SIMULATE FAIL: pkg=${pkg} forbidden=${ex}"
      printf '%s\n' "$sim" >>"${RUNTIME_APT_INSTALL_LOG:-/dev/null}" 2>/dev/null || true
      return 0
    fi
    if [[ "$ex" == *"*"* ]]; then
      local prefix="${ex%\*}"
      if [[ -n "$allow_file" ]]; then
        local line
        while IFS= read -r line || [[ -n "$line" ]]; do
          [[ -z "$line" || "$line" =~ ^# ]] && continue
          [[ "$line" == "${prefix}"* ]] && continue 2
        done <"$allow_file"
      fi
      if printf '%s\n' "$sim" | grep -qE "(^|[[:space:]]|,)Inst[[:space:]]+${prefix}"; then
        log_fail "FAIL: simulate: installing '${pkg}' would Inst forbidden pattern: ${ex}"
        runtime_apt_install_log_append "SIMULATE FAIL: pkg=${pkg} forbidden_pattern=${ex}"
        printf '%s\n' "$sim" >>"${RUNTIME_APT_INSTALL_LOG:-/dev/null}" 2>/dev/null || true
        return 0
      fi
    fi
  done <"$exclude_file"

  return 1
}

runtime_apt_gdm3_allowlist_file() {
  local base
  if declare -f runtime_image_dir >/dev/null 2>&1; then
    base="$(runtime_image_dir)"
  else
    base="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  fi
  printf '%s/assets/runtime_gdm3_allowed_dependencies.txt' "$base"
}

# Scan rootfs for forbidden desktop metapackages. Returns 0 if none found, 1 if any found.
runtime_rootfs_forbidden_desktop_metapackages_present() {
  local root="${1:?}"
  local pkg line found=0

  while IFS= read -r pkg; do
    [[ -z "$pkg" || "$pkg" =~ ^# ]] && continue
    if [[ "$pkg" == *"*"* ]]; then
      local prefix="${pkg%\*}"
      while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        if runtime_rootfs_dpkg_installed "$root" "$line"; then
          log_fail "FAIL: forbidden desktop metapackage installed: ${line} (pattern ${pkg})"
          found=1
        fi
      done < <(dpkg-query --root="$root" -W -f='${Package}\n' 2>/dev/null | grep -E "^${prefix}" || true)
      continue
    fi
    if runtime_rootfs_dpkg_installed "$root" "$pkg"; then
      log_fail "FAIL: forbidden desktop metapackage installed: ${pkg}"
      found=1
    fi
  done <"$(runtime_desktop_environment_forbidden_file)"

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    if runtime_rootfs_dpkg_installed "$root" "$line"; then
      log_fail "FAIL: forbidden ubuntu-desktop family installed: ${line}"
      found=1
    fi
  done < <(dpkg-query --root="$root" -W -f='${Package}\n' 2>/dev/null | grep -E '^ubuntu-desktop' || true)

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    log_fail "FAIL: forbidden *-desktop metapackage installed: ${line}"
    found=1
  done < <(dpkg-query --root="$root" -W -f='${Package}\n' 2>/dev/null | grep -E '\-desktop$' || true)

  # Return 0 when any forbidden desktop metapackage is installed.
  [[ $found -gt 0 ]]
}

# Forensic: openbox must be installed before gdm3 (Provides x-session-manager / x-window-manager).
runtime_gdm3_log_openbox_provider_forensic() {
  local dpkg_out provides_out

  log "=== gdm3 pre-install: openbox session provider forensic ==="
  runtime_apt_install_log_append "=== gdm3 pre-install: openbox session provider forensic ==="

  dpkg_out="$(runtime_rootfs_chroot_exec "dpkg -l openbox 2>/dev/null || echo '(dpkg -l openbox failed)'" 2>&1 || true)"
  log "$dpkg_out"
  runtime_apt_install_log_append "$dpkg_out"

  provides_out="$(runtime_rootfs_chroot_exec \
    "apt-cache show openbox 2>/dev/null | grep -E '^Package:|^Provides:' || echo '(apt-cache show openbox Provides missing)'" 2>&1 || true)"
  log "$provides_out"
  runtime_apt_install_log_append "$provides_out"

  if printf '%s\n' "$provides_out" | grep -q 'x-session-manager'; then
    log_pass "openbox Provides: x-session-manager"
    runtime_apt_install_log_append "openbox_provides_x_session_manager=yes"
  else
    log_fail "openbox does not Provide x-session-manager in apt-cache"
    runtime_apt_install_log_append "openbox_provides_x_session_manager=no"
  fi

  if printf '%s\n' "$provides_out" | grep -q 'x-window-manager'; then
    log_pass "openbox Provides: x-window-manager"
    runtime_apt_install_log_append "openbox_provides_x_window_manager=yes"
  else
    log_warn "openbox does not list x-window-manager in Provides"
    runtime_apt_install_log_append "openbox_provides_x_window_manager=no"
  fi
}

runtime_gdm3_record_simulate_ubuntu_session() {
  local sim="${1:-}"

  if printf '%s\n' "$sim" | grep -qE '(^|[[:space:]]|,)Inst[[:space:]]+ubuntu-session([[:space:]]|,|$)'; then
    RUNTIME_GDM3_SIMULATE_UBUNTU_SESSION="yes"
    runtime_apt_install_log_append "gdm3_simulate_ubuntu_session=yes"
    return 0
  fi
  RUNTIME_GDM3_SIMULATE_UBUNTU_SESSION="no"
  runtime_apt_install_log_append "gdm3_simulate_ubuntu_session=no"
  return 1
}

runtime_gdm3_append_install_strategy_report() {
  RUNTIME_GDM3_INSTALL_STRATEGY="${RUNTIME_GDM3_INSTALL_STRATEGY:-openbox_provider}"
  RUNTIME_GDM3_DEPENDENCY_OR_RESOLVED_BY="${RUNTIME_GDM3_DEPENDENCY_OR_RESOLVED_BY:-x-session-manager}"
  export RUNTIME_GDM3_INSTALL_STRATEGY RUNTIME_GDM3_DEPENDENCY_OR_RESOLVED_BY RUNTIME_GDM3_SIMULATE_UBUNTU_SESSION

  runtime_apt_install_log_append "gdm3_install_strategy=${RUNTIME_GDM3_INSTALL_STRATEGY}"
  runtime_apt_install_log_append "gdm3_dependency_or_resolved_by=${RUNTIME_GDM3_DEPENDENCY_OR_RESOLVED_BY}"
  runtime_apt_install_log_append "gdm3_simulate_ubuntu_session=${RUNTIME_GDM3_SIMULATE_UBUNTU_SESSION:-unknown}"
  log "gdm3_install_strategy=${RUNTIME_GDM3_INSTALL_STRATEGY}"
  log "gdm3_dependency_or_resolved_by=${RUNTIME_GDM3_DEPENDENCY_OR_RESOLVED_BY}"
  log "gdm3_simulate_ubuntu_session=${RUNTIME_GDM3_SIMULATE_UBUNTU_SESSION:-unknown}"
}

# Install gdm3 on jammy; simulate blocks desktop metapackages only (DM chain allowlisted).
# Requires openbox installed first (runtime_install_gui_stack_packages order).
recoverix_runtime_apt_install_gdm3() {
  local pkg="gdm3"
  local attempt=1 max="${RECOVERIX_APT_INSTALL_RETRIES:-3}"
  local allow_file desktop_forbidden out rc sim

  allow_file="$(runtime_apt_gdm3_allowlist_file)"
  desktop_forbidden="$(runtime_desktop_environment_forbidden_file)"
  [[ -f "$allow_file" ]] || {
    log_fail "missing gdm3 allowlist: ${allow_file}"
    return 1
  }
  [[ -f "$desktop_forbidden" ]] || {
    log_fail "missing desktop forbidden list: ${desktop_forbidden}"
    return 1
  }

  if ! runtime_rootfs_dpkg_installed "${ROOTFS_RESOLVED:?}" openbox; then
    log_fail "FAIL: gdm3 install requires openbox first (Provides x-session-manager for Depends OR)"
    return 1
  fi

  RUNTIME_GDM3_INSTALL_STRATEGY="openbox_provider"
  RUNTIME_GDM3_DEPENDENCY_OR_RESOLVED_BY="x-session-manager"
  export RUNTIME_GDM3_INSTALL_STRATEGY RUNTIME_GDM3_DEPENDENCY_OR_RESOLVED_BY

  runtime_apt_configure_strict_minimal_policy
  runtime_apt_install_log_init

  runtime_gdm3_log_openbox_provider_forensic

  while [[ $attempt -le $max ]]; do
    log_step "apt install: ${pkg} (gdm3 minimal, no recommends; attempt ${attempt}/${max})"

    apt_opts="-o APT::Install-Recommends=false -o APT::Install-Suggests=false"
    sim="$(runtime_rootfs_chroot_exec \
      "DEBIAN_FRONTEND=noninteractive apt-get ${apt_opts} install -sy --no-install-recommends --no-install-suggests '${pkg}'" 2>&1)" || true
    runtime_apt_install_log_append "=== gdm3 simulate (pre-policy-check) attempt=${attempt} ==="
    printf '%s\n' "$sim" >>"${RUNTIME_APT_INSTALL_LOG:-/dev/null}" 2>/dev/null || true

    if runtime_gdm3_record_simulate_ubuntu_session "$sim"; then
      log_fail "FAIL: gdm3 simulate still Inst ubuntu-session — install openbox before gdm3"
      runtime_gdm3_append_install_strategy_report
      return 1
    fi

    if runtime_apt_simulate_introduces_excluded "$pkg" "$allow_file" "$desktop_forbidden"; then
      log_fail "FAIL: gdm3 simulate would install a desktop metapackage — see ${RUNTIME_APT_INSTALL_LOG}"
      log_fail "hint: DM chain allowed (${allow_file}); metapackages only in ${desktop_forbidden}"
      return 1
    fi

    set +e
    out="$(runtime_rootfs_chroot_exec \
      "DEBIAN_FRONTEND=noninteractive apt-get -o APT::Install-Recommends=false -o APT::Install-Suggests=false install -y --no-install-recommends --no-install-suggests '${pkg}'" 2>&1)"
    rc=$?
    set -e

    runtime_apt_install_log_append "=== install gdm3 attempt=${attempt} rc=${rc} ==="
    printf '%s\n' "$out" >>"${RUNTIME_APT_INSTALL_LOG:-/dev/null}" 2>/dev/null || true

    if [[ $rc -eq 0 ]]; then
      log_pass "apt install OK: gdm3 (login manager; openbox session policy unchanged)"
      runtime_gdm3_append_install_strategy_report
      return 0
    fi

    log_warn "WARN: gdm3 install failed (rc=${rc}, attempt ${attempt}/${max})"
    printf '%s\n' "$out" >&2
    attempt=$((attempt + 1))
    [[ $attempt -le $max ]] && sleep 2
  done

  log_fail "FAIL: gdm3 install failed after ${max} attempts"
  return 1
}

runtime_rootfs_dpkg_installed() {
  local root="${1:?}" pkg="${2:?}"
  dpkg-query --root="$root" -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'
}

# Jammy: mutter metapackage may be absent when mutter-common satisfies gnome-shell.
runtime_gui_capability_mutter_provider() {
  local root="${1:?}"
  if runtime_rootfs_dpkg_installed "$root" mutter; then
    printf 'mutter'
    return 0
  fi
  if runtime_rootfs_dpkg_installed "$root" mutter-common; then
    printf 'mutter-common'
    return 0
  fi
  return 1
}

# Runtime-capability validation for Recoverix GUI boot (not exact package identity).
# Returns 0 when fail_count=0; WARN does not affect return code.
# shellcheck disable=SC2120
runtime_gui_verify_boot_capabilities() {
  local root="${1:-${ROOTFS_RESOLVED:?}}"
  local pass_count=0 warn_count=0 fail_count=0
  local provider user accounts conf default_target gdm_unit

  log "=== verify Recoverix Runtime GUI boot capabilities (dpkg -l) ==="

  log "--- display-manager dependency chain (allowed) ---"
  if runtime_rootfs_dpkg_installed "$root" gdm3; then
    log_pass "PASS: gdm3"
    pass_count=$((pass_count + 1))
  else
    log_fail "FAIL: gdm3 missing (display manager required)"
    fail_count=$((fail_count + 1))
  fi

  if runtime_rootfs_dpkg_installed "$root" gnome-shell; then
    log_pass "PASS: gnome-shell"
    pass_count=$((pass_count + 1))
  else
    log_fail "FAIL: gnome-shell missing (gdm3 dependency chain)"
    fail_count=$((fail_count + 1))
  fi

  if runtime_rootfs_dpkg_installed "$root" gnome-session-bin; then
    log_pass "PASS: gnome-session-bin"
    pass_count=$((pass_count + 1))
  elif runtime_rootfs_dpkg_installed "$root" gnome-session-common; then
    log_pass "PASS: gnome-session runtime dependency satisfied (gnome-session-common)"
    pass_count=$((pass_count + 1))
  else
    log_fail "FAIL: gnome-session runtime dependency missing (gnome-session-bin or gnome-session-common)"
    fail_count=$((fail_count + 1))
  fi

  provider="$(runtime_gui_capability_mutter_provider "$root" 2>/dev/null || true)"
  if [[ -n "$provider" ]]; then
    log_pass "PASS: mutter runtime dependency satisfied (${provider})"
    pass_count=$((pass_count + 1))
  else
    log_fail "FAIL: mutter runtime dependency missing (mutter or mutter-common)"
    fail_count=$((fail_count + 1))
  fi

  if runtime_rootfs_dpkg_installed "$root" evolution-data-server; then
    log_pass "PASS: evolution-data-server (optional DM chain)"
    pass_count=$((pass_count + 1))
  else
    log_warn "WARN: evolution-data-server absent (optional jammy variation)"
    warn_count=$((warn_count + 1))
  fi

  log "--- forbidden desktop environment ---"
  if runtime_rootfs_forbidden_desktop_metapackages_present "$root"; then
    fail_count=$((fail_count + 1))
  else
    log_pass "PASS: ubuntu-desktop absent"
    pass_count=$((pass_count + 1))
    log_pass "PASS: ubuntu-session absent"
    pass_count=$((pass_count + 1))
    log_pass "PASS: task-desktop absent"
    pass_count=$((pass_count + 1))
    log_pass "PASS: snapd absent"
    pass_count=$((pass_count + 1))
  fi

  log "--- session ---"
  if [[ -x "${root}/usr/bin/openbox" && -f "${root}/usr/share/xsessions/openbox.desktop" ]]; then
    log_pass "PASS: openbox xsession"
    pass_count=$((pass_count + 1))
  else
    log_fail "FAIL: openbox xsession missing (GUI boot capability)"
    fail_count=$((fail_count + 1))
  fi

  user="${RECOVERIX_RUNTIME_USER:-recoverix}"
  if declare -f runtime_run_accountsservice_validation >/dev/null 2>&1; then
    if [[ -z "${ROOTFS_RESOLVED:-}" ]]; then
      ROOTFS_RESOLVED="$root"
      export ROOTFS_RESOLVED
    elif [[ "${ROOTFS_RESOLVED%/}" != "${root%/}" ]]; then
      log_warn "WARN: ROOTFS_RESOLVED (${ROOTFS_RESOLVED}) != verify root (${root}); using ROOTFS_RESOLVED for AccountsService"
    fi
    if runtime_run_accountsservice_validation "$user"; then
      pass_count=$((pass_count + 1))
    else
      fail_count=$((fail_count + 1))
    fi
  elif declare -f runtime_verify_accountsservice_user_file >/dev/null 2>&1; then
    local acc_rc=0
    if [[ -z "${ROOTFS_RESOLVED:-}" ]]; then
      ROOTFS_RESOLVED="$root"
      export ROOTFS_RESOLVED
    fi
    set +e
    runtime_verify_accountsservice_user_file "$user"
    acc_rc=$?
    set -e
    log "accountsservice_validation_rc=${acc_rc}"
    if [[ $acc_rc -eq 0 ]]; then
      pass_count=$((pass_count + 1))
    else
      fail_count=$((fail_count + 1))
    fi
  else
    accounts="${root}/var/lib/AccountsService/users/${user}"
    if [[ -f "$accounts" ]] && grep -q '^Session=openbox' "$accounts"; then
      log_pass "PASS: AccountsService Session=openbox"
      pass_count=$((pass_count + 1))
    else
      log_fail "FAIL: AccountsService user file missing in rootfs"
      fail_count=$((fail_count + 1))
    fi
  fi

  if [[ $fail_count -eq 0 ]]; then
    default_target="$(chroot "$root" systemctl get-default 2>/dev/null | tr -d '\r\n' || true)"
    if [[ "$default_target" == "graphical.target" ]]; then
      log_pass "PASS: graphical.target configured"
      pass_count=$((pass_count + 1))
    else
      log_fail "FAIL: graphical.target not default (got ${default_target:-unknown})"
      fail_count=$((fail_count + 1))
    fi
    conf="${root}/etc/gdm3/custom.conf"
    if [[ -f "$conf" ]] && grep -q '^AutomaticLoginEnable=True' "$conf" 2>/dev/null && \
       grep -q "^AutomaticLogin=${user}" "$conf" 2>/dev/null; then
      log_pass "PASS: gdm autologin configured"
      pass_count=$((pass_count + 1))
    elif [[ "${RECOVERIX_RUNTIME_AUTOLOGIN:-1}" != "1" ]]; then
      log_pass "PASS: gdm autologin disabled by policy"
      pass_count=$((pass_count + 1))
    else
      log_fail "FAIL: gdm autologin not configured for ${user}"
      fail_count=$((fail_count + 1))
    fi
    if [[ -x "${root}/usr/bin/openbox" && -f "${root}/usr/share/xsessions/openbox.desktop" ]]; then
      log_pass "PASS: openbox session configured"
      pass_count=$((pass_count + 1))
    fi
    gdm_unit="$(test -f "${root}/lib/systemd/system/gdm.service" && echo gdm.service || true)"
    [[ -z "$gdm_unit" && -f "${root}/lib/systemd/system/gdm3.service" ]] && gdm_unit="gdm3.service"
    [[ -n "$gdm_unit" ]] && log_info "gdm unit: ${gdm_unit}"
  fi

  if [[ $fail_count -eq 0 ]]; then
    log_pass "PASS: runtime GUI stack validated"
  fi

  log "--- GUI capability summary ---"
  log "PASS=${pass_count}"
  log "WARN=${warn_count}"
  log "FAIL=${fail_count}"

  if [[ $fail_count -gt 0 ]]; then
    return 1
  fi
  return 0
}

runtime_apt_verify_gui_stack_dpkg_policy() {
  runtime_gui_verify_boot_capabilities "${ROOTFS_RESOLVED:?}"
}

# recoverix_runtime_apt_install PKG — one package, strict policy, retries.
recoverix_runtime_apt_install() {
  local pkg="${1:?}"
  local attempt=1 max="${RECOVERIX_APT_INSTALL_RETRIES:-3}"
  local out rc

  while [[ $attempt -le $max ]]; do
    log_step "apt install: ${pkg} (attempt ${attempt}/${max}, strict minimal)"

    if runtime_apt_simulate_introduces_excluded "$pkg"; then
      RUNTIME_LAST_APT_INSTALL_OUTPUT="simulate: refusing install of ${pkg} — would violate runtime purity policy"
      log_fail "FAIL: refusing install of ${pkg} — would violate runtime purity policy"
      if declare -f runtime_apt_classify_install_failure >/dev/null 2>&1; then
        runtime_apt_classify_install_failure "$pkg" "$RUNTIME_LAST_APT_INSTALL_OUTPUT" ""
      fi
      return 1
    fi

    set +e
    out="$(runtime_rootfs_chroot_exec \
      "DEBIAN_FRONTEND=noninteractive apt-get -o APT::Install-Recommends=false -o APT::Install-Suggests=false install -y --no-install-recommends --no-install-suggests '${pkg}'" 2>&1)"
    rc=$?
    set -e

    runtime_apt_install_log_append "=== install ${pkg} attempt=${attempt} rc=${rc} ==="
    printf '%s\n' "$out" >>"${RUNTIME_APT_INSTALL_LOG:-/dev/null}" 2>/dev/null || true

    if [[ $rc -eq 0 ]]; then
      RUNTIME_LAST_APT_INSTALL_OUTPUT=""
      log_pass "apt install OK: ${pkg}"
      return 0
    fi

    RUNTIME_LAST_APT_INSTALL_OUTPUT="$out"
    log_warn "WARN: apt install failed for ${pkg} (rc=${rc}, attempt ${attempt}/${max})"
    printf '%s\n' "$out" >&2
    if declare -f runtime_apt_classify_install_failure >/dev/null 2>&1; then
      runtime_apt_classify_install_failure "$pkg" "$out" ""
    fi
    attempt=$((attempt + 1))
    [[ $attempt -le $max ]] && sleep 2
  done

  log_fail "FAIL: recoverix_runtime_apt_install failed after ${max} attempts: ${pkg}"
  log_fail "FAIL: see install log: ${RUNTIME_APT_INSTALL_LOG:-n/a}"
  if declare -f runtime_apt_classify_install_failure >/dev/null 2>&1; then
    runtime_apt_classify_install_failure "$pkg" "${RUNTIME_LAST_APT_INSTALL_OUTPUT:-}" ""
  fi
  return 1
}

# Deterministic sorted one-by-one install (avoids bulk apt resolver desktop chains).
recoverix_runtime_apt_install_packages() {
  local -a pkgs=("$@")
  local -a sorted=()
  local p

  if [[ "${#pkgs[@]}" -eq 0 ]]; then
    log_fail "FAIL: recoverix_runtime_apt_install_packages: empty package list"
    return 1
  fi

  mapfile -t sorted < <(printf '%s\n' "${pkgs[@]}" | LC_ALL=C sort -u)
  log_step "strict minimal apt install (${#sorted[@]} packages, deterministic order)"
  runtime_apt_install_log_init

  for p in "${sorted[@]}"; do
    recoverix_runtime_apt_install "$p" || return 1
  done

  log_pass "strict minimal apt install complete (${#sorted[@]} packages)"
  log_info "install log: ${RUNTIME_APT_INSTALL_LOG}"
  return 0
}
