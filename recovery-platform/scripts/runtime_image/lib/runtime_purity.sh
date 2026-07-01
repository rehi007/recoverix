#!/usr/bin/env bash
# Runtime rootfs purity: excluded desktop/bloat packages and dependency tracing.

runtime_image_assets_dir() {
  if [[ -n "${RUNTIME_IMAGE_DIR:-}" ]]; then
    printf '%s/assets' "${RUNTIME_IMAGE_DIR}"
    return 0
  fi
  printf '%s' "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/assets"
}

runtime_debootstrap_exclude_file() {
  printf '%s/runtime_exclude_packages.txt' "$(runtime_image_assets_dir)"
}

runtime_gdm3_dependency_allowlist_file() {
  printf '%s/runtime_gdm3_allowed_dependencies.txt' "$(runtime_image_assets_dir)"
}

runtime_purity_is_allowed_display_manager_dependency() {
  local pkg="${1:?}"
  local allow_file
  allow_file="$(runtime_gdm3_dependency_allowlist_file)"
  [[ -f "$allow_file" ]] || return 1
  runtime_purity_allowlist_has "$allow_file" "$pkg"
}

runtime_purity_allowlist_has() {
  local allow_file="${1:?}" name="${2:?}"
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == "$name" ]] && return 0
  done <"$allow_file"
  return 1
}

# List installed package names matching an exclude entry (supports libreoffice* prefix).
runtime_purity_installed_matching_exclude() {
  local pattern="${1:?}"
  local prefix

  if [[ "$pattern" == *"*"* ]]; then
    prefix="${pattern%\*}"
    host_dpkg_query -W -f='${Package}\n' 2>/dev/null | grep -E "^${prefix}" || true
    return 0
  fi

  if host_dpkg_query -W -f='${Status}' "$pattern" 2>/dev/null | grep -qE 'install ok installed'; then
    printf '%s\n' "$pattern"
  fi
}

runtime_purity_collect_installed_excluded() {
  local exclude_file="${1:-$(runtime_debootstrap_exclude_file)}"
  local -a found=()
  local line pkg match

  [[ -f "$exclude_file" ]] || return 0

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    while IFS= read -r match; do
      [[ -n "$match" ]] || continue
      if runtime_purity_is_allowed_display_manager_dependency "$match"; then
        continue
      fi
      found+=("$match")
    done < <(runtime_purity_installed_matching_exclude "$line")
  done <"$exclude_file"

  if [[ "${#found[@]}" -gt 0 ]]; then
    printf '%s\n' "${found[@]}" | LC_ALL=C sort -u
  fi
}

runtime_purity_trace_excluded_package() {
  local excluded="${1:?}"
  local report="${2:?}"

  {
    echo "=== excluded package trace: ${excluded} ==="
    echo "timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo
    echo "--- dpkg -s ${excluded} ---"
    runtime_rootfs_chroot_exec "dpkg -s '${excluded}'" 2>&1 || true
    echo
    echo "--- apt-cache policy ${excluded} ---"
    runtime_rootfs_chroot_exec "apt-cache policy '${excluded}'" 2>&1 || true
    echo
    echo "--- apt-cache rdepends --installed ${excluded} ---"
    runtime_rootfs_chroot_exec "apt-cache rdepends --installed '${excluded}'" 2>&1 || true
    echo
    echo "--- apt-cache rdepends ${excluded} (top) ---"
    runtime_rootfs_chroot_exec "apt-cache rdepends '${excluded}'" 2>&1 | head -60 || true
    echo
    echo "--- installed packages recommending ${excluded} ---"
    runtime_rootfs_chroot_exec \
      "apt-cache rdepends --installed '${excluded}' 2>/dev/null | sed -n '3,\$p' | while read -r dep; do
         [[ -z \"\$dep\" ]] && continue
         d=\${dep%% *}
         apt-cache depends \"\$d\" 2>/dev/null | grep -E 'Recommends|Suggests' | grep -F '${excluded}' && echo \"via recommends: \$d\"
       done" 2>&1 || true
    echo
    echo "--- recent apt history (install lines) ---"
    runtime_rootfs_chroot_exec \
      "grep -h '^Install:' /var/log/apt/history.log /var/log/apt/term.log 2>/dev/null | tail -30" 2>&1 || true
    echo
    echo "--- seed packages with Depends/Recommends on ${excluded} ---"
    runtime_rootfs_chroot_exec \
      "for p in \$(dpkg-query -W -f='\${Package}\n' 2>/dev/null | head -500); do
         apt-cache depends \"\$p\" 2>/dev/null | grep -qE '(Depends|Recommends|Suggests):.*${excluded}' && echo \"related: \$p\"
       done" 2>&1 | head -40 || true
    echo
  } >>"$report"
}

runtime_debootstrap_write_excluded_dependency_trace_report() {
  local report="${1:?}"
  local -a excluded=()

  mkdir -p "$(dirname "$report")"
  {
    echo "=== Recoverix runtime excluded dependency trace ==="
    echo "timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "rootfs: ${ROOTFS_RESOLVED:-}"
    echo
  } >"$report"

  mapfile -t excluded < <(runtime_purity_collect_installed_excluded)
  if [[ "${#excluded[@]}" -eq 0 ]]; then
    echo "no excluded packages installed" >>"$report"
    return 0
  fi

  echo "installed excluded packages: ${excluded[*]}" >>"$report"
  echo >>"$report"

  local pkg
  for pkg in "${excluded[@]}"; do
    runtime_purity_trace_excluded_package "$pkg" "$report"
  done

  log_info "excluded dependency trace report: ${report}"
  return 0
}

runtime_debootstrap_verify_no_bloat_packages() {
  local exclude_file mounted=0 failures=0
  local -a installed_excluded=()
  local ts trace_report pkg

  exclude_file="$(runtime_debootstrap_exclude_file)"
  [[ -f "$exclude_file" ]] || return 0

  log_step "verify runtime rootfs purity (excluded desktop/bloat packages)"

  runtime_debootstrap_mount && mounted=1

  mapfile -t installed_excluded < <(runtime_purity_collect_installed_excluded "$exclude_file")
  for pkg in "${installed_excluded[@]}"; do
    log_fail "FAIL: excluded package present in runtime rootfs: ${pkg}"
    failures=$((failures + 1))
  done

  if [[ $failures -gt 0 ]]; then
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    trace_report="${REPORT_DIR:-/recovery/build/reports/runtime-build}/runtime_excluded_dependency_trace_${ts}.txt"
    runtime_debootstrap_write_excluded_dependency_trace_report "$trace_report"
    log_fail "FAIL: runtime purity validation failed — trace: ${trace_report}"
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  fi

  [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
  log_pass "runtime rootfs purity OK (no excluded desktop/bloat packages)"
  return 0
}
