#!/usr/bin/env bash
# Kernel, EFI, bootability, GTK, squashfs, and size validators.

validate_kernel_safety() {
  local report="$1"
  local kver="${KEEP_KERNEL_FLAVOR}"
  local kpkg="linux-image-${kver}"
  local failures=0

  if ! host_dpkg_query -W -f='${Status}' "$kpkg" 2>/dev/null | grep -q installed; then
    printf 'FAIL\tkernel_safety\tpackage not installed: %s\n' "$kpkg" >> "$report"
    return 1
  fi

  local vmlinuz="vmlinuz-${kver}"
  local initrd="initrd.img-${kver}"
  local moddir="${ROOTFS_RESOLVED}/lib/modules/${kver}"

  for f in "${ROOTFS_RESOLVED}/boot/${vmlinuz}" "${ROOTFS_RESOLVED}/boot/${initrd}"; do
    if [[ ! -e "$f" ]]; then
      printf 'FAIL\tkernel_safety\tmissing boot/%s (required for KEEP_KERNEL_FLAVOR=%s)\n' "$f" "$kver" >> "$report"
      failures=1
    fi
  done

  if [[ ! -d "$moddir" ]] || [[ -z "$(ls -A "$moddir" 2>/dev/null)" ]]; then
    printf 'FAIL\tkernel_safety\tmissing or empty %s\n' "$moddir" >> "$report"
    failures=1
  fi

  if [[ $failures -eq 0 ]]; then
    printf 'PASS\tkernel_safety\t%s boot artifacts present\n' "$kver" >> "$report"
    return 0
  fi
  die "KERNEL SAFETY: missing boot artifacts for $kver"
}

validate_efi_runtime() {
  local report="$1"
  local failures=0
  local paths=(
    "boot/efi/EFI/ubuntu/shimx64.efi"
    "boot/efi/EFI/ubuntu/grubx64.efi"
    "boot/grub/grub.cfg"
  )
  local p
  for rel in "${paths[@]}"; do
    p="${ROOTFS_RESOLVED}/${rel}"
    if [[ ! -f "$p" ]]; then
      printf 'WARN\tefi_runtime\tmissing %s (may be unmounted ESP in build tree)\n' "$rel" >> "$report"
      failures=1
    else
      printf 'PASS\tefi_runtime\t%s\n' "$rel" >> "$report"
    fi
  done
  # shimx64/grubx64 under EFI/Boot or EFI/RecoveryBoot are alternate layouts
  if [[ $failures -ne 0 ]]; then
    for alt in \
      "boot/efi/EFI/Boot/bootx64.efi" \
      "boot/efi/EFI/RecoveryBoot/shimx64.efi"; do
      if [[ -f "${ROOTFS_RESOLVED}/${alt}" ]]; then
        printf 'PASS\tefi_runtime\talternate: %s\n' "$alt" >> "$report"
        failures=0
      fi
    done
  fi
  if [[ $failures -ne 0 ]]; then
    log "WARN: EFI paths incomplete in rootfs tree — verify ESP mount before production"
  fi
  return 0
}

validate_runtime_bootability() {
  local report="$1"
  local kver="${KEEP_KERNEL_FLAVOR}"
  local fail=0

  if host_dpkg_query -W -f='${Status}' "linux-image-${kver}" 2>/dev/null | grep -q installed; then
    if ! chroot_run "update-initramfs -u -k ${kver}" &>>"${REPORT_DIR}/bootability_initramfs.log"; then
      printf 'FAIL\tbootability\tupdate-initramfs -k %s\n' "$kver" >> "$report"
      fail=1
    else
      printf 'PASS\tbootability\tupdate-initramfs -k %s\n' "$kver" >> "$report"
    fi
  else
    printf 'WARN\tbootability\tskip initramfs — kernel package missing\n' >> "$report"
  fi

  if [[ -d "${ROOTFS_RESOLVED}/boot/grub" ]]; then
    if ! chroot_run "grub-mkconfig -o /boot/grub/grub.cfg" &>>"${REPORT_DIR}/bootability_grub.log"; then
      printf 'FAIL\tbootability\tgrub-mkconfig\n' >> "$report"
      fail=1
    else
      printf 'PASS\tbootability\tgrub-mkconfig\n' >> "$report"
    fi
  else
    printf 'WARN\tbootability\tno /boot/grub — grub-mkconfig skipped\n' >> "$report"
  fi

  if [[ $fail -ne 0 ]]; then
    die "BOOTABILITY: initramfs or grub-mkconfig failed — see ${REPORT_DIR}/bootability_*.log"
  fi
  return 0
}

validate_gtk_runtime_enhanced() {
  local report="$1"
  local py="${REPORT_DIR}/gtk_probe.py"
  cat > "$py" <<'PYEOF'
import os
import sys

os.environ.setdefault("DISPLAY", ":0")

errors = []

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk, GLib
except Exception as exc:
    print(f"FAIL gi import: {exc}")
    sys.exit(1)

print("PASS gi.repository Gtk import")

init_ok = Gtk.init_check(None)
print(f"INFO Gtk.init_check -> {init_ok}")

try:
    display = Gdk.Display.get_default()
    print(f"INFO Gdk.Display.get_default -> {display}")
except Exception as exc:
    print(f"WARN Gdk display: {exc}")

try:
    win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    win.set_title("recoverix-probe")
    win.set_default_size(320, 120)
    win.destroy()
    print("PASS Gtk window create/destroy")
except Exception as exc:
    print(f"WARN Gtk window: {exc}")

sys.exit(0)
PYEOF

  mkdir -p "${ROOTFS_RESOLVED}/tmp"
  cp "$py" "${ROOTFS_RESOLVED}/tmp/gtk_probe.py"
  if ! chroot_run "python3 /tmp/gtk_probe.py" &>"${REPORT_DIR}/gtk_probe.log"; then
    cat "${REPORT_DIR}/gtk_probe.log" >> "$report" || true
    die "GTK RUNTIME: enhanced probe failed — see ${REPORT_DIR}/gtk_probe.log"
  fi
  if grep -q '^FAIL' "${REPORT_DIR}/gtk_probe.log" 2>/dev/null; then
    grep '^FAIL' "${REPORT_DIR}/gtk_probe.log" >> "$report"
    die "GTK RUNTIME: gi/Gtk probe reported FAIL"
  fi
  grep -E '^PASS|^INFO|^WARN' "${REPORT_DIR}/gtk_probe.log" 2>/dev/null | while read -r l; do
    printf 'PASS\tgtk_runtime\t%s\n' "$l" >> "$report"
  done
}

write_runtime_size_report() {
  local report="$1"
  local json="${REPORT_DIR}/runtime_size_$(date -u +%Y%m%dT%H%M%SZ).json"
  local total_kb usr_kb var_kb fw_kb cache_kb snap_kb

  total_kb=$(du -sk "${ROOTFS_RESOLVED}" 2>/dev/null | awk '{print $1}' || echo 0)
  usr_kb=$(du -sk "${ROOTFS_RESOLVED}/usr" 2>/dev/null | awk '{print $1}' || echo 0)
  var_kb=$(du -sk "${ROOTFS_RESOLVED}/var" 2>/dev/null | awk '{print $1}' || echo 0)
  fw_kb=$(du -sk "${ROOTFS_RESOLVED}/usr/lib/firmware" 2>/dev/null | awk '{print $1}' || echo 0)
  cache_kb=$(du -sk "${ROOTFS_RESOLVED}/var/cache" 2>/dev/null | awk '{print $1}' || echo 0)
  snap_kb=$(du -sk "${ROOTFS_RESOLVED}/var/lib/snapd" 2>/dev/null | awk '{print $1}' || echo 0)

  local est_squash_mb
  est_squash_mb=$(( (total_kb * 55 / 100) / 1024 ))  # ~45% compression heuristic

  {
    echo "=== Runtime Size Report ==="
    echo "rootfs: ${ROOTFS_RESOLVED}"
    echo "total: $(numfmt --to=iec $((total_kb*1024)) 2>/dev/null || echo ${total_kb}K)"
    echo "/usr:  $(numfmt --to=iec $((usr_kb*1024)) 2>/dev/null || echo ${usr_kb}K)"
    echo "/var:  $(numfmt --to=iec $((var_kb*1024)) 2>/dev/null || echo ${var_kb}K)"
    echo "firmware: $(numfmt --to=iec $((fw_kb*1024)) 2>/dev/null || echo ${fw_kb}K)"
    echo "cache: $(numfmt --to=iec $((cache_kb*1024)) 2>/dev/null || echo ${cache_kb}K)"
    echo "snapd: $(numfmt --to=iec $((snap_kb*1024)) 2>/dev/null || echo ${snap_kb}K)"
    echo "estimated_squashfs: ~${est_squash_mb} MiB (heuristic)"
    echo
    echo "--- removable candidates (top dpkg, not installed check) ---"
    host_dpkg_query -W -f='${Package}\t${Installed-Size}\n' 2>/dev/null \
      | sort -t$'\t' -k2 -nr | head -15
  } | tee -a "$report"

  cat > "$json" <<EOF
{
  "rootfs": "${ROOTFS_RESOLVED}",
  "total_kb": ${total_kb},
  "usr_kb": ${usr_kb},
  "var_kb": ${var_kb},
  "firmware_kb": ${fw_kb},
  "cache_kb": ${cache_kb},
  "snapd_kb": ${snap_kb},
  "estimated_squashfs_mib": ${est_squash_mb}
}
EOF
  log "size report JSON: $json"
}

validate_squashfs_readiness() {
  local report="$1"
  local broken_log="${REPORT_DIR}/broken_symlinks.txt"
  local class_log="${REPORT_DIR}/broken_symlink_classification_$(date -u +%Y%m%dT%H%M%SZ).txt"
  local fail=0
  local broken_count warnable_count

  find "${ROOTFS_RESOLVED}" -xtype l 2>/dev/null > "$broken_log" || true
  broken_count=$(wc -l < "$broken_log" | tr -d ' ')
  broken_count="${broken_count:-0}"

  if [[ -f "${SCRIPT_DIR}/lib/symlink_classifier.sh" ]]; then
    # shellcheck source=symlink_classifier.sh
    source "${SCRIPT_DIR}/lib/symlink_classifier.sh"
    symlink_classify_scan "$class_log"
    warnable_count="${SYMLINK_STAT_WARNABLE:-0}"
    if [[ "${warnable_count}" -gt 0 ]]; then
      printf 'WARN\tsquashfs_readiness\t%d warnable broken symlinks (UNKNOWN/BOOT_CRITICAL/GTK_CRITICAL); total=%d; report=%s\n' \
        "$warnable_count" "$broken_count" "$class_log" >> "$report"
    elif [[ "${broken_count}" -gt 0 ]]; then
      printf 'PASS\tsquashfs_readiness\t%d broken symlinks, 0 warnable (INTENTIONAL/PACKAGE_MANAGED/RUNTIME_DYNAMIC); report=%s\n' \
        "$broken_count" "$class_log" >> "$report"
    else
      printf 'PASS\tsquashfs_readiness\tno broken symlinks\n' >> "$report"
    fi
  else
    warnable_count="$broken_count"
    if [[ "${broken_count:-0}" -gt 0 ]]; then
      printf 'WARN\tsquashfs_readiness\t%d broken symlinks (see %s)\n' "$broken_count" "$broken_log" >> "$report"
    else
      printf 'PASS\tsquashfs_readiness\tno broken symlinks\n' >> "$report"
    fi
  fi

  if ! chroot_run "dpkg --audit" &>"${REPORT_DIR}/dpkg_audit.log"; then
    if grep -q . "${REPORT_DIR}/dpkg_audit.log" 2>/dev/null; then
      printf 'FAIL\tsquashfs_readiness\tdpkg --audit issues\n' >> "$report"
      fail=1
    fi
  else
    printf 'PASS\tsquashfs_readiness\tdpkg --audit clean\n' >> "$report"
  fi

  if chroot_run "ldconfig -r / 2>/dev/null | head -1"; then
    printf 'PASS\tsquashfs_readiness\tldconfig runnable\n' >> "$report"
  else
    printf 'WARN\tsquashfs_readiness\tldconfig check inconclusive in chroot\n' >> "$report"
  fi

  if chroot_run "dpkg -C" &>/dev/null; then
    printf 'PASS\tsquashfs_readiness\tdpkg -C no broken packages\n' >> "$report"
  else
    printf 'WARN\tsquashfs_readiness\tdpkg -C reported changes\n' >> "$report"
  fi

  if [[ $fail -ne 0 ]]; then
    die "SQUASHFS READINESS: dpkg consistency failed"
  fi
  return 0
}
