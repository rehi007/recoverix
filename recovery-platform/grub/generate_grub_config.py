"""Generate grub.cfg for RecoveryBoot (no GRUB installation)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Optional, Sequence

from common.logger import get_logger, setup_logging
from partition_manager.models import EFI_BOOT_RELATIVE, PartitionState

logger = get_logger(__name__)

_DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "efi_assets" / "grub.cfg.template"
)
_WINDOWS_EFI_PATH = "/" + EFI_BOOT_RELATIVE.replace("\\", "/")
_DEFAULT_RECOVERY_KERNEL = "/recovery/vmlinuz"
_DEFAULT_RECOVERY_INITRD = "/recovery/initrd.img"

# TODO(fallback): Implement ESP UUID search.fs_uuid fallback when search --file fails.
# Policy: Windows boot survivability over Recovery entry; re-verify bootmgfw.efi path.


@dataclass(frozen=True)
class GrubConfigOptions:
    """Inputs for grub.cfg generation."""

    timeout: int = 5
    default_index: int = 0
    windows_efi_path: str = _WINDOWS_EFI_PATH
    recovery_kernel: str = _DEFAULT_RECOVERY_KERNEL
    recovery_initrd: str = _DEFAULT_RECOVERY_INITRD
    template_path: Optional[Path] = None


def normalize_line_endings(text: str) -> str:
    """Force LF-only line endings for grub.cfg (never CRLF)."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.rstrip("\n") + "\n"


def normalize_efi_path(path: str) -> str:
    """Normalize EFI paths to forward-slash GRUB style."""
    normalized = path.replace("\\", "/").strip()
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def discover_windows_efi_path(*, live: bool = False) -> str:
    """
    Discover Windows EFI boot path.

    Uses partition_manager ESP discovery on Windows when live=True.
    Falls back to the standard bootmgfw.efi path used with GRUB search.
    """
    standard = _WINDOWS_EFI_PATH
    if not live or sys.platform != "win32":
        logger.debug("using standard Windows EFI path: %s", standard)
        return standard

    try:
        from partition_manager.discovery import discover_partitions

        discovery = discover_partitions(dry_run=False)
        efi = discovery.efi_partition
        if efi is None or efi.state != PartitionState.FOUND.value:
            logger.warning("ESP not found; using standard Windows EFI path")
            return standard

        logger.info(
            "ESP discovered for Windows EFI path (disk=%s part=%s)",
            efi.disk_number,
            efi.partition_number,
        )
    except Exception as exc:
        logger.warning("ESP discovery failed (%s); using standard path", exc)

    return standard


def _load_template(options: GrubConfigOptions) -> Template:
    template_path = options.template_path or _DEFAULT_TEMPLATE
    text = normalize_line_endings(template_path.read_text(encoding="utf-8"))
    return Template(text)


def generate_grub_config(options: Optional[GrubConfigOptions] = None) -> str:
    """Render grub.cfg text from template and options."""
    opts = options or GrubConfigOptions()
    windows_path = normalize_efi_path(opts.windows_efi_path)
    recovery_kernel = normalize_efi_path(opts.recovery_kernel)
    recovery_initrd = normalize_efi_path(opts.recovery_initrd)

    template = _load_template(opts)
    rendered = template.safe_substitute(
        TIMEOUT=str(opts.timeout),
        DEFAULT_INDEX=str(opts.default_index),
        WINDOWS_EFI_PATH=windows_path,
        RECOVERY_KERNEL=recovery_kernel,
        RECOVERY_INITRD=recovery_initrd,
    )
    return normalize_line_endings(rendered)


def write_grub_config(output: Path, options: Optional[GrubConfigOptions] = None) -> Path:
    """Write rendered grub.cfg to disk (LF line endings only)."""
    content = generate_grub_config(options)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")
    logger.info("wrote grub.cfg -> %s", output)
    return output


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Generate RecoveryBoot grub.cfg")
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output grub.cfg path",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Optional template path (default: efi_assets/grub.cfg.template)",
    )
    parser.add_argument("--timeout", type=int, default=5, help="GRUB menu timeout")
    parser.add_argument(
        "--recovery-kernel",
        default=_DEFAULT_RECOVERY_KERNEL,
        help="Recovery Linux kernel path on ESP/root",
    )
    parser.add_argument(
        "--recovery-initrd",
        default=_DEFAULT_RECOVERY_INITRD,
        help="Recovery Linux initrd path on ESP/root",
    )
    parser.add_argument(
        "--windows-efi-path",
        default=None,
        help="Override Windows EFI path (default: auto /EFI/Microsoft/Boot/bootmgfw.efi)",
    )
    parser.add_argument(
        "--discover-esp",
        action="store_true",
        help="Attempt live ESP discovery on Windows before generating",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    windows_path = args.windows_efi_path or discover_windows_efi_path(live=args.discover_esp)
    options = GrubConfigOptions(
        timeout=args.timeout,
        default_index=0,
        windows_efi_path=windows_path,
        recovery_kernel=args.recovery_kernel,
        recovery_initrd=args.recovery_initrd,
        template_path=args.template,
    )
    write_grub_config(args.output, options)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
