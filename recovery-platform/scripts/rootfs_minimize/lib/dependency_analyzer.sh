#!/usr/bin/env bash
# apt-rdepends reverse dependency impact analysis for purge sets.

DEPENDENCY_ANCHORS=(
  partclone
  grub-common
  python3-gi
  libgtk-3-0
  initramfs-tools
  ntfs-3g
  efibootmgr
)

analyze_dependency_impact() {
  local report="$1"
  shift
  local purge_pkgs=("$@")
  local dep_log="${REPORT_DIR}/dependency_tree_$(date -u +%Y%m%dT%H%M%SZ).log"
  : > "$dep_log"

  if ! chroot_run "command -v apt-rdepends" &>/dev/null; then
    printf 'SKIP\tdependency_tree\tDEPENDENCY_CHECK_SKIPPED: apt-rdepends not in rootfs (optional for minimal runtime)\n' >> "$report"
    log "SKIP: apt-rdepends missing — dependency check skipped (does not block purge)"
    return 0
  fi

  local anchor closure_file
  closure_file="$(mktemp)"
  trap 'rm -f "$closure_file"' RETURN

  local anchor
  for anchor in "${DEPENDENCY_ANCHORS[@]}"; do
    if ! host_dpkg_query -W -f='${Status}' "$anchor" 2>/dev/null | grep -q installed; then
      printf 'WARN\tdependency_tree\tanchor not installed: %s\n' "$anchor" >> "$report"
      continue
    fi
    log "dependency tree for anchor: $anchor"
    {
      echo "=== apt-rdepends $anchor ==="
      chroot_run "apt-rdepends $anchor 2>/dev/null" || true
      echo
    } >> "$dep_log"
    chroot_run "apt-rdepends $anchor 2>/dev/null" >> "$closure_file" || true
  done

  local purge pkg line
  local conflict=0
  for purge in "${purge_pkgs[@]}"; do
    [[ -z "$purge" ]] && continue
    if grep -qx "$purge" "$closure_file" 2>/dev/null; then
      printf 'FAIL\tdependency_tree\tpurge %s appears in anchor dependency closure\n' "$purge" >> "$report"
      conflict=1
    fi
    # reverse: would removing purge break anchors?
    local rd
    rd="$(chroot_run "apt-cache rdepends --installed ${purge} 2>/dev/null" || true)"
    if echo "$rd" | grep -qE '(^|[[:space:]])(partclone|grub-common|python3-gi|libgtk-3-0|initramfs-tools|ntfs-3g|efibootmgr)($|[[:space:]])'; then
      printf 'FAIL\tdependency_tree\tinstalled rdepends link purge %s -> critical stack\n' "$purge" >> "$report"
      conflict=1
    fi
  done

  if [[ $conflict -ne 0 ]]; then
    die "DEPENDENCY: purge set impacts critical dependency tree — see $dep_log and $report"
  fi

  printf 'PASS\tdependency_tree\tno anchor closure conflict (log: %s)\n' "$dep_log" >> "$report"
  log "dependency tree log: $dep_log"
}
