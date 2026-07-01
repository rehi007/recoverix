#!/usr/bin/env bash
# Installed as: /usr/local/sbin/recoverix-restore-plan
# Prints JSON restore dry-run plan (simulation only; no restore execution).
set -euo pipefail

export PYTHONPATH="/usr/local/lib/recoverix${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m recovery_runtime.gtk_ui.restore_plan "$@"
