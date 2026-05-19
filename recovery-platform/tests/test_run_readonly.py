"""Tests for common.command.run_readonly."""

from unittest.mock import MagicMock, patch

from common.command import run_readonly


@patch("common.command.subprocess.run")
def test_run_readonly_executes(mock_run):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="ok",
        stderr="",
    )
    result = run_readonly(["echo", "test"])
    assert result.dry_run is False
    assert result.stdout == "ok"
    mock_run.assert_called_once()
