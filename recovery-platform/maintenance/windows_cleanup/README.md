# Recoverix Cleanup Tool

This maintenance tool removes Windows-side Recoverix installation artifacts
from a test PC. It is intentionally separate from the main installer and
runtime code.

It removes:

- `RecoveryBootMonitor` scheduled task
- Recoverix firmware/NVRAM boot entries
- firmware one-shot boot values exposed through Windows BCD, when present
- Recoverix EFI folders under the EFI System Partition
- Recoverix Windows files under `C:\Program Files\Recoverix`
- Recoverix data under `C:\ProgramData\Recoverix`
- Recoverix desktop shortcuts and uninstall registry entries

It does not remove:

- `RECOVERY_LINUX` partition
- `RECOVERY_IMAGE` partition
- backup images
- Windows, Microsoft EFI files, or Windows recovery partitions

Build:

```bash
python3 recovery-platform/maintenance/windows_cleanup/build_cleanup_tool.py --version T.1.0.0
```

Output:

```text
recovery-platform/release/maintenance/RecoverixCleanup-T.1.0.0-x64.exe
```

After running the cleanup EXE on Windows, delete `RECOVERY_LINUX` and
`RECOVERY_IMAGE` manually from Windows Disk Management if the machine must be
returned to its pre-test partition layout.

Restart Windows immediately after running the cleanup tool. Do not reinstall
Recoverix or delete/merge recovery partitions before the restart.
