#!/usr/bin/env bash
# Installed as: /usr/local/sbin/recoverix-restore-preflight
# Prints JSON restore preflight summary (non-destructive; no restore execution).
set -euo pipefail

export PYTHONPATH="/usr/local/lib/recoverix${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m recovery_runtime.gtk_ui.restore_preflight "$@"
