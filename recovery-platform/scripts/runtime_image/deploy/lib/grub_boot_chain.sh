#!/usr/bin/env bash
# GRUB boot-chain tracing, contamination checks, and host Recoverix entry purge.
# Sourced by deploy/40_stage_esp_runtime.sh and 12_verify_esp_layout.sh.

RECOVERIX_GRUB_STANDALONE_MARKER='Recoverix standalone GRUB active'
RECOVERIX_GRUB_EMBEDDED_CONFIG_PATH='boot/grub/grub.cfg'
RECOVERIX_GRUB_EMBEDDED_PREFIX='(memdisk)/boot/grub'

RECOVERIX_GRUB_MENU_TITLES=(
  'Windows Boot Manager'
  'Recoverix Runtime'
  'Recoverix Runtime (systemd debug)'
)

RECOVERIX_GRUB_DIRECT_MENU_TITLES=(
  'Recoverix Runtime'
  'Recoverix Runtime (systemd debug)'
)

recoverix_grub_sed_render() {
  local in="${1:?}"
  local out="${2:?}"
  local recovery_uuid="${3:?}"
  local kernel_version="${4:?}"
  local esp_uuid="${5:?}"
  local recoveryboot_rel="${6:?}"
  local recovery_hotkey="${7:-q}"
  local boot_timeout="${8:-2}"

  sed -e "s/@RECOVERY_UUID@/${recovery_uuid}/g" \
      -e "s/@KERNEL_VERSION@/${kernel_version}/g" \
      -e "s/@ESP_UUID@/${esp_uuid}/g" \
      -e "s|@RECOVERYBOOT_REL_DIR@|${recoveryboot_rel}|g" \
      -e "s/@RECOVERY_HOTKEY@/${recovery_hotkey}/g" \
      -e "s/@BOOT_TIMEOUT_SEC@/${boot_timeout}/g" \
      "$in" >"$out"
  chmod 0644 "$out"
}

recoverix_grub_render_external_menu_cfg() {
  local template="${1:?}"
  local out="${2:?}"
  local recovery_uuid="${3:?}"
  local kernel_version="${4:?}"
  local esp_uuid="${5:?}"
  local recoveryboot_rel="${6:?}"
  local recovery_hotkey="${7:-q}"
  local boot_timeout="${8:-2}"

  [[ -f "$template" ]] || return 1
  recoverix_grub_sed_render "$template" "$out" "$recovery_uuid" "$kernel_version" "$esp_uuid" "$recoveryboot_rel" "$recovery_hotkey" "$boot_timeout"
}

recoverix_grub_render_embedded_bootstrap() {
  local bootstrap_template="${1:?}"
  local out="${2:?}"
  local recovery_uuid="${3:?}"
  local kernel_version="${4:?}"
  local esp_uuid="${5:?}"
  local recoveryboot_rel="${6:?}"
  local recovery_hotkey="${7:-q}"
  local boot_timeout="${8:-2}"

  [[ -f "$bootstrap_template" ]] || return 1
  recoverix_grub_sed_render "$bootstrap_template" "$out" "$recovery_uuid" "$kernel_version" "$esp_uuid" "$recoveryboot_rel" "$recovery_hotkey" "$boot_timeout"
}

recoverix_grub_report_generation_sources() {
  local report="${1:?}"
  local external_template="${2:?}"
  local embedded_template="${3:?}"
  local external_out="${4:?}"
  local embedded_out="${5:?}"

  {
    echo "grub_generation_sources:"
    echo "  external_menu_template: ${external_template}"
    echo "  embedded_bootstrap_template: ${embedded_template}"
    echo "  external_active_grub_cfg: ${external_out}"
    echo "  embedded_mkstandalone_cfg: ${embedded_out}"
    echo "  appended_sections: none (two-file split: bootstrap embedded + menu external)"
  } >>"$report"
}

recoverix_grub_dump_contamination() {
  local report="${1:?}"
  local label="${2:?}"
  local cfg="${3:?}"

  {
    echo "${label}_contamination_dump:"
    if [[ -f "$cfg" ]]; then
      grep -nE 'prefix|configfile|source|ubuntu|boot/grub|\$\{config_directory\}|\$\{cmdpath\}|search --file' "$cfg" 2>/dev/null \
        || echo "  (no matching lines)"
    else
      echo "  (file missing)"
    fi
  } >>"$report"
}

# External EFI/RecoveryBoot/grub.cfg — menu-only; no Ubuntu chain directives.
recoverix_grub_validate_external_menu_cfg() {
  local cfg="${1:-}"
  [[ -f "$cfg" ]] || { printf 'missing external grub.cfg\n'; return 1; }

  if grep -qE '(^|[[:space:]])configfile[[:space:]]+' "$cfg" 2>/dev/null; then
    printf 'configfile directive present\n'
    return 1
  fi
  if grep -qE '(^|[[:space:]])source[[:space:]]+' "$cfg" 2>/dev/null; then
    printf 'source directive present\n'
    return 1
  fi
  if grep -qE '(^|[[:space:]])prefix=' "$cfg" 2>/dev/null; then
    printf 'prefix= override present\n'
    return 1
  fi
  if grep -qi 'EFI/ubuntu' "$cfg" 2>/dev/null; then
    printf 'EFI/ubuntu reference present\n'
    return 1
  fi
  if grep -qF '/boot/grub' "$cfg" 2>/dev/null; then
    printf '/boot/grub reference present\n'
    return 1
  fi
  if grep -qF '${config_directory}' "$cfg" 2>/dev/null; then
    printf '${config_directory} present\n'
    return 1
  fi
  if grep -qF '${cmdpath}' "$cfg" 2>/dev/null; then
    printf '${cmdpath} present\n'
    return 1
  fi
  if grep -qE 'search[[:space:]]+--file.*grub' "$cfg" 2>/dev/null; then
    printf 'search --file grub present\n'
    return 1
  fi
  return 0
}

# Embedded bootstrap (grub-mkstandalone) — ESP search + configfile to RecoveryBoot menu cfg.
recoverix_grub_validate_embedded_bootstrap() {
  local cfg="${1:?}"
  local recoveryboot_rel="${2:?}"
  local esp_uuid="${3:?}"
  local expected_configfile="configfile (\$root)/${recoveryboot_rel}/grub.cfg"

  [[ -f "$cfg" ]] || { printf 'missing embedded bootstrap\n'; return 1; }
  if ! grep -qF "$RECOVERIX_GRUB_STANDALONE_MARKER" "$cfg" 2>/dev/null; then
    printf 'standalone diagnostic marker missing\n'
    return 1
  fi
  if ! grep -qE 'search[[:space:]].*--fs-uuid[[:space:]].*--set=root' "$cfg" 2>/dev/null; then
    printf 'search --fs-uuid --set=root missing\n'
    return 1
  fi
  if ! grep -qF "$esp_uuid" "$cfg" 2>/dev/null; then
    printf 'ESP UUID missing from embedded bootstrap\n'
    return 1
  fi
  if ! grep -qF "$expected_configfile" "$cfg" 2>/dev/null; then
    printf 'configfile path mismatch (expected %s)\n' "$expected_configfile"
    return 1
  fi
  if grep -qF '/boot/grub/grub.cfg' "$cfg" 2>/dev/null; then
    printf '/boot/grub/grub.cfg reference present\n'
    return 1
  fi
  if grep -qi 'EFI/ubuntu' "$cfg" 2>/dev/null; then
    printf 'EFI/ubuntu reference present\n'
    return 1
  fi
  return 0
}

recoverix_grub_report_embedded_bootstrap_rendered() {
  local report="${1:?}"
  local embedded_cfg="${2:?}"
  local esp_uuid="${3:?}"
  local recoveryboot_rel="${4:?}"

  {
    echo "esp_uuid_detected: ${esp_uuid}"
    echo "embedded_root_search_cmd: search --no-floppy --fs-uuid --set=root ${esp_uuid}"
    echo "embedded_configfile_target: (${recoveryboot_rel}/grub.cfg on \$root)"
    echo "embedded_bootstrap_rendered: begin"
    if [[ -f "$embedded_cfg" ]]; then
      sed 's/^/  /' "$embedded_cfg"
    else
      echo "  (missing ${embedded_cfg})"
    fi
    echo "embedded_bootstrap_rendered: end"
  } >>"$report"
}

recoverix_grub_menuentry_count() {
  local cfg="${1:-}"
  local title="${2:-}"
  [[ -f "$cfg" && -n "$title" ]] || { printf '0'; return 0; }
  if grep -qF "menuentry \"${title}\"" "$cfg" 2>/dev/null; then
    grep -cF "menuentry \"${title}\"" "$cfg" 2>/dev/null
  else
    printf '0'
  fi
}

recoverix_grub_extract_menuentry_block() {
  local cfg="${1:?}" title="${2:?}"
  awk -v t="$title" '
    index($0, "menuentry \"" t "\"") == 1 { found = 1 }
    found { print }
    found && /^}/ { exit }
  ' "$cfg"
}

# Validate one xorg forensic A/B menuentry (shared cmdline except systemd.unit).
recoverix_grub_validate_xorg_forensic_one_menuentry() {
  local cfg="${1:?}" title="${2:?}" want_unit="${3:?}" block failures=0

  block="$(recoverix_grub_extract_menuentry_block "$cfg" "$title")"
  if [[ -z "$block" ]]; then
    printf '%s\n' "missing menuentry \"${title}\""
    return 1
  fi
  if ! grep -q 'recoverix.debug.xorg=1' <<<"$block"; then
    printf '%s\n' "${title}: missing recoverix.debug.xorg=1"
    failures=$((failures + 1))
  fi
  if ! grep -q 'recoverix.root=1' <<<"$block"; then
    printf '%s\n' "${title}: missing recoverix.root=1"
    failures=$((failures + 1))
  fi
  if ! grep -q 'recoverix.uuid=' <<<"$block"; then
    printf '%s\n' "${title}: missing recoverix.uuid="
    failures=$((failures + 1))
  fi
  if ! grep -qE 'root=tmpfs|root=UUID=' <<<"$block"; then
    printf '%s\n' "${title}: missing root=tmpfs or root=UUID="
    failures=$((failures + 1))
  fi
  if ! grep -qF "systemd.unit=${want_unit}" <<<"$block"; then
    printf '%s\n' "${title}: must set systemd.unit=${want_unit}"
    failures=$((failures + 1))
  fi
  if [[ "$want_unit" == "graphical.target" ]] && grep -q 'systemd.unit=multi-user.target' <<<"$block"; then
    printf '%s\n' "${title}: must not set systemd.unit=multi-user.target"
    failures=$((failures + 1))
  fi
  if [[ "$want_unit" == "multi-user.target" ]] && grep -q 'systemd.unit=graphical.target' <<<"$block"; then
    printf '%s\n' "${title}: must not set systemd.unit=graphical.target"
    failures=$((failures + 1))
  fi
  [[ $failures -eq 0 ]]
}

recoverix_grub_validate_core_one_menuentry() {
  local cfg="${1:?}" title="${2:?}" want_unit="${3:?}" block failures=0

  block="$(recoverix_grub_extract_menuentry_block "$cfg" "$title")"
  if [[ -z "$block" ]]; then
    printf '%s\n' "missing menuentry \"${title}\""
    return 1
  fi
  if ! grep -q 'recoverix.root=1' <<<"$block"; then
    printf '%s\n' "${title}: missing recoverix.root=1"
    failures=$((failures + 1))
  fi
  if ! grep -q 'recoverix.uuid=' <<<"$block"; then
    printf '%s\n' "${title}: missing recoverix.uuid="
    failures=$((failures + 1))
  fi
  if [[ "$title" == 'Recoverix Runtime' || "$title" == 'Recoverix Runtime (systemd debug)' ]]; then
    if ! grep -q 'root=tmpfs' <<<"$block"; then
      printf '%s\n' "${title}: missing root=tmpfs"
      failures=$((failures + 1))
    fi
  fi
  if [[ "$title" == 'Recoverix Runtime' ]] && ! grep -q 'recoverix.safe=1' <<<"$block"; then
    printf '%s\n' "${title}: missing recoverix.safe=1"
    failures=$((failures + 1))
  fi
  if [[ "$title" == 'Recoverix Runtime' ]] && ! grep -q 'recoverix.gui=1' <<<"$block"; then
    printf '%s\n' "${title}: missing recoverix.gui=1"
    failures=$((failures + 1))
  fi
  if [[ "$title" == 'Recoverix Runtime (systemd debug)' ]]; then
    if ! grep -q 'recoverix.safe=1' <<<"$block"; then
      printf '%s\n' "${title}: missing recoverix.safe=1"
      failures=$((failures + 1))
    fi
    if ! grep -q 'recoverix.debug.xorg=1' <<<"$block"; then
      printf '%s\n' "${title}: missing recoverix.debug.xorg=1"
      failures=$((failures + 1))
    fi
  fi
  if ! grep -qF "systemd.unit=${want_unit}" <<<"$block"; then
    printf '%s\n' "${title}: must set systemd.unit=${want_unit}"
    failures=$((failures + 1))
  fi
  if [[ "$want_unit" == "multi-user.target" ]] && grep -q 'systemd.unit=graphical.target' <<<"$block"; then
    printf '%s\n' "${title}: must not set systemd.unit=graphical.target"
    failures=$((failures + 1))
  fi
  [[ $failures -eq 0 ]]
}

# Minimal supported Recoverix menu set: stable GUI runtime + debug console entries.
recoverix_grub_validate_core_menuentries() {
  local cfg="${1:?}" failures=0

  recoverix_grub_validate_core_one_menuentry "$cfg" \
    'Recoverix Runtime' 'multi-user.target' || failures=$((failures + 1))
  recoverix_grub_validate_core_one_menuentry "$cfg" \
    'Recoverix Runtime (systemd debug)' 'multi-user.target' || failures=$((failures + 1))
  [[ $failures -eq 0 ]]
}

recoverix_grub_menuentry_pass_label() {
  case "${1:-}" in
    'Recoverix Runtime') printf '%s' 'grub.cfg menuentry "Recoverix Runtime"' ;;
    'Recoverix Runtime (systemd debug)') printf '%s' 'grub.cfg systemd debug menuentry' ;;
    'Windows Boot Manager') printf '%s' 'grub.cfg Windows Boot Manager menuentry' ;;
    *) printf 'active menuentry count=1 (%s)' "${1:-}" ;;
  esac
}

recoverix_grub_cfg_includes_host_ubuntu() {
  local cfg="${1:-}"
  [[ -f "$cfg" ]] || return 1
  if grep -qE '(^|[[:space:]])configfile[[:space:]]+.*/boot/grub/grub\.cfg' "$cfg" 2>/dev/null; then
    return 0
  fi
  if grep -qE '(^|[[:space:]])configfile[[:space:]]+.*EFI/ubuntu/grub\.cfg' "$cfg" 2>/dev/null; then
    return 0
  fi
  if grep -qF '/boot/grub/grub.cfg' "$cfg" 2>/dev/null; then
    return 0
  fi
  if grep -qE '(^|[[:space:]])source[[:space:]]+' "$cfg" 2>/dev/null; then
    return 0
  fi
  return 1
}

recoverix_grub_report_path() {
  local report="${1:?}"
  local label="${2:?}"
  local path="${3:-}"
  if [[ -f "$path" ]]; then
    {
      echo "${label}: ${path}"
      echo "${label}_size: $(stat -c '%s' "$path" 2>/dev/null || echo unknown)"
      echo "${label}_mtime: $(stat -c '%Y' "$path" 2>/dev/null || echo unknown)"
    } >>"$report"
  else
    echo "${label}: missing (${path})" >>"$report"
  fi
}

recoverix_grub_report_cfg_includes() {
  local report="${1:?}"
  local label="${2:?}"
  local cfg="${3:-}"
  [[ -f "$cfg" ]] || return 0
  {
    echo "${label}_configfile_lines:"
    grep -nE '(^|[[:space:]])configfile[[:space:]]+' "$cfg" 2>/dev/null || echo "  (none)"
    echo "${label}_source_lines:"
    grep -nE '(^|[[:space:]])source[[:space:]]+' "$cfg" 2>/dev/null || echo "  (none)"
    echo "${label}_prefix_lines:"
    grep -nE '(^|[[:space:]])prefix=' "$cfg" 2>/dev/null || echo "  (none)"
    echo "${label}_chainloader_lines:"
    grep -nE '(^|[[:space:]])chainloader[[:space:]]+' "$cfg" 2>/dev/null || echo "  (none)"
  } >>"$report"
}

recoverix_grub_report_boot_chain() {
  local esp_root="${1:?}"
  local report="${2:?}"
  local active_rel="${RECOVERIX_ESP_ACTIVE_REL_DIR:-EFI/RecoveryBoot}"
  local legacy_rel="${RECOVERIX_ESP_REL_DIR:-EFI/Recoverix}"
  local direct_rel="${RECOVERIX_ESP_DIRECT_REL_DIR:-EFI/RecoverixDirect}"
  local fallback_rel="${RECOVERIX_ESP_FALLBACK_REL_DIR:-EFI/Boot}"
  local ubuntu_rel="EFI/ubuntu"
  local host_grub="/boot/grub/grub.cfg"

  {
    echo "boot_chain_trace: begin"
    echo "boot_chain_active_rel_dir: ${active_rel}"
    echo "boot_chain_legacy_rel_dir: ${legacy_rel}"
    echo "boot_chain_direct_rel_dir: ${direct_rel}"
    echo "boot_chain_fallback_rel_dir: ${fallback_rel}"
    echo "embedded_config_path: ${RECOVERIX_GRUB_EMBEDDED_CONFIG_PATH}"
    echo "embedded_prefix: ${RECOVERIX_GRUB_EMBEDDED_PREFIX}"
    echo "external_menu_path: ${esp_root%/}/${active_rel}/grub.cfg"
    echo "direct_menu_path: ${esp_root%/}/${direct_rel}/grub.cfg"
  } >>"$report"

  recoverix_grub_report_path "$report" "active_shimx64_efi" "${esp_root%/}/${active_rel}/shimx64.efi"
  recoverix_grub_report_path "$report" "active_grubx64_efi" "${esp_root%/}/${active_rel}/grubx64.efi"
  recoverix_grub_report_path "$report" "active_grub_cfg" "${esp_root%/}/${active_rel}/grub.cfg"
  recoverix_grub_report_path "$report" "direct_shimx64_efi" "${esp_root%/}/${direct_rel}/shimx64.efi"
  recoverix_grub_report_path "$report" "direct_grubx64_efi" "${esp_root%/}/${direct_rel}/grubx64.efi"
  recoverix_grub_report_path "$report" "direct_grub_cfg" "${esp_root%/}/${direct_rel}/grub.cfg"
  recoverix_grub_report_path "$report" "fallback_bootx64_efi" "${esp_root%/}/${fallback_rel}/bootx64.efi"
  recoverix_grub_report_path "$report" "fallback_grubx64_efi" "${esp_root%/}/${fallback_rel}/grubx64.efi"
  recoverix_grub_report_path "$report" "legacy_grub_cfg" "${esp_root%/}/${legacy_rel}/grub.cfg"
  recoverix_grub_report_path "$report" "ubuntu_shimx64_efi" "${esp_root%/}/${ubuntu_rel}/shimx64.efi"
  recoverix_grub_report_path "$report" "ubuntu_grubx64_efi" "${esp_root%/}/${ubuntu_rel}/grubx64.efi"
  recoverix_grub_report_path "$report" "ubuntu_grub_cfg" "${esp_root%/}/${ubuntu_rel}/grub.cfg"
  recoverix_grub_report_path "$report" "host_ubuntu_grub_cfg" "$host_grub"

  recoverix_grub_report_cfg_includes "$report" "active_grub_cfg" "${esp_root%/}/${active_rel}/grub.cfg"
  recoverix_grub_report_cfg_includes "$report" "direct_grub_cfg" "${esp_root%/}/${direct_rel}/grub.cfg"
  recoverix_grub_report_cfg_includes "$report" "ubuntu_grub_cfg" "${esp_root%/}/${ubuntu_rel}/grub.cfg"
  recoverix_grub_report_cfg_includes "$report" "host_ubuntu_grub_cfg" "$host_grub"

  echo "boot_chain_trace: end" >>"$report"
}

recoverix_grub_trace_host_entry_sources() {
  local report="${1:?}"
  local found=0
  {
    echo "recoverix_host_entry_sources:"
    if [[ -f /etc/grub.d/41_recoverix ]]; then
      echo "  - /etc/grub.d/41_recoverix"
      found=1
    fi
    local f
    for f in /etc/grub.d/*recoverix* /etc/grub.d/*Recoverix*; do
      [[ -e "$f" ]] || continue
      echo "  - ${f}"
      found=1
    done
    if [[ $found -eq 0 ]]; then
      echo "  (none)"
    fi
  } >>"$report"
}

recoverix_grub_purge_host_entries() {
  local report="${1:-}"
  local purged=0
  local f

  for f in /etc/grub.d/*recoverix* /etc/grub.d/*Recoverix*; do
    [[ -e "$f" ]] || continue
    rm -f "$f"
    purged=1
    [[ -n "$report" ]] && echo "purged_host_grub_snippet: ${f}" >>"$report"
  done

  if [[ $purged -eq 1 ]]; then
    if command -v update-grub >/dev/null 2>&1; then
      update-grub >>"${report:-/dev/null}" 2>&1 || return 1
      [[ -n "$report" ]] && echo "host_grub_regenerated: /boot/grub/grub.cfg" >>"$report"
    else
      return 1
    fi
  else
    [[ -n "$report" ]] && echo "host_grub_snippet_purge: skipped (no recoverix snippets)" >>"$report"
  fi
  return 0
}

recoverix_grub_verify_standalone_efi() {
  local grub_efi="${1:?}"
  local report="${2:-}"
  local active_rel="${RECOVERIX_ESP_ACTIVE_REL_DIR:-EFI/RecoveryBoot}"

  [[ -f "$grub_efi" ]] || return 1
  command -v strings >/dev/null 2>&1 || return 2

  if ! strings "$grub_efi" | grep -qF "$RECOVERIX_GRUB_STANDALONE_MARKER"; then
    [[ -n "$report" ]] && echo "standalone_efi_verify: FAIL marker missing in EFI binary" >>"$report"
    return 3
  fi
  if strings "$grub_efi" | grep -qE 'configfile[[:space:]]+.*/boot/grub/grub\.cfg'; then
    [[ -n "$report" ]] && echo "standalone_efi_verify: FAIL chains to /boot/grub/grub.cfg" >>"$report"
    return 4
  fi
  if strings "$grub_efi" | grep -qF 'EFI/ubuntu/grub.cfg'; then
    [[ -n "$report" ]] && echo "standalone_efi_verify: FAIL chains to EFI/ubuntu/grub.cfg" >>"$report"
    return 5
  fi
  if ! strings "$grub_efi" | grep -qE 'search.*--fs-uuid.*--set=root'; then
    [[ -n "$report" ]] && echo "standalone_efi_verify: FAIL missing embedded ESP search --set=root" >>"$report"
    return 6
  fi
  if ! strings "$grub_efi" | grep -qF "configfile (\$root)/${active_rel}/grub.cfg"; then
    if ! strings "$grub_efi" | grep -qF "($root)/${active_rel}/grub.cfg"; then
      [[ -n "$report" ]] && echo "standalone_efi_verify: FAIL missing RecoveryBoot configfile target" >>"$report"
      return 7
    fi
  fi
  [[ -n "$report" ]] && echo "standalone_efi_verify: PASS" >>"$report"
  return 0
}

recoverix_grub_efi_identical() {
  local a="${1:?}"
  local b="${2:?}"
  [[ -f "$a" && -f "$b" ]] && cmp -s "$a" "$b"
}

# Diagnostic: command -v / which / fixed path / PATH (for sudo secure_path issues).
recoverix_grub_report_mkstandalone_detection() {
  local report="${1:-}"
  local cmd_v="" which_out="" cv_rc=0 which_rc=0

  [[ -n "$report" ]] || return 0

  cmd_v="$(command -v grub-mkstandalone 2>/dev/null)" || cv_rc=$?
  if command -v which >/dev/null 2>&1; then
    which_out="$(which grub-mkstandalone 2>/dev/null)" || which_rc=$?
  else
    which_out="(which not in PATH)"
    which_rc=127
  fi

  {
    echo "grub_mkstandalone_detection: begin"
    echo "current_PATH: ${PATH:-<empty>}"
    echo "command_v_grub_mkstandalone: ${cmd_v:-<not_found>} (rc=${cv_rc})"
    echo "which_grub_mkstandalone: ${which_out:-<not_found>} (rc=${which_rc})"
    if [[ -x /usr/bin/grub-mkstandalone ]]; then
      echo "usr_bin_grub_mkstandalone: executable"
    else
      echo "usr_bin_grub_mkstandalone: missing-or-not-executable"
    fi
    echo "grub_mkstandalone_detection: end"
  } >>"$report"
}

# Resolve grub-mkstandalone: A) command -v, B) /usr/bin, C) PATH + sbin fallbacks.
recoverix_grub_resolve_mkstandalone_bin() {
  local bin="" dir="" p="" cv=""

  if cv="$(command -v grub-mkstandalone 2>/dev/null)" && [[ -n "$cv" && -x "$cv" ]]; then
    printf '%s' "$cv"
    return 0
  fi
  if [[ -x /usr/bin/grub-mkstandalone ]]; then
    printf '%s' "/usr/bin/grub-mkstandalone"
    return 0
  fi
  if [[ -n "${PATH:-}" ]]; then
    IFS=':' read -r -a _rx_path_dirs <<<"${PATH}"
    for dir in "${_rx_path_dirs[@]}"; do
      [[ -n "$dir" ]] || continue
      p="${dir%/}/grub-mkstandalone"
      if [[ -x "$p" ]]; then
        printf '%s' "$p"
        return 0
      fi
    done
  fi
  for p in /usr/sbin/grub-mkstandalone /sbin/grub-mkstandalone /bin/grub-mkstandalone; do
    if [[ -x "$p" ]]; then
      printf '%s' "$p"
      return 0
    fi
  done
  return 1
}

recoverix_grub_resolve_mod_dir() {
  local d=""
  for d in \
    /usr/lib/grub/x86_64-efi \
    /usr/lib/grub-efi-amd64 \
    /usr/lib/grub2/x86_64-efi; do
    if [[ -d "$d" ]]; then
      printf '%s' "$d"
      return 0
    fi
  done
  return 1
}

recoverix_grub_build_standalone_efi() {
  local embedded_cfg="${1:?}"
  local grub_efi="${2:?}"
  local report="${3:-}"
  local mk_bin="" mod_dir="" detection_method="" cv_bin=""
  local -a mk_cmd

  [[ -f "$embedded_cfg" ]] || {
    [[ -n "$report" ]] && echo "standalone_build_failure: missing embedded bootstrap ${embedded_cfg}" >>"$report"
    return 1
  }

  recoverix_grub_report_mkstandalone_detection "$report"

  if ! mk_bin="$(recoverix_grub_resolve_mkstandalone_bin)"; then
    [[ -n "$report" ]] && {
      echo "detected_grub_mkstandalone: missing"
      echo "detected_path: missing"
      echo "standalone_build_failure: grub-mkstandalone binary not found"
    } >>"$report"
    return 2
  fi

  cv_bin="$(command -v grub-mkstandalone 2>/dev/null || true)"
  if [[ -n "$cv_bin" && "$cv_bin" == "$mk_bin" ]]; then
    detection_method="command -v"
  elif [[ "$mk_bin" == "/usr/bin/grub-mkstandalone" ]]; then
    detection_method="/usr/bin fixed path"
  else
    detection_method="PATH or alternate fixed path"
  fi

  if ! mod_dir="$(recoverix_grub_resolve_mod_dir)"; then
    [[ -n "$report" ]] && {
      echo "detected_grub_mkstandalone: ${mk_bin}"
      echo "detected_path: ${mk_bin}"
      echo "detected_grub_mod_dir: missing"
      echo "grub_mkstandalone_detection_method: ${detection_method}"
      echo "standalone_build_failure: grub module dir missing (install grub-efi-amd64-bin)"
    } >>"$report"
    return 3
  fi

  mkdir -p "$(dirname "$grub_efi")"

  mk_cmd=(
    "$mk_bin"
    --format=x86_64-efi
    --output="$grub_efi"
    -d "$mod_dir"
    --locales=
    --fonts=
    "${RECOVERIX_GRUB_EMBEDDED_CONFIG_PATH}=${embedded_cfg}"
  )
  if "$mk_bin" --help 2>&1 | grep -q -- '--compress=xz'; then
    mk_cmd+=(--compress=xz)
  fi
  if "$mk_bin" --help 2>&1 | grep -q -- 'disable-shim-lock'; then
    mk_cmd+=(--disable-shim-lock)
  fi

  if [[ -n "$report" ]]; then
    {
      echo "detected_grub_mkstandalone: ${mk_bin}"
      echo "detected_path: ${mk_bin}"
      echo "detected_grub_mod_dir: ${mod_dir}"
      echo "grub_mkstandalone_detection_method: ${detection_method}"
      echo "embedded_grub_cfg_source: ${embedded_cfg}"
      echo "embedded_config_path: ${RECOVERIX_GRUB_EMBEDDED_CONFIG_PATH}"
      echo "embedded_prefix: ${RECOVERIX_GRUB_EMBEDDED_PREFIX}"
      echo "external_active_grub_cfg_role: menu-only (EFI/RecoveryBoot/grub.cfg)"
      echo "standalone_build_cmdline: $(printf '%q ' "${mk_cmd[@]}")"
    } >>"$report"
  fi

  if "${mk_cmd[@]}" >>"${report:-/dev/null}" 2>&1; then
    [[ -n "$report" ]] && echo "grubx64_built: grub-mkstandalone (${grub_efi})" >>"$report"
    if recoverix_grub_verify_standalone_efi "$grub_efi" "$report"; then
      return 0
    fi
    [[ -n "$report" ]] && echo "standalone_build_failure: standalone EFI post-verify failed" >>"$report"
    return 5
  fi
  [[ -n "$report" ]] && echo "standalone_build_failure: grub-mkstandalone execution failed (see log above)" >>"$report"
  return 4
}

# Back-compat alias for verify scripts.
recoverix_grub_validate_standalone_cfg() {
  recoverix_grub_validate_external_menu_cfg "$@"
}
