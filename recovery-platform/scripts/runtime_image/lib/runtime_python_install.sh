#!/usr/bin/env bash
# Install recovery-platform Python packages into runtime rootfs for squashfs.

recoverix_repo_root() {
  if [[ -n "${RECOVERIX_REPO_ROOT:-}" ]]; then
    printf '%s' "${RECOVERIX_REPO_ROOT}"
    return 0
  fi
  printf '%s' "$(cd "$(runtime_image_dir)/../../.." && pwd)"
}

recoverix_platform_src_root() {
  printf '%s/recovery-platform' "$(recoverix_repo_root)"
}

recoverix_python_lib_root() {
  printf '%s/usr/local/lib/recoverix' "${ROOTFS_RESOLVED}"
}

# Packages required for backup/restore GTK runtime (import-closed set).
RUNTIME_PYTHON_PACKAGES=(
  common
  backup_engine
  validation
  boot_manager
  rollback
  restore_engine
  partition_manager
  recovery_runtime
)

# Critical modules verified in rootfs and squashfs.
RUNTIME_PYTHON_VERIFY_PATHS=(
  usr/local/lib/recoverix/backup_engine/__init__.py
  usr/local/lib/recoverix/backup_engine/backup_planner.py
  usr/local/lib/recoverix/backup_engine/manifest.py
  usr/local/lib/recoverix/backup_engine/backup_finalize.py
  usr/local/lib/recoverix/backup_engine/backup_runtime_log.py
  usr/local/lib/recoverix/backup_engine/backup_artifacts.py
  usr/local/lib/recoverix/backup_engine/efi_backup.py
  usr/local/lib/recoverix/backup_engine/run_backup.py
  usr/local/lib/recoverix/common/__init__.py
  usr/local/lib/recoverix/validation/image_validation.py
  usr/local/lib/recoverix/boot_manager/__init__.py
  usr/local/lib/recoverix/boot_manager/bootorder_planner.py
  usr/local/lib/recoverix/boot_manager/firmware_reader.py
  usr/local/lib/recoverix/rollback/failure_counter.py
  usr/local/lib/recoverix/restore_engine/partclone_restore.py
  usr/local/lib/recoverix/partition_manager/models.py
  usr/local/lib/recoverix/recovery_runtime/__init__.py
  usr/local/lib/recoverix/recovery_runtime/discover.py
  usr/local/lib/recoverix/recovery_runtime/gtk_ui/restore_preflight.py
  usr/local/lib/recoverix/recovery_runtime/gtk_ui/restore_plan.py
  usr/local/lib/recoverix/recovery_runtime/gtk_ui/window.py
  usr/local/lib/recoverix/recovery_runtime/gtk_ui/gtk_compat.py
  usr/local/lib/recoverix/recovery_runtime/gtk_ui/selftest.py
)

runtime_copy_python_package() {
  local pkg="$1"
  local src_root dest
  src_root="$(recoverix_platform_src_root)"
  dest="$(recoverix_python_lib_root)/${pkg}"

  if [[ ! -d "${src_root}/${pkg}" ]]; then
    log "FAIL: python package source missing: ${src_root}/${pkg}"
    return 1
  fi

  mkdir -p "$dest"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      --exclude '*.pyo' \
      --exclude '.pytest_cache/' \
      "${src_root}/${pkg}/" "${dest}/"
  else
    find "${src_root}/${pkg}" -name '*.py' -print0 | while IFS= read -r -d '' py; do
      rel="${py#${src_root}/${pkg}/}"
      mkdir -p "${dest}/$(dirname "$rel")"
      install -m 0644 "$py" "${dest}/${rel}"
    done
  fi

  if [[ ! -f "${dest}/__init__.py" ]] && [[ -f "${src_root}/${pkg}/__init__.py" ]]; then
    install -m 0644 "${src_root}/${pkg}/__init__.py" "${dest}/__init__.py"
  fi

  log "PASS: installed python package ${pkg} -> ${dest}"
  return 0
}

runtime_install_recoverix_python_platform() {
  local pkg rc=0
  local lib_root
  lib_root="$(recoverix_python_lib_root)"

  log "=== Install recovery-platform Python packages into rootfs ==="
  log "target: ${lib_root}"
  log "packages: ${RUNTIME_PYTHON_PACKAGES[*]}"

  mkdir -p "$lib_root"
  for pkg in "${RUNTIME_PYTHON_PACKAGES[@]}"; do
    runtime_copy_python_package "$pkg" || rc=1
  done
  return "$rc"
}

runtime_verify_rootfs_recoverix_python_platform() {
  local lib_root rel failures=0

  lib_root="$(recoverix_python_lib_root)"
  log "=== Rootfs python platform verification ==="

  for rel in "${RUNTIME_PYTHON_VERIFY_PATHS[@]}"; do
    if [[ ! -f "${ROOTFS_RESOLVED}/${rel}" ]]; then
      log "FAIL: missing /${rel}"
      failures=$((failures + 1))
    fi
  done

  if [[ $failures -eq 0 ]]; then
    log "PASS: required python modules present under ${lib_root}"
    return 0
  fi
  return 1
}

runtime_verify_squashfs_recoverix_python_platform() {
  local sq="${RUNTIME_SQUASHFS:?}"
  local tmpdir rel extracted failures=0

  log "=== Squashfs python platform verification ==="

  if [[ ! -f "$sq" ]]; then
    log "FAIL: squashfs missing: ${sq}"
    return 1
  fi

  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/recoverix-py-verify.XXXXXX")"
  for rel in "${RUNTIME_PYTHON_VERIFY_PATHS[@]}"; do
    if ! unsquashfs -f -d "${tmpdir}" "$sq" "$rel" >>"${tmpdir}/extract.log" 2>&1; then
      log "FAIL: squashfs does not contain /${rel}"
      failures=$((failures + 1))
      continue
    fi
    extracted="${tmpdir}/${rel}"
    if [[ ! -f "$extracted" ]]; then
      log "FAIL: /${rel} missing after extract"
      failures=$((failures + 1))
    else
      log "PASS: squashfs contains /${rel}"
    fi
  done

  rm -rf "${tmpdir}"
  [[ $failures -eq 0 ]] && return 0
  return 1
}

runtime_run_python_import_self_test() {
  local lib_root py
  lib_root="$(recoverix_python_lib_root)"
  py="${lib_root}/.import_self_test.py"

  cat >"$py" <<'PY'
import backup_engine
import backup_engine.backup_planner
import backup_engine.backup_state
import backup_engine.backup_finalize
import boot_manager.bootorder_planner
import rollback.failure_counter
import recovery_runtime.main
import recovery_runtime.runtime_context
import recovery_runtime.actions
print("PASS: runtime python import self-test")
PY

  if PYTHONPATH="${lib_root}${PYTHONPATH:+:${PYTHONPATH}}" \
    python3 "$py" >>"${REPORT_DIR}/python_import_self_test.log" 2>&1; then
    log "PASS: python import self-test"
    rm -f "$py"
    return 0
  fi

  log "FAIL: python import self-test (see ${REPORT_DIR}/python_import_self_test.log)"
  tail -5 "${REPORT_DIR}/python_import_self_test.log" 2>/dev/null | while read -r line; do
    log "INFO: ${line}"
  done
  rm -f "$py"
  return 1
}
