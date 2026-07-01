#!/usr/bin/env bash
# Safety guards for host GRUB entry install (additive only; no EFI/shim/grub-install).

set -euo pipefail

HOST_GRUB_FORBIDDEN=(
  grub-install
  grub-install.real
  efibootmgr
  shim-install
  mokutil
)

host_grub_die() {
  printf '[recoverix-deploy] ERROR: %s\n' "$*" >&2
  exit 2
}

host_grub_require_root() {
  [[ "$(id -u)" -eq 0 ]] || host_grub_die "run as root (sudo)"
}

host_grub_assert_safe_command() {
  local base
  base="$(basename "$1")"
  local forbidden
  for forbidden in "${HOST_GRUB_FORBIDDEN[@]}"; do
    if [[ "$base" == "$forbidden" ]]; then
      host_grub_die "forbidden command in deploy phase: ${base}"
    fi
  done
}

host_grub_backup_file() {
  local f="$1"
  local ts
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  [[ -f "$f" ]] || return 0
  cp -a "$f" "${f}.recoverix-backup-${ts}"
  printf '[recoverix-deploy] backup: %s.recoverix-backup-%s\n' "$f" "$ts"
}

host_grub_verify_default_unchanged() {
  local before="${1:-}"
  local after
  after="$(grep -E '^GRUB_DEFAULT=' /etc/default/grub 2>/dev/null | head -1 || true)"
  if [[ -n "$before" && -n "$after" && "$before" != "$after" ]]; then
    host_grub_die "GRUB_DEFAULT changed (${before} -> ${after}) — abort"
  fi
  printf '[recoverix-deploy] GRUB_DEFAULT: %s\n' "${after:-unknown}"
}

host_grub_report_paths() {
  printf '[recoverix-deploy] root UUID: %s\n' "$(findmnt -no UUID / 2>/dev/null || echo unknown)"
  printf '[recoverix-deploy] /boot/recoverix: %s\n' "$(ls -la /boot/recoverix 2>/dev/null | wc -l) files"
}
