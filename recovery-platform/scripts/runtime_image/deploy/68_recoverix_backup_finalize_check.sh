#!/usr/bin/env bash
# Installed as: /usr/local/sbin/recoverix-backup-finalize-check
# JSON report: canonical backup finalize state on RECOVERY_IMAGE (read-only).
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:${PATH}}"
export PYTHONPATH="/usr/local/lib/recoverix${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m backup_engine.backup_finalize "$@"
