#!/usr/bin/env bash
# Recoverix initrd paths, build helpers, and hook verification (rootfs/host-safe).

RECOVERIX_INITRD_SUFFIX="${RECOVERIX_INITRD_SUFFIX:--recoverix}"

recoverix_initrd_basename() {
  local kver="${1:-${KEEP_KERNEL_FLAVOR}}"
  printf 'initrd.img-%s%s' "$kver" "$RECOVERIX_INITRD_SUFFIX"
}

recoverix_initrd_rootfs_path() {
  printf '%s/boot/%s' "${ROOTFS_RESOLVED}" "$(recoverix_initrd_basename "$1")"
}

recoverix_initrd_runtime_path() {
  printf '%s/%s' "${RUNTIME_DIR}" "$(recoverix_initrd_basename "$1")"
}

recoverix_initrd_host_path() {
  printf '/boot/recoverix/%s' "$(recoverix_initrd_basename "$1")"
}

# Resolve recoverix initrd build artifact (runtime dir only; not kept in rootfs for squashfs).
recoverix_initrd_resolve_source() {
  local kver="${1:-${KEEP_KERNEL_FLAVOR}}"
  local p
  for p in "$(recoverix_initrd_runtime_path "$kver")"; do
    if [[ -f "$p" ]]; then
      printf '%s' "$p"
      return 0
    fi
  done
  # Transient: mkinitramfs output before 20_install moves it to RUNTIME_DIR
  p="$(recoverix_initrd_rootfs_path "$kver")"
  if [[ -f "$p" ]]; then
    printf '%s' "$p"
    return 0
  fi
  # Legacy: update-initramfs output name (pre-mkinitramfs -o); use only if hooks present
  p="${ROOTFS_RESOLVED}/boot/initrd.img-${kver}"
  if [[ -f "$p" ]] && recoverix_verify_initrd_hooks "$p" 2>/dev/null; then
    printf '%s' "$p"
    return 0
  fi
  return 1
}

recoverix_initrd_is_legacy_name() {
  local path="$1"
  [[ "$path" == *"${RECOVERIX_INITRD_SUFFIX}" ]] && return 1
  return 0
}

recoverix_initrd_required_hooks() {
  cat <<'EOF'
scripts/init-top/00-recoverix-rootdir
scripts/init-top/01-recoverix-tty-quiet
scripts/local-top/00-recoverix-root-tmpfs
scripts/local-premount/recoverix-overlay
scripts/init-bottom/00-recoverix-handoff
scripts/recoverix-lib
etc/recoverix/runtime.conf
EOF
}

recoverix_verify_initrd_hooks() {
  local initrd="$1"
  local report="${2:-}"
  local needle
  local failures=0
  local listing

  [[ -f "$initrd" ]] || return 1
  listing="$(lsinitramfs "$initrd" 2>/dev/null)" || return 1
  local _n=0 _max=64
  while IFS= read -r needle; do
    _n=$((_n + 1))
    if [[ $_n -gt $_max ]]; then
      printf '[runtime-image] FAIL: initrd hook iteration guard exceeded (%s)\n' "$_max" >&2
      return 1
    fi
    [[ -n "$needle" ]] || continue
    if grep -qF "$needle" <<<"$listing"; then
      [[ -n "$report" ]] && printf 'PASS\tinitrd hook: %s\n' "$needle" >> "$report"
    else
      [[ -n "$report" ]] && printf 'FAIL\tinitrd hook missing: %s\n' "$needle" >> "$report"
      failures=$((failures + 1))
    fi
  done < <(recoverix_initrd_required_hooks)
  [[ $failures -eq 0 ]]
}

host_initrd_fingerprint() {
  local kver="${1:-${KEEP_KERNEL_FLAVOR}}"
  local f="/boot/initrd.img-${kver}"
  [[ -f "$f" ]] || return 1
  stat -c '%s:%Y' "$f" 2>/dev/null
}

assert_host_initrd_unchanged() {
  local kver="${1:-${KEEP_KERNEL_FLAVOR}}"
  local before="${2:-}"
  local after
  after="$(host_initrd_fingerprint "$kver" || true)"
  if [[ -n "$before" && -n "$after" && "$before" != "$after" ]]; then
    printf '[runtime-image] ERROR: host initrd changed (%s -> %s) — abort\n' \
      "$before" "$after" >&2
    return 1
  fi
  return 0
}
