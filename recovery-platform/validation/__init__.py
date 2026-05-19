"""Pre/post operation validation (BitLocker, GPT, Secure Boot)."""

from .system_check import SystemCheckResult, run_system_check

__all__ = ["SystemCheckResult", "run_system_check"]
