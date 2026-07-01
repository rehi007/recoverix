# Recoverix Installer Preflight Policy

Product: Recoverix
Version: T.1.0.0

The installer must complete preflight checks before making destructive or
boot-impacting changes.

## Required Checks

- Administrator rights
- Windows x64
- Supported Windows version
- UEFI boot mode
- GPT system disk
- EFI system partition exists and is accessible
- Windows system partition can be identified
- BitLocker is off or suspended according to product policy
- Windows Fast Startup and hibernation are off or safely handled
- NTFS dirty state is clear
- The disk is not dynamic
- Partition shrink/resize feasibility
- Sufficient free space for Recoverix partitions
- Recovery Runtime partition can be created
- Recovery Image partition can be created
- 100 MB unallocated space can be reserved behind Windows
- Smart App Control limitation is shown to the user
- AC power recommendation is shown where applicable

## Partition Sizing

`RECOVERY_LINUX`:

```text
4 GB fixed
```

`RECOVERY_IMAGE`:

```text
max(Windows used space * 0.75 + 5 GB, 45 GB)
```

Reserved unallocated space behind Windows:

```text
100 MB
```

The EFI partition must be reused as-is. The installer must not resize, format,
or recreate the existing EFI system partition.

## Abort Conditions

The installer must stop before changing disk layout when any of these conditions
are detected:

- No UEFI boot
- No GPT system disk
- EFI partition not found
- Windows partition cannot be identified
- BitLocker state is unsafe
- NTFS is dirty and cannot be repaired safely
- Required shrink space is unavailable
- Unsupported dynamic or storage spaces layout
- Multiple ambiguous Windows installations
- User does not accept required notices

## Pre-change Backup

Before modifying partitions or boot entries, the installer should capture:

- Current GPT partition table
- EFI file inventory
- NVRAM boot entries and BootOrder
- Windows partition size and offset
- Planned Recoverix partition sizes
- Installer version and timestamp

