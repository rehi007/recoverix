"""CLI entry point for destructive restore execution."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from common.errors import (
    BitLockerActiveError,
    ConfirmationRequiredError,
    InvalidConfirmationPhraseError,
    RestoreEnvironmentError,
    RestoreSafetyError,
)
from common.logger import setup_logging
from recovery_runtime.state import RuntimeState
from restore_engine.confirmation import RESTORE_CONFIRMATION_PHRASE, print_target_disk_display
from restore_engine.restore_executor import RestoreExecutor, build_execution_context
from restore_engine.restore_safety import authorize_restore_execution

_APPLY_USAGE = (
    "Restore requires: --apply --confirm --phrase "
    f'"{RESTORE_CONFIRMATION_PHRASE}"'
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Execute destructive restore (requires safety authorization)",
    )
    parser.add_argument("--apply", action="store_true", help="Authorize restore execution")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm destructive restore execution",
    )
    parser.add_argument(
        "--phrase",
        required=False,
        help=f'Confirmation phrase (exact): "{RESTORE_CONFIRMATION_PHRASE}"',
    )
    parser.add_argument(
        "--compatible-restore",
        action="store_true",
        help="Allow disk replacement/hard-copy restore when compatibility checks pass",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.apply:
        print(f"error: {_APPLY_USAGE}", file=sys.stderr)
        return 2
    if not args.confirm:
        print("error: --apply requires --confirm", file=sys.stderr)
        return 2
    if not args.phrase:
        print("error: --phrase is required", file=sys.stderr)
        return 2

    try:
        safety = authorize_restore_execution(
            apply=True,
            confirmed=True,
            confirmation_phrase=args.phrase,
            compatible_restore=args.compatible_restore,
        )
    except ConfirmationRequiredError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except InvalidConfirmationPhraseError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except BitLockerActiveError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RestoreEnvironmentError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RestoreSafetyError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not safety.allowed:
        print(safety.reason or "restore not authorized", file=sys.stderr)
        return 1

    for line in safety.target_disk_display:
        print(line)

    runtime_state = RuntimeState(destructive_allowed=True, restore_enabled=False)
    ctx = build_execution_context(safety, confirmed=True, runtime_state=runtime_state)
    executor = RestoreExecutor(ctx)
    result = executor.execute()

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"status={result.status} success={result.success} stage={result.current_stage}")
        if result.reason:
            print(f"reason={result.reason}")

    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
