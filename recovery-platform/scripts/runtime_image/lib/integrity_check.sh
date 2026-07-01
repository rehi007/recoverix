#!/usr/bin/env bash
# Pre-squashfs rootfs integrity checks (read-only on ROOTFS).

# Trap cleanup state (must not use function-local vars — RETURN trap runs after locals unset).
__RT_SQVERIFY_INDEX_FILE=""
__RT_SQVERIFY_TMPDIR=""

_sqverify_cleanup() {
  if [[ -n "${__RT_SQVERIFY_INDEX_FILE:-}" && -f "${__RT_SQVERIFY_INDEX_FILE}" ]]; then
    rm -f "${__RT_SQVERIFY_INDEX_FILE}"
  fi
  if [[ -n "${__RT_SQVERIFY_TMPDIR:-}" && -d "${__RT_SQVERIFY_TMPDIR}" ]]; then
    log "removing extract workspace: ${__RT_SQVERIFY_TMPDIR}"
    rm -rf "${__RT_SQVERIFY_TMPDIR}"
  fi
  __RT_SQVERIFY_INDEX_FILE=""
  __RT_SQVERIFY_TMPDIR=""
}

_runtime_integrity_bug() {
  log_fail "BUG: integrity_check.sh shell error — $*"
}

_runtime_integrity_fail() {
  log_fail "FAIL: runtime integrity validation failed — $*"
}

: "${RT_READ_LOOP_MAX:=200000}"
: "${RUNTIME_SQ_LISTING_TIMEOUT_SEC:=30}"

_rt_phase_enter() {
  log_step "STEP ENTER: $*"
}

_rt_phase_exit() {
  log_step "STEP EXIT : $*"
}

# Bounded line iteration (no unbounded while-read on huge files).
_rt_log_file_head_lines() {
  local file="${1:?}"
  local max_lines="${2:-20}"
  local -a lines=()
  local i=0

  mapfile -t lines < <(head -n "$max_lines" "$file" 2>/dev/null || true)
  for ((i = 0; i < ${#lines[@]}; i++)); do
    [[ -n "${lines[$i]}" ]] && log_info "  ${lines[$i]}"
  done
}

# Single awk pass: raw unsquashfs -ll → normalized index (never read/write same path).
_rt_squashfs_build_index_from_raw() {
  local raw_listing="${1:?}"
  local index_file="${2:?}"

  : >"$index_file"
  awk '
  {
    for (i = 1; i <= NF; i++) {
      if ($i ~ /^squashfs-root\//) {
        path = $i
        sub(/^squashfs-root\/?/, "", path)
        if ($(i + 1) == "->" && (i + 2) <= NF) {
          tgt = $(i + 2)
          for (j = i + 3; j <= NF; j++)
            tgt = tgt " " $j
          print path "\t" tgt
        } else
          print path "\t"
        break
      }
    }
  }' <"$raw_listing" | LC_ALL=C sort -u >"$index_file"
}

runtime_verify_rootfs_integrity() {
  local report="${1:-}"
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  local failures=0

  _rt_report() {
    [[ -n "$report" ]] && printf '%s\n' "$*" >> "$report"
    log "$*"
  }

  _rt_report "=== Rootfs integrity pre-squashfs ==="
  _rt_report "rootfs: ${ROOTFS_RESOLVED}"

  if declare -f runtime_log_kernel_artifact_forensic >/dev/null 2>&1; then
    runtime_log_kernel_artifact_forensic "rootfs_integrity_pre_squashfs" "$report"
    runtime_check_kernel_artifact_path_mismatch "$report"
  fi

  # shellcheck source=initrd_recoverix.sh
  source "$(dirname "${BASH_SOURCE[0]}")/initrd_recoverix.sh"
  local initrd_path
  initrd_path="$(recoverix_initrd_resolve_source "$kver" 2>/dev/null || true)"
  [[ -z "$initrd_path" ]] && initrd_path="${ROOTFS_RESOLVED}/boot/initrd.img-${kver}"

  for f in \
    "${ROOTFS_RESOLVED}/boot/vmlinuz-${kver}" \
    "${initrd_path}" \
    "${ROOTFS_RESOLVED}/lib/modules/${kver}"; do
    if [[ ! -e "$f" ]]; then
      _rt_report "FAIL: missing ${f}"
      failures=$((failures + 1))
    else
      _rt_report "PASS: ${f}"
    fi
  done

  if ! host_dpkg_query -W -f='${Status}' "linux-image-${kver}" 2>/dev/null | grep -q installed; then
    _rt_report "FAIL: linux-image-${kver} not installed in rootfs"
    failures=$((failures + 1))
  fi

  if declare -f runtime_verify_rootfs_amdgpu_kernel_module >/dev/null 2>&1; then
    if ! runtime_verify_rootfs_amdgpu_kernel_module "$report"; then
      _rt_report "FAIL: runtime_verify_rootfs_amdgpu_kernel_module"
      failures=$((failures + 1))
    fi
  else
    _rt_report "FAIL: runtime_verify_rootfs_amdgpu_kernel_module unavailable"
    failures=$((failures + 1))
  fi

  if host_dpkg_query -W -f='${Package}' 'linux-image-*' 2>/dev/null | grep -v "linux-image-${kver}" | grep -q .; then
    _rt_report "WARN: other linux-image packages still present (old kernel reference risk)"
  fi

  if command -v python3 >/dev/null; then
    if chroot "${ROOTFS_RESOLVED}" /bin/bash -c \
      'python3 -c "import gi; gi.require_version(\"Gtk\",\"3.0\"); from gi.repository import Gtk"' 2>/dev/null; then
      _rt_report "PASS: GTK import in rootfs chroot"
    else
      _rt_report "WARN: GTK import check failed in chroot (runtime GUI risk)"
    fi
  fi

  if command -v partclone >/dev/null 2>&1 || \
     chroot "${ROOTFS_RESOLVED}" partclone.ntfs -V &>/dev/null; then
    _rt_report "PASS: partclone available"
  else
    _rt_report "WARN: partclone not verified"
  fi

  [[ $failures -eq 0 ]] && return 0
  return 1
}

runtime_fix_init_symlinks() {
  local failures=0
  local root="${ROOTFS_RESOLVED}"

  log "=== Normalize init symlinks under rootfs ==="
  log "rootfs: ${root}"

  if [[ ! -x "${root}/usr/lib/systemd/systemd" && ! -x "${root}/lib/systemd/systemd" ]]; then
    log "FAIL: no systemd binary under /usr/lib/systemd/systemd or /lib/systemd/systemd in rootfs"
    return 1
  fi

  log "Current init symlinks (before normalize):"
  if [[ -e "${root}/usr/sbin/init" || -L "${root}/usr/sbin/init" ]]; then
    ls -ld "${root}/usr/sbin/init" || true
    readlink "${root}/usr/sbin/init" 2>/dev/null || true
  else
    log "INFO: ${root}/usr/sbin/init missing before normalize"
  fi
  if [[ -e "${root}/sbin/init" || -L "${root}/sbin/init" ]]; then
    ls -ld "${root}/sbin/init" || true
    readlink "${root}/sbin/init" 2>/dev/null || true
  else
    log "INFO: ${root}/sbin/init missing before normalize"
  fi

  rm -f "${root}/usr/sbin/init" "${root}/sbin/init"

  ln -sf ../lib/systemd/systemd "${root}/usr/sbin/init"
  ln -sf ../lib/systemd/systemd "${root}/sbin/init"

  log "Init symlinks after normalize:"
  ls -ld "${root}/usr/sbin/init" "${root}/sbin/init" || true
  readlink "${root}/usr/sbin/init" 2>/dev/null || true
  readlink "${root}/sbin/init" 2>/dev/null || true

  local p full target resolved
  for p in /usr/sbin/init /sbin/init; do
    full="${root}${p}"
    if [[ ! -e "$full" && ! -L "$full" ]]; then
      log "FAIL: ${p} missing after normalize"
      failures=$((failures + 1))
      continue
    fi
    if ! test -e "$full"; then
      log "FAIL: test -e failed for ${p} after normalize"
      failures=$((failures + 1))
    fi

    target="$(readlink "$full" 2>/dev/null || true)"
    if [[ "$target" != "../lib/systemd/systemd" ]]; then
      log "FAIL: ${p} symlink target unexpected (wanted ../lib/systemd/systemd, got: ${target:-NONE})"
      failures=$((failures + 1))
    else
      log "PASS: ${p} symlink target ../lib/systemd/systemd"
    fi

    resolved="$(readlink -f "$full" 2>/dev/null || true)"
    if [[ "$resolved" == "${root}/usr/lib/systemd/systemd" || "$resolved" == "${root}/lib/systemd/systemd" ]]; then
      log "PASS: ${p} resolves to ${resolved}"
    else
      log "FAIL: ${p} resolves outside expected systemd path (${resolved:-UNKNOWN})"
      failures=$((failures + 1))
    fi
  done

  [[ $failures -eq 0 ]] && return 0
  return 1
}

# Recovery Runtime: no host block mounts, fsck, or swap from fstab.
runtime_fix_recovery_fstab() {
  local root="${ROOTFS_RESOLVED}"
  local fstab="${root}/etc/fstab"
  local bak="${root}/etc/fstab.dist-ubuntu.bak"

  log "=== Install Recoverix minimal /etc/fstab (no host fsck/swap) ==="
  log "rootfs: ${root}"

  if [[ -f "$fstab" ]] && ! grep -q 'Recoverix Recovery Runtime' "$fstab" 2>/dev/null; then
    if [[ ! -f "$bak" ]]; then
      cp -a "$fstab" "$bak"
      log "Backed up original fstab -> ${bak}"
    fi
    log "Previous fstab (host-style) summary:"
    grep -v '^#' "$fstab" 2>/dev/null | grep -v '^[[:space:]]*$' || true
  fi

  cat >"$fstab" <<'EOF'
# Recoverix Recovery Runtime — immutable overlay root
# Virtual filesystems only. No host block devices, fsck, or swap.

proc    /proc   proc    defaults,nosuid,nodev,noexec    0  0
sysfs   /sys    sysfs   defaults,nosuid,nodev,noexec    0  0
tmpfs   /tmp    tmpfs   defaults,nosuid,nodev,mode=1777 0  0
EOF

  log "Installed minimal fstab at ${fstab}"
  runtime_verify_recovery_fstab
}

runtime_verify_recovery_fstab() {
  local fstab="${ROOTFS_RESOLVED}/etc/fstab"
  local failures=0

  log "=== Verify Recoverix /etc/fstab ==="

  if [[ ! -f "$fstab" ]]; then
    log "FAIL: missing ${fstab}"
    return 1
  fi

  if ! grep -q 'Recoverix Recovery Runtime' "$fstab"; then
    log "FAIL: fstab is not Recoverix minimal template"
    failures=$((failures + 1))
  fi

  if grep -v '^#' "$fstab" | grep -qE '[[:space:]]swap[[:space:]]'; then
    log "FAIL: fstab contains swap entry"
    failures=$((failures + 1))
  fi
  if grep -v '^#' "$fstab" | grep -q '/swapfile'; then
    log "FAIL: fstab references /swapfile"
    failures=$((failures + 1))
  fi
  if grep -v '^#' "$fstab" | grep -qE 'UUID='; then
    log "FAIL: fstab contains UUID= (host block mount)"
    failures=$((failures + 1))
  fi
  if grep -v '^#' "$fstab" | grep -qE '[[:space:]][12][[:space:]]*$'; then
    log "FAIL: fstab contains fsck pass 1 or 2"
    failures=$((failures + 1))
  fi
  if grep -v '^#' "$fstab" | grep -qE '[[:space:]]ext4[[:space:]]'; then
    log "FAIL: fstab contains ext4 block mount"
    failures=$((failures + 1))
  fi
  if grep -v '^#' "$fstab" | grep -qE '[[:space:]]vfat[[:space:]]'; then
    log "FAIL: fstab contains vfat block mount"
    failures=$((failures + 1))
  fi

  for want in 'proc.*/proc' 'sysfs.*/sys' 'tmpfs.*/tmp'; do
    if grep -v '^#' "$fstab" | grep -qE "$want"; then
      log "PASS: fstab has ${want%%.*} entry"
    else
      log "FAIL: fstab missing ${want%%.*} entry"
      failures=$((failures + 1))
    fi
  done

  log "Current active fstab lines:"
  grep -v '^#' "$fstab" | grep -v '^[[:space:]]*$' || true

  [[ $failures -eq 0 ]] && return 0
  return 1
}

runtime_verify_rootfs_layout_strict() {
  local failures=0

  log "=== Rootfs layout strict check (usrmerge + init/systemd) ==="
  log "rootfs: ${ROOTFS_RESOLVED}"

  local d
  for d in /bin /usr /etc /lib /var; do
    if [[ -d "${ROOTFS_RESOLVED}${d}" ]]; then
      log "PASS: dir exists ${d}"
    else
      log "FAIL: missing dir ${d}"
      failures=$((failures + 1))
    fi
  done

  if [[ -L "${ROOTFS_RESOLVED}/sbin" ]] && [[ "$(readlink "${ROOTFS_RESOLVED}/sbin")" == "usr/sbin" ]]; then
    log "PASS: usrmerge /sbin -> usr/sbin"
  else
    log "FAIL: /sbin is not usr/sbin symlink"
    failures=$((failures + 1))
  fi

  if [[ -L "${ROOTFS_RESOLVED}/bin" ]] && [[ "$(readlink "${ROOTFS_RESOLVED}/bin")" == "usr/bin" ]]; then
    log "PASS: usrmerge /bin -> usr/bin"
  else
    log "FAIL: /bin is not usr/bin symlink"
    failures=$((failures + 1))
  fi

  if [[ -L "${ROOTFS_RESOLVED}/lib" ]] && [[ "$(readlink "${ROOTFS_RESOLVED}/lib")" == "usr/lib" ]]; then
    log "PASS: usrmerge /lib -> usr/lib"
  else
    log "FAIL: /lib is not usr/lib symlink"
    failures=$((failures + 1))
  fi

  if [[ -x "${ROOTFS_RESOLVED}/usr/lib/systemd/systemd" ]]; then
    log "PASS: /usr/lib/systemd/systemd exists and executable"
  else
    log "FAIL: missing or non-executable /usr/lib/systemd/systemd"
    failures=$((failures + 1))
  fi

  local p
  for p in /usr/sbin/init /sbin/init; do
    if [[ -L "${ROOTFS_RESOLVED}${p}" ]]; then
      local target
      target="$(readlink "${ROOTFS_RESOLVED}${p}" || true)"
      if [[ "$target" == "../lib/systemd/systemd" || "$target" == "/lib/systemd/systemd" ]]; then
        local resolved
        resolved="$(readlink -f "${ROOTFS_RESOLVED}${p}" 2>/dev/null || true)"
        if [[ "$resolved" == "${ROOTFS_RESOLVED}/usr/lib/systemd/systemd" || "$resolved" == "${ROOTFS_RESOLVED}/lib/systemd/systemd" ]]; then
          log "PASS: ${p} symlink resolves to systemd (${target} -> ${resolved})"
        else
          log "FAIL: ${p} symlink resolves outside rootfs (${target} -> ${resolved:-UNKNOWN})"
          failures=$((failures + 1))
        fi
      else
        log "FAIL: ${p} symlink target unexpected: ${target:-NONE}"
        failures=$((failures + 1))
      fi
    elif [[ -x "${ROOTFS_RESOLVED}${p}" ]]; then
      log "PASS: ${p} exists and executable (non-symlink)"
    else
      log "FAIL: ${p} missing"
      failures=$((failures + 1))
    fi
  done

  [[ $failures -eq 0 ]] && return 0
  return 1
}

# shellcheck source=firmware_provision.sh
source "$(dirname "${BASH_SOURCE[0]}")/firmware_provision.sh"
# shellcheck source=staging_guard.sh
source "$(dirname "${BASH_SOURCE[0]}")/staging_guard.sh"
# shellcheck source=runtime_tools_install.sh
source "$(dirname "${BASH_SOURCE[0]}")/runtime_tools_install.sh"
# shellcheck source=runtime_python_install.sh
source "$(dirname "${BASH_SOURCE[0]}")/runtime_python_install.sh"
# shellcheck source=recovery_ui_install.sh
source "$(dirname "${BASH_SOURCE[0]}")/recovery_ui_install.sh"

# Small workspace for targeted unsquashfs path extract (not full image).
runtime_squashfs_verify_workspace_mkdir() {
  local tmpdir base
  local -a bases

  bases=(
    "${RECOVERY_SQUASHFS_VERIFY_TMP:-/recovery/tmp}"
    /var/tmp
    /tmp
  )

  for base in "${bases[@]}"; do
    if mkdir -p "$base" 2>/dev/null; then
      tmpdir="$(mktemp -d "${base%/}/recoverix-sqverify.XXXXXX" 2>/dev/null)" && {
        log "workspace: ${tmpdir}" >&2
        printf '%s' "$tmpdir"
        return 0
      }
    fi
  done

  log "FAIL: cannot create temporary workspace for targeted squashfs extract"
  return 1
}

# Required paths that must appear in unsquashfs -ll listing (squashfs-root/... lines).
RUNTIME_SQUASHFS_LISTING_REQUIRED_PATHS=(
  usr/local/sbin/recoverix-backup-finalize-check
  usr/sbin/init
  usr/lib/systemd/systemd
)

# Verify unsquashfs -ll: raw_listing.txt + index.txt (separate files), timeout, no unbounded while-read.
# Args: sq raw_listing index_file
runtime_verify_unsquashfs_listing() {
  local sq="${1:?}"
  local raw_listing="${2:?}"
  local index_file="${3:?}"
  local rc=0
  local relpath missing=0
  local timeout_sec="${RUNTIME_SQ_LISTING_TIMEOUT_SEC:-30}"

  _rt_phase_enter "capture squashfs raw listing"
  if command -v timeout >/dev/null 2>&1; then
    timeout "${timeout_sec}s" unsquashfs -ll "$sq" >"$raw_listing" 2>&1
    rc=$?
    if [[ $rc -eq 124 ]]; then
      _runtime_integrity_fail "unsquashfs -ll timed out after ${timeout_sec}s"
      _rt_phase_exit "capture squashfs raw listing"
      return 1
    fi
  else
    unsquashfs -ll "$sq" >"$raw_listing" 2>&1
    rc=$?
  fi
  _rt_phase_exit "capture squashfs raw listing"

  if [[ $rc -ne 0 ]]; then
    _runtime_integrity_fail "unsquashfs -ll read failure (rc=${rc})"
    [[ -s "$raw_listing" ]] && _rt_log_file_head_lines "$raw_listing" 5
    return 1
  fi

  if [[ ! -s "$raw_listing" ]]; then
    _runtime_integrity_fail "unsquashfs -ll produced empty raw listing"
    return 1
  fi

  _rt_phase_enter "build listing index"
  _rt_squashfs_build_index_from_raw "$raw_listing" "$index_file"
  _rt_phase_exit "build listing index"

  if [[ ! -s "$index_file" ]]; then
    _runtime_integrity_fail "squashfs listing index empty after awk/sort"
    return 1
  fi

  RUNTIME_BOOT_SQ_RAW_LISTING="$raw_listing"
  RUNTIME_BOOT_SQ_INDEX="$index_file"
  RUNTIME_BOOT_SQ_LISTING_VERIFIED=1
  export RUNTIME_BOOT_SQ_RAW_LISTING RUNTIME_BOOT_SQ_INDEX RUNTIME_BOOT_SQ_LISTING_VERIFIED

  for relpath in "${RUNTIME_SQUASHFS_LISTING_REQUIRED_PATHS[@]}"; do
    if grep -qF "$relpath" "$index_file" 2>/dev/null; then
      log_info "index required path present: ${relpath}"
    else
      _runtime_integrity_fail "required path missing from squashfs index: ${relpath}"
      missing=$((missing + 1))
    fi
  done

  if [[ $missing -gt 0 ]]; then
    return 1
  fi

  _rt_phase_enter "listing preview"
  log_info "unsquashfs -ll listing preview (head -n 20):"
  _rt_log_file_head_lines "$raw_listing" 20
  log_pass "listing preview complete"
  _rt_phase_exit "listing preview"

  return 0
}

# One-pass unsquashfs -ll → index file (raw + index paths must differ).
runtime_squashfs_ll_build_index() {
  local sq="${1:?}"
  local sq_index_file="${2:?}"
  local raw_listing="${sq_index_file%.index.txt}.raw_listing.txt"
  local rc=0
  local timeout_sec="${RUNTIME_SQ_LISTING_TIMEOUT_SEC:-30}"

  if [[ "$raw_listing" == "$sq_index_file" ]]; then
    raw_listing="${sq_index_file}.raw_listing.txt"
  fi

  _rt_phase_enter "squashfs listing index for layout check"
  if command -v timeout >/dev/null 2>&1; then
    timeout "${timeout_sec}s" unsquashfs -ll "$sq" >"$raw_listing" 2>&1
    rc=$?
  else
    unsquashfs -ll "$sq" >"$raw_listing" 2>&1
    rc=$?
  fi
  if [[ $rc -ne 0 ]]; then
    _runtime_integrity_fail "unsquashfs -ll read failure (rc=${rc}): ${sq}"
    rm -f "$raw_listing"
    _rt_phase_exit "squashfs listing index for layout check"
    return 1
  fi

  _rt_squashfs_build_index_from_raw "$raw_listing" "$sq_index_file"
  _rt_phase_exit "squashfs listing index for layout check"

  if [[ ! -s "$sq_index_file" ]]; then
    _runtime_integrity_fail "unsquashfs -ll produced empty listing index: ${sq}"
    return 1
  fi
  return 0
}

# Exact path lookup in index (path without leading slash, e.g. usr/sbin/init).
runtime_squashfs_index_lookup() {
  local sq_index_file="${1:?}"
  local relpath="${2#/}"
  local line

  line="$(awk -F '\t' -v p="$relpath" '$1 == p { print; exit }' "$sq_index_file" 2>/dev/null || true)"
  if [[ -z "$line" ]]; then
    return 1
  fi
  printf '%s' "$line"
  return 0
}

runtime_squashfs_log_index_paths() {
  local sq_index_file="${1:?}"
  shift
  local relpath line path tgt

  for relpath in "$@"; do
    relpath="${relpath#/}"
    if line="$(runtime_squashfs_index_lookup "$sq_index_file" "$relpath")"; then
      path="${line%%	*}"
      tgt="${line#*	}"
      if [[ -n "$tgt" ]]; then
        log "INFO: squashfs path /${path} -> ${tgt}"
      else
        log "INFO: squashfs path /${path}"
      fi
    else
      log "INFO: squashfs path /${relpath} (not in listing index)"
    fi
  done
}

# Validate required layout from unsquashfs -ll index (squashfs read OK; no full extract needed).
runtime_squashfs_verify_layout_from_index() {
  local sq_index_file="${1:?}"
  local failures=0
  local relpath line path tgt link want pair

  for relpath in usr/lib/systemd/systemd usr/sbin/init etc/fstab; do
    if runtime_squashfs_index_lookup "$sq_index_file" "$relpath"; then
      log "PASS: squashfs index has /${relpath}"
    else
      _runtime_integrity_fail "squashfs missing required path /${relpath} (listing index)"
      failures=$((failures + 1))
    fi
  done

  for pair in sbin:usr/sbin bin:usr/bin lib:usr/lib; do
    link="${pair%%:*}"
    want="${pair#*:}"
    if line="$(runtime_squashfs_index_lookup "$sq_index_file" "$link")"; then
      tgt="${line#*	}"
      if [[ "$tgt" == "$want" ]]; then
        log "PASS: squashfs index usrmerge /${link} -> ${want}"
      else
        _runtime_integrity_fail "squashfs /${link} index target ${tgt:-NONE} (expected ${want})"
        failures=$((failures + 1))
      fi
    else
      _runtime_integrity_fail "squashfs index missing /${link}"
      failures=$((failures + 1))
    fi
  done

  if line="$(runtime_squashfs_index_lookup "$sq_index_file" "usr/sbin/init")"; then
    tgt="${line#*	}"
    case "${tgt}" in
      ../lib/systemd/systemd | /lib/systemd/systemd) ;;
      *)
        _runtime_integrity_fail "/usr/sbin/init index symlink unexpected: ${tgt:-NONE}"
        failures=$((failures + 1))
        ;;
    esac
  fi

  [[ $failures -eq 0 ]] || return 1
  log_pass "squashfs layout verified from listing index (read-only)"
  return 0
}

runtime_verify_squashfs_layout_strict() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local failures=0
  local tmpdir=""
  local sq_index_file=""
  local meta tgt resolved link want pair
  local -a extract_paths=(
    usr/lib/systemd/systemd
    usr/sbin/init
    etc/fstab
    sbin
    bin
    lib
  )
  local -a index_paths=(
    sbin
    bin
    lib
    usr/sbin/init
    sbin/init
    usr/lib/systemd/systemd
    etc/fstab
  )

  log "=== Squashfs layout strict check (lightweight: metadata + partial extract) ==="
  log "squashfs: ${sq}"

  if [[ ! -f "$sq" ]]; then
    _runtime_integrity_fail "squashfs image missing: ${sq}"
    return 1
  fi

  meta="$(unsquashfs -s "$sq" 2>/dev/null || true)"
  if [[ -z "$meta" ]]; then
    _runtime_integrity_fail "unsquashfs cannot read squashfs metadata (squashfs read failure)"
    return 1
  fi
  log "PASS: squashfs metadata readable (unsquashfs -s)"
  {
    local _meta_tmp _n=0 _line
    _meta_tmp="$(mktemp "${TMPDIR:-/tmp}/recoverix-meta.XXXXXX")"
    printf '%s\n' "$meta" >"$_meta_tmp"
    while IFS= read -r _line && [[ $_n -lt 8 ]]; do
      [[ -n "$_line" ]] && log "INFO: ${_line}"
      _n=$((_n + 1))
    done <"$_meta_tmp"
    rm -f "$_meta_tmp"
  }

  tmpdir="$(runtime_squashfs_verify_workspace_mkdir)" || return 1
  __RT_SQVERIFY_TMPDIR="$tmpdir"
  sq_index_file="${tmpdir}/.squashfs-path-index"
  __RT_SQVERIFY_INDEX_FILE="$sq_index_file"
  trap _sqverify_cleanup RETURN

  log "building normalized path index (unsquashfs -ll, one pass)..."
  if ! runtime_squashfs_ll_build_index "$sq" "$sq_index_file"; then
    return 1
  fi
  log "PASS: squashfs listing readable (unsquashfs -ll)"
  runtime_squashfs_log_index_paths "$sq_index_file" "${index_paths[@]}"

  log "targeted extract paths: ${extract_paths[*]}"
  if unsquashfs -f -d "${tmpdir}" "$sq" "${extract_paths[@]}" >>"${tmpdir}/extract.log" 2>&1; then
    log "PASS: targeted unsquashfs extract OK"
  else
    log_warn "WARN: targeted unsquashfs extract failed — squashfs read OK; validating layout from listing index"
    log_warn "WARN: extract diagnostic: ${tmpdir}/extract.log (overlay/verify-mnt issues are separate)"
    if [[ -s "${tmpdir}/extract.log" ]]; then
      _rt_log_file_head_lines "${tmpdir}/extract.log" 8
    fi
    if runtime_squashfs_verify_layout_from_index "$sq_index_file"; then
      log_pass "squashfs layout OK via index (extract skipped)"
      return 0
    fi
    _runtime_integrity_fail "squashfs layout missing required paths in listing index"
    return 1
  fi

  # --- usrmerge (from extracted symlinks; authoritative) ---
  for pair in sbin:usr/sbin bin:usr/bin lib:usr/lib; do
    link="${pair%%:*}"
    want="${pair#*:}"
    if [[ -L "${tmpdir}/${link}" ]] && [[ "$(readlink "${tmpdir}/${link}")" == "${want}" ]]; then
      log "PASS: usrmerge /${link} -> ${want} (extracted)"
    else
      _runtime_integrity_fail "usrmerge /${link} expected -> ${want}"
      failures=$((failures + 1))
    fi
  done

  # --- systemd (extracted file) ---
  if test -e "${tmpdir}/usr/lib/systemd/systemd"; then
    log "PASS: extracted /usr/lib/systemd/systemd"
  else
    _runtime_integrity_fail "extracted /usr/lib/systemd/systemd missing"
    failures=$((failures + 1))
  fi

  # --- init: /usr/sbin/init and /sbin/init via usrmerge ---
  if test -e "${tmpdir}/usr/sbin/init"; then
    log "PASS: extracted /usr/sbin/init"
  else
    _runtime_integrity_fail "extracted /usr/sbin/init missing"
    failures=$((failures + 1))
  fi

  if test -L "${tmpdir}/usr/sbin/init"; then
    tgt="$(readlink "${tmpdir}/usr/sbin/init" 2>/dev/null || true)"
    log "INFO: extracted /usr/sbin/init -> ${tgt:-NONE}"
    resolved="$(readlink -f "${tmpdir}/usr/sbin/init" 2>/dev/null || true)"
    log "INFO: readlink -f /usr/sbin/init -> ${resolved:-NONE}"
    case "${tgt}" in
      ../lib/systemd/systemd | /lib/systemd/systemd) ;;
      *)
        _runtime_integrity_fail "/usr/sbin/init symlink target unexpected: ${tgt:-NONE}"
        failures=$((failures + 1))
        ;;
    esac
    if [[ -n "$resolved" ]] && [[ "$resolved" == *"/usr/lib/systemd/systemd" || "$resolved" == *"/lib/systemd/systemd" ]]; then
      log "PASS: /usr/sbin/init resolves to systemd"
    else
      _runtime_integrity_fail "/usr/sbin/init does not resolve to systemd (${resolved:-NONE})"
      failures=$((failures + 1))
    fi
  else
    _runtime_integrity_fail "/usr/sbin/init is not a symlink after extract"
    failures=$((failures + 1))
  fi

  if test -e "${tmpdir}/sbin/init"; then
    log "PASS: /sbin/init reachable after extract"
    resolved="$(readlink -f "${tmpdir}/sbin/init" 2>/dev/null || true)"
    log "INFO: readlink -f /sbin/init -> ${resolved:-NONE}"
  elif test -L "${tmpdir}/sbin" && test -e "${tmpdir}/usr/sbin/init"; then
    log "PASS: /sbin/init via usrmerge (/sbin -> usr/sbin, usr/sbin/init present)"
  else
    _runtime_integrity_fail "/sbin/init not reachable after extract"
    failures=$((failures + 1))
  fi

  if test -f "${tmpdir}/etc/fstab"; then
    log "PASS: extracted /etc/fstab"
    if grep -q 'Recoverix Recovery Runtime' "${tmpdir}/etc/fstab" 2>/dev/null; then
      log "PASS: fstab is Recoverix minimal template"
    elif grep -v '^#' "${tmpdir}/etc/fstab" | grep -qE 'UUID=|/swapfile|[[:space:]]swap[[:space:]]'; then
      log "WARN: fstab may still contain host block/swap entries"
    fi
  else
    _runtime_integrity_fail "extracted /etc/fstab missing"
    failures=$((failures + 1))
  fi

  if [[ $failures -eq 0 ]]; then
    log_pass "squashfs layout strict check OK (extract path)"
    return 0
  fi
  _runtime_integrity_fail "squashfs layout check failed (${failures} issue(s))"
  return 1
}
