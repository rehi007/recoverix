#!/usr/bin/env bash
# Installed as: /usr/local/sbin/recoverix-rootfs-debug
# Post-handoff rootfs/tty state diagnostics — append-only, read-only collection.
# Diagnostic only: never mutates state; every probe is timeout-guarded so this
# service can never stall the boot it is observing.
set -u

LOG="${RECOVERIX_ROOTFS_DEBUG_LOG:-/var/log/recoverix-rootfs-debug.log}"
ROOT_LOG="${RECOVERIX_ROOTFS_DEBUG_ROOT_LOG:-/recovery-rootfs-debug.log}"
# Persistent copy on the (FAT) ESP so the log survives a black-console boot and
# is readable from the host Ubuntu at /boot/efi/EFI/RecoveryBoot/<name>.
ESP_LOG_REL="${RECOVERIX_ROOTFS_DEBUG_ESP_REL:-EFI/RecoveryBoot/recoverix-rootfs-debug.log}"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"

rootfs_debug_ensure_log() {
  mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
  if ! touch "$LOG" 2>/dev/null; then
    LOG="/run/recoverix-rootfs-debug.log"
    mkdir -p /run 2>/dev/null || true
    touch "$LOG" 2>/dev/null || LOG="/tmp/recoverix-rootfs-debug.log"
  fi
}

section() {
  printf '\n===== %s =====\n' "$1" >>"$LOG" 2>&1 || true
}

# Run a probe with a hard timeout so a blocked syscall/D-Bus call cannot hang boot.
append_cmd() {
  local title="$1"
  shift
  section "$title"
  {
    printf 'cmd: %s\n' "$*"
    if command -v timeout >/dev/null 2>&1; then
      timeout 5 "$@" 2>&1 || printf '(exit %s)\n' "$?"
    else
      "$@" 2>&1 || printf '(exit %s)\n' "$?"
    fi
  } >>"$LOG" 2>&1 || true
}

# Append a shell snippet (pipes/globs allowed) under a titled section, guarded.
append_sh() {
  local title="$1" snippet="$2"
  section "$title"
  {
    printf 'cmd: %s\n' "$snippet"
    if command -v timeout >/dev/null 2>&1; then
      timeout 5 sh -c "$snippet" 2>&1 || printf '(exit %s)\n' "$?"
    else
      sh -c "$snippet" 2>&1 || printf '(exit %s)\n' "$?"
    fi
  } >>"$LOG" 2>&1 || true
}

# Copy the finished log onto the ESP (FAT). Only writes the single log file;
# never touches grub.cfg / EFI binaries / NVRAM. Read-only discovery + rw mount
# of just the ESP, single file copy, sync, unmount.
rootfs_debug_persist_to_esp() {
  local src="$1" dev mp tmpmp="/run/recoverix-esp-debug" dest

  # 1) Already-mounted ESP candidates.
  for mp in /boot/efi /boot/EFI /efi; do
    if [[ -d "${mp}/EFI/RecoveryBoot" ]]; then
      dest="${mp}/${ESP_LOG_REL}"
      if mkdir -p "$(dirname "$dest")" 2>/dev/null && cp -f "$src" "$dest" 2>/dev/null; then
        sync 2>/dev/null || true
        section "ESP persist"; printf 'esp_log_written: %s (already-mounted %s)\n' "$dest" "$mp" >>"$LOG" 2>&1 || true
        return 0
      fi
    fi
  done

  # 2) Discover ESP among vfat partitions and mount read-write transiently.
  mkdir -p "$tmpmp" 2>/dev/null || true
  local devlist=""
  if command -v blkid >/dev/null 2>&1; then
    devlist="$(timeout 5 blkid -o device -t TYPE=vfat 2>/dev/null)"
  fi
  if [[ -z "$devlist" ]] && command -v lsblk >/dev/null 2>&1; then
    devlist="$(timeout 5 lsblk -rno NAME,FSTYPE 2>/dev/null | awk '$2=="vfat"{print "/dev/"$1}')"
  fi
  if [[ -z "$devlist" ]]; then
    devlist="$(ls /dev/sd*[0-9] /dev/nvme*p[0-9]* /dev/mmcblk*p[0-9]* 2>/dev/null)"
  fi

  for dev in $devlist; do
    [[ -b "$dev" ]] || continue
    if timeout 5 mount -t vfat -o rw "$dev" "$tmpmp" 2>/dev/null; then
      if [[ -d "${tmpmp}/EFI/RecoveryBoot" ]]; then
        dest="${tmpmp}/${ESP_LOG_REL}"
        mkdir -p "$(dirname "$dest")" 2>/dev/null || true
        cp -f "$src" "$dest" 2>/dev/null && sync 2>/dev/null || true
        section "ESP persist"; printf 'esp_log_written: %s (mounted %s -> %s)\n' "${ESP_LOG_REL}" "$dev" "$tmpmp" >>"$LOG" 2>&1 || true
        umount "$tmpmp" 2>/dev/null || true
        return 0
      fi
      umount "$tmpmp" 2>/dev/null || true
    fi
  done

  section "ESP persist"; printf 'esp_log_written: FAILED (no writable ESP with EFI/RecoveryBoot found)\n' >>"$LOG" 2>&1 || true
  return 1
}

rootfs_debug_collect() {
  rootfs_debug_ensure_log

  section "recoverix-rootfs-debug ${TS}"
  {
    printf 'marker: post-handoff rootfs/tty diagnostics\n'
    printf 'timestamp: %s\n' "$TS"
    printf 'cmdline: %s\n' "$(tr '\0' ' ' </proc/cmdline 2>/dev/null || true)"
    printf 'log_path: %s\n' "$LOG"
    printf 'log_mirror_path: %s\n' "$ROOT_LOG"
  } >>"$LOG" 2>&1 || true

  section "startup settle"
  {
    printf 'info: sleeping 5s before collection so tty/tui logs can flush\n'
  } >>"$LOG" 2>&1 || true
  sleep 5

  # --- root filesystem structure ---
  append_cmd "mount" mount
  append_cmd "findmnt" findmnt
  append_cmd "cat /proc/mounts" cat /proc/mounts
  append_cmd "ls -la /" ls -la /
  append_cmd "ls -la /root" ls -la /root
  append_cmd "ls -la /run" ls -la /run
  append_cmd "ls -la /run/rootfs-root" ls -la /run/rootfs-root
  append_cmd "readlink -f /" readlink -f /
  append_cmd "readlink -f /sbin/init" readlink -f /sbin/init
  append_cmd "systemctl list-units --failed" systemctl list-units --failed --no-pager --no-legend

  # --- tty / console device state ---
  section "ls -la /dev/tty*"
  {
    printf 'cmd: ls -la /dev/tty*\n'
    ls -la /dev/tty* 2>&1 || printf '(exit %s)\n' "$?"
  } >>"$LOG" 2>&1 || true
  append_cmd "ls -la /dev/console" ls -la /dev/console
  append_cmd "ls -l /dev/tty1 /dev/tty2 /dev/console" ls -l /dev/tty1 /dev/tty2 /dev/console
  append_cmd "cat /proc/cmdline" cat /proc/cmdline
  append_cmd "systemctl status systemd-logind.service" \
    systemctl status systemd-logind.service --no-pager

  # --- getty / login chain (UnitRemoved investigation) ---
  append_cmd "systemctl status getty.target" \
    systemctl status getty.target --no-pager
  append_cmd "systemctl status getty@tty1.service" \
    systemctl status getty@tty1.service --no-pager
  append_cmd "systemctl status recoverix-runtime-tui.service" \
    systemctl status recoverix-runtime-tui.service --no-pager
  append_cmd "systemctl status recoverix-gui-launch.service" \
    systemctl status recoverix-gui-launch.service --no-pager
  append_cmd "journalctl -b -u getty@tty1.service" \
    journalctl -b -u getty@tty1.service --no-pager
  append_cmd "journalctl -b -u recoverix-runtime-tui.service" \
    journalctl -b -u recoverix-runtime-tui.service --no-pager
  append_cmd "journalctl -b -u recoverix-gui-launch.service" \
    journalctl -b -u recoverix-gui-launch.service --no-pager
  append_cmd "systemctl list-jobs" systemctl list-jobs --no-pager
  append_cmd "systemctl --failed" systemctl --failed --no-pager
  append_sh "ps aux | grep getty/agetty/login" \
    "ps aux 2>/dev/null | grep -E '[a]getty|[l]ogin|[g]etty' || echo '(no getty/agetty/login processes)'"

  # --- getty drop-in final merged content ---
  append_cmd "systemctl cat getty@tty1.service" \
    systemctl cat getty@tty1.service --no-pager
  append_sh "cat getty@.service.d drop-ins" \
    "for f in /etc/systemd/system/getty@.service.d/*.conf /etc/systemd/system/getty@tty1.service.d/*.conf; do [ -f \"\$f\" ] && { echo \"--- \$f ---\"; cat \"\$f\"; }; done; true"

  # --- getty@tty1 runtime exit/result state (UnitRemoved root cause) ---
  append_cmd "systemctl show getty@tty1.service (exit/result)" \
    systemctl show getty@tty1.service \
      -p ActiveState -p SubState -p Result -p ExecMainStatus -p ExecMainCode \
      -p ConditionResult -p AssertResult -p LoadState -p UnitFileState
  append_cmd "systemctl show recoverix-runtime-tui.service (exit/result)" \
    systemctl show recoverix-runtime-tui.service \
      -p ActiveState -p SubState -p Result -p ExecMainStatus -p ExecMainCode \
      -p ConditionResult -p AssertResult -p LoadState -p UnitFileState
  append_cmd "systemctl show recoverix-gui-launch.service (exit/result)" \
    systemctl show recoverix-gui-launch.service \
      -p ActiveState -p SubState -p Result -p ExecMainStatus -p ExecMainCode \
      -p ConditionResult -p AssertResult -p LoadState -p UnitFileState
  append_sh "journalctl -b | grep -i agetty" \
    "journalctl -b --no-pager 2>/dev/null | grep -i agetty || echo '(no agetty journal lines)'"
  append_sh "journalctl -b | grep -i tty1" \
    "journalctl -b --no-pager 2>/dev/null | grep -i tty1 || echo '(no tty1 journal lines)'"

  # --- explicit per-device tty/console nodes ---
  append_cmd "ls -l /dev/tty0" ls -l /dev/tty0
  append_cmd "ls -l /dev/tty1" ls -l /dev/tty1
  append_cmd "ls -l /dev/tty2" ls -l /dev/tty2
  append_cmd "ls -l /dev/console" ls -l /dev/console

  # --- agetty / login process table (ps -ef) ---
  append_sh "ps -ef | grep agetty" \
    "ps -ef 2>/dev/null | grep -E '[a]getty' || echo '(no agetty process)'"
  append_sh "ps -ef | grep login" \
    "ps -ef 2>/dev/null | grep -E '[l]ogin' || echo '(no login process)'"
  append_sh "ps -ef | grep recoverix-gui-launch" \
    "ps -ef 2>/dev/null | grep -E '[r]ecoverix-runtime-tui|[r]ecoverix-gui-launch|[s]tartx|[o]penbox-session|[r]ecoverix-recovery-ui' || echo '(no gui launch processes)'"
  if [[ -f /var/log/recovery-runtime.log ]]; then
    append_cmd "cat /var/log/recovery-runtime.log" cat /var/log/recovery-runtime.log
  elif [[ -f /tmp/recovery-runtime.log ]]; then
    append_cmd "cat /tmp/recovery-runtime.log" cat /tmp/recovery-runtime.log
  else
    section "cat recovery-runtime.log"
    printf 'cmd: cat /var/log/recovery-runtime.log || cat /tmp/recovery-runtime.log\n(missing)\n' >>"$LOG" 2>&1 || true
  fi
  if [[ -f /tmp/recoverix-runtime-bootstrap.log ]]; then
    append_cmd "cat /tmp/recoverix-runtime-bootstrap.log" cat /tmp/recoverix-runtime-bootstrap.log
  else
    section "cat /tmp/recoverix-runtime-bootstrap.log"
    printf 'cmd: cat /tmp/recoverix-runtime-bootstrap.log\n(missing)\n' >>"$LOG" 2>&1 || true
  fi
  if [[ -f /tmp/recovery-runtime-service.log ]]; then
    append_cmd "cat /tmp/recovery-runtime-service.log" cat /tmp/recovery-runtime-service.log
  else
    section "cat /tmp/recovery-runtime-service.log"
    printf 'cmd: cat /tmp/recovery-runtime-service.log\n(missing)\n' >>"$LOG" 2>&1 || true
  fi
  append_cmd "systemctl cat recoverix-runtime-tui.service" \
    systemctl cat recoverix-runtime-tui.service --no-pager
  append_cmd "systemctl cat recoverix-gui-launch.service" \
    systemctl cat recoverix-gui-launch.service --no-pager

  # ============================================================
  #  GUI path forensics (Xorg / GDM / seat / DRM / session)
  #  Collection only — read-only, timeout-guarded.
  # ============================================================
  section "=== GUI forensic collection (Xorg/GDM/seat/DRM/session) ==="

  # --- DISPLAY state ---
  append_sh "echo DISPLAY / XAUTHORITY" \
    'echo "DISPLAY=${DISPLAY:-}"; echo "XAUTHORITY=${XAUTHORITY:-}"'

  # --- Xorg / display-manager ---
  append_cmd "systemctl status display-manager.service" \
    systemctl status display-manager.service --no-pager
  append_cmd "systemctl status gdm.service" \
    systemctl status gdm.service --no-pager
  append_cmd "journalctl -b -u gdm.service" \
    journalctl -b -u gdm.service --no-pager
  append_sh "journalctl -b | grep -i xorg" \
    "journalctl -b --no-pager 2>/dev/null | grep -i xorg || echo '(no xorg journal lines)'"
  append_sh "journalctl -b | grep -i gdm" \
    "journalctl -b --no-pager 2>/dev/null | grep -i gdm || echo '(no gdm journal lines)'"

  # --- seat ---
  append_cmd "loginctl" loginctl --no-pager
  append_cmd "loginctl seat-status seat0" loginctl seat-status seat0 --no-pager

  # --- DRM ---
  append_cmd "ls -l /dev/dri" ls -l /dev/dri
  append_cmd "ls -l /sys/class/drm" ls -l /sys/class/drm

  # --- session ---
  append_cmd "loginctl list-sessions" loginctl list-sessions --no-pager
  append_cmd "loginctl session-status" loginctl session-status --no-pager

  # --- process table (Xorg/gdm/mutter) ---
  append_sh "ps -ef | grep Xorg" \
    "ps -ef 2>/dev/null | grep -E '[X]org' || echo '(no Xorg process)'"
  append_sh "ps -ef | grep gdm" \
    "ps -ef 2>/dev/null | grep -E '[g]dm' || echo '(no gdm process)'"
  append_sh "ps -ef | grep mutter" \
    "ps -ef 2>/dev/null | grep -E '[m]utter' || echo '(no mutter process)'"

  # --- failure snapshot (GUI context) ---
  append_cmd "systemctl --failed (gui)" systemctl --failed --no-pager
  append_cmd "systemctl list-jobs (gui)" systemctl list-jobs --no-pager

  # ============================================================
  #  GDM deep forensic (where does gdm.service stall?)
  # ============================================================
  section "===== GDM deep forensic ====="

  append_cmd "systemctl cat gdm.service" systemctl cat gdm.service --no-pager
  append_cmd "systemctl show gdm.service (exit/result)" \
    systemctl show gdm.service \
      -p ActiveState -p SubState -p Result -p ExecMainStatus -p ExecMainCode \
      -p ConditionResult -p AssertResult -p LoadState -p UnitFileState
  append_cmd "journalctl -b -u gdm.service -n 300" \
    journalctl -b -u gdm.service -n 300 --no-pager
  append_sh "journalctl -b | grep -i gdm" \
    "journalctl -b --no-pager 2>/dev/null | grep -i 'gdm' || echo '(no gdm journal lines)'"
  append_sh "journalctl -b | grep -i gdm-launch" \
    "journalctl -b --no-pager 2>/dev/null | grep -i 'gdm-launch' || echo '(no gdm-launch journal lines)'"
  append_sh "journalctl -b | grep -i gnome-shell" \
    "journalctl -b --no-pager 2>/dev/null | grep -i 'gnome-shell' || echo '(no gnome-shell journal lines)'"
  append_sh "journalctl -b | grep -i mutter" \
    "journalctl -b --no-pager 2>/dev/null | grep -i 'mutter' || echo '(no mutter journal lines)'"
  append_sh "journalctl -b | grep -i wayland" \
    "journalctl -b --no-pager 2>/dev/null | grep -i 'wayland' || echo '(no wayland journal lines)'"
  append_sh "journalctl -b | grep -i x11" \
    "journalctl -b --no-pager 2>/dev/null | grep -i 'x11' || echo '(no x11 journal lines)'"
  append_sh "journalctl -b | grep -i display" \
    "journalctl -b --no-pager 2>/dev/null | grep -i 'display' || echo '(no display journal lines)'"

  append_cmd "ls -la /etc/gdm3" ls -la /etc/gdm3
  append_cmd "cat /etc/gdm3/custom.conf" cat /etc/gdm3/custom.conf
  append_cmd "ls -la /usr/sbin/gdm3" ls -la /usr/sbin/gdm3
  append_sh "which gdm3" "which gdm3 || command -v gdm3 || echo '(gdm3 not in PATH)'"

  append_cmd "systemctl list-dependencies graphical.target" \
    systemctl list-dependencies graphical.target --no-pager
  append_cmd "systemctl list-dependencies display-manager.service" \
    systemctl list-dependencies display-manager.service --no-pager

  # ============================================================
  #  DRM seat deep forensic (greeter session create failure)
  # ============================================================
  section "===== DRM seat deep forensic ====="

  append_cmd "loginctl" loginctl --no-pager
  append_cmd "loginctl seat-status seat0" loginctl seat-status seat0 --no-pager
  append_cmd "loginctl list-sessions" loginctl list-sessions --no-pager
  append_cmd "loginctl list-users" loginctl list-users --no-pager
  append_cmd "systemctl status systemd-logind.service" \
    systemctl status systemd-logind.service --no-pager
  append_cmd "journalctl -b -u systemd-logind.service -n 300" \
    journalctl -b -u systemd-logind.service -n 300 --no-pager

  append_cmd "ls -la /dev/dri" ls -la /dev/dri
  append_cmd "ls -la /dev/dri/by-path" ls -la /dev/dri/by-path
  append_sh "find /sys/class/drm -maxdepth 2" \
    "find /sys/class/drm -maxdepth 2 2>&1 || echo '(no /sys/class/drm)'"
  append_sh "lspci -nnk | grep VGA/Display/3D" \
    "lspci -nnk 2>/dev/null | grep -A5 -E 'VGA|Display|3D' || echo '(lspci unavailable or no GPU match)'"
  append_sh "lsmod | grep gpu drivers" \
    "lsmod 2>/dev/null | grep -E 'amdgpu|radeon|i915|nouveau|nvidia' || echo '(no drm kernel module loaded)'"
  append_sh "dmesg | grep -i drm" \
    "dmesg 2>/dev/null | grep -i drm || echo '(no drm dmesg or dmesg restricted)'"
  append_sh "dmesg | grep -i amdgpu" \
    "dmesg 2>/dev/null | grep -i amdgpu || echo '(no amdgpu dmesg)'"
  append_sh "dmesg | grep -i fb" \
    "dmesg 2>/dev/null | grep -i fb || echo '(no fb dmesg)'"
  append_sh "dmesg | grep -i gpu" \
    "dmesg 2>/dev/null | grep -i gpu || echo '(no gpu dmesg)'"
  append_cmd "udevadm info -q all -n /dev/dri/card0" \
    udevadm info -q all -n /dev/dri/card0
  append_cmd "systemctl status display-manager.service (drm-seat)" \
    systemctl status display-manager.service --no-pager

  # ============================================================
  #  AMD GPU deep forensic (amdgpu driver / firmware load stage)
  # ============================================================
  section "===== AMD GPU deep forensic ====="

  append_cmd "lspci -nn" lspci -nn
  append_cmd "lspci -nnk" lspci -nnk
  append_sh "find /lib/firmware -iname '*amdgpu*'" \
    "find /lib/firmware -iname '*amdgpu*' 2>/dev/null || echo '(no /lib/firmware amdgpu match)'"
  append_sh "find /usr/lib/firmware -iname '*amdgpu*'" \
    "find /usr/lib/firmware -iname '*amdgpu*' 2>/dev/null || echo '(no /usr/lib/firmware amdgpu match)'"
  append_cmd "modinfo amdgpu" modinfo amdgpu
  append_sh "modprobe -c | grep amdgpu" \
    "modprobe -c 2>/dev/null | grep amdgpu || echo '(no amdgpu modprobe config)'"
  append_sh "find /lib/modules/\$(uname -r) -name 'amdgpu*.ko*'" \
    "find /lib/modules/\"\$(uname -r)\" -name 'amdgpu*.ko*' 2>/dev/null || echo '(no amdgpu module .ko found)'"
  append_sh "lsinitramfs initrd | grep amdgpu" \
    "lsinitramfs \"/boot/initrd.img-\$(uname -r)\" 2>/dev/null | grep amdgpu || echo '(no amdgpu in initramfs or lsinitramfs unavailable)'"
  append_cmd "cat /proc/modules" cat /proc/modules
  append_cmd "cat /proc/cmdline (amd-gpu)" cat /proc/cmdline
  append_sh "dmesg | grep -i firmware" \
    "dmesg 2>/dev/null | grep -i firmware || echo '(no firmware dmesg)'"
  append_sh "dmesg | grep -i amdgpu" \
    "dmesg 2>/dev/null | grep -i amdgpu || echo '(no amdgpu dmesg)'"
  append_sh "dmesg | grep -i drm (amd-gpu)" \
    "dmesg 2>/dev/null | grep -i drm || echo '(no drm dmesg)'"
  append_sh "dmesg | grep -i pci" \
    "dmesg 2>/dev/null | grep -i pci || echo '(no pci dmesg)'"
  append_sh "dmesg | grep -i vga" \
    "dmesg 2>/dev/null | grep -i vga || echo '(no vga dmesg)'"
  append_sh "dmesg | grep -i framebuffer" \
    "dmesg 2>/dev/null | grep -i framebuffer || echo '(no framebuffer dmesg)'"

  # Mirror the full diagnostics to a root-directory copy so it is trivially
  # recoverable even if /var is on a separate / hidden overlay layer.
  if [[ "$LOG" != "$ROOT_LOG" ]]; then
    cp -f "$LOG" "$ROOT_LOG" 2>/dev/null \
      || { mkdir -p "$(dirname "$ROOT_LOG")" 2>/dev/null; cp -f "$LOG" "$ROOT_LOG" 2>/dev/null; } \
      || true
  fi

  if [[ -f /run/recoverix-boot/boot/recoverix/recoverix-gui-launch.mark ]]; then
    append_cmd "cat /run/recoverix-boot/boot/recoverix/recoverix-gui-launch.mark" \
      cat /run/recoverix-boot/boot/recoverix/recoverix-gui-launch.mark
  else
    section "cat /run/recoverix-boot/boot/recoverix/recoverix-gui-launch.mark"
    printf 'cmd: cat /run/recoverix-boot/boot/recoverix/recoverix-gui-launch.mark\n(missing)\n' >>"$LOG" 2>&1 || true
  fi

  if [[ -f /tmp/recoverix-gui/recoverix-gui-session.log ]]; then
    append_cmd "cat /tmp/recoverix-gui/recoverix-gui-session.log" \
      cat /tmp/recoverix-gui/recoverix-gui-session.log
  else
    section "cat /tmp/recoverix-gui/recoverix-gui-session.log"
    printf 'cmd: cat /tmp/recoverix-gui/recoverix-gui-session.log\n(missing)\n' >>"$LOG" 2>&1 || true
  fi

  if [[ -f /home/recoverix/.xsession-errors ]]; then
    append_cmd "cat /home/recoverix/.xsession-errors" cat /home/recoverix/.xsession-errors
  else
    section "cat /home/recoverix/.xsession-errors"
    printf 'cmd: cat /home/recoverix/.xsession-errors\n(missing)\n' >>"$LOG" 2>&1 || true
  fi

  if [[ -f /tmp/recoverix-gui-service.log ]]; then
    append_cmd "cat /tmp/recoverix-gui-service.log" cat /tmp/recoverix-gui-service.log
  else
    section "cat /tmp/recoverix-gui-service.log"
    printf 'cmd: cat /tmp/recoverix-gui-service.log\n(missing)\n' >>"$LOG" 2>&1 || true
  fi

  if [[ -f /tmp/recoverix-gui/Xorg.0.log ]]; then
    append_cmd "cat /tmp/recoverix-gui/Xorg.0.log" cat /tmp/recoverix-gui/Xorg.0.log
  elif [[ -f /var/log/Xorg.0.log ]]; then
    append_cmd "cat /var/log/Xorg.0.log" cat /var/log/Xorg.0.log
  else
    section "cat /tmp/recoverix-gui/Xorg.0.log"
    printf 'cmd: cat /tmp/recoverix-gui/Xorg.0.log\n(missing)\n' >>"$LOG" 2>&1 || true
  fi

  # Persist to ESP so the log is recoverable from host Ubuntu when the console
  # is unusable. Best-effort, never fails the unit.
  rootfs_debug_persist_to_esp "$LOG" || true

  printf '[recoverix-rootfs-debug] diagnostics appended to %s (mirror: %s, esp: %s)\n' \
    "$LOG" "$ROOT_LOG" "$ESP_LOG_REL" >&2
}

rootfs_debug_collect
exit 0
