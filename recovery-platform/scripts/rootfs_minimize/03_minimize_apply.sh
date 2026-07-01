#!/usr/bin/env bash
# Apply minimization: file prune + optional apt purge (chroot only).
# Usage:
#   sudo APT_DRY_RUN=1 ./03_minimize_apply.sh tier1   # simulate apt only
#   sudo APT_DRY_RUN=0 ./03_minimize_apply.sh tier1   # destructive — review plan first

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/snapd_cleanup.sh
source "${DIR}/lib/snapd_cleanup.sh"
require_root

TIER="${1:-tier1}"
SNAPSHOT_TAG="$(date -u +%Y%m%dT%H%M%SZ)"
SNAPD_REPORT="${REPORT_DIR}/snapd_cleanup_${SNAPSHOT_TAG}.txt"

if [[ "${APT_DRY_RUN}" == "0" ]]; then
  if [[ "${SKIP_SAFETY_GATE:-0}" == "1" ]]; then
    log "========================================================================"
    log "WARNING: SKIP_SAFETY_GATE=1 — preflight and safety gate SKIPPED"
    log "WARNING: Tier1 purge may remove boot/runtime packages — NOT for production"
    log "========================================================================"
  else
    log "Running preflight runner (mandatory before Tier1 apply)..."
    set +e
    "${DIR}/08_preflight_run.sh" "$TIER"
    PF_RC=$?
    set -e
    if [[ "$PF_RC" -eq 2 ]]; then
      die "08_preflight_run FAILED (exit 2) — Tier1 purge FORBIDDEN. See preflight_summary_*.txt in ${REPORT_DIR}"
    fi
    if [[ "$PF_RC" -eq 1 ]] && [[ "${FORCE_WARN:-0}" != "1" ]]; then
      die "08_preflight_run WARN-only (exit 1) — purge blocked. Fix WARNs or set FORCE_WARN=1 (not recommended)"
    fi
    if [[ "$PF_RC" -ne 0 ]] && [[ "${FORCE_WARN:-0}" == "1" ]]; then
      log "WARNING: proceeding with FORCE_WARN=1 despite preflight exit ${PF_RC}"
    fi
    log "Preflight passed (exit ${PF_RC}) — proceeding to apply"
  fi
fi

log "=== Phase 0: recommend snapshot ==="
log "Create rollback tarball BEFORE apply:"
log "  sudo ${DIR}/06_snapshot_rootfs.sh pre-minimize-${SNAPSHOT_TAG}"

"${DIR}/04_prune_filesystem.sh"

mount_chroot_fs

if [[ -f "${ROOTFS_RESOLVED}/swapfile" ]]; then
  log "Removing embedded 2G swapfile from rootfs image"
  rm -f "${ROOTFS_RESOLVED}/swapfile"
fi

read_list() {
  local file="$1"
  while read -r pkg; do
    [[ -z "$pkg" || "$pkg" =~ ^# ]] && continue
    host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q installed && echo "$pkg"
  done < "$file"
}

PURGE_PKGS=()
case "$TIER" in
  tier1) mapfile -t PURGE_PKGS < <(read_list "${DIR}/packages_purge_tier1.txt") ;;
  tier2) mapfile -t PURGE_PKGS < <(read_list "${DIR}/packages_purge_tier1.txt"; read_list "${DIR}/packages_purge_tier2.txt") ;;
  all)   mapfile -t PURGE_PKGS < <(read_list "${DIR}/packages_purge_tier1.txt"; read_list "${DIR}/packages_purge_tier2.txt") ;;
  *) die "usage: $0 [tier1|tier2|all]" ;;
esac

PURGE_WITHOUT_SNAPD=()
PURGE_HAS_SNAPD=0
for pkg in "${PURGE_PKGS[@]}"; do
  if [[ "$pkg" == "snapd" ]]; then
    PURGE_HAS_SNAPD=1
  else
    PURGE_WITHOUT_SNAPD+=("$pkg")
  fi
done

if [[ ${#PURGE_PKGS[@]} -eq 0 ]]; then
  log "No tier packages installed to purge"
else
  if [[ "${APT_DRY_RUN}" == "1" ]]; then
    log "APT_DRY_RUN=1 — simulate purge only"
    chroot_run "DEBIAN_FRONTEND=noninteractive apt-get -y -s purge ${PURGE_PKGS[*]}" || true
  else
    log "APT_DRY_RUN=0 — PURGING packages inside chroot (snapd isolated)"

    if [[ ${#PURGE_WITHOUT_SNAPD[@]} -gt 0 ]]; then
      set +e
      chroot_run "DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::='--force-confold' purge ${PURGE_WITHOUT_SNAPD[*]}"
      other_rc=$?
      set -e
      if [[ $other_rc -ne 0 ]]; then
        log "WARN: non-snapd purge exited ${other_rc} (continuing)"
      fi
    fi

    if [[ "$PURGE_HAS_SNAPD" -eq 1 ]]; then
      log "=== Snapd chroot-safe purge (see ${SNAPD_REPORT}) ==="
      : > "$SNAPD_REPORT"
      set +e
      snapd_purge_with_recovery "$SNAPD_REPORT"
      SNAPD_RC=$?
      set -e
      if [[ "$SNAPD_RC" -eq "$SNAPD_CLEANUP_RC_WARN" ]]; then
        log "========================================================================"
        log "RECOVERABLE WARN: snapd purge used force cleanup — Tier1 apply continues"
        log "Report: ${SNAPD_REPORT}"
        log "========================================================================"
      elif [[ "$SNAPD_RC" -ne 0 ]]; then
        log "WARN: snapd cleanup returned rc=${SNAPD_RC} — see ${SNAPD_REPORT}"
      else
        log "Snapd cleanup: success (${SNAPD_REPORT})"
      fi
    fi

    set +e
    chroot_run "DEBIAN_FRONTEND=noninteractive apt-get -y autoremove --purge"
    chroot_run "DEBIAN_FRONTEND=noninteractive apt-get clean"
    set -e
  fi
fi

"${DIR}/04_prune_filesystem.sh"
"${DIR}/05_verify_runtime.sh"
log "Done. Re-check size: du -sh ${ROOTFS_RESOLVED}"
if [[ -f "$SNAPD_REPORT" ]]; then
  log "Snapd cleanup report: ${SNAPD_REPORT}"
fi
