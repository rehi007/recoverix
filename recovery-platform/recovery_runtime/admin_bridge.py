"""Bridge for privileged runtime helper invocations from the TUI user."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from common.command import run_command

RUNTIME_ADMIN_HELPER = "/usr/local/sbin/recoverix-backup-admin"
RUNTIME_PROGRESS_PREFIX = "__RECOVERIX_PROGRESS__ "

ProgressCallback = Callable[[dict[str, Any]], None]


def helper_available() -> bool:
    return Path(RUNTIME_ADMIN_HELPER).is_file()


def run_runtime_admin_json(*args: str) -> Tuple[int, dict[str, Any], str]:
    result = run_command(
        ["sudo", "-n", RUNTIME_ADMIN_HELPER, *args],
        dry_run=False,
        confirmed=True,
    )
    payload = decode_json_payload(result.stdout)
    return result.returncode, payload, result.stderr.strip()


def run_runtime_admin_streaming_json(
    *args: str,
    on_progress: Optional[ProgressCallback] = None,
) -> Tuple[int, dict[str, Any], str]:
    argv = ["sudo", "-n", RUNTIME_ADMIN_HELPER, *args]
    completed = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    stdout_parts: list[str] = []
    assert completed.stdout is not None
    pending = ""
    last_progress_candidate = ""
    while True:
        char = completed.stdout.read(1)
        if char == "" and completed.poll() is not None:
            break
        if char == "":
            continue
        stdout_parts.append(char)
        if char in "\r\n":
            candidate = pending
            pending = ""
        else:
            pending += char
            candidate = pending
        event = decode_progress_payload(candidate)
        if event and on_progress is not None and candidate != last_progress_candidate:
            last_progress_candidate = candidate
            on_progress(event)

    if pending:
        event = decode_progress_payload(pending)
        if event and on_progress is not None and pending != last_progress_candidate:
            on_progress(event)

    returncode = completed.wait()
    stdout = "".join(stdout_parts)
    payload = decode_json_payload(stdout)
    return returncode, payload, ""


def decode_json_payload(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def decode_progress_payload(line: str) -> dict[str, Any]:
    candidate = line.strip()
    if not candidate.startswith(RUNTIME_PROGRESS_PREFIX):
        return {}
    raw_payload = candidate[len(RUNTIME_PROGRESS_PREFIX) :].strip()
    if not raw_payload:
        return {}
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
