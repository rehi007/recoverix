#!/usr/bin/env bash
# Host safety lock — refuse operations that could touch the running host.

validate_host_safety_lock() {
  local rootfs="$1"
  local report="${2:-}"

  _safety_fail() {
    local msg="$1"
    if [[ -n "$report" ]]; then
      printf 'FAIL\thost_safety\t%s\n' "$msg" >> "$report"
    fi
    die "HOST SAFETY LOCK: $msg"
  }

  if [[ -z "$rootfs" ]]; then
    _safety_fail "ROOTFS is empty"
  fi

  case "$rootfs" in
    "/" | "" )
      _safety_fail "ROOTFS must not be / or empty"
      ;;
  esac

  local host_root
  host_root="$(readlink -f / 2>/dev/null || echo /)"
  if [[ "$rootfs" == "$host_root" ]]; then
    _safety_fail "ROOTFS resolves to host root ($host_root)"
  fi

  # ROOTFS must not be the mountpoint of host / (e.g. ROOTFS=/ recovery/build/rootfs is OK)
  local mnt_target
  mnt_target="$(findmnt -n -o TARGET -- "$rootfs" 2>/dev/null | head -1 || true)"
  if [[ "$mnt_target" == "/" ]]; then
    _safety_fail "ROOTFS path is mount target of host /"
  fi

  if [[ -f "$rootfs/etc/os-release" ]]; then
    local rootfs_name
    rootfs_name="$(grep ^PRETTY_NAME= "$rootfs/etc/os-release" 2>/dev/null | head -1 || true)"
    if [[ -z "$rootfs_name" ]]; then
      _safety_fail "invalid rootfs (unreadable os-release)"
    fi
  else
    _safety_fail "missing $rootfs/etc/os-release — not an isolated rootfs"
  fi

  # Detect host paths bind-mounted INTO rootfs (dangerous misconfiguration)
  if command -v findmnt >/dev/null 2>&1; then
    while IFS= read -r line; do
      [[ -z "$line" ]] && continue
      local src="${line%% *}"
      local tgt="${line#* }"
      # Skip virtual fs
      case "$src" in
        proc|sysfs|dev|devpts|tmpfs|efivarfs|none|udev) continue ;;
      esac
      # Source outside rootfs tree but mounted under rootfs → host leak
      if [[ "$src" != "$rootfs"* ]] && [[ "$tgt" == "$rootfs"* ]]; then
        if [[ "$src" == /* ]] && [[ ! "$src" == "$rootfs"* ]]; then
          _safety_fail "host bind mount detected: $src -> $tgt"
        fi
      fi
    done < <(findmnt -rn -o SOURCE,TARGET 2>/dev/null | grep "^[^ ]* ${rootfs}" || true)
  fi

  # Host /proc mounted as rootfs/proc before our mount is OK; host /usr under rootfs is not
  for leak in usr lib lib64 bin sbin etc; do
    if mountpoint -q "${rootfs}/${leak}" 2>/dev/null; then
      local src
      src="$(findmnt -n -o SOURCE -- "${rootfs}/${leak}" 2>/dev/null || true)"
      if [[ -n "$src" && "$src" == /* && "$src" != "$rootfs"* ]]; then
        _safety_fail "host runtime bind mount at ${rootfs}/${leak} <- $src"
      fi
    fi
  done

  if [[ -n "$report" ]]; then
    printf 'PASS\thost_safety\tROOTFS=%s isolated from host\n' "$rootfs" >> "$report"
  fi
  log "host safety lock: OK ($rootfs)"
  return 0
}
