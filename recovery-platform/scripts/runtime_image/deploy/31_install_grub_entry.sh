#!/usr/bin/env bash
# OPTIONAL legacy: install Recoverix menu into Ubuntu host GRUB (/etc/grub.d/41_recoverix).
# Usage: sudo RECOVERIX_INSTALL_HOST_GRUB=1 ./31_install_grub_entry.sh
#
# Default policy: disabled. RecoveryBoot must use EFI/RecoveryBoot/grub.cfg only.
# Does NOT: grub-install, efibootmgr, shim/EFI overwrite, GRUB_DEFAULT change.

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RI_DIR="$(cd "${DIR}/.." && pwd)"
# shellcheck source=../lib/common.sh
source "${RI_DIR}/lib/common.sh"
# shellcheck source=lib/host_grub_safety.sh
source "${DIR}/lib/host_grub_safety.sh"

host_grub_require_root
host_grub_assert_safe_command "$0"

if [[ "${RECOVERIX_INSTALL_HOST_GRUB:-0}" != "1" ]]; then
  printf '[recoverix-deploy] skipped: host GRUB install disabled (set RECOVERIX_INSTALL_HOST_GRUB=1 to override)\n' >&2
  printf '[recoverix-deploy] RecoveryBoot path: sudo ./deploy/40_stage_esp_runtime.sh\n' >&2
  exit 0
fi

KVER="${KEEP_KERNEL_FLAVOR}"
GRUB_SNIPPET="/etc/grub.d/41_recoverix"
TEMPLATE="${DIR}/grub/41_recoverix.template"
HOST_BOOT="/boot/recoverix"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/host_grub_install_${TS}.txt"
mkdir -p "${REPORT_DIR}"

log() { printf '[recoverix-deploy] %s\n' "$*"; }

[[ -f "${HOST_BOOT}/initrd.img-${KVER}-recoverix" ]] || \
  host_grub_die "run 30_stage_host_boot.sh first"
[[ -f "${HOST_BOOT}/runtime.squashfs" ]] || host_grub_die "missing ${HOST_BOOT}/runtime.squashfs"
[[ -f "/boot/vmlinuz-${KVER}" ]] || host_grub_die "missing /boot/vmlinuz-${KVER}"
[[ -f "$TEMPLATE" ]] || host_grub_die "missing template ${TEMPLATE}"

ROOT_UUID="$(findmnt -no UUID /)"
[[ -n "$ROOT_UUID" ]] || host_grub_die "cannot read root UUID"

GRUB_DEFAULT_BEFORE="$(grep -E '^GRUB_DEFAULT=' /etc/default/grub 2>/dev/null | head -1 || true)"

log "=== Install Recoverix GRUB entry (update-grub only) ==="
{
  echo "timestamp: ${TS}"
  echo "recovery_uuid: ${ROOT_UUID}"
  echo "grub_default_before: ${GRUB_DEFAULT_BEFORE}"
  echo "efi_modified: false"
  echo "grub_install: false"
} > "$REPORT"

host_grub_backup_file /etc/default/grub
host_grub_backup_file "$GRUB_SNIPPET"
[[ -f /boot/grub/grub.cfg ]] && host_grub_backup_file /boot/grub/grub.cfg

sed -e "s/@RECOVERY_UUID@/${ROOT_UUID}/g" \
    -e "s/@KERNEL_VERSION@/${KVER}/g" \
    "$TEMPLATE" > "$GRUB_SNIPPET"
chmod 0755 "$GRUB_SNIPPET"
log "installed ${GRUB_SNIPPET}"

log "running update-grub (regenerates /boot/grub/grub.cfg only)..."
host_grub_assert_safe_command update-grub
update-grub 2>&1 | tee -a "$REPORT"
UPDATE_RC=${PIPESTATUS[0]}
[[ $UPDATE_RC -eq 0 ]] || host_grub_die "update-grub failed (rc=${UPDATE_RC})"

host_grub_verify_default_unchanged "$GRUB_DEFAULT_BEFORE"

if grep -q 'Recoverix Runtime' /boot/grub/grub.cfg 2>/dev/null; then
  echo "grub_cfg_contains_recoverix: yes" >> "$REPORT"
  log "verified: Recoverix entry in /boot/grub/grub.cfg"
else
  host_grub_die "Recoverix menu not found in grub.cfg after update-grub"
fi

{
  echo "grub_default_after: $(grep -E '^GRUB_DEFAULT=' /etc/default/grub | head -1)"
  echo "status: OK"
} >> "$REPORT"

log "Install complete. GRUB_DEFAULT unchanged — Ubuntu remains default."
log "Firmware BootCurrent may be RecoveryBoot — select 'ubuntu' entry for this menu."
log "Report: ${REPORT}"
