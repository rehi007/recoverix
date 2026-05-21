"""Write protection for backup dry-run vs apply modes."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from common.errors import ConfirmationRequiredError
from common.logger import get_logger

logger = get_logger(__name__)


class WriteForbiddenError(Exception):
    """Raised when a write is attempted in dry-run mode."""


class WriteGuard:
    """Gate all mutating backup operations behind --apply and --confirm."""

    def __init__(self, *, apply: bool, confirmed: bool) -> None:
        self.apply = apply
        self.confirmed = confirmed

    @property
    def dry_run(self) -> bool:
        return not self.apply

    def _ensure_write_allowed(self, operation: str) -> None:
        if self.dry_run:
            raise WriteForbiddenError(f"dry-run: {operation} forbidden")
        if not self.confirmed:
            raise ConfirmationRequiredError(
                f"Refusing {operation} without --confirm"
            )

    def log_planned(self, operation: str) -> None:
        logger.info("[DRY-RUN] planned: %s", operation)

    def mkdir(self, path: Path, *, operation: str = "mkdir") -> None:
        if self.dry_run:
            self.log_planned(f"{operation}: {path}")
            return
        self._ensure_write_allowed(operation)
        path.mkdir(parents=True, exist_ok=True)

    def write_text(self, path: Path, content: str, *, operation: str = "write") -> None:
        if self.dry_run:
            self.log_planned(f"{operation}: {path}")
            return
        self._ensure_write_allowed(operation)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def unlink(self, path: Path, *, operation: str = "unlink") -> None:
        if self.dry_run:
            self.log_planned(f"{operation}: {path}")
            return
        self._ensure_write_allowed(operation)
        if path.is_file():
            path.unlink()
