#!/usr/bin/env bash
# Production-grade minimal rootfs via debootstrap (primary runtime build path).

: "${RECOVERIX_RUNTIME_BUILD_MODE:=debootstrap}"
: "${RECOVERIX_FORCE_DEBOOTSTRAP:=0}"
: "${RECOVERIX_ALLOW_HOST_CLONE_SNAPSHOT:=0}"
: "${RUNTIME_DEBOOTSTRAP_SUITE:=jammy}"
: "${RUNTIME_DEBOOTSTRAP_VARIANT:=minbase}"
: "${RUNTIME_DEBOOTSTRAP_MIRROR:=http://archive.ubuntu.com/ubuntu/}"
: "${RUNTIME_DEBOOTSTRAP_MIN_FREE_GB:=12}"
: "${RUNTIME_ROOTFS_SIZE_TARGET_GB:=3}"
: "${RUNTIME_ROOTFS_SIZE_WARN_GB:=3}"
: "${RUNTIME_ROOTFS_SIZE_FAIL_GB:=5}"
: "${KEEP_RUNTIME_ROOTFS:=1}"
: "${RUNTIME_APT_CACHE_WARN_MB:=300}"
: "${RUNTIME_APT_CACHE_FAIL_MB:=1024}"
: "${RECOVERIX_RESET_MACHINE_ID:=1}"
: "${RECOVERIX_RUNTIME_ENABLE_GUI:=1}"

# Set by provision: debootstrap | snapshot | dev-host-rsync
RECOVERIX_RUNTIME_ROOTFS_SOURCE="${RECOVERIX_RUNTIME_ROOTFS_SOURCE:-unknown}"

runtime_image_assets_dir() {
  if [[ -n "${RUNTIME_IMAGE_DIR:-}" ]]; then
    printf '%s/assets' "${RUNTIME_IMAGE_DIR}"
    return 0
  fi
  printf '%s' "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/assets"
}

runtime_rootfs_source_marker_path() {
  printf '%s/etc/recoverix/runtime-rootfs-source' "${ROOTFS_RESOLVED:?}"
}

runtime_write_rootfs_source_marker() {
  local mode="${1:?}"
  mkdir -p "${ROOTFS_RESOLVED}/etc/recoverix"
  printf '%s\n' "$mode" >"$(runtime_rootfs_source_marker_path)"
  RECOVERIX_RUNTIME_ROOTFS_SOURCE="$mode"
  export RECOVERIX_RUNTIME_ROOTFS_SOURCE
  log_info "runtime rootfs source mode: ${mode}"
}

runtime_read_rootfs_source_marker() {
  local marker
  marker="$(runtime_rootfs_source_marker_path)"
  if [[ -f "$marker" ]]; then
    head -1 "$marker" 2>/dev/null | tr -d '\r\n'
    return 0
  fi
  return 1
}

runtime_rootfs_size_gib() {
  local root="${1:?}"
  local kb
  kb="$(du -sk "$root" 2>/dev/null | awk '{print $1}')"
  [[ -n "$kb" ]] || return 1
  awk -v k="$kb" 'BEGIN { printf "%.2f", k/1024/1024 }'
}

runtime_contamination_add() {
  local _arr_name="${1:?}"
  local _item="${2:?}"
  eval "$_arr_name+=(\"\$_item\")"
}

runtime_contamination_check_path() {
  local root="${1:?}"
  local rel="${2:?}"
  [[ -e "${root}/${rel#/}" ]]
}

runtime_contamination_check_nonempty_dir() {
  local root="${1:?}"
  local rel="${2:?}"
  local dir="${root}/${rel#/}"
  [[ -d "$dir" ]] || return 1
  [[ -n "$(ls -A "$dir" 2>/dev/null)" ]]
}

runtime_host_machine_id() {
  if [[ -f /etc/machine-id ]]; then
    tr -d '\r\n' </etc/machine-id 2>/dev/null || true
    return 0
  fi
  return 1
}

runtime_rootfs_machine_id() {
  local root="${1:?}"
  if [[ -f "${root}/etc/machine-id" ]]; then
    tr -d '\r\n' <"${root}/etc/machine-id" 2>/dev/null || true
    return 0
  fi
  return 1
}

runtime_rootfs_apt_archive_deb_bytes() {
  local root="${1:?}"
  local dir="${root}/var/cache/apt/archives"
  [[ -d "$dir" ]] || {
    printf '0'
    return 0
  }
  find "$dir" -maxdepth 1 -type f -name '*.deb' -printf '%s\n' 2>/dev/null | \
    awk '{s+=$1} END {print s+0}'
}

runtime_contamination_trace() {
  local root="${1:?}"
  local runtime_user runtime_home
  local -a fail_heuristics=()
  local -a fail_offenders=()
  local -a warn_heuristics=()
  local -a warn_notes=()
  local -a p
  local f host_mid root_mid
  local deb_bytes=0 warn_bytes fail_bytes
  local home_dir home_base

  runtime_user="${RECOVERIX_RUNTIME_USER:-recoverix}"
  runtime_home="${RECOVERIX_RUNTIME_HOME:-/home/recoverix}"

  # 1) host user residue
  if runtime_contamination_check_nonempty_dir "$root" "home"; then
    while IFS= read -r f; do
      [[ -n "$f" ]] || continue
      home_base="${f##*/}"
      if [[ "$home_base" == "$runtime_user" ]]; then
        runtime_contamination_add warn_notes "runtime user home accepted: /home/${home_base}"
        continue
      fi
      runtime_contamination_add fail_heuristics "host-user-residue"
      runtime_contamination_add fail_offenders "home/${home_base}"
    done < <(ls -A "${root}/home" 2>/dev/null || true)
  fi

  # Runtime session files are expected and not contamination.
  p=(
    "var/lib/AccountsService/users/${runtime_user}"
    "etc/gdm3/custom.conf"
    "etc/profile.d/recoverix-runtime-tui-autostart.sh"
    "etc/xdg/autostart/recoverix-recovery-ui.desktop"
    "etc/xdg/openbox/autostart"
  )
  for f in "${p[@]}"; do
    if runtime_contamination_check_path "$root" "$f"; then
      runtime_contamination_add warn_notes "runtime session file accepted: /${f}"
    fi
  done

  # 2) browser/editor residue
  p=(
    "usr/share/cursor"
    "opt/google/chrome"
    "usr/lib/firefox"
    "usr/lib/chromium-browser"
    "usr/share/code"
  )
  for f in "${p[@]}"; do
    if runtime_contamination_check_path "$root" "$f"; then
      runtime_contamination_add fail_heuristics "browser-editor-residue"
      runtime_contamination_add fail_offenders "$f"
    fi
  done

  # 3) snap residue
  p=(
    "var/lib/snapd"
    "snap"
    "var/snap"
    "etc/systemd/system/snapd.service"
  )
  for f in "${p[@]}"; do
    if runtime_contamination_check_path "$root" "$f"; then
      runtime_contamination_add fail_heuristics "snap-residue"
      runtime_contamination_add fail_offenders "$f"
    fi
  done

  # 4) machine-id reuse (FAIL only if exact host match)
  host_mid="$(runtime_host_machine_id 2>/dev/null || true)"
  root_mid="$(runtime_rootfs_machine_id "$root" 2>/dev/null || true)"
  if [[ -n "$host_mid" && -n "$root_mid" && "$host_mid" == "$root_mid" ]]; then
    runtime_contamination_add fail_heuristics "machine-id-reuse"
    runtime_contamination_add fail_offenders "etc/machine-id(matches-host)"
  fi

  # 5) apt cache bloat (*.deb payload only; ignore lock/partial)
  deb_bytes="$(runtime_rootfs_apt_archive_deb_bytes "$root" 2>/dev/null || echo 0)"
  warn_bytes=$((RUNTIME_APT_CACHE_WARN_MB * 1024 * 1024))
  fail_bytes=$((RUNTIME_APT_CACHE_FAIL_MB * 1024 * 1024))
  if [[ "${deb_bytes:-0}" -gt "$fail_bytes" ]]; then
    runtime_contamination_add fail_heuristics "apt-cache-bloat"
    runtime_contamination_add fail_offenders \
      "var/cache/apt/archives/*.deb(${deb_bytes} bytes > ${fail_bytes} bytes)"
  elif [[ "${deb_bytes:-0}" -gt "$warn_bytes" ]]; then
    runtime_contamination_add warn_heuristics "apt-cache-bloat-warn"
    runtime_contamination_add warn_notes \
      "var/cache/apt/archives/*.deb(${deb_bytes} bytes > ${warn_bytes} bytes)"
  fi

  # 6) host journal residue
  if runtime_contamination_check_nonempty_dir "$root" "var/log/journal"; then
    runtime_contamination_add fail_heuristics "host-journal-residue"
    runtime_contamination_add fail_offenders "var/log/journal(non-empty)"
  fi

  # dedupe and export textual lists (fail + warn separated)
  if [[ "${#fail_heuristics[@]}" -gt 0 ]]; then
    RECOVERIX_CONTAMINATION_HEURISTICS="$(
      printf '%s\n' "${fail_heuristics[@]}" | awk '!seen[$0]++'
    )"
  else
    RECOVERIX_CONTAMINATION_HEURISTICS=""
  fi
  if [[ "${#fail_offenders[@]}" -gt 0 ]]; then
    RECOVERIX_CONTAMINATION_PATHS="$(
      printf '%s\n' "${fail_offenders[@]}" | awk '!seen[$0]++'
    )"
  else
    RECOVERIX_CONTAMINATION_PATHS=""
  fi
  if [[ "${#warn_heuristics[@]}" -gt 0 ]]; then
    RECOVERIX_CONTAMINATION_WARN_HEURISTICS="$(
      printf '%s\n' "${warn_heuristics[@]}" | awk '!seen[$0]++'
    )"
  else
    RECOVERIX_CONTAMINATION_WARN_HEURISTICS=""
  fi
  if [[ "${#warn_notes[@]}" -gt 0 ]]; then
    RECOVERIX_CONTAMINATION_WARN_PATHS="$(
      printf '%s\n' "${warn_notes[@]}" | awk '!seen[$0]++'
    )"
  else
    RECOVERIX_CONTAMINATION_WARN_PATHS=""
  fi
  export RECOVERIX_CONTAMINATION_HEURISTICS RECOVERIX_CONTAMINATION_PATHS \
    RECOVERIX_CONTAMINATION_WARN_HEURISTICS RECOVERIX_CONTAMINATION_WARN_PATHS

  if [[ -n "${RECOVERIX_CONTAMINATION_HEURISTICS}" ]]; then
    return 0
  fi
  return 1
}

# Investigation-only logging; does not change contamination pass/fail policy.
runtime_log_host_contamination_forensic() {
  local root="${1:?}"
  local report="${2:-}"
  local runtime_user="${RECOVERIX_RUNTIME_USER:-recoverix}"
  local primary_path primary_reason clone_like
  local match_count=0
  local -a find_lines=()
  local rel f line u

  log "=== Host contamination forensic (investigation) ==="

  if [[ -n "${RECOVERIX_CONTAMINATION_HEURISTICS:-}" ]]; then
    clone_like="yes"
  else
    clone_like="no"
  fi

  log "rootfs_host_clone_like_formula: (runtime_contamination_trace returns 0) -> rootfs_host_clone_like=yes"
  log "rootfs_host_clone_like_formula: (runtime_contamination_trace returns 1) -> rootfs_host_clone_like=no"
  log "rootfs_host_clone_like=${clone_like}"
  log "RECOVERIX_CONTAMINATION_HEURISTICS=${RECOVERIX_CONTAMINATION_HEURISTICS:-<empty>}"
  log "RECOVERIX_CONTAMINATION_PATHS=${RECOVERIX_CONTAMINATION_PATHS:-<empty>}"

  log "contamination_policy: home/* — FAIL if non-empty and basename != whitelist user (${runtime_user})"
  log "contamination_policy: /root — NOT used for contamination FAIL"
  log "contamination_policy: /home/ubuntu — checked only via home/* directory scan (not separate rule)"
  log "contamination_policy: /home/for — checked only via home/* directory scan (not separate rule)"
  log "contamination_policy: /etc/passwd — NOT used for contamination FAIL (forensic dump only)"
  log "contamination_policy: /etc/group — NOT used for contamination FAIL (forensic dump only)"
  log "contamination_policy: browser-editor paths — usr/share/cursor, opt/google/chrome, ..."
  log "contamination_policy: snap paths — var/lib/snapd, snap, var/snap, ..."
  log "contamination_policy: machine-id — FAIL if rootfs etc/machine-id equals host"
  log "contamination_policy: apt-cache — FAIL if var/cache/apt/archives/*.deb exceeds threshold"
  log "contamination_policy: journal — FAIL if var/log/journal non-empty"

  primary_path="$(printf '%s\n' "${RECOVERIX_CONTAMINATION_PATHS:-}" | awk 'NF { print; exit }')"
  primary_reason="$(printf '%s\n' "${RECOVERIX_CONTAMINATION_HEURISTICS:-}" | awk 'NF { print; exit }')"
  [[ -n "$primary_path" ]] || primary_path="unknown"
  [[ -n "$primary_reason" ]] || primary_reason="unknown"

  log "host_contamination_path=${primary_path}"
  log "host_contamination_reason=${primary_reason}"

  log "forensic: ls -A ${root}/home (contamination home scan source)"
  ls -A "${root}/home" 2>&1 | while IFS= read -r line; do
    [[ -n "$line" ]] && log "  ${line}"
  done

  log "forensic: find \"${root}\" -path \"*/home/for*\" -ls"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -n "$line" ]] || continue
    find_lines+=("$line")
    log "  ${line}"
  done < <(find "$root" -path "*/home/for*" -ls 2>/dev/null || true)
  match_count="${#find_lines[@]}"

  log "host_contamination_match_count=${match_count}"

  log "forensic: matched offender path file listing"
  while IFS= read -r rel || [[ -n "$rel" ]]; do
    [[ -n "$rel" ]] || continue
    log "  --- ${rel} ---"
    if [[ -d "${root}/${rel}" ]]; then
      while IFS= read -r f || [[ -n "$f" ]]; do
        log "  ${f#${root}/}"
      done < <(find "${root}/${rel}" 2>/dev/null | LC_ALL=C sort | head -n 200)
    elif [[ -e "${root}/${rel}" ]]; then
      log "  ${rel} (single path)"
    else
      log "  ${rel} (not found on disk)"
    fi
  done <<<"${RECOVERIX_CONTAMINATION_PATHS:-}"

  log "forensic: /etc/passwd entries (informational; not contamination FAIL criteria)"
  for u in root ubuntu for "$runtime_user"; do
    if line="$(grep -m1 "^${u}:" "${root}/etc/passwd" 2>/dev/null || true)"; then
      [[ -n "$line" ]] && log "  passwd: ${line}"
    else
      log "  passwd: no entry for ${u}"
    fi
  done

  log "forensic: /etc/group entries (informational; not contamination FAIL criteria)"
  for u in root ubuntu for "$runtime_user"; do
    if line="$(grep -m1 "^${u}:" "${root}/etc/group" 2>/dev/null || true)"; then
      [[ -n "$line" ]] && log "  group: ${line}"
    else
      log "  group: no entry for ${u}"
    fi
  done

  for line in \
    "host_contamination_path=${primary_path}" \
    "host_contamination_reason=${primary_reason}" \
    "host_contamination_match_count=${match_count}" \
    "rootfs_host_clone_like=${clone_like}"; do
    [[ -n "$report" ]] && printf '%s\n' "$line" >>"$report"
  done
}

runtime_rootfs_looks_like_host_clone() {
  local root="${1:?}"
  local report="${2:-}"
  local runtime_user="${RECOVERIX_RUNTIME_USER:-recoverix}"
  local runtime_home="${RECOVERIX_RUNTIME_HOME:-/home/recoverix}"

  # Deterministic policy:
  # - FAIL only on concrete offending path evidence.
  # - No size-only heuristic.
  if runtime_contamination_trace "$root"; then
    log_fail "FAIL: host contamination detected:"
    while IFS= read -r f; do
      [[ -n "$f" ]] && log_fail " - ${f}"
    done <<<"${RECOVERIX_CONTAMINATION_PATHS:-}"
    while IFS= read -r f; do
      [[ -n "$f" ]] && log_info "contamination heuristic triggered: ${f}"
    done <<<"${RECOVERIX_CONTAMINATION_HEURISTICS:-}"
    runtime_log_host_contamination_forensic "$root" "${report}"
    return 0
  fi
  while IFS= read -r f; do
    [[ -n "$f" ]] && log_warn "WARN: contamination heuristic triggered: ${f}"
  done <<<"${RECOVERIX_CONTAMINATION_WARN_HEURISTICS:-}"
  while IFS= read -r f; do
    [[ -n "$f" ]] && log_info "INFO: ${f}"
  done <<<"${RECOVERIX_CONTAMINATION_WARN_PATHS:-}"
  if runtime_contamination_check_path "$root" "${runtime_home#/}"; then
    log_pass "runtime user home accepted: ${runtime_home}"
    log_info "runtime user whitelist active: ${runtime_user}"
  fi

  log_pass "no host contamination detected"
  log_pass "production debootstrap runtime accepted"
  return 1
}

runtime_reset_machine_id_for_first_boot() {
  local root="${ROOTFS_RESOLVED:?}"
  local machine_id="${root}/etc/machine-id"
  local dbus_id="${root}/var/lib/dbus/machine-id"

  [[ "${RECOVERIX_RESET_MACHINE_ID:-1}" == "1" ]] || return 0

  mkdir -p "${root}/etc" "${root}/var/lib/dbus"
  : >"${machine_id}"
  ln -snf /etc/machine-id "${dbus_id}"
  log_info "runtime machine-id reset for first boot regeneration"
  return 0
}

runtime_debootstrap_packages_file() {
  printf '%s/runtime_debootstrap_packages.txt' "$(runtime_image_assets_dir)"
}

runtime_debootstrap_package_allowed() {
  local pkg="${1:?}"

  if [[ "${RECOVERIX_RUNTIME_ENABLE_GUI:-1}" == "1" ]]; then
    return 0
  fi

  case "$pkg" in
    python3-gi|python3-gi-cairo|gir1.2-gtk-3.0|libgtk-3-0|\
    xserver-xorg-core|xserver-xorg-video-dummy|libx11-6|dbus-x11)
      return 1
      ;;
  esac

  return 0
}

runtime_debootstrap_chroot() {
  chroot "${ROOTFS_RESOLVED}" /bin/bash -c "$1"
}

runtime_debootstrap_mount() {
  runtime_chroot_mount_deps "${ROOTFS_RESOLVED}" || return 1
  mkdir -p "${ROOTFS_RESOLVED}/etc"
  cp -f /etc/resolv.conf "${ROOTFS_RESOLVED}/etc/resolv.conf" 2>/dev/null || true
  return 0
}

runtime_debootstrap_umount() {
  runtime_chroot_umount_deps "${ROOTFS_RESOLVED}" || true
}

runtime_assert_debootstrap_disk_space() {
  local min_gib="${RUNTIME_DEBOOTSTRAP_MIN_FREE_GB}"
  local avail_kib avail_gib

  log_step "debootstrap disk space precheck (min ${min_gib}GiB)"
  avail_kib="$(runtime_disk_avail_kib_for_path "${ROOTFS:-/recovery/build/rootfs}" 2>/dev/null || echo 0)"
  avail_gib="$(awk -v k="${avail_kib:-0}" 'BEGIN { printf "%.2f", k/1024/1024 }')"
  if [[ -n "${avail_kib}" && "${avail_kib}" -gt 0 ]]; then
    local min_kib=$((min_gib * 1024 * 1024))
    if [[ "$avail_kib" -lt "$min_kib" ]]; then
      log_fail "FAIL: need at least ${min_gib}GiB free for debootstrap (avail=${avail_gib}GiB)"
      return 1
    fi
    log_pass "debootstrap disk precheck: ${avail_gib}GiB free"
  fi
  runtime_assert_build_disk_safety
  return 0
}

runtime_debootstrap_unmount_rootfs() {
  local root="${ROOTFS_RESOLVED:?}"
  local m
  while read -r m; do
    [[ -n "$m" ]] || continue
    umount -l "$m" 2>/dev/null || true
  done < <(findmnt -rn -o TARGET -- "$root" 2>/dev/null | sort -r)
}

runtime_debootstrap_wipe_rootfs() {
  local root="${ROOTFS_RESOLVED:?}"

  if [[ "${RECOVERIX_SQUASHFS_STAGE:-0}" == "1" ]]; then
    log_fail "FAIL: rootfs wipe forbidden during squashfs build stage"
    return 1
  fi

  log_step "debootstrap: wipe existing rootfs contents"
  runtime_debootstrap_unmount_rootfs
  if [[ -d "$root" ]]; then
    find "$root" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  fi
  mkdir -p "$root"
  log_pass "rootfs wiped for debootstrap: ${root}"
  if declare -f runtime_log_kernel_artifact_forensic >/dev/null 2>&1; then
    runtime_log_kernel_artifact_forensic "debootstrap_post_wipe" ""
  fi
}

runtime_debootstrap_install_host_tooling() {
  if command -v debootstrap >/dev/null 2>&1; then
    return 0
  fi
  log_info "installing debootstrap on build host"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends debootstrap
}

runtime_debootstrap_run() {
  local suite="${RUNTIME_DEBOOTSTRAP_SUITE}"
  local variant="${RUNTIME_DEBOOTSTRAP_VARIANT}"
  local mirror="${RUNTIME_DEBOOTSTRAP_MIRROR}"
  local root="${ROOTFS_RESOLVED:?}"

  log_step "debootstrap: create minimal Ubuntu ${suite} (${variant})"
  runtime_debootstrap_install_host_tooling
  runtime_assert_debootstrap_disk_space || return 1

  if ! debootstrap --arch=amd64 --variant="${variant}" "${suite}" "${root}" "${mirror}"; then
    log_fail "FAIL: debootstrap failed for ${suite}"
    return 1
  fi

  log_pass "debootstrap base system created"
  return 0
}

runtime_debootstrap_install_seed_packages() {
  local pkg_file mounted=0
  local -a pkgs=()

  pkg_file="$(runtime_debootstrap_packages_file)"

  log_step "debootstrap: install runtime seed packages (release-aware validation)"

  if [[ ! -f "$pkg_file" ]]; then
    log_fail "FAIL: missing package list: ${pkg_file}"
    return 1
  fi

  runtime_debootstrap_mount && mounted=1

  runtime_debootstrap_configure_apt_repositories || {
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  }

  runtime_debootstrap_chroot_apt_update || {
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  }

  export RECOVERIX_VALIDATE_PACKAGES_IN_ROOTFS=1
  runtime_resolve_debootstrap_install_packages "$pkg_file" || {
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  }
  pkgs=("${RUNTIME_DEBOOTSTRAP_INSTALL_PKGS[@]}")
  if [[ "${#pkgs[@]}" -eq 0 ]]; then
    log_fail "FAIL: no packages to install after validation"
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  fi

  runtime_debootstrap_assert_package_apt_candidate partclone || {
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  }

  local ts report
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  report="${REPORT_DIR:-/recovery/build/reports/runtime-build}/runtime_apt_repos_${ts}.txt"
  runtime_debootstrap_write_apt_repository_report "$report"

  log_step "debootstrap: strict minimal install resolved packages (${#pkgs[@]})"
  recoverix_runtime_apt_install_packages "${pkgs[@]}" || {
    log_fail "FAIL: strict minimal seed package install failed"
    [[ $mounted -eq 1 ]] && runtime_debootstrap_umount
    return 1
  }

  runtime_debootstrap_chroot 'DEBIAN_FRONTEND=noninteractive apt-get clean' || true
  runtime_debootstrap_chroot 'rm -rf /var/lib/apt/lists/*' || true

  [[ $mounted -eq 1 ]] && runtime_debootstrap_umount

  runtime_assert_runtime_filesystem_binaries "${ROOTFS_RESOLVED}" || {
    log_fail "FAIL: filesystem binaries missing after seed install"
    return 1
  }

  log_pass "debootstrap seed packages installed (${#pkgs[@]} packages)"
  return 0
}

runtime_debootstrap_install_kernel() {
  local report="${REPORT_DIR:-/recovery/build/reports/runtime-build}/kernel_packages_install_$(date -u +%Y%m%dT%H%M%SZ).txt"
  mkdir -p "$(dirname "$report")"

  log_step "debootstrap: install kernel packages (runtime_install_kernel_packages)"
  if declare -f runtime_install_kernel_packages >/dev/null 2>&1; then
    runtime_install_kernel_packages "$report" || {
      log_fail "FAIL: runtime_debootstrap_install_kernel — runtime_install_kernel_packages failed (report: ${report})"
      return 1
    }
    log_info "kernel packages forensic report: ${report}"
    return 0
  fi

  log_fail "FAIL: runtime_debootstrap_install_kernel — runtime_install_kernel_packages unavailable (source runtime_kernel_packages.sh)"
  return 1
}

runtime_debootstrap_verify_size_target() {
  local gib="${1:-$(runtime_rootfs_size_gib "${ROOTFS_RESOLVED}")}"
  local warn="${RUNTIME_ROOTFS_SIZE_WARN_GB}"
  local fail="${RUNTIME_ROOTFS_SIZE_FAIL_GB}"
  local target="${RUNTIME_ROOTFS_SIZE_TARGET_GB}"

  log_step "debootstrap: rootfs size target (${target}GiB goal, warn>${warn}GiB, fail>${fail}GiB)"
  log_info "rootfs size: ${gib}GiB"

  awk -v g="$gib" -v f="$fail" 'BEGIN { exit !(g+0 > f+0) }' && {
    log_fail "FAIL: rootfs size ${gib}GiB exceeds maximum ${fail}GiB (not storage-efficient)"
    return 1
  }

  awk -v g="$gib" -v w="$warn" 'BEGIN { exit !(g+0 > w+0) }' && {
    log_warn "WARN: rootfs size ${gib}GiB exceeds target ${target}GiB (review package list)"
  } || log_pass "rootfs size within target (${gib}GiB)"

  return 0
}

runtime_debootstrap_build_rootfs() {
  if [[ "${RECOVERIX_SQUASHFS_STAGE:-0}" == "1" ]]; then
    log_fail "FAIL: debootstrap forbidden during squashfs build stage"
    return 1
  fi

  RUNTIME_INSTALL_KERNEL_PACKAGES_EXECUTED=0
  export RUNTIME_INSTALL_KERNEL_PACKAGES_EXECUTED

  runtime_debootstrap_wipe_rootfs
  runtime_debootstrap_run || return 1
  # install_seed_packages configures apt repos, validates partclone policy, then installs seed list
  runtime_debootstrap_install_seed_packages || return 1
  runtime_debootstrap_install_kernel || return 1
  runtime_reset_machine_id_for_first_boot || return 1
  runtime_debootstrap_verify_no_bloat_packages || return 1
  runtime_debootstrap_verify_size_target || return 1
  runtime_write_rootfs_source_marker "debootstrap"
  log_pass "production debootstrap runtime rootfs complete"
  return 0
}

# Primary entry: provision rootfs (debootstrap production path, snapshot reuse, dev rsync emergency).
runtime_provision_rootfs_primary() {
  local root="${ROOTFS_RESOLVED:?}"
  local mode="${RECOVERIX_RUNTIME_BUILD_MODE:-auto}"
  local existing_source=""

  if [[ "${RECOVERIX_SQUASHFS_STAGE:-0}" == "1" ]]; then
    log_fail "FAIL: runtime_provision_rootfs_primary forbidden during squashfs build stage"
    log_fail "hint: run 00_build_runtime.sh first; squashfs stage must not reprovision rootfs"
    return 1
  fi

  log_step "provision runtime rootfs (mode=${mode})"

  if existing_source="$(runtime_read_rootfs_source_marker 2>/dev/null)"; then
    log_info "existing rootfs source marker: ${existing_source}"
  fi

  if [[ "${RECOVERIX_FORCE_DEBOOTSTRAP:-0}" == "1" ]]; then
    log_info "RECOVERIX_FORCE_DEBOOTSTRAP=1 — rebuilding rootfs via debootstrap"
    runtime_debootstrap_build_rootfs || return 1
    if declare -f runtime_kernel_package_assert_force_debootstrap_contract >/dev/null 2>&1; then
      runtime_kernel_package_assert_force_debootstrap_contract "${RUNTIME_KERNEL_PACKAGES_INSTALL_REPORT:-}" || return 1
    fi
    return 0
  fi

  if runtime_dev_host_rsync_bootstrap_enabled; then
    if runtime_rootfs_has_minimal_userspace "$root"; then
      runtime_write_rootfs_source_marker "dev-host-rsync"
      log_pass "rootfs userspace present (dev-host-rsync mode)"
      return 0
    fi
    log_warn "dev-host-rsync enabled; attempting emergency bootstrap"
    if runtime_dev_host_rsync_bootstrap_rootfs; then
      runtime_write_rootfs_source_marker "dev-host-rsync"
      return 0
    fi
    return 1
  fi

  if runtime_rootfs_has_minimal_userspace "$root"; then
    if runtime_rootfs_looks_like_host_clone "$root"; then
      if [[ "${RECOVERIX_ALLOW_HOST_CLONE_SNAPSHOT:-0}" == "1" ]]; then
        log_warn "WARN: host-clone-like rootfs allowed by RECOVERIX_ALLOW_HOST_CLONE_SNAPSHOT=1"
        runtime_write_rootfs_source_marker "snapshot"
        log_pass "using existing rootfs (snapshot/host-clone allowed)"
        return 0
      fi
      log_fail "FAIL: rootfs looks like a host Ubuntu clone (storage-inefficient)"
      log_fail "FAIL: production path is debootstrap — set RECOVERIX_FORCE_DEBOOTSTRAP=1 to rebuild"
      return 1
    fi
    case "${existing_source:-}" in
      debootstrap|snapshot|dev-host-rsync)
        RECOVERIX_RUNTIME_ROOTFS_SOURCE="$existing_source"
        export RECOVERIX_RUNTIME_ROOTFS_SOURCE
        log_pass "using existing minimal runtime rootfs (source=${existing_source})"
        if declare -f runtime_kernel_package_log_provision_context >/dev/null 2>&1; then
          log_warn "runtime_install_kernel_packages skipped — existing rootfs reused (source=${existing_source})"
          runtime_kernel_package_log_provision_context ""
          runtime_kernel_package_append_install_report "${KEEP_KERNEL_FLAVOR:?}" ""
        fi
        return 0
        ;;
    esac
    runtime_write_rootfs_source_marker "snapshot"
    log_pass "using existing minimal runtime rootfs (snapshot reuse, no prior marker)"
    if declare -f runtime_kernel_package_log_provision_context >/dev/null 2>&1; then
      log_warn "runtime_install_kernel_packages skipped — snapshot reuse (no prior marker)"
      runtime_kernel_package_log_provision_context ""
      runtime_kernel_package_append_install_report "${KEEP_KERNEL_FLAVOR:?}" ""
    fi
    return 0
  fi

  case "$mode" in
    snapshot-only)
      log_fail "FAIL: rootfs incomplete and RECOVERIX_RUNTIME_BUILD_MODE=snapshot-only"
      return 1
      ;;
    debootstrap|auto)
      log_info "building fresh minimal rootfs via debootstrap (production path)"
      runtime_debootstrap_build_rootfs || return 1
      return 0
      ;;
    *)
      log_fail "FAIL: unknown RECOVERIX_RUNTIME_BUILD_MODE=${mode}"
      return 1
      ;;
  esac
}

runtime_maybe_cleanup_rootfs_after_build() {
  local root="${ROOTFS_RESOLVED:?}"

  if [[ "${KEEP_RUNTIME_ROOTFS:-1}" != "0" ]]; then
    log_info "KEEP_RUNTIME_ROOTFS=${KEEP_RUNTIME_ROOTFS:-1} — retaining rootfs for debug"
    return 0
  fi

  if [[ ! -f "${RUNTIME_SQUASHFS:-}" ]]; then
    log_warn "WARN: squashfs missing — skipping rootfs cleanup"
    return 0
  fi

  log_step "cleanup temporary rootfs (KEEP_RUNTIME_ROOTFS=0)"
  runtime_debootstrap_unmount_rootfs

  local stamp backup
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup="$(dirname "$root")/rootfs.bak.${stamp}"
  if [[ -d "$root" ]]; then
    mv "$root" "$backup" 2>/dev/null || {
      log_fail "FAIL: could not move rootfs aside to ${backup}"
      return 1
    }
    mkdir -p "$root"
    log_pass "rootfs moved aside to ${backup}; empty ${root} recreated"
  fi
  return 0
}
