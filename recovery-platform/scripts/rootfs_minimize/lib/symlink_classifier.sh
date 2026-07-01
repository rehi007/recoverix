#!/usr/bin/env bash
# Broken symlink classifier for /recovery/build/rootfs only.
# Categories: INTENTIONAL, REMOVABLE, PACKAGE_MANAGED, RUNTIME_DYNAMIC,
#             BOOT_CRITICAL, GTK_CRITICAL, UNKNOWN

SYMLINK_CAT_INTENTIONAL="INTENTIONAL"
SYMLINK_CAT_REMOVABLE="REMOVABLE"
SYMLINK_CAT_PACKAGE_MANAGED="PACKAGE_MANAGED"
SYMLINK_CAT_RUNTIME_DYNAMIC="RUNTIME_DYNAMIC"
SYMLINK_CAT_BOOT_CRITICAL="BOOT_CRITICAL"
SYMLINK_CAT_GTK_CRITICAL="GTK_CRITICAL"
SYMLINK_CAT_UNKNOWN="UNKNOWN"

# Stats (global, updated by symlink_classify_scan)
SYMLINK_STAT_INTENTIONAL=0
SYMLINK_STAT_REMOVABLE=0
SYMLINK_STAT_PACKAGE_MANAGED=0
SYMLINK_STAT_RUNTIME_DYNAMIC=0
SYMLINK_STAT_BOOT_CRITICAL=0
SYMLINK_STAT_GTK_CRITICAL=0
SYMLINK_STAT_UNKNOWN=0
SYMLINK_STAT_TOTAL=0
SYMLINK_STAT_WARNABLE=0

_SYMLINK_WHITELIST_FILE="${SCRIPT_DIR}/symlink_intentional_whitelist.txt"
_SYMLINK_DPKG_CACHE=""

_symlink_rel_from_abs() {
  local link="$1"
  local rel="${link#${ROOTFS_RESOLVED}/}"
  printf '%s' "${rel#/}"
}

_symlink_load_whitelist() {
  local f="${_SYMLINK_WHITELIST_FILE}"
  [[ -f "$f" ]] || return 1
  while read -r line; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    echo "$line"
  done < "$f"
}

_symlink_match_glob_list() {
  local rel="$1"
  local pat
  while read -r pat; do
    [[ -z "$pat" ]] && continue
    case "$rel" in
      $pat) return 0 ;;
    esac
  done
  return 1
}

_symlink_is_intentional() {
  local rel="$1"
  _symlink_match_glob_list "$rel" < <(_symlink_load_whitelist 2>/dev/null || true)
}

_symlink_is_boot_critical() {
  local rel="$1"
  local link="$2"
  local target="${3:-}"
  local base
  base="$(basename "$rel")"
  case "$rel" in
    boot/*|boot) return 0 ;;
    lib/modules/*|lib/modules) return 0 ;;
    usr/lib/modules/*|usr/lib/modules) return 0 ;;
    boot/efi/*|boot/efi|EFI/*|EFI) return 0 ;;
    usr/lib/grub/*|usr/lib/grub|usr/share/grub/*) return 0 ;;
    usr/lib/shim/*|usr/lib/shim*) return 0 ;;
    usr/bin/grub*|usr/bin/python3|usr/bin/python3.*|usr/bin/partclone*) return 0 ;;
    usr/lib/python3/*|usr/lib/python3) return 0 ;;
    usr/lib/x86_64-linux-gnu/libgtk*|usr/lib/x86_64-linux-gnu/libgdk*| \
    usr/lib/x86_64-linux-gnu/libglib*|usr/lib/x86_64-linux-gnu/girepository*) return 0 ;;
  esac
  case "$base" in
    grub*|shim*|vmlinuz*|initrd*|partclone*|python3*) return 0 ;;
  esac
  case "$target" in
    /boot/*|/lib/modules/*|/usr/lib/modules/*|/boot/efi/*|/EFI/*|/usr/lib/grub/*|/usr/share/grub/*|/usr/lib/shim/*) return 0 ;;
    /usr/bin/grub*|/usr/bin/python3|/usr/bin/partclone*) return 0 ;;
    ../boot/*|../../boot/*|../lib/modules/*|../../lib/modules/*) return 0 ;;
  esac
  return 1
}

_symlink_is_gtk_critical() {
  local rel="$1"
  local target="${2:-}"
  case "$rel" in
    usr/lib/python3/dist-packages/gi/*|usr/lib/girepository-*/*|usr/lib/x86_64-linux-gnu/girepository-*/*) return 0 ;;
    usr/lib/x86_64-linux-gnu/libgtk-3.so*|usr/lib/x86_64-linux-gnu/libgdk-3.so*) return 0 ;;
    etc/xdg/menus/*gtk*|usr/share/gtk-3.0/*) return 0 ;;
  esac
  case "$target" in
    /usr/lib/x86_64-linux-gnu/libgtk*|/usr/lib/x86_64-linux-gnu/libgdk*|/usr/lib/x86_64-linux-gnu/girepository*| \
    /usr/lib/python3/dist-packages/gi/*) return 0 ;;
  esac
  case "$rel" in
    *python3-gi*|*libgtk*|*libgdk*|*girepository*|*pygtk*|*gi/repository*)
      return 0
      ;;
  esac
  return 1
}

_symlink_is_runtime_dynamic() {
  local rel="$1"
  local target="${2:-}"
  case "$rel" in
    run/*|run) return 0 ;;
    var/run/*) return 0 ;;
    proc/*|proc|sys/*|sys|dev/*|dev|tmp/*|tmp) return 0 ;;
    var/lib/systemd/*|var/lib/private/*|var/lib/dbus/*) return 0 ;;
    overlay/*|overlay|squashfs/*|upper/*|work/*) return 0 ;;
  esac
  case "$target" in
    /run/*|/var/run/*|/proc/*|/sys/*|/dev/*|none|tmpfs|/tmp/*) return 0 ;;
  esac
  return 1
}

_symlink_is_removable_heuristic() {
  local rel="$1"
  local base
  base="$(basename "$rel")"
  case "$rel" in
    var/cache/*|var/lib/snapd/*|var/lib/snapd) return 0 ;;
    snap/*|snap) return 0 ;;
    usr/share/doc/*|usr/share/man/*|usr/share/help/*|usr/share/info/*) return 0 ;;
    usr/share/applications/*|usr/share/pixmaps/*) return 0 ;;
    opt/google/*|opt/chrome/*|opt/Cursor/*) return 0 ;;
    usr/lib/firefox/*|usr/lib/chromium/*|usr/lib/chromium-browser/*|usr/lib/cursor/*|usr/lib/Cursor/*) return 0 ;;
    usr/lib/libreoffice/*|usr/lib/thunderbird/*|usr/lib/mozilla/*) return 0 ;;
    usr/lib/cups/*|usr/lib/ghostscript/*) return 0 ;;
    home/*/.cache/*|root/.cache/*) return 0 ;;
    usr/local/bin/google-chrome*|usr/local/bin/cursor*) return 0 ;;
  esac
  case "$base" in
    *chrome*|*chromium*|*firefox*|*cursor*|*thunderbird*|*libreoffice*|*snap*|*desktop*|*gnome-shell*|*code*) return 0 ;;
  esac
  case "$rel" in
    *chrome*|*chromium*|*firefox*|*cursor*|*Cursor*|*thunderbird*|*libreoffice*|*snapd*|*cups*)
      return 0
      ;;
  esac
  return 1
}

# dpkg owner for path inside rootfs (no host modification)
_symlink_dpkg_owner() {
  local rel="$1"
  local cache_key="./${rel}"
  local line owner

  if [[ -n "${_SYMLINK_DPKG_CACHE}" && -f "${_SYMLINK_DPKG_CACHE}" ]]; then
    line="$(grep -F "${cache_key}"$'\t' "${_SYMLINK_DPKG_CACHE}" 2>/dev/null | head -1 || true)"
    if [[ -n "$line" ]]; then
      printf '%s' "${line#*$'\t'}"
      return 0
    fi
  fi

  owner="$(host_dpkg_query -S "${rel}" 2>/dev/null | head -1 || true)"
  if [[ -z "$owner" ]]; then
    owner="$(host_dpkg_query -S "/${rel}" 2>/dev/null | head -1 || true)"
  fi
  if [[ -n "$owner" ]]; then
    owner="${owner%%:*}"
    if [[ -n "${_SYMLINK_DPKG_CACHE}" ]]; then
      printf '%s\t%s\n' "${cache_key}" "${owner}" >> "${_SYMLINK_DPKG_CACHE}" 2>/dev/null || true
    fi
    printf '%s' "$owner"
    return 0
  fi
  return 1
}

_symlink_package_installed() {
  local pkg="$1"
  [[ -z "$pkg" ]] && return 1
  host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"
}

_symlink_package_is_keep_critical() {
  local pkg="$1"
  local keep_file="${SCRIPT_DIR}/packages_keep.txt"
  local bc_file="${SCRIPT_DIR}/packages_boot_critical.txt"
  [[ -z "$pkg" ]] && return 1
  if [[ -f "$keep_file" ]] && grep -qx "$pkg" "$keep_file" 2>/dev/null; then
    return 0
  fi
  if [[ -f "$bc_file" ]]; then
    local pat
    while read -r pat; do
      [[ -z "$pat" || "$pat" =~ ^# ]] && continue
      case "$pkg" in
        $pat) return 0 ;;
      esac
    done < "$bc_file"
  fi
  case "$pkg" in
    python3*|libgtk*|libgdk*|libglib*|python3-gi*|gir1.2-*|partclone*|grub*|shim*|linux-image*|linux-modules*|initramfs*)
      return 0
      ;;
  esac
  return 1
}

# Output: category|action|reason|package|protected_by|matched_rule
symlink_classify_link() {
  local link="$1"
  local rel target pkg category action reason protected_by rule

  rel="$(_symlink_rel_from_abs "$link")"
  target="$(readlink "$link" 2>/dev/null || true)"
  pkg=""
  protected_by="-"
  rule="-"
  action="keep"

  if _symlink_is_boot_critical "$rel" "$link" "$target"; then
    category="$SYMLINK_CAT_BOOT_CRITICAL"
    reason="boot_runtime_path"
    rule="boot|modules|efi|grub|shim|python3|partclone"
    protected_by="boot_critical_policy"
    action="keep"
    printf '%s|%s|%s|%s|%s|%s' "$category" "$action" "$reason" "$pkg" "$protected_by" "$rule"
    return 0
  fi

  if _symlink_is_gtk_critical "$rel" "$target"; then
    category="$SYMLINK_CAT_GTK_CRITICAL"
    reason="gtk_python_gi_runtime"
    rule="gtk|gi|girepository"
    protected_by="gtk_critical_policy"
    action="keep"
    printf '%s|%s|%s|%s|%s|%s' "$category" "$action" "$reason" "$pkg" "$protected_by" "$rule"
    return 0
  fi

  if _symlink_is_intentional "$rel"; then
    category="$SYMLINK_CAT_INTENTIONAL"
    reason="intentional_whitelist"
    rule="symlink_intentional_whitelist.txt"
    protected_by="whitelist"
    action="keep"
    printf '%s|%s|%s|%s|%s|%s' "$category" "$action" "$reason" "$pkg" "$protected_by" "$rule"
    return 0
  fi

  if _symlink_is_runtime_dynamic "$rel" "$target"; then
    category="$SYMLINK_CAT_RUNTIME_DYNAMIC"
    reason="overlay_runtime_mount"
    rule="run|proc|sys|dev|overlay|systemd_state"
    protected_by="runtime_dynamic"
    action="keep"
    printf '%s|%s|%s|%s|%s|%s' "$category" "$action" "$reason" "$pkg" "$protected_by" "$rule"
    return 0
  fi

  if pkg="$(_symlink_dpkg_owner "$rel")"; [[ -n "$pkg" ]]; then
    if _symlink_package_installed "$pkg"; then
      category="$SYMLINK_CAT_PACKAGE_MANAGED"
      reason="dpkg_owned_installed"
      rule="dpkg-query -S"
      protected_by="package:${pkg}"
      action="keep"
      printf '%s|%s|%s|%s|%s|%s' "$category" "$action" "$reason" "$pkg" "$protected_by" "$rule"
      return 0
    fi
  fi

  if _symlink_is_removable_heuristic "$rel"; then
    if [[ -n "$pkg" ]] && _symlink_package_installed "$pkg" && _symlink_package_is_keep_critical "$pkg"; then
      category="$SYMLINK_CAT_PACKAGE_MANAGED"
      reason="removable_blocked_active_critical_pkg"
      rule="keep_packages:${pkg}"
      protected_by="package:${pkg}"
      action="keep"
      printf '%s|%s|%s|%s|%s|%s' "$category" "$action" "$reason" "$pkg" "$protected_by" "$rule"
      return 0
    fi
    category="$SYMLINK_CAT_REMOVABLE"
    reason="desktop_cache_remnant"
    rule="removable_heuristic"
    protected_by="-"
    action="remove"
    printf '%s|%s|%s|%s|%s|%s' "$category" "$action" "$reason" "${pkg:-}" "$protected_by" "$rule"
    return 0
  fi

  category="$SYMLINK_CAT_UNKNOWN"
  reason="unclassified_dangling"
  rule="-"
  protected_by="-"
  action="keep"
  printf '%s|%s|%s|%s|%s|%s' "$category" "$action" "$reason" "${pkg:-}" "$protected_by" "$rule"
}

symlink_classifier_action_for_category() {
  local category="$1"
  case "$category" in
    "$SYMLINK_CAT_REMOVABLE") printf 'remove' ;;
    *) printf 'keep' ;;
  esac
}

symlink_category_counts_as_warn() {
  local category="$1"
  case "$category" in
    "$SYMLINK_CAT_UNKNOWN"|"$SYMLINK_CAT_BOOT_CRITICAL"|"$SYMLINK_CAT_GTK_CRITICAL") return 0 ;;
    *) return 1 ;;
  esac
}

symlink_classify_reset_stats() {
  SYMLINK_STAT_INTENTIONAL=0
  SYMLINK_STAT_REMOVABLE=0
  SYMLINK_STAT_PACKAGE_MANAGED=0
  SYMLINK_STAT_RUNTIME_DYNAMIC=0
  SYMLINK_STAT_BOOT_CRITICAL=0
  SYMLINK_STAT_GTK_CRITICAL=0
  SYMLINK_STAT_UNKNOWN=0
  SYMLINK_STAT_TOTAL=0
  SYMLINK_STAT_WARNABLE=0
}

_symlink_bump_stat() {
  local category="$1"
  SYMLINK_STAT_TOTAL=$((SYMLINK_STAT_TOTAL + 1))
  case "$category" in
    "$SYMLINK_CAT_INTENTIONAL") SYMLINK_STAT_INTENTIONAL=$((SYMLINK_STAT_INTENTIONAL + 1)) ;;
    "$SYMLINK_CAT_REMOVABLE") SYMLINK_STAT_REMOVABLE=$((SYMLINK_STAT_REMOVABLE + 1)) ;;
    "$SYMLINK_CAT_PACKAGE_MANAGED") SYMLINK_STAT_PACKAGE_MANAGED=$((SYMLINK_STAT_PACKAGE_MANAGED + 1)) ;;
    "$SYMLINK_CAT_RUNTIME_DYNAMIC") SYMLINK_STAT_RUNTIME_DYNAMIC=$((SYMLINK_STAT_RUNTIME_DYNAMIC + 1)) ;;
    "$SYMLINK_CAT_BOOT_CRITICAL") SYMLINK_STAT_BOOT_CRITICAL=$((SYMLINK_STAT_BOOT_CRITICAL + 1)) ;;
    "$SYMLINK_CAT_GTK_CRITICAL") SYMLINK_STAT_GTK_CRITICAL=$((SYMLINK_STAT_GTK_CRITICAL + 1)) ;;
    "$SYMLINK_CAT_UNKNOWN") SYMLINK_STAT_UNKNOWN=$((SYMLINK_STAT_UNKNOWN + 1)) ;;
  esac
  if symlink_category_counts_as_warn "$category"; then
    SYMLINK_STAT_WARNABLE=$((SYMLINK_STAT_WARNABLE + 1))
  fi
}

# Scan all broken symlinks; write classification report; return stats via globals.
symlink_classify_scan() {
  local classification_report="$1"
  local link rel target trace category action reason pkg protby rule

  symlink_classify_reset_stats
  _SYMLINK_DPKG_CACHE="$(mktemp "${TMPDIR:-/tmp}/symlink_dpkg_cache.XXXXXX")"
  : > "${_SYMLINK_DPKG_CACHE}" 2>/dev/null || _SYMLINK_DPKG_CACHE=""

  {
    echo "=== Broken symlink classification ==="
    echo "rootfs: ${ROOTFS_RESOLVED}"
    echo "timestamp: $(date -u +%Y%m%dT%H%M%SZ)"
    echo
  } > "$classification_report"

  while IFS= read -r link; do
    [[ -z "$link" ]] && continue
    rel="$(_symlink_rel_from_abs "$link")"
    target="$(readlink "$link" 2>/dev/null || true)"
    trace="$(symlink_classify_link "$link")"
    IFS='|' read -r category action reason pkg protby rule <<< "$trace"
    _symlink_bump_stat "$category"

    {
      echo "[${rel}]"
      echo "path=${link}"
      echo "target=${target}"
      echo "category=${category}"
      echo "reason=${reason}"
      echo "package=${pkg:--}"
      echo "action=${action}"
      echo "protected_by=${protby}"
      echo "matched_rule=${rule}"
      echo
    } >> "$classification_report"
  done < <(find "${ROOTFS_RESOLVED}" -xtype l 2>/dev/null || true)

  {
    echo "=== Category statistics ==="
    echo "INTENTIONAL: ${SYMLINK_STAT_INTENTIONAL}"
    echo "REMOVABLE: ${SYMLINK_STAT_REMOVABLE}"
    echo "PACKAGE_MANAGED: ${SYMLINK_STAT_PACKAGE_MANAGED}"
    echo "RUNTIME_DYNAMIC: ${SYMLINK_STAT_RUNTIME_DYNAMIC}"
    echo "BOOT_CRITICAL: ${SYMLINK_STAT_BOOT_CRITICAL}"
    echo "GTK_CRITICAL: ${SYMLINK_STAT_GTK_CRITICAL}"
    echo "UNKNOWN: ${SYMLINK_STAT_UNKNOWN}"
    echo "TOTAL: ${SYMLINK_STAT_TOTAL}"
    echo "WARNABLE: ${SYMLINK_STAT_WARNABLE}"
  } >> "$classification_report"

  [[ -n "${_SYMLINK_DPKG_CACHE}" && -f "${_SYMLINK_DPKG_CACHE}" ]] && rm -f "${_SYMLINK_DPKG_CACHE}" 2>/dev/null || true
  _SYMLINK_DPKG_CACHE=""
  return 0
}

symlink_write_stats_to_summary() {
  local summary="$1"
  {
    echo ""
    echo "=== Symlink classification statistics ==="
    echo "INTENTIONAL: ${SYMLINK_STAT_INTENTIONAL}"
    echo "REMOVABLE: ${SYMLINK_STAT_REMOVABLE}"
    echo "PACKAGE_MANAGED: ${SYMLINK_STAT_PACKAGE_MANAGED}"
    echo "RUNTIME_DYNAMIC: ${SYMLINK_STAT_RUNTIME_DYNAMIC}"
    echo "BOOT_CRITICAL: ${SYMLINK_STAT_BOOT_CRITICAL}"
    echo "GTK_CRITICAL: ${SYMLINK_STAT_GTK_CRITICAL}"
    echo "UNKNOWN: ${SYMLINK_STAT_UNKNOWN}"
    echo "TOTAL: ${SYMLINK_STAT_TOTAL}"
    echo "WARNABLE: ${SYMLINK_STAT_WARNABLE}"
  } >> "$summary"
}
