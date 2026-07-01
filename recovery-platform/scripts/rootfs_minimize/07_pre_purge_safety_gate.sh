#!/usr/bin/env bash
# Pre-purge safety gate — run BEFORE Tier1 apply (or standalone audit).
# Usage:
#   sudo ./07_pre_purge_safety_gate.sh [tier1|tier2|all]
#   sudo RUN_BOOTABILITY=0 ./07_pre_purge_safety_gate.sh tier1   # skip heavy initramfs/grub regen
#
# Exit 0 = PASS, 1 = WARN only, 2 = FAIL in gate report.

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_DIR="$DIR"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/exit_codes.sh
source "${DIR}/lib/exit_codes.sh"
# shellcheck source=lib/host_safety.sh
source "${DIR}/lib/host_safety.sh"
# shellcheck source=lib/boot_critical.sh
source "${DIR}/lib/boot_critical.sh"
# shellcheck source=lib/dependency_analyzer.sh
source "${DIR}/lib/dependency_analyzer.sh"
# shellcheck source=lib/validators.sh
source "${DIR}/lib/validators.sh"

require_root
TIER="${1:-tier1}"
RUN_BOOTABILITY="${RUN_BOOTABILITY:-1}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/pre_purge_gate_${TIER}_${TS}.log"
SIM_LOG="${REPORT_DIR}/simulate_${TIER}_${TS}.log"

read_list() {
  local file="$1"
  while read -r pkg; do
    [[ -z "$pkg" || "$pkg" =~ ^# ]] && continue
    echo "$pkg"
  done < "$file"
}

log "=== Pre-purge safety gate (tier=${TIER}) ==="
log "ROOTFS=${ROOTFS_RESOLVED}"
: > "$REPORT"

# --- 9. Host safety lock (first) ---
validate_host_safety_lock "$ROOTFS_RESOLVED" "$REPORT"

mount_chroot_fs

# Build purge package array
PURGE_PKGS=()
case "$TIER" in
  tier1)
    while read -r p; do PURGE_PKGS+=("$p"); done < <(read_list "${DIR}/packages_purge_tier1.txt")
    ;;
  tier2)
    while read -r p; do PURGE_PKGS+=("$p"); done < <(read_list "${DIR}/packages_purge_tier1.txt"; read_list "${DIR}/packages_purge_tier2.txt")
    ;;
  all)
    while read -r p; do PURGE_PKGS+=("$p"); done < <(read_list "${DIR}/packages_purge_tier1.txt"; read_list "${DIR}/packages_purge_tier2.txt")
    ;;
  *) die "usage: $0 [tier1|tier2|all]" ;;
esac

# Filter to installed only
INSTALLED=()
for pkg in "${PURGE_PKGS[@]}"; do
  host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q installed && INSTALLED+=("$pkg")
done
PURGE_PKGS=("${INSTALLED[@]}")
log "Installed purge candidates: ${#PURGE_PKGS[@]}"

# --- 1. Boot critical: purge list must not include patterns ---
if [[ ${#PURGE_PKGS[@]} -gt 0 ]]; then
  validate_purge_list_not_critical "$REPORT" "${PURGE_PKGS[@]}"
else
  printf 'PASS\tboot_critical_purge_list\tno packages to purge\n' >> "$REPORT"
fi

# --- 2. apt simulate + REMV critical check ---
if [[ ${#PURGE_PKGS[@]} -gt 0 ]]; then
  log "apt purge simulation..."
  set +e
  chroot_run "DEBIAN_FRONTEND=noninteractive apt-get -y -s purge ${PURGE_PKGS[*]}" 2>&1 | tee "$SIM_LOG"
  set -e
  validate_simulate_remv_not_critical "$SIM_LOG" "$REPORT"
else
  printf 'PASS\tboot_critical_simulate\tskipped empty purge set\n' >> "$REPORT"
fi

# --- 3. Dependency tree analyzer ---
if [[ ${#PURGE_PKGS[@]} -gt 0 ]]; then
  analyze_dependency_impact "$REPORT" "${PURGE_PKGS[@]}"
else
  printf 'PASS\tdependency_tree\tempty purge set\n' >> "$REPORT"
fi

# --- 4. Kernel safety ---
validate_kernel_safety "$REPORT"

# --- 5. EFI runtime (warn if ESP not in tree) ---
validate_efi_runtime "$REPORT"

# --- 6. Bootability (initramfs + grub-mkconfig) ---
if [[ "$RUN_BOOTABILITY" == "1" ]]; then
  validate_runtime_bootability "$REPORT"
else
  printf 'SKIP\tbootability\tRUN_BOOTABILITY=0\n' >> "$REPORT"
  log "SKIP bootability tests (RUN_BOOTABILITY=0)"
fi

# --- Critical packages still installed (baseline) ---
validate_critical_packages_still_installed "$REPORT"

# --- 7. GTK GUI runtime enhanced ---
validate_gtk_runtime_enhanced "$REPORT"

# --- 8. SquashFS readiness ---
validate_squashfs_readiness "$REPORT"

# --- 7b. Runtime size report ---
write_runtime_size_report "$REPORT"

# Summary (safe counts — avoid grep -c || echo 0 producing "0\n0")
local_gate_pass=0 local_gate_warn=0 local_gate_fail=0 local_gate_skip=0
rootfs_count_gate_status "$REPORT" local_gate
log "Gate report: ${REPORT}"
log "Summary: PASS=${local_gate_PASS} FAIL=${local_gate_FAIL} WARN=${local_gate_WARN} SKIP=${local_gate_SKIP}"

if [[ "${local_gate_FAIL}" -gt 0 ]]; then
  log "PRE-PURGE GATE FAILED — fix issues before Tier1 purge"
  exit "$RC_FAIL"
fi

if [[ "${local_gate_WARN}" -gt 0 ]]; then
  log "PRE-PURGE GATE PASSED WITH WARNINGS (${local_gate_WARN}) — review report before apt purge apply"
  exit "$RC_WARN"
fi

log "PRE-PURGE GATE PASSED — review report before apt purge apply"
exit "$RC_PASS"
