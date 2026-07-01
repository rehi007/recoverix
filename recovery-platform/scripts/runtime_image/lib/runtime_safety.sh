#!/usr/bin/env bash
# Forbid host bootloader / EFI mutation during image-build phase.

RUNTIME_FORBIDDEN_CMDS=(
  efibootmgr
  grub-install
  grub-mkdevicemap
  update-grub
  update-grub2
  grub-reboot
  shim-install
  mokutil
)

runtime_safety_assert_no_host_boot_mutation() {
  local cmd="$1"
  local base
  base="$(basename "$cmd")"
  local forbidden
  for forbidden in "${RUNTIME_FORBIDDEN_CMDS[@]}"; do
    if [[ "$base" == "$forbidden" ]]; then
      printf '[runtime-image] BLOCKED: %s — host boot modification forbidden in image-build phase\n' "$base" >&2
      return 1
    fi
  done
  return 0
}

# Wrap: refuse forbidden commands if invoked from runtime_image scripts.
runtime_safety_check_invocation() {
  local caller="${1:-}"
  case "$caller" in
    *grub-install*|*efibootmgr*|*update-grub*) return 1 ;;
  esac
  return 0
}
