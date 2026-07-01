#!/usr/bin/env bash
# Stage Recoverix boot artifacts to Recovery Linux /boot/recoverix (additive; host initrd untouched).
# Usage: sudo RECOVERY_LINUX_UUID=<p4-uuid> ./30_stage_host_boot.sh
#
# Does NOT: grub-install, efibootmgr, update-grub, EFI/shim changes.

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RI_DIR="$(cd "${DIR}/.." && pwd)"
# shellcheck source=../lib/common.sh
source "${RI_DIR}/lib/common.sh"
# shellcheck source=../lib/initrd_recoverix.sh
source "${RI_DIR}/lib/initrd_recoverix.sh"
# shellcheck source=lib/host_grub_safety.sh
source "${DIR}/lib/host_grub_safety.sh"

host_grub_require_root
host_grub_assert_safe_command "$0"

KVER="${KEEP_KERNEL_FLAVOR}"
INITRD_BASENAME="$(recoverix_initrd_basename "$KVER")"
ROOTFS_BOOT="${ROOTFS_RESOLVED}/boot"
RUNTIME_SQ="${RUNTIME_SQUASHFS}"
BUILD_STAMP="${RUNTIME_DIR}/latest_runtime_build.stamp"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/host_stage_${TS}.txt"
mkdir -p "${REPORT_DIR}"

RECOVERY_LINUX_STAGE_MP=""
STAGED_MOUNT_BY_US=0
TARGET_IS_CURRENT_ROOT=no
STAGED_TO_RECOVERY_LINUX_PARTITION=no
HANDOFF_PARAM_CONF_VERIFIED=no
INITRD_SOURCE_SHA256=""
INITRD_TARGET_SHA256=""

log() { printf '[recoverix-deploy] %s\n' "$*"; }

host_stage_cleanup_mount() {
  if [[ "$STAGED_MOUNT_BY_US" == 1 && -n "$RECOVERY_LINUX_STAGE_MP" ]]; then
    if mountpoint -q "$RECOVERY_LINUX_STAGE_MP" 2>/dev/null; then
      umount "$RECOVERY_LINUX_STAGE_MP" 2>/dev/null || \
        log "WARN: umount ${RECOVERY_LINUX_STAGE_MP} failed (check manually)"
    fi
    rmdir "$RECOVERY_LINUX_STAGE_MP" 2>/dev/null || true
    STAGED_MOUNT_BY_US=0
  fi
}

trap host_stage_cleanup_mount EXIT

host_stage_sha256() {
  sha256sum "$1" 2>/dev/null | awk '{print $1}'
}

host_stage_verify_initrd_handoff_param_conf() {
  local initrd="$1"
  local label="${2:-initrd}"
  local tmp handoff

  if ! command -v unmkinitramfs >/dev/null 2>&1; then
    host_grub_die "unmkinitramfs required to verify ${label} handoff (install initramfs-tools)"
  fi

  tmp="$(mktemp -d "${TMPDIR:-/tmp}/recoverix-initrd-verify.XXXXXX")"
  if ! unmkinitramfs "$initrd" "$tmp" 2>/dev/null; then
    rm -rf "$tmp"
    host_grub_die "unmkinitramfs failed for ${label}: ${initrd}"
  fi

  handoff="${tmp}/scripts/init-bottom/00-recoverix-handoff"
  if [[ ! -f "$handoff" ]]; then
    rm -rf "$tmp"
    host_grub_die "${label} missing scripts/init-bottom/00-recoverix-handoff"
  fi
  if ! grep -qF 'recoverix_write_param_conf' "$handoff"; then
    rm -rf "$tmp"
    host_grub_die "${label} handoff missing recoverix_write_param_conf — rebuild initrd (20_install)"
  fi
  if ! grep -qF 'Recoverix handoff wrote /conf/param.conf' "$handoff"; then
    rm -rf "$tmp"
    host_grub_die "${label} handoff missing param.conf milestone — rebuild initrd (20_install)"
  fi
  rm -rf "$tmp"
  return 0
}

host_stage_wait_block_uuid() {
  local uuid="$1"
  local dev="/dev/disk/by-uuid/${uuid}"
  local count=0
  while [[ ! -e "$dev" ]] && [[ $count -lt 300 ]]; do
    sleep 0.1
    count=$((count + 1))
  done
  [[ -e "$dev" ]] || return 1
  printf '%s' "$dev"
}

host_stage_resolve_recovery_linux_boot() {
  local uuid="$1"
  local host_uuid="$2"
  local mp="" dev="" existing_mp=""

  if [[ -z "$uuid" ]]; then
    host_grub_die "RECOVERY_LINUX_UUID is empty — set to Recovery Linux (p4) partition UUID"
  fi

  if [[ "$host_uuid" == "$uuid" ]]; then
    STAGING_BOOT="/boot/recoverix"
    RECOVERY_LINUX_MOUNTPOINT="/"
    TARGET_IS_CURRENT_ROOT=yes
    STAGED_TO_RECOVERY_LINUX_PARTITION=yes
    log "Recovery Linux UUID matches host root — staging to ${STAGING_BOOT}"
    return 0
  fi

  TARGET_IS_CURRENT_ROOT=no

  existing_mp="$(findmnt -rn -o TARGET -U "$uuid" 2>/dev/null | head -1 || true)"
  if [[ -n "$existing_mp" ]]; then
    RECOVERY_LINUX_MOUNTPOINT="${existing_mp}"
    STAGING_BOOT="${existing_mp%/}/boot/recoverix"
    STAGED_TO_RECOVERY_LINUX_PARTITION=yes
    log "Recovery Linux already mounted at ${RECOVERY_LINUX_MOUNTPOINT}"
    return 0
  fi

  dev="$(host_stage_wait_block_uuid "$uuid")" || \
    host_grub_die "block device not found for RECOVERY_LINUX_UUID=${uuid}"

  if ! blkid -o value -s UUID "$dev" 2>/dev/null | grep -qxF "$uuid"; then
    host_grub_die "device ${dev} does not match UUID ${uuid}"
  fi

  RECOVERY_LINUX_STAGE_MP="$(mktemp -d /mnt/recoverix-linux-stage.XXXXXX 2>/dev/null || mktemp -d "${TMPDIR:-/tmp}/recoverix-linux-stage.XXXXXX")"
  log "mounting Recovery Linux ${uuid} at ${RECOVERY_LINUX_STAGE_MP}"
  if ! mount -t ext4 -o rw "$dev" "$RECOVERY_LINUX_STAGE_MP"; then
    host_grub_die "mount failed: ${dev} -> ${RECOVERY_LINUX_STAGE_MP}"
  fi
  STAGED_MOUNT_BY_US=1
  RECOVERY_LINUX_MOUNTPOINT="${RECOVERY_LINUX_STAGE_MP}"
  STAGING_BOOT="${RECOVERY_LINUX_STAGE_MP}/boot/recoverix"
  STAGED_TO_RECOVERY_LINUX_PARTITION=yes
}

log "=== Stage Recoverix artifacts to Recovery Linux /boot/recoverix ==="
{
  echo "timestamp: ${TS}"
  echo "host_boot_modified: additive_only"
  echo "efi_modified: false"
} > "$REPORT"

[[ -d "${ROOTFS_RESOLVED}" ]] || host_grub_die "missing ROOTFS: ${ROOTFS_RESOLVED}"
[[ -f "${ROOTFS_BOOT}/vmlinuz-${KVER}" ]] || host_grub_die "missing rootfs vmlinuz"
[[ -f "${RUNTIME_SQ}" ]] || host_grub_die "missing ${RUNTIME_SQ}"

INITRD_SRC="$(recoverix_initrd_resolve_source "$KVER" || true)"
[[ -n "$INITRD_SRC" ]] || host_grub_die "missing recoverix initrd — run 20_install_initramfs_hook.sh first"
if ! recoverix_verify_initrd_hooks "$INITRD_SRC"; then
  host_grub_die "recoverix initrd missing required hooks: ${INITRD_SRC}"
fi
log "initrd source: ${INITRD_SRC}"

host_stage_verify_initrd_handoff_param_conf "$INITRD_SRC" "source"
HANDOFF_PARAM_CONF_VERIFIED=yes
log "source initrd handoff param.conf verified"

HOST_ROOT_UUID="$(findmnt -no UUID / 2>/dev/null || true)"
RECOVERY_LINUX_UUID="${RECOVERY_LINUX_UUID:-${HOST_ROOT_UUID:-}}"
[[ -n "$RECOVERY_LINUX_UUID" ]] || host_grub_die "cannot resolve RECOVERY_LINUX_UUID (set env to p4 UUID)"

host_stage_resolve_recovery_linux_boot "$RECOVERY_LINUX_UUID" "$HOST_ROOT_UUID"

VMLINUX_TARGET="${STAGING_BOOT}/vmlinuz-${KVER}"
INITRD_TARGET="${STAGING_BOOT}/${INITRD_BASENAME}"

log "staging target: ${STAGING_BOOT} (mount=${RECOVERY_LINUX_MOUNTPOINT})"

HOST_FP_BEFORE="$(host_initrd_fingerprint "$KVER" || true)"
echo "host_initrd_fingerprint_before: ${HOST_FP_BEFORE}" >> "$REPORT"

mkdir -p "${STAGING_BOOT}"

[[ -f "${ROOTFS_BOOT}/vmlinuz-${KVER}" ]] || host_grub_die "missing rootfs vmlinuz for staging"
install -m 0644 "${ROOTFS_BOOT}/vmlinuz-${KVER}" "${VMLINUX_TARGET}"
log "staged kernel -> ${VMLINUX_TARGET}"

INITRD_SOURCE_SHA256="$(host_stage_sha256 "$INITRD_SRC")"
install -m 0644 "${INITRD_SRC}" "${INITRD_TARGET}"
INITRD_TARGET_SHA256="$(host_stage_sha256 "$INITRD_TARGET")"
log "staged initrd -> ${INITRD_TARGET}"

if [[ "$INITRD_SOURCE_SHA256" != "$INITRD_TARGET_SHA256" ]]; then
  host_grub_die "initrd sha256 mismatch after copy (source=${INITRD_SOURCE_SHA256} target=${INITRD_TARGET_SHA256})"
fi
log "initrd sha256 match: ${INITRD_SOURCE_SHA256}"

host_stage_verify_initrd_handoff_param_conf "$INITRD_TARGET" "target"
log "target initrd handoff param.conf verified"

assert_host_initrd_unchanged "$KVER" "$HOST_FP_BEFORE" || \
  host_grub_die "host default initrd changed during staging — abort"

install -m 0644 "${RUNTIME_SQ}" "${STAGING_BOOT}/runtime.squashfs"
log "staged squashfs -> ${STAGING_BOOT}/runtime.squashfs ($(stat -c '%s' "${STAGING_BOOT}/runtime.squashfs") bytes)"

if [[ -f "${BUILD_STAMP}" ]]; then
  install -m 0644 "${BUILD_STAMP}" "${STAGING_BOOT}/latest_runtime_build.stamp"
  log "staged build metadata -> ${STAGING_BOOT}/latest_runtime_build.stamp"
else
  log "WARN: build metadata stamp missing (${BUILD_STAMP})"
fi

printf '%s\n' "${RECOVERY_LINUX_UUID}" > "${STAGING_BOOT}/recovery-root.uuid"
chmod 0644 "${STAGING_BOOT}/recovery-root.uuid"
log "recovery-root.uuid -> ${RECOVERY_LINUX_UUID}"

{
  echo "staging_scope: Recovery Linux partition /boot/recoverix"
  echo "staging_boot_dir: ${STAGING_BOOT}"
  echo "recovery_linux_uuid: ${RECOVERY_LINUX_UUID}"
  echo "recovery_linux_mountpoint: ${RECOVERY_LINUX_MOUNTPOINT}"
  echo "host_root_uuid: ${HOST_ROOT_UUID:-unknown}"
  echo "target_is_current_root: ${TARGET_IS_CURRENT_ROOT}"
  echo "staged_to_recovery_linux_partition: ${STAGED_TO_RECOVERY_LINUX_PARTITION}"
  echo "initrd_source: ${INITRD_SRC}"
  echo "initrd_source_sha256: ${INITRD_SOURCE_SHA256}"
  echo "initrd_target: ${INITRD_TARGET}"
  echo "initrd_target_sha256: ${INITRD_TARGET_SHA256}"
  echo "handoff_param_conf_verified: ${HANDOFF_PARAM_CONF_VERIFIED}"
  echo "staged_kernel: ${VMLINUX_TARGET}"
  echo "staged_initrd: ${INITRD_TARGET}"
  echo "staged_squashfs: ${STAGING_BOOT}/runtime.squashfs"
  if [[ -f "${STAGING_BOOT}/latest_runtime_build.stamp" ]]; then
    echo "staged_build_metadata: ${STAGING_BOOT}/latest_runtime_build.stamp"
  else
    echo "staged_build_metadata: missing (source ${BUILD_STAMP})"
  fi
  echo "recoverix.uuid_grub_target: ${RECOVERY_LINUX_UUID}"
  echo "host_initrd_preserved: /boot/initrd.img-${KVER}"
  echo "esp_kernel_initrd_policy: bootloader_only_on_ESP"
  echo "status: OK"
} >> "$REPORT"

host_grub_report_paths
log "Stage complete (Recovery Linux ${RECOVERY_LINUX_UUID} -> ${STAGING_BOOT}). Next: sudo ./deploy/40_stage_esp_runtime.sh"
log "Report: ${REPORT}"
