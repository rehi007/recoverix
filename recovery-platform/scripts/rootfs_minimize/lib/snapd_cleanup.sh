#!/usr/bin/env bash
# Chroot-safe snapd cleanup — ROOTFS only, never host snapd / host namespace.

SNAPD_CLEANUP_RC_SUCCESS=0
SNAPD_CLEANUP_RC_WARN=1
SNAPD_CLEANUP_RC_FAIL=2

# True if path resolves under ROOTFS (never host /).
_snapd_under_rootfs() {
  local p="$1"
  local resolved
  resolved="$(readlink -f "$p" 2>/dev/null || echo "$p")"
  [[ "$resolved" == "${ROOTFS_RESOLVED}"* ]]
}

_snapd_report() {
  local report="$1"
  shift
  printf '%s\n' "$*" >> "$report"
  log "$*"
}

# Mount dev/proc/sys for chroot WITHOUT binding host /run (avoids host snap ns in purge).
snapd_mount_chroot_no_host_run() {
  if chroot_is_mounted; then
    snapd_unbind_host_run_from_rootfs "prepare" >/dev/null 2>&1 || true
    return 0
  fi
  log "mounting chroot FS (no host /run bind) under ${ROOTFS_RESOLVED}"
  mkdir -p \
    "${ROOTFS_RESOLVED}/dev" \
    "${ROOTFS_RESOLVED}/dev/pts" \
    "${ROOTFS_RESOLVED}/proc" \
    "${ROOTFS_RESOLVED}/sys" \
    "${ROOTFS_RESOLVED}/run"
  mount -o bind /dev "${ROOTFS_RESOLVED}/dev"
  mount -o bind /dev/pts "${ROOTFS_RESOLVED}/dev/pts"
  mount -t proc proc "${ROOTFS_RESOLVED}/proc"
  mount -t sysfs sysfs "${ROOTFS_RESOLVED}/sys"
  mkdir -p "${ROOTFS_RESOLVED}/run/snapd"
  # Intentionally do NOT: mount -o bind /run "${ROOTFS_RESOLVED}/run"
}

snapd_chroot_run() {
  snapd_mount_chroot_no_host_run
  chroot "${ROOTFS_RESOLVED}" /bin/bash -lc "$*"
}

# Unbind only ${ROOTFS}/run when it is a bind mount from host /run.
snapd_unbind_host_run_from_rootfs() {
  local report="${1:-}"
  local mp="${ROOTFS_RESOLVED}/run"
  local src tgt

  if ! mountpoint -q "$mp" 2>/dev/null; then
    [[ -n "$report" ]] && _snapd_report "$report" "run_bind: not mounted at ${mp}"
    return 0
  fi

  src="$(findmnt -n -o SOURCE -- "$mp" 2>/dev/null || true)"
  tgt="$(findmnt -n -o TARGET -- "$mp" 2>/dev/null || true)"

  # Host leak: target is under rootfs but source is outside rootfs tree.
  if [[ -n "$src" && "$src" != "${ROOTFS_RESOLVED}"* ]]; then
    [[ -n "$report" ]] && _snapd_report "$report" "run_bind: DETECTED host leak src=${src} -> ${tgt}"
    umount -l "$mp" 2>/dev/null || umount "$mp" 2>/dev/null || {
      [[ -n "$report" ]] && _snapd_report "$report" "run_bind: WARN lazy umount failed for ${mp}"
      return 1
    }
    [[ -n "$report" ]] && _snapd_report "$report" "run_bind: unbound ${mp} from host namespace (host /run untouched)"
    mkdir -p "${ROOTFS_RESOLVED}/run/snapd"
    return 0
  fi

  [[ -n "$report" ]] && _snapd_report "$report" "run_bind: ${mp} source=${src} (no host leak)"
  return 0
}

# List mount targets under ROOTFS related to snap (never umount host paths).
snapd_detect_rootfs_snap_mounts() {
  local report="${1:-}"
  if ! command -v findmnt >/dev/null 2>&1; then
    return 0
  fi
  while IFS= read -r src tgt; do
    [[ -z "$tgt" ]] && continue
    _snapd_under_rootfs "$tgt" || continue
    case "$tgt" in
      *snapd*|*/snap/*|*/snap) ;;
      *) continue ;;
    esac
    [[ -n "$report" ]] && _snapd_report "$report" "detected_mount: src=${src} tgt=${tgt}"
    printf '%s\n' "$tgt"
  done < <(findmnt -rn -o SOURCE,TARGET 2>/dev/null || true)
}

snapd_lazy_umount_rootfs_snap_mounts() {
  local report="$1"
  local tgt
  while IFS= read -r tgt; do
    [[ -z "$tgt" ]] && continue
    _snapd_under_rootfs "$tgt" || continue
    if mountpoint -q "$tgt" 2>/dev/null; then
      umount -l "$tgt" 2>/dev/null || umount "$tgt" 2>/dev/null || true
      _snapd_report "$report" "lazy_umount: ${tgt}"
    fi
  done < <(snapd_detect_rootfs_snap_mounts "$report")
}

snapd_detect_artifacts() {
  local report="$1"
  local p
  for p in \
    "${ROOTFS_RESOLVED}/run/snapd/ns" \
    "${ROOTFS_RESOLVED}/run/snapd" \
    "${ROOTFS_RESOLVED}/var/lib/snapd" \
    "${ROOTFS_RESOLVED}/var/snap" \
    "${ROOTFS_RESOLVED}/snap"; do
    if [[ -e "$p" ]]; then
      _snapd_report "$report" "artifact: ${p}"
    fi
  done
  if [[ -d "${ROOTFS_RESOLVED}/run/snapd/ns" ]]; then
    find "${ROOTFS_RESOLVED}/run/snapd/ns" -maxdepth 1 -type l -o -type d 2>/dev/null | while read -r ns; do
      [[ -n "$ns" ]] && _snapd_report "$report" "namespace_entry: ${ns}"
    done
  fi
}

snapd_force_remove_data_dirs() {
  local report="$1"
  local d
  for d in \
    "${ROOTFS_RESOLVED}/var/lib/snapd" \
    "${ROOTFS_RESOLVED}/var/snap" \
    "${ROOTFS_RESOLVED}/snap" \
    "${ROOTFS_RESOLVED}/run/snapd/ns" \
    "${ROOTFS_RESOLVED}/run/snapd"; do
    if [[ -e "$d" ]]; then
      rm -rf "$d" 2>/dev/null || true
      _snapd_report "$report" "removed_path: ${d}"
    fi
  done
  mkdir -p "${ROOTFS_RESOLVED}/run/snapd"
}

snapd_dpkg_repair() {
  local report="$1"
  local rc=0
  _snapd_report "$report" "dpkg_repair: dpkg --configure -a"
  snapd_chroot_run "DEBIAN_FRONTEND=noninteractive dpkg --configure -a" >>"${report}" 2>&1 || rc=$?
  _snapd_report "$report" "dpkg_repair: apt -f install -y"
  snapd_chroot_run "DEBIAN_FRONTEND=noninteractive apt-get -y -f install" >>"${report}" 2>&1 || rc=$?
  return "$rc"
}

snapd_dpkg_force_remove_package() {
  local report="$1"
  local rc=0
  _snapd_report "$report" "dpkg_force: attempting --remove --force-remove-reinstreq snapd"
  snapd_chroot_run "DEBIAN_FRONTEND=noninteractive dpkg --remove --force-remove-reinstreq --force-depends snapd" \
    >>"${report}" 2>&1 || rc=$?

  local info_dir="${ROOTFS_RESOLVED}/var/lib/dpkg/info"
  local status_file="${ROOTFS_RESOLVED}/var/lib/dpkg/status"
  if [[ $rc -ne 0 ]]; then
    _snapd_report "$report" "dpkg_force: scrubbing snapd info files under rootfs dpkg admin"
    rm -f "${info_dir}"/snapd.* 2>/dev/null || true
    if [[ -f "$status_file" ]] && grep -q '^Package: snapd$' "$status_file" 2>/dev/null; then
      sed -i '/^Package: snapd$/,/^$/d' "$status_file" 2>/dev/null || true
      _snapd_report "$report" "dpkg_force: removed snapd stanza from rootfs status"
    fi
    snapd_dpkg_repair "$report" || true
  fi
  return "$rc"
}

snapd_validate_clean() {
  local report="$1"
  local ok=0

  if host_dpkg_query -W -f='${Status}' snapd 2>/dev/null | grep -q "install ok installed"; then
    _snapd_report "$report" "validation: WARN snapd still listed installed in rootfs dpkg"
    ok=1
  else
    _snapd_report "$report" "validation: PASS snapd not installed in rootfs"
  fi

  local d
  for d in "${ROOTFS_RESOLVED}/var/lib/snapd" "${ROOTFS_RESOLVED}/snap"; do
    if [[ -d "$d" ]] && [[ -n "$(ls -A "$d" 2>/dev/null)" ]]; then
      _snapd_report "$report" "validation: WARN non-empty ${d}"
      ok=1
    fi
  done

  if mountpoint -q "${ROOTFS_RESOLVED}/run" 2>/dev/null; then
    local src
    src="$(findmnt -n -o SOURCE -- "${ROOTFS_RESOLVED}/run" 2>/dev/null || true)"
    if [[ -n "$src" && "$src" != "${ROOTFS_RESOLVED}"* ]] && [[ -d "${ROOTFS_RESOLVED}/run/snapd/ns" ]]; then
      _snapd_report "$report" "validation: WARN host /run still bound with snapd/ns visible"
      ok=1
    fi
  fi

  return "$ok"
}

# Full prepare before apt purge (call when snapd is in purge list).
snapd_cleanup_prepare() {
  local report="$1"
  {
    echo "=== Snapd chroot-safe cleanup prepare ==="
    echo "timestamp: $(date -u +%Y%m%dT%H%M%SZ)"
    echo "rootfs: ${ROOTFS_RESOLVED}"
    echo "policy: never modify host snapd or host /run namespace"
    echo
  } >> "$report"

  snapd_unbind_host_run_from_rootfs "$report"
  snapd_detect_artifacts "$report"
  snapd_lazy_umount_rootfs_snap_mounts "$report"
  snapd_force_remove_data_dirs "$report"
  mkdir -p "${ROOTFS_RESOLVED}/run/snapd"
  _snapd_report "$report" "prepare: complete"
}

# Purge snapd with recovery; non-fatal to minimize apply (returns WARN on recoverable issues).
snapd_purge_with_recovery() {
  local report="$1"
  local purge_rc=0

  snapd_cleanup_prepare "$report"

  _snapd_report "$report" "purge: apt-get purge snapd (chroot, no host /run bind)"
  set +e
  snapd_chroot_run "DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::='--force-confold' purge snapd" \
    >>"${report}" 2>&1
  purge_rc=$?
  set -u

  if [[ $purge_rc -ne 0 ]]; then
    _snapd_report "$report" "purge: WARN apt purge snapd failed (rc=${purge_rc}) — recoverable cleanup"
    snapd_unbind_host_run_from_rootfs "$report"
    snapd_lazy_umount_rootfs_snap_mounts "$report"
    snapd_force_remove_data_dirs "$report"
    snapd_dpkg_force_remove_package "$report"
    snapd_dpkg_repair "$report"
  fi

  snapd_force_remove_data_dirs "$report"
  snapd_dpkg_repair "$report"

  if snapd_validate_clean "$report"; then
    _snapd_report "$report" "result: RECOVERABLE_WARN — review validation lines"
    return "$SNAPD_CLEANUP_RC_WARN"
  fi

  _snapd_report "$report" "result: SUCCESS"
  return "$SNAPD_CLEANUP_RC_SUCCESS"
}
