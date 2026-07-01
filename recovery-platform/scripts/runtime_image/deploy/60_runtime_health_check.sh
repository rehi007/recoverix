#!/usr/bin/env bash
# Recoverix Runtime health check — run inside a booted Recoverix Runtime session.
# Installed as: /usr/local/sbin/recoverix-health-check
# Usage: recoverix-health-check
#        sudo recoverix-health-check   # full overlay writable validation (recommended)
#
# Read-only checks only. Does not modify overlay, initramfs, EFI, or GRUB.

set -u

PASS=0
WARN=0
FAIL=0

ok() { printf '[PASS] %s\n' "$*"; PASS=$((PASS + 1)); }
warn() { printf '[WARN] %s\n' "$*"; WARN=$((WARN + 1)); }
fail() { printf '[FAIL] %s\n' "$*"; FAIL=$((FAIL + 1)); }

log_section() {
  printf '\n=== %s ===\n' "$*"
}

# --- 1) overlayfs on / ---
check_overlay_root() {
  local fstype options

  fstype="$(findmnt -no FSTYPE / 2>/dev/null || true)"
  if [[ "$fstype" == "overlay" ]]; then
    ok "root (/) filesystem type is overlay"
  else
    fail "root (/) is not overlay (fstype=${fstype:-unknown})"
    return
  fi

  if mount | grep -qE ' on / type overlay '; then
    ok "mount table shows overlay on /"
  else
    warn "overlay on / not matched in mount(8) output (findmnt may still be OK)"
  fi

  options="$(findmnt -no OPTIONS / 2>/dev/null || true)"
  if [[ -n "$options" ]]; then
    ok "overlay mount options present"
    printf '       options: %s\n' "$options"
  else
    warn "could not read overlay mount options for /"
  fi
}

# --- 2) squashfs ro layer ---
check_squashfs_ro() {
  local line fstype options

  line="$(findmnt -rn -t squashfs 2>/dev/null | head -1 || true)"
  if [[ -z "$line" ]]; then
    line="$(mount -t squashfs 2>/dev/null | head -1 || true)"
  fi

  if [[ -z "$line" ]]; then
    fail "no squashfs mount found (runtime lower layer missing)"
    return
  fi

  ok "squashfs mount present: ${line}"

  fstype="$(echo "$line" | awk '{print $3}')"
  options="$(echo "$line" | awk '{print $4}')"
  if [[ "$fstype" == "squashfs" ]]; then
    ok "squashfs filesystem type confirmed"
  else
    warn "unexpected squashfs line format: ${line}"
  fi

  if [[ "$options" == *ro* ]]; then
    ok "squashfs mounted read-only"
  else
    fail "squashfs mount is not read-only (options=${options})"
  fi
}

# --- 3) overlay upper/work writable ---
# Writable probes require root: upperdir is typically root-owned (mode 0700).
# Non-root runs must not FAIL the whole check when overlay is otherwise healthy.
check_overlay_writable() {
  local probe="/run/recoverix/.runtime-health-write-test"
  local upper_base="/run/recoverix-overlay/upper"
  local work_base="/run/recoverix-overlay/work"

  mkdir -p /run/recoverix 2>/dev/null || true

  if [[ "$(id -u)" -ne 0 ]]; then
    warn "overlay writable validation requires root privileges (skipped write probes)"
    warn "re-run with: sudo recoverix-health-check"
    if [[ -d "$upper_base" ]]; then
      ok "overlay upperdir path present (${upper_base})"
    else
      warn "overlay upperdir path not found (${upper_base})"
    fi
    if [[ -d "$work_base" ]]; then
      ok "overlay workdir path present (${work_base})"
    else
      warn "overlay workdir path not found (${work_base})"
    fi
    return
  fi

  if touch "$probe" 2>/dev/null; then
    ok "merged overlay root is writable (touch ${probe})"
    rm -f "$probe" 2>/dev/null || true
  else
    fail "cannot write to overlay merged root (${probe})"
  fi

  if [[ -d "$upper_base" ]]; then
    if touch "${upper_base}/.recoverix-write-test" 2>/dev/null; then
      ok "overlay upperdir writable (${upper_base})"
      rm -f "${upper_base}/.recoverix-write-test" 2>/dev/null || true
    else
      fail "overlay upperdir not writable (${upper_base})"
    fi
  else
    warn "overlay upperdir path not found (${upper_base})"
  fi

  if [[ -d "$work_base" ]]; then
    ok "overlay workdir exists (${work_base})"
  else
    warn "overlay workdir path not found (${work_base})"
  fi
}

# --- 4) systemd PID1 ---
check_systemd_pid1() {
  local comm args

  comm="$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ' || true)"
  args="$(tr '\0' ' ' </proc/1/cmdline 2>/dev/null || true)"

  if [[ "$comm" == "systemd" ]]; then
    ok "PID1 comm is systemd"
  elif [[ "$comm" == "init" || "$comm" == "(init)" ]] && [[ "$args" == *systemd* ]]; then
    ok "PID1 is init stub launching systemd"
  else
    fail "PID1 is not systemd (comm=${comm:-unknown} cmdline=${args:-unknown})"
    return
  fi

  if systemctl is-system-running >/dev/null 2>&1; then
    ok "systemd reports: $(systemctl is-system-running 2>/dev/null)"
  else
    fail "systemctl is-system-running failed"
  fi
}

# --- 5) GTK ---
check_gtk() {
  if python3 -c \
    'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk; Gtk.init_check(None)' \
    2>/dev/null; then
    ok "GTK 3 (python3-gi) import successful"
  else
    fail "GTK import failed (python3-gi / Gtk 3)"
  fi
}

# --- 6) partclone ---
check_partclone() {
  if command -v partclone.ntfs >/dev/null 2>&1 && partclone.ntfs -V >/dev/null 2>&1; then
    ok "partclone.ntfs available: $(partclone.ntfs -V 2>&1 | head -1)"
    return
  fi
  if command -v partclone >/dev/null 2>&1 && partclone -V >/dev/null 2>&1; then
    ok "partclone available: $(partclone -V 2>&1 | head -1)"
    return
  fi
  fail "partclone / partclone.ntfs not available"
}

# --- 7) fstab: no host UUID block mounts ---
check_fstab_no_host_block() {
  local fstab="/etc/fstab"
  local line

  if [[ ! -f "$fstab" ]]; then
    fail "missing ${fstab}"
    return
  fi

  if grep -q 'Recoverix Recovery Runtime' "$fstab" 2>/dev/null; then
    ok "fstab is Recoverix minimal template"
  else
    warn "fstab missing Recoverix template marker"
  fi

  while IFS= read -r line; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    if grep -qE '^[[:space:]]*UUID=' <<<"$line"; then
      fail "fstab contains host UUID mount: ${line}"
    fi
    if grep -qE '[[:space:]]ext4[[:space:]]|[[:space:]]vfat[[:space:]]|[[:space:]]xfs[[:space:]]' <<<"$line"; then
      fail "fstab contains block filesystem mount: ${line}"
    fi
    if grep -qE '[[:space:]][12][[:space:]]*$' <<<"$line"; then
      warn "fstab entry has fsck pass 1/2: ${line}"
    fi
  done <"$fstab"

  if ! grep -v '^#' "$fstab" | grep -v '^[[:space:]]*$' | grep -qE 'UUID=|ext4|vfat'; then
    ok "fstab has no host UUID/ext4/vfat block entries"
  fi
}

# --- 8) no swapfile / swap activation in fstab ---
check_fstab_no_swap() {
  local fstab="/etc/fstab"
  local line bad=0

  if [[ ! -f "$fstab" ]]; then
    return
  fi

  while IFS= read -r line; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    if grep -q '/swapfile' <<<"$line"; then
      fail "fstab references /swapfile: ${line}"
      bad=1
    fi
    if grep -qE '[[:space:]]swap[[:space:]]' <<<"$line"; then
      fail "fstab contains swap entry: ${line}"
      bad=1
    fi
  done <"$fstab"

  if [[ $bad -eq 0 ]]; then
    ok "fstab has no swapfile or swap activation entries"
  fi

  if swapon --show 2>/dev/null | grep -q .; then
    warn "swap devices active: $(swapon --show 2>/dev/null | tail -n +2 | tr '\n' '; ')"
  else
    ok "no active swap devices (swapon --show empty)"
  fi
}

# --- 9) Recoverix boot log ---
check_boot_log() {
  local -a candidates=(
    /run/recoverix/boot.log
    /var/log/recoverix/boot.log
  )
  local found=""

  for f in "${candidates[@]}"; do
    if [[ -f "$f" ]]; then
      found="$f"
      break
    fi
  done

  if [[ -n "$found" ]]; then
    ok "Recoverix boot log exists (${found})"
    if grep -qE '\[INFO\]|\[ERROR\]|Recoverix' "$found" 2>/dev/null; then
      ok "boot log contains Recoverix structured entries"
    else
      warn "boot log exists but lacks expected Recoverix log markers"
    fi
  else
    warn "Recoverix boot log not found (${candidates[*]})"
  fi
}

# --- 11) AMDGPU firmware ---
check_amdgpu_firmware() {
  local min_files="${RUNTIME_MIN_AMDGPU_FIRMWARE_FILES:-50}"
  local min_bin="${RUNTIME_MIN_AMDGPU_FIRMWARE_BIN:-10}"
  local fw_dir count bin_count candidates

  candidates=(
    /usr/lib/firmware/amdgpu
    /lib/firmware/amdgpu
  )

  if [[ -d /usr/lib/firmware ]]; then
    ok "firmware tree exists (/usr/lib/firmware)"
  elif [[ -d /lib/firmware ]]; then
    ok "firmware tree exists (/lib/firmware)"
  else
    fail "no firmware directory (/usr/lib/firmware or /lib/firmware)"
    return
  fi

  fw_dir=""
  for d in "${candidates[@]}"; do
    if [[ -d "$d" ]]; then
      fw_dir="$d"
      break
    fi
  done

  if [[ -z "$fw_dir" ]]; then
    fail "amdgpu firmware directory missing (${candidates[*]})"
    return
  fi

  count="$(find "$fw_dir" -type f 2>/dev/null | wc -l | tr -d ' ')"
  bin_count="$(find "$fw_dir" -type f -name '*.bin' 2>/dev/null | wc -l | tr -d ' ')"
  printf '       amdgpu_dir=%s file_count=%s (min=%s) bin_count=%s (min=%s)\n' \
    "$fw_dir" "$count" "$min_files" "$bin_count" "$min_bin"

  if [[ "$count" -ge "$min_files" ]]; then
    ok "amdgpu firmware present (${count} files)"
  else
    fail "amdgpu firmware incomplete (${count} files, need >=${min_files})"
  fi

  if [[ "$bin_count" -ge "$min_bin" ]]; then
    ok "amdgpu .bin firmware payloads (${bin_count} .bin)"
  else
    fail "amdgpu .bin firmware insufficient (${bin_count} .bin, need >=${min_bin})"
  fi
}

# --- 10) overlay lowerdir / upperdir / workdir ---
check_overlay_dirs() {
  local options lower upper work

  options="$(findmnt -no OPTIONS / 2>/dev/null || true)"
  if [[ -z "$options" ]]; then
    fail "cannot read overlay options for lowerdir/upperdir/workdir validation"
    return
  fi

  lower="$(sed -n 's/.*lowerdir=\([^,]*\).*/\1/p' <<<"$options")"
  upper="$(sed -n 's/.*upperdir=\([^,]*\).*/\1/p' <<<"$options")"
  work="$(sed -n 's/.*workdir=\([^,]*\).*/\1/p' <<<"$options")"

  if [[ -n "$lower" && -d "$lower" ]]; then
    ok "overlay lowerdir exists (${lower})"
    if findmnt -no FSTYPE "$lower" 2>/dev/null | grep -qx squashfs; then
      ok "overlay lowerdir is squashfs"
    else
      warn "overlay lowerdir is not squashfs ($(findmnt -no FSTYPE "$lower" 2>/dev/null || echo unknown))"
    fi
  else
    fail "overlay lowerdir missing or not a directory (${lower:-unset})"
  fi

  if [[ -n "$upper" && -d "$upper" ]]; then
    ok "overlay upperdir exists (${upper})"
  else
    fail "overlay upperdir missing or not a directory (${upper:-unset})"
  fi

  if [[ -n "$work" && -d "$work" ]]; then
    ok "overlay workdir exists (${work})"
  else
    fail "overlay workdir missing or not a directory (${work:-unset})"
  fi

  printf '       lowerdir=%s\n' "${lower:-?}"
  printf '       upperdir=%s\n' "${upper:-?}"
  printf '       workdir=%s\n' "${work:-?}"
}

# --- 12) xorg forensic GUI stack (recoverix.debug.xorg=1) ---
check_xorg_forensic_gui_stack() {
  local gfx_state gdm_state gdm3_state dm_state log_file

  if ! grep -qw 'recoverix.debug.xorg=1' /proc/cmdline 2>/dev/null; then
    ok "non-xorg-forensic cmdline — GUI stack check skipped"
    return
  fi

  log_file="/var/log/recoverix-xorg.log"
  gfx_state="$(systemctl is-active graphical.target 2>/dev/null || echo inactive)"
  gdm_state="$(systemctl is-active gdm.service 2>/dev/null || echo inactive)"
  gdm3_state="$(systemctl is-active gdm3.service 2>/dev/null || echo inactive)"
  dm_state="$(systemctl is-active display-manager.service 2>/dev/null || echo inactive)"

  printf 'systemctl_get_default: %s\n' "$(systemctl get-default 2>/dev/null || echo unknown)"
  printf 'default_target_path: %s\n' "$(readlink -f /etc/systemd/system/default.target 2>/dev/null || echo unknown)"
  printf 'graphical.target_is_active: %s\n' "$gfx_state"
  printf 'multi-user.target_is_active: %s\n' "$(systemctl is-active multi-user.target 2>/dev/null || echo unknown)"
  printf 'gdm.service_is_active: %s\n' "$gdm_state"
  printf 'gdm3.service_is_active: %s\n' "$gdm3_state"
  printf 'display-manager.service_is_active: %s\n' "$dm_state"

  if [[ "$gfx_state" == "active" ]]; then
    ok "graphical.target active"
    printf 'graphical_target: PASS\n'
  else
    fail "graphical.target inactive (state=${gfx_state})"
    printf 'graphical_target: FAIL (state=%s)\n' "$gfx_state"
  fi

  if [[ "$gdm_state" == "active" || "$gdm3_state" == "active" || "$dm_state" == "active" ]]; then
    ok "display manager running (gdm=${gdm_state} gdm3=${gdm3_state} display-manager=${dm_state})"
    printf 'gdm_running: PASS\n'
  else
    fail "gdm start failure (gdm.service=${gdm_state} gdm3.service=${gdm3_state} display-manager=${dm_state})"
    printf 'gdm_running: FAIL\n'
    systemctl status gdm.service --no-pager 2>&1 | sed 's/^/  /' || true
    systemctl status gdm3.service --no-pager 2>&1 | sed 's/^/  /' || true
    systemctl status display-manager.service --no-pager 2>&1 | sed 's/^/  /' || true
  fi

  if [[ -f "$log_file" ]]; then
    ok "xorg forensic log present (${log_file})"
    printf 'recoverix_xorg_log_tail:\n'
    tail -n 30 "$log_file" 2>/dev/null | sed 's/^/  /' || true
  else
    fail "xorg forensic log missing (${log_file}) — collectors did not run"
  fi
}

# --- 13) forensic limited sudo (recoverix.root / recoverix.debug.xorg) ---
check_forensic_sudo_installed() {
  if [[ -f /etc/systemd/system/recoverix-forensic-sudo.service ]]; then
    ok "forensic sudo service installed"
  else
    fail "forensic sudo service missing (/etc/systemd/system/recoverix-forensic-sudo.service)"
  fi

  if [[ -x /usr/local/sbin/recoverix-forensic-sudo-enable ]]; then
    ok "forensic sudo helper present"
  else
    fail "forensic sudo helper missing (/usr/local/sbin/recoverix-forensic-sudo-enable)"
  fi

  if [[ -f /etc/sudoers.d/99-recoverix-forensic ]]; then
    ok "forensic sudoers policy file present"
  else
    fail "forensic sudoers missing (/etc/sudoers.d/99-recoverix-forensic)"
  fi

  if systemctl is-enabled recoverix-forensic-sudo.service >/dev/null 2>&1; then
    ok "forensic sudo service enabled (systemctl is-enabled)"
  elif [[ -L /etc/systemd/system/sysinit.target.wants/recoverix-forensic-sudo.service || \
          -L /etc/systemd/system/multi-user.target.wants/recoverix-forensic-sudo.service ]]; then
    ok "forensic sudo service enabled (target.wants symlink)"
  else
    fail "forensic sudo service not enabled"
  fi
}

check_forensic_sudo() {
  local lint

  check_forensic_sudo_installed

  if ! grep -qE '(^| )recoverix\.root=1|recoverix\.debug\.xorg=1' /proc/cmdline 2>/dev/null; then
    ok "non-forensic cmdline — forensic sudo policy not required"
    if [[ -f /etc/sudoers.d/99-recoverix-forensic ]]; then
      warn "forensic sudoers present on non-forensic boot (unexpected)"
    fi
    return
  fi

  printf 'forensic_sudo_policy: recoverix.root=1 or recoverix.debug.xorg=1\n'

  if [[ -f /etc/recoverix/staging/sudoers-recoverix-forensic ]]; then
    ok "forensic sudo staging present"
    if visudo -cf /etc/recoverix/staging/sudoers-recoverix-forensic >/dev/null 2>&1; then
      ok "sudoers_validation: PASS (staging)"
    else
      fail "sudoers_validation: FAIL (staging)"
    fi
  else
    warn "forensic sudo staging missing"
  fi

  if command -v systemctl >/dev/null 2>&1; then
    if systemctl status recoverix-forensic-sudo.service --no-pager >/dev/null 2>&1; then
      ok "forensic sudo service known to systemd (systemctl status)"
    else
      fail "forensic sudo service not found by systemd (stale runtime image?)"
    fi
  fi

  if [[ -f /etc/sudoers.d/99-recoverix-forensic ]]; then
    ok "forensic sudo active policy present (/etc/sudoers.d/99-recoverix-forensic)"
    if visudo -cf /etc/sudoers.d/99-recoverix-forensic >/dev/null 2>&1; then
      ok "sudoers_validation: PASS (active)"
    else
      fail "sudoers_validation: FAIL (active)"
    fi
    printf 'sudo_allowed_commands:\n'
    grep -E '^Cmnd_Alias RECOVERIX_FORENSIC_|^recoverix ALL=' /etc/sudoers.d/99-recoverix-forensic 2>/dev/null \
      | sed 's/^/  /' || true
  else
    fail "forensic mode but sudo policy not active"
  fi

  if id recoverix >/dev/null 2>&1; then
    local sudo_true_rc=0 sudo_jctl_rc=0 sudo_true_cmd
    if [[ -x /usr/bin/true ]]; then
      sudo_true_cmd='sudo -n /usr/bin/true'
    elif [[ -x /bin/true ]]; then
      sudo_true_cmd='sudo -n /bin/true'
    else
      sudo_true_cmd='sudo -n /usr/bin/journalctl --version'
    fi
    if command -v runuser >/dev/null 2>&1; then
      printf 'forensic_sudo_runtime_test_cmd: runuser -u recoverix -- %s\n' "$sudo_true_cmd"
      runuser -u recoverix -- /bin/sh -c "$sudo_true_cmd" >/dev/null 2>&1 || sudo_true_rc=$?
    else
      printf 'forensic_sudo_runtime_test_cmd: su - recoverix -c %s\n' "$sudo_true_cmd"
      su - recoverix -c "$sudo_true_cmd" >/dev/null 2>&1 || sudo_true_rc=$?
    fi
    printf 'forensic_sudo_runtime_test_rc: %s\n' "$sudo_true_rc"
    if [[ $sudo_true_rc -eq 0 ]]; then
      ok "forensic sudo runtime execution test (${sudo_true_cmd})"
      printf 'forensic_sudo_runtime_test: PASS\n'
    else
      fail "forensic sudo runtime execution test (${sudo_true_cmd} failed)"
      printf 'forensic_sudo_runtime_test: FAIL\n'
    fi
    if command -v runuser >/dev/null 2>&1; then
      printf 'forensic_sudo_allowed_cmd_test_cmd: runuser -u recoverix -- sudo -n /usr/bin/journalctl --version\n'
      runuser -u recoverix -- /bin/sh -c 'sudo -n /usr/bin/journalctl --version' >/dev/null 2>&1 || sudo_jctl_rc=$?
    else
      printf 'forensic_sudo_allowed_cmd_test_cmd: su - recoverix -c sudo -n /usr/bin/journalctl --version\n'
      su - recoverix -c 'sudo -n /usr/bin/journalctl --version' >/dev/null 2>&1 || sudo_jctl_rc=$?
    fi
    printf 'forensic_sudo_allowed_cmd_test_rc: %s\n' "$sudo_jctl_rc"
    if [[ $sudo_jctl_rc -eq 0 ]]; then
      ok "forensic sudo allowed command test (journalctl --version)"
      printf 'forensic_sudo_allowed_cmd_test: PASS\n'
    else
      fail "forensic sudo allowed command test (journalctl --version)"
      printf 'forensic_sudo_allowed_cmd_test: FAIL\n'
    fi
  else
    warn "recoverix user missing — skipped forensic sudo runtime tests"
  fi

  if command -v systemctl >/dev/null 2>&1; then
    printf 'systemctl_enabled: %s\n' "$(systemctl is-enabled recoverix-forensic-sudo.service 2>/dev/null || echo unknown)"
    systemctl show recoverix-forensic-sudo.service -p ActiveState,SubState,Result 2>/dev/null \
      | sed 's/^/systemctl_boot: /' || true
  fi
  if [[ -f /var/log/recoverix-forensic-sudo.log ]]; then
    ok "forensic sudo activation log present"
    printf 'forensic_sudo_log_tail:\n'
    tail -n 20 /var/log/recoverix-forensic-sudo.log 2>/dev/null | sed 's/^/  /' || true
  else
    warn "forensic sudo activation log missing (/var/log/recoverix-forensic-sudo.log)"
  fi
  if [[ -f /run/recoverix/forensic-sudo-enabled ]]; then
    ok "forensic sudo boot marker present"
  elif grep -qE '(^| )recoverix\.root=1|recoverix\.debug\.xorg=1' /proc/cmdline 2>/dev/null; then
    fail "forensic boot but activation marker missing"
  fi
}

# --- main ---
log_section "Recoverix Runtime health check"
printf 'timestamp: %s\n' "$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || date)"
printf 'hostname: %s\n' "$(hostname 2>/dev/null || echo unknown)"
printf 'euid: %s (uid=%s)\n' "$(id -u)" "$(id -un 2>/dev/null || echo unknown)"
printf 'cmdline: %s\n' "$(tr '\0' ' ' </proc/cmdline 2>/dev/null || true)"

if grep -qE 'recoverix\.root=1|recoverix\.root=yes' /proc/cmdline 2>/dev/null; then
  ok "cmdline indicates Recoverix runtime (recoverix.root)"
else
  warn "recoverix.root not on cmdline — results may reflect host Ubuntu, not Recoverix"
fi

log_section "1) overlay on /"
check_overlay_root

log_section "2) squashfs read-only lower layer"
check_squashfs_ro

log_section "3) overlay upper/work writable"
check_overlay_writable

log_section "4) systemd PID1"
check_systemd_pid1

log_section "5) GTK environment"
check_gtk

log_section "6) partclone"
check_partclone

log_section "7) fstab — no host block mounts"
check_fstab_no_host_block

log_section "8) fstab — no swap activation"
check_fstab_no_swap

log_section "9) Recoverix boot log"
check_boot_log

log_section "10) overlay lowerdir/upperdir/workdir"
check_overlay_dirs

log_section "11) AMDGPU firmware"
check_amdgpu_firmware

log_section "12) xorg forensic GUI stack"
check_xorg_forensic_gui_stack

log_section "13) forensic limited sudo"
check_forensic_sudo

log_section "summary"
printf 'PASS=%d WARN=%d FAIL=%d\n' "$PASS" "$WARN" "$FAIL"

if [[ $FAIL -gt 0 ]]; then
  printf '[FAIL] critical checks failed — exit 1\n'
  exit 1
fi

printf '[PASS] runtime health check complete — exit 0\n'
exit 0
