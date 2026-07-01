#!/usr/bin/env bash
# Installed as: /usr/local/sbin/recoverix-runtime-tui
# Launches the console-first Recovery Runtime menu.
set -euo pipefail

BOOTSTRAP_LOG="/tmp/recoverix-runtime-bootstrap.log"
SERVICE_LOG="/tmp/recovery-runtime-service.log"

timestamp() {
  date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date
}

{
  printf '%s | wrapper: enter\n' "$(timestamp)"
  printf '%s | wrapper: tty=%s\n' "$(timestamp)" "$(tty 2>/dev/null || echo unavailable)"
  printf '%s | wrapper: pwd=%s\n' "$(timestamp)" "$PWD"
} >>"$SERVICE_LOG" 2>&1 || true

{
  printf '%s | wrapper: enter\n' "$(timestamp)"
} >>"$BOOTSTRAP_LOG" 2>&1 || true

export PYTHONPATH="/usr/local/lib/recoverix${PYTHONPATH:+:${PYTHONPATH}}"
printf '%s | wrapper: exec python3 runtime shim\n' "$(timestamp)" >>"$SERVICE_LOG" 2>&1 || true
python3 -u - "$@" <<'PY' 2>&1 | tee -a "$SERVICE_LOG"
from datetime import datetime, timezone
from pathlib import Path
import sys
import traceback

SERVICE_LOG = Path("/tmp/recovery-runtime-service.log")

def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        with SERVICE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} | shim: {message}\n")
    except OSError:
        pass

log("enter")
log(f"argv={sys.argv!r}")

try:
    log("import recovery_runtime.main start")
    from recovery_runtime.main import main as runtime_main
    log("import recovery_runtime.main ok")
except Exception as exc:
    log(f"import recovery_runtime.main failed: {exc!r}")
    traceback.print_exc()
    raise

try:
    log("runtime_main() start")
    rc = runtime_main(sys.argv[1:])
    log(f"runtime_main() returned rc={rc!r}")
    raise SystemExit(rc)
except SystemExit:
    raise
except Exception as exc:
    log(f"runtime_main() exception: {exc!r}")
    traceback.print_exc()
    raise
PY
exit "${PIPESTATUS[0]}"
