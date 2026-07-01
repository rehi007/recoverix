#!/usr/bin/env bash
# Installed as: /usr/local/sbin/recoverix-runtime-prepare
# Prepare runtime-owned mountpoints and mount RECOVERY_IMAGE read-only before UI starts.
# Keep startup minimal: do not pre-mount the Windows NTFS volume here.
set -euo pipefail

RUNTIME_ROOT="/run/recovery-runtime"
MOUNT_ROOT="${RUNTIME_ROOT}/mnt"
IMAGE_LABEL="RECOVERY_IMAGE"
IMAGE_MOUNT="${MOUNT_ROOT}/recovery_image"
SERVICE_LOG="/tmp/recovery-runtime-service.log"
PERSIST_DIR="/run/recoverix-boot/boot/recoverix"
PERSIST_SERVICE_LOG="${PERSIST_DIR}/recovery-runtime-service.log"

timestamp() {
  date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date
}

log() {
  printf '%s | prepare: %s\n' "$(timestamp)" "$*" >>"$SERVICE_LOG" 2>&1 || true
}

if [[ -d "$PERSIST_DIR" ]] && touch "$PERSIST_SERVICE_LOG" 2>/dev/null; then
  SERVICE_LOG="$PERSIST_SERVICE_LOG"
fi

touch "$SERVICE_LOG" 2>/dev/null || true
chmod 0666 "$SERVICE_LOG" 2>/dev/null || true

show_startup_animation() {
  local tty_path="/dev/tty1"
  local frame path
  [ -w "$tty_path" ] || return 0

  if command -v stty >/dev/null 2>&1; then
    for path in /dev/console /dev/tty0 /dev/tty1; do
      [ -e "$path" ] || continue
      stty -F "$path" -echo -icanon min 0 time 0 2>/dev/null || true
    done
  fi
  if printf '\033[H\033[2J\033[3J' >"$tty_path" 2>/dev/null; then
    for frame in . .. ... . .. ...; do
      if command -v stty >/dev/null 2>&1; then
        stty -F "$tty_path" -echo -icanon min 0 time 0 2>/dev/null || true
      fi
      printf '\rStarting Recoverix Runtime%-3s' "$frame" >"$tty_path" 2>/dev/null || break
      sleep 0.45
    done
    printf '\033[H\033[2J\033[3J' >"$tty_path" 2>/dev/null || true
  fi
}

show_startup_animation

mkdir -p "$IMAGE_MOUNT"
chmod 0755 "$RUNTIME_ROOT" "$MOUNT_ROOT" "$IMAGE_MOUNT" || true
log "mount root ready: ${IMAGE_MOUNT}"

if grep -qsE "[[:space:]]${IMAGE_MOUNT}[[:space:]]" /proc/mounts; then
  log "RECOVERY_IMAGE already mounted at ${IMAGE_MOUNT}"
else
  device=""
  if [[ -L "/dev/disk/by-label/${IMAGE_LABEL}" ]]; then
    device="$(readlink -f "/dev/disk/by-label/${IMAGE_LABEL}" 2>/dev/null || true)"
  fi

  if [[ -z "$device" ]]; then
    device="$(blkid -L "${IMAGE_LABEL}" 2>/dev/null || true)"
  fi

  if [[ -z "$device" ]]; then
    log "RECOVERY_IMAGE device not found"
  else
    existing_mount="$(awk -v dev="$device" '$1 == dev { print $2; exit }' /proc/mounts || true)"
    if [[ -n "$existing_mount" ]]; then
      log "RECOVERY_IMAGE already mounted on ${existing_mount}"
    elif mount -o ro "$device" "$IMAGE_MOUNT" >>"$SERVICE_LOG" 2>&1; then
      log "mounted ${device} -> ${IMAGE_MOUNT}"
    else
      log "mount failed for ${device} -> ${IMAGE_MOUNT}"
    fi
  fi
fi

log "skip WINDOWS_NTFS pre-mount for faster menu startup"

exit 0
