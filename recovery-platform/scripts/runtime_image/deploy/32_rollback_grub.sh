#!/usr/bin/env bash
# Rollback Recoverix host GRUB entry and optionally remove staged /boot/recoverix.
# Usage: sudo ./32_rollback_grub.sh [--remove-artifacts]

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/host_grub_safety.sh
source "${DIR}/lib/host_grub_safety.sh"

host_grub_require_root
host_grub_assert_safe_command "$0"

REMOVE_ARTIFACTS=0
[[ "${1:-}" == "--remove-artifacts" ]] && REMOVE_ARTIFACTS=1

GRUB_SNIPPET="/etc/grub.d/41_recoverix"
GRUB_DEFAULT_BEFORE="$(grep -E '^GRUB_DEFAULT=' /etc/default/grub 2>/dev/null | head -1 || true)"

printf '[recoverix-deploy] removing %s\n' "$GRUB_SNIPPET"
rm -f "$GRUB_SNIPPET"

printf '[recoverix-deploy] update-grub...\n'
update-grub

host_grub_verify_default_unchanged "$GRUB_DEFAULT_BEFORE"

if [[ $REMOVE_ARTIFACTS -eq 1 ]]; then
  printf '[recoverix-deploy] removing /boot/recoverix\n'
  rm -rf /boot/recoverix
fi

printf '[recoverix-deploy] rollback complete\n'
