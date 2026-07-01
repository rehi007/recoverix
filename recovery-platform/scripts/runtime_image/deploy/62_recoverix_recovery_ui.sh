#!/usr/bin/env bash
# Installed as: /usr/local/sbin/recoverix-recovery-ui
# Launches GTK Recovery UI skeleton (mock backup gating; no destructive restore).
set -euo pipefail

export PYTHONPATH="/usr/local/lib/recoverix${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m recovery_runtime.gtk_ui.main "$@"
