# Commercial Distribution Scope

This file defines what belongs in a commercial Recoverix installer and what
must remain source-only or local-only.

## Include in installer

- `backup_engine/`
- `boot_manager/`
- `common/`
- `config/product_manifest.json`
- `efi_assets/`
- `grub/`
- `native/nvram_writer/`
- `partition_manager/`
- `recovery_runtime/`
- `restore_engine/`
- `rollback/`
- `validation/`
- `windows_agent/`
- Required runtime image build/deploy assets from `scripts/runtime_image/`

## Exclude from installer

- `.git/`
- `.idea/`
- `.pytest_cache/`
- `.recoveryboot_logs/`
- `.venv/`
- `__pycache__/`
- `*.pyc`, `*.pyo`, `*.pyd`
- `tests/`
- `tests/integration/`
- `tests/manual/`
- `Dockerfile.test`
- `requirements-dev.txt`
- Temporary logs, build scratch files, and accidental `C:*` directories
- Development-only investigation documents unless explicitly needed by support

## Keep in source repository

These are not installer payloads, but should stay in the source tree until the
installer and GUI are stable:

- `tests/`
- `docs/`
- `scripts/rootfs_minimize/`
- `scripts/runtime_image/`
- `Dockerfile.test`
- `requirements-dev.txt`

## Notes

- Do not remove TUI code until the GUI restore path is fully tested. The TUI is
  still the recovery fallback.
- Do not remove `windows_agent/` or `native/nvram_writer/`; they are required for
  Windows-side RecoveryBoot NVRAM repair.
- Do not include local cache, virtual environment, or IDE directories in release
  packages.
