#!/usr/bin/env bash
# Purge Recoverix menuentries from Ubuntu host GRUB (update-grub regeneration).
# Usage: sudo ./33_purge_host_grub_recoverix.sh
#
# RecoveryBoot uses EFI/RecoveryBoot/grub.cfg only — host /boot/grub/grub.cfg must stay clean.

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RI_DIR="$(cd "${DIR}/.." && pwd)"
# shellcheck source=../lib/common.sh
source "${RI_DIR}/lib/common.sh"
# shellcheck source=lib/host_grub_safety.sh
source "${DIR}/lib/host_grub_safety.sh"
# shellcheck source=lib/grub_boot_chain.sh
source "${DIR}/lib/grub_boot_chain.sh"

host_grub_require_root
host_grub_assert_safe_command "$0"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/host_grub_purge_${TS}.txt"
mkdir -p "${REPORT_DIR}"

{
  echo "timestamp: ${TS}"
  echo "nvram_modified: false"
  echo "efi_modified: false"
} >"$REPORT"

recoverix_grub_trace_host_entry_sources "$REPORT"
recoverix_grub_purge_host_entries "$REPORT" || host_grub_die "purge failed — see ${REPORT}"

printf '[recoverix-deploy] host Ubuntu grub.cfg Recoverix purge complete\n'
printf '[recoverix-deploy] Report: %s\n' "$REPORT"
