#!/usr/bin/env bash
# Post-change runtime verification (delegates to full safety gate).
# Usage: sudo ./05_verify_runtime.sh [tier1|tier2|all]

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "${DIR}/07_pre_purge_safety_gate.sh" "${1:-tier1}"
