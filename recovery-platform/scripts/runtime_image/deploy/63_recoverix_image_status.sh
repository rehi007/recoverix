#!/usr/bin/env bash
# Installed as: /usr/local/sbin/recoverix-image-status
# Prints JSON summary of RECOVERY_IMAGE status (non-destructive).
set -euo pipefail

export PYTHONPATH="/usr/local/lib/recoverix${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m recovery_runtime.gtk_ui.image_status "$@"

