#!/usr/bin/env bash
# Install Recoverix runtime utilities into rootfs (no host tree paths at runtime).

runtime_image_dir() {
  if [[ -n "${RUNTIME_IMAGE_DIR:-}" ]]; then
    printf '%s' "${RUNTIME_IMAGE_DIR}"
    return 0
  fi
  printf '%s' "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
}

# $1 = deploy source basename, $2 = installed sbin name (e.g. recoverix-health-check)
runtime_tool_deploy_source() {
  printf '%s/deploy/%s' "$(runtime_image_dir)" "$1"
}

runtime_tool_rootfs_path() {
  printf '%s/usr/local/sbin/%s' "${ROOTFS_RESOLVED}" "$1"
}

runtime_install_rootfs_tool() {
  local src_basename="$1"
  local install_name="$2"
  local src dest

  src="$(runtime_tool_deploy_source "$src_basename")"
  dest="$(runtime_tool_rootfs_path "$install_name")"

  log "=== Install ${install_name} into rootfs ==="
  log "source: ${src}"
  log "target: ${dest}"

  if [[ ! -f "$src" ]]; then
    log "FAIL: tool source missing: ${src}"
    return 1
  fi

  mkdir -p "${ROOTFS_RESOLVED}/usr/local/sbin"
  install -m 0755 "$src" "$dest"
  log "PASS: installed ${install_name} (mode 0755)"
  return 0
}

runtime_verify_rootfs_tool() {
  local install_name="$1"
  local dest

  dest="$(runtime_tool_rootfs_path "$install_name")"
  log "=== Rootfs ${install_name} verification ==="

  if [[ ! -f "$dest" ]]; then
    log "FAIL: missing ${dest}"
    return 1
  fi

  if [[ ! -x "$dest" ]]; then
    log "FAIL: ${dest} is not executable"
    return 1
  fi

  log "PASS: ${dest} exists and is executable"
  return 0
}

runtime_verify_squashfs_tool() {
  local install_name="$1"
  local rel="usr/local/sbin/${install_name}"
  local sq="${RUNTIME_SQUASHFS:?}"
  local tmpdir extracted

  log "=== Squashfs ${install_name} verification ==="

  if [[ ! -f "$sq" ]]; then
    log "FAIL: squashfs missing: ${sq}"
    return 1
  fi

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/recoverix-tool-verify.XXXXXX")"
  if ! unsquashfs -f -d "${tmpdir}" "$sq" "$rel" >>"${tmpdir}/extract.log" 2>&1; then
    log "FAIL: squashfs does not contain /${rel}"
    rm -rf "${tmpdir}"
    return 1
  fi

  extracted="${tmpdir}/${rel}"
  if [[ -f "$extracted" && -x "$extracted" ]]; then
    log "PASS: squashfs contains executable /${rel}"
    rm -rf "${tmpdir}"
    return 0
  fi

  log "FAIL: /${rel} in squashfs is missing or not executable"
  rm -rf "${tmpdir}"
  return 1
}

runtime_health_check_source() {
  runtime_tool_deploy_source "60_runtime_health_check.sh"
}

runtime_health_check_rootfs_path() {
  runtime_tool_rootfs_path "recoverix-health-check"
}

runtime_install_recoverix_health_check() {
  runtime_install_rootfs_tool "60_runtime_health_check.sh" "recoverix-health-check"
}

runtime_verify_rootfs_recoverix_health_check() {
  runtime_verify_rootfs_tool "recoverix-health-check"
}

runtime_verify_squashfs_recoverix_health_check() {
  runtime_verify_squashfs_tool "recoverix-health-check"
}

runtime_install_recoverix_runtime_tools() {
  runtime_install_recoverix_health_check
}

runtime_verify_rootfs_recoverix_runtime_tools() {
  runtime_verify_rootfs_recoverix_health_check
}

runtime_verify_squashfs_recoverix_runtime_tools() {
  local rc=0
  runtime_verify_squashfs_recoverix_health_check || rc=1
  return "$rc"
}
