#!/usr/bin/env bash
# Release-aware apt repository initialization inside debootstrap rootfs.

: "${RUNTIME_DEBOOTSTRAP_SUITE:=jammy}"
: "${RUNTIME_DEBOOTSTRAP_MIRROR:=http://archive.ubuntu.com/ubuntu/}"
: "${RUNTIME_APT_COMPONENTS:=main restricted universe multiverse}"
: "${RUNTIME_APT_ENABLE_UPDATES:=1}"
: "${RUNTIME_APT_ENABLE_SECURITY:=1}"

runtime_apt_sources_template_file() {
  local suite="${RUNTIME_DEBOOTSTRAP_SUITE:-jammy}"
  printf '%s/runtime_apt_sources.%s.list' "$(runtime_image_assets_dir)" "$suite"
}

runtime_apt_normalize_mirror() {
  local m="${1:-http://archive.ubuntu.com/ubuntu/}"
  m="${m%/}"
  printf '%s/' "$m"
}

# Write /etc/apt/sources.list and quarantine debootstrap-only fragments.
runtime_debootstrap_configure_apt_repositories() {
  local root="${ROOTFS_RESOLVED:?}"
  local mirror suite components template
  local -a lines=()

  mirror="$(runtime_apt_normalize_mirror "${RUNTIME_DEBOOTSTRAP_MIRROR}")"
  suite="${RUNTIME_DEBOOTSTRAP_SUITE:-jammy}"
  components="${RUNTIME_APT_COMPONENTS:-main restricted universe multiverse}"

  log_step "configure runtime apt repositories"

  mkdir -p "${root}/etc/apt" "${root}/etc/apt/sources.list.d"

  if [[ -d "${root}/etc/apt/sources.list.d" ]]; then
    local f base
    for f in "${root}/etc/apt/sources.list.d"/*; do
      [[ -e "$f" ]] || continue
      base="$(basename "$f")"
      [[ "$base" == "recoverix-runtime.list" ]] && continue
      mv -f "$f" "${f}.debootstrap-bak" 2>/dev/null || true
      log_info "quarantined debootstrap apt fragment: ${base} -> ${base}.debootstrap-bak"
    done
  fi

  template="$(runtime_apt_sources_template_file)"
  if [[ -f "$template" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      [[ -z "${line// }" ]] && continue
      line="${line//@MIRROR@/$mirror}"
      line="${line//@SUITE@/$suite}"
      line="${line//@COMPONENTS@/$components}"
      lines+=("$line")
    done <"$template"
  else
    lines+=("deb ${mirror} ${suite} ${components}")
    [[ "${RUNTIME_APT_ENABLE_UPDATES:-1}" == "1" ]] && \
      lines+=("deb ${mirror} ${suite}-updates ${components}")
    [[ "${RUNTIME_APT_ENABLE_SECURITY:-1}" == "1" ]] && \
      lines+=("deb ${mirror} ${suite}-security ${components}")
  fi

  {
    echo "# Recoverix production runtime apt sources (${suite})"
    echo "# mirror=${mirror}"
    echo "# components=${components}"
    printf '%s\n' "${lines[@]}"
  } >"${root}/etc/apt/sources.list"

  runtime_apt_configure_strict_minimal_policy || return 1

  log_pass "apt sources.list initialized (${#lines[@]} deb lines)"
  log_info "enabled components: ${components}"
  log_info "suites: ${suite}, ${suite}-updates, ${suite}-security"
  return 0
}

runtime_debootstrap_chroot_apt_update() {
  local out rc

  log_step "apt-get update (runtime rootfs)"

  if ! runtime_debootstrap_chroot 'test -f /etc/resolv.conf'; then
    log_fail "FAIL: rootfs missing /etc/resolv.conf — cannot resolve mirror hostnames"
    return 1
  fi

  set +e
  out="$(runtime_debootstrap_chroot \
    'DEBIAN_FRONTEND=noninteractive apt-get update -o Acquire::Retries=3 2>&1')"
  rc=$?
  set -e

  if [[ $rc -ne 0 ]]; then
    log_fail "FAIL: apt-get update failed in runtime rootfs (exit=${rc})"
    log_fail "FAIL: mirror=${RUNTIME_DEBOOTSTRAP_MIRROR} suite=${RUNTIME_DEBOOTSTRAP_SUITE:-jammy}"
    log_fail "FAIL: check network, DNS, proxy, and firewall to archive.ubuntu.com"
    printf '%s\n' "$out" >&2
    return 1
  fi

  if printf '%s\n' "$out" | grep -qiE 'Failed to fetch|Could not resolve|Connection timed out|Temporary failure resolving'; then
    log_fail "FAIL: apt-get update reported network/mirror errors"
    printf '%s\n' "$out" >&2
    return 1
  fi

  log_pass "apt-get update succeeded in runtime rootfs"
  return 0
}

runtime_package_repo_requirements_file() {
  printf '%s/runtime_package_repo_requirements.txt' "$(runtime_image_assets_dir)"
}

# Echo required component for pkg on suite (e.g. universe), or empty.
runtime_package_required_apt_component() {
  local suite="${1:?}" pkg="${2:?}"
  local req_file
  req_file="$(runtime_package_repo_requirements_file)"
  [[ -f "$req_file" ]] || return 1
  awk -v s="$suite" -v p="$pkg" '$1 == s && $2 == p { print $3; exit }' "$req_file" 2>/dev/null
}

runtime_debootstrap_chroot_apt_cache_policy() {
  local pkg="${1:?}"
  runtime_debootstrap_chroot "apt-cache policy '${pkg}'" 2>/dev/null
}

runtime_debootstrap_assert_package_apt_candidate() {
  local pkg="${1:?}"
  local policy required_component failures=0

  log_step "verify package availability in rootfs apt: ${pkg}"

  policy="$(runtime_debootstrap_chroot_apt_cache_policy "$pkg" || true)"
  if [[ -z "$policy" ]]; then
    log_fail "FAIL: apt-cache policy ${pkg} produced no output"
    return 1
  fi

  if ! printf '%s\n' "$policy" | grep -qE '^[[:space:]]+[0-9]+[[:space:]]+http'; then
    log_fail "FAIL: ${pkg} — no apt index entry in rootfs (wrong/missing repository?)"
    printf '%s\n' "$policy" >&2
    failures=$((failures + 1))
  fi

  if ! printf '%s\n' "$policy" | grep -qE 'Candidate: [0-9]'; then
    log_fail "FAIL: ${pkg} — no install candidate in rootfs apt"
    required_component="$(runtime_package_required_apt_component "${RUNTIME_DEBOOTSTRAP_SUITE:-jammy}" "$pkg" 2>/dev/null || true)"
    if [[ -n "$required_component" ]]; then
      log_fail "FAIL: ${pkg} requires apt component: ${required_component} (enable in sources.list)"
    fi
    printf '%s\n' "$policy" >&2
    failures=$((failures + 1))
  fi

  [[ $failures -eq 0 ]] || return 1

  if printf '%s\n' "$policy" | grep -qi universe; then
    log_info "${pkg} package origin: universe (expected for partclone on jammy)"
  fi

  log_pass "${pkg} install candidate available in rootfs apt"
  return 0
}

runtime_debootstrap_write_apt_repository_report() {
  local report="${1:?}"
  local root="${ROOTFS_RESOLVED:?}"
  local pkg mirror suite

  mirror="$(runtime_apt_normalize_mirror "${RUNTIME_DEBOOTSTRAP_MIRROR}")"
  suite="${RUNTIME_DEBOOTSTRAP_SUITE:-jammy}"

  mkdir -p "$(dirname "$report")"

  {
    echo "=== Recoverix runtime apt repository report ==="
    echo "timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "mirror: ${mirror}"
    echo "suite: ${suite}"
    echo "components: ${RUNTIME_APT_COMPONENTS:-main restricted universe multiverse}"
    echo
    echo "--- enabled repositories (sources.list) ---"
    cat "${root}/etc/apt/sources.list" 2>/dev/null || echo "missing sources.list"
    echo
    echo "--- sources.list.d (active) ---"
    ls -la "${root}/etc/apt/sources.list.d" 2>/dev/null || true
    echo
    echo "--- apt-cache policy partclone ---"
    runtime_debootstrap_chroot_apt_cache_policy partclone || echo "apt-cache policy partclone failed"
    echo
    echo "--- apt-cache policy ntfs-3g ---"
    runtime_debootstrap_chroot_apt_cache_policy ntfs-3g || true
    echo
    echo "--- package origin (partclone) ---"
    runtime_debootstrap_chroot_apt_cache_policy partclone 2>/dev/null | grep -E '^\s+[0-9]+|Candidate:|Installed' || true
  } >"$report"

  log_pass "apt repository report: ${report}"
  return 0
}

# Configure sources, apt update, validate critical packages, write report.
runtime_debootstrap_configure_and_validate_apt() {
  local mounted=0
  local ts report

  runtime_debootstrap_mount && mounted=1

  runtime_debootstrap_configure_apt_repositories || {
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  }

  runtime_debootstrap_chroot_apt_update || {
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  }

  runtime_debootstrap_assert_package_apt_candidate partclone || {
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  }

  runtime_debootstrap_assert_package_apt_candidate ntfs-3g || {
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  }

  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  report="${REPORT_DIR:-/recovery/build/reports/runtime-build}/runtime_apt_repos_${ts}.txt"
  runtime_debootstrap_write_apt_repository_report "$report"

  [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
  return 0
}
