#!/usr/bin/env bash
# Verify ESP Recoverix layout (read-only; no NVRAM / Microsoft EFI mutation).
# Usage: sudo ./12_verify_esp_layout.sh
#
# Policy: missing Recoverix ESP artifacts = FAIL; bootmgfw change = FAIL.

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/initrd_recoverix.sh
source "${DIR}/lib/initrd_recoverix.sh"
# shellcheck source=deploy/lib/esp_safety.sh
source "${DIR}/deploy/lib/esp_safety.sh"
# shellcheck source=deploy/lib/grub_boot_chain.sh
source "${DIR}/deploy/lib/grub_boot_chain.sh"

recoverix_esp_require_root
recoverix_esp_assert_safe_command "$0"

KVER="${KEEP_KERNEL_FLAVOR}"
HOST_BOOT="/boot/recoverix"
VMLINUX_HOST="${HOST_BOOT}/vmlinuz-${KVER}"
INITRD_HOST="$(recoverix_initrd_host_path "$KVER")"
RECOVERY_LINUX_UUID="${RECOVERY_LINUX_UUID:-$(findmnt -no UUID / 2>/dev/null || true)}"
ACTIVE_BOOT_REL_DIR="${RECOVERIX_ESP_ACTIVE_REL_DIR:-EFI/RecoveryBoot}"
LEGACY_BOOT_REL_DIR="${RECOVERIX_ESP_REL_DIR:-EFI/Recoverix}"
DIRECT_BOOT_REL_DIR="${RECOVERIX_ESP_DIRECT_REL_DIR:-EFI/RecoverixDirect}"
FALLBACK_BOOT_REL_DIR="${RECOVERIX_ESP_FALLBACK_REL_DIR:-EFI/Boot}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/esp_verify_${TS}.txt"
BOOTMGFW_SNAP="${REPORT_DIR}/esp_verify_bootmgfw_${TS}.txt"
mkdir -p "${REPORT_DIR}"

PASS=0
WARN=0
FAIL=0

ok() { printf 'PASS\t%s\n' "$*"; echo "PASS	$*" >>"$REPORT"; PASS=$((PASS + 1)); }
warn() { printf 'WARN\t%s\n' "$*"; echo "WARN	$*" >>"$REPORT"; WARN=$((WARN + 1)); }
fail() { printf 'FAIL\t%s\n' "$*"; echo "FAIL	$*" >>"$REPORT"; FAIL=$((FAIL + 1)); }

{
  echo "timestamp: ${TS}"
  echo "nvram_modified: false"
  echo "efibootmgr_invoked: false"
} >"$REPORT"

discovered=""
discovered="$(recoverix_esp_discover_timed "${DIR}/deploy/lib/esp_safety.sh" 2>/dev/null)" || true
if [[ -z "$discovered" ]]; then
  fail "ESP not found"
  echo "summary: PASS=${PASS} WARN=${WARN} FAIL=${FAIL}" | tee -a "$REPORT"
  exit 2
fi
ok "ESP discovered: ${discovered}"
echo "esp_discovered: ${discovered}" >>"$REPORT"

recoverix_esp_ensure_mounted "$discovered"
ESP_ROOT="${RECOVERIX_ESP_ROOT}"
ESP_DIR="$(recoverix_esp_dir "$ESP_ROOT")"
trap 'recoverix_esp_umount_if_mounted_by_us' EXIT

recoverix_esp_snapshot_bootmgfw "$ESP_ROOT" "$BOOTMGFW_SNAP"
if grep -q '^present=yes$' "$BOOTMGFW_SNAP" 2>/dev/null; then
  ok "EFI/Microsoft/Boot/bootmgfw.efi present (not modified by this script)"
else
  warn "bootmgfw.efi not on ESP (Windows path absent)"
fi

[[ -d "$ESP_DIR" ]] && ok "EFI/Recoverix directory exists" || fail "missing ${RECOVERIX_ESP_REL_DIR}"

ACTIVE_BOOT_DIR="${ESP_ROOT%/}/${ACTIVE_BOOT_REL_DIR}"
LEGACY_BOOT_DIR="${ESP_ROOT%/}/${LEGACY_BOOT_REL_DIR}"
DIRECT_BOOT_DIR="${ESP_ROOT%/}/${DIRECT_BOOT_REL_DIR}"
FALLBACK_BOOT_DIR="${ESP_ROOT%/}/${FALLBACK_BOOT_REL_DIR}"
ACTIVE_GRUB_CFG="${ACTIVE_BOOT_DIR}/grub.cfg"
LEGACY_GRUB_CFG="${LEGACY_BOOT_DIR}/grub.cfg"
DIRECT_GRUB_CFG="${DIRECT_BOOT_DIR}/grub.cfg"
ACTIVE_GRUB_EFI="${ACTIVE_BOOT_DIR}/grubx64.efi"
ACTIVE_SHIM_EFI="${ACTIVE_BOOT_DIR}/shimx64.efi"
DIRECT_GRUB_EFI="${DIRECT_BOOT_DIR}/grubx64.efi"
DIRECT_SHIM_EFI="${DIRECT_BOOT_DIR}/shimx64.efi"
FALLBACK_BOOTX64_EFI="${FALLBACK_BOOT_DIR}/bootx64.efi"
FALLBACK_GRUBX64_EFI="${FALLBACK_BOOT_DIR}/grubx64.efi"

if [[ -f "$ACTIVE_GRUB_CFG" ]]; then
  GRUB_CFG="$ACTIVE_GRUB_CFG"
  ok "active grub.cfg detected under ${ACTIVE_BOOT_REL_DIR}"
elif [[ -f "$LEGACY_GRUB_CFG" ]]; then
  GRUB_CFG="$LEGACY_GRUB_CFG"
  warn "active grub.cfg fallback to legacy path (${LEGACY_BOOT_REL_DIR})"
else
  GRUB_CFG="$ACTIVE_GRUB_CFG"
fi

echo "active_grub_cfg_path: ${GRUB_CFG}" >>"$REPORT"
echo "active_grubx64_efi_path: ${ACTIVE_GRUB_EFI}" >>"$REPORT"
echo "active_shimx64_efi_path: ${ACTIVE_SHIM_EFI}" >>"$REPORT"
echo "direct_grub_cfg_path: ${DIRECT_GRUB_CFG}" >>"$REPORT"
echo "direct_grubx64_efi_path: ${DIRECT_GRUB_EFI}" >>"$REPORT"
echo "direct_shimx64_efi_path: ${DIRECT_SHIM_EFI}" >>"$REPORT"
echo "fallback_bootx64_efi_path: ${FALLBACK_BOOTX64_EFI}" >>"$REPORT"
echo "fallback_grubx64_efi_path: ${FALLBACK_GRUBX64_EFI}" >>"$REPORT"
recoverix_grub_report_boot_chain "$ESP_ROOT" "$REPORT"
recoverix_grub_trace_host_entry_sources "$REPORT"

if [[ -f "$ACTIVE_SHIM_EFI" ]]; then
  ok "active shimx64.efi present (${ACTIVE_BOOT_REL_DIR})"
else
  warn "active shimx64.efi missing (${ACTIVE_BOOT_REL_DIR})"
fi
if [[ -f "$ACTIVE_GRUB_EFI" ]]; then
  ok "active grubx64.efi present (${ACTIVE_BOOT_REL_DIR})"
else
  fail "active grubx64.efi missing (${ACTIVE_BOOT_REL_DIR})"
fi
if [[ -f "$DIRECT_SHIM_EFI" ]]; then
  ok "direct shimx64.efi present (${DIRECT_BOOT_REL_DIR})"
else
  fail "direct shimx64.efi missing (${DIRECT_BOOT_REL_DIR})"
fi
if [[ -f "$DIRECT_GRUB_EFI" ]]; then
  ok "direct grubx64.efi present (${DIRECT_BOOT_REL_DIR})"
else
  fail "direct grubx64.efi missing (${DIRECT_BOOT_REL_DIR})"
fi
if [[ -f "$DIRECT_GRUB_CFG" ]]; then
  ok "direct grub.cfg present (${DIRECT_BOOT_REL_DIR})"
else
  fail "direct grub.cfg missing (${DIRECT_BOOT_REL_DIR})"
fi
if [[ -f "$FALLBACK_BOOTX64_EFI" ]]; then
  ok "fallback bootx64.efi present (${FALLBACK_BOOT_REL_DIR})"
else
  fail "fallback bootx64.efi missing (${FALLBACK_BOOT_REL_DIR})"
fi
if [[ -f "$FALLBACK_GRUBX64_EFI" ]]; then
  ok "fallback grubx64.efi present for shim chain (${FALLBACK_BOOT_REL_DIR})"
else
  ok "fallback grubx64.efi absent; standalone bootx64.efi mode allowed"
fi

VMLINUX_ESP="${ESP_DIR}/vmlinuz-${KVER}"
INITRD_ESP="${ESP_DIR}/initrd.img-${KVER}-recoverix"

[[ -f "$GRUB_CFG" ]] && ok "grub.cfg exists" || fail "missing grub.cfg"
[[ ! -f "$VMLINUX_ESP" ]] && ok "ESP has no vmlinuz (bootloader-only policy)" || \
  fail "ESP must not contain ${RECOVERIX_ESP_REL_DIR}/vmlinuz-${KVER}"
[[ ! -f "$INITRD_ESP" ]] && ok "ESP has no initrd (bootloader-only policy)" || \
  fail "ESP must not contain ${RECOVERIX_ESP_REL_DIR}/initrd.img-${KVER}-recoverix"
[[ -f "${VMLINUX_HOST}" ]] && ok "Recovery Linux kernel staged" || fail "missing ${VMLINUX_HOST}"
[[ -f "${INITRD_HOST}" ]] && ok "Recovery Linux initrd staged" || fail "missing ${INITRD_HOST}"

if [[ -f "${ESP_DIR}/shimx64.efi" ]]; then
  ok "optional shimx64.efi present (EFI/Recoverix)"
else
  if [[ -f "$ACTIVE_SHIM_EFI" ]]; then
    ok "optional shimx64.efi present (active boot path)"
  else
    warn "shimx64.efi not staged (optional)"
  fi
fi

if [[ -f "$GRUB_CFG" ]]; then
  grep -q 'set default=windows_boot_manager' "$GRUB_CFG" && ok "grub.cfg defaults to Windows Boot Manager" || \
    fail "grub.cfg must default to Windows Boot Manager"
  grep -q -- '--hotkey=' "$GRUB_CFG" && ok "grub.cfg exposes RecoveryBoot hotkey" || \
    fail "grub.cfg missing RecoveryBoot hotkey"
  grep -qF "/boot/recoverix/vmlinuz-${KVER}" "$GRUB_CFG" && ok "grub.cfg kernel path (Recovery Linux)" || \
    fail "grub.cfg missing /boot/recoverix/vmlinuz-${KVER}"
  grep -qF "/boot/recoverix/initrd.img-${KVER}-recoverix" "$GRUB_CFG" && ok "grub.cfg initrd path (Recovery Linux)" || \
    fail "grub.cfg missing /boot/recoverix/initrd.img-${KVER}-recoverix"
  if grep -qF "/EFI/Recoverix/vmlinuz-${KVER}" "$GRUB_CFG"; then
    fail "grub.cfg still references ESP kernel path"
  else
    ok "grub.cfg has no ESP kernel path"
  fi
  grep -q 'recoverix.root=1' "$GRUB_CFG" && ok "grub.cfg recoverix.root=1" || fail "grub.cfg missing recoverix.root=1"
  _base_block="$(recoverix_grub_extract_menuentry_block "$GRUB_CFG" 'Recoverix Runtime')"
  if [[ -z "$_base_block" ]]; then
    fail "grub.cfg missing Recoverix Runtime menuentry"
  else
    if grep -q 'recoverix.safe=1' <<<"$_base_block"; then
      ok "grub.cfg default Recoverix Runtime uses recoverix.safe=1"
    else
      fail "grub.cfg default Recoverix Runtime missing recoverix.safe=1"
    fi
    if grep -q 'recoverix.gui=1' <<<"$_base_block"; then
      ok "grub.cfg default Recoverix Runtime uses recoverix.gui=1"
    else
      fail "grub.cfg default Recoverix Runtime missing recoverix.gui=1"
    fi
    if grep -q 'systemd.unit=multi-user.target' <<<"$_base_block"; then
      ok "grub.cfg default Recoverix Runtime uses multi-user.target"
    else
      fail "grub.cfg default Recoverix Runtime missing systemd.unit=multi-user.target"
    fi
    if grep -q 'root=tmpfs' <<<"$_base_block"; then
      ok "grub.cfg default Recoverix Runtime uses root=tmpfs"
    else
      fail "grub.cfg default Recoverix Runtime missing root=tmpfs"
    fi
    if grep -qw 'quiet' <<<"$_base_block" && \
       grep -q 'loglevel=3' <<<"$_base_block" && \
       grep -q 'rd.udev.log_level=3' <<<"$_base_block" && \
       grep -q 'systemd.show_status=0' <<<"$_base_block" && \
       grep -q 'vt.global_cursor_default=0' <<<"$_base_block"; then
      ok "grub.cfg default Recoverix Runtime uses quiet boot params"
    else
      fail "grub.cfg default Recoverix Runtime missing quiet boot params"
    fi
  fi
  # Diagnostic menu: systemd debug (verbose PID1 logging, quiet/splash removed).
  _dbg_block="$(recoverix_grub_extract_menuentry_block "$GRUB_CFG" 'Recoverix Runtime (systemd debug)')"
  if [[ -z "$_dbg_block" ]]; then
    fail "grub.cfg missing systemd debug menuentry"
  else
    ok "grub.cfg systemd debug menuentry present"
    if grep -q 'systemd.log_level=debug' <<<"$_dbg_block" && \
       grep -q 'systemd.log_target=console' <<<"$_dbg_block" && \
       grep -q 'systemd.show_status=1' <<<"$_dbg_block"; then
      ok "grub.cfg systemd debug sets debug logging params"
    else
      fail "grub.cfg systemd debug missing log_level/log_target/show_status params"
    fi
    if grep -qw 'quiet' <<<"$_dbg_block" || grep -qw 'splash' <<<"$_dbg_block"; then
      fail "grub.cfg systemd debug must not contain quiet/splash"
    else
      ok "grub.cfg systemd debug has quiet/splash removed"
    fi
    if grep -q 'systemd.unit=multi-user.target' <<<"$_dbg_block"; then
      ok "grub.cfg systemd debug uses multi-user.target"
    else
      fail "grub.cfg systemd debug missing systemd.unit=multi-user.target"
    fi
  fi

  if recoverix_grub_validate_external_menu_cfg "$GRUB_CFG" >/dev/null 2>&1; then
    ok "RecoveryBoot external grub.cfg menu-only (no prefix/configfile/source/ubuntu)"
  else
    recoverix_grub_dump_contamination "$REPORT" "active_grub_cfg" "$GRUB_CFG"
    fail "RecoveryBoot grub.cfg contamination: $(recoverix_grub_validate_external_menu_cfg "$GRUB_CFG" 2>/dev/null || true)"
  fi
  if recoverix_grub_cfg_includes_host_ubuntu "$GRUB_CFG"; then
    recoverix_grub_dump_contamination "$REPORT" "active_grub_cfg" "$GRUB_CFG"
    fail "RecoveryBoot grub.cfg includes Ubuntu host grub.cfg (configfile contamination)"
  else
    ok "RecoveryBoot grub.cfg has no /boot/grub or EFI/ubuntu dependency"
  fi
  ok "RecoveryBoot grub isolated from Ubuntu menu chain"

  _title=""
  for _title in "${RECOVERIX_GRUB_MENU_TITLES[@]}"; do
    _cnt="$(recoverix_grub_menuentry_count "$GRUB_CFG" "$_title")"
    if [[ "$_cnt" -eq 1 ]]; then
      ok "$(recoverix_grub_menuentry_pass_label "$_title")"
    elif [[ "$_cnt" -eq 0 ]]; then
      fail "active menuentry missing (${_title})"
    else
      fail "duplicate active menuentry (${_title} count=${_cnt})"
    fi
  done
fi

if [[ -f "$DIRECT_GRUB_CFG" ]]; then
  grep -q 'set default=recoverix_runtime' "$DIRECT_GRUB_CFG" && ok "direct grub.cfg defaults to Recoverix Runtime" || \
    fail "direct grub.cfg must default to Recoverix Runtime"
  if grep -q -- '--hotkey=' "$DIRECT_GRUB_CFG"; then
    fail "direct grub.cfg must not require RecoveryBoot hotkey"
  else
    ok "direct grub.cfg does not require hotkey"
  fi
  grep -qF "/boot/recoverix/vmlinuz-${KVER}" "$DIRECT_GRUB_CFG" && ok "direct grub.cfg kernel path (Recovery Linux)" || \
    fail "direct grub.cfg missing /boot/recoverix/vmlinuz-${KVER}"
  grep -qF "/boot/recoverix/initrd.img-${KVER}-recoverix" "$DIRECT_GRUB_CFG" && ok "direct grub.cfg initrd path (Recovery Linux)" || \
    fail "direct grub.cfg missing /boot/recoverix/initrd.img-${KVER}-recoverix"
  grep -q 'recoverix.root=1' "$DIRECT_GRUB_CFG" && ok "direct grub.cfg recoverix.root=1" || fail "direct grub.cfg missing recoverix.root=1"
  _direct_base_block="$(recoverix_grub_extract_menuentry_block "$DIRECT_GRUB_CFG" 'Recoverix Runtime')"
  if [[ -z "$_direct_base_block" ]]; then
    fail "direct grub.cfg missing Recoverix Runtime menuentry"
  else
    grep -q 'recoverix.safe=1' <<<"$_direct_base_block" && ok "direct Recoverix Runtime uses recoverix.safe=1" || \
      fail "direct Recoverix Runtime missing recoverix.safe=1"
    grep -q 'recoverix.gui=1' <<<"$_direct_base_block" && ok "direct Recoverix Runtime uses recoverix.gui=1" || \
      fail "direct Recoverix Runtime missing recoverix.gui=1"
    grep -q 'systemd.unit=multi-user.target' <<<"$_direct_base_block" && ok "direct Recoverix Runtime uses multi-user.target" || \
      fail "direct Recoverix Runtime missing systemd.unit=multi-user.target"
    grep -q 'root=tmpfs' <<<"$_direct_base_block" && ok "direct Recoverix Runtime uses root=tmpfs" || \
      fail "direct Recoverix Runtime missing root=tmpfs"
  fi
  if recoverix_grub_validate_external_menu_cfg "$DIRECT_GRUB_CFG" >/dev/null 2>&1; then
    ok "direct grub.cfg menu-only (no prefix/configfile/source/ubuntu)"
  else
    recoverix_grub_dump_contamination "$REPORT" "direct_grub_cfg" "$DIRECT_GRUB_CFG"
    fail "direct grub.cfg contamination: $(recoverix_grub_validate_external_menu_cfg "$DIRECT_GRUB_CFG" 2>/dev/null || true)"
  fi
  for _title in "${RECOVERIX_GRUB_DIRECT_MENU_TITLES[@]}"; do
    _cnt="$(recoverix_grub_menuentry_count "$DIRECT_GRUB_CFG" "$_title")"
    if [[ "$_cnt" -eq 1 ]]; then
      ok "direct $(recoverix_grub_menuentry_pass_label "$_title")"
    elif [[ "$_cnt" -eq 0 ]]; then
      fail "direct menuentry missing (${_title})"
    else
      fail "duplicate direct menuentry (${_title} count=${_cnt})"
    fi
  done
fi

UBUNTU_GRUB_EFI="${ESP_ROOT}/EFI/ubuntu/grubx64.efi"
if [[ -f "$ACTIVE_GRUB_EFI" ]]; then
  if recoverix_grub_verify_standalone_efi "$ACTIVE_GRUB_EFI" "$REPORT"; then
    ok "active grubx64.efi embeds standalone marker and RecoveryBoot menu chain"
  else
    fail "active grubx64.efi standalone EFI contamination or missing marker"
  fi
  if strings "$ACTIVE_GRUB_EFI" 2>/dev/null | grep -qF "$RECOVERIX_GRUB_STANDALONE_MARKER"; then
    ok "standalone diagnostic marker present in grubx64.efi"
  else
    fail "standalone diagnostic marker missing in grubx64.efi"
  fi
  if strings "$ACTIVE_GRUB_EFI" 2>/dev/null | grep -qE 'search[[:space:]].*--fs-uuid[[:space:]].*--set=root'; then
    ok "embedded bootstrap has search --fs-uuid --set=root"
  else
    fail "embedded bootstrap missing search --fs-uuid --set=root in grubx64.efi"
  fi
  if strings "$ACTIVE_GRUB_EFI" 2>/dev/null | grep -qF '($root)/'"${ACTIVE_BOOT_REL_DIR}"'/grub.cfg' || \
     strings "$ACTIVE_GRUB_EFI" 2>/dev/null | grep -qF "${ACTIVE_BOOT_REL_DIR}/grub.cfg"; then
    ok "embedded bootstrap configfile target matches RecoveryBoot menu path"
  else
    fail "embedded bootstrap configfile path mismatch in grubx64.efi"
  fi
  _esp_uuid_verify="$(findmnt -no UUID "$ESP_ROOT" 2>/dev/null || true)"
  if [[ -n "$_esp_uuid_verify" ]] && strings "$ACTIVE_GRUB_EFI" 2>/dev/null | grep -qF "$_esp_uuid_verify"; then
    ok "embedded bootstrap embeds esp_uuid for ESP search"
    echo "esp_uuid_detected: ${_esp_uuid_verify}" >>"$REPORT"
    echo "embedded_root_search_cmd: search --no-floppy --fs-uuid --set=root ${_esp_uuid_verify}" >>"$REPORT"
  else
    fail "embedded grub cannot locate ESP (esp_uuid missing in grubx64.efi)"
  fi
  if [[ -f "$UBUNTU_GRUB_EFI" ]] && recoverix_grub_efi_identical "$ACTIVE_GRUB_EFI" "$UBUNTU_GRUB_EFI"; then
    fail "active grubx64.efi byte-identical to EFI/ubuntu/grubx64.efi"
  elif [[ -f "$UBUNTU_GRUB_EFI" ]]; then
    ok "active grubx64.efi differs from EFI/ubuntu/grubx64.efi"
  else
    warn "EFI/ubuntu/grubx64.efi not present for comparison"
  fi
fi

if [[ -f "$DIRECT_GRUB_EFI" ]]; then
  if RECOVERIX_ESP_ACTIVE_REL_DIR="$DIRECT_BOOT_REL_DIR" recoverix_grub_verify_standalone_efi "$DIRECT_GRUB_EFI" "$REPORT"; then
    ok "direct grubx64.efi embeds standalone marker and direct Recoverix menu chain"
  else
    fail "direct grubx64.efi standalone EFI contamination or missing marker"
  fi
  if strings "$DIRECT_GRUB_EFI" 2>/dev/null | grep -qF "$RECOVERIX_GRUB_STANDALONE_MARKER"; then
    ok "standalone diagnostic marker present in direct grubx64.efi"
  else
    fail "standalone diagnostic marker missing in direct grubx64.efi"
  fi
  if strings "$DIRECT_GRUB_EFI" 2>/dev/null | grep -qF '($root)/'"${DIRECT_BOOT_REL_DIR}"'/grub.cfg' || \
     strings "$DIRECT_GRUB_EFI" 2>/dev/null | grep -qF "${DIRECT_BOOT_REL_DIR}/grub.cfg"; then
    ok "direct embedded bootstrap configfile target matches direct menu path"
  else
    fail "direct embedded bootstrap configfile path mismatch in grubx64.efi"
  fi
  if [[ -f "$UBUNTU_GRUB_EFI" ]] && recoverix_grub_efi_identical "$DIRECT_GRUB_EFI" "$UBUNTU_GRUB_EFI"; then
    fail "direct grubx64.efi byte-identical to EFI/ubuntu/grubx64.efi"
  elif [[ -f "$UBUNTU_GRUB_EFI" ]]; then
    ok "direct grubx64.efi differs from EFI/ubuntu/grubx64.efi"
  fi
fi

if [[ -f "$FALLBACK_GRUBX64_EFI" ]]; then
  if recoverix_grub_verify_standalone_efi "$FALLBACK_GRUBX64_EFI" "$REPORT"; then
    ok "fallback grubx64.efi embeds standalone marker and RecoveryBoot menu chain"
  else
    fail "fallback grubx64.efi standalone EFI contamination or missing marker"
  fi
elif [[ -f "$FALLBACK_BOOTX64_EFI" ]]; then
  if recoverix_grub_verify_standalone_efi "$FALLBACK_BOOTX64_EFI" "$REPORT"; then
    ok "fallback bootx64.efi is standalone Recoverix GRUB"
  else
    warn "fallback bootx64.efi is not standalone GRUB; expected if it is shim"
  fi
fi

HOST_UBUNTU_GRUB="/boot/grub/grub.cfg"
if [[ -f "$HOST_UBUNTU_GRUB" ]]; then
  _host_rx=0
  for _title in "${RECOVERIX_GRUB_MENU_TITLES[@]}"; do
    _hcnt="$(recoverix_grub_menuentry_count "$HOST_UBUNTU_GRUB" "$_title")"
    if [[ "$_hcnt" -gt 0 ]]; then
      _host_rx=1
      if [[ "$_hcnt" -gt 1 ]]; then
        fail "duplicate Recoverix entry in host Ubuntu grub.cfg (${_title} count=${_hcnt})"
      else
        fail "host Ubuntu grub.cfg contaminated with Recoverix entry (${_title})"
      fi
    fi
  done
  if [[ $_host_rx -eq 0 ]]; then
    ok "host Ubuntu grub.cfg has no Recoverix menuentries"
  fi
  if [[ -f /etc/grub.d/41_recoverix ]]; then
    fail "Recoverix host entry source still present (/etc/grub.d/41_recoverix)"
  else
    ok "no /etc/grub.d/41_recoverix host snippet"
  fi
else
  warn "host Ubuntu grub.cfg not found (/boot/grub/grub.cfg)"
fi

UBUNTU_ESP_GRUB="${ESP_ROOT}/EFI/ubuntu/grub.cfg"
if [[ -f "$UBUNTU_ESP_GRUB" ]]; then
  if recoverix_grub_cfg_includes_host_ubuntu "$UBUNTU_ESP_GRUB"; then
    ok "Ubuntu ESP grub.cfg chains to host (expected for F12 Ubuntu path)"
  else
    warn "Ubuntu ESP grub.cfg has no obvious host configfile chain"
  fi
  _ucnt="$(recoverix_grub_menuentry_count "$UBUNTU_ESP_GRUB" "Recoverix Runtime")"
  if [[ "$_ucnt" -gt 0 ]]; then
    fail "Ubuntu ESP grub.cfg contains Recoverix entries (should be host-only chain)"
  else
    ok "Ubuntu ESP grub.cfg has no Recoverix menuentries"
  fi
fi

if [[ -f "$INITRD_ESP" ]]; then
  fail "legacy ESP initrd must be removed (${INITRD_ESP})"
fi
if [[ -f "$VMLINUX_ESP" ]]; then
  fail "legacy ESP vmlinuz must be removed (${VMLINUX_ESP})"
fi

SQUASHFS_HOST="${HOST_BOOT}/runtime.squashfs"
if [[ -f "$SQUASHFS_HOST" ]]; then
  ok "host runtime.squashfs present (${SQUASHFS_HOST})"
  echo "runtime_squashfs_host: ${SQUASHFS_HOST}" >>"$REPORT"
  echo "runtime_squashfs_bytes: $(stat -c '%s' "$SQUASHFS_HOST")" >>"$REPORT"
  if [[ -f "$GRUB_CFG" ]] && grep -q "recoverix.uuid=" "$GRUB_CFG"; then
    if [[ -n "$RECOVERY_LINUX_UUID" ]] && grep -qF "recoverix.uuid=${RECOVERY_LINUX_UUID}" "$GRUB_CFG"; then
      ok "grub.cfg recoverix.uuid matches Recovery Linux UUID"
    else
      warn "grub.cfg recoverix.uuid may not match RECOVERY_LINUX_UUID (set env or remount p4)"
    fi
  fi
else
  fail "missing host ${SQUASHFS_HOST} (initramfs squashfs path)"
fi

# Microsoft tree must remain intact (no overwrite detection via forbidden file mtimes in Microsoft/Boot)
MICROSOFT_BOOT="${ESP_ROOT}/EFI/Microsoft/Boot"
if [[ -d "$MICROSOFT_BOOT" ]]; then
  if [[ -f "${MICROSOFT_BOOT}/bootmgfw.efi" ]]; then
    ok "EFI/Microsoft/Boot/bootmgfw.efi still present"
  else
    fail "bootmgfw.efi missing under EFI/Microsoft/Boot"
  fi
else
  warn "EFI/Microsoft/Boot not present on this ESP"
fi

# Ensure we did not install under EFI/Microsoft
if [[ -d "${ESP_ROOT}/EFI/Microsoft/Recoverix" ]]; then
  fail "unexpected EFI/Microsoft/Recoverix — must use EFI/Recoverix only"
else
  ok "no EFI/Microsoft/Recoverix contamination"
fi

echo "summary: PASS=${PASS} WARN=${WARN} FAIL=${FAIL}" | tee -a "$REPORT"
log "Report: ${REPORT}"

[[ $FAIL -gt 0 ]] && exit 2
[[ $WARN -gt 0 ]] && exit 1
exit 0
