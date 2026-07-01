# Recoverix Commercial Packaging Layout

Product: Recoverix
Version: T.1.0.0

This document defines the target layout for the commercial installer payload.

## Customer Installer

```text
RecoverixSetup-T.1.0.0-x64.exe
```

The installer UI language is Korean.

## Installed Windows Layout

Program files:

```text
C:\Program Files\Recoverix\
  agent\
  bin\
  efi\
  licenses\
  repair\
  status\
  uninstall\
```

Program data:

```text
C:\ProgramData\Recoverix\
  config\
  logs\
  state\
```

Desktop shortcut:

```text
Recoverix 상태확인 및 안내.lnk
```

## Required Windows Components

- `recoverix-nvram-writer.exe`
- Windows Agent executable or script set
- RecoveryBootMonitor task registration script
- Permission hardening script
- Recoverix Status and Guide application
- Uninstaller
- Repair setup assets

## Required EFI Components

Recoverix boot manager:

```text
EFI\RecoveryBoot\
```

Direct recovery entry:

```text
EFI\RecoverixDirect\
```

Fallback boot path:

```text
EFI\Boot\bootx64.efi
```

## Runtime Components

The installer includes the Recoverix Recovery Runtime components needed to boot
the recovery environment. It does not include a customer backup image.

The backup image is created later by the first backup operation into the
`RECOVERY_IMAGE` partition.

## Excluded From Customer Package

- `.git`
- Build caches
- Python `__pycache__`
- Test directories
- Development-only logs
- Investigation notes
- Local boot diagnostics
- IDE/project cache
- Existing customer backup images

## Release Manifest

Each installer build should include:

```text
build-info.json
checksums.txt
ThirdPartyNotices.txt
SourceOffer.txt
```

Recommended manifest fields:

- product name
- product version
- build date
- installer hash
- runtime image hash
- Windows Agent version
- NVRAM writer version
- status app version
- source commit or internal build id

