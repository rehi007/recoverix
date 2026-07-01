#!/usr/bin/env bash
# Analyze rootfs size and installed packages; write report (no modifications).
# Usage: sudo ./01_analyze_rootfs.sh

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"

require_root
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/analysis_${TS}.txt"
REPORT_JSON="${REPORT_DIR}/analysis_${TS}.json"

log "Analyzing ${ROOTFS_RESOLVED} -> ${REPORT}"

{
  echo "=== Recoverix Rootfs Analysis ==="
  echo "timestamp: ${TS}"
  echo "rootfs: ${ROOTFS_RESOLVED}"
  echo
  echo "--- os-release ---"
  cat "${ROOTFS_RESOLVED}/etc/os-release" 2>/dev/null || true
  echo
  echo "--- du top-level ---"
  du -sh "${ROOTFS_RESOLVED}"/* 2>/dev/null | sort -hr || true
  echo
  echo "--- du usr/share (top 25) ---"
  du -sh "${ROOTFS_RESOLVED}/usr/share"/* 2>/dev/null | sort -hr | head -25 || true
  echo
  echo "--- large var paths ---"
  du -sh "${ROOTFS_RESOLVED}/var/lib/snapd" 2>/dev/null || true
  du -sh "${ROOTFS_RESOLVED}/var/lib/apt" 2>/dev/null || true
  du -sh "${ROOTFS_RESOLVED}/var/log/journal" 2>/dev/null || true
  du -sh "${ROOTFS_RESOLVED}/swapfile" 2>/dev/null || true
  echo
  echo "--- dpkg top 50 by installed size (KiB) ---"
  host_dpkg_query -W -f='${Package}\t${Installed-Size}\t${Status}\n' 2>/dev/null \
    | awk '$3 ~ /installed/' | sort -t$'\t' -k2 -nr | head -50
  echo
  echo "--- keep-list packages present ---"
  while read -r pkg; do
    [[ -z "$pkg" || "$pkg" =~ ^# ]] && continue
    if host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q installed; then
      echo "KEEP OK: $pkg"
    else
      echo "KEEP MISSING: $pkg"
    fi
  done < "${DIR}/packages_keep.txt"
  echo
  echo "--- tier1 purge candidates installed ---"
  while read -r pkg; do
    [[ -z "$pkg" || "$pkg" =~ ^# ]] && continue
    if host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q installed; then
      echo "PURGE CANDIDATE: $pkg"
    fi
  done < "${DIR}/packages_purge_tier1.txt"
} | tee "$REPORT"

# Minimal JSON summary
TOTAL_KB=$(du -sk "${ROOTFS_RESOLVED}" 2>/dev/null | awk '{print $1}')
SNAP_KB=$(du -sk "${ROOTFS_RESOLVED}/var/lib/snapd" 2>/dev/null | awk '{print $2}' || echo 0)
printf '{"rootfs":"%s","total_kb":%s,"snapd_kb":%s,"report":"%s"}\n' \
  "$ROOTFS_RESOLVED" "${TOTAL_KB:-0}" "${SNAP_KB:-0}" "$REPORT" > "$REPORT_JSON"

log "Wrote ${REPORT}"
