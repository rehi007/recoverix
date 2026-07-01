#!/usr/bin/env bash
# Release-aware apt package validation and runtime filesystem binary checks.

: "${RUNTIME_DEBOOTSTRAP_SUITE:=jammy}"

runtime_package_release_map_file() {
  if [[ -n "${RUNTIME_IMAGE_DIR:-}" ]]; then
    printf '%s/assets/runtime_package_release_map.txt' "${RUNTIME_IMAGE_DIR}"
    return 0
  fi
  printf '%s' "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/assets/runtime_package_release_map.txt"
}

# Echo replacement package for obsolete pkg on suite, or empty.
runtime_obsolete_package_replacement() {
  local suite="${1:?}"
  local pkg="${2:?}"
  local map_file obsolete replacement

  map_file="$(runtime_package_release_map_file)"
  [[ -f "$map_file" ]] || return 1

  while read -r obsolete replacement _; do
    [[ -z "${obsolete:-}" || "$obsolete" =~ ^# ]] && continue
    if [[ "$obsolete" == "$pkg" ]]; then
      printf '%s' "$replacement"
      return 0
    fi
  done < <(awk -v s="$suite" '$1 == s && $2 != "" { print $2, $3 }' "$map_file" 2>/dev/null)

  return 1
}

# apt-cache show on build host.
runtime_apt_cache_package_available_host() {
  local pkg="${1:?}"
  apt-cache show "$pkg" >/dev/null 2>&1
}

# apt-cache show inside debootstrap rootfs (requires chroot mounts + configured repos).
runtime_apt_cache_package_available_rootfs() {
  local pkg="${1:?}"
  runtime_debootstrap_chroot "apt-cache show '${pkg}'" >/dev/null 2>&1
}

runtime_apt_cache_package_available() {
  if [[ "${RECOVERIX_VALIDATE_PACKAGES_IN_ROOTFS:-0}" == "1" ]]; then
    runtime_apt_cache_package_available_rootfs "$1"
  else
    runtime_apt_cache_package_available_host "$1"
  fi
}

# recoverix_validate_runtime_package PKG [required|optional]
# Exit 0: install PKG
# Exit 2: skip obsolete PKG (replacement suggested / validated separately)
# Exit 3: optional PKG missing — skip install
# Exit 1: required PKG missing / validation failed
recoverix_validate_runtime_package() {
  local pkg="${1:?}"
  local tier="${2:-required}"
  local suite="${RUNTIME_DEBOOTSTRAP_SUITE:-jammy}"
  local replacement=""

  log_step "package validation: ${pkg} (${tier}, suite=${suite})"

  if replacement="$(runtime_obsolete_package_replacement "$suite" "$pkg" 2>/dev/null)"; then
    log_warn "WARN: obsolete package on ${suite}: ${pkg}"
    log_info "skipped obsolete package: ${pkg}"
    log_info "replacement suggestion: ${replacement}"
    if runtime_apt_cache_package_available "$replacement"; then
      log_pass "replacement package available in apt: ${replacement}"
    else
      log_fail "FAIL: replacement package not in apt cache: ${replacement}"
      [[ "$tier" == "required" ]] && return 1
      return 3
    fi
    return 2
  fi

  if runtime_apt_cache_package_available "$pkg"; then
    log_pass "package install candidate: ${pkg} (apt-cache show OK)"
    [[ "$tier" == "optional" ]] && log_info "optional package present: ${pkg}"
    if [[ "${RECOVERIX_VALIDATE_PACKAGES_IN_ROOTFS:-0}" == "1" ]]; then
      local req_comp
      req_comp="$(runtime_package_required_apt_component "$suite" "$pkg" 2>/dev/null || true)"
      [[ -n "$req_comp" ]] && log_info "${pkg} requires apt component: ${req_comp} (enabled in sources.list)"
    fi
    return 0
  fi

  if [[ "$tier" == "optional" ]]; then
    log_warn "WARN: optional package not in apt cache (skipping): ${pkg}"
    return 3
  fi

  log_fail "FAIL: required package not in apt cache: ${pkg}"
  if [[ "${RECOVERIX_VALIDATE_PACKAGES_IN_ROOTFS:-0}" == "1" ]]; then
    log_fail "FAIL: validate rootfs /etc/apt/sources.list includes required components (universe for partclone)"
    runtime_debootstrap_chroot_apt_cache_policy "$pkg" >&2 || true
  else
    log_info "hint: host apt may differ from rootfs — package resolution runs in rootfs after repository setup"
  fi
  return 1
}

# Read seed file; validate; resolve obsolete → replacement; dedupe install list.
# Sets global array RUNTIME_DEBOOTSTRAP_INSTALL_PKGS.
runtime_resolve_debootstrap_install_packages() {
  local pkg_file="${1:?}"
  local -a raw=() resolved=() seen=()
  local pkg rc replacement tier failures=0

  RUNTIME_DEBOOTSTRAP_INSTALL_PKGS=()

  mapfile -t raw < <(grep -v '^[[:space:]]*#' "$pkg_file" | grep -v '^[[:space:]]*$' || true)
  if [[ "${#raw[@]}" -eq 0 ]]; then
    log_fail "FAIL: empty debootstrap package list: ${pkg_file}"
    return 1
  fi

  log_step "resolve debootstrap package list (${#raw[@]} entries from seed file)"

  _rt_pkg_seen() {
    local p="$1" x
    for x in "${seen[@]}"; do
      [[ "$x" == "$p" ]] && return 0
    done
    return 1
  }

  _rt_pkg_add() {
    local p="$1"
    _rt_pkg_seen "$p" && return 0
    seen+=("$p")
    resolved+=("$p")
    return 0
  }

  for pkg in "${raw[@]}"; do
    tier="required"
    if [[ "$pkg" == optional:* ]]; then
      tier="optional"
      pkg="${pkg#optional:}"
    fi

    if declare -f runtime_debootstrap_package_allowed >/dev/null 2>&1 && \
       ! runtime_debootstrap_package_allowed "$pkg"; then
      log_info "GUI-disabled seed omission: ${pkg}"
      continue
    fi

    recoverix_validate_runtime_package "$pkg" "$tier"
    rc=$?
    case "$rc" in
      0)
        _rt_pkg_add "$pkg"
        ;;
      2)
        replacement="$(runtime_obsolete_package_replacement "${RUNTIME_DEBOOTSTRAP_SUITE:-jammy}" "$pkg")"
        if [[ -n "$replacement" ]]; then
          recoverix_validate_runtime_package "$replacement" "required" || failures=$((failures + 1))
          _rt_pkg_add "$replacement"
        fi
        ;;
      3)
        log_info "optional package omitted from install list: ${pkg}"
        ;;
      *)
        failures=$((failures + 1))
        ;;
    esac
  done

  if [[ $failures -gt 0 ]]; then
    log_fail "FAIL: package list resolution failed (${failures} error(s))"
    return 1
  fi

  RUNTIME_DEBOOTSTRAP_INSTALL_PKGS=("${resolved[@]}")
  log_pass "resolved install package count: ${#RUNTIME_DEBOOTSTRAP_INSTALL_PKGS[@]}"
  log_info "install packages: ${RUNTIME_DEBOOTSTRAP_INSTALL_PKGS[*]}"
  return 0
}

runtime_rootfs_path_executable() {
  local root="${1:?}" relpath="${2:?}"
  [[ -e "${root}/${relpath}" ]] && [[ -x "${root}/${relpath}" ]]
}

# Required NTFS/partclone binaries for Recoverix backup runtime.
runtime_assert_runtime_filesystem_binaries() {
  local root="${1:-${ROOTFS_RESOLVED:?}}"
  local failures=0

  log_step "verify runtime filesystem binaries (ntfs-3g / mount.ntfs / partclone.ntfs)"

  if runtime_rootfs_path_executable "$root" "usr/bin/ntfs-3g"; then
    log_pass "usr/bin/ntfs-3g"
  else
    log_fail "FAIL: missing or not executable: ${root}/usr/bin/ntfs-3g"
    failures=$((failures + 1))
  fi

  if runtime_rootfs_path_executable "$root" "usr/sbin/mount.ntfs"; then
    log_pass "usr/sbin/mount.ntfs"
  else
    log_fail "FAIL: missing or not executable: ${root}/usr/sbin/mount.ntfs (install ntfs-3g)"
    failures=$((failures + 1))
  fi

  if runtime_rootfs_path_executable "$root" "usr/bin/partclone.ntfs"; then
    log_pass "usr/bin/partclone.ntfs"
  elif runtime_rootfs_path_executable "$root" "usr/sbin/partclone.ntfs"; then
    log_pass "usr/sbin/partclone.ntfs (Debian path; policy alias usr/bin/partclone.ntfs)"
    log_info "note: partclone.ntfs is at usr/sbin on Ubuntu jammy; install partclone package"
  else
    log_fail "FAIL: missing partclone.ntfs (expected usr/bin or usr/sbin under ${root})"
    failures=$((failures + 1))
  fi

  [[ $failures -eq 0 ]] || return 1
  log_pass "runtime filesystem binaries OK"
  return 0
}
