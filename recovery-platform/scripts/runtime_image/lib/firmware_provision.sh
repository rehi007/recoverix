#!/usr/bin/env bash
# Stage AMDGPU firmware from host Ubuntu into runtime rootfs (no chroot apt).

runtime_host_amdgpu_firmware_src() {
  printf '%s' "${RUNTIME_HOST_FIRMWARE_AMDGPU:-/usr/lib/firmware/amdgpu}"
}

runtime_rootfs_amdgpu_firmware_dst() {
  printf '%s' "${ROOTFS_RESOLVED}/usr/lib/firmware/amdgpu"
}

runtime_firmware_file_count() {
  local dir="$1"
  find "$dir" -type f 2>/dev/null | wc -l | tr -d ' '
}

runtime_firmware_bin_count() {
  local dir="$1"
  find "$dir" -type f \( -name '*.bin' -o -name '*.bin.zst' \) 2>/dev/null | wc -l | tr -d ' '
}

runtime_firmware_resolve_dir() {
  local root="${ROOTFS_RESOLVED}"
  local sub="${1:-amdgpu}"

  if [[ -d "${root}/usr/lib/firmware/${sub}" ]]; then
    printf '%s' "${root}/usr/lib/firmware/${sub}"
    return 0
  fi
  if [[ -d "${root}/lib/firmware/${sub}" ]]; then
    printf '%s' "${root}/lib/firmware/${sub}"
    return 0
  fi
  return 1
}

runtime_verify_host_amdgpu_firmware() {
  local src count bin_count
  local min_files="${RUNTIME_MIN_AMDGPU_FIRMWARE_FILES:-50}"
  local min_bin="${RUNTIME_MIN_AMDGPU_FIRMWARE_BIN:-10}"

  src="$(runtime_host_amdgpu_firmware_src)"
  log "=== Host AMDGPU firmware verification ==="
  log "source: ${src}"

  if [[ ! -d "$src" ]]; then
    log "FAIL: host amdgpu firmware directory missing (${src})"
    log "hint: install linux-firmware on host Ubuntu"
    return 1
  fi

  count="$(runtime_firmware_file_count "$src")"
  bin_count="$(runtime_firmware_bin_count "$src")"
  log "INFO: host amdgpu file_count=${count} bin_count=${bin_count}"

  if [[ "$count" -lt "$min_files" ]]; then
    log "FAIL: host amdgpu firmware incomplete (${count} files, need >=${min_files})"
    return 1
  fi
  log "PASS: host amdgpu firmware file count (${count} >= ${min_files})"

  if [[ "$bin_count" -lt "$min_bin" ]]; then
    log "FAIL: host amdgpu .bin firmware insufficient (${bin_count} .bin, need >=${min_bin})"
    return 1
  fi
  log "PASS: host amdgpu .bin payloads (${bin_count} >= ${min_bin})"
  return 0
}

runtime_copy_host_amdgpu_firmware() {
  local src dst host_files host_bins
  local root_files root_bins

  src="$(runtime_host_amdgpu_firmware_src)"
  dst="$(runtime_rootfs_amdgpu_firmware_dst)"

  log "=== Copy host AMDGPU firmware into rootfs ==="
  log "source: ${src}"
  log "target: ${dst}"

  if ! runtime_verify_host_amdgpu_firmware; then
    return 1
  fi

  host_files="$(runtime_firmware_file_count "$src")"
  host_bins="$(runtime_firmware_bin_count "$src")"

  mkdir -p "${ROOTFS_RESOLVED}/usr/lib/firmware"

  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "${src}/" "${dst}/"
  else
    rm -rf "${dst}"
    mkdir -p "${dst}"
    cp -a "${src}/." "${dst}/"
  fi

  if [[ ! -d "$dst" ]]; then
    log "FAIL: firmware copy did not create ${dst}"
    return 1
  fi

  root_files="$(runtime_firmware_file_count "$dst")"
  root_bins="$(runtime_firmware_bin_count "$dst")"
  log "INFO: after copy rootfs file_count=${root_files} bin_count=${root_bins} (host files=${host_files} bins=${host_bins})"

  if [[ "$root_files" -lt "$host_files" ]]; then
    log "FAIL: rootfs amdgpu file count (${root_files}) < host (${host_files})"
    return 1
  fi
  if [[ "$root_bins" -lt "$host_bins" ]]; then
    log "FAIL: rootfs amdgpu .bin count (${root_bins}) < host (${host_bins})"
    return 1
  fi

  log "PASS: host amdgpu firmware copied into rootfs"
  return 0
}

runtime_verify_rootfs_firmware() {
  local failures=0
  local fw_dir count bin_count min_files min_bin

  min_files="${RUNTIME_MIN_AMDGPU_FIRMWARE_FILES:-50}"
  min_bin="${RUNTIME_MIN_AMDGPU_FIRMWARE_BIN:-10}"

  log "=== Rootfs firmware verification (amdgpu) ==="

  if [[ -d "${ROOTFS_RESOLVED}/usr/lib/firmware" ]]; then
    log "PASS: firmware tree exists (${ROOTFS_RESOLVED}/usr/lib/firmware)"
  elif [[ -d "${ROOTFS_RESOLVED}/lib/firmware" ]]; then
    log "PASS: firmware tree exists (${ROOTFS_RESOLVED}/lib/firmware)"
  else
    log "FAIL: missing usr/lib/firmware (and /lib/firmware) in rootfs"
    failures=$((failures + 1))
  fi

  if fw_dir="$(runtime_firmware_resolve_dir amdgpu)"; then
    count="$(runtime_firmware_file_count "$fw_dir")"
    bin_count="$(runtime_firmware_bin_count "$fw_dir")"
    log "INFO: amdgpu dir=${fw_dir} file_count=${count} bin_count=${bin_count}"

    if [[ "$count" -ge "$min_files" ]]; then
      log "PASS: amdgpu firmware present (${count} files, min=${min_files})"
    else
      log "FAIL: amdgpu firmware incomplete (${count} files, need >=${min_files})"
      failures=$((failures + 1))
    fi

    if [[ "$bin_count" -ge "$min_bin" ]]; then
      log "PASS: amdgpu .bin payloads (${bin_count} .bin, min=${min_bin})"
    else
      log "FAIL: amdgpu .bin firmware insufficient (${bin_count} .bin, need >=${min_bin})"
      failures=$((failures + 1))
    fi
  else
    log "FAIL: missing usr/lib/firmware/amdgpu (and /lib/firmware/amdgpu)"
    failures=$((failures + 1))
  fi

  [[ $failures -eq 0 ]] && return 0
  return 1
}

runtime_ensure_amdgpu_firmware() {
  log "=== Ensure AMDGPU firmware in rootfs (host copy) ==="

  if ! runtime_copy_host_amdgpu_firmware; then
    log "FAIL: could not copy host amdgpu firmware into rootfs"
    return 1
  fi

  if runtime_verify_rootfs_firmware; then
    log "PASS: rootfs amdgpu firmware ready"
    return 0
  fi

  log "FAIL: rootfs amdgpu firmware validation failed after host copy"
  return 1
}

runtime_linux_modules_extra_package_name() {
  printf 'linux-modules-extra-%s' "${KEEP_KERNEL_FLAVOR:?}"
}

runtime_amdgpu_kernel_module_find_in_rootfs() {
  find "${ROOTFS_RESOLVED}/lib/modules" -name 'amdgpu*.ko*' 2>/dev/null | LC_ALL=C sort -u
}

# Path for unsquashfs -ll raw listing paired with runtime_squashfs_ll_build_index (integrity_check.sh).
runtime_squashfs_index_raw_listing_path() {
  local sq_index_file="${1:?}"
  local raw="${sq_index_file%.index.txt}.raw_listing.txt"

  if [[ "$raw" == "$sq_index_file" ]]; then
    printf '%s.raw_listing.txt' "$sq_index_file"
  else
    printf '%s' "$raw"
  fi
}

# Extract amdgpu.ko paths from unsquashfs -ll raw listing (squashfs-root/ prefix stripped).
runtime_squashfs_amdgpu_find_in_raw_listing() {
  local raw_listing="${1:?}"

  [[ -s "$raw_listing" ]] || return 0
  awk '
  /amdgpu\.ko/ {
    for (i = 1; i <= NF; i++) {
      if ($i ~ /^squashfs-root\//) {
        path = $i
        sub(/^squashfs-root\/?/, "", path)
        print path
        break
      }
    }
  }' "$raw_listing" 2>/dev/null | LC_ALL=C sort -u
}

# Forensic: unsquashfs -ll on squashfs and search amdgpu.ko (no index awk).
runtime_squashfs_amdgpu_find_via_unsquashfs_ll() {
  local sq="${1:?}"
  local timeout_sec="${RUNTIME_SQ_LISTING_TIMEOUT_SEC:-30}"
  local raw_tmp rc=0

  if ! command -v unsquashfs >/dev/null 2>&1; then
    log_warn "WARN: unsquashfs not available for amdgpu forensic listing"
    return 0
  fi

  raw_tmp="$(mktemp)"
  if command -v timeout >/dev/null 2>&1; then
    timeout "${timeout_sec}s" unsquashfs -ll "$sq" >"$raw_tmp" 2>&1
    rc=$?
  else
    unsquashfs -ll "$sq" >"$raw_tmp" 2>&1
    rc=$?
  fi
  if [[ $rc -ne 0 ]]; then
    log_warn "WARN: unsquashfs -ll amdgpu forensic listing failed (rc=${rc})"
    rm -f "$raw_tmp"
    return 0
  fi
  runtime_squashfs_amdgpu_find_in_raw_listing "$raw_tmp"
  rm -f "$raw_tmp"
}

runtime_amdgpu_kernel_module_append_report() {
  local report="${1:-}"
  local pkg paths_line
  local -a paths=()
  local count=0

  pkg="$(runtime_linux_modules_extra_package_name)"
  mapfile -t paths < <(runtime_amdgpu_kernel_module_find_in_rootfs)
  count="${#paths[@]}"
  if [[ "$count" -gt 0 ]]; then
    paths_line="${paths[*]}"
  else
    paths_line="(none)"
  fi

  for line in \
    "linux_modules_extra_package: ${pkg}" \
    "amdgpu_kernel_module_count: ${count}" \
    "amdgpu_kernel_module_paths: ${paths_line}"; do
    log "$line"
    [[ -n "$report" ]] && printf '%s\n' "$line" >>"$report"
  done
}

runtime_verify_rootfs_amdgpu_kernel_module() {
  local report="${1:-}"
  local failures=0
  local pkg count
  local kver="${KEEP_KERNEL_FLAVOR:?}"
  local verify_rootfs_path verify_modules_path

  pkg="$(runtime_linux_modules_extra_package_name)"
  verify_rootfs_path="$(runtime_kernel_artifact_validation_root)"
  verify_modules_path="$(runtime_kernel_artifact_modules_path)"

  log "=== Rootfs amdgpu kernel module verification ==="
  log "verify_rootfs_path=${verify_rootfs_path}"
  log "verify_modules_path=${verify_modules_path}"
  [[ -n "$report" ]] && {
    echo "verify_function=runtime_verify_rootfs_amdgpu_kernel_module" >>"$report"
    echo "verify_rootfs_path=${verify_rootfs_path}" >>"$report"
    echo "verify_modules_path=${verify_modules_path}" >>"$report"
  }

  if declare -f runtime_log_kernel_artifact_forensic >/dev/null 2>&1; then
    runtime_log_kernel_artifact_forensic "amdgpu_verify" "$report"
    runtime_check_kernel_artifact_path_mismatch "$report"
  fi

  if declare -f runtime_kernel_package_log_provision_context >/dev/null 2>&1; then
    runtime_kernel_package_log_provision_context "$report"
  fi
  if declare -f runtime_kernel_package_append_install_report >/dev/null 2>&1; then
    runtime_kernel_package_append_install_report "$kver" "$report"
  fi
  if declare -f runtime_kernel_package_log_dpkg_query_w >/dev/null 2>&1; then
    runtime_kernel_package_log_dpkg_query_w "$kver" "$report"
  fi
  if declare -f runtime_kernel_package_log_chroot_dpkg_list >/dev/null 2>&1; then
    if declare -f runtime_debootstrap_mount >/dev/null 2>&1 && runtime_debootstrap_mount; then
      runtime_kernel_package_log_chroot_dpkg_list "$report"
      runtime_debootstrap_umount || true
    else
      runtime_kernel_package_report_line "$report" \
        "chroot_dpkg_list_skipped=mount failed (run during squashfs build with chroot)"
    fi
  fi

  runtime_amdgpu_kernel_module_append_report "$report"

  if host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
    log "PASS: ${pkg} installed in rootfs"
    [[ -n "$report" ]] && echo "PASS: ${pkg} installed in rootfs" >>"$report"
  else
    log_fail "FAIL: runtime_verify_rootfs_amdgpu_kernel_module — ${pkg} not installed in rootfs"
    [[ -n "$report" ]] && echo "FAIL: runtime_verify_rootfs_amdgpu_kernel_module — ${pkg} not installed in rootfs" >>"$report"
    if declare -f runtime_read_rootfs_source_marker >/dev/null 2>&1; then
      local _src
      _src="$(runtime_read_rootfs_source_marker 2>/dev/null || true)"
      if [[ -z "$_src" || "$_src" != "debootstrap" ]]; then
        runtime_kernel_package_report_line "$report" \
          "hint: rootfs may have been reused without runtime_install_kernel_packages — set RECOVERIX_FORCE_DEBOOTSTRAP=1"
      fi
    fi
    failures=$((failures + 1))
  fi

  count="$(runtime_amdgpu_kernel_module_find_in_rootfs | wc -l | tr -d ' ')"
  if [[ "${count:-0}" -ge 1 ]]; then
    log "PASS: amdgpu kernel module present"
    [[ -n "$report" ]] && echo "PASS: amdgpu kernel module present" >>"$report"
  else
    log_fail "FAIL: runtime_verify_rootfs_amdgpu_kernel_module — amdgpu kernel module missing"
    [[ -n "$report" ]] && echo "FAIL: runtime_verify_rootfs_amdgpu_kernel_module — amdgpu kernel module missing" >>"$report"
    failures=$((failures + 1))
  fi

  if [[ "${RECOVERIX_FORCE_DEBOOTSTRAP:-0}" == "1" ]] && \
     declare -f runtime_kernel_package_assert_force_debootstrap_contract >/dev/null 2>&1; then
    if ! runtime_kernel_package_assert_force_debootstrap_contract "$report"; then
      failures=$((failures + 1))
    fi
  fi

  [[ $failures -eq 0 ]] && return 0
  return 1
}

runtime_verify_squashfs_amdgpu_kernel_module() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local report="${1:-}"
  local failures=0
  local index count rootfs_count raw_match_count unsquashfs_count
  local index_total_lines raw_amdgpu_line_count
  local pkg raw_listing unsquashfs_forensic_cmd
  local verify_method search_path index_build_cmd index_verify_cmd detected_prefix
  local -a paths=()
  local -a rootfs_paths=()
  local -a unsquashfs_paths=()

  pkg="$(runtime_linux_modules_extra_package_name)"
  verify_method="runtime_squashfs_ll_build_index+awk+unsquashfs_ll_grep"
  search_path="lib/modules/**/amdgpu*.ko*; usr/lib/modules/**/amdgpu*.ko*"
  index_build_cmd="unsquashfs -ll (integrity_check.sh runtime_squashfs_ll_build_index); awk (_rt_squashfs_build_index_from_raw)"
  index_verify_cmd="awk -F tab ((^|/)lib/modules|(^|/)usr/lib/modules + amdgpu*.ko); grep -E"

  log "=== Squashfs amdgpu kernel module verification ==="

  if [[ ! -f "$sq" ]]; then
    log "FAIL: squashfs missing for amdgpu kernel module check"
    failures=$((failures + 1))
    return 1
  fi

  if ! declare -f runtime_squashfs_ll_build_index >/dev/null 2>&1; then
    log "FAIL: runtime_squashfs_ll_build_index unavailable (source integrity_check.sh)"
    return 1
  fi

  [[ -n "$report" ]] && {
    printf 'linux_modules_extra_package: %s\n' "$pkg"
    printf 'squashfs_amdgpu_verify_method=%s\n' "$verify_method"
    printf 'squashfs_amdgpu_search_path=%s\n' "$search_path"
    printf 'squashfs_index_build_command=%s\n' "$index_build_cmd"
    printf 'squashfs_index_verify_command=%s\n' "$index_verify_cmd"
  } >>"$report"

  log "squashfs_amdgpu_verify_method=${verify_method}"
  log "squashfs_amdgpu_search_path=${search_path}"
  log "squashfs_index_build_command=${index_build_cmd}"
  log "squashfs_index_verify_command=${index_verify_cmd}"

  # Rootfs inventory (find).
  mapfile -t rootfs_paths < <(runtime_amdgpu_kernel_module_find_in_rootfs)
  rootfs_count="${#rootfs_paths[@]}"
  log "rootfs_amdgpu_match_count=${rootfs_count}"
  log "rootfs_amdgpu_find_command=find ${ROOTFS_RESOLVED}/lib/modules -name 'amdgpu*.ko*'"
  if [[ "$rootfs_count" -gt 0 ]]; then
    log "rootfs_amdgpu_paths: ${rootfs_paths[*]}"
  else
    log "rootfs_amdgpu_paths: (none)"
  fi
  [[ -n "$report" ]] && {
    printf 'rootfs_amdgpu_match_count=%s\n' "$rootfs_count"
    if [[ "$rootfs_count" -gt 0 ]]; then
      printf 'rootfs_amdgpu_paths: %s\n' "${rootfs_paths[*]}"
    else
      printf 'rootfs_amdgpu_paths: (none)\n'
    fi
  } >>"$report"

  index="$(mktemp)"
  runtime_squashfs_ll_build_index "$sq" "$index"
  raw_listing="$(runtime_squashfs_index_raw_listing_path "$index")"
  log "squashfs_index_raw_listing=${raw_listing}"

  index_total_lines="$(wc -l <"$index" 2>/dev/null | tr -d ' ')"
  index_total_lines="${index_total_lines:-0}"
  log "squashfs_index_line_count=${index_total_lines}"

  # Index-based verification (usrmerge: lib/modules and usr/lib/modules).
  mapfile -t paths < <(awk -F '\t' \
    '($1 ~ /(^|\/)lib\/modules\// || $1 ~ /(^|\/)usr\/lib\/modules\//) && $1 ~ /amdgpu.*\.ko/ { print $1 }' \
    "$index" | LC_ALL=C sort -u)
  count="${#paths[@]}"
  raw_match_count="$(grep -E -c '(^lib/modules/.*/amdgpu.*\.ko|^usr/lib/modules/.*/amdgpu.*\.ko)' "$index" 2>/dev/null || true)"
  raw_match_count="${raw_match_count:-0}"

  detected_prefix=""
  if [[ "$count" -gt 0 ]]; then
    local _has_lib_prefix=0 _has_usr_lib_prefix=0 _p
    for _p in "${paths[@]}"; do
      case "$_p" in
        usr/lib/modules/*) _has_usr_lib_prefix=1 ;;
        lib/modules/*) _has_lib_prefix=1 ;;
      esac
    done
    [[ $_has_lib_prefix -eq 1 ]] && detected_prefix="lib"
    [[ $_has_usr_lib_prefix -eq 1 ]] && detected_prefix="${detected_prefix}${detected_prefix:+|}usr/lib"
  fi
  [[ -z "$detected_prefix" ]] && detected_prefix="(none)"
  log "squashfs_amdgpu_detected_path_prefix=${detected_prefix}"

  log "squashfs_amdgpu_match_count=${count}"
  log "squashfs_amdgpu_raw_match_count=${raw_match_count}"
  log "amdgpu_kernel_module_count: ${count}"
  if [[ "$count" -gt 0 ]]; then
    log "amdgpu_kernel_module_paths: ${paths[*]}"
  else
    log "amdgpu_kernel_module_paths: (none)"
  fi

  # Forensic: amdgpu.ko in unsquashfs -ll raw listing (same pass as index build when available).
  unsquashfs_forensic_cmd="unsquashfs -ll ${sq}; grep amdgpu.ko"
  if [[ -s "$raw_listing" ]]; then
    mapfile -t unsquashfs_paths < <(runtime_squashfs_amdgpu_find_in_raw_listing "$raw_listing")
    raw_amdgpu_line_count="$(grep -cF 'amdgpu.ko' "$raw_listing" 2>/dev/null || true)"
    raw_amdgpu_line_count="${raw_amdgpu_line_count:-0}"
    log "squashfs_unsquashfs_forensic_source=index_build_raw_listing"
  else
    log_warn "WARN: index raw listing missing — running dedicated unsquashfs -ll amdgpu forensic"
    mapfile -t unsquashfs_paths < <(runtime_squashfs_amdgpu_find_via_unsquashfs_ll "$sq")
    raw_amdgpu_line_count=0
    log "squashfs_unsquashfs_forensic_source=dedicated_unsquashfs_ll"
  fi
  unsquashfs_count="${#unsquashfs_paths[@]}"

  log "squashfs_unsquashfs_match_count=${unsquashfs_count}"
  log "squashfs_unsquashfs_raw_line_count=${raw_amdgpu_line_count}"
  log "squashfs_unsquashfs_forensic_command=${unsquashfs_forensic_cmd}"
  if [[ "$unsquashfs_count" -gt 0 ]]; then
    log "squashfs_unsquashfs_paths: ${unsquashfs_paths[*]}"
    if [[ -s "$raw_listing" ]]; then
      grep -F 'amdgpu.ko' "$raw_listing" 2>/dev/null | head -n 5 | while IFS= read -r line; do
        log "squashfs_unsquashfs_sample: ${line}"
      done
    fi
  else
    log "squashfs_unsquashfs_paths: (none)"
  fi

  [[ -n "$report" ]] && {
    printf 'squashfs_amdgpu_match_count=%s\n' "$count"
    printf 'squashfs_amdgpu_raw_match_count=%s\n' "$raw_match_count"
    printf 'squashfs_amdgpu_detected_path_prefix=%s\n' "$detected_prefix"
    printf 'squashfs_index_line_count=%s\n' "$index_total_lines"
    printf 'amdgpu_kernel_module_count: %s\n' "$count"
    if [[ "$count" -gt 0 ]]; then
      printf 'amdgpu_kernel_module_paths: %s\n' "${paths[*]}"
    else
      printf 'amdgpu_kernel_module_paths: (none)\n'
    fi
    printf 'squashfs_unsquashfs_match_count=%s\n' "$unsquashfs_count"
    printf 'squashfs_unsquashfs_forensic_command=%s\n' "$unsquashfs_forensic_cmd"
    if [[ "$unsquashfs_count" -gt 0 ]]; then
      printf 'squashfs_unsquashfs_paths: %s\n' "${unsquashfs_paths[*]}"
    else
      printf 'squashfs_unsquashfs_paths: (none)\n'
    fi
  } >>"$report"

  if [[ "$count" -ge 1 ]] || [[ "${unsquashfs_count:-0}" -ge 1 ]]; then
    log "PASS: squashfs amdgpu kernel module present"
    [[ -n "$report" ]] && echo "PASS: squashfs amdgpu kernel module present" >>"$report"
  elif [[ "${rootfs_count:-0}" -gt 0 ]]; then
    log_fail "FAIL: amdgpu lost during squashfs build"
    [[ -n "$report" ]] && echo "FAIL: amdgpu lost during squashfs build" >>"$report"
    failures=$((failures + 1))
  else
    log_fail "FAIL: runtime_verify_squashfs_amdgpu_kernel_module — amdgpu kernel module missing"
    [[ -n "$report" ]] && echo "FAIL: runtime_verify_squashfs_amdgpu_kernel_module — amdgpu kernel module missing" >>"$report"
    failures=$((failures + 1))
  fi

  rm -f "$index" "$raw_listing"
  [[ $failures -eq 0 ]] && return 0
  return 1
}

runtime_verify_squashfs_firmware() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local failures=0
  local min_files="${RUNTIME_MIN_AMDGPU_FIRMWARE_FILES:-50}"
  local min_bin="${RUNTIME_MIN_AMDGPU_FIRMWARE_BIN:-10}"
  local index file_count bin_count

  log "=== Squashfs firmware verification (listing index) ==="

  if [[ ! -f "$sq" ]]; then
    log "FAIL: squashfs missing for firmware check"
    return 1
  fi

  index="$(mktemp)"
  runtime_squashfs_ll_build_index "$sq" "$index"

  file_count="$(awk -F '\t' '$1 ~ /^usr\/lib\/firmware\/amdgpu\// { c++ } END { print c + 0 }' "$index")"
  bin_count="$(awk -F '\t' '$1 ~ /^usr\/lib\/firmware\/amdgpu\/.*\.bin(\.zst)?$/ { c++ } END { print c + 0 }' "$index")"
  log "INFO: squashfs amdgpu paths=${file_count} .bin_paths=${bin_count}"

  if [[ "$file_count" -ge "$min_files" ]]; then
    log "PASS: squashfs contains amdgpu firmware (${file_count} paths, min=${min_files})"
  else
    log "FAIL: squashfs amdgpu firmware insufficient (${file_count} paths, need >=${min_files})"
    failures=$((failures + 1))
  fi

  if [[ "$bin_count" -ge "$min_bin" ]]; then
    log "PASS: squashfs contains amdgpu .bin firmware (${bin_count} .bin, min=${min_bin})"
  else
    log "FAIL: squashfs amdgpu .bin insufficient (${bin_count} .bin, need >=${min_bin})"
    failures=$((failures + 1))
  fi

  if awk -F '\t' '$1 ~ /^usr\/lib\/firmware\/amdgpu\// && $1 ~ /\.bin(\.zst)?$/ { found=1; exit } END { exit !found }' "$index"; then
    log "PASS: squashfs lists at least one usr/lib/firmware/amdgpu/*.bin payload"
  else
    log "FAIL: squashfs has no amdgpu .bin firmware payload entries"
    failures=$((failures + 1))
  fi

  rm -f "$index"
  [[ $failures -eq 0 ]] && return 0
  return 1
}
