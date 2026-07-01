#!/usr/bin/env bash
# Simulate apt purge for tier lists; block if a keep-package would be removed.
# Usage: sudo ./02_generate_purge_plan.sh [tier1|tier2|all]

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/exit_codes.sh
source "${DIR}/lib/exit_codes.sh"
# shellcheck source=lib/boot_critical.sh
source "${DIR}/lib/boot_critical.sh"
require_root

TIER="${1:-tier1}"
PLAN="${REPORT_DIR}/purge_plan_${TIER}_$(date -u +%Y%m%dT%H%M%SZ).sh"
BLOCKED="${REPORT_DIR}/purge_blocked_${TIER}.txt"
: > "$BLOCKED"

read_list() {
  local file="$1"
  while read -r pkg; do
    [[ -z "$pkg" || "$pkg" =~ ^# ]] && continue
    echo "$pkg"
  done < "$file"
}

KEEP_SET="$(read_list "${DIR}/packages_keep.txt" | sort -u)"
PURGE_LIST=""
case "$TIER" in
  tier1) PURGE_LIST="$(read_list "${DIR}/packages_purge_tier1.txt")" ;;
  tier2) PURGE_LIST="$(read_list "${DIR}/packages_purge_tier2.txt")" ;;
  all)
    PURGE_LIST="$(read_list "${DIR}/packages_purge_tier1.txt"; read_list "${DIR}/packages_purge_tier2.txt")"
    ;;
  *) die "usage: $0 [tier1|tier2|all]" ;;
esac

mount_chroot_fs

{
  echo "#!/usr/bin/env bash"
  echo "# Auto-generated purge plan — REVIEW before running 03_minimize_apply.sh"
  echo "# ROOTFS=${ROOTFS_RESOLVED}"
  echo "# Tier=${TIER}"
  echo "set -euo pipefail"
  echo "ROOTFS=\"${ROOTFS_RESOLVED}\""
  echo "source \"${DIR}/lib/common.sh\""
  echo "require_root"
  echo "mount_chroot_fs"
  echo
  echo "# --- apt purge (inside chroot) ---"
} > "$PLAN"

INSTALLED_PURGE=()
while read -r pkg; do
  [[ -z "$pkg" ]] && continue
  if ! host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q installed; then
    continue
  fi
  if echo "$KEEP_SET" | grep -qx "$pkg"; then
    echo "BLOCKED (in keep list): $pkg" >> "$BLOCKED"
    continue
  fi
  if filter_purge_skip_keep_kernel "$pkg"; then
    echo "BLOCKED (KEEP_KERNEL_FLAVOR=${KEEP_KERNEL_FLAVOR}): $pkg" >> "$BLOCKED"
    log "Excluding keep kernel from purge: ${pkg}"
    continue
  fi
  INSTALLED_PURGE+=("$pkg")
done <<< "$PURGE_LIST"

if [[ ${#INSTALLED_PURGE[@]} -eq 0 ]]; then
  log "No installed packages to purge in tier ${TIER}"
  exit 0
fi

GATE_SNAP="${REPORT_DIR}/purge_gate_${TIER}.log"
: > "$GATE_SNAP"
validate_purge_list_not_critical "$GATE_SNAP" "${INSTALLED_PURGE[@]}"

log "Simulating purge of ${#INSTALLED_PURGE[@]} packages..."
SIM_OUT="${REPORT_DIR}/simulate_${TIER}.log"
set +e
chroot_run "DEBIAN_FRONTEND=noninteractive apt-get -y -s purge ${INSTALLED_PURGE[*]}" 2>&1 | tee "$SIM_OUT"
apt_sim_rc=${PIPESTATUS[0]}
set -e
if [[ "$apt_sim_rc" -ne 0 ]]; then
  log "NOTICE: apt-get -s purge exited ${apt_sim_rc} (simulation output still in ${SIM_OUT})"
fi

validate_simulate_remv_not_critical "$SIM_OUT" "$BLOCKED"

# Check keep packages mentioned as REMV in simulation
while read -r keep_pkg; do
  [[ -z "$keep_pkg" ]] && continue
  if grep -qE "REMV.*${keep_pkg}\b" "$SIM_OUT" 2>/dev/null; then
    echo "BLOCKED (simulate removes keep pkg): $keep_pkg" >> "$BLOCKED"
  fi
done <<< "$KEEP_SET"

if [[ -s "$BLOCKED" ]]; then
  log "BLOCKED packages detected — fix lists before apply:"
  cat "$BLOCKED"
  die "purge plan blocked"
fi

log "Run full gate: sudo ${DIR}/07_pre_purge_safety_gate.sh ${TIER}"

{
  echo "chroot_run 'DEBIAN_FRONTEND=noninteractive apt-get -y purge ${INSTALLED_PURGE[*]}'"
  echo "chroot_run 'DEBIAN_FRONTEND=noninteractive apt-get -y autoremove --purge'"
  echo "chroot_run 'DEBIAN_FRONTEND=noninteractive apt-get clean'"
} >> "$PLAN"

chmod +x "$PLAN"
log "Purge plan: ${PLAN}"
log "Sim log: ${SIM_OUT}"
exit "$RC_PASS"
