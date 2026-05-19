"""Verify underscore package names are importable."""

import importlib

PACKAGES = (
    "windows_agent",
    "boot_manager",
    "partition_manager",
    "recovery_runtime",
    "backup_engine",
    "restore_engine",
    "common",
    "config",
    "validation",
    "rollback",
    "tools",
)


def test_packages_importable():
    for name in PACKAGES:
        module = importlib.import_module(name)
        assert module.__doc__
