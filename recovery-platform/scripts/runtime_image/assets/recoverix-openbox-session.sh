#!/bin/sh
# Openbox session wrapper for Recoverix GUI boots started from tty1.

set -eu

RUNTIME_DIR="${RECOVERIX_GUI_RUNTIME_DIR:-/tmp/recoverix-gui}"
LOG="${RECOVERIX_GUI_FORENSIC_LOG:-/var/log/recoverix-gui.log}"
XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-${RUNTIME_DIR}/config}"

mkdir -p "${RUNTIME_DIR}" "$(dirname "${LOG}")" "${XDG_CONFIG_HOME}/openbox"
touch "${LOG}" 2>/dev/null || LOG="${RUNTIME_DIR}/recoverix-openbox-session.log"

export XDG_CURRENT_DESKTOP=Openbox
export XDG_SESSION_DESKTOP=openbox
export DESKTOP_SESSION=openbox
export XDG_CONFIG_HOME
export RECOVERIX_UI_SESSION_MANAGED=1

cat >"${XDG_CONFIG_HOME}/openbox/rc.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <mouse>
    <context name="Root"/>
    <context name="Desktop"/>
  </mouse>
  <keyboard/>
  <menu>
    <hideDelay>0</hideDelay>
    <middle>no</middle>
    <submenuShowDelay>0</submenuShowDelay>
    <submenuHideDelay>0</submenuHideDelay>
    <applicationIcons>no</applicationIcons>
    <manageDesktops>no</manageDesktops>
  </menu>
</openbox_config>
EOF

echo "===== recoverix-openbox-session $(date -u +%Y-%m-%dT%H:%M:%SZ) =====" >>"${LOG}" 2>&1 || true
if command -v openbox >/dev/null 2>&1; then
  openbox --startup /bin/true >>"${LOG}" 2>&1 &
else
  echo "openbox binary missing; falling back to openbox-session" >>"${LOG}" 2>&1 || true
  openbox-session >>"${LOG}" 2>&1 &
fi
wm_pid="$!"

cleanup() {
  if kill -0 "${wm_pid}" 2>/dev/null; then
    kill "${wm_pid}" 2>/dev/null || true
    wait "${wm_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

sleep 1
if [ ! -x /usr/local/sbin/recoverix-recovery-ui ]; then
  echo "missing launcher: /usr/local/sbin/recoverix-recovery-ui" >>"${LOG}" 2>&1 || true
  exit 127
fi

/usr/local/sbin/recoverix-recovery-ui >>"${LOG}" 2>&1
ui_rc="$?"
echo "recoverix-recovery-ui exited rc=${ui_rc}" >>"${LOG}" 2>&1 || true
exit "${ui_rc}"
