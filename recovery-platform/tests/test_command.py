"""Tests for common.command."""

from types import SimpleNamespace
from unittest.mock import patch

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


@patch("common.command.subprocess.run")
def test_live_command_forwards_input_text(mock_run):
    mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

    result = run_command(
        ["parted", "/dev/example"],
        dry_run=False,
        confirmed=True,
        input_text="Yes\n",
    )

    assert result.returncode == 0
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs["input"] == "Yes\n"
