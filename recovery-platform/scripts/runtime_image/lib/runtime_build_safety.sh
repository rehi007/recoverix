#!/usr/bin/env bash
# Runtime build safety: disk pressure, free space, artifact inventory, cleanup policy.
# Does NOT touch initramfs/overlay/EFI/GRUB/restore execution.

# Defaults (override via config.env or environment).
: "${RUNTIME_BUILD_MIN_FREE_GB:=8}"
: "${RUNTIME_DISK_WARN_PERCENT:=90}"
: "${RUNTIME_DISK_FAIL_PERCENT:=95}"
: "${RUNTIME_DISK_HARD_FAIL_PERCENT:=99}"
: "${RUNTIME_ARTIFACT_REPORT_KEEP:=20}"
: "${RUNTIME_SQUASHFS_BACKUP_KEEP:=3}"
: "${RECOVERIX_DEV_HOST_RSYNC_BOOTSTRAP:=0}"

# Usage percent for mount point containing path (empty if unknown).
runtime_disk_usage_percent_for_path() {
  local path="${1:?}"
  local mountpoint pct

  if [[ ! -e "$path" ]]; then
    path="$(dirname "$path")"
  fi
  mountpoint="$(df -P "$path" 2>/dev/null | awk 'NR==2 {print $6}')"
  if [[ -z "$mountpoint" ]]; then
    return 1
  fi
  pct="$(df -P "$mountpoint" 2>/dev/null | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
  [[ -n "$pct" ]] || return 1
  printf '%s' "$pct"
  return 0
}

# Available KiB on filesystem containing path.
runtime_disk_avail_kib_for_path() {
  local path="${1:?}"
  if [[ ! -e "$path" ]]; then
    path="$(dirname "$path")"
  fi
  df -P "$path" 2>/dev/null | awk 'NR==2 {print $4}'
}

runtime_dev_host_rsync_bootstrap_enabled() {
  [[ "${RECOVERIX_DEV_HOST_RSYNC_BOOTSTRAP:-0}" == "1" ]]
}

# Disk pressure gate: 90% WARN, 95% FAIL, 99% HARD FAIL.
# Returns: 0=ok, 1=warn-only (build may continue), 2=fail, 3=hard-fail
runtime_check_disk_pressure_gate() {
  local label="${1:-build filesystem}"
  local path="${2:-/recovery}"
  local pct avail_kib avail_gib min_gib="${RUNTIME_BUILD_MIN_FREE_GB}"

  log_step "disk pressure safety gate (${label})"

  if ! pct="$(runtime_disk_usage_percent_for_path "$path")"; then
    log_warn "WARN: could not determine disk usage for ${path}"
    return 0
  fi

  avail_kib="$(runtime_disk_avail_kib_for_path "$path" 2>/dev/null || echo 0)"
  avail_gib="$(awk -v k="${avail_kib:-0}" 'BEGIN { printf "%.2f", k/1024/1024 }')"

  log_info "disk ${label}: path=${path} used=${pct}% avail=${avail_gib}GiB"

  if [[ "$pct" -ge "${RUNTIME_DISK_HARD_FAIL_PERCENT}" ]]; then
    log_fail "HARD FAIL: disk usage ${pct}% >= ${RUNTIME_DISK_HARD_FAIL_PERCENT}% on ${path} — aborting immediately"
    return 3
  fi

  if [[ "$pct" -ge "${RUNTIME_DISK_FAIL_PERCENT}" ]]; then
    log_fail "FAIL: disk usage ${pct}% >= ${RUNTIME_DISK_FAIL_PERCENT}% on ${path} — build blocked"
    return 2
  fi

  if [[ "$pct" -ge "${RUNTIME_DISK_WARN_PERCENT}" ]]; then
    log_warn "WARN: disk usage ${pct}% >= ${RUNTIME_DISK_WARN_PERCENT}% on ${path}"
  else
    log_pass "disk pressure gate: ${pct}% used (below ${RUNTIME_DISK_WARN_PERCENT}% warn threshold)"
  fi

  # Minimum free space (independent of percent — small partitions can be 100% at low GiB).
  if [[ -n "${avail_kib}" && "${avail_kib}" -gt 0 ]]; then
    local min_kib=$((min_gib * 1024 * 1024))
    if [[ "$avail_kib" -lt "$min_kib" ]]; then
      log_fail "FAIL: free space ${avail_gib}GiB < minimum ${min_gib}GiB required for runtime build on ${path}"
      return 2
    fi
    log_pass "free space check: ${avail_gib}GiB >= ${min_gib}GiB minimum"
  fi

  return 0
}

# Enforce disk gate; exit script on fail/hard-fail.
runtime_assert_build_disk_safety() {
  local rc=0
  set +e
  runtime_check_disk_pressure_gate "recovery build tree" "${ROOTFS:-/recovery/build/rootfs}" 
  rc=$?
  set -e

  case "$rc" in
    0|1) return 0 ;;
    2) die "disk pressure safety gate failed — free space or usage threshold" ;;
    3) die "HARD FAIL: disk pressure safety gate — host filesystem critically full" ;;
    *) die "disk pressure safety gate returned unexpected status ${rc}" ;;
  esac
}

runtime_rootfs_is_bootstrap_stub() {
  local root="${1:?}"
  [[ -f "${root}/etc/os-release" ]] || return 1
  grep -q 'recoverix-runtime-bootstrap' "${root}/etc/os-release" 2>/dev/null
}

runtime_rootfs_has_minimal_userspace() {
  local root="${1:?}"
  if runtime_rootfs_is_bootstrap_stub "$root"; then
    return 1
  fi
  [[ -x "${root}/bin/sh" || -x "${root}/usr/bin/sh" ]] || return 1
  [[ -x "${root}/usr/bin/python3" ]] || return 1
  [[ -f "${root}/etc/os-release" ]] || return 1
  return 0
}

# DEV-ONLY: full host rsync into rootfs (explicit opt-in).
runtime_dev_host_rsync_bootstrap_rootfs() {
  local root="${ROOTFS_RESOLVED:?}"

  if ! runtime_dev_host_rsync_bootstrap_enabled; then
    log_fail "FAIL: host rsync bootstrap is disabled (set RECOVERIX_DEV_HOST_RSYNC_BOOTSTRAP=1 for dev-only)"
    return 1
  fi

  log_warn "WARN: DEV host rsync bootstrap enabled — copying host / into ${root}"
  log_warn "WARN: this is NOT for production; risk of runaway copy and host contamination"

  runtime_assert_build_disk_safety

  if ! command -v rsync >/dev/null 2>&1; then
    log_fail "FAIL: rsync not found on build host"
    return 1
  fi

  rsync -aHAX --numeric-ids --delete \
    --exclude='/proc' --exclude='/proc/*' \
    --exclude='/sys' --exclude='/sys/*' \
    --exclude='/dev' --exclude='/dev/*' \
    --exclude='/run' --exclude='/run/*' \
    --exclude='/tmp' --exclude='/tmp/*' \
    --exclude='/var/tmp' --exclude='/var/tmp/*' \
    --exclude='/mnt' --exclude='/mnt/*' \
    --exclude='/media' --exclude='/media/*' \
    --exclude='/lost+found' \
    --exclude='/recovery' --exclude='/recovery/*' \
    / "${root}/"

  if [[ ! -f "${root}/etc/os-release" ]]; then
    log_fail "FAIL: dev host rsync bootstrap did not produce /etc/os-release"
    return 1
  fi

  log_pass "dev host rsync bootstrap complete (development mode only)"
  return 0
}

runtime_squashfs_stage_append_report() {
  local report="${1:-}" force_ignored="${2:-no}"

  for line in \
    "squashfs_stage_reprovision_allowed=no" \
    "squashfs_stage_rootfs_wipe_allowed=no" \
    "squashfs_stage_rootfs_source=prepared" \
    "squashfs_stage_force_debootstrap_ignored=${force_ignored}"; do
    log "$line"
    [[ -n "$report" ]] && printf '%s\n' "$line" >>"$report"
  done
}

# Squashfs build: verify prepared rootfs only — never provision, wipe, or debootstrap.
runtime_assert_rootfs_prepared_for_squashfs() {
  local report="${1:-}"
  local root="${ROOTFS_RESOLVED:?}"
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  local failures=0
  local force_ignored="no"
  local pkg_extra="linux-modules-extra-${kver}"
  local vmlinuz="${root}/boot/vmlinuz-${kver}"
  local initrd="${root}/boot/initrd.img-${kver}"
  local modules_dir="${root}/lib/modules/${kver}"
  local amdgpu_count=0

  log_step "verify prepared rootfs for squashfs (no reprovision)"

  if [[ "${RECOVERIX_FORCE_DEBOOTSTRAP:-0}" == "1" ]]; then
    force_ignored="yes"
    log_warn "WARN: RECOVERIX_FORCE_DEBOOTSTRAP=1 ignored during squashfs stage (rootfs wipe forbidden)"
  fi
  runtime_squashfs_stage_append_report "$report" "$force_ignored"

  if declare -f runtime_log_kernel_artifact_forensic >/dev/null 2>&1; then
    runtime_log_kernel_artifact_forensic "squashfs_entry" "$report"
    runtime_check_kernel_artifact_path_mismatch "$report"
  fi

  if [[ ! -d "$root" ]] || [[ ! -f "${root}/etc/os-release" ]]; then
    log_fail "FAIL: rootfs not prepared before squashfs build"
    log_fail "hint: run 00_build_runtime.sh with RECOVERIX_FORCE_DEBOOTSTRAP=1"
    failures=$((failures + 1))
  elif ! runtime_rootfs_has_minimal_userspace "$root"; then
    log_fail "FAIL: rootfs not prepared before squashfs build (incomplete userspace)"
    log_fail "hint: run 00_build_runtime.sh with RECOVERIX_FORCE_DEBOOTSTRAP=1"
    failures=$((failures + 1))
  else
    log_pass "rootfs userspace present (${root})"
  fi

  if [[ ! -e "$vmlinuz" ]]; then
    log_fail "FAIL: rootfs not prepared — missing ${vmlinuz}"
    failures=$((failures + 1))
  else
    log_pass "rootfs kernel artifact present: ${vmlinuz}"
  fi

  if [[ ! -e "$initrd" ]]; then
    log_fail "FAIL: rootfs not prepared — missing ${initrd}"
    failures=$((failures + 1))
  else
    log_pass "rootfs kernel artifact present: ${initrd}"
  fi

  if [[ ! -d "$modules_dir" ]]; then
    log_fail "FAIL: rootfs not prepared — missing ${modules_dir}"
    failures=$((failures + 1))
  else
    log_pass "rootfs modules tree present: ${modules_dir}"
  fi

  if declare -f host_dpkg_query >/dev/null 2>&1; then
    if host_dpkg_query -W -f='${Status}' "$pkg_extra" 2>/dev/null | grep -q 'install ok installed'; then
      log_pass "rootfs package installed: ${pkg_extra}"
    else
      log_fail "FAIL: rootfs not prepared — ${pkg_extra} not installed"
      log_fail "hint: run 00_build_runtime.sh with RECOVERIX_FORCE_DEBOOTSTRAP=1"
      failures=$((failures + 1))
    fi
  fi

  if declare -f runtime_amdgpu_kernel_module_find_in_rootfs >/dev/null 2>&1; then
    amdgpu_count="$(runtime_amdgpu_kernel_module_find_in_rootfs | wc -l | tr -d ' ')"
  else
    amdgpu_count="$(find "$modules_dir" -name 'amdgpu*.ko*' 2>/dev/null | wc -l | tr -d ' ')"
  fi
  if [[ "${amdgpu_count:-0}" -ge 1 ]]; then
    log_pass "rootfs amdgpu kernel module present (count=${amdgpu_count})"
  else
    log_fail "FAIL: rootfs not prepared — amdgpu kernel module missing under ${modules_dir}"
    failures=$((failures + 1))
  fi

  [[ $failures -eq 0 ]] || return 1
  log_pass "rootfs prepared for squashfs (verify-only; no reprovision/wipe)"
  return 0
}

# Squashfs entry: verify-only alias (must not call runtime_provision_rootfs_primary).
runtime_assert_rootfs_userspace_ready() {
  runtime_assert_rootfs_prepared_for_squashfs "${1:-}"
}

runtime_cleanup_stale_build_mounts() {
  local mnt="${RUNTIME_DIR:?}/verify-mnt"
  if [[ -d "$mnt" ]]; then
    log_step "cleanup stale build mounts"
    umount -R "${mnt}" 2>/dev/null || true
    rm -rf "${mnt}"
    log_pass "removed stale ${mnt}"
  fi
}

# Rotate squashfs backups before a new build; keep RUNTIME_SQUASHFS_BACKUP_KEEP files.
runtime_cleanup_old_squashfs_artifacts() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local keep="${RUNTIME_SQUASHFS_BACKUP_KEEP}"
  local dir stamp

  log_step "cleanup old squashfs artifacts (keep ${keep} backups)"

  dir="$(dirname "$sq")"
  if [[ -f "$sq" ]]; then
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    cp -a "$sq" "${dir}/runtime.squashfs.bak.${stamp}"
    log_info "squashfs backup: ${dir}/runtime.squashfs.bak.${stamp}"
  fi

  mapfile -t backups < <(find "$dir" -maxdepth 1 -type f -name 'runtime.squashfs.bak.*' -printf '%T@ %p\n' 2>/dev/null | sort -n | awk '{print $2}')
  if [[ "${#backups[@]}" -gt "$keep" ]]; then
    local remove=$(( ${#backups[@]} - keep ))
    local i
    for ((i=0; i<remove; i++)); do
      log_info "removing old squashfs backup: ${backups[$i]}"
      rm -f "${backups[$i]}"
    done
  fi

  log_pass "squashfs backup rotation policy applied"
}

# Create build tree dirs without requiring populated rootfs yet.
runtime_ensure_build_tree_directories() {
  local candidate host_root

  log_step "ensure build tree directories"
  mkdir -p "${ROOTFS}" "${RUNTIME_DIR}" "${REPORT_DIR}" || die "cannot create build tree under /recovery"

  if [[ ! -d "${ROOTFS}" ]]; then
    die "ROOTFS directory missing after mkdir: ${ROOTFS}"
  fi

  candidate="$(readlink -f "${ROOTFS}")"
  [[ "$candidate" != "/" ]] || die "ROOTFS cannot be /"
  host_root="$(readlink -f /)"
  [[ "$candidate" != "$host_root" ]] || die "ROOTFS resolves to host root — refusing"

  ROOTFS_RESOLVED="$candidate"
  export ROOTFS_RESOLVED
  log_info "ROOTFS_RESOLVED=${ROOTFS_RESOLVED} (directory only; userspace validated separately)"
  log_pass "build tree directories ready"
}

runtime_cleanup_old_build_reports() {
  local dir="${REPORT_DIR:?}"
  local keep="${RUNTIME_ARTIFACT_REPORT_KEEP}"

  log_step "cleanup old build reports (keep ${keep})"

  [[ -d "$dir" ]] || mkdir -p "$dir"

  mapfile -t old_reports < <(find "$dir" -maxdepth 1 -type f \( -name 'squashfs_build_*.txt' -o -name 'runtime_build_*.txt' -o -name 'boot_validation_*.txt' -o -name 'runtime_artifact_inventory_*.txt' \) -printf '%T@ %p\n' 2>/dev/null | sort -n | awk '{print $2}')
  local count="${#old_reports[@]}"
  if [[ "$count" -le "$keep" ]]; then
    log_pass "build reports: ${count} file(s), within keep=${keep}"
    return 0
  fi

  local remove=$((count - keep))
  local i
  for ((i=0; i<remove; i++)); do
    log_info "removing old report: ${old_reports[$i]}"
    rm -f "${old_reports[$i]}"
  done
  log_pass "removed ${remove} old build report(s)"
}

runtime_write_build_artifact_inventory() {
  local report="${1:?}"
  local kver="${KEEP_KERNEL_FLAVOR:-unknown}"

  log_step "runtime build artifact inventory"

  {
    echo "=== Recoverix runtime build artifact inventory ==="
    echo "timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "policy: production debootstrap minimal runtime (no host clone by default)"
    echo "runtime_rootfs_source: ${RECOVERIX_RUNTIME_ROOTFS_SOURCE:-unknown}"
    echo "runtime_build_mode: ${RECOVERIX_RUNTIME_BUILD_MODE:-debootstrap}"
    echo "host_rsync_bootstrap: ${RECOVERIX_DEV_HOST_RSYNC_BOOTSTRAP:-0}"
    echo "keep_runtime_rootfs: ${KEEP_RUNTIME_ROOTFS:-1}"
    echo "runtime_user: ${RECOVERIX_RUNTIME_USER:-recoverix}"
    echo "runtime_user_uid: ${RECOVERIX_RUNTIME_UID:-2000}"
    echo "runtime_user_gid: ${RECOVERIX_RUNTIME_GID:-2000}"
    echo "runtime_user_home: ${RECOVERIX_RUNTIME_HOME:-/home/recoverix}"
    echo "runtime_autologin: ${RECOVERIX_RUNTIME_AUTOLOGIN:-1}"
    echo "runtime_allow_sudo: ${RECOVERIX_RUNTIME_ALLOW_SUDO:-0}"
    echo "runtime_user_whitelist: ${RECOVERIX_RUNTIME_USER:-recoverix}"
    echo "runtime_apt_components: ${RUNTIME_APT_COMPONENTS:-main restricted universe multiverse}"
    echo
    echo "--- paths ---"
    echo "ROOTFS=${ROOTFS_RESOLVED:-n/a}"
    echo "RUNTIME_DIR=${RUNTIME_DIR:-n/a}"
    echo "RUNTIME_SQUASHFS=${RUNTIME_SQUASHFS:-n/a}"
    echo "REPORT_DIR=${REPORT_DIR:-n/a}"
    echo
    echo "--- disk ---"
    df -h "${ROOTFS:-/recovery}" 2>/dev/null || true
    echo
    echo "--- rootfs ---"
    if [[ -d "${ROOTFS_RESOLVED:-}" ]]; then
      du -sh "${ROOTFS_RESOLVED}" 2>/dev/null || true
      echo "rootfs_size_gib: $(runtime_rootfs_size_gib "${ROOTFS_RESOLVED}" 2>/dev/null || echo n/a)"
      echo "rootfs_source_marker: $(runtime_read_rootfs_source_marker 2>/dev/null || echo n/a)"
      echo "rootfs_userspace_ok: $(runtime_rootfs_has_minimal_userspace "${ROOTFS_RESOLVED}" && echo yes || echo no)"
      echo "rootfs_host_clone_like: $(runtime_rootfs_looks_like_host_clone "${ROOTFS_RESOLVED}" "$report" && echo yes || echo no)"
    else
      echo "rootfs: missing"
    fi
    echo
    echo "--- squashfs ---"
    if [[ -f "${RUNTIME_SQUASHFS:-}" ]]; then
      ls -la "${RUNTIME_SQUASHFS}" 2>/dev/null || true
      unsquashfs -s "${RUNTIME_SQUASHFS}" 2>/dev/null || true
    else
      echo "squashfs: not built yet"
    fi
    echo
    echo "--- kernel artifacts (in rootfs) ---"
    for f in \
      "${ROOTFS_RESOLVED}/boot/vmlinuz-${kver}" \
      "${ROOTFS_RESOLVED}/boot/initrd.img-${kver}"; do
      if [[ -e "$f" ]]; then
        ls -la "$f"
      else
        echo "missing: $f"
      fi
    done
    echo
    echo "--- recoverix CLIs in rootfs ---"
    local cli
    for cli in \
      recoverix-health-check \
      recoverix-backup-finalize-check \
      recoverix-image-status \
      recoverix-recovery-ui; do
      local p="${ROOTFS_RESOLVED}/usr/local/sbin/${cli}"
      if [[ -x "$p" ]]; then
        echo "ok: ${p}"
      else
        echo "missing: ${p}"
      fi
    done
    echo
    echo "--- runtime user/session ---"
    if [[ -f "${ROOTFS_RESOLVED}/etc/passwd" ]]; then
      grep "^${RECOVERIX_RUNTIME_USER:-recoverix}:" "${ROOTFS_RESOLVED}/etc/passwd" 2>/dev/null || echo "missing: runtime user entry"
    else
      echo "missing: ${ROOTFS_RESOLVED}/etc/passwd"
    fi
    if [[ -d "${ROOTFS_RESOLVED}${RECOVERIX_RUNTIME_HOME:-/home/recoverix}" ]]; then
      ls -ld "${ROOTFS_RESOLVED}${RECOVERIX_RUNTIME_HOME:-/home/recoverix}" 2>/dev/null || true
    else
      echo "missing: ${ROOTFS_RESOLVED}${RECOVERIX_RUNTIME_HOME:-/home/recoverix}"
    fi
    if [[ -f "${ROOTFS_RESOLVED}/etc/gdm3/custom.conf" ]]; then
      sed -n '1,80p' "${ROOTFS_RESOLVED}/etc/gdm3/custom.conf" 2>/dev/null || true
    else
      echo "missing: ${ROOTFS_RESOLVED}/etc/gdm3/custom.conf"
    fi
    echo "--- GUI stack ---"
    echo "display_manager: gdm3"
    if [[ -f "${ROOTFS_RESOLVED}/lib/systemd/system/gdm.service" ]]; then
      echo "gdm_unit: /lib/systemd/system/gdm.service"
    elif [[ -f "${ROOTFS_RESOLVED}/lib/systemd/system/gdm3.service" ]]; then
      echo "gdm_unit: /lib/systemd/system/gdm3.service"
    else
      echo "gdm_unit: missing"
    fi
    if [[ -x "${ROOTFS_RESOLVED}/usr/sbin/gdm3" ]]; then
      echo "gdm_binary: /usr/sbin/gdm3"
    elif [[ -x "${ROOTFS_RESOLVED}/usr/sbin/gdm" ]]; then
      echo "gdm_binary: /usr/sbin/gdm"
    else
      echo "gdm_binary: missing"
    fi
    if [[ -f "${ROOTFS_RESOLVED}/lib/systemd/system/graphical.target" ]]; then
      echo "graphical.target: present"
      chroot "${ROOTFS_RESOLVED}" systemctl get-default 2>/dev/null | sed 's/^/default_target: /' || true
    else
      echo "graphical.target: missing"
    fi
    if [[ -f "$(dirname "${BASH_SOURCE[0]}")/../assets/runtime_gui_packages.txt" ]]; then
      echo "gui_packages: $(grep -v '^#' "$(dirname "${BASH_SOURCE[0]}")/../assets/runtime_gui_packages.txt" | paste -sd, -)"
    fi
    if [[ -f "${ROOTFS_RESOLVED}/var/lib/AccountsService/users/${RECOVERIX_RUNTIME_USER:-recoverix}" ]]; then
      echo "accounts_service: present"
    else
      echo "accounts_service: missing"
    fi
    echo
    echo "--- python platform ---"
    if [[ -d "${ROOTFS_RESOLVED}/usr/local/lib/recoverix" ]]; then
      du -sh "${ROOTFS_RESOLVED}/usr/local/lib/recoverix"/* 2>/dev/null || true
    else
      echo "missing: usr/local/lib/recoverix"
    fi
    echo
    echo "--- recent reports ---"
    ls -lt "${REPORT_DIR}" 2>/dev/null | head -15 || true
  } | tee -a "$report"

  log_pass "artifact inventory written: ${report}"
}
