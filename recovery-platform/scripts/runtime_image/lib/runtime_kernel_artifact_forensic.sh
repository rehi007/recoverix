#!/usr/bin/env bash
# Kernel artifact path/stage forensics (investigation only — no install policy changes).

runtime_kernel_artifact_validation_root() {
  printf '%s' "${ROOTFS_RESOLVED:-}"
}

runtime_kernel_artifact_validation_path() {
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  local root
  root="$(runtime_kernel_artifact_validation_root)"
  printf '%s/boot/vmlinuz-%s' "${root%/}" "$kver"
}

runtime_kernel_artifact_modules_path() {
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  local root
  root="$(runtime_kernel_artifact_validation_root)"
  printf '%s/lib/modules/%s' "${root%/}" "$kver"
}

runtime_kernel_artifact_append_report() {
  local report="${1:-}" stage="${2:?}" validation_path="${3:?}"
  local modules_path="${4:?}" root_exists modules_exists

  root_exists="no"
  modules_exists="no"
  [[ -d "$(runtime_kernel_artifact_validation_root)" ]] && root_exists="yes"
  [[ -d "$modules_path" ]] && modules_exists="yes"

  for line in \
    "kernel_artifact_validation_stage=${stage}" \
    "kernel_artifact_validation_path=${validation_path}" \
    "kernel_artifact_modules_path=${modules_path}" \
    "ROOTFS_RESOLVED=$(runtime_kernel_artifact_validation_root)" \
    "RUNTIME_SQUASHFS=${RUNTIME_SQUASHFS:-}" \
    "rootfs_exists=${root_exists}" \
    "modules_dir_exists=${modules_exists}"; do
    log "$line"
    [[ -n "$report" ]] && printf '%s\n' "$line" >>"$report"
  done
}

runtime_log_kernel_artifact_forensic() {
  local stage="${1:?}" report="${2:-}"
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  local root validation_path modules_path boot_dir

  root="$(runtime_kernel_artifact_validation_root)"
  validation_path="$(runtime_kernel_artifact_validation_path)"
  modules_path="$(runtime_kernel_artifact_modules_path)"
  boot_dir="${root%/}/boot"

  log "=== Kernel artifact forensic (${stage}) ==="
  runtime_kernel_artifact_append_report "$report" "$stage" "$validation_path" "$modules_path"

  if [[ -z "$root" ]]; then
    log "WARN: ROOTFS_RESOLVED unset — skipping ls forensic"
    return 0
  fi

  log "forensic: ls -ld ${root}"
  ls -ld "${root}" 2>&1 | while IFS= read -r line; do
    log "  ${line}"
  done

  if [[ -d "$boot_dir" ]]; then
    log "forensic: ls -l ${boot_dir}"
    ls -l "${boot_dir}" 2>&1 | while IFS= read -r line; do
      log "  ${line}"
    done
  else
    log "forensic: boot dir missing: ${boot_dir}"
  fi

  if [[ -d "${root%/}/lib/modules" ]]; then
    log "forensic: ls -l ${root%/}/lib/modules"
    ls -l "${root%/}/lib/modules" 2>&1 | while IFS= read -r line; do
      log "  ${line}"
    done
  else
    log "forensic: lib/modules missing: ${root%/}/lib/modules"
  fi

  if [[ -e "$validation_path" ]]; then
    log "forensic: vmlinuz present: ${validation_path}"
    ls -l "$validation_path" 2>&1 | while IFS= read -r line; do
      log "  ${line}"
    done
  else
    log "forensic: vmlinuz missing: ${validation_path}"
  fi

  local initrd_path="${boot_dir}/initrd.img-${kver}"
  if [[ -e "$initrd_path" ]]; then
    log "forensic: initrd present: ${initrd_path}"
    ls -l "$initrd_path" 2>&1 | while IFS= read -r line; do
      log "  ${line}"
    done
  else
    log "forensic: initrd missing: ${initrd_path}"
  fi

  if [[ -d "$modules_path" ]]; then
    log "forensic: modules tree present: ${modules_path}"
    ls -ld "$modules_path" 2>&1 | while IFS= read -r line; do
      log "  ${line}"
    done
  else
    log "forensic: modules tree missing: ${modules_path}"
  fi

  if [[ -f "${RUNTIME_SQUASHFS:-}" ]]; then
    local sq_mtime sq_size
    sq_mtime="$(stat -c '%y' "${RUNTIME_SQUASHFS}" 2>/dev/null || echo unknown)"
    sq_size="$(stat -c '%s' "${RUNTIME_SQUASHFS}" 2>/dev/null || echo unknown)"
    log "forensic: squashfs artifact mtime=${sq_mtime} size=${sq_size} path=${RUNTIME_SQUASHFS}"
  else
    log "forensic: squashfs artifact missing: ${RUNTIME_SQUASHFS:-<unset>}"
  fi

  return 0
}

# Compare rootfs on disk vs squashfs listing (diagnostic only).
runtime_check_kernel_artifact_path_mismatch() {
  local report="${1:-}"
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  local root sq
  local rootfs_vmlinuz rootfs_initrd rootfs_modules
  local sq_has_vmlinuz=0 sq_has_initrd=0 sq_has_modules=0
  local rootfs_has_vmlinuz=0 rootfs_has_initrd=0 rootfs_has_modules=0
  local index=""

  root="$(runtime_kernel_artifact_validation_root)"
  sq="${RUNTIME_SQUASHFS:-}"
  rootfs_vmlinuz="${root%/}/boot/vmlinuz-${kver}"
  rootfs_initrd="${root%/}/boot/initrd.img-${kver}"
  rootfs_modules="${root%/}/lib/modules/${kver}"

  [[ -e "$rootfs_vmlinuz" ]] && rootfs_has_vmlinuz=1
  [[ -e "$rootfs_initrd" ]] && rootfs_has_initrd=1
  [[ -d "$rootfs_modules" ]] && rootfs_has_modules=1

  if [[ -f "$sq" ]] && declare -f runtime_squashfs_ll_build_index >/dev/null 2>&1; then
    index="$(mktemp)"
    if runtime_squashfs_ll_build_index "$sq" "$index"; then
      if awk -F '\t' -v k="$kver" '$1 == "boot/vmlinuz-" k { found=1 } END { exit !found }' "$index"; then
        sq_has_vmlinuz=1
      fi
      if awk -F '\t' -v k="$kver" '$1 == "boot/initrd.img-" k { found=1 } END { exit !found }' "$index"; then
        sq_has_initrd=1
      fi
      if awk -F '\t' -v k="$kver" '$1 ~ ("^lib/modules/" k "/") { found=1 } END { exit !found }' "$index"; then
        sq_has_modules=1
      fi
      log "forensic: squashfs index kernel paths vmlinuz=${sq_has_vmlinuz} initrd=${sq_has_initrd} modules=${sq_has_modules}"
      [[ -n "$report" ]] && {
        printf 'squashfs_index_vmlinuz=%s\n' "$([[ $sq_has_vmlinuz -eq 1 ]] && echo yes || echo no)" >>"$report"
        printf 'squashfs_index_initrd=%s\n' "$([[ $sq_has_initrd -eq 1 ]] && echo yes || echo no)" >>"$report"
        printf 'squashfs_index_modules=%s\n' "$([[ $sq_has_modules -eq 1 ]] && echo yes || echo no)" >>"$report"
      }
    else
      log_warn "WARN: could not build squashfs index for kernel path mismatch check"
    fi
    rm -f "$index"
  fi

  log "forensic: rootfs_has vmlinuz=${rootfs_has_vmlinuz} initrd=${rootfs_has_initrd} modules=${rootfs_has_modules}"

  if [[ $rootfs_has_vmlinuz -eq 0 && $sq_has_vmlinuz -eq 1 ]]; then
    log_fail "FAIL: validation path mismatch (rootfs missing boot/vmlinuz-${kver}; squashfs contains it)"
    [[ -n "$report" ]] && echo "FAIL: validation path mismatch (rootfs vmlinuz missing; squashfs has boot/vmlinuz-${kver})" >>"$report"
  fi
  if [[ $rootfs_has_initrd -eq 0 && $sq_has_initrd -eq 1 ]]; then
    log_fail "FAIL: validation path mismatch (rootfs missing boot/initrd.img-${kver}; squashfs contains it)"
    [[ -n "$report" ]] && echo "FAIL: validation path mismatch (rootfs initrd missing; squashfs has boot/initrd.img-${kver})" >>"$report"
  fi
  if [[ $rootfs_has_modules -eq 0 && $sq_has_modules -eq 1 ]]; then
    log_fail "FAIL: validation path mismatch (rootfs missing lib/modules/${kver}; squashfs contains it)"
    [[ -n "$report" ]] && echo "FAIL: validation path mismatch (rootfs modules missing; squashfs has lib/modules/${kver})" >>"$report"
  fi

  if [[ $rootfs_has_vmlinuz -eq 1 && $sq_has_vmlinuz -eq 0 && -f "$sq" ]]; then
    log_warn "WARN: rootfs has vmlinuz but squashfs index does not (squashfs may be stale or exclude boot)"
  fi

  return 0
}
