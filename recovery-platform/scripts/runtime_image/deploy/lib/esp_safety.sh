#!/usr/bin/env bash
# ESP staging safety: additive EFI/Recoverix only; never touch Windows NVRAM or Microsoft boot chain.

set -euo pipefail

RECOVERIX_ESP_TIMEOUT_SEC="${RECOVERIX_ESP_TIMEOUT_SEC:-30}"
RECOVERIX_ESP_MOUNT="${RECOVERIX_ESP_MOUNT:-/mnt/recoverix-esp-staging}"
RECOVERIX_ESP_REL_DIR="${RECOVERIX_ESP_REL_DIR:-EFI/Recoverix}"
WINDOWS_BOOTMGFW_REL="EFI/Microsoft/Boot/bootmgfw.efi"

ESP_FORBIDDEN_CMDS=(
  efibootmgr
  grub-install
  grub-install.real
  shim-install
  mokutil
)

# Paths that must never be written or removed by Recoverix ESP staging.
ESP_FORBIDDEN_WRITE_PREFIXES=(
  EFI/Microsoft
  EFI/Boot/bootmgfw.efi
)

recoverix_esp_die() {
  printf '[recoverix-esp] ERROR: %s\n' "$*" >&2
  exit 2
}

recoverix_esp_log() {
  printf '[recoverix-esp] %s\n' "$*"
}

recoverix_esp_require_root() {
  [[ "$(id -u)" -eq 0 ]] || recoverix_esp_die "run as root (sudo)"
}

recoverix_esp_assert_safe_command() {
  local base
  base="$(basename "$1")"
  local forbidden
  for forbidden in "${ESP_FORBIDDEN_CMDS[@]}"; do
    if [[ "$base" == "$forbidden" ]]; then
      recoverix_esp_die "forbidden command in ESP deploy phase: ${base}"
    fi
  done
}

recoverix_esp_rel_to_abs() {
  local esp_root="$1"
  local rel="${2//\\//}"
  printf '%s/%s' "${esp_root%/}" "$rel"
}

recoverix_esp_assert_safe_dest() {
  local rel="${1//\\//}"
  local norm="${rel#/}"
  norm="$(printf '%s' "$norm" | tr '[:upper:]' '[:lower:]')"
  local prefix
  for prefix in "${ESP_FORBIDDEN_WRITE_PREFIXES[@]}"; do
  local pl="${prefix//\\//}"
  pl="$(printf '%s' "$pl" | tr '[:upper:]' '[:lower:]')"
    if [[ "$norm" == "$pl" || "$norm" == "$pl"/* ]]; then
      recoverix_esp_die "refusing to write forbidden ESP path: ${rel}"
    fi
  done
  if [[ "$norm" == *bootmgfw.efi ]]; then
    recoverix_esp_die "refusing to touch bootmgfw.efi: ${rel}"
  fi
}

recoverix_esp_with_timeout() {
  timeout "${RECOVERIX_ESP_TIMEOUT_SEC}" "$@"
}

# Find mounted ESP path or block device for ESP (stdout: mount path or device).
recoverix_esp_discover() {
  local candidate mount m dev

  for candidate in /boot/efi /efi; do
    if mountpoint -q "$candidate" 2>/dev/null; then
      mount="$(findmnt -no TARGET "$candidate" 2>/dev/null || printf '%s' "$candidate")"
      printf '%s' "$mount"
      return 0
    fi
  done

  while IFS= read -r m; do
    [[ -n "$m" && -d "${m}/EFI" ]] || continue
    printf '%s' "$m"
    return 0
  done < <(findmnt -rn -t vfat -o TARGET 2>/dev/null || true)

  dev="$(blkid -t PARTTYPE=c12a7328-f81f-11d2-ba4a-00a0c93ec93b -o device 2>/dev/null | head -1 || true)"
  if [[ -n "$dev" ]]; then
    printf '%s' "$dev"
    return 0
  fi

  return 1
}

recoverix_esp_discover_timed() {
  local lib_path="${1:?}"
  local out=""
  if out="$(recoverix_esp_with_timeout bash -c "source \"${lib_path}\"; recoverix_esp_discover")"; then
    printf '%s' "$out"
    return 0
  fi
  return 1
}

# Mount ESP at RECOVERIX_ESP_MOUNT if needed. Sets RECOVERIX_ESP_MOUNTED_BY_US=1 when we mount.
recoverix_esp_ensure_mounted() {
  local discovered="${1:-}"
  local target="${RECOVERIX_ESP_MOUNT}"
  RECOVERIX_ESP_MOUNTED_BY_US=0
  RECOVERIX_ESP_ROOT=""

  if [[ -z "$discovered" ]]; then
    recoverix_esp_die "ESP not found (timeout ${RECOVERIX_ESP_TIMEOUT_SEC}s)"
  fi

  if [[ -b "$discovered" ]]; then
    mkdir -p "$target"
    if mountpoint -q "$target" 2>/dev/null; then
      RECOVERIX_ESP_ROOT="$target"
      return 0
    fi
    recoverix_esp_with_timeout mount -t vfat -o umask=0077 "$discovered" "$target" || \
      recoverix_esp_with_timeout mount -t vfat "$discovered" "$target" || \
      recoverix_esp_die "failed to mount ESP device ${discovered} at ${target}"
    RECOVERIX_ESP_MOUNTED_BY_US=1
    RECOVERIX_ESP_ROOT="$target"
    return 0
  fi

  if mountpoint -q "$discovered" 2>/dev/null; then
    RECOVERIX_ESP_ROOT="$discovered"
    return 0
  fi

  recoverix_esp_die "ESP path is not a block device or mountpoint: ${discovered}"
}

recoverix_esp_umount_if_mounted_by_us() {
  [[ "${RECOVERIX_ESP_MOUNTED_BY_US:-0}" -eq 1 ]] || return 0
  umount "${RECOVERIX_ESP_ROOT}" 2>/dev/null || true
}

recoverix_esp_verify_rw() {
  local esp_root="$1"
  local probe="${esp_root}/${RECOVERIX_ESP_REL_DIR}/.recoverix-esp-rw-probe"
  recoverix_esp_assert_safe_dest "${RECOVERIX_ESP_REL_DIR}/.recoverix-esp-rw-probe"
  mkdir -p "${esp_root}/${RECOVERIX_ESP_REL_DIR}"
  if ! echo recoverix-esp-probe >"$probe" 2>/dev/null; then
    recoverix_esp_die "ESP not writable at ${RECOVERIX_ESP_REL_DIR}"
  fi
  rm -f "$probe"
}

recoverix_esp_free_bytes() {
  local esp_root="$1"
  local free_blocks block_size

  free_blocks="$(stat -f -c '%a' "$esp_root" 2>/dev/null || printf '0')"
  block_size="$(stat -f -c '%S' "$esp_root" 2>/dev/null || printf '0')"
  if [[ ! "$free_blocks" =~ ^[0-9]+$ || ! "$block_size" =~ ^[0-9]+$ ]]; then
    printf '0'
    return 0
  fi
  printf '%s' "$((free_blocks * block_size))"
}

recoverix_esp_require_free_space() {
  local esp_root="$1"
  local min_bytes="${2:?}"
  local free_bytes

  free_bytes="$(recoverix_esp_free_bytes "$esp_root")"
  if [[ ! "$free_bytes" =~ ^[0-9]+$ ]]; then
    recoverix_esp_die "unable to determine ESP free space"
  fi
  if (( free_bytes < min_bytes )); then
    recoverix_esp_die "ESP free space too low (${free_bytes} bytes < required ${min_bytes})"
  fi
}

recoverix_esp_bootmgfw_path() {
  local esp_root="$1"
  recoverix_esp_rel_to_abs "$esp_root" "$WINDOWS_BOOTMGFW_REL"
}

recoverix_esp_snapshot_bootmgfw() {
  local esp_root="$1"
  local out="$2"
  local path
  path="$(recoverix_esp_bootmgfw_path "$esp_root")"
  if [[ -f "$path" ]]; then
    stat -c 'size=%s mtime=%Y inode=%i' "$path" >"$out"
    sha256sum "$path" >>"$out" 2>/dev/null || true
    printf 'present=yes\n' >>"$out"
  else
    printf 'present=no\n' >"$out"
  fi
}

recoverix_esp_verify_bootmgfw_unchanged() {
  local esp_root="$1"
  local before="$2"
  local path after_fp
  path="$(recoverix_esp_bootmgfw_path "$esp_root")"
  if [[ ! -f "$before" ]]; then
    recoverix_esp_die "missing bootmgfw snapshot: ${before}"
  fi
  if grep -q '^present=no$' "$before" 2>/dev/null; then
    [[ ! -f "$path" ]] && return 0
    recoverix_esp_die "bootmgfw.efi appeared during staging (was absent)"
  fi
  [[ -f "$path" ]] || recoverix_esp_die "bootmgfw.efi missing after staging"
  after_fp="$(mktemp)"
  recoverix_esp_snapshot_bootmgfw "$esp_root" "$after_fp"
  if ! diff -q "$before" "$after_fp" >/dev/null 2>&1; then
    rm -f "$after_fp"
    recoverix_esp_die "EFI/Microsoft/Boot/bootmgfw.efi changed — abort"
  fi
  rm -f "$after_fp"
}

recoverix_esp_dir() {
  local esp_root="$1"
  recoverix_esp_rel_to_abs "$esp_root" "$RECOVERIX_ESP_REL_DIR"
}
