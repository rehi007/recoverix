#!/bin/sh
# Launch Recoverix GUI on tty1 only for the dedicated GUI boot path.

set -eu

USER_NAME="${USER:-recoverix}"
HOME_DIR="${HOME:-/home/${USER_NAME}}"
RUNTIME_DIR="/tmp/recoverix-gui"
CACHE_DIR="${RUNTIME_DIR}"
XAUTHORITY_FILE="${RUNTIME_DIR}/Xauthority"
XDG_CACHE_DIR="${RUNTIME_DIR}/cache"
XDG_DATA_DIR="${RUNTIME_DIR}/share"
XORG_LOG_FILE="${RUNTIME_DIR}/Xorg.0.log"
PERSIST_DIR="/run/recoverix-boot/boot/recoverix"
PERSIST_LOG_FILE="${PERSIST_DIR}/recoverix-gui-session.log"
PERSIST_MARK_FILE="${PERSIST_DIR}/recoverix-gui-launch.mark"
FALLBACK_LOG_FILE="${RUNTIME_DIR}/recoverix-gui-session.log"
LOG_FILE="${FALLBACK_LOG_FILE}"
GUARD_FILE="${RUNTIME_DIR}/recoverix-gui-autostarted"
DRM_WAIT_SEC="${RECOVERIX_GUI_DRM_WAIT_SEC:-20}"
DRM_STABLE_SEC="${RECOVERIX_GUI_DRM_STABLE_SEC:-2}"

mkdir -p "${RUNTIME_DIR}" "${XDG_CACHE_DIR}" "${XDG_DATA_DIR}"
export HOME="${HOME_DIR}"
export USER="${USER_NAME}"
export LOGNAME="${USER_NAME}"
export XAUTHORITY="${XAUTHORITY_FILE}"
export XDG_CACHE_HOME="${XDG_CACHE_DIR}"
export XDG_DATA_HOME="${XDG_DATA_DIR}"

prepare_tty() {
  local tty_path="/dev/tty1"
  [ -w "${tty_path}" ] || return 0
  if command -v stty >/dev/null 2>&1; then
    for path in /dev/console /dev/tty0 /dev/tty1; do
      [ -e "${path}" ] || continue
      stty -F "${path}" -echo -icanon min 0 time 0 2>/dev/null || true
    done
  fi
  printf '\033[H\033[2J\033[3J' >"${tty_path}" 2>/dev/null || true
}

prepare_tty

log_msg() {
  echo "$*" | tee -a "${LOG_FILE}" 2>/dev/null || true
}

drm_card_driver() {
  local card="$1" driver=""
  if [ -e "${card}/device/driver" ]; then
    driver="$(basename "$(readlink -f "${card}/device/driver" 2>/dev/null)" 2>/dev/null || true)"
  fi
  if [ -z "${driver}" ] && [ -r "${card}/device/uevent" ]; then
    driver="$(sed -n 's/^DRIVER=//p' "${card}/device/uevent" 2>/dev/null | head -n 1)"
  fi
  printf '%s' "${driver}"
}

real_drm_cards() {
  local card name dev driver
  for card in /sys/class/drm/card[0-9]*; do
    [ -e "${card}" ] || continue
    name="$(basename "${card}")"
    dev="/dev/dri/${name}"
    [ -e "${dev}" ] || continue
    driver="$(drm_card_driver "${card}")"
    case "${driver}" in
      ""|simpledrm|simple-framebuffer|efi-framebuffer|vesafb)
        continue
        ;;
    esac
    printf '%s:%s ' "${name}" "${driver}"
  done
}

drm_snapshot() {
  local card name dev driver
  for card in /sys/class/drm/card[0-9]*; do
    [ -e "${card}" ] || continue
    name="$(basename "${card}")"
    dev="/dev/dri/${name}"
    driver="$(drm_card_driver "${card}")"
    printf '%s driver=%s dev=%s; ' "${name}" "${driver:-unknown}" "$([ -e "${dev}" ] && printf yes || printf no)"
  done
}

wait_for_graphics_ready() {
  local i ready snapshot

  if command -v udevadm >/dev/null 2>&1; then
    log_msg "waiting for udev device settlement"
    udevadm settle --timeout=8 >>"${LOG_FILE}" 2>&1 || true
  fi

  i=0
  while [ "${i}" -le "${DRM_WAIT_SEC}" ]; do
    ready="$(real_drm_cards)"
    if [ -n "${ready}" ]; then
      log_msg "drm ready: ${ready}"
      if command -v udevadm >/dev/null 2>&1; then
        udevadm settle --timeout=5 >>"${LOG_FILE}" 2>&1 || true
      fi
      if [ "${DRM_STABLE_SEC}" -gt 0 ] 2>/dev/null; then
        log_msg "stabilizing drm for ${DRM_STABLE_SEC}s"
        sleep "${DRM_STABLE_SEC}" 2>/dev/null || true
      fi
      return 0
    fi

    if [ "${i}" -eq 0 ] || [ $((i % 5)) -eq 0 ]; then
      snapshot="$(drm_snapshot)"
      log_msg "waiting for real drm card (${i}/${DRM_WAIT_SEC}s): ${snapshot:-none}"
    fi
    sleep 1 2>/dev/null || true
    i=$((i + 1))
  done

  snapshot="$(drm_snapshot)"
  log_msg "warning: real drm card not ready after ${DRM_WAIT_SEC}s; continuing with Xorg (${snapshot:-none})"
  return 0
}

XORG_BIN="/usr/lib/xorg/Xorg.wrap"
if [ ! -x "${XORG_BIN}" ]; then
  XORG_BIN="/usr/lib/xorg/Xorg"
fi
if [ ! -x "${XORG_BIN}" ]; then
  XORG_BIN="$(command -v Xorg.wrap 2>/dev/null || true)"
fi
if [ -z "${XORG_BIN}" ] || [ ! -x "${XORG_BIN}" ]; then
  XORG_BIN="$(command -v Xorg 2>/dev/null || true)"
fi

if [ -d "${PERSIST_DIR}" ] && touch "${PERSIST_LOG_FILE}" 2>/dev/null; then
  LOG_FILE="${PERSIST_LOG_FILE}"
  {
    echo "recoverix-gui-launch entered $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "cmdline=$(cat /proc/cmdline 2>/dev/null || echo unavailable)"
  } >"${PERSIST_MARK_FILE}" 2>/dev/null || true
else
  touch "${FALLBACK_LOG_FILE}" 2>/dev/null || true
fi

{
  echo ""
  echo "===== recoverix-gui-launch $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
  echo "cmdline=$(cat /proc/cmdline 2>/dev/null || echo unavailable)"
  echo "log_file=${LOG_FILE}"
  echo "xorg_bin=${XORG_BIN:-missing}"
} | tee -a "${LOG_FILE}" 2>/dev/null || true

if [ -e "${GUARD_FILE}" ]; then
  echo "guard file present; skip duplicate GUI launch" | tee -a "${LOG_FILE}" 2>/dev/null || true
  exit 0
fi

touch "${GUARD_FILE}"
touch "${XAUTHORITY_FILE}" 2>/dev/null || true

if [ -z "${XORG_BIN}" ] || [ ! -x "${XORG_BIN}" ]; then
  log_msg "Xorg binary not found"
  exit 127
fi

wait_for_graphics_ready

SESSION_CMD="/usr/bin/xinit /usr/local/sbin/recoverix-openbox-session -- ${XORG_BIN} :0 vt1 -keeptty -nolisten tcp"
case "${XORG_BIN}" in
  *Xorg.wrap) ;;
  *)
    SESSION_CMD="${SESSION_CMD} -logfile ${XORG_LOG_FILE}"
    ;;
esac

log_msg "starting: ${SESSION_CMD}"
set +e
${SESSION_CMD} >>"${LOG_FILE}" 2>&1
rc=$?
set -e
if [ "${rc}" -eq 0 ]; then
  log_msg "xinit completed successfully"
  exit 0
fi

log_msg "xinit exited rc=${rc}"
exit "${rc}"
