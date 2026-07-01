#!/usr/bin/env bash
# Boot-critical package pattern matching and apt simulation REMV analysis.
# Kernel: only KEEP_KERNEL_FLAVOR packages are protected; old kernels may be purged.

BOOT_CRITICAL_FILE="${SCRIPT_DIR}/packages_boot_critical.txt"

keep_kernel_flavor() {
  if [[ -z "${KEEP_KERNEL_FLAVOR:-}" ]]; then
    die "KEEP_KERNEL_FLAVOR is not set in config.env"
  fi
  printf '%s' "$KEEP_KERNEL_FLAVOR"
}

# Exact dpkg names protected for the running/keep kernel flavor.
keep_kernel_protected_package_names() {
  local kver
  kver="$(keep_kernel_flavor)"
  printf '%s\n' \
    "linux-image-${kver}" \
    "linux-modules-${kver}" \
    "linux-modules-extra-${kver}" \
    "linux-headers-${kver}"
}

# True if package is in the keep-kernel protected set (exact name).
package_is_keep_kernel_protected() {
  local pkg="$1"
  local kver line
  kver="$(keep_kernel_flavor)"
  while read -r line; do
    [[ "$pkg" == "$line" ]] && return 0
  done < <(keep_kernel_protected_package_names)
  return 1
}

# True if package is any linux kernel family package (image/modules/headers/hwe).
package_is_kernel_family() {
  local pkg="$1"
  case "$pkg" in
    linux-image-*|linux-modules-*|linux-modules-extra-*|linux-headers-*|linux-hwe-*)
      return 0
      ;;
  esac
  return 1
}

_read_critical_patterns() {
  local f="${1:-$BOOT_CRITICAL_FILE}"
  while read -r line; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    echo "$line"
  done < "$f"
}

# Returns 0 if pkg must not be purged / must not appear in REMV.
package_matches_critical_pattern() {
  local pkg="$1"

  # Kernel family: protect only KEEP_KERNEL_FLAVOR packages; allow old kernel purge.
  if package_is_kernel_family "$pkg"; then
    if package_is_keep_kernel_protected "$pkg"; then
      return 0
    fi
    return 1
  fi

  local pat
  while read -r pat; do
    [[ -z "$pat" ]] && continue
    case "$pkg" in
      $pat) return 0 ;;
    esac
  done < <(_read_critical_patterns)
  return 1
}

# Block if any purge candidate matches critical patterns or keep kernel packages.
validate_purge_list_not_critical() {
  local report="$1"
  shift
  local pkg kver
  kver="$(keep_kernel_flavor)"
  for pkg in "$@"; do
    if package_is_keep_kernel_protected "$pkg"; then
      printf 'FAIL\tboot_critical_purge_list\t%s is protected (KEEP_KERNEL_FLAVOR=%s)\n' "$pkg" "$kver" >> "$report"
      die "BOOT CRITICAL: purge list contains keep kernel package: $pkg (KEEP_KERNEL_FLAVOR=${kver})"
    fi
    if package_matches_critical_pattern "$pkg"; then
      printf 'FAIL\tboot_critical_purge_list\t%s matches critical pattern\n' "$pkg" >> "$report"
      die "BOOT CRITICAL: purge list contains protected package/pattern: $pkg"
    fi
  done
  printf 'PASS\tboot_critical_purge_list\tno critical/keep-kernel packages in purge set (KEEP_KERNEL_FLAVOR=%s)\n' "$kver" >> "$report"
}

# Parse apt-get -s purge output for REMOVED packages; fail if any match critical.
validate_simulate_remv_not_critical() {
  local sim_log="$1"
  local report="$2"
  local removed=()
  local line pkg

  while IFS= read -r line; do
    if [[ "$line" =~ ^REMV[[:space:]]+(.+)$ ]]; then
      removed+=("${BASH_REMATCH[1]}")
      continue
    fi
    if [[ "$line" =~ ^[[:space:]]+([a-z0-9.+~-]+)[[:space:]]*$ ]] && [[ "$line" != *"following"* ]]; then
      pkg="${BASH_REMATCH[1]}"
      if host_dpkg_query -W -f='${Package}' "$pkg" &>/dev/null; then
        removed+=("$pkg")
      fi
    fi
  done < "$sim_log"

  local in_block=0
  while IFS= read -r line; do
    if [[ "$line" == *"packages will be REMOVED"* ]]; then
      in_block=1
      continue
    fi
    if [[ $in_block -eq 1 ]]; then
      [[ -z "$line" ]] && in_block=0 && continue
      pkg="$(echo "$line" | awk '{print $1}')"
      [[ -n "$pkg" ]] && removed+=("$pkg")
    fi
  done < "$sim_log"

  local hit=0 kver
  kver="$(keep_kernel_flavor)"
  for pkg in "${removed[@]}"; do
    [[ -z "$pkg" ]] && continue
    if package_matches_critical_pattern "$pkg"; then
      if package_is_keep_kernel_protected "$pkg"; then
        printf 'FAIL\tboot_critical_simulate\tREMV would remove keep kernel: %s (KEEP_KERNEL_FLAVOR=%s)\n' "$pkg" "$kver" >> "$report"
      else
        printf 'FAIL\tboot_critical_simulate\tREMV would remove: %s\n' "$pkg" >> "$report"
      fi
      hit=1
    fi
  done

  if [[ $hit -ne 0 ]]; then
    die "BOOT CRITICAL: apt purge simulation removes protected package(s) — see $report"
  fi
  printf 'PASS\tboot_critical_simulate\tno critical/keep-kernel REMV in simulation\n' >> "$report"
}

# Keep-kernel packages must remain installed; static patterns from boot_critical.txt.
validate_critical_packages_still_installed() {
  local report="$1"
  local pkg pat kver
  kver="$(keep_kernel_flavor)"

  while read -r pkg; do
    [[ -z "$pkg" ]] && continue
    if ! host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
      printf 'FAIL\tboot_critical_installed\tkeep kernel package missing: %s (KEEP_KERNEL_FLAVOR=%s)\n' "$pkg" "$kver" >> "$report"
      die "BOOT CRITICAL: keep kernel package not installed: $pkg"
    fi
  done < <(keep_kernel_protected_package_names)

  while read -r pat; do
    [[ -z "$pat" || "$pat" =~ ^# ]] && continue
    while read -r pkg; do
      [[ -z "$pkg" ]] && continue
      if package_is_kernel_family "$pkg"; then
        continue
      fi
      if ! host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
        printf 'WARN\tboot_critical_installed\tmissing: %s (pattern %s)\n' "$pkg" "$pat" >> "$report"
      fi
    done < <(host_dpkg_query -W -f='${Package}\n' 2>/dev/null | while read -r p; do
      case "$p" in $pat) echo "$p" ;; esac
    done)
  done < <(_read_critical_patterns)

  printf 'PASS\tboot_critical_installed\tkeep kernel + critical pattern scan complete (KEEP_KERNEL_FLAVOR=%s)\n' "$kver" >> "$report"
}

# Used by 02_generate_purge_plan: skip keep-kernel packages when building purge list.
filter_purge_skip_keep_kernel() {
  local pkg="$1"
  if package_is_keep_kernel_protected "$pkg"; then
    return 0
  fi
  return 1
}
