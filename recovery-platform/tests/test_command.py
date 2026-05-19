"""Tests for common.command."""

import pytest

from common.command import run_command
from common.errors import ConfirmationRequiredError


def test_dry_run_does_not_execute():
    result = run_command(["false"], dry_run=True)
    assert result.dry_run is True
    assert result.returncode == 0


def test_live_requires_confirmation():
    with pytest.raises(ConfirmationRequiredError):
        run_command(["echo", "ok"], dry_run=False, confirmed=False)
