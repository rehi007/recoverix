#!/usr/bin/env bash
# Prevent recursive squashfs / initrd embedding in the runtime rootfs image.

runtime_rootfs_boot_staging_dir() {
  printf '%s' "${ROOTFS_RESOLVED}/boot/recoverix"
}

# Remove host-only boot artifacts accidentally left under rootfs.
runtime_purge_rootfs_boot_staging() {
  local staging_dir initrd_path kver

  staging_dir="$(runtime_rootfs_boot_staging_dir)"
  log "=== Purge rootfs boot staging (must not enter squashfs) ==="

  if [[ -d "$staging_dir" ]]; then
    # Avoid find|grep under set -e (empty dir makes grep exit 1).
    if [[ -n "$(find "$staging_dir" -mindepth 1 -print -quit 2>/dev/null || true)" ]]; then
      log "removing ${staging_dir}/*"
      rm -rf "${staging_dir:?}"/*
    else
      log "INFO: ${staging_dir} already empty"
    fi
  else
    log "INFO: ${staging_dir} absent (OK)"
  fi

  kver="${KEEP_KERNEL_FLAVOR:?}"
  initrd_path="$(recoverix_initrd_rootfs_path "$kver")"
  if [[ -f "$initrd_path" ]]; then
    log "removing recoverix initrd from rootfs (use ${RUNTIME_DIR}/): ${initrd_path}"
    rm -f "$initrd_path"
  fi

  return 0
}

runtime_verify_rootfs_no_staged_artifacts() {
  local failures=0
  local -a hits

  log "=== Rootfs staged-artifact guard (pre-squashfs) ==="
  log "policy: runtime.squashfs + initrd.img-*-recoverix must NOT live in rootfs"

  mapfile -t hits < <(find "${ROOTFS_RESOLVED}" -type f -name '*.squashfs' 2>/dev/null || true)
  if [[ ${#hits[@]} -gt 0 ]]; then
    log "FAIL: rootfs contains squashfs image(s):"
    printf '       %s\n' "${hits[@]}"
    failures=$((failures + 1))
  else
    log "PASS: no *.squashfs under rootfs"
  fi

  mapfile -t hits < <(find "${ROOTFS_RESOLVED}" -type f -name 'initrd.img-*-recoverix' 2>/dev/null || true)
  if [[ ${#hits[@]} -gt 0 ]]; then
    log "FAIL: rootfs contains recoverix initrd(s):"
    printf '       %s\n' "${hits[@]}"
    failures=$((failures + 1))
  else
    log "PASS: no initrd.img-*-recoverix under rootfs"
  fi

  if [[ -f "$(runtime_rootfs_boot_staging_dir)/runtime.squashfs" ]]; then
    log "FAIL: $(runtime_rootfs_boot_staging_dir)/runtime.squashfs must not exist in rootfs"
    failures=$((failures + 1))
  fi

  if [[ -n "$(compgen -G "$(runtime_rootfs_boot_staging_dir)/initrd.img-*-recoverix" 2>/dev/null || true)" ]]; then
    log "FAIL: recoverix initrd under $(runtime_rootfs_boot_staging_dir)"
    failures=$((failures + 1))
  fi

  [[ $failures -eq 0 ]] && return 0
  return 1
}

runtime_verify_squashfs_no_nested_boot_artifacts() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local index failures=0

  log "=== Squashfs nested boot-artifact guard ==="

  if [[ ! -f "$sq" ]]; then
    log "FAIL: squashfs missing"
    return 1
  fi

  index="$(mktemp)"
  runtime_squashfs_ll_build_index "$sq" "$index"

  if awk -F '\t' '$1 ~ /^boot\/recoverix\/.*\.squashfs$/ { found=1 } END { exit !found }' "$index" 2>/dev/null; then
    log "FAIL: squashfs embeds boot/recoverix/*.squashfs (recursive image)"
    failures=$((failures + 1))
  else
    log "PASS: no boot/recoverix/*.squashfs inside squashfs"
  fi

  if awk -F '\t' '$1 ~ /^boot\/recoverix\/initrd\.img-.*-recoverix$/ { found=1 } END { exit !found }' "$index" 2>/dev/null; then
    log "FAIL: squashfs embeds boot/recoverix/initrd.img-*-recoverix"
    failures=$((failures + 1))
  else
    log "PASS: no recoverix initrd under boot/recoverix in squashfs"
  fi

  if awk -F '\t' '$1 == "boot/recoverix/runtime.squashfs" { found=1 } END { exit !found }' "$index" 2>/dev/null; then
    log "FAIL: squashfs contains boot/recoverix/runtime.squashfs"
    failures=$((failures + 1))
  fi

  rm -f "$index"
  [[ $failures -eq 0 ]] && return 0
  return 1
}
