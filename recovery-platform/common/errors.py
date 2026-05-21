"""Domain-specific exceptions for the recovery platform."""

from __future__ import annotations


class RecoveryError(Exception):
    """Base exception for all recovery platform errors."""


class DryRunOnlyError(RecoveryError):
    """Raised when a destructive operation is attempted without confirmation."""


class ConfirmationRequiredError(RecoveryError):
    """Raised when explicit user confirmation is missing for a risky action."""


class BitLockerActiveError(RecoveryError):
    """Raised when BitLocker is ON and the requested operation must be refused."""


class RestoreEnvironmentError(RecoveryError):
    """Raised when restore is attempted outside the recovery runtime."""


class InvalidConfirmationPhraseError(RecoveryError):
    """Raised when the restore confirmation phrase does not match."""


class RestoreSafetyError(RecoveryError):
    """Raised when restore safety checks fail before execution."""
