"""Tests for common.config."""

from common.config import load_config


def test_defaults_are_safe():
    cfg = load_config()
    assert cfg.dry_run is True
