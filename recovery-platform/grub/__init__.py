"""GRUB configuration generation (no GRUB installation)."""

from .generate_grub_config import GrubConfigOptions, generate_grub_config, write_grub_config

__all__ = ["GrubConfigOptions", "generate_grub_config", "write_grub_config"]
