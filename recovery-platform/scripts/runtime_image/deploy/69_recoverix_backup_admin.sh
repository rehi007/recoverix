#!/usr/bin/env bash
# Installed as: /usr/local/sbin/recoverix-backup-admin
# Privileged JSON helper for backup reset/apply operations.
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:${PATH}}"
export PYTHONPATH="/usr/local/lib/recoverix${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m recovery_runtime.backup_admin "$@"
