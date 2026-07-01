#!/usr/bin/env bash
# Stage Recoverix bootloader to ESP EFI/RecoveryBoot and UEFI fallback path
# (additive only; kernel/initrd on Recovery Linux).
# Usage: sudo ./40_stage_esp_runtime.sh
#
# Does NOT: efibootmgr, BootOrder, grub-install, EFI/Microsoft overwrite, bootmgfw.efi,
#           host build artifact deletion on failure.

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RI_DIR="$(cd "${DIR}/.." && pwd)"
# shellcheck source=../lib/common.sh
source "${RI_DIR}/lib/common.sh"
# shellcheck source=../lib/initrd_recoverix.sh
source "${RI_DIR}/lib/initrd_recoverix.sh"
# shellcheck source=lib/host_grub_safety.sh
source "${DIR}/lib/host_grub_safety.sh"
# shellcheck source=lib/esp_safety.sh
source "${DIR}/lib/esp_safety.sh"
# shellcheck source=lib/grub_boot_chain.sh
source "${DIR}/lib/grub_boot_chain.sh"

recoverix_esp_require_root
recoverix_esp_assert_safe_command "$0"
host_grub_assert_safe_command "$0"

KVER="${KEEP_KERNEL_FLAVOR}"
HOST_BOOT="/boot/recoverix"
ACTIVE_BOOT_REL_DIR="${RECOVERIX_ESP_ACTIVE_REL_DIR:-EFI/RecoveryBoot}"
LEGACY_BOOT_REL_DIR="${RECOVERIX_ESP_REL_DIR:-EFI/Recoverix}"
DIRECT_BOOT_REL_DIR="${RECOVERIX_ESP_DIRECT_REL_DIR:-EFI/RecoverixDirect}"
FALLBACK_BOOT_REL_DIR="${RECOVERIX_ESP_FALLBACK_REL_DIR:-EFI/Boot}"
RECOVERIX_ESP_STAGE_FALLBACK="${RECOVERIX_ESP_STAGE_FALLBACK:-1}"
RECOVERY_HOTKEY="${RECOVERIX_BOOT_HOTKEY:-q}"
BOOT_TIMEOUT_SEC="${RECOVERIX_BOOT_TIMEOUT_SEC:-2}"
ESP_MIN_FREE_BYTES="${RECOVERIX_ESP_MIN_FREE_BYTES:-16777216}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/esp_stage_${TS}.txt"
BOOTMGFW_SNAP="${REPORT_DIR}/esp_bootmgfw_before_${TS}.txt"
mkdir -p "${REPORT_DIR}"

EMBEDDED_GRUB_CFG=""
DIRECT_EMBEDDED_GRUB_CFG=""
recoverix_esp_stage_cleanup() {
  rm -f "${EMBEDDED_GRUB_CFG:-}"
  rm -f "${DIRECT_EMBEDDED_GRUB_CFG:-}"
  recoverix_esp_umount_if_mounted_by_us
}

log() { recoverix_esp_log "$*"; }

log "=== Stage Recoverix bootloader to ESP (${ACTIVE_BOOT_REL_DIR}; kernel/initrd on Recovery Linux) ==="
{
  echo "timestamp: ${TS}"
  echo "esp_modified: additive_only"
  echo "nvram_modified: false"
  echo "efibootmgr: false"
  echo "bootorder_changed: false"
} >"$REPORT"

VMLINUX_HOST="${HOST_BOOT}/vmlinuz-${KVER}"

[[ -f "${HOST_BOOT}/runtime.squashfs" ]] || recoverix_esp_die "run deploy/30_stage_host_boot.sh first (missing ${HOST_BOOT}/runtime.squashfs)"
[[ -f "$(recoverix_initrd_host_path "$KVER")" ]] || recoverix_esp_die "missing staged initrd at $(recoverix_initrd_host_path "$KVER")"
[[ -f "${VMLINUX_HOST}" ]] || recoverix_esp_die "missing staged kernel at ${VMLINUX_HOST} (run deploy/30_stage_host_boot.sh)"

# Prevent stale staging: host artifacts must be newer than latest runtime build stamp.
BUILD_STAMP="${RUNTIME_DIR}/latest_runtime_build.stamp"
if [[ -f "$BUILD_STAMP" ]]; then
  BUILD_EPOCH="$(awk -F': ' '/^build_epoch:/ {print $2}' "$BUILD_STAMP" | tr -d '[:space:]' || true)"
  if [[ -n "$BUILD_EPOCH" && "$BUILD_EPOCH" =~ ^[0-9]+$ ]]; then
    SQ_MTIME="$(stat -c '%Y' "${HOST_BOOT}/runtime.squashfs" 2>/dev/null || echo 0)"
    INITRD_MTIME="$(stat -c '%Y' "$(recoverix_initrd_host_path "$KVER")" 2>/dev/null || echo 0)"
    VMLINUX_MTIME="$(stat -c '%Y' "${VMLINUX_HOST}" 2>/dev/null || echo 0)"
    if (( SQ_MTIME < BUILD_EPOCH || INITRD_MTIME < BUILD_EPOCH || VMLINUX_MTIME < BUILD_EPOCH )); then
      recoverix_esp_die "stale host artifacts vs latest runtime build (run: sudo ./deploy/30_stage_host_boot.sh after sudo ./00_build_runtime.sh)"
    fi
  fi
fi

RECOVERY_LINUX_UUID="${RECOVERY_LINUX_UUID:-$(findmnt -no UUID /)}"
[[ -n "$RECOVERY_LINUX_UUID" ]] || recoverix_esp_die "cannot read Recovery Linux UUID (set RECOVERY_LINUX_UUID for p4)"
echo "recovery_linux_uuid: ${RECOVERY_LINUX_UUID}" >>"$REPORT"
echo "boot_policy: esp_bootloader_only; kernel_initrd_on_recovery_linux" >>"$REPORT"

discovered=""
if ! discovered="$(recoverix_esp_discover_timed "${DIR}/lib/esp_safety.sh")"; then
  recoverix_esp_die "ESP discovery failed within ${RECOVERIX_ESP_TIMEOUT_SEC}s"
fi
echo "esp_discovered: ${discovered}" >>"$REPORT"

recoverix_esp_ensure_mounted "$discovered"
ESP_ROOT="${RECOVERIX_ESP_ROOT}"
ESP_DIR="$(recoverix_esp_dir "$ESP_ROOT")"
ACTIVE_BOOT_DIR="${ESP_ROOT%/}/${ACTIVE_BOOT_REL_DIR}"
LEGACY_BOOT_DIR="${ESP_ROOT%/}/${LEGACY_BOOT_REL_DIR}"
DIRECT_BOOT_DIR="${ESP_ROOT%/}/${DIRECT_BOOT_REL_DIR}"
FALLBACK_BOOT_DIR="${ESP_ROOT%/}/${FALLBACK_BOOT_REL_DIR}"
recoverix_esp_require_free_space "$ESP_ROOT" "$ESP_MIN_FREE_BYTES"
echo "esp_mount: ${ESP_ROOT}" >>"$REPORT"
echo "esp_recoverix_dir: ${ESP_DIR}" >>"$REPORT"
echo "boot_chain_active_dir: ${ACTIVE_BOOT_DIR}" >>"$REPORT"
echo "boot_chain_legacy_dir: ${LEGACY_BOOT_DIR}" >>"$REPORT"
echo "boot_chain_direct_dir: ${DIRECT_BOOT_DIR}" >>"$REPORT"
echo "boot_chain_fallback_dir: ${FALLBACK_BOOT_DIR}" >>"$REPORT"
echo "stage_fallback_bootx64: ${RECOVERIX_ESP_STAGE_FALLBACK}" >>"$REPORT"
echo "recovery_hotkey: ${RECOVERY_HOTKEY}" >>"$REPORT"
echo "boot_timeout_sec: ${BOOT_TIMEOUT_SEC}" >>"$REPORT"
echo "esp_min_free_bytes: ${ESP_MIN_FREE_BYTES}" >>"$REPORT"
echo "esp_free_bytes: $(recoverix_esp_free_bytes "$ESP_ROOT")" >>"$REPORT"

trap 'recoverix_esp_stage_cleanup' EXIT

recoverix_esp_snapshot_bootmgfw "$ESP_ROOT" "$BOOTMGFW_SNAP"
echo "bootmgfw_snapshot: ${BOOTMGFW_SNAP}" >>"$REPORT"

recoverix_esp_verify_rw "$ESP_ROOT"
log "ESP RW verified under ${RECOVERIX_ESP_REL_DIR}"
recoverix_esp_assert_safe_dest "${ACTIVE_BOOT_REL_DIR}/.recoverix-esp-rw-probe"
recoverix_esp_assert_safe_dest "${DIRECT_BOOT_REL_DIR}/.recoverix-esp-rw-probe"
mkdir -p "${ACTIVE_BOOT_DIR}"
if ! echo recoverix-esp-probe >"${ACTIVE_BOOT_DIR}/.recoverix-esp-rw-probe" 2>/dev/null; then
  recoverix_esp_die "ESP not writable at ${ACTIVE_BOOT_REL_DIR}"
fi
rm -f "${ACTIVE_BOOT_DIR}/.recoverix-esp-rw-probe"
log "ESP RW verified under ${ACTIVE_BOOT_REL_DIR}"
mkdir -p "${DIRECT_BOOT_DIR}"
if ! echo recoverix-esp-probe >"${DIRECT_BOOT_DIR}/.recoverix-esp-rw-probe" 2>/dev/null; then
  recoverix_esp_die "ESP not writable at ${DIRECT_BOOT_REL_DIR}"
fi
rm -f "${DIRECT_BOOT_DIR}/.recoverix-esp-rw-probe"
log "ESP RW verified under ${DIRECT_BOOT_REL_DIR}"

ESP_UUID="$(findmnt -no UUID "$ESP_ROOT" 2>/dev/null || blkid -s UUID -o value "$(findmnt -no SOURCE "$ESP_ROOT" 2>/dev/null)" 2>/dev/null || true)"
[[ -n "$ESP_UUID" ]] || recoverix_esp_die "cannot read ESP filesystem UUID"
echo "esp_uuid: ${ESP_UUID}" >>"$REPORT"

mkdir -p "$ESP_DIR" "$ACTIVE_BOOT_DIR" "$DIRECT_BOOT_DIR"
recoverix_esp_assert_safe_dest "${RECOVERIX_ESP_REL_DIR}/grub.cfg"
recoverix_esp_assert_safe_dest "${ACTIVE_BOOT_REL_DIR}/grub.cfg"
recoverix_esp_assert_safe_dest "${DIRECT_BOOT_REL_DIR}/grub.cfg"

# Remove legacy kernel/initrd from ESP (bootloader-only policy).
for _legacy_esp in \
	  "${ESP_DIR}/vmlinuz-${KVER}" \
	  "${ESP_DIR}/initrd.img-${KVER}-recoverix" \
	  "${ACTIVE_BOOT_DIR}/vmlinuz-${KVER}" \
	  "${ACTIVE_BOOT_DIR}/initrd.img-${KVER}-recoverix" \
	  "${DIRECT_BOOT_DIR}/vmlinuz-${KVER}" \
	  "${DIRECT_BOOT_DIR}/initrd.img-${KVER}-recoverix"; do
  if [[ -f "$_legacy_esp" ]]; then
    rm -f "$_legacy_esp"
    log "removed legacy ESP kernel/initrd: ${_legacy_esp}"
    echo "removed_legacy_esp_artifact: ${_legacy_esp}" >>"$REPORT"
  fi
done
echo "staging_kernel_path: ${VMLINUX_HOST}" >>"$REPORT"
echo "staging_initrd_path: $(recoverix_initrd_host_path "$KVER")" >>"$REPORT"
log "kernel/initrd remain on Recovery Linux (not copied to ESP)"

# RecoveryBoot GRUB: embedded bootstrap (mkstandalone) + external menu-only grub.cfg
MENU_TEMPLATE="${DIR}/grub/recoverix-esp.cfg.template"
DIRECT_MENU_TEMPLATE="${DIR}/grub/recoverix-direct.cfg.template"
BOOTSTRAP_TEMPLATE="${DIR}/grub/recoverix-esp-embedded.bootstrap.template"
[[ -f "$MENU_TEMPLATE" ]] || recoverix_esp_die "missing template ${MENU_TEMPLATE}"
[[ -f "$DIRECT_MENU_TEMPLATE" ]] || recoverix_esp_die "missing template ${DIRECT_MENU_TEMPLATE}"
[[ -f "$BOOTSTRAP_TEMPLATE" ]] || recoverix_esp_die "missing bootstrap ${BOOTSTRAP_TEMPLATE}"
ACTIVE_GRUB_CFG="${ACTIVE_BOOT_DIR}/grub.cfg"
LEGACY_GRUB_CFG="${LEGACY_BOOT_DIR}/grub.cfg"
DIRECT_GRUB_CFG="${DIRECT_BOOT_DIR}/grub.cfg"
GRUB_CFG="${ESP_DIR}/grub.cfg"
EMBEDDED_GRUB_CFG="$(mktemp "${REPORT_DIR}/recoverix-embedded-grub.XXXXXX")"
DIRECT_EMBEDDED_GRUB_CFG="$(mktemp "${REPORT_DIR}/recoverix-direct-embedded-grub.XXXXXX")"

mkdir -p "$ACTIVE_BOOT_DIR" "$DIRECT_BOOT_DIR"
if ! recoverix_grub_render_external_menu_cfg "$MENU_TEMPLATE" "$ACTIVE_GRUB_CFG" \
  "$RECOVERY_LINUX_UUID" "$KVER" "$ESP_UUID" "$ACTIVE_BOOT_REL_DIR" "$RECOVERY_HOTKEY" "$BOOT_TIMEOUT_SEC"; then
  recoverix_esp_die "failed to render external RecoveryBoot menu grub.cfg"
fi
if ! recoverix_grub_render_external_menu_cfg "$DIRECT_MENU_TEMPLATE" "$DIRECT_GRUB_CFG" \
  "$RECOVERY_LINUX_UUID" "$KVER" "$ESP_UUID" "$DIRECT_BOOT_REL_DIR" "$RECOVERY_HOTKEY" 0; then
  recoverix_esp_die "failed to render direct Recoverix menu grub.cfg"
fi
if ! recoverix_grub_render_embedded_bootstrap "$BOOTSTRAP_TEMPLATE" "$EMBEDDED_GRUB_CFG" \
  "$RECOVERY_LINUX_UUID" "$KVER" "$ESP_UUID" "$ACTIVE_BOOT_REL_DIR" "$RECOVERY_HOTKEY" "$BOOT_TIMEOUT_SEC"; then
  recoverix_esp_die "failed to render embedded bootstrap grub.cfg"
fi
if ! recoverix_grub_render_embedded_bootstrap "$BOOTSTRAP_TEMPLATE" "$DIRECT_EMBEDDED_GRUB_CFG" \
  "$RECOVERY_LINUX_UUID" "$KVER" "$ESP_UUID" "$DIRECT_BOOT_REL_DIR" "$RECOVERY_HOTKEY" 0; then
  recoverix_esp_die "failed to render direct embedded bootstrap grub.cfg"
fi
recoverix_grub_report_embedded_bootstrap_rendered "$REPORT" "$EMBEDDED_GRUB_CFG" "$ESP_UUID" "$ACTIVE_BOOT_REL_DIR"
recoverix_grub_report_embedded_bootstrap_rendered "$REPORT" "$DIRECT_EMBEDDED_GRUB_CFG" "$ESP_UUID" "$DIRECT_BOOT_REL_DIR"
recoverix_grub_report_generation_sources "$REPORT" "$MENU_TEMPLATE" "$BOOTSTRAP_TEMPLATE" \
  "$ACTIVE_GRUB_CFG" "$EMBEDDED_GRUB_CFG"
recoverix_grub_report_generation_sources "$REPORT" "$DIRECT_MENU_TEMPLATE" "$BOOTSTRAP_TEMPLATE" \
  "$DIRECT_GRUB_CFG" "$DIRECT_EMBEDDED_GRUB_CFG"
echo "grub_esp_target_path: ${ACTIVE_GRUB_CFG}" >>"$REPORT"
echo "direct_grub_esp_target_path: ${DIRECT_GRUB_CFG}" >>"$REPORT"
log "staged external menu grub.cfg -> ${ACTIVE_GRUB_CFG}"
log "staged direct recovery grub.cfg -> ${DIRECT_GRUB_CFG}"

_external_reason=""
if ! _external_reason="$(recoverix_grub_validate_external_menu_cfg "$ACTIVE_GRUB_CFG")"; then
  recoverix_grub_dump_contamination "$REPORT" "active_grub_cfg" "$ACTIVE_GRUB_CFG"
  recoverix_esp_die "active grub.cfg contamination: ${_external_reason}"
fi
_embedded_reason=""
if ! _embedded_reason="$(recoverix_grub_validate_embedded_bootstrap "$EMBEDDED_GRUB_CFG" "$ACTIVE_BOOT_REL_DIR" "$ESP_UUID")"; then
  recoverix_grub_dump_contamination "$REPORT" "embedded_bootstrap" "$EMBEDDED_GRUB_CFG"
  recoverix_esp_die "embedded bootstrap invalid: ${_embedded_reason}"
fi
_direct_external_reason=""
if ! _direct_external_reason="$(recoverix_grub_validate_external_menu_cfg "$DIRECT_GRUB_CFG")"; then
  recoverix_grub_dump_contamination "$REPORT" "direct_grub_cfg" "$DIRECT_GRUB_CFG"
  recoverix_esp_die "direct grub.cfg contamination: ${_direct_external_reason}"
fi
_direct_embedded_reason=""
if ! _direct_embedded_reason="$(recoverix_grub_validate_embedded_bootstrap "$DIRECT_EMBEDDED_GRUB_CFG" "$DIRECT_BOOT_REL_DIR" "$ESP_UUID")"; then
  recoverix_grub_dump_contamination "$REPORT" "direct_embedded_bootstrap" "$DIRECT_EMBEDDED_GRUB_CFG"
  recoverix_esp_die "direct embedded bootstrap invalid: ${_direct_embedded_reason}"
fi
echo "active_grub_cfg_validation: PASS (external menu-only)" >>"$REPORT"
echo "embedded_bootstrap_validation: PASS" >>"$REPORT"
echo "direct_grub_cfg_validation: PASS (direct runtime menu-only)" >>"$REPORT"
echo "direct_embedded_bootstrap_validation: PASS" >>"$REPORT"
if [[ "${LEGACY_BOOT_DIR}" != "${ACTIVE_BOOT_DIR}" ]]; then
  mkdir -p "$LEGACY_BOOT_DIR"
  install -m 0644 "$ACTIVE_GRUB_CFG" "$LEGACY_GRUB_CFG"
  log "staged legacy menu grub.cfg -> ${LEGACY_GRUB_CFG}"
fi
install -m 0644 "$ACTIVE_GRUB_CFG" "$GRUB_CFG"
log "staged reference menu grub.cfg -> ${GRUB_CFG}"

# grubx64.efi — embed bootstrap only; external ACTIVE_GRUB_CFG holds menuentries
GRUB_EFI="${ACTIVE_BOOT_DIR}/grubx64.efi"
DIRECT_GRUB_EFI="${DIRECT_BOOT_DIR}/grubx64.efi"
UBUNTU_GRUB_EFI="${ESP_ROOT}/EFI/ubuntu/grubx64.efi"
recoverix_esp_assert_safe_dest "${ACTIVE_BOOT_REL_DIR}/grubx64.efi"
recoverix_esp_assert_safe_dest "${DIRECT_BOOT_REL_DIR}/grubx64.efi"
_grub_build_rc=0
recoverix_grub_build_standalone_efi "$EMBEDDED_GRUB_CFG" "$GRUB_EFI" "$REPORT" || _grub_build_rc=$?
if [[ $_grub_build_rc -ne 0 ]]; then
  case "$_grub_build_rc" in
    2) recoverix_esp_die "grub-mkstandalone not found (see report: grub_mkstandalone_detection)" ;;
    3) recoverix_esp_die "grub EFI module dir missing — install grub-efi-amd64-bin (see report)" ;;
    4) recoverix_esp_die "grub-mkstandalone execution failed (see report: standalone_build_cmdline)" ;;
    5) recoverix_esp_die "standalone EFI verify failed (see report: standalone_efi_verify)" ;;
    *) recoverix_esp_die "standalone grub build failed (rc=${_grub_build_rc}, see report)" ;;
  esac
fi
if [[ -f "$UBUNTU_GRUB_EFI" ]] && recoverix_grub_efi_identical "$GRUB_EFI" "$UBUNTU_GRUB_EFI"; then
  recoverix_esp_die "RecoveryBoot grubx64.efi is byte-identical to EFI/ubuntu/grubx64.efi (not standalone)"
fi
echo "grubx64_source: grub-mkstandalone-embedded-bootstrap" >>"$REPORT"
echo "grubx64_embedded_bootstrap: ${EMBEDDED_GRUB_CFG}" >>"$REPORT"
log "staged standalone grubx64.efi -> ${GRUB_EFI}"

_direct_grub_build_rc=0
RECOVERIX_ESP_ACTIVE_REL_DIR="$DIRECT_BOOT_REL_DIR" \
  recoverix_grub_build_standalone_efi "$DIRECT_EMBEDDED_GRUB_CFG" "$DIRECT_GRUB_EFI" "$REPORT" || _direct_grub_build_rc=$?
if [[ $_direct_grub_build_rc -ne 0 ]]; then
  case "$_direct_grub_build_rc" in
    2) recoverix_esp_die "direct grub-mkstandalone not found (see report: grub_mkstandalone_detection)" ;;
    3) recoverix_esp_die "direct grub EFI module dir missing — install grub-efi-amd64-bin (see report)" ;;
    4) recoverix_esp_die "direct grub-mkstandalone execution failed (see report: standalone_build_cmdline)" ;;
    5) recoverix_esp_die "direct standalone EFI verify failed (see report: standalone_efi_verify)" ;;
    *) recoverix_esp_die "direct standalone grub build failed (rc=${_direct_grub_build_rc}, see report)" ;;
  esac
fi
if [[ -f "$UBUNTU_GRUB_EFI" ]] && recoverix_grub_efi_identical "$DIRECT_GRUB_EFI" "$UBUNTU_GRUB_EFI"; then
  recoverix_esp_die "RecoverixDirect grubx64.efi is byte-identical to EFI/ubuntu/grubx64.efi (not standalone)"
fi
echo "direct_grubx64_source: grub-mkstandalone-direct-embedded-bootstrap" >>"$REPORT"
echo "direct_grubx64_embedded_bootstrap: ${DIRECT_EMBEDDED_GRUB_CFG}" >>"$REPORT"
log "staged direct standalone grubx64.efi -> ${DIRECT_GRUB_EFI}"

# UEFI removable-media fallback. If NVRAM boot entries disappear, firmware usually
# exposes the SSD itself and loads EFI/Boot/bootx64.efi from that disk.
if [[ "${RECOVERIX_ESP_STAGE_FALLBACK}" == "1" ]]; then
  FALLBACK_BOOTX64="${FALLBACK_BOOT_DIR}/bootx64.efi"
  FALLBACK_GRUBX64="${FALLBACK_BOOT_DIR}/grubx64.efi"
  FALLBACK_BACKUP="${REPORT_DIR}/esp_fallback_bootx64_before_${TS}.efi"
  recoverix_esp_assert_safe_dest "${FALLBACK_BOOT_REL_DIR}/bootx64.efi"
  recoverix_esp_assert_safe_dest "${FALLBACK_BOOT_REL_DIR}/grubx64.efi"
  mkdir -p "$FALLBACK_BOOT_DIR"

  if [[ -f "$FALLBACK_BOOTX64" ]]; then
    install -m 0644 "$FALLBACK_BOOTX64" "$FALLBACK_BACKUP"
    echo "fallback_bootx64_previous_backup: ${FALLBACK_BACKUP}" >>"$REPORT"
  else
    echo "fallback_bootx64_previous_backup: none (path absent)" >>"$REPORT"
  fi

  _fallback_shim_src=""
  for _c in \
    "${ESP_ROOT}/EFI/ubuntu/shimx64.efi" \
    "/boot/efi/EFI/ubuntu/shimx64.efi" \
    "/usr/lib/shim/shimx64.efi.signed"; do
    if [[ -f "$_c" ]]; then
      _fallback_shim_src="$_c"
      break
    fi
  done

  if [[ -n "$_fallback_shim_src" ]]; then
    install -m 0644 "$_fallback_shim_src" "$FALLBACK_BOOTX64"
    install -m 0644 "$GRUB_EFI" "$FALLBACK_GRUBX64"
    echo "fallback_bootx64_source: shim (${_fallback_shim_src})" >>"$REPORT"
    echo "fallback_grubx64_source: ${GRUB_EFI}" >>"$REPORT"
    log "staged fallback shim -> ${FALLBACK_BOOTX64}"
    log "staged fallback standalone grub -> ${FALLBACK_GRUBX64}"
  else
    install -m 0644 "$GRUB_EFI" "$FALLBACK_BOOTX64"
    rm -f "$FALLBACK_GRUBX64"
    echo "fallback_bootx64_source: standalone_grub (${GRUB_EFI})" >>"$REPORT"
    echo "fallback_grubx64_source: not_used (shim source not found)" >>"$REPORT"
    log "staged fallback standalone grub -> ${FALLBACK_BOOTX64}"
  fi
else
  echo "fallback_bootx64_source: skipped (RECOVERIX_ESP_STAGE_FALLBACK=0)" >>"$REPORT"
  log "fallback bootx64 staging skipped"
fi

# Optional shim (additive under active boot path only)
if [[ "${RECOVERIX_ESP_STAGE_SHIM:-0}" == "1" ]]; then
  SHIM_ESP="${ACTIVE_BOOT_DIR}/shimx64.efi"
  recoverix_esp_assert_safe_dest "${ACTIVE_BOOT_REL_DIR}/shimx64.efi"
  _shim_src=""
  for _c in \
    "${ESP_ROOT}/EFI/ubuntu/shimx64.efi" \
    "/boot/efi/EFI/ubuntu/shimx64.efi" \
    "/usr/lib/shim/shimx64.efi.signed"; do
    if [[ -f "$_c" ]]; then
      _shim_src="$_c"
      break
    fi
  done
  if [[ -n "$_shim_src" ]]; then
    mkdir -p "$ACTIVE_BOOT_DIR"
    install -m 0644 "$_shim_src" "$SHIM_ESP"
    echo "shimx64_staged: yes (${_shim_src})" >>"$REPORT"
    log "staged optional shimx64.efi -> ${SHIM_ESP}"
  else
    echo "shimx64_staged: skipped (source not found)" >>"$REPORT"
    log "WARN: RECOVERIX_ESP_STAGE_SHIM=1 but shimx64.efi source not found"
  fi
else
  echo "shimx64_staged: optional_skipped" >>"$REPORT"
fi

# Direct recovery entry must be independently bootable from firmware F12.
DIRECT_SHIM_ESP="${DIRECT_BOOT_DIR}/shimx64.efi"
recoverix_esp_assert_safe_dest "${DIRECT_BOOT_REL_DIR}/shimx64.efi"
_direct_shim_src=""
for _c in \
  "${ACTIVE_BOOT_DIR}/shimx64.efi" \
  "${ESP_ROOT}/EFI/ubuntu/shimx64.efi" \
  "/boot/efi/EFI/ubuntu/shimx64.efi" \
  "/usr/lib/shim/shimx64.efi.signed"; do
  if [[ -f "$_c" ]]; then
    _direct_shim_src="$_c"
    break
  fi
done
if [[ -z "$_direct_shim_src" ]]; then
  recoverix_esp_die "cannot stage direct recovery shimx64.efi (no shim source found)"
fi
mkdir -p "$DIRECT_BOOT_DIR"
install -m 0644 "$_direct_shim_src" "$DIRECT_SHIM_ESP"
echo "direct_shimx64_staged: yes (${_direct_shim_src})" >>"$REPORT"
log "staged direct shimx64.efi -> ${DIRECT_SHIM_ESP}"

# Recovery Linux artifact presence (ESP must not hold kernel/initrd).
HOST_INITRD="$(recoverix_initrd_host_path "$KVER")"
[[ -f "$HOST_INITRD" ]] || recoverix_esp_die "missing Recovery Linux initrd: ${HOST_INITRD}"
[[ -f "${VMLINUX_HOST}" ]] || recoverix_esp_die "missing Recovery Linux kernel: ${VMLINUX_HOST}"
echo "recovery_linux_vmlinuz: ${VMLINUX_HOST}" >>"$REPORT"
echo "recovery_linux_initrd: ${HOST_INITRD}" >>"$REPORT"

if [[ -f "${ESP_DIR}/vmlinuz-${KVER}" || -f "${ESP_DIR}/initrd.img-${KVER}-recoverix" ]]; then
  recoverix_esp_die "ESP still contains kernel/initrd under ${RECOVERIX_ESP_REL_DIR} (bootloader-only policy)"
fi

if ! grep -qF "/boot/recoverix/vmlinuz-${KVER}" "$ACTIVE_GRUB_CFG"; then
  recoverix_esp_die "grub.cfg missing Recovery Linux kernel path"
fi
if ! grep -qF "/boot/recoverix/initrd.img-${KVER}-recoverix" "$ACTIVE_GRUB_CFG"; then
  recoverix_esp_die "grub.cfg missing Recovery Linux initrd path"
fi
if ! grep -qF "set default=windows_boot_manager" "$ACTIVE_GRUB_CFG"; then
  recoverix_esp_die "grub.cfg must default to Windows Boot Manager"
fi
if ! grep -qF -- "--hotkey=${RECOVERY_HOTKEY}" "$ACTIVE_GRUB_CFG"; then
  recoverix_esp_die "grub.cfg missing configured recovery hotkey (${RECOVERY_HOTKEY})"
fi
if ! grep -qF "recoverix.uuid=${RECOVERY_LINUX_UUID}" "$ACTIVE_GRUB_CFG"; then
  recoverix_esp_die "grub.cfg missing recoverix.uuid (Recovery Linux UUID)"
fi
if ! grep -qF "/boot/recoverix/vmlinuz-${KVER}" "$DIRECT_GRUB_CFG"; then
  recoverix_esp_die "direct grub.cfg missing Recovery Linux kernel path"
fi
if ! grep -qF "/boot/recoverix/initrd.img-${KVER}-recoverix" "$DIRECT_GRUB_CFG"; then
  recoverix_esp_die "direct grub.cfg missing Recovery Linux initrd path"
fi
if ! grep -qF "set default=recoverix_runtime" "$DIRECT_GRUB_CFG"; then
  recoverix_esp_die "direct grub.cfg must default to Recoverix Runtime"
fi
if ! grep -qF "recoverix.uuid=${RECOVERY_LINUX_UUID}" "$DIRECT_GRUB_CFG"; then
  recoverix_esp_die "direct grub.cfg missing recoverix.uuid (Recovery Linux UUID)"
fi
if grep -qF "/EFI/Recoverix/vmlinuz-${KVER}" "$ACTIVE_GRUB_CFG"; then
  recoverix_esp_die "grub.cfg still references ESP kernel path (must use /boot/recoverix)"
fi
if grep -qF "/EFI/Recoverix/vmlinuz-${KVER}" "$DIRECT_GRUB_CFG" || \
   grep -qF "/EFI/RecoverixDirect/vmlinuz-${KVER}" "$DIRECT_GRUB_CFG"; then
  recoverix_esp_die "direct grub.cfg still references ESP kernel path (must use /boot/recoverix)"
fi
if recoverix_grub_validate_core_menuentries "$ACTIVE_GRUB_CFG"; then
  echo "recoverix_runtime_boot_target: multi-user.target" >>"$REPORT"
  echo "recoverix_debug_shell_boot_target: multi-user.target" >>"$REPORT"
  echo "recoverix_systemd_debug_boot_target: multi-user.target" >>"$REPORT"
else
  echo "recoverix_core_menuentries: invalid" >>"$REPORT"
  recoverix_grub_validate_core_menuentries "$ACTIVE_GRUB_CFG" 2>&1 | sed 's/^/recoverix_core_grub_error: /' >>"$REPORT" || true
  recoverix_esp_die "grub.cfg core Recoverix menuentries invalid"
fi
if recoverix_grub_validate_core_menuentries "$DIRECT_GRUB_CFG"; then
  echo "direct_recoverix_runtime_boot_target: multi-user.target" >>"$REPORT"
  echo "direct_recoverix_systemd_debug_boot_target: multi-user.target" >>"$REPORT"
else
  echo "direct_recoverix_core_menuentries: invalid" >>"$REPORT"
  recoverix_grub_validate_core_menuentries "$DIRECT_GRUB_CFG" 2>&1 | sed 's/^/direct_recoverix_core_grub_error: /' >>"$REPORT" || true
  recoverix_esp_die "direct grub.cfg core Recoverix menuentries invalid"
fi

SQUASHFS_HOST="${HOST_BOOT}/runtime.squashfs"
[[ -f "$SQUASHFS_HOST" ]] || recoverix_esp_die "missing ${SQUASHFS_HOST}"
echo "runtime_squashfs_host: ${SQUASHFS_HOST}" >>"$REPORT"
echo "runtime_squashfs_bytes: $(stat -c '%s' "$SQUASHFS_HOST")" >>"$REPORT"
echo "runtime_squashfs_mtime: $(stat -c '%Y' "$SQUASHFS_HOST" 2>/dev/null || echo unknown)" >>"$REPORT"
echo "runtime_squashfs_initramfs_path: /boot/recoverix/runtime.squashfs" >>"$REPORT"

recoverix_esp_verify_bootmgfw_unchanged "$ESP_ROOT" "$BOOTMGFW_SNAP"
echo "microsoft_bootmgfw_unchanged: yes" >>"$REPORT"

if [[ "${RECOVERIX_PURGE_HOST_GRUB_ENTRIES:-1}" == "1" ]]; then
  log "purging Recoverix entries from Ubuntu /etc/grub.d (RecoveryBoot-only policy)"
  recoverix_grub_trace_host_entry_sources "$REPORT"
  recoverix_grub_purge_host_entries "$REPORT" || \
    recoverix_esp_die "failed to purge host Ubuntu grub Recoverix snippets"
else
  echo "host_grub_purge: skipped (RECOVERIX_PURGE_HOST_GRUB_ENTRIES=0)" >>"$REPORT"
fi

recoverix_grub_report_boot_chain "$ESP_ROOT" "$REPORT"

{
  echo "staged_grub_cfg: ${ACTIVE_GRUB_CFG}"
  echo "staged_grub_efi: ${GRUB_EFI}"
  if [[ -f "${ACTIVE_BOOT_DIR}/shimx64.efi" ]]; then
    echo "active_shimx64_efi_path: ${ACTIVE_BOOT_DIR}/shimx64.efi"
  else
    echo "active_shimx64_efi_path: missing (${ACTIVE_BOOT_DIR}/shimx64.efi)"
  fi
	  echo "active_grubx64_efi_path: ${GRUB_EFI}"
	  echo "active_grub_cfg_path: ${ACTIVE_GRUB_CFG}"
	  echo "legacy_grub_cfg_path: ${LEGACY_GRUB_CFG}"
	  echo "direct_shimx64_efi_path: ${DIRECT_SHIM_ESP}"
	  echo "direct_grubx64_efi_path: ${DIRECT_GRUB_EFI}"
	  echo "direct_grub_cfg_path: ${DIRECT_GRUB_CFG}"
	  if [[ "${RECOVERIX_ESP_STAGE_FALLBACK}" == "1" ]]; then
    echo "fallback_bootx64_efi_path: ${FALLBACK_BOOT_DIR}/bootx64.efi"
    if [[ -f "${FALLBACK_BOOT_DIR}/grubx64.efi" ]]; then
      echo "fallback_grubx64_efi_path: ${FALLBACK_BOOT_DIR}/grubx64.efi"
    else
      echo "fallback_grubx64_efi_path: not_used"
    fi
  else
    echo "fallback_bootx64_efi_path: skipped"
  fi
  echo "recovery_linux_uuid: ${RECOVERY_LINUX_UUID}"
  echo "status: OK"
} >>"$REPORT"

log "ESP stage complete (NVRAM/BootOrder untouched)."
log "Firmware: manually boot ${RECOVERIX_ESP_REL_DIR}/grubx64.efi to test — no efibootmgr."
log "Report: ${REPORT}"
