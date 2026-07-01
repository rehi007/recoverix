"""Subprocess wrapper with dry-run support."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence, Union

from common.errors import ConfirmationRequiredError
from common.logger import get_logger

logger = get_logger(__name__)

CommandInput = Union[str, Sequence[str]]


@dataclass(frozen=True)
class CommandResult:
    """Result of a subprocess invocation."""

    argv: List[str]
    returncode: int
    stdout: str
    stderr: str
    dry_run: bool


def _normalize_argv(command: CommandInput) -> List[str]:
    if isinstance(command, str):
        return shlex.split(command)
    return list(command)


def run_command(
    command: CommandInput,
    *,
    dry_run: bool = True,
    confirmed: bool = False,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    input_text: Optional[str] = None,
    timeout: Optional[float] = None,
    check: bool = False,
) -> CommandResult:
    """
    Execute a shell command or log it when dry_run is enabled.

    Destructive or privileged operations require confirmed=True when dry_run=False.
    """
    argv = _normalize_argv(command)
    display = " ".join(shlex.quote(arg) for arg in argv)

    if dry_run:
        logger.info("[DRY-RUN] would execute: %s", display)
        return CommandResult(argv=argv, returncode=0, stdout="", stderr="", dry_run=True)

    if not confirmed:
        raise ConfirmationRequiredError(
            f"Refusing to run without confirmation: {display}"
        )

    # TODO: verify elevated/admin privileges before running privileged commands on Windows.

    logger.info("executing: %s", display)
    completed = subprocess.run(
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        timeout=timeout,
        check=False,
    )

    if completed.stdout:
        logger.debug("stdout: %s", completed.stdout.rstrip())
    if completed.stderr:
        logger.debug("stderr: %s", completed.stderr.rstrip())

    result = CommandResult(
        argv=argv,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        dry_run=False,
    )

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, argv, output=result.stdout, stderr=result.stderr
        )

    return result


def run_readonly(
    command: CommandInput,
    *,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout: Optional[float] = None,
) -> CommandResult:
    """Execute a read-only diagnostic command (always live, logged)."""
    argv = _normalize_argv(command)
    display = " ".join(shlex.quote(arg) for arg in argv)
    logger.info("readonly execute: %s", display)

    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        timeout=timeout,
        check=False,
    )

    if completed.stdout:
        logger.debug("stdout: %s", completed.stdout.rstrip())
    if completed.stderr:
        logger.debug("stderr: %s", completed.stderr.rstrip())

    return CommandResult(
        argv=argv,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        dry_run=False,
    )
