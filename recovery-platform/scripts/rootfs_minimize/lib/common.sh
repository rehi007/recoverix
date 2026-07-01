#!/usr/bin/env bash
# Shared guards for rootfs-only chroot work (never touch host /).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/config.env"

log() { printf '[rootfs-minimize] %s\n' "$*"; }
die() { printf '[rootfs-minimize] ERROR: %s\n' "$*" >&2; exit 1; }

resolve_rootfs() {
  local candidate="${1:-$ROOTFS}"
  if [[ -z "$candidate" ]]; then
    die "ROOTFS is empty"
  fi
  if [[ ! -d "$candidate" ]]; then
    die "ROOTFS directory does not exist: $candidate"
  fi
  candidate="$(readlink -f "$candidate")"
  case "$candidate" in
    "/" | "/home" | "/usr" | "/var" | "/etc" | "/bin" | "/sbin")
      die "refusing dangerous ROOTFS path: $candidate"
      ;;
  esac
  if [[ ! -f "$candidate/etc/os-release" ]]; then
    die "not a rootfs (missing etc/os-release): $candidate"
  fi
  local host_root
  host_root="$(readlink -f /)"
  if [[ "$candidate" == "$host_root" ]]; then
    die "ROOTFS resolves to host root — aborting"
  fi
  printf '%s' "$candidate"
}

ROOTFS_RESOLVED="$(resolve_rootfs "$ROOTFS")"
REPORT_DIR="${REPORT_DIR:-/recovery/build/reports/rootfs-minimize}"
mkdir -p "$REPORT_DIR"

# Early host safety (re-run in gate for report line)
if [[ -f "${SCRIPT_DIR}/lib/host_safety.sh" ]]; then
  # shellcheck source=lib/host_safety.sh
  source "${SCRIPT_DIR}/lib/host_safety.sh"
  validate_host_safety_lock "$ROOTFS_RESOLVED" ""
fi

chroot_is_mounted() {
  mountpoint -q "${ROOTFS_RESOLVED}/proc" 2>/dev/null
}

mount_chroot_fs() {
  if chroot_is_mounted; then
    return 0
  fi
  log "mounting virtual filesystems under ${ROOTFS_RESOLVED}"
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
  if [[ -d /run ]]; then
    mount -o bind /run "${ROOTFS_RESOLVED}/run"
  fi
  if [[ -d /sys/firmware/efi/efivars ]]; then
    mkdir -p "${ROOTFS_RESOLVED}/sys/firmware/efi/efivars"
    mount -o bind,ro /sys/firmware/efi/efivars "${ROOTFS_RESOLVED}/sys/firmware/efi/efivars" 2>/dev/null || true
  fi
}

umount_chroot_fs() {
  log "unmounting virtual filesystems"
  for m in run sys firmware/efi/efivars proc dev/pts dev; do
    if mountpoint -q "${ROOTFS_RESOLVED}/${m}" 2>/dev/null; then
      umount "${ROOTFS_RESOLVED}/${m}" || true
    fi
  done
}

chroot_run() {
  mount_chroot_fs
  chroot "${ROOTFS_RESOLVED}" /bin/bash -lc "$*"
}

host_dpkg_query() {
  dpkg-query --admindir="${ROOTFS_RESOLVED}/var/lib/dpkg" --root="${ROOTFS_RESOLVED}" "$@"
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "run as root (sudo) — chroot and mount require privileges"
  fi
}
