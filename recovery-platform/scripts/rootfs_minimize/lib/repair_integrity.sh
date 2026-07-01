#!/usr/bin/env bash
# Rootfs integrity repair helpers (broken symlinks, ldconfig, dpkg, post-repair checks).
# All paths under ROOTFS_RESOLVED only — never modifies host /.

# symlink_classifier.sh is sourced by 09/07 before this file (requires SCRIPT_DIR).

REPAIR_STAT_SCANNED=0
REPAIR_STAT_REMOVED=0
REPAIR_STAT_SKIPPED=0
REPAIR_STAT_PROTECTED=0
REPAIR_STAT_MANUAL=0
REPAIR_STAT_WARNINGS=0
REPAIR_STAT_ERRORS=0

REPAIR_RC_SUCCESS=0
REPAIR_RC_WARN=1
REPAIR_RC_FATAL=2
REPAIR_RC_HOST_SAFETY=3
REPAIR_RC_BOOT_CRITICAL=4

find_broken_symlinks() {
  find "${ROOTFS_RESOLVED}" -xtype l 2>/dev/null || true
}

count_broken_symlinks() {
  local n
  n="$(find_broken_symlinks | wc -l | tr -d ' ')"
  printf '%s' "${n:-0}"
}

count_broken_symlinks_warnable() {
  if declare -f symlink_classify_scan &>/dev/null; then
    local tmp_report
    tmp_report="$(mktemp)"
    symlink_classify_scan "$tmp_report"
    rm -f "$tmp_report"
    printf '%s' "${SYMLINK_STAT_WARNABLE:-0}"
    return 0
  fi
  count_broken_symlinks
}

repair_mount_chroot() {
  local rc=0
  if chroot_is_mounted; then
    log "chroot mounts already present under ${ROOTFS_RESOLVED}"
    return 0
  fi
  log "mounting virtual filesystems under ${ROOTFS_RESOLVED} (rootfs only)"
  mkdir -p \
    "${ROOTFS_RESOLVED}/dev" \
    "${ROOTFS_RESOLVED}/dev/pts" \
    "${ROOTFS_RESOLVED}/proc" \
    "${ROOTFS_RESOLVED}/sys" \
    "${ROOTFS_RESOLVED}/run" || return 1

  mount -o bind /dev "${ROOTFS_RESOLVED}/dev" || rc=$?
  if [[ $rc -ne 0 ]]; then
    log "WARN: bind mount /dev failed (rc=${rc})"
    return "$rc"
  fi
  mount -o bind /dev/pts "${ROOTFS_RESOLVED}/dev/pts" || rc=$?
  mount -t proc proc "${ROOTFS_RESOLVED}/proc" || rc=$?
  mount -t sysfs sysfs "${ROOTFS_RESOLVED}/sys" || rc=$?
  if [[ -d /run ]]; then
    mount -o bind /run "${ROOTFS_RESOLVED}/run" 2>/dev/null || log "WARN: bind /run skipped"
  fi
  if [[ -d /sys/firmware/efi/efivars ]]; then
    mkdir -p "${ROOTFS_RESOLVED}/sys/firmware/efi/efivars"
    mount -o bind,ro /sys/firmware/efi/efivars "${ROOTFS_RESOLVED}/sys/firmware/efi/efivars" 2>/dev/null || true
  fi
  return 0
}

repair_validate_boot_critical_artifacts() {
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  local failures=0
  local f

  for f in \
    "${ROOTFS_RESOLVED}/boot/vmlinuz-${kver}" \
    "${ROOTFS_RESOLVED}/boot/initrd.img-${kver}" \
    "${ROOTFS_RESOLVED}/lib/modules/${kver}"; do
    if [[ ! -e "$f" ]]; then
      log "BOOT CRITICAL: missing ${f}"
      failures=1
    fi
  done

  if [[ $failures -ne 0 ]]; then
    return "$REPAIR_RC_BOOT_CRITICAL"
  fi
  return 0
}

# Write full classification report (no removals).
repair_classify_broken_symlinks() {
  local classification_report="$1"
  if ! declare -f symlink_classify_scan &>/dev/null; then
    log "WARN: symlink_classifier.sh not available"
    return 1
  fi
  symlink_classify_scan "$classification_report"
  return 0
}

# Remove only REMOVABLE category; never UNKNOWN or protected categories.
repair_broken_symlinks() {
  local report="$1"
  local debug_report="$2"
  local dry_run="${3:-0}"
  local removed=0 kept=0 skipped=0 scanned=0

  if ! declare -f symlink_classify_link &>/dev/null; then
    log "ERROR: symlink classifier required for repair"
    return 1
  fi

  symlink_classify_reset_stats

  {
    echo "=== Broken symlink repair (classifier-driven) ==="
    echo "rootfs: ${ROOTFS_RESOLVED}"
    echo "dry_run: ${dry_run}"
    echo "policy: auto-remove REMOVABLE only; UNKNOWN never removed"
    echo
  } >> "$report"

  {
    echo "=== Broken symlink debug trace ==="
    echo "timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'link\ttarget\tcategory\taction\treason\tpackage\tprotected_by\tmatched_rule\n'
  } >> "$debug_report"

  local link target rel trace category action reason pkg protby rule
  while IFS= read -r link; do
    [[ -z "$link" ]] && continue
    scanned=$((scanned + 1))
    rel="$(_symlink_rel_from_abs "$link")"
    target="$(readlink "$link" 2>/dev/null || true)"
    trace="$(symlink_classify_link "$link")"
    IFS='|' read -r category action reason pkg protby rule <<< "$trace"
    _symlink_bump_stat "$category"

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$link" "$target" "$category" "$action" "$reason" "${pkg:--}" "$protby" "$rule" >> "$debug_report"

    case "$category" in
      REMOVABLE)
        if [[ "$action" != "remove" ]]; then
          action="keep"
        fi
        if [[ "${dry_run}" == "1" ]]; then
          echo "DRY_REMOVE\t${link}\t${category}\t${rule}" >> "$report"
          echo "[DRYRUN] would remove: ${link} (${rule})" >&2
          removed=$((removed + 1))
        else
          rm -f "$link" 2>/dev/null || {
            echo "ERROR\t${link}\trm_failed" >> "$report"
            REPAIR_STAT_ERRORS=$((REPAIR_STAT_ERRORS + 1))
            continue
          }
          echo "REMOVED\t${link}\t${category}\t${rule}" >> "$report"
          removed=$((removed + 1))
        fi
        ;;
      UNKNOWN)
        echo "SKIP\t${link}\tUNKNOWN\tnever_auto_remove" >> "$report"
        skipped=$((skipped + 1))
        if [[ "${dry_run}" == "1" ]]; then
          echo "[DRYRUN] would skip (UNKNOWN): ${link}" >&2
        fi
        ;;
      *)
        echo "KEEP\t${link}\t${category}\t${reason}" >> "$report"
        kept=$((kept + 1))
        if [[ "${dry_run}" == "1" ]]; then
          echo "[DRYRUN] would skip (${category}): ${link}" >&2
        fi
        ;;
    esac
  done < <(find_broken_symlinks)

  REPAIR_STAT_SCANNED=$scanned
  REPAIR_STAT_REMOVED=$removed
  REPAIR_STAT_PROTECTED=$kept
  REPAIR_STAT_MANUAL=$skipped
  REPAIR_STAT_SKIPPED=$skipped

  {
    echo
    echo "summary: scanned=${scanned} removed=${removed} kept=${kept} unknown_skipped=${skipped}"
    echo "warnable_remaining: ${SYMLINK_STAT_WARNABLE}"
  } >> "$report"

  return 0
}

repair_run_ldconfig() {
  local report="$1"
  chroot_run "ldconfig" &>>"${REPORT_DIR}/repair_ldconfig.log" 2>/dev/null || {
    local rc=$?
    printf 'WARN\trepair_ldconfig\tldconfig failed (rc=%s) see repair_ldconfig.log\n' "$rc" >> "$report"
    REPAIR_STAT_WARNINGS=$((REPAIR_STAT_WARNINGS + 1))
    return 1
  }
  printf 'PASS\trepair_ldconfig\tldconfig OK\n' >> "$report"
  return 0
}

repair_run_dpkg_audit() {
  local report="$1"
  local audit_log="${REPORT_DIR}/repair_dpkg_audit.log"
  local changes_log="${REPORT_DIR}/repair_dpkg_C.log"
  : > "$audit_log" 2>/dev/null || true
  : > "$changes_log" 2>/dev/null || true

  local audit_rc=0 c_rc=0
  chroot_run "dpkg --audit" >>"$audit_log" 2>&1 || audit_rc=$?
  chroot_run "dpkg -C" >>"$changes_log" 2>&1 || c_rc=$?

  {
    echo "=== dpkg --audit (rc=${audit_rc}) ==="
    cat "$audit_log" 2>/dev/null || true
    echo
    echo "=== dpkg -C (rc=${c_rc}) ==="
    cat "$changes_log" 2>/dev/null || true
  } >> "$report"

  if [[ -s "$audit_log" ]] && grep -q '[^[:space:]]' "$audit_log" 2>/dev/null; then
    printf 'WARN\trepair_dpkg\tdpkg --audit reported issues\n' >> "$report"
    REPAIR_STAT_WARNINGS=$((REPAIR_STAT_WARNINGS + 1))
    return 1
  fi
  printf 'PASS\trepair_dpkg\tdpkg --audit clean\n' >> "$report"

  if [[ $c_rc -ne 0 ]] || { [[ -s "$changes_log" ]] && grep -q '[^[:space:]]' "$changes_log" 2>/dev/null; }; then
    printf 'WARN\trepair_dpkg\tdpkg -C reported changes\n' >> "$report"
    REPAIR_STAT_WARNINGS=$((REPAIR_STAT_WARNINGS + 1))
    return 1
  fi
  printf 'PASS\trepair_dpkg\tdpkg -C clean\n' >> "$report"
  return 0
}

repair_initramfs_sanity() {
  local report="$1"
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  chroot_run "update-initramfs -u -k ${kver}" &>>"${REPORT_DIR}/repair_initramfs.log" 2>/dev/null || {
    local rc=$?
    printf 'WARN\trepair_initramfs\tupdate-initramfs failed (rc=%s)\n' "$rc" >> "$report"
    REPAIR_STAT_WARNINGS=$((REPAIR_STAT_WARNINGS + 1))
    return 1
  }
  printf 'PASS\trepair_initramfs\tupdate-initramfs -k %s\n' "$kver" >> "$report"
  return 0
}

repair_post_validation() {
  local report="$1"
  local broken warnable

  broken="$(count_broken_symlinks)"
  warnable="$(count_broken_symlinks_warnable)"

  if [[ "${warnable:-0}" -eq 0 ]]; then
    printf 'PASS\tpost_repair_symlinks\t%d broken symlinks, 0 warnable (intentional/package/runtime OK)\n' "$broken" >> "$report"
  else
    printf 'WARN\tpost_repair_symlinks\t%d warnable broken symlinks (%d total)\n' "$warnable" "$broken" >> "$report"
    REPAIR_STAT_WARNINGS=$((REPAIR_STAT_WARNINGS + 1))
  fi

  repair_run_ldconfig "$report" || true

  if chroot_run "partclone.ntfs -V" &>>"${REPORT_DIR}/repair_partclone.log" 2>/dev/null \
     || chroot_run "partclone --version" &>>"${REPORT_DIR}/repair_partclone.log" 2>/dev/null; then
    printf 'PASS\tpost_repair_partclone\tpartclone runnable\n' >> "$report"
  else
    printf 'WARN\tpost_repair_partclone\tpartclone check failed\n' >> "$report"
    REPAIR_STAT_WARNINGS=$((REPAIR_STAT_WARNINGS + 1))
  fi

  if chroot_run "python3 -c \"import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; Gtk.init_check(None)\"" \
    &>>"${REPORT_DIR}/repair_gtk.log" 2>/dev/null; then
    printf 'PASS\tpost_repair_gtk\tpython3 gi/Gtk import OK\n' >> "$report"
  else
    printf 'WARN\tpost_repair_gtk\tGTK import probe failed\n' >> "$report"
    REPAIR_STAT_WARNINGS=$((REPAIR_STAT_WARNINGS + 1))
  fi

  printf '%s' "${warnable:-0}"
}
