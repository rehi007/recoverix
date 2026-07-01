# Recoverix Rollback, Removal, and Update Policy

Product: Recoverix
Version: T.1.0.0

## Installation Rollback

The installer must record enough state to undo a failed installation where
possible.

State to record before changes:

- GPT partition table
- EFI file inventory
- NVRAM boot entries
- BootOrder
- Windows partition size and offset
- Created Recoverix directories
- Installed scheduled tasks
- Installed service or agent entries
- Permission changes

Rollback should attempt to:

- Remove newly copied Recoverix files
- Remove newly created scheduled tasks
- Restore or remove Recoverix NVRAM entries
- Restore EFI file changes where safe
- Leave customer data partitions untouched unless the rollback step clearly owns
  the created partition and has not stored a valid backup image

## Removal Policy

Recoverix must not be removed by deleting a single executable.

The uninstaller should offer at least two modes:

1. Windows component removal
   - Remove status app
   - Remove Windows Agent
   - Remove scheduled task
   - Remove NVRAM writer
   - Remove desktop shortcut
   - Keep recovery partitions and backup image

2. Full Recoverix removal
   - Remove Windows components
   - Remove Recoverix EFI entries
   - Remove Recoverix NVRAM entries
   - Remove Recoverix runtime partition
   - Remove Recoverix image partition
   - Return freed space according to product policy

Full removal must warn that deleting `RECOVERY_IMAGE` permanently removes the
local recovery backup.

## Update Policy

Updates should be versioned and reversible where possible.

Update targets:

- Windows Agent
- NVRAM writer
- Status app
- Recovery Runtime
- EFI files
- documentation and notices

Before updating boot or runtime components, save:

- previous runtime hash
- previous EFI file hash
- previous NVRAM state
- previous agent version

## Repair Policy

The product should provide repair functions for:

- Windows Agent reinstall
- task scheduler reinstall
- NVRAM writer reinstall
- EFI file repair
- fallback EFI repair
- desktop shortcut repair

